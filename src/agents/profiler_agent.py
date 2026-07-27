"""
ProfilerAgent - the first thing the crew does with any new dataset.

Computes basic shape/completeness stats per source and, critically,
decides which downstream agents are worth running and with what
parameters. This is what makes the orchestration "adaptive" rather than
a fixed pipeline: a dataset with too few days to forecast skips the
forecast agent instead of running it and producing a garbage forecast;
a source that failed to ingest entirely gets its dependent agents
skipped with a clear reason instead of a crash.
"""
from .base import Agent

MIN_DAYS_FOR_FORECAST = 14
MIN_SPEND_COVERAGE_FOR_EFFICIENCY = 0.5  # fraction of expected (date, channel) rows present


class ProfilerAgent(Agent):
    name = "profiler"

    def run(self, context: dict) -> dict:
        ingested = context["ingest"]
        dates = ingested["dates"]
        daily = ingested["daily"]
        raw = ingested["raw"]
        source_errors = ingested["source_errors"]

        n_days = len(dates)

        channels = sorted({c for d in daily.values() for c in d["spend_by_channel"].keys()}
                           or {r["channel"] for r in raw.get("spend", [])})
        expected_spend_rows = n_days * max(len(channels), 1)
        actual_spend_rows = len(raw.get("spend", []))
        spend_coverage = (actual_spend_rows / expected_spend_rows) if expected_spend_rows else 0.0

        regions = sorted({r for d in daily.values() for r in d["revenue_by_region_channel"].keys()})
        has_tickets = "tickets" not in source_errors and any(d["ticket_by_region"] for d in daily.values())
        has_spend = "spend" not in source_errors and actual_spend_rows > 0
        has_revenue = "revenue" not in source_errors and n_days > 0

        recommended = []
        skipped = {}

        if has_revenue:
            recommended.append("quality")
            recommended.append("anomaly")
        else:
            skipped["quality"] = "revenue source unavailable"
            skipped["anomaly"] = "revenue source unavailable"

        if has_revenue and n_days >= MIN_DAYS_FOR_FORECAST:
            recommended.append("forecast")
        else:
            skipped["forecast"] = (
                f"only {n_days} days of history, need >= {MIN_DAYS_FOR_FORECAST} to forecast reliably"
            )

        if has_spend and has_revenue:
            recommended.append("efficiency")
        else:
            skipped["efficiency"] = "spend or revenue source unavailable"

        profile = {
            "n_days": n_days,
            "date_range": [dates[0], dates[-1]] if dates else [None, None],
            "regions": regions,
            "channels": channels,
            "has_revenue": has_revenue,
            "has_spend": has_spend,
            "has_tickets": has_tickets,
            "spend_coverage": round(spend_coverage, 3),
            "spend_coverage_flag": (
                "degraded" if 0 < spend_coverage < MIN_SPEND_COVERAGE_FOR_EFFICIENCY else "ok"
            ),
            "source_errors": source_errors,
            "recommended_agents": recommended,
            "skipped_agents": skipped,
        }
        return profile
