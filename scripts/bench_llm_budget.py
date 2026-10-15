"""Measure how often a model's SQL reply is cut off at a given token cap (code review M-19).

The question behind M-19 is "is num_predict=128 too small for multi-column SQL?". That can only be
answered against the real model, so this script does NOT change any limit: it runs the same
questions at several caps and reports, per cap, how many replies stopped because they hit it
(`done_reason == "length"`), how long they took and how long the replies were.

    python scripts/bench_llm_budget.py --questions my_questions.txt --budgets 128,256,512
    python scripts/bench_llm_budget.py --questions my_questions.txt --budgets 128,512 --json out.json

Needs a reachable Ollama endpoint (DV_NLP_OLLAMA_URL), the model (DV_NLP_OLLAMA_MODEL) and, for
building each prompt, the embedding index. Run it on a machine that has them. It sends nothing but
the question prompts and stores nothing but the summary. The aggregation (`sweep`) is a plain
function tested with a fake model in tests/test_llm_budget_bench.py; those tests prove the
arithmetic, not any model's behaviour.

How to read the result: pick the smallest cap whose cut-off rate is ~0 on questions that resemble
real use. Then set DV_SQL_NUM_PREDICT to it on the server. Larger caps cost latency only when the
model actually writes more, but that is exactly what this measures.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CallFn = Callable[[str, int], dict[str, Any]]


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1) of a non-empty list."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def sweep(questions: list[str], budgets: list[int], call: CallFn) -> dict[int, dict[str, Any]]:
    """Run every question at every cap. `call(question, cap)` returns
    {"response": str, "done_reason": str | None, "latency_ms": float}; an exception counts as a failed call."""
    if not questions or not budgets:
        raise ValueError("need at least one question and one budget")
    out: dict[int, dict[str, Any]] = {}
    for cap in budgets:
        cut = failed = 0
        latencies: list[float] = []
        lengths: list[int] = []
        for question in questions:
            try:
                reply = call(question, cap)
            except Exception:  # noqa: BLE001 - a failed call is a data point, not a crash
                failed += 1
                continue
            latencies.append(float(reply.get("latency_ms", 0.0)))
            lengths.append(len(reply.get("response") or ""))
            if reply.get("done_reason") == "length":
                cut += 1
        answered = len(questions) - failed
        out[cap] = {
            "questions": len(questions),
            "answered": answered,
            "failed": failed,
            "cut_off": cut,
            "cut_off_rate": round(cut / answered, 4) if answered else None,
            "p50_ms": round(percentile(latencies, 0.5), 1) if latencies else None,
            "p95_ms": round(percentile(latencies, 0.95), 1) if latencies else None,
            "avg_reply_chars": round(sum(lengths) / len(lengths), 1) if lengths else None,
        }
    return out


def recommend(result: dict[int, dict[str, Any]], max_cut_off_rate: float = 0.0) -> int | None:
    """The smallest cap whose cut-off rate is within `max_cut_off_rate`, or None if none is."""
    for cap in sorted(result):
        rate = result[cap]["cut_off_rate"]
        if rate is not None and rate <= max_cut_off_rate:
            return cap
    return None


def read_questions(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"{path}: no questions found")
    return lines


def build_real_call(url: str, model: str, timeout: float) -> CallFn:
    """The real model call, one prompt per question (built with the app's own prompt builder)."""
    import requests

    from backend.config import RequestContext
    from backend.nlp.retriever import get_relevant_schema
    from backend.nlp.sql_generator import build_prompt

    ctx = RequestContext(login_id=os.environ.get("BENCH_LOGIN", "iris810"))
    prompts: dict[str, str] = {}

    def call(question: str, cap: int) -> dict[str, Any]:
        if question not in prompts:
            shortlist = get_relevant_schema(question, ctx)
            prompts[question] = build_prompt(question, shortlist["tables"], shortlist["columns"])
        started = time.perf_counter()
        response = requests.post(
            url,
            json={"model": model, "prompt": prompts[question], "stream": False, "options": {"num_predict": cap}},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        return {
            "response": body.get("response", ""),
            "done_reason": body.get("done_reason"),
            "latency_ms": (time.perf_counter() - started) * 1000,
        }

    return call


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cut-off rate of the SQL model's replies at several token caps.")
    parser.add_argument("--questions", required=True, help="text file, one question per line (# comments allowed)")
    parser.add_argument("--budgets", default="128,256,512", help="comma-separated num_predict values")
    parser.add_argument("--url", default=None, help="Ollama generate URL (default: DV_NLP_OLLAMA_URL)")
    parser.add_argument("--model", default=None, help="model name (default: DV_NLP_OLLAMA_MODEL)")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json", dest="json_out", help="write the summary to this file")
    args = parser.parse_args(argv)

    from backend.nlp import nlp_config

    url = args.url or nlp_config.OLLAMA_URL
    model = args.model or nlp_config.OLLAMA_MODEL
    questions = read_questions(args.questions)
    budgets = [int(b) for b in args.budgets.split(",") if b.strip()]
    print(f"{len(questions)} question(s) x {len(budgets)} cap(s) against {model}")

    result = sweep(questions, budgets, build_real_call(url, model, args.timeout))
    print(f"{'cap':>6} {'answered':>9} {'cut off':>8} {'rate':>7} {'p50 ms':>8} {'p95 ms':>8} {'avg chars':>10}")
    for cap, r in result.items():
        rate = "-" if r["cut_off_rate"] is None else f"{r['cut_off_rate']:.0%}"
        print(f"{cap:>6} {r['answered']:>9} {r['cut_off']:>8} {rate:>7} {r['p50_ms']!s:>8} {r['p95_ms']!s:>8} {r['avg_reply_chars']!s:>10}")
    pick = recommend(result)
    print(f"smallest cap with no cut-off replies: {pick if pick is not None else 'none of the tested caps'}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in result.items()}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
