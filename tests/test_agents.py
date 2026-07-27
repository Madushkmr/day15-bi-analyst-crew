import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingest import ingest_all
from src.agents.profiler_agent import ProfilerAgent
from src.agents.quality_agent import QualityAgent
from src.agents.anomaly_agent import AnomalyAgent
from src.agents.forecast_agent import ForecastAgent
from src.agents.efficiency_agent import EfficiencyAgent
from src.agents.narrative_agent import NarrativeAgent

SOURCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")


def _context():
    revenue_csv = os.path.join(SOURCES_DIR, "daily_revenue.csv")
    spend_json = os.path.join(SOURCES_DIR, "marketing_spend.json")
    tickets_sql = os.path.join(SOURCES_DIR, "support_tickets.sql")
    return {"ingest": ingest_all(revenue_csv, spend_json, tickets_sql)}


def test_profiler_recommends_all_agents_on_clean_enough_data():
    ctx = _context()
    profile = ProfilerAgent().run(ctx)
    ctx["profiler"] = profile

    assert profile["n_days"] == 60
    assert set(profile["recommended_agents"]) == {"quality", "anomaly", "forecast", "efficiency"}
    assert profile["has_revenue"] and profile["has_spend"] and profile["has_tickets"]


def test_quality_agent_flags_the_injected_spend_gap():
    ctx = _context()
    ctx["profiler"] = ProfilerAgent().run(ctx)
    quality = QualityAgent().run(ctx)

    types = {issue["type"] for issue in quality["issues"]}
    assert "missing_spend_rows" in types
    assert quality["quality_score"] < 100


def test_anomaly_agent_finds_injected_na_paid_search_drop():
    ctx = _context()
    ctx["profiler"] = ProfilerAgent().run(ctx)
    anomaly = AnomalyAgent().run(ctx)

    assert anomaly["total_anomalies"] > 0
    assert "NA/Paid Search" in anomaly["revenue_anomalies"]
    hits = anomaly["revenue_anomalies"]["NA/Paid Search"]
    assert any(a["direction"] == "drop" for a in hits)


def test_forecast_agent_produces_requested_horizon():
    ctx = _context()
    forecast = ForecastAgent(horizon_days=5).run(ctx)

    assert forecast["horizon_days"] == 5
    assert len(forecast["forecast"]) == 5
    for point in forecast["forecast"]:
        assert point["low_80"] <= point["forecast"] <= point["high_80"]


def test_efficiency_agent_flags_paid_search_degradation():
    ctx = _context()
    efficiency = EfficiencyAgent().run(ctx)

    assert "Paid Search" in efficiency["channels"]
    assert efficiency["channels"]["Paid Search"]["pct_change"] < 0


def test_narrative_agent_handles_a_full_context():
    ctx = _context()
    ctx["profiler"] = ProfilerAgent().run(ctx)
    ctx["quality"] = QualityAgent().run(ctx)
    ctx["anomaly"] = AnomalyAgent().run(ctx)
    ctx["forecast"] = ForecastAgent().run(ctx)
    ctx["efficiency"] = EfficiencyAgent().run(ctx)

    narrative = NarrativeAgent().run(ctx)
    assert narrative["summary"]
    assert narrative["finding_count"] == len(narrative["findings"])
    # high severity findings should sort before medium/low
    severities = [f["severity"] for f in narrative["findings"]]
    if "high" in severities and "low" in severities:
        assert severities.index("high") < severities.index("low")


def test_narrative_agent_handles_missing_agents_gracefully():
    """Simulates a run where quality/anomaly/forecast/efficiency were all
    skipped or failed - narrative must still produce a (shorter) report
    instead of raising."""
    ctx = _context()
    ctx["profiler"] = ProfilerAgent().run(ctx)
    narrative = NarrativeAgent().run(ctx)
    assert narrative["summary"]
    assert narrative["findings"] == []
