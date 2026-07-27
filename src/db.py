"""
SQLite persistence for the agent crew's run history and audit trail.

Two tables:
  - runs:         one row per orchestrator run (status, timing, final report)
  - agent_traces: one row per agent execution within a run (status,
                  attempts, duration, error, output summary) - this is
                  the audit trail that makes the crew's decisions
                  inspectable after the fact, not just its final answer.
"""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    report_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    agent_name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    error TEXT,
    output_summary TEXT
);
"""


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def create_run(conn):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now,))
    conn.commit()
    return cur.lastrowid


def record_trace(conn, run_id, seq, agent_name, status, attempts, duration_ms, error=None, output_summary=None):
    conn.execute(
        """INSERT INTO agent_traces
           (run_id, agent_name, seq, status, attempts, duration_ms, error, output_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, agent_name, seq, status, attempts, duration_ms, error,
         json.dumps(output_summary) if output_summary is not None else None),
    )
    conn.commit()


def finish_run(conn, run_id, status, report):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, report_json = ? WHERE id = ?",
        (now, status, json.dumps(report), run_id),
    )
    conn.commit()


def get_run(conn, run_id):
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["report"] = json.loads(run.pop("report_json")) if run.get("report_json") else None
    traces = conn.execute(
        "SELECT * FROM agent_traces WHERE run_id = ? ORDER BY seq ASC", (run_id,)
    ).fetchall()
    run["traces"] = []
    for t in traces:
        t = dict(t)
        t["output_summary"] = json.loads(t["output_summary"]) if t.get("output_summary") else None
        run["traces"].append(t)
    return run


def list_runs(conn, limit=50):
    rows = conn.execute(
        "SELECT id, started_at, finished_at, status FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
