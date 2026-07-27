"""
EfficiencyAgent - tracks marketing ROAS (revenue / spend) per channel
and flags channels whose recent-window ROAS has degraded materially
versus their own baseline. Days with missing spend data are excluded
from the average rather than treated as spend=0 (which would make ROAS
look artificially infinite/undefined) - the gap itself is QualityAgent's
job to flag.
"""
import statistics

from .base import Agent, AgentError

DEFAULT_RECENT_WINDOW = 7
DEGRADATION_THRESHOLD = 0.25  # 25% drop in ROAS vs baseline triggers a flag


class EfficiencyAgent(Agent):
    name = "efficiency"

    def __init__(self, recent_window=DEFAULT_RECENT_WINDOW):
        self.recent_window = recent_window

    def run(self, context: dict) -> dict:
        ingested = context["ingest"]
        dates = ingested["dates"]
        daily = ingested["daily"]

        channels = sorted({c for d in daily.values() for c in d["spend_by_channel"].keys()})
        if not channels:
            raise AgentError("no spend-by-channel data available")

        findings = {}
        for channel in channels:
            revenue_by_channel = []
            for d in dates:
                rev = sum(
                    cc.get(channel, 0.0)
                    for cc in daily[d]["revenue_by_region_channel"].values()
                )
                spend = daily[d]["spend_by_channel"].get(channel)
                if spend and spend > 0:
                    revenue_by_channel.append((d, rev / spend))

            if len(revenue_by_channel) <= self.recent_window + 3:
                continue

            baseline = [roas for _, roas in revenue_by_channel[:-self.recent_window]]
            recent = [roas for _, roas in revenue_by_channel[-self.recent_window:]]
            baseline_mean = statistics.mean(baseline)
            recent_mean = statistics.mean(recent)
            if baseline_mean == 0:
                continue
            pct_change = (recent_mean - baseline_mean) / baseline_mean

            findings[channel] = {
                "baseline_roas": round(baseline_mean, 2),
                "recent_roas": round(recent_mean, 2),
                "pct_change": round(pct_change, 3),
                "days_with_data": len(revenue_by_channel),
                "flag": "degraded" if pct_change <= -DEGRADATION_THRESHOLD else (
                    "improved" if pct_change >= DEGRADATION_THRESHOLD else "stable"
                ),
            }

        return {
            "recent_window_days": self.recent_window,
            "degradation_threshold": DEGRADATION_THRESHOLD,
            "channels": findings,
        }
