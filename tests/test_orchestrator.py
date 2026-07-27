import csv
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from src.orchestrator import Orchestrator

SOURCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")


def _real_config(tmp_path, db_name="test_runs.db"):
    return {
        "sources": {
            "revenue_csv": os.path.join(SOURCES_DIR, "daily_revenue.csv"),
            "spend_json": os.path.join(SOURCES_DIR, "marketing_spend.json"),
            "tickets_sql": os.path.join(SOURCES_DIR, "support_tickets.sql"),
        },
        "database": str(tmp_path / db_name),
        "order": ["profiler", "quality", "anomaly", "forecast", "efficiency", "narrative"],
        "agents": {
            "profiler": {"retries": 1, "backoff_seconds": 0.01},
            "quality": {"retries": 2, "backoff_seconds": 0.01},
            "anomaly": {"retries": 2, "backoff_seconds": 0.01},
            "forecast": {"retries": 2, "backoff_seconds": 0.01},
            "efficiency": {"retries": 2, "backoff_seconds": 0.01},
            "narrative": {"retries": 1, "backoff_seconds": 0.01},
        },
    }


def _write_config(tmp_path, config, name="agents.yaml"):
    path = tmp_path / name
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    return str(path)


def test_full_run_completes_and_all_agents_ok(tmp_path):
    config_path = _write_config(tmp_path, _real_config(tmp_path))
    orch = Orchestrator(config_path=config_path)
    run = orch.run()
    orch.close()

    assert run["status"] == "completed"
    statuses = {t["agent_name"]: t["status"] for t in run["traces"]}
    assert statuses == {
        "ingest": "ok", "profiler": "ok", "quality": "ok",
        "anomaly": "ok", "forecast": "ok", "efficiency": "ok", "narrative": "ok",
    }
    assert run["report"]["narrative"]["finding_count"] > 0


def test_transient_failure_is_retried_and_recovers(tmp_path):
    config_path = _write_config(tmp_path, _real_config(tmp_path, "retry_runs.db"))
    # anomaly agent fails once, then succeeds on its retry
    orch = Orchestrator(config_path=config_path, fault_injection={"anomaly": 1})
    run = orch.run()
    orch.close()

    trace = next(t for t in run["traces"] if t["agent_name"] == "anomaly")
    assert trace["status"] == "ok"
    assert trace["attempts"] == 2  # first attempt failed, second succeeded
    assert run["status"] == "completed"


def test_permanent_failure_falls_back_without_crashing_run(tmp_path):
    config_path = _write_config(tmp_path, _real_config(tmp_path, "fallback_runs.db"))
    # forecast fails more times than its retry budget allows (retries=2 -> 3 total attempts)
    orch = Orchestrator(config_path=config_path, fault_injection={"forecast": 10})
    run = orch.run()
    orch.close()

    trace = next(t for t in run["traces"] if t["agent_name"] == "forecast")
    assert trace["status"] == "failed"
    assert trace["attempts"] == 3
    # the run still completes and narrative still runs on the partial context
    assert run["status"] in ("completed", "completed_with_errors")
    assert run["report"]["forecast"] is None
    assert run["report"]["narrative"] is not None
    assert "forecast" in run["report"]["skipped_agents"]


def test_profiler_skips_forecast_on_short_history(tmp_path):
    # Build a tiny 5-day dataset - too short to forecast, per
    # profiler_agent.MIN_DAYS_FOR_FORECAST (14).
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(5)]

    revenue_csv = tmp_path / "revenue.csv"
    with open(revenue_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "region", "channel", "revenue"])
        for d in days:
            w.writerow([d.isoformat(), "NA", "Organic", 1000])

    spend_json = tmp_path / "spend.json"
    with open(spend_json, "w") as f:
        json.dump([{"date": d.isoformat(), "channel": "Organic", "spend": 100} for d in days], f)

    tickets_sql = tmp_path / "tickets.sql"
    with open(tickets_sql, "w") as f:
        f.write("CREATE TABLE tickets (date TEXT, region TEXT, ticket_count INTEGER, avg_resolution_hours REAL);\n")
        for d in days:
            f.write(f"INSERT INTO tickets VALUES ('{d.isoformat()}', 'NA', 5, 4.0);\n")

    config = {
        "sources": {
            "revenue_csv": str(revenue_csv),
            "spend_json": str(spend_json),
            "tickets_sql": str(tickets_sql),
        },
        "database": str(tmp_path / "short_runs.db"),
        "order": ["profiler", "quality", "anomaly", "forecast", "efficiency", "narrative"],
        "agents": {name: {"retries": 1, "backoff_seconds": 0.01}
                   for name in ["profiler", "quality", "anomaly", "forecast", "efficiency", "narrative"]},
    }
    config_path = _write_config(tmp_path, config, "short_config.yaml")

    orch = Orchestrator(config_path=config_path)
    run = orch.run()
    orch.close()

    forecast_trace = next(t for t in run["traces"] if t["agent_name"] == "forecast")
    assert forecast_trace["status"] == "skipped"
    assert "forecast" in run["report"]["skipped_agents"]
    assert run["report"]["forecast"] is None
