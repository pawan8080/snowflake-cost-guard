"""Seeded synthetic Snowflake QUERY_HISTORY generator.

Deterministic (seed 42 by default) so the sample findings in the README and
the test suite never drift. The patterns are deliberately realistic: a stable
daily baseline, a handful of waste patterns every day, and one injected
credit spike on 2026-11-05 that the anomaly detector should catch.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from .model import EXPECTED_COLUMNS, GB

WAREHOUSES = [
    # (name, size, typical users)
    ("BI_WH", "Large", ["PAWAN", "BI_ANALYST"]),
    ("ETL_WH", "X-Large", ["DBT_RUNNER", "ETL_SERVICE"]),
    ("COMPUTE_WH", "Medium", ["PAWAN", "DATA_SCIENCE"]),
    ("ADHOC_WH", "Small", ["BI_ANALYST", "DATA_SCIENCE"]),
]

FILTERED_QUERIES = [
    "SELECT ORDER_DATE, SUM(AMOUNT) FROM ANALYTICS.FACT_ORDERS WHERE ORDER_DATE >= '2026-10-01' GROUP BY 1",
    "SELECT CUSTOMER_ID, COUNT(*) FROM ANALYTICS.FACT_ORDERS WHERE REGION = 'ON' GROUP BY 1",
    "SELECT PRODUCT_ID, SUM(QTY) FROM ANALYTICS.FACT_SALES WHERE SALE_DATE = '2026-11-01' GROUP BY 1",
    "SELECT * FROM REPORTING.DAILY_KPI WHERE KPI_DATE >= DATEADD(day, -7, CURRENT_DATE)",
]
UNFILTERED_QUERIES = [
    "SELECT * FROM ANALYTICS.FACT_ORDERS",
    "SELECT CUSTOMER_ID, AMOUNT FROM ANALYTICS.FACT_EVENTS",
    "SELECT * FROM STAGING.RAW_EVENTS",
]


def generate_records(seed: int = 42, days: int = 14, start: date = date(2026, 10, 26)) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    counter = 0

    def add(day: date, warehouse, size, user, seconds, gb_scanned, p_scanned, p_total, spill_gb, text, rows_produced=1000):
        nonlocal counter
        counter += 1
        rows.append(
            {
                "query_id": f"01a{counter:06d}-sample-query",
                "query_text": text,
                "user_name": user,
                "warehouse_name": warehouse,
                "warehouse_size": size,
                "start_time": f"{day.isoformat()} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}",
                "execution_time_ms": int(seconds * 1000),
                "bytes_scanned": int(gb_scanned * GB),
                "partitions_scanned": int(p_scanned),
                "partitions_total": int(p_total),
                "bytes_spilled_remote": int(spill_gb * GB),
                "rows_produced": int(rows_produced),
            }
        )

    for offset in range(days):
        day = start + timedelta(days=offset)

        # Stable baseline: a varying number of ordinary filtered queries a day.
        for _ in range(rng.randint(17, 27)):
            wh_name, wh_size, users = rng.choice(WAREHOUSES)
            add(
                day, wh_name, wh_size, rng.choice(users),
                seconds=rng.uniform(5, 160),
                gb_scanned=rng.uniform(0.2, 3.0),
                p_scanned=rng.randint(5, 60), p_total=1000,
                spill_gb=0,
                text=rng.choice(FILTERED_QUERIES),
                rows_produced=rng.randint(100, 5000),
            )

        # Waste pattern 1: one long-running, large, spilling, poorly pruned query.
        add(
            day, "ETL_WH", "X-Large", "ETL_SERVICE",
            seconds=rng.uniform(380, 950),
            gb_scanned=rng.uniform(18, 35),
            p_scanned=950, p_total=1000,
            spill_gb=rng.uniform(0.5, 3.0),
            text="SELECT * FROM ANALYTICS.FACT_EVENTS WHERE EVENT_TS >= '2020-01-01'",
            rows_produced=250000,
        )
        # Waste pattern 2: one unfiltered full scan.
        add(
            day, "COMPUTE_WH", "Medium", "DATA_SCIENCE",
            seconds=rng.uniform(40, 220),
            gb_scanned=rng.uniform(4, 9),
            p_scanned=900, p_total=1000,
            spill_gb=0,
            text=rng.choice(UNFILTERED_QUERIES),
            rows_produced=900000,
        )
        # Waste pattern 3: one tiny query on an oversized warehouse.
        add(
            day, "ETL_WH", "X-Large", "DBT_RUNNER",
            seconds=rng.uniform(2, 6),
            gb_scanned=rng.uniform(0.01, 0.1),
            p_scanned=2, p_total=1000,
            spill_gb=0,
            text="SELECT COUNT(*) FROM ANALYTICS.DIM_DATE WHERE DATE_KEY = 20261101",
            rows_produced=1,
        )

        # Injected spike: 2026-11-05 (day index 10) — a runaway ETL job.
        if offset == 10:
            for _ in range(15):
                add(
                    day, "ETL_WH", "X-Large", "ETL_SERVICE",
                    seconds=900,
                    gb_scanned=28.0,
                    p_scanned=980, p_total=1000,
                    spill_gb=2.0,
                    text="SELECT * FROM ANALYTICS.FACT_EVENTS WHERE EVENT_TS >= '2019-01-01'",
                    rows_produced=500000,
                )

    rows.sort(key=lambda r: r["start_time"])
    # Re-number IDs in chronological order so the sample reads naturally.
    for i, row in enumerate(rows, start=1):
        row["query_id"] = f"01a{i:06d}-sample-query"
    return rows


def write_sample_csv(path: str | Path, seed: int = 42) -> int:
    rows = generate_records(seed=seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPECTED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
