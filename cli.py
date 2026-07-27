#!/usr/bin/env python3
"""
Command-line interface for the BI Analyst Crew.

Usage:
    python cli.py run                       # run the full agent crew once
    python cli.py run --fault anomaly=1     # simulate 1 transient failure
                                             # in the anomaly agent (retried
                                             # and recovered) - demo of the
                                             # retry path
    python cli.py list-runs                 # list past runs
    python cli.py report <run_id>           # print a past run's narrative
                                             # + full agent trace
"""
import argparse
import json
import sys

from src import db
from src.orchestrator import Orchestrator


def parse_fault(spec):
    """--fault name=count[,name=count...] -> {name: count}"""
    faults = {}
    if not spec:
        return faults
    for part in spec.split(","):
        name, _, count = part.partition("=")
        faults[name.strip()] = int(count) if count else 1
    return faults


def cmd_run(args):
    orch = Orchestrator(db_path=args.db, fault_injection=parse_fault(args.fault))
    run = orch.run()
    orch.close()

    print(f"Run #{run['id']} - status: {run['status']}")
    print()
    print("Agent trace:")
    for t in run["traces"]:
        marker = {"ok": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(t["status"], t["status"].upper())
        print(f"  [{marker:5s}] {t['agent_name']:10s} attempts={t['attempts']} "
              f"{t['duration_ms']:.1f}ms" + (f"  error={t['error']}" if t["error"] else ""))
    print()
    narrative = run["report"].get("narrative") if run["report"] else None
    if narrative:
        print("Executive summary:")
        print(" ", narrative["summary"])
        print()
        print(f"Top findings ({narrative['finding_count']} total):")
        for f in narrative["findings"][:10]:
            print(f"  - [{f['severity']}] {f['category']}: {f['summary']}")
    else:
        print("No narrative produced - see report for details:")
        print(json.dumps(run["report"], indent=2)[:2000])


def cmd_list_runs(args):
    conn = db.connect(args.db)
    runs = db.list_runs(conn)
    if not runs:
        print("No runs yet. Run `python cli.py run` first.")
        return
    for r in runs:
        print(f"#{r['id']:<4} {r['status']:<20} started={r['started_at']} finished={r['finished_at']}")


def cmd_report(args):
    conn = db.connect(args.db)
    run = db.get_run(conn, args.run_id)
    if run is None:
        print(f"No such run: {args.run_id}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(run, indent=2))


def main():
    parser = argparse.ArgumentParser(description="BI Analyst Crew CLI")
    parser.add_argument("--db", default=None, help="path to the SQLite run database "
                                                     "(default: config/agents.yaml 'database' setting)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the full agent crew once")
    p_run.add_argument("--fault", default=None,
                        help="simulate transient failures, e.g. 'anomaly=1' or 'forecast=1,efficiency=3'")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list-runs", help="list past runs")
    p_list.set_defaults(func=cmd_list_runs)

    p_report = sub.add_parser("report", help="print a past run's full detail")
    p_report.add_argument("run_id", type=int)
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if args.db is None:
        args.db = "crew_runs.db"
    args.func(args)


if __name__ == "__main__":
    main()
