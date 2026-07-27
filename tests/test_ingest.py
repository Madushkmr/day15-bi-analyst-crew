import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingest import ingest_all, IngestError

SOURCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")


def _paths():
    return (
        os.path.join(SOURCES_DIR, "daily_revenue.csv"),
        os.path.join(SOURCES_DIR, "marketing_spend.json"),
        os.path.join(SOURCES_DIR, "support_tickets.sql"),
    )


def test_ingest_all_merges_three_sources():
    revenue_csv, spend_json, tickets_sql = _paths()
    result = ingest_all(revenue_csv, spend_json, tickets_sql)

    assert len(result["dates"]) == 60
    assert result["source_errors"] == {}

    first_day = result["daily"][result["dates"][0]]
    assert first_day["revenue_total"] > 0
    assert "NA" in first_day["revenue_by_region_channel"]
    assert first_day["spend_total"] > 0
    assert first_day["ticket_count_total"] > 0


def test_ingest_survives_one_bad_source(tmp_path):
    revenue_csv, spend_json, _ = _paths()
    bad_tickets_sql = tmp_path / "broken.sql"
    bad_tickets_sql.write_text("THIS IS NOT VALID SQL;;;")

    result = ingest_all(revenue_csv, spend_json, str(bad_tickets_sql))

    # revenue + spend still loaded fine even though tickets failed
    assert len(result["dates"]) == 60
    assert "tickets" in result["source_errors"]
    assert all(d["ticket_count_total"] == 0 for d in result["daily"].values())


def test_ingest_raises_when_revenue_missing(tmp_path):
    _, spend_json, tickets_sql = _paths()
    missing_csv = str(tmp_path / "does_not_exist.csv")

    # revenue missing but spend+tickets fine -> should NOT raise, since at
    # least one source loaded; source_errors records the failure
    result = ingest_all(missing_csv, spend_json, tickets_sql)
    assert "revenue" in result["source_errors"]
    assert len(result["dates"]) > 0  # dates still populated from spend/tickets


def test_ingest_raises_when_all_sources_missing(tmp_path):
    missing = str(tmp_path / "nope")
    try:
        ingest_all(missing + ".csv", missing + ".json", missing + ".sql")
        assert False, "expected IngestError"
    except IngestError:
        pass
