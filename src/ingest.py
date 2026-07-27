"""
Multi-source ingestion layer.

Pulls together three heterogeneous BI sources that never live in the same
system in the real world:

  - daily_revenue.csv     (sales platform export, CSV, wide granularity:
                            date x region x channel)
  - marketing_spend.json  (ad platform reporting API dump, JSON records,
                            date x channel)
  - support_tickets.sql   (support desk DB dump, loaded into a throwaway
                            local SQLite database, date x region)

and reconciles them onto a single daily timeline the agent crew can reason
about. Each source is loaded independently and defensively - a malformed
or missing source raises IngestError with enough detail for the
orchestrator's retry/fallback logic to decide what to do, rather than
crashing the whole pipeline.
"""
import csv
import json
import os
import sqlite3
import tempfile
from collections import defaultdict


class IngestError(Exception):
    """Raised when a single source fails to load or parse."""


def _read_revenue_csv(path):
    if not os.path.exists(path):
        raise IngestError(f"revenue source not found: {path}")
    rows = []
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({
                    "date": r["date"],
                    "region": r["region"],
                    "channel": r["channel"],
                    "revenue": float(r["revenue"]),
                })
    except (KeyError, ValueError) as e:
        raise IngestError(f"malformed revenue CSV row: {e}")
    if not rows:
        raise IngestError("revenue CSV parsed but contained zero rows")
    return rows


def _read_spend_json(path):
    if not os.path.exists(path):
        raise IngestError(f"spend source not found: {path}")
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise IngestError(f"malformed spend JSON: {e}")
    rows = []
    for r in data:
        try:
            rows.append({
                "date": r["date"],
                "channel": r["channel"],
                "spend": float(r["spend"]),
            })
        except (KeyError, ValueError) as e:
            raise IngestError(f"malformed spend JSON record {r!r}: {e}")
    if not rows:
        raise IngestError("spend JSON parsed but contained zero records")
    return rows


def _read_tickets_sql(sql_path):
    if not os.path.exists(sql_path):
        raise IngestError(f"tickets source not found: {sql_path}")
    with open(sql_path) as f:
        script = f.read()
    # Loaded into a temporary local SQLite db - never persisted, mirrors
    # "receiving a DB dump and materializing it locally to query."
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(script)
            cur = conn.execute("SELECT date, region, ticket_count, avg_resolution_hours FROM tickets")
            rows = [
                {"date": d, "region": region, "ticket_count": int(c), "avg_resolution_hours": float(h)}
                for d, region, c, h in cur.fetchall()
            ]
        except sqlite3.Error as e:
            raise IngestError(f"tickets SQL script failed: {e}")
        finally:
            conn.close()
    finally:
        os.remove(db_path)
    if not rows:
        raise IngestError("tickets SQL loaded but table was empty")
    return rows


def ingest_all(revenue_csv, spend_json, tickets_sql):
    """
    Loads all three sources and merges them onto a unified daily timeline.

    Returns a dict:
      {
        "dates": sorted list of ISO date strings covering the union of
                 all sources,
        "daily": {date: {
            "revenue_total": float,
            "revenue_by_region_channel": {region: {channel: revenue}},
            "spend_total": float | None,   # None if no spend rows that day
            "spend_by_channel": {channel: spend},
            "ticket_count_total": int | None,
            "ticket_by_region": {region: {"count": int, "resolution_hours": float}},
        }},
        "raw": {"revenue": [...], "spend": [...], "tickets": [...]},
        "source_errors": {},   # populated by caller if a source is skipped
      }

    Each source is read independently so a failure in one does not prevent
    reading the others - the caller (orchestrator) decides whether a
    partial merge is acceptable.
    """
    daily = defaultdict(lambda: {
        "revenue_total": 0.0,
        "revenue_by_region_channel": defaultdict(dict),
        "spend_total": 0.0,
        "spend_by_channel": {},
        "ticket_count_total": 0,
        "ticket_by_region": {},
    })
    raw = {"revenue": [], "spend": [], "tickets": []}
    source_errors = {}

    try:
        revenue_rows = _read_revenue_csv(revenue_csv)
        raw["revenue"] = revenue_rows
        for r in revenue_rows:
            d = daily[r["date"]]
            d["revenue_total"] += r["revenue"]
            d["revenue_by_region_channel"][r["region"]][r["channel"]] = r["revenue"]
    except IngestError as e:
        source_errors["revenue"] = str(e)

    try:
        spend_rows = _read_spend_json(spend_json)
        raw["spend"] = spend_rows
        # Track which (date, channel) actually had a record so the quality
        # agent can distinguish "spend of 0" from "spend missing".
        seen_spend = set()
        for r in spend_rows:
            d = daily[r["date"]]
            d["spend_total"] += r["spend"]
            d["spend_by_channel"][r["channel"]] = r["spend"]
            seen_spend.add((r["date"], r["channel"]))
        raw["_seen_spend_keys"] = seen_spend
    except IngestError as e:
        source_errors["spend"] = str(e)

    try:
        ticket_rows = _read_tickets_sql(tickets_sql)
        raw["tickets"] = ticket_rows
        for r in ticket_rows:
            d = daily[r["date"]]
            d["ticket_count_total"] += r["ticket_count"]
            d["ticket_by_region"][r["region"]] = {
                "count": r["ticket_count"],
                "resolution_hours": r["avg_resolution_hours"],
            }
    except IngestError as e:
        source_errors["tickets"] = str(e)

    if not daily:
        raise IngestError(f"all sources failed to load: {source_errors}")

    dates = sorted(daily.keys())
    # normalize defaultdicts to plain dicts for clean JSON serialization
    daily_plain = {}
    for d, v in daily.items():
        v = dict(v)
        v["revenue_by_region_channel"] = {
            region: dict(channels) for region, channels in v["revenue_by_region_channel"].items()
        }
        daily_plain[d] = v

    return {
        "dates": dates,
        "daily": daily_plain,
        "raw": raw,
        "source_errors": source_errors,
    }
