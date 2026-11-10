"""Waste flags, cost rollups, and daily-burn anomaly detection."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .model import GB, QueryRecord, credits_per_hour

LONG_RUNNING_SECONDS = 300
LARGE_SCAN_BYTES = 10 * GB
POOR_PRUNING_RATIO = 0.80
POOR_PRUNING_MIN_PARTITIONS = 100
POOR_PRUNING_MIN_SCAN_BYTES = 1 * GB
OVERSIZED_MIN_CREDITS_PER_HOUR = 16.0  # X-Large and up
OVERSIZED_MAX_SECONDS = 10
NO_FILTER_MIN_SCAN_BYTES = 1 * GB

FLAG_LONG_RUNNING = "LONG_RUNNING"
FLAG_LARGE_SCAN = "LARGE_SCAN"
FLAG_REMOTE_SPILL = "REMOTE_SPILL"
FLAG_POOR_PRUNING = "POOR_PRUNING"
FLAG_OVERSIZED_WAREHOUSE = "OVERSIZED_WAREHOUSE"
FLAG_NO_FILTER_SCAN = "NO_FILTER_SCAN"

ALL_FLAGS = [
    FLAG_LONG_RUNNING,
    FLAG_LARGE_SCAN,
    FLAG_REMOTE_SPILL,
    FLAG_POOR_PRUNING,
    FLAG_OVERSIZED_WAREHOUSE,
    FLAG_NO_FILTER_SCAN,
]


def flag_query(record: QueryRecord) -> list[str]:
    """Return every waste flag that applies to one query. Honest heuristics only."""
    flags: list[str] = []

    if record.execution_seconds > LONG_RUNNING_SECONDS:
        flags.append(FLAG_LONG_RUNNING)

    if record.bytes_scanned > LARGE_SCAN_BYTES:
        flags.append(FLAG_LARGE_SCAN)

    if record.bytes_spilled_remote > 0:
        flags.append(FLAG_REMOTE_SPILL)

    if record.partitions_total >= POOR_PRUNING_MIN_PARTITIONS and record.bytes_scanned > POOR_PRUNING_MIN_SCAN_BYTES:
        ratio = record.partitions_scanned / record.partitions_total if record.partitions_total else 0.0
        if ratio > POOR_PRUNING_RATIO:
            flags.append(FLAG_POOR_PRUNING)

    if credits_per_hour(record.warehouse_size) >= OVERSIZED_MIN_CREDITS_PER_HOUR and record.execution_seconds < OVERSIZED_MAX_SECONDS:
        flags.append(FLAG_OVERSIZED_WAREHOUSE)

    text = record.query_text or ""
    if (
        record.bytes_scanned > NO_FILTER_MIN_SCAN_BYTES
        and re.search(r"\bSELECT\b", text, flags=re.IGNORECASE)
        and not re.search(r"\bWHERE\b", text, flags=re.IGNORECASE)
    ):
        flags.append(FLAG_NO_FILTER_SCAN)

    return flags


@dataclass
class Rollup:
    name: str
    queries: int = 0
    credits: float = 0.0
    execution_seconds: float = 0.0
    bytes_scanned: int = 0
    flagged_queries: int = 0


@dataclass
class DayBurn:
    day: str
    credits: float
    queries: int
    z_score: float | None = None
    is_anomaly: bool = False


@dataclass
class Analysis:
    records: list[QueryRecord]
    flags_by_id: dict[str, list[str]]
    flag_counts: dict[str, int]
    by_warehouse: list[Rollup]
    by_user: list[Rollup]
    by_day: list[DayBurn]
    anomalies: list[DayBurn] = field(default_factory=list)

    @property
    def total_credits(self) -> float:
        return sum(r.credits for r in self.records)

    @property
    def total_queries(self) -> int:
        return len(self.records)

    @property
    def flagged_queries(self) -> int:
        return sum(1 for f in self.flags_by_id.values() if f)

    def top_expensive(self, n: int = 10) -> list[QueryRecord]:
        return sorted(self.records, key=lambda r: r.credits, reverse=True)[:n]


def _rollup(records: list[QueryRecord], flags_by_id: dict[str, list[str]], key) -> list[Rollup]:
    buckets: dict[str, Rollup] = {}
    for r in records:
        name = key(r)
        bucket = buckets.setdefault(name, Rollup(name=name))
        bucket.queries += 1
        bucket.credits += r.credits
        bucket.execution_seconds += r.execution_seconds
        bucket.bytes_scanned += r.bytes_scanned
        if flags_by_id.get(r.query_id):
            bucket.flagged_queries += 1
    return sorted(buckets.values(), key=lambda b: b.credits, reverse=True)


def detect_anomalies(daily: list[DayBurn], window: int = 7, min_history: int = 3, z_threshold: float = 2.0) -> list[DayBurn]:
    """Flag days whose credit burn is a z-score outlier vs the prior days.

    Pure rolling baseline: for each day, mean/std come only from the up to
    `window` days before it, so a spike cannot hide inside its own baseline.
    """
    anomalies: list[DayBurn] = []
    history: list[float] = []
    for day in daily:
        if len(history) >= min_history:
            recent = history[-window:]
            mean = statistics.fmean(recent)
            std = statistics.pstdev(recent) if len(recent) > 1 else 0.0
            if std > 0:
                day.z_score = (day.credits - mean) / std
                day.is_anomaly = day.z_score > z_threshold
                if day.is_anomaly:
                    anomalies.append(day)
        history.append(day.credits)
    return anomalies


def analyze(records: list[QueryRecord]) -> Analysis:
    flags_by_id = {r.query_id: flag_query(r) for r in records}
    flag_counts = {name: 0 for name in ALL_FLAGS}
    for flags in flags_by_id.values():
        for f in flags:
            flag_counts[f] += 1

    daily_map: dict[str, DayBurn] = {}
    for r in records:
        d = daily_map.setdefault(r.day, DayBurn(day=r.day, credits=0.0, queries=0))
        d.credits += r.credits
        d.queries += 1
    by_day = [daily_map[k] for k in sorted(daily_map)]
    anomalies = detect_anomalies(by_day)

    return Analysis(
        records=records,
        flags_by_id=flags_by_id,
        flag_counts=flag_counts,
        by_warehouse=_rollup(records, flags_by_id, lambda r: r.warehouse_name),
        by_user=_rollup(records, flags_by_id, lambda r: r.user_name),
        by_day=by_day,
        anomalies=anomalies,
    )
