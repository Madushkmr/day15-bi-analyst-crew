"""
QualityAgent - checks the merged dataset for the kinds of problems that
silently corrupt downstream analysis: missing dates, missing
(date, channel) spend rows, and negative/implausible values.

Runs after ProfilerAgent and only if the profiler determined a revenue
source is available.
"""
from datetime import date, timedelta

from .base import Agent, AgentError


class QualityAgent(Agent):
    name = "quality"

    def run(self, context: dict) -> dict:
        ingested = context["ingest"]
        dates = ingested["dates"]
        daily = ingested["daily"]
        raw = ingested["raw"]

        if not dates:
            raise AgentError("no dates available to check")

        issues = []

        # 1. Missing calendar days (gaps in the revenue timeline itself)
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[-1])
        expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
        missing_days = sorted(expected - set(dates))
        if missing_days:
            issues.append({
                "type": "missing_calendar_days",
                "severity": "high",
                "detail": f"{len(missing_days)} calendar day(s) missing from revenue data",
                "examples": missing_days[:5],
            })

        # 2. Missing spend rows per (date, channel) - a channel silently
        #    dropped from an export, rather than reporting spend=0.
        channels = sorted({c for d in daily.values() for c in d["spend_by_channel"].keys()})
        seen_spend_keys = raw.get("_seen_spend_keys", set())
        missing_spend = []
        if channels and seen_spend_keys:
            for d in dates:
                for c in channels:
                    if (d, c) not in seen_spend_keys:
                        missing_spend.append(f"{d}/{c}")
        if missing_spend:
            # summarize contiguous-looking gaps by channel rather than
            # dumping every row
            by_channel = {}
            for key in missing_spend:
                d, c = key.split("/")
                by_channel.setdefault(c, []).append(d)
            detail_parts = [f"{c}: {len(ds)} day(s) (e.g. {ds[0]}..{ds[-1]})" for c, ds in by_channel.items()]
            issues.append({
                "type": "missing_spend_rows",
                "severity": "medium",
                "detail": "marketing spend rows missing for: " + "; ".join(detail_parts),
                "affected_count": len(missing_spend),
            })

        # 3. Implausible values: negative revenue/spend, zero-or-negative
        #    resolution hours
        negative_revenue = [r for r in raw.get("revenue", []) if r["revenue"] < 0]
        negative_spend = [r for r in raw.get("spend", []) if r["spend"] < 0]
        if negative_revenue:
            issues.append({
                "type": "negative_revenue",
                "severity": "high",
                "detail": f"{len(negative_revenue)} row(s) with negative revenue",
            })
        if negative_spend:
            issues.append({
                "type": "negative_spend",
                "severity": "high",
                "detail": f"{len(negative_spend)} row(s) with negative spend",
            })

        score = max(0, 100 - 15 * len(issues))
        return {
            "issues": issues,
            "quality_score": score,
            "checked_days": len(dates),
        }
