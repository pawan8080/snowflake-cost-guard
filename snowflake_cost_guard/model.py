"""Data model, credit maths, and CSV loading.

Credit model (Snowflake warehouse credits per hour, standard warehouses):
    X-Small 1, Small 2, Medium 4, Large 8, X-Large 16,
    2X-Large 32, 3X-Large 64, 4X-Large 128, 5X-Large 256, 6X-Large 512

Estimated credits for one query = execution_seconds / 3600 * credits_per_hour

This is an execution-time approximation. It excludes cloud-services credits,
storage, serverless features, and the 60-second minimum billing per warehouse
resume — see README limitations.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

# Keys are normalised warehouse sizes (upper-case, no spaces).
WAREHOUSE_CREDITS_PER_HOUR: dict[str, float] = {
    "X-SMALL": 1.0,
    "XSMALL": 1.0,
    "XS": 1.0,
    "SMALL": 2.0,
    "S": 2.0,
    "MEDIUM": 4.0,
    "M": 4.0,
    "LARGE": 8.0,
    "L": 8.0,
    "X-LARGE": 16.0,
    "XLARGE": 16.0,
    "XL": 16.0,
    "2X-LARGE": 32.0,
    "2XLARGE": 32.0,
    "2XL": 32.0,
    "3X-LARGE": 64.0,
    "3XLARGE": 64.0,
    "3XL": 64.0,
    "4X-LARGE": 128.0,
    "4XLARGE": 128.0,
    "4XL": 128.0,
    "5X-LARGE": 256.0,
    "5XLARGE": 256.0,
    "5XL": 256.0,
    "6X-LARGE": 512.0,
    "6XLARGE": 512.0,
    "6XL": 512.0,
}

GB = 1024 ** 3


def normalize_size(size: str | None) -> str:
    return (size or "").strip().upper().replace(" ", "")


def credits_per_hour(warehouse_size: str | None) -> float:
    """Credits/hour for a warehouse size. Unknown / missing size -> 0.0."""
    if not warehouse_size:
        return 0.0
    raw = warehouse_size.strip().upper()
    # Try the raw form first (e.g. "X-Large"), then the compact form ("XLARGE").
    return WAREHOUSE_CREDITS_PER_HOUR.get(raw, WAREHOUSE_CREDITS_PER_HOUR.get(normalize_size(raw), 0.0))


def estimated_credits(execution_time_ms: float, warehouse_size: str | None) -> float:
    """Estimated credits burned by a single query."""
    seconds = max(0.0, float(execution_time_ms)) / 1000.0
    return seconds / 3600.0 * credits_per_hour(warehouse_size)


@dataclass
class QueryRecord:
    query_id: str
    query_text: str
    user_name: str
    warehouse_name: str
    warehouse_size: str
    start_time: str  # ISO-like "YYYY-MM-DD HH:MM:SS" (date part is used for daily rollups)
    execution_time_ms: float
    bytes_scanned: int
    partitions_scanned: int
    partitions_total: int
    bytes_spilled_remote: int
    rows_produced: int
    credits: float = field(init=False)

    def __post_init__(self) -> None:
        self.credits = estimated_credits(self.execution_time_ms, self.warehouse_size)

    @property
    def execution_seconds(self) -> float:
        return self.execution_time_ms / 1000.0

    @property
    def day(self) -> str:
        return self.start_time[:10]


def _to_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _to_int(value: str | None, default: int = 0) -> int:
    return int(_to_float(value, float(default)))


# Canonical column names this tool expects (lower-case in the CSV).
EXPECTED_COLUMNS = [
    "query_id",
    "query_text",
    "user_name",
    "warehouse_name",
    "warehouse_size",
    "start_time",
    "execution_time_ms",
    "bytes_scanned",
    "partitions_scanned",
    "partitions_total",
    "bytes_spilled_remote",
    "rows_produced",
]


def record_from_row(row: dict) -> QueryRecord:
    # Accept upper-case headers from a raw Snowflake export too.
    lowered = {str(k).lower(): v for k, v in row.items() if k is not None}
    return QueryRecord(
        query_id=str(lowered.get("query_id", "")).strip(),
        query_text=str(lowered.get("query_text", "") or ""),
        user_name=str(lowered.get("user_name", "") or "UNKNOWN").strip(),
        warehouse_name=str(lowered.get("warehouse_name", "") or "UNKNOWN").strip(),
        warehouse_size=str(lowered.get("warehouse_size", "") or "").strip(),
        start_time=str(lowered.get("start_time", "") or "").strip(),
        execution_time_ms=_to_float(lowered.get("execution_time_ms")),
        bytes_scanned=_to_int(lowered.get("bytes_scanned")),
        partitions_scanned=_to_int(lowered.get("partitions_scanned")),
        partitions_total=_to_int(lowered.get("partitions_total")),
        bytes_spilled_remote=_to_int(lowered.get("bytes_spilled_remote")),
        rows_produced=_to_int(lowered.get("rows_produced")),
    )


def load_csv(path: str | Path) -> list[QueryRecord]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []
        return [record_from_row(row) for row in reader if row]
