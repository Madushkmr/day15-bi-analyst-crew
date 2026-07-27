# Day 15 — BI Analyst Crew

Day 15 of a daily AI-app series (BI focus). A small **multi-agent orchestration
layer** that reconciles three separate business systems (a sales platform CSV
export, a marketing platform JSON dump, and a support-desk SQL dump), profiles
the merged data, and adaptively decides which of a crew of specialist agents
— data quality, anomaly detection, forecasting, marketing efficiency, and
narrative generation — are worth running, with retries and graceful fallback
when an individual agent fails.

## Why this matters for BI work

Every BI team eventually builds (or buys) some version of this: revenue lives
in the sales platform, spend lives in ad platform reports, support load lives
in a helpdesk tool, and nobody automatically checks all three together. A
human analyst does this by hand — pull each export, eyeball it for gaps,
run whatever anomaly/forecast script they have lying around, and write up a
summary for Monday standup. This app automates that whole loop and, more
importantly, automates the *judgment calls* around it: if support-ticket
data fails to load this week, the analyst-crew doesn't crash the whole
report — it notes the gap and still delivers revenue/forecast findings. If a
dataset only has 5 days of history, it skips forecasting instead of quietly
returning a meaningless trend line.

## Complexity tier: adaptive multi-agent orchestration

This is a step up from Day 14's single-algorithm Flask app (recursive
contribution-tree decomposition). Day 15 introduces:

- **Multi-source ingestion**: three genuinely different file formats (CSV,
  JSON, a SQL dump loaded into a throwaway SQLite db) merged onto one daily
  timeline, with each source failing independently rather than as a unit.
- **An orchestration/planning layer**, not just a fixed pipeline: a
  `ProfilerAgent` inspects the merged data and decides which downstream
  agents are worth running and why (see `src/agents/profiler_agent.py`).
- **Retry + fallback fault tolerance**: every agent runs under a
  configurable retry budget with exponential backoff; if it still fails,
  the orchestrator records the failure and continues the run instead of
  crashing, and the narrative agent produces a report from whatever
  succeeded. This is demonstrable on demand via fault injection
  (`--fault agent_name=count`), not just theoretical.
- **A full audit trail**: every agent execution (status, attempt count,
  duration, error, output summary) is persisted to SQLite per run, so a
  run's behavior can be inspected after the fact.
- **Five distinct analytical techniques** combined and synthesized into one
  narrative: data-quality checks, z-score anomaly detection, OLS-plus-seasonal
  forecasting, marketing-efficiency (ROAS) trend analysis, and rule-based NLG.
- Both a **REST API + live dashboard** (Flask, Chart.js) and a **CLI**, plus
  a **pytest suite** covering the ingestion edge cases, each agent's logic,
  and the orchestrator's retry/skip/fallback behavior, and a **Dockerfile**.

## Architecture

```
day15-bi-analyst-crew/
├── app.py                     # Flask REST API + dashboard
├── cli.py                     # command-line interface
├── make_sample_data.py        # regenerates sample_data/* (fixed seed)
├── config/
│   └── agents.yaml            # source paths, agent order, retry/backoff, params
├── src/
│   ├── ingest.py              # loads + merges the 3 sources, per-source error isolation
│   ├── db.py                  # SQLite schema: runs + agent_traces (audit trail)
│   ├── orchestrator.py        # planner: retries, fallback, persistence
│   └── agents/
│       ├── base.py            # shared Agent interface
│       ├── profiler_agent.py  # decides which agents to run, and why
│       ├── quality_agent.py   # missing days/rows, negative values
│       ├── anomaly_agent.py   # z-score anomalies, revenue + ticket metrics
│       ├── forecast_agent.py  # OLS trend + day-of-week seasonal forecast
│       ├── efficiency_agent.py# ROAS trend per marketing channel
│       └── narrative_agent.py # synthesizes everything into one report
├── templates/
│   └── dashboard.html         # run history, live agent trace, chart w/ anomalies+forecast
├── sample_data/
│   ├── daily_revenue.csv      # 60 days x 3 regions x 3 channels
│   ├── marketing_spend.json   # 60 days x 3 channels (one channel has a week-long gap)
│   └── support_tickets.sql    # SQL dump loaded into a local SQLite db at ingest time
├── tests/
│   ├── test_ingest.py         # multi-source merge + per-source failure isolation
│   ├── test_agents.py         # each agent's logic against the injected incidents
│   └── test_orchestrator.py   # retry recovery, permanent-failure fallback, adaptive skip
├── requirements.txt
└── Dockerfile
```

### How a run works

