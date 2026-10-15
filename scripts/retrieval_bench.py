"""Benchmark plumbing for the retrieval evaluation (no model, no index, no database needed).

Kept separate from scripts/eval_retrieval.py so the parts that decide whether a retrieval
change is an improvement - case loading, grading, summarising, comparing with a saved
baseline - are plain functions that can be unit-tested. eval_retrieval.py supplies the part
that needs the embedding model: calling get_relevant_schema().

Why this exists (code review M-21, M-22, L-10, L-11): each of those is a retrieval change
that must not lower top-1 / top-3 accuracy, and six hand-written cases cannot show that. The
workflow is

    python scripts/eval_retrieval.py --from-index 300 --json baseline.json   # before the change
    ... make the change ...
    python scripts/eval_retrieval.py --from-index 300 --baseline baseline.json

and the second run exits 1 if accuracy dropped by more than --max-drop. No result is stored
in the repository: numbers are only meaningful for the index and corpus they were run on.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple


class Case(NamedTuple):
    query: str
    expected: frozenset[str]       # any of these tables is a correct top answer (lowercase)
    login_id: str = "iris810"


# The original six hand-written cases, unchanged, so the default run is exactly as before.
BUILT_IN = [
    ("What is the total gross NPA for the quarter", "cims_raq_q_sec8_sec_credit"),
    ("provision made for possible losses on doubtful advances, comparing domestic and overseas operations",
     "cims_raq_q_sec2_part_a"),
    ("SEC2_PART_A risk category standard provision", "cims_raq_q_sec2_part_a"),
    ("total loan assets domestic", "cims_raq_q_sec1_part_a_dom"),
    ("infra sector credit breakup", "cims_raq_q_sec8_infra_brkup"),
    ("sensitivity to securities part b overseas", "cims_raq_q_sec9_sensec_partb"),
]


def built_in_cases(login_id: str = "iris810") -> list[Case]:
    return [Case(q, frozenset({t.lower()}), login_id) for q, t in BUILT_IN]


def load_cases_file(path: str, default_login: str = "iris810") -> list[Case]:
    """JSON-lines: {"query": "...", "expected": "table" | ["table", ...], "login_id": "..."}.

    Blank lines and lines starting with # are ignored. A malformed line is an error that names
    its line number: a silently skipped case would make a benchmark look better than it is.
    """
    cases: list[Case] = []
    with open(path, encoding="utf-8") as fh:
        for number, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
                query = str(obj["query"]).strip()
                expected = obj["expected"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{path}:{number}: not a valid case ({exc.__class__.__name__}: {exc})") from None
            tables = [expected] if isinstance(expected, str) else list(expected)
            if not query or not tables or not all(isinstance(t, str) and t.strip() for t in tables):
                raise ValueError(f"{path}:{number}: a case needs a query and at least one expected table")
            cases.append(Case(query, frozenset(t.strip().lower() for t in tables), str(obj.get("login_id") or default_login)))
    if not cases:
        raise ValueError(f"{path}: no cases found")
    return cases


def grade(case: Case, ranked: list[str], k: int | None = None) -> str:
    """"top1" | "topk" | "miss". `ranked` is the retriever's table list, best first."""
    names = [r.lower() for r in ranked]
    if names and names[0] in case.expected:
        return "top1"
    if any(n in case.expected for n in (names if k is None else names[:k])):
        return "topk"
    return "miss"


def summarize(graded: list[str], seconds: list[float] | None = None) -> dict[str, Any]:
    n = len(graded)
    if n == 0:
        raise ValueError("no cases were graded")
    top1 = graded.count("top1")
    topk = top1 + graded.count("topk")
    out: dict[str, Any] = {
        "cases": n, "top1": top1, "topk": topk,
        "top1_rate": round(top1 / n, 4), "topk_rate": round(topk / n, 4),
    }
    if seconds:
        out["avg_ms"] = round(1000 * sum(seconds) / len(seconds), 1)
    return out


def compare(current: dict[str, Any], baseline: dict[str, Any], max_drop: float = 0.0) -> list[str]:
    """Problems when `current` is worse than `baseline` by more than `max_drop` (a fraction,
    0.02 = two points). Different case counts make the comparison meaningless, so that is
    reported too."""
    problems = []
    if current["cases"] != baseline["cases"]:
        problems.append(f"case count differs ({current['cases']} now, {baseline['cases']} in the baseline)")
    for key, label in (("top1_rate", "top-1"), ("topk_rate", "top-k")):
        drop = baseline[key] - current[key]
        if drop > max_drop + 1e-9:
            problems.append(f"{label} accuracy fell from {baseline[key]:.1%} to {current[key]:.1%}")
    return problems


def index_cases(sample: int | None, seed: int = 7, login_id: str = "iris810") -> list[Case]:
    """Cases generated from the shipped index (needs scripts/_bench_cases.py and the index)."""
    from _bench_cases import build_cases

    return [Case(c.query, frozenset(t.lower() for t in c.tables), login_id) for c in build_cases(sample, seed)]
