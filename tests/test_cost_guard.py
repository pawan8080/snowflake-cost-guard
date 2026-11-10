import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from snowflake_cost_guard.analyze import (
    FLAG_LARGE_SCAN,
    FLAG_LONG_RUNNING,
    FLAG_NO_FILTER_SCAN,
    FLAG_OVERSIZED_WAREHOUSE,
    FLAG_POOR_PRUNING,
    FLAG_REMOTE_SPILL,
    analyze,
    detect_anomalies,
    flag_query,
    DayBurn,
)
from snowflake_cost_guard.generate import generate_records, write_sample_csv
from snowflake_cost_guard.model import GB, QueryRecord, credits_per_hour, estimated_credits, load_csv
from snowflake_cost_guard.report import render_html, write_report


def make_record(**overrides):
    base = dict(
        query_id="q1",
        query_text="SELECT A FROM T WHERE A > 1",
        user_name="PAWAN",
        warehouse_name="BI_WH",
        warehouse_size="Medium",
        start_time="2026-11-01 09:00:00",
        execution_time_ms=10_000,
        bytes_scanned=100_000_000,
        partitions_scanned=10,
        partitions_total=1000,
        bytes_spilled_remote=0,
        rows_produced=100,
    )
    base.update(overrides)
    return QueryRecord(**base)


# --- Credit maths -----------------------------------------------------------

def test_credits_per_hour_table():
    assert credits_per_hour("X-Small") == 1
    assert credits_per_hour("Small") == 2
    assert credits_per_hour("Medium") == 4
    assert credits_per_hour("Large") == 8
    assert credits_per_hour("X-Large") == 16
    assert credits_per_hour("2X-Large") == 32
    assert credits_per_hour("3X-Large") == 64
    assert credits_per_hour("4X-Large") == 128
    assert credits_per_hour("XL") == 16
    assert credits_per_hour("") == 0
    assert credits_per_hour("nonsense") == 0


def test_estimated_credits():
    # 3600s on a Medium (4 credits/hour) = exactly 4 credits.
    assert estimated_credits(3600 * 1000, "Medium") == 4.0
    # 1800s on an X-Large (16/hour) = 8 credits.
    assert estimated_credits(1800 * 1000, "X-Large") == 8.0
    assert estimated_credits(0, "Large") == 0.0


def test_record_computes_credits():
    r = make_record(execution_time_ms=900_000, warehouse_size="Large")
    assert r.credits == 2.0  # 900s = 0.25h * 8
    assert r.day == "2026-11-01"


# --- Flag rules --------------------------------------------------------------

def test_flag_long_running():
    assert FLAG_LONG_RUNNING in flag_query(make_record(execution_time_ms=301_000))
    assert FLAG_LONG_RUNNING not in flag_query(make_record(execution_time_ms=299_000))


def test_flag_large_scan():
    assert FLAG_LARGE_SCAN in flag_query(make_record(bytes_scanned=11 * GB))
    assert FLAG_LARGE_SCAN not in flag_query(make_record(bytes_scanned=9 * GB))


def test_flag_remote_spill():
    assert FLAG_REMOTE_SPILL in flag_query(make_record(bytes_spilled_remote=1))
    assert FLAG_REMOTE_SPILL not in flag_query(make_record(bytes_spilled_remote=0))


def test_flag_poor_pruning():
    bad = make_record(partitions_scanned=950, partitions_total=1000, bytes_scanned=5 * GB)
    assert FLAG_POOR_PRUNING in flag_query(bad)
    good = make_record(partitions_scanned=50, partitions_total=1000, bytes_scanned=5 * GB)
    assert FLAG_POOR_PRUNING not in flag_query(good)
    # Tiny scans do not count even at a bad ratio.
    tiny = make_record(partitions_scanned=950, partitions_total=1000, bytes_scanned=10_000_000)
    assert FLAG_POOR_PRUNING not in flag_query(tiny)


