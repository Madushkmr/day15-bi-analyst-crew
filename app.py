#!/usr/bin/env python3
"""
Flask dashboard + REST API for the BI Analyst Crew.

Routes:
  GET  /                    dashboard: run history, agent trace timeline,
                             narrative, findings, revenue chart with
                             anomaly markers + forecast overlay
  POST /api/run             trigger a new orchestrator run
                             (optional JSON body: {"fault": {"anomaly": 1}})
  GET  /api/runs            list past runs
  GET  /api/runs/<id>       full detail (report + agent trace) for one run
"""
from flask import Flask, jsonify, render_template, request

from src import db
from src.ingest import ingest_all
from src.orchestrator import Orchestrator, load_config

app = Flask(__name__)
DB_PATH = "crew_runs.db"


@app.route("/")
def dashboard():
    conn = db.connect(DB_PATH)
    runs = db.list_runs(conn)
    latest = db.get_run(conn, runs[0]["id"]) if runs else None
    conn.close()
    return render_template("dashboard.html", runs=runs, latest=latest)


@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(silent=True) or {}
    fault = body.get("fault", {})
    orch = Orchestrator(db_path=DB_PATH, fault_injection=fault)
    run = orch.run()
    orch.close()
    return jsonify(run)


@app.route("/api/runs")
def api_runs():
    conn = db.connect(DB_PATH)
    runs = db.list_runs(conn)
    conn.close()
    return jsonify(runs)


@app.route("/api/runs/<int:run_id>")
def api_run_detail(run_id):
    conn = db.connect(DB_PATH)
    run = db.get_run(conn, run_id)
    conn.close()
    if run is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


@app.route("/api/runs/<int:run_id>/timeseries")
def api_run_timeseries(run_id):
    """Total daily revenue series + this run's anomaly markers + forecast
    overlay, for the dashboard chart. Re-reads the (static) source files
    rather than storing the full series in every run's report row."""
    conn = db.connect(DB_PATH)
    run = db.get_run(conn, run_id)
    conn.close()
    if run is None:
        return jsonify({"error": "not found"}), 404

    cfg = load_config()
    ingested = ingest_all(cfg["sources"]["revenue_csv"], cfg["sources"]["spend_json"], cfg["sources"]["tickets_sql"])
    dates = ingested["dates"]
    totals = [round(ingested["daily"][d]["revenue_total"], 2) for d in dates]

    anomaly_dates = set()
    report = run.get("report") or {}
    anomaly_report = report.get("anomaly") or {}
    for series in anomaly_report.get("revenue_anomalies", {}).values():
        for a in series:
            anomaly_dates.add(a["date"])

    forecast_points = (report.get("forecast") or {}).get("forecast", [])

    return jsonify({
        "dates": dates,
        "revenue_total": totals,
        "anomaly_dates": sorted(anomaly_dates),
        "forecast": forecast_points,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
