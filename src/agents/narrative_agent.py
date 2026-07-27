"""
NarrativeAgent - the crew's "chief of staff." Runs last, reads whatever
the other agents managed to produce off the shared context (some may
have been skipped by the profiler or failed and been dropped by the
orchestrator's fallback logic), and writes a single plain-English
executive summary plus a ranked list of findings.

Never assumes every other agent ran - each section is written
defensively so a partial run still produces a useful (if shorter)
report instead of crashing on a missing key.
"""
from .base import Agent


def _fmt_money(v):
    return f"${v:,.0f}"


class NarrativeAgent(Agent):
    name = "narrative"

    def run(self, context: dict) -> dict:
        profile = context.get("profiler", {})
        quality = context.get("quality")
        anomaly = context.get("anomaly")
        forecast = context.get("forecast")
        efficiency = context.get("efficiency")

        findings = []
        lines = []

        date_range = profile.get("date_range", [None, None])
        lines.append(
            f"Analyzed {profile.get('n_days', '?')} days of business data "
            f"({date_range[0]} to {date_range[1]}) across "
            f"{len(profile.get('regions', []))} region(s) and {len(profile.get('channels', []))} channel(s)."
        )

        if profile.get("skipped_agents"):
            skipped_desc = "; ".join(f"{k} ({v})" for k, v in profile["skipped_agents"].items())
            lines.append(f"Note: some analyses were skipped - {skipped_desc}.")

        # --- Quality ---
        if quality:
            if quality["issues"]:
                for issue in quality["issues"]:
                    findings.append({
                        "category": "data_quality",
                        "severity": issue["severity"],
                        "summary": issue["detail"],
                    })
                lines.append(
                    f"Data quality score: {quality['quality_score']}/100 - "
                    f"{len(quality['issues'])} issue(s) found ({', '.join(i['type'] for i in quality['issues'])})."
                )
            else:
                lines.append("Data quality checks passed with no issues found.")

        # --- Anomalies ---
        if anomaly:
            rev_anom = anomaly.get("revenue_anomalies", {})
            tick_anom = anomaly.get("ticket_anomalies", {})
            if rev_anom:
                # find the single worst anomaly to lead with
                worst_key, worst_list = max(
                    rev_anom.items(), key=lambda kv: max(abs(a["z_score"]) for a in kv[1])
                )
                worst = max(worst_list, key=lambda a: abs(a["z_score"]))
                region, channel = worst_key.split("/")
                lines.append(
                    f"Revenue anomaly: {region}/{channel} revenue {worst['direction']}ped to "
                    f"{_fmt_money(worst['value'])} on {worst['date']} "
                    f"(baseline {_fmt_money(worst['baseline_mean'])}, z={worst['z_score']})."
                )
                for key, alist in rev_anom.items():
                    for a in alist:
                        findings.append({
                            "category": "revenue_anomaly",
                            "severity": a["severity"],
                            "summary": f"{key}: {a['direction']} to {_fmt_money(a['value'])} on {a['date']} (z={a['z_score']})",
                        })
            if tick_anom:
                for key, alist in tick_anom.items():
                    region, metric = key.split("/")
                    for a in alist:
                        findings.append({
                            "category": "ops_anomaly",
                            "severity": a["severity"],
                            "summary": f"{region} {metric}: {a['direction']} to {a['value']} on {a['date']} (z={a['z_score']})",
                        })
                lines.append(
                    f"Operational anomaly: support ticket metrics moved abnormally in "
                    f"{len(tick_anom)} region/metric series - possible downstream impact of the revenue issue."
                )

        # --- Efficiency ---
        if efficiency and efficiency.get("channels"):
            degraded = {c: v for c, v in efficiency["channels"].items() if v["flag"] == "degraded"}
            if degraded:
                parts = [f"{c} ({v['pct_change']:+.0%})" for c, v in degraded.items()]
                lines.append(f"Marketing efficiency declining for: {', '.join(parts)}.")
                for c, v in degraded.items():
                    findings.append({
                        "category": "efficiency",
                        "severity": "medium",
                        "summary": f"{c} ROAS dropped {v['pct_change']:+.0%} vs baseline "
                                   f"({v['baseline_roas']} -> {v['recent_roas']})",
                    })

        # --- Forecast ---
        if forecast and forecast.get("forecast"):
            fc = forecast["forecast"]
            total = sum(p["forecast"] for p in fc)
            trend_word = "growing" if forecast["trend_slope_per_day"] > 0 else "declining"
            lines.append(
                f"{forecast['horizon_days']}-day revenue forecast: {_fmt_money(total)} total, "
                f"trend is {trend_word} (~{_fmt_money(abs(forecast['trend_slope_per_day']))}/day)."
            )

        severity_rank = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: severity_rank.get(f["severity"], 3))

        return {
            "summary": " ".join(lines),
            "findings": findings,
            "finding_count": len(findings),
        }
