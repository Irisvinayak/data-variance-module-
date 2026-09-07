# ranking.py — shared ranking primitives for the retrieval layer.
#
# Extracted VERBATIM from retriever.py so that backend/nlp/scoped_retriever.py
# (return-scoped stage-2 retrieval) can reuse the exact same fusion arithmetic
# instead of growing a second, subtly-different copy of it. Two copies of a
# scoring rule that are supposed to agree but drift is precisely how the
# "columns arrive similarity-ranked" assumption in intent_resolver.py rotted.
#
# This module is deliberately behaviour-preserving: retriever.py keeps
# module-level aliases (_rrf = ranking.rrf, ...) and its call sites are
# rewritten to these helpers only where the loop body was already identical.
# The extraction commit's acceptance criterion is that scripts/eval_retrieval.py
# produces byte-identical output before and after.

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .nlp_config import RRF_K

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def rrf(rank: int, k: int = RRF_K) -> float:
    """Reciprocal-rank contribution. See nlp_config.RRF_K for why k is 5 here
    and not the textbook 60 — at this corpus size 60 flattens the top-5 to
    within 7% of each other and rank stops meaning anything."""
    return 1.0 / (k + rank + 1)


def tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def lexical_overlap(query_tokens: set, text: str) -> float:
    """Fraction of query tokens also present in `text` — a cheap lexical
    signal layered on top of cosine similarity. Pure embedding similarity
    can't tell "TOT_EXPO_DOM" (literal term match) apart from a merely
    topic-adjacent column at nearly the same cosine score; exact word overlap
    is a cheap, precise tie-breaker for exactly that situation."""
    if not query_tokens:
        return 0.0
    text_tokens = tokens(text)
    if not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def fuse_best_hit_per_table(
    hits: Sequence[Tuple[float, Mapping[str, Any]]],
    weight: float,
    scores: Dict[str, float],
    *,
    all_table_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    signal_leaders: Optional[Set[str]] = None,
    texts_by_table: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Accumulate one retrieval signal into `scores`, in place.

    Scores each table by its BEST-ranked hit in this signal only — NOT summed
    across every hit that table happens to contribute. Summing let a table with
    many loosely-relevant columns (e.g. 8 generic risk/finance column names all
    embedding "close enough") out-accumulate a table with a single, precisely-
    matching column, which is backwards: depth of one exact match should beat
    breadth of several mediocre ones.

    The table holding this signal's #1 hit is recorded in `signal_leaders`.
    That set is the strongest single predictor of a correct answer that this
    pipeline has — measured over the benchmark, a table leading >=1 signal is
    81% correct while one leading none is 0% — and it is deliberately NOT
    inferrable from `scores`, because breadth across signals correlates with
    being WRONG (present in 4 signals: 62% correct; in 3: 90%). See
    confidence.table_confidence.

    Mutates `scores`, and optionally `all_table_meta`, `signal_leaders` and
    `texts_by_table`, so several signals can be fused into one accumulator —
    which is what both the global and the scoped caller do.
    """
    scored: set = set()
    for rank, (_, record) in enumerate(hits):
        tbl = record["table"]
        if all_table_meta is not None:
            all_table_meta.setdefault(tbl, {"table": tbl})
        if texts_by_table is not None:
            texts_by_table.setdefault(tbl, []).append(record.get("text", ""))
        if tbl in scored:
            continue
        if not scored and signal_leaders is not None:
            signal_leaders.add(tbl)
        scored.add(tbl)
        scores[tbl] = scores.get(tbl, 0.0) + rrf(rank) * weight
