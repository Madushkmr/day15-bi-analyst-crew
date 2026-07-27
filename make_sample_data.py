"""
Regenerates the three synthetic source files under sample_data/ with a fixed
random seed, so the demo is reproducible.

Simulates three real-world BI data sources that never live in the same
system in practice:
  1. daily_revenue.csv      - exported from the sales/e-commerce platform
  2. marketing_spend.json   - exported from the ad platforms' reporting API
  3. support_tickets.sql    - a dump of the support desk's ticket table
                              (loaded into a local SQLite db at ingest time,
                              never committed as a binary file)

A handful of realistic problems are deliberately injected so the agent
crew has something to find:
  - a genuine revenue anomaly: NA/Paid Search revenue collapses for the
    last 5 days (current period), while every other region/channel only
    moves with normal day-to-day noise.
  - a data quality gap: Referral channel marketing spend is simply
    missing for a full week (rows omitted, not zero) - simulating an
    export that silently dropped rows.
  - an operational spike: support ticket volume and resolution time both
    jump in NA over the same last-5-days window as the revenue drop -
    a plausible downstream consequence, for the narrative agent to
    correlate against.
"""
import csv
import json
import random
from datetime import date, timedelta

random.seed(42)

DAYS = 60
START = date(2026, 5, 29)  # -> ends 2026-07-27, "today" in the series
REGIONS = ["NA", "EMEA", "APAC"]
CHANNELS = ["Paid Search", "Organic", "Referral"]

dates = [START + timedelta(days=i) for i in range(DAYS)]
ANOMALY_START = DAYS - 5  # last 5 days = injected incident window


def base_revenue(region, channel, i):
    region_base = {"NA": 5200, "EMEA": 3400, "APAC": 2600}[region]
    channel_mult = {"Paid Search": 1.15, "Organic": 1.0, "Referral": 0.7}[channel]
    weekday_mult = 1.0 + (0.08 if (START + timedelta(days=i)).weekday() in (4, 5) else 0)
    noise = random.uniform(-0.08, 0.08)
    value = region_base * channel_mult * weekday_mult * (1 + noise)
    if region == "NA" and channel == "Paid Search" and i >= ANOMALY_START:
        value *= 0.35  # collapse
    return round(value, 2)


def base_spend(channel, i):
    channel_base = {"Paid Search": 1400, "Organic": 150, "Referral": 500}[channel]
    noise = random.uniform(-0.1, 0.1)
    return round(channel_base * (1 + noise), 2)


def base_tickets(region, i):
    region_base = {"NA": 18, "EMEA": 11, "APAC": 9}[region]
    noise = random.uniform(-0.15, 0.15)
    count = region_base * (1 + noise)
    resolution = 4.5 + random.uniform(-0.5, 0.5)
    if region == "NA" and i >= ANOMALY_START:
        count *= 2.4
        resolution *= 1.8
    return round(count), round(resolution, 2)


def write_revenue():
    path = "sample_data/daily_revenue.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "region", "channel", "revenue"])
        for i, d in enumerate(dates):
            for region in REGIONS:
                for channel in CHANNELS:
                    w.writerow([d.isoformat(), region, channel, base_revenue(region, channel, i)])
    print(f"wrote {path}")


def write_spend():
    path = "sample_data/marketing_spend.json"
    records = []
    # Referral spend missing entirely for days 20-26 (a full week) -
    # simulated dropped export rows, a data-quality issue for the
    # quality agent to catch.
    gap_start, gap_end = 20, 26
    for i, d in enumerate(dates):
        for channel in CHANNELS:
            if channel == "Referral" and gap_start <= i <= gap_end:
                continue
            records.append({
                "date": d.isoformat(),
                "channel": channel,
                "spend": base_spend(channel, i),
            })
    with open(path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"wrote {path} ({len(records)} records, Referral gap days {dates[gap_start]}..{dates[gap_end]})")


def write_tickets_sql():
    path = "sample_data/support_tickets.sql"
    lines = [
        "CREATE TABLE tickets (",
        "  date TEXT NOT NULL,",
        "  region TEXT NOT NULL,",
        "  ticket_count INTEGER NOT NULL,",
        "  avg_resolution_hours REAL NOT NULL",
        ");",
    ]
    for i, d in enumerate(dates):
        for region in REGIONS:
            count, resolution = base_tickets(region, i)
            lines.append(
                f"INSERT INTO tickets (date, region, ticket_count, avg_resolution_hours) "
                f"VALUES ('{d.isoformat()}', '{region}', {count}, {resolution});"
            )
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    write_revenue()
    write_spend()
    write_tickets_sql()
    print("Done. Injected incident window:", dates[ANOMALY_START], "to", dates[-1])