1. **Ingest** — `src/ingest.py` reads all three sources independently. If one
   fails to parse (missing file, malformed JSON, broken SQL), it's recorded
   in `source_errors` and the other two still load — the whole run only
   aborts if *every* source fails.
2. **Profile** — `ProfilerAgent` looks at what actually came through: how many
   days of history, which regions/channels are present, whether spend
   coverage is thin, whether any source errored out — and returns a
   `recommended_agents` list plus reasons for anything it's skipping.
3. **Specialist agents run in order**, each only if the profiler recommended
   it, each wrapped in retry-with-backoff:
   - `QualityAgent` — missing calendar days, missing spend rows for a
     (date, channel) pair, negative values.
   - `AnomalyAgent` — z-score detection over the most recent window vs. an
     earlier baseline, run independently on revenue-by-region-channel and
     on ticket count/resolution-time-by-region.
   - `ForecastAgent` — ordinary least-squares trend plus a day-of-week
     seasonal adjustment, projected forward N days with an 80% band.
   - `EfficiencyAgent` — per-channel ROAS (revenue/spend), recent window vs.
     baseline, flagging channels that have degraded materially.
4. **Narrative** — `NarrativeAgent` always runs last and reads whatever ended
   up on the shared context, writing one executive summary plus a
   severity-ranked findings list. It's written defensively so a partial run
   (some agents skipped or failed) still produces a shorter, honest report
   instead of crashing.
5. Every attempt is written to `agent_traces`; the finished report is written
   to `runs`. Both are queryable via the CLI, the REST API, or the dashboard.

### The injected incidents (sample data)

`make_sample_data.py` seeds three deliberate, realistic problems so the crew
has something to find:

- **Revenue anomaly**: NA/Paid Search revenue collapses to ~35% of normal for
  the final 5 days of the window — everything else only moves with ordinary
  day-to-day noise.
- **Data quality gap**: Referral-channel marketing spend rows are missing
  entirely for a full week (dropped from the export, not reported as zero).
- **Operational spike**: NA support-ticket volume and average resolution time
  both jump over the same final-5-days window — a plausible downstream
  consequence of the revenue incident, for the narrative agent to flag
  alongside it.

## Running it

```bash
cd day15-bi-analyst-crew
pip install -r requirements.txt

# (optional) regenerate the sample data - already checked in with a fixed seed
python make_sample_data.py

# CLI
python cli.py run                      # run the full crew once, print trace + summary
python cli.py run --fault anomaly=1    # simulate 1 transient failure in AnomalyAgent
                                        # (retried automatically, run still succeeds)
python cli.py run --fault forecast=10  # simulate a permanent failure in ForecastAgent
                                        # (retry budget exhausted -> skipped, run still completes)
python cli.py list-runs
python cli.py report <run_id>

# Dashboard + API
python app.py                          # http://localhost:5000
```

Open `http://localhost:5000`, click **Run new analysis** (optionally typing
e.g. `anomaly=1` into the fault-injection box to see the retry path live),
and browse the agent trace, executive summary, findings, and the revenue
chart with anomaly markers and forecast overlay. Past runs are listed in the
sidebar.

### REST API

```bash
curl -X POST localhost:5000/api/run -H 'Content-Type: application/json' -d '{}'
curl -X POST localhost:5000/api/run -H 'Content-Type: application/json' \
     -d '{"fault": {"anomaly": 1}}'
curl localhost:5000/api/runs
curl localhost:5000/api/runs/1
curl localhost:5000/api/runs/1/timeseries
```

## Tests

```bash
pytest tests/ -v
```

Covers: multi-source ingestion merging correctly and surviving a broken
source; each agent finding the specific incident injected into the sample
data; the orchestrator recovering from a transient failure via retry,
falling back gracefully after a permanent failure without crashing the run,
and the profiler correctly skipping forecast on a dataset too short to
trend.

## Docker

```bash
docker build -t bi-analyst-crew .
docker run -p 5000:5000 bi-analyst-crew
```

## Notes / limitations

- All agents are intentionally dependency-light (stdlib `statistics`,
  hand-rolled OLS) rather than pulling in numpy/pandas/statsmodels — the
  point of this project is the orchestration layer, not the sophistication
  of any single model.
- Fault injection (`--fault`/`{"fault": {...}}`) is a deliberate testing hook,
  not a production circuit breaker — real transient-failure handling would
  also want jittered backoff and per-agent circuit breakers, which this demo
  keeps simple on purpose.
- This is a demo/portfolio project over synthetic data with a fixed random
  seed, not a production data platform — a real version would need
  incremental ingestion, a real message bus between agents instead of a
  shared in-memory context dict, and authentication on the API.
