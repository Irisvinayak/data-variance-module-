"""Run every NLP date scenario against the real data and check it executes.

For each table the embedding index covers (i.e. every table the NLP bar can
answer about), each scenario query goes through date_resolver and then the
real service.compute_variance — the same two calls /variance/nlresolve makes
after intent resolution. A scenario passes only when it returns rows.

The LLM/retrieval stages are deliberately NOT in the loop: they choose the
table, which is fixed here, so a failure in this report is always a date or
compute problem.

    python scripts/eval_nlp_dates.py                  # host from .env VERSION
    python scripts/eval_nlp_dates.py --version 6.0 --tenant 1001
    python scripts/eval_nlp_dates.py --max-tables 3 --verbose
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SCENARIOS = [
    "total", "latest", "current",
    "31-Mar-2025", "31/03/2025", "2025-03-31", "31.03.2025", "on 30-Mar-2025",
    "March 2025", "Mar-25", "in Jan 2025",
    "Q1 2025", "2025 Q1", "Q1FY25", "Q1 of FY25", "FY25 Q1", "Q4 FY2024-25",
    "H1 2025", "H2 FY25",
    "2024", "FY25", "FY2024-25", "CY2024", "financial year 2025",
    "last quarter", "previous month", "last year", "last 2 quarters", "last 3 months",
    "last 5 periods", "trailing four quarters",
    "QoQ", "MoM", "YoY", "year over year", "same quarter last year",
    "Mar 2025 vs Mar 2024", "31-Mar-2025 vs 31-Dec-2024 vs 30-Sep-2024",
    "between Jan 2025 and Mar 2025", "from Jan 2025 to Jun 2025",
    "as of 31-Mar-2025 vs last 2 quarters", "Q1FY25 QoQ", "March 2025 YoY",
    "since Q1FY24", "since Jan 2025",
    "31-Mar-2030", "March 2001", "last 2 quaters", "2",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="5.5 or 6.0 (default: .env VERSION)")
    ap.add_argument("--tenant", default="", help="tenant id (6.0)")
    ap.add_argument("--max-tables", type=int, default=0, help="limit tables tested (0 = all)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)

    from backend.config.context import RequestContext
    from backend import hosts
    import backend.data.report_lookup as report_lookup

    if args.version:
        # .env loads with override=True, so VERSION cannot be flipped from the
        # shell; pin the profile explicitly instead.
        profile = hosts.get_profile(args.version)
        hosts.get_profile = lambda *a, **k: profile
        report_lookup.get_profile = hosts.get_profile

    from backend.data import service
    from backend.data.db import execute_query
    from backend.data.report_lookup import parse_returns
    from backend.nlp import indexed_returns, return_lookup
    from backend.nlp.date_resolver import resolve_reporting_date

    ctx = RequestContext(tenant_id=args.tenant)
    profile = hosts.get_profile()
    names = {r.get("Id"): r.get("Name") for r in parse_returns(ctx)}
    print(f"{profile.name} | tenant={args.tenant or '-'} | fiscal year starts month "
          f"{profile.fiscal_year_start_month}")

    tables = []
    for rid in sorted(indexed_returns.indexed_return_ids(ctx)):
        for t in indexed_returns.tables_for_return(rid, ctx):
            meta = return_lookup.get_return_for_table(t, ctx=ctx) or {}
            tables.append((rid, t, meta.get("filter_col") or "RDATE", meta.get("report_freq") or "M"))
    if args.max_tables:
        tables = tables[: args.max_tables]
    print(f"{len(tables)} indexed table(s) x {len(SCENARIOS)} scenario(s)\n")

    failures, total, skipped = [], 0, 0
    for rid, table, filter_col, freq in tables:
        try:
            available = service.get_available_dates(rid, table, execute_query, ctx=ctx)
        except Exception as exc:
            available = []
            print(f"  skip {table}: {exc}")
        if not available:
            skipped += 1
            continue
        print(f"== {names.get(rid, rid)} / {table} (freq {freq}, {len(available)} dates, "
              f"{available[-1]} .. {available[0]})")
        for q in SCENARIOS:
            total += 1
            try:
                anchor, periods, dates, notes = resolve_reporting_date(q, rid, table, filter_col, freq, ctx=ctx)
                res = service.compute_variance(
                    return_id=rid, table_name=table, reporting_date=dates[0],
                    reporting_period=periods, execute_query_fn=execute_query,
                    comparison_dates=dates, ctx=ctx,
                )
                ok = not res.get("error") and res.get("rows")
                detail = res.get("error") or f"{len(res.get('rows', []))} rows"
            except Exception as exc:
                ok, dates, notes, detail = False, [], [], f"{type(exc).__name__}: {exc}"
            if not ok:
                failures.append((table, q, detail))
            if args.verbose or not ok:
                mark = "ok  " if ok else "FAIL"
                print(f"  {mark} {q!r:42} -> {dates} | {detail}" + (f" | {notes}" if notes else ""))
        print()

    print(f"{total - len(failures)}/{total} scenario runs executed with rows"
          f"{f' ({skipped} table(s) with no data skipped)' if skipped else ''}")
    for table, q, detail in failures:
        print(f"  FAIL {table}: {q!r} -> {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
