# Snowflake Cost Guard

**Snowflake tells you the monthly bill. It does not tell you which query burned it.**

Snowflake Cost Guard attributes estimated credits to every individual query, warehouse, and user in a `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` export, flags the waste patterns that drive credit burn, detects anomalous burn days, and renders the whole thing as one self-contained HTML report.

![stack](https://img.shields.io/badge/python-3.10%2B-blue) ![dependencies](https://img.shields.io/badge/dependencies-stdlib%20only-green) ![tests](https://img.shields.io/badge/tests-16%20passing-brightgreen)

## The problem

Snowflake billing is credit-based and warehouse-level. The console and the monthly invoice tell you a warehouse consumed X credits — not which query, user, or pattern inside that warehouse did it. So the usual cost surprises ship silently:

- one unfiltered `SELECT *` over a multi-terabyte fact table
- a dbt / ETL job quietly spilling to remote disk every night
- a 4-second lookup running on a 2X-Large warehouse
- a runaway job that burns in one day what the account normally burns in ten

Cost Guard makes the burn visible at query level, where you can actually fix it.

## How Snowflake credits work (the short version)

Warehouses consume credits per hour based on size, billed per second with a 60-second minimum each time a warehouse starts:

| Warehouse size | Credits / hour |
|---|---|
| X-Small | 1 |
| Small | 2 |
| Medium | 4 |
| Large | 8 |
| X-Large | 16 |
| 2X-Large | 32 |
| 3X-Large | 64 |
| 4X-Large | 128 |

Cost Guard estimates one query's credits as:

```
estimated_credits = execution_seconds / 3600 × credits_per_hour(warehouse_size)
```

That simple multiplication is the entire cost model — no black box.

## Install

No dependencies. Python 3.10+ standard library only (`csv`, `statistics`, `html`, `argparse`).

```bash
git clone https://github.com/pawan8080/snowflake-cost-guard.git
cd snowflake-cost-guard
```

## Usage

### 1. Try it on the seeded sample data

```bash
python -m snowflake_cost_guard --generate-sample examples/sample_query_history.csv
python -m snowflake_cost_guard --input examples/sample_query_history.csv --output examples/report.html
```

The sample is deterministic (seed 42): 14 days of synthetic query history with realistic waste patterns and one injected runaway-ETL day.

### 2. Run it on your own Snowflake account

Run [`sql/export_query_history.sql`](sql/export_query_history.sql) in Snowflake — it selects exactly the columns this tool expects from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` — export the result as CSV with headers, then:

```bash
python -m snowflake_cost_guard --input query_history.csv --output report.html
```

[`sql/export_warehouse_metering.sql`](sql/export_warehouse_metering.sql) pulls `WAREHOUSE_METERING_HISTORY` so you can cross-check the per-query estimates against Snowflake's own hourly metering.

Without `--output`, the tool prints a text summary to stdout instead of writing HTML.

## Waste flags

| Flag | Rule | Why it costs you |
|---|---|---|
| `LONG_RUNNING` | Execution time > 300s | Credits are time × size. Long queries are the bill. |
| `LARGE_SCAN` | Bytes scanned > 10 GB | Usually a missing filter, a missing clustering key, or a `SELECT *` habit. |
| `REMOTE_SPILL` | Bytes spilled to remote disk > 0 | The warehouse ran out of local disk / memory. Often fixed by a larger warehouse for that job, a better join order, or less data. |
| `POOR_PRUNING` | > 80% of partitions scanned (≥ 100 partitions, > 1 GB scanned) | Micro-partition pruning is Snowflake's main scan optimisation. Scanning almost everything means filters do not line up with how the table is clustered / ordered. |
| `OVERSIZED_WAREHOUSE` | X-Large or larger, execution < 10s | A 16+ credits/hour warehouse for a 4-second lookup. Right-size it, or route small queries to a small warehouse. |
| `NO_FILTER_SCAN` | `SELECT` with no `WHERE` in the query text and > 1 GB scanned | A simple, honest heuristic for full-table scans. It will miss some and catch some innocent ones — it is a prompt to look, not a verdict. |

## Anomaly detection

Daily total credits are compared against a rolling baseline of up to the 7 prior days (mean and standard deviation, minimum 3 days of history). A day with a z-score above 2.0 is flagged as an anomaly. A spike cannot hide inside its own baseline, because the baseline only uses days *before* it.

## Sample findings

From the seeded sample shipped in `examples/` (370 queries, 2026-10-26 to 2026-11-08):

| Metric | Value |
|---|---|
| Total estimated credits | **154.51** |
| Queries analysed | 370 |
| Flagged queries (unique) | 61 |
| Top warehouse | **ETL_WH — 128.93 credits** (83% of all burn) |
| Top user | **ETL_SERVICE — 118.04 credits** |
| Anomaly day | **2026-11-05 — 67.84 credits in one day, z = 48.28** |

Flag counts on the sample: `LONG_RUNNING` 29, `LARGE_SCAN` 29, `REMOTE_SPILL` 29, `POOR_PRUNING` 43, `OVERSIZED_WAREHOUSE` 18, `NO_FILTER_SCAN` 14.

The story is the usual one: the burn is not spread out. One warehouse and one service account are almost the entire bill, and one runaway day is almost half of it.

## Report

The HTML report is a single self-contained file (inline CSS, no JavaScript, no external assets) with:

- summary cards: total credits, queries, flagged queries, top warehouse, top user
- flag counts, credits by warehouse, credits by user, daily burn with z-scores
- the top 10 most expensive queries with their flags and query text

## Limitations — read this before trusting a number

- **The credit figure is an execution-time approximation.** Real Snowflake billing is per warehouse while it is running, with a 60-second minimum per resume, and includes idle time between queries. Per-query execution time does not capture idle gaps, so totals will not reconcile exactly with `WAREHOUSE_METERING_HISTORY` — they should land close, and the metering SQL is included precisely so you can check.
- **Excluded:** cloud-services credits, storage costs, serverless features (e.g. automatic clustering, Snowpipe serverless, search optimisation), and multi-cluster scaling nuance.
- **The flags are heuristics.** `NO_FILTER_SCAN` reads query text with a simple `WHERE` check; `POOR_PRUNING` uses a fixed ratio. Both are starting points for investigation, not proof of waste.
- `ACCOUNT_USAGE` data is latent (up to ~3 hours) and requires privileges on the `SNOWFLAKE` database.

## Tests

```bash
python -m pytest tests/ -q
# 16 passed
```

Tests cover the credit table and credit maths, every flag rule (positive and negative cases), warehouse / user rollups, anomaly detection on spiking and quiet series, sample-generation determinism, and HTML report creation.

## Project structure

```
snowflake-cost-guard/
├── snowflake_cost_guard/
│   ├── __main__.py      # python -m entry point
│   ├── cli.py           # argument parsing + text summary
│   ├── model.py         # credit rates, QueryRecord, CSV loading
│   ├── analyze.py       # flag rules, rollups, anomaly detection
│   ├── generate.py      # seeded synthetic QUERY_HISTORY generator
│   └── report.py        # self-contained HTML report
├── sql/
│   ├── export_query_history.sql        # ACCOUNT_USAGE.QUERY_HISTORY → the CSV this tool eats
│   └── export_warehouse_metering.sql   # metering cross-check queries
├── examples/
│   ├── sample_query_history.csv
│   └── report.html
└── tests/
    └── test_cost_guard.py
```

## License

MIT
