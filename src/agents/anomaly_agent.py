"""
AnomalyAgent - z-score based anomaly detection over the last N days of
each (region, channel) revenue series and each region's ticket metrics,
compared against a baseline built from the earlier history.

Deliberately simple and dependency-free (mean/stdev from the stdlib
`statistics` module) rather than pulling in numpy/scikit-learn - this is
a crew of small specialists, not one big model, and each one should be
easy to read end to end.
"""
import statistics

from .base import Agent, AgentError

DEFAULT_RECENT_WINDOW = 7
DEFAULT_Z_THRESHOLD = 2.5


def _zscore_series(values, recent_window, z_threshold):
    """values: list of (date, value) sorted by date. Returns list of anomaly dicts."""
    if len(values) <= recent_window + 3:
        return []  # not enough baseline history to judge

    baseline = [v for _, v in values[:-recent_window]]
    recent = values[-recent_window:]

    mean = statistics.mean(baseline)
    stdev = statistics.pstdev(baseline)
    if stdev == 0:
        return []

    anomalies = []
    for d, v in recent:
        z = (v - mean) / stdev
        if abs(z) >= z_threshold:
            anomalies.append({
                "date": d,
                "value": round(v, 2),
                "baseline_mean": round(mean, 2),
                "z_score": round(z, 2),
                "direction": "drop" if z < 0 else "spike",
                "severity": "high" if abs(z) >= z_threshold * 1.6 else "medium",
            })
    return anomalies


class AnomalyAgent(Agent):
    name = "anomaly"

    def __init__(self, recent_window=DEFAULT_RECENT_WINDOW, z_threshold=DEFAULT_Z_THRESHOLD):
        self.recent_window = recent_window
        self.z_threshold = z_threshold

    def run(self, context: dict) -> dict:
        ingested = context["ingest"]
        dates = ingested["dates"]
        daily = ingested["daily"]

        if not dates:
            raise AgentError("no dates available for anomaly detection")

        regions = sorted({r for d in daily.values() for r in d["revenue_by_region_channel"].keys()})
        channels = sorted({c for d in daily.values()
                            for cc in d["revenue_by_region_channel"].values() for c in cc.keys()})

        revenue_findings = {}
        for region in regions:
            for channel in channels:
                series = []
                for d in dates:
                    v = daily[d]["revenue_by_region_channel"].get(region, {}).get(channel)
                    if v is not None:
                        series.append((d, v))
                anomalies = _zscore_series(series, self.recent_window, self.z_threshold)
                if anomalies:
                    revenue_findings[f"{region}/{channel}"] = anomalies

        ticket_findings = {}
        for region in regions:
            for metric in ("count", "resolution_hours"):
                series = []
                for d in dates:
                    entry = daily[d]["ticket_by_region"].get(region)
                    if entry is not None:
                        series.append((d, entry[metric]))
                anomalies = _zscore_series(series, self.recent_window, self.z_threshold)
                if anomalies:
                    ticket_findings[f"{region}/{metric}"] = anomalies

        total = sum(len(v) for v in revenue_findings.values()) + sum(len(v) for v in ticket_findings.values())
        return {
            "recent_window_days": self.recent_window,
            "z_threshold": self.z_threshold,
            "revenue_anomalies": revenue_findings,
            "ticket_anomalies": ticket_findings,
            "total_anomalies": total,
        }
