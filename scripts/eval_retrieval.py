"""
eval_retrieval.py — fast, no-LLM, no-Oracle retrieval accuracy check.

get_relevant_schema() only touches the FAISS/BM25 indices in artifacts/nlp-index/
and this app's own XML config (Returns.xml, table-mapping files, XML_User.xml)
for return_id/auth resolution — no Ollama call, no Oracle connection. That
makes it cheap enough to run after every embedding rebuild or retriever.py
change, to catch a retrieval-quality regression before it ever reaches an
LLM call or a real query.

Run from the project root:
    backend/.venv/Scripts/python.exe scripts/eval_retrieval.py                  # the six built-in cases
    ... scripts/eval_retrieval.py --cases-file my_cases.jsonl                    # your own cases
    ... scripts/eval_retrieval.py --from-index 300                               # cases derived from the index
    ... scripts/eval_retrieval.py --from-index 300 --json baseline.json          # save the result
    ... scripts/eval_retrieval.py --from-index 300 --baseline baseline.json --max-drop 0.02

The last form exits 1 when top-1 or top-k accuracy fell by more than --max-drop, which is how a
retrieval change (M-21, M-22, L-10, L-11 in docs/ISSUES_STATUS.md) is gated. The grading and
comparison logic lives in scripts/retrieval_bench.py so it is unit-tested without the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT_DIR, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import retrieval_bench as rb  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Retrieval accuracy benchmark (no LLM, no Oracle).")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--cases-file", help="JSON-lines file: {\"query\", \"expected\", \"login_id\"?}")
    src.add_argument("--from-index", type=int, metavar="N", help="derive N cases from the shipped index (0 = all)")
    p.add_argument("--seed", type=int, default=7, help="sampling seed for --from-index")
    p.add_argument("--login", default="iris810", help="login used for the retrieval's authorisation filter")
    p.add_argument("--json", dest="json_out", help="write the summary to this file")
    p.add_argument("--baseline", help="summary saved by an earlier --json run to compare against")
    p.add_argument("--max-drop", type=float, default=0.0, help="allowed accuracy drop as a fraction (0.02 = 2 points)")
    p.add_argument("--quiet", action="store_true", help="print only the summary and failures")
    return p.parse_args(argv)


def select_cases(args):
    if args.cases_file:
        return rb.load_cases_file(args.cases_file, args.login)
    if args.from_index is not None:
        return rb.index_cases(None if args.from_index == 0 else args.from_index, args.seed, args.login)
    return rb.built_in_cases(args.login)


def main(argv=None) -> int:
    args = parse_args(argv)
    cases = select_cases(args)

    from backend.config import RequestContext
    from backend.nlp.retriever import get_relevant_schema

    graded, seconds, misses = [], [], []
    for case in cases:
        start = time.perf_counter()
        result = get_relevant_schema(case.query, RequestContext(login_id=case.login_id))
        elapsed = time.perf_counter() - start
        seconds.append(elapsed)

        ranked = [t["table"].lower() for t in result["tables"]]
        outcome = rb.grade(case, ranked)
        graded.append(outcome)
        if outcome == "miss":
            misses.append((case, ranked))
        if not args.quiet:
            print(f"[{outcome.upper():4}] {elapsed*1000:6.1f}ms  {case.query!r}")
            print(f"        expected={sorted(case.expected)}  got={ranked}")

    summary = rb.summarize(graded, seconds)
    print()
    print(f"cases:          {summary['cases']}")
    print(f"top-1 accuracy: {summary['top1']}/{summary['cases']} ({summary['top1_rate']:.0%})")
    print(f"top-k accuracy: {summary['topk']}/{summary['cases']} ({summary['topk_rate']:.0%})")
    print(f"avg latency:    {summary['avg_ms']:.1f}ms/query")

    if misses:
        print()
        print(f"{len(misses)} MISS(ES):")
        for case, ranked in misses[:50]:
            print(f"  - {case.query!r} -> expected {sorted(case.expected)}, got {ranked}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print(f"summary written to {args.json_out}")

    if args.baseline:
        with open(args.baseline, encoding="utf-8") as fh:
            baseline = json.load(fh)
        problems = rb.compare(summary, baseline, args.max_drop)
        for problem in problems:
            print(f"REGRESSION: {problem}")
        print("no regression against the baseline" if not problems else f"{len(problems)} regression(s)")
        return 1 if problems else 0

    # Without a baseline the exit code keeps its old meaning: non-zero if anything missed entirely.
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
