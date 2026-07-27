"""
Orchestrator - the planner that turns a set of independent agents into an
adaptive, fault-tolerant BI analysis crew.

Responsibilities that make this more than a fixed pipeline (contrast
with a plain DAG runner):
  1. Ingests all sources itself and hands the merged result to
     ProfilerAgent, which decides which of the remaining agents are
     worth running given THIS run's data (see profiler_agent.py).
  2. Executes each recommended agent with retry + exponential backoff.
     If an agent still fails after its retry budget, the orchestrator
     does NOT abort the run - it records the failure, skips that
     agent's contribution, and lets the rest of the crew (especially
     NarrativeAgent) continue with a partial picture. A single flaky
     agent should degrade the report, not crash the business.
  3. Persists a full audit trail (every attempt, timing, error, and a
     summary of each agent's output) to SQLite so a run can be
     inspected after the fact, not just trusted at face value.
"""
import time
import traceback

import yaml

from . import db
from .ingest import ingest_all
from .agents.profiler_agent import ProfilerAgent
from .agents.quality_agent import QualityAgent
from .agents.anomaly_agent import AnomalyAgent
from .agents.forecast_agent import ForecastAgent
from .agents.efficiency_agent import EfficiencyAgent
from .agents.narrative_agent import NarrativeAgent

AGENT_CLASSES = {
    "profiler": ProfilerAgent,
    "quality": QualityAgent,
    "anomaly": AnomalyAgent,
    "forecast": ForecastAgent,
    "efficiency": EfficiencyAgent,
    "narrative": NarrativeAgent,
}

# Agents the profiler is allowed to skip. profiler/narrative are structural
# and always attempted.
CONDITIONAL_AGENTS = {"quality", "anomaly", "forecast", "efficiency"}


def load_config(path="config/agents.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def _build_agent(name, agent_cfg):
    cls = AGENT_CLASSES[name]
    params = (agent_cfg or {}).get("params", {})
    return cls(**params) if params else cls()


def _summarize_output(name, output):
    """Small, JSON-safe summary of an agent's output for the trace log
    (the full output already lives in the run report; this is just for
    quick scanning of the audit trail)."""
    if output is None:
        return None
    if name == "profiler":
        return {"n_days": output.get("n_days"), "recommended": output.get("recommended_agents")}
    if name == "quality":
        return {"quality_score": output.get("quality_score"), "issue_count": len(output.get("issues", []))}
    if name == "anomaly":
        return {"total_anomalies": output.get("total_anomalies")}
    if name == "forecast":
        return {"horizon_days": output.get("horizon_days"), "trend_slope_per_day": output.get("trend_slope_per_day")}
    if name == "efficiency":
        return {"channels_flagged": len([c for c in output.get("channels", {}).values() if c["flag"] != "stable"])}
    if name == "narrative":
        return {"finding_count": output.get("finding_count")}
    return {"keys": list(output.keys())} if isinstance(output, dict) else str(output)[:200]


class Orchestrator:
    def __init__(self, config_path="config/agents.yaml", db_path=None, fault_injection=None):
        self.config = load_config(config_path)
        self.db_path = db_path or self.config.get("database", "crew_runs.db")
        self.conn = db.connect(self.db_path)
        # fault_injection: optional {agent_name: n_failures_before_success}
        # used by tests / demo to exercise the retry and fallback paths
        # without needing genuinely flaky infrastructure.
        self.fault_injection = fault_injection or {}
        self._fault_counters = dict(self.fault_injection)

    def _maybe_inject_fault(self, name):
        remaining = self._fault_counters.get(name, 0)
        if remaining > 0:
            self._fault_counters[name] -= 1
            raise RuntimeError(f"[fault injection] simulated transient failure in '{name}'")

    def _run_agent_with_retries(self, name, agent, context, retries, backoff_seconds):
        attempts = 0
        last_error = None
        start = time.monotonic()
        while attempts <= retries:
            attempts += 1
            try:
                self._maybe_inject_fault(name)
                output = agent.run(context)
                duration_ms = (time.monotonic() - start) * 1000
                return output, attempts, duration_ms, None
            except Exception as e:  # noqa: BLE001 - crew must never crash on one agent
                last_error = f"{type(e).__name__}: {e}"
                if attempts <= retries:
                    time.sleep(backoff_seconds * (2 ** (attempts - 1)))
        duration_ms = (time.monotonic() - start) * 1000
        return None, attempts, duration_ms, last_error

    def run(self):
        run_id = db.create_run(self.conn)
        context = {}
        seq = 0
        agents_cfg = self.config.get("agents", {})
        sources = self.config["sources"]

        # --- Step 0: ingest (not itself an "agent", but traced the same way) ---
        seq += 1
        start = time.monotonic()
        try:
            self._maybe_inject_fault("ingest")
            ingested = ingest_all(sources["revenue_csv"], sources["spend_json"], sources["tickets_sql"])
            context["ingest"] = ingested
            duration_ms = (time.monotonic() - start) * 1000
            db.record_trace(self.conn, run_id, seq, "ingest", "ok", 1, duration_ms,
                             output_summary={"days": len(ingested["dates"]),
                                              "source_errors": ingested["source_errors"]})
        except Exception as e:  # noqa: BLE001 - a bad source must not crash the run
            duration_ms = (time.monotonic() - start) * 1000
            db.record_trace(self.conn, run_id, seq, "ingest", "failed", 1, duration_ms, error=str(e))
            db.finish_run(self.conn, run_id, "failed", {"error": f"ingestion failed: {e}"})
            return db.get_run(self.conn, run_id)

        skipped = {}

        for name in self.config["order"]:
            seq += 1
            agent_cfg = agents_cfg.get(name, {})

            if name in CONDITIONAL_AGENTS:
                recommended = context.get("profiler", {}).get("recommended_agents", [])
                if name not in recommended:
                    reason = context.get("profiler", {}).get("skipped_agents", {}).get(
                        name, "not recommended by profiler")
                    db.record_trace(self.conn, run_id, seq, name, "skipped", 0, 0.0, error=reason)
                    skipped[name] = reason
                    continue

            agent = _build_agent(name, agent_cfg)
            output, attempts, duration_ms, error = self._run_agent_with_retries(
                name, agent, context,
                retries=agent_cfg.get("retries", 1),
                backoff_seconds=agent_cfg.get("backoff_seconds", 0.1),
            )

            if error is None:
                context[name] = output
                db.record_trace(self.conn, run_id, seq, name, "ok", attempts, duration_ms,
                                 output_summary=_summarize_output(name, output))
            else:
                # Fallback: don't abort the run. Record the failure and
                # move on - narrative_agent and the final report both
                # tolerate missing keys.
                skipped[name] = f"failed after {attempts} attempt(s): {error}"
                db.record_trace(self.conn, run_id, seq, name, "failed", attempts, duration_ms, error=error)

        report = {
            "profile": context.get("profiler"),
            "quality": context.get("quality"),
            "anomaly": context.get("anomaly"),
            "forecast": context.get("forecast"),
            "efficiency": context.get("efficiency"),
            "narrative": context.get("narrative"),
            "skipped_agents": skipped,
        }
        status = "completed" if "narrative" in context else "completed_with_errors"
        db.finish_run(self.conn, run_id, status, report)
        return db.get_run(self.conn, run_id)

    def close(self):
        self.conn.close()
