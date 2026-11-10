"""CLI for Snowflake Cost Guard.

    python -m snowflake_cost_guard --generate-sample examples/sample_query_history.csv
    python -m snowflake_cost_guard --input examples/sample_query_history.csv --output examples/report.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyze import analyze
from .generate import write_sample_csv
from .model import load_csv
from .report import write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snowflake_cost_guard",
        description="Attribute Snowflake credits per query, flag waste, and render an HTML cost report.",
    )
    parser.add_argument("--generate-sample", metavar="PATH", help="Write a seeded synthetic QUERY_HISTORY CSV to PATH and exit (unless --input is also given).")
    parser.add_argument("--input", metavar="PATH", help="QUERY_HISTORY export CSV to analyse.")
    parser.add_argument("--output", metavar="PATH", help="Write the HTML report to PATH. Without it, a text summary is printed.")
    parser.add_argument("--seed", type=int, default=42, help="Seed for --generate-sample (default 42).")
    return parser


def print_summary(analysis) -> None:
    a = analysis
    print(f"Queries analysed : {a.total_queries}")
    print(f"Total credits    : {a.total_credits:.2f} (estimated)")
    print(f"Flagged queries  : {a.flagged_queries}")
    print("Flag counts:")
    for name, count in a.flag_counts.items():
        print(f"  {name:<22} {count}")
    if a.by_warehouse:
        top = a.by_warehouse[0]
        print(f"Top warehouse    : {top.name} ({top.credits:.2f} credits, {top.queries} queries)")
    if a.by_user:
        top = a.by_user[0]
        print(f"Top user         : {top.name} ({top.credits:.2f} credits, {top.queries} queries)")
    if a.anomalies:
        for d in a.anomalies:
            print(f"Anomaly day      : {d.day} — {d.credits:.2f} credits, z={d.z_score:.2f}")
    else:
        print("Anomaly days     : none detected")
    print("Top 5 expensive queries:")
    for r in a.top_expensive(5):
        flags = ",".join(a.flags_by_id.get(r.query_id, [])) or "-"
        print(f"  {r.query_id}  {r.credits:.3f} credits  {r.warehouse_name}/{r.user_name}  [{flags}]")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.generate_sample:
        count = write_sample_csv(args.generate_sample, seed=args.seed)
        print(f"Wrote {count} sample rows to {args.generate_sample}")
        if not args.input:
            return 0

    if not args.input:
        print("Nothing to analyse: pass --input PATH (or --generate-sample PATH).", file=sys.stderr)
        return 2

    records = load_csv(Path(args.input))
    if not records:
        print(f"No rows found in {args.input}", file=sys.stderr)
        return 1

    analysis = analyze(records)
    print_summary(analysis)

    if args.output:
        write_report(analysis, args.output)
        print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