def test_flag_oversized_warehouse():
    assert FLAG_OVERSIZED_WAREHOUSE in flag_query(make_record(warehouse_size="X-Large", execution_time_ms=5_000))
    assert FLAG_OVERSIZED_WAREHOUSE in flag_query(make_record(warehouse_size="2X-Large", execution_time_ms=9_000))
    assert FLAG_OVERSIZED_WAREHOUSE not in flag_query(make_record(warehouse_size="X-Large", execution_time_ms=60_000))
    assert FLAG_OVERSIZED_WAREHOUSE not in flag_query(make_record(warehouse_size="Small", execution_time_ms=5_000))


def test_flag_no_filter_scan():
    unfiltered = make_record(query_text="SELECT * FROM BIG_TABLE", bytes_scanned=5 * GB)
    assert FLAG_NO_FILTER_SCAN in flag_query(unfiltered)
    filtered = make_record(query_text="SELECT * FROM BIG_TABLE WHERE ID = 1", bytes_scanned=5 * GB)
    assert FLAG_NO_FILTER_SCAN not in flag_query(filtered)
    small = make_record(query_text="SELECT * FROM SMALL_TABLE", bytes_scanned=1_000_000)
    assert FLAG_NO_FILTER_SCAN not in flag_query(small)


def test_clean_query_has_no_flags():
    assert flag_query(make_record()) == []


# --- Rollups -----------------------------------------------------------------

def test_rollups():
    records = [
        make_record(query_id="a", user_name="U1", warehouse_name="W1", execution_time_ms=3600_000, warehouse_size="Medium"),
        make_record(query_id="b", user_name="U1", warehouse_name="W2", execution_time_ms=1800_000, warehouse_size="Large"),
        make_record(query_id="c", user_name="U2", warehouse_name="W1", execution_time_ms=3600_000, warehouse_size="Medium"),
    ]
    result = analyze(records)
    assert result.total_queries == 3
    assert result.total_credits == 12.0  # 4 + 4 + 4
    by_wh = {r.name: r for r in result.by_warehouse}
    assert by_wh["W1"].credits == 8.0 and by_wh["W1"].queries == 2
    assert result.by_warehouse[0].name == "W1"
    by_user = {r.name: r for r in result.by_user}
    assert by_user["U1"].credits == 8.0
    top = result.top_expensive(1)
    assert top[0].credits == 4.0


# --- Anomaly detection --------------------------------------------------------

def test_detect_anomalies_flags_spike():
    days = [DayBurn(day=f"2026-11-{i:02d}", credits=10.0 + (i % 2), queries=20) for i in range(1, 9)]
    days.append(DayBurn(day="2026-11-09", credits=90.0, queries=40))
    anomalies = detect_anomalies(days)
    assert [d.day for d in anomalies] == ["2026-11-09"]
    assert days[-1].z_score is not None and days[-1].z_score > 2


def test_detect_anomalies_quiet_series():
    days = [DayBurn(day=f"2026-11-{i:02d}", credits=10.0, queries=20) for i in range(1, 10)]
    # Zero variance baseline -> no z-score, no false positives.
    assert detect_anomalies(days) == []


# --- Sample generation --------------------------------------------------------

def test_sample_generation_deterministic():
    first = generate_records(seed=42)
    second = generate_records(seed=42)
    assert first == second
    assert len(first) > 300


def test_sample_csv_roundtrip(tmp_path):
    path = tmp_path / "sample.csv"
    count = write_sample_csv(path, seed=42)
    assert count > 300
    with open(path, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert "execution_time_ms" in header and "warehouse_size" in header
    records = load_csv(path)
    assert len(records) == count
    result = analyze(records)
    assert result.total_credits > 0
    # Every waste pattern is injected daily, so every flag must fire.
    for name, flag_count in result.flag_counts.items():
        assert flag_count > 0, f"flag {name} never fired on sample data"
    # The injected 2026-11-05 spike must be caught.
    assert "2026-11-05" in [d.day for d in result.anomalies]


# --- Report -------------------------------------------------------------------

def test_html_report_creation(tmp_path):
    records = [make_record(query_id="a"), make_record(query_id="b", user_name="U2")]
    result = analyze(records)
    html_text = render_html(result)
    assert "Snowflake Cost Guard" in html_text
    assert "Top 10 expensive queries" in html_text
    out = write_report(result, tmp_path / "report.html")
    assert out.exists() and out.stat().st_size > 500
