"""
ForecastAgent - short-horizon total-revenue forecast using ordinary
least-squares linear trend plus a 7-day-cycle seasonal adjustment
(day-of-week average residual), implemented from scratch with the
stdlib so the crew has no heavy numerical dependency.

Only invoked when ProfilerAgent judged there was enough history
(>= MIN_DAYS_FOR_FORECAST days) to make a trend estimate meaningful.
"""
import statistics
from datetime import date, timedelta

from .base import Agent, AgentError

DEFAULT_HORIZON_DAYS = 7


def _ols(xs, ys):
    n = len(xs)
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


class ForecastAgent(Agent):
    name = "forecast"

    def __init__(self, horizon_days=DEFAULT_HORIZON_DAYS):
        self.horizon_days = horizon_days

    def run(self, context: dict) -> dict:
        ingested = context["ingest"]
        dates = ingested["dates"]
        daily = ingested["daily"]

        if len(dates) < 10:
            raise AgentError("not enough history to fit a trend")

        xs = list(range(len(dates)))
        ys = [daily[d]["revenue_total"] for d in dates]

        slope, intercept = _ols(xs, ys)
        fitted = [intercept + slope * x for x in xs]
        residuals = [y - f for y, f in zip(ys, fitted)]
        residual_stdev = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0

        # day-of-week seasonal adjustment: average residual per weekday
        weekday_residuals = {}
        for d, r in zip(dates, residuals):
            wd = date.fromisoformat(d).weekday()
            weekday_residuals.setdefault(wd, []).append(r)
        weekday_adj = {wd: statistics.mean(rs) for wd, rs in weekday_residuals.items()}

        last_date = date.fromisoformat(dates[-1])
        forecast = []
        for h in range(1, self.horizon_days + 1):
            x = len(dates) - 1 + h
            trend_value = intercept + slope * x
            fdate = last_date + timedelta(days=h)
            adj = weekday_adj.get(fdate.weekday(), 0.0)
            point = trend_value + adj
            forecast.append({
                "date": fdate.isoformat(),
                "forecast": round(max(point, 0), 2),
                "low_80": round(max(point - 1.28 * residual_stdev, 0), 2),
                "high_80": round(point + 1.28 * residual_stdev, 2),
            })

        return {
            "method": "OLS trend + day-of-week seasonal adjustment",
            "horizon_days": self.horizon_days,
            "trend_slope_per_day": round(slope, 2),
            "residual_stdev": round(residual_stdev, 2),
            "forecast": forecast,
        }
