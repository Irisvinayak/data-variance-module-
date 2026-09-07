# scoped_retriever.py — stage 2 of return-scoped NL resolution.
#
# The manual wizard never guesses: the user names the return, picks the table
# from that return's mapping, and picks a date proven to have data. The NL path
# should be staged the same way, and this module is the middle stage:
#
#   stage 1  which RETURN            — exact, from the query's named return or
#                                      the user's clarification answer (main.py)
#   stage 2  which TABLE and COLUMN  — THIS MODULE, embeddings scoped to that
#                                      return's tables only
#   stage 3  which DATE              — resolved against real submissions
#                                      (date_resolver.py)
#
# Why it exists: main.py's _shortlist_for_return used to hand the rest of the
# pipeline a shortlist built with NO reference to the query at all — tables in
# table-mapping XML order, every column of every table in raw index order, and
# a confidence hardcoded to 0.5/1.0. Downstream, intent_resolver.py shows the
# LLM only the first INTENT_MAX_COLS_PER_TABLE columns while validating against
# the full list, so a correct column sitting past that cap in an arbitrary
# order was not merely deprioritised — it was unreachable.
#
# Scope is never widened here. If the user named a return, answering from a
# different one is a silent wrong answer, so this module ranks strictly within
# the tables it is given and returns an empty shortlist rather than reaching
# outside it.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import confidence as confidence_mod
from . import ranking, schema_info
from .embedder import embed_query
from .index_store import subset_search
from .lexical_search import search_bm25
from .nlp_config import (
    BM25_INDEX_PATH,
    BM25_SIGNAL_WEIGHT,
    BM25_TOP_K,
    COLUMN_INDEX_PATH,
    COLUMN_META_PATH,
    ROW_LABEL_INDEX_PATH,
    ROW_LABEL_META_PATH,
    TABLE_INDEX_PATH,
    TABLE_META_PATH,
    TIE_EPSILON,
    TOP_K_LABELS,
)
from .query_analyzer import QueryAnalysis, analyze_query

logger = logging.getLogger(__name__)


def _empty(reason: str) -> Dict[str, Any]:
    logger.info("[nlp.scoped_retriever] empty shortlist — %s", reason)
    return {
        "tables": [],
        "columns": [],
        "matched_labels": [],
        "table_confidence": 0.0,
        "table_ambiguous": False,
    }


def rank_within_tables(
    query: str,
    tables: Sequence[Mapping[str, Any]],
    login_id: str = "",
    *,
    analysis: Optional[QueryAnalysis] = None,
) -> Dict[str, Any]:
    """Rank an ALREADY-DETERMINED set of tables, and the columns inside them,
    against what the query actually asks for.

    `tables` are shortlist-shaped records — {"table", "return_id",
    "return_name", "filter_col", "report_freq"} — as main.py already builds
    them. They come back in ranked order with their original keys intact,
    because filter_col/report_freq come from this app's XML and are not
    recoverable from the embedding index, yet compute_variance needs them.

    Every table given is presumed already authorized and already
    return_id-resolved by the caller; this module makes no authorization
    decision, exactly as retriever.py's header argues (one place decides
    access, and it is not here).

    Returns the same shape retriever.get_relevant_schema does, so the two are
    interchangeable to callers:
      {"tables", "columns", "matched_labels", "table_confidence", "table_ambiguous"}
    """
    if not tables:
        return _empty("caller supplied no tables")

    if analysis is None:
        analysis = analyze_query(query)

    # metric_text is the query with the return name, dates and request
    # boilerplate stripped — the words that actually describe the DATA. On this
    # path the return is already pinned, so leaving its name in would be pure
    # noise: every table under it embeds that name and matches it equally.
    search_text = analysis.metric_text or analysis.normalized or query
    q_vec = embed_query(search_text)

    # Canonical (caller-cased) name per uppercase key, so index records — which
    # carry lowercase names — can be rewritten to the names the caller and
    # everything downstream compare against. Same rewrite _shortlist_for_return
    # has always done, kept here so callers stop having to remember it.
    canonical: Dict[str, str] = {}
    meta_by_name: Dict[str, Mapping[str, Any]] = {}
    for record in tables:
        name = record["table"]
        canonical[name.upper()] = name
        meta_by_name[name.upper()] = record
    scope = list(canonical)

    def _canonicalize(hits):
        """Rewrite each hit's "table" to the caller's casing.

        Index records are lowercase while the caller's names come from this
        app's XML and are uppercase. Fusing them un-normalized scores the same
        table under two different keys, which silently doubles every table in
        the ranking and duplicates its whole column list downstream.
        """
        return [
            (score, {**record, "table": canonical[record["table"].upper()]})
            for score, record in hits
            if record["table"].upper() in canonical
        ]

    col_hits = _canonicalize(subset_search(COLUMN_INDEX_PATH, COLUMN_META_PATH, q_vec, scope))
    label_hits = _canonicalize(subset_search(
        ROW_LABEL_INDEX_PATH, ROW_LABEL_META_PATH, q_vec, scope, k=TOP_K_LABELS * 3
    ))
    table_hits = _canonicalize(subset_search(TABLE_INDEX_PATH, TABLE_META_PATH, q_vec, scope))

    # BM25's index is table-level and has no subset parameter, so it is
    # post-filtered to the scope rather than searched within it. That is
    # imperfect — a scoped table can fall outside BM25's global top-K and score
    # nothing — but MEASUREMENT says include it anyway: on the 150-case
    # benchmark, scoped table top-1 is 57% without this signal and 88% with it.
    # (The original design excluded BM25 on the reasoning that with only a
    # handful of same-return candidates the dense column signal would dominate.
    # That reasoning was wrong: near-identical sibling tables inside one return
    # — Part A vs Part B vs Part C of the same section — are exactly the case
    # dense similarity cannot separate and a literal term match can.)
    bm25_hits = _canonicalize(
        search_bm25(BM25_INDEX_PATH, search_text, BM25_TOP_K)
    ) if BM25_SIGNAL_WEIGHT else []

    # ── Fuse, using the SAME arithmetic as the global path (ranking.py) ──────
    # The QA strong-match is deliberately absent: a QA hit pins a table
    # GLOBALLY, and honouring one here could jump outside the return the user
    # named — the one thing this stage must never do.
    scores: Dict[str, float] = {}
    signal_leaders: set = set()
    texts_by_table: Dict[str, List[str]] = {}

    for hits, weight in (
        (col_hits, 2.0),
        (label_hits, 2.0),
        (table_hits, 1.0),
        (bm25_hits, BM25_SIGNAL_WEIGHT),
    ):
        ranking.fuse_best_hit_per_table(
            hits, weight, scores,
            signal_leaders=signal_leaders, texts_by_table=texts_by_table,
        )

    # Tables the index knows nothing about still belong in the shortlist — the
    # caller decided they are in scope, and dropping them here would silently
    # narrow the user's own choice. They simply score 0.
    for key in scope:
        scores.setdefault(canonical[key], 0.0)

    query_tokens = ranking.tokens(search_text)
    lexical_overlaps = {
        tbl: ranking.lexical_overlap(query_tokens, " ".join(texts_by_table.get(tbl, [])))
        for tbl in scores
    }
    for tbl, overlap in lexical_overlaps.items():
        scores[tbl] += overlap * 0.03

    # Name is the tie-break so the order is stable across runs rather than
    # depending on dict insertion order.
    ranked_names = sorted(scores, key=lambda t: (-scores[t], t.upper()))
    ranked_pairs = [(name, scores[name]) for name in ranked_names]

    table_confidence, table_ambiguous = confidence_mod.table_confidence(
        ranked_pairs, False, lexical_overlaps, TIE_EPSILON,
        signal_leaders=signal_leaders,
    )

    ranked_tables = [
        {**meta_by_name[name.upper()], "table": canonical[name.upper()], "score": score}
        for name, score in ranked_pairs
    ]

    columns = _rank_columns(q_vec, [t["table"] for t in ranked_tables], canonical)

    matched_labels: List[Dict[str, Any]] = []
    seen_labels = set()
    for _, lbl in label_hits:
        key = (lbl["table"].upper(), lbl["column"], lbl["value"])
        if key in seen_labels:
            continue
        seen_labels.add(key)
        matched_labels.append(lbl)
    matched_labels = matched_labels[:TOP_K_LABELS]

    logger.info(
        "[nlp.scoped_retriever] query=%r | login_id=%r | search_text=%r | scope=%d table(s) | "
        "top=%s | confidence=%.3f | ambiguous=%s | %d column(s), %d label(s)",
        query, login_id, search_text, len(scope),
        ranked_tables[0]["table"] if ranked_tables else None,
        table_confidence, table_ambiguous, len(columns), len(matched_labels),
    )

    return {
        "tables": ranked_tables,
        "columns": columns,
        "matched_labels": matched_labels,
        "table_confidence": table_confidence,
        "table_ambiguous": table_ambiguous,
    }


def _rank_columns(
    q_vec, table_order: List[str], canonical: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Every column of every scoped table, grouped by table rank and ordered
    within a table by relevance.

    COMPLETE on purpose — no similarity threshold. This list IS the set
    intent_resolver._validate_grounding accepts from, so dropping a
    low-similarity column would make it permanently unselectable, which is
    precisely the failure this module exists to remove. Ordering is the lever;
    membership is not.

    Non-metric columns are DEMOTED, not removed. schema.json knows that of 527
    columns only 389 are numeric, and embedding similarity alone will happily
    rank a label above a measure — measured: "provisions on npa" ranks the
    varchar2 column movement_provision_npa first, and "outstanding amount
    standard assets" ranks the varchar2 column "assets" in positions 1, 2 AND 3.
    Those are row labels; selecting one as the answer column yields a variance
    table with no numbers in it. But they remain legitimate row-label matches
    and legitimate evidence for the table, so they stay selectable — just below
    every real measure.
    """
    out: List[Dict[str, Any]] = []
    for table in table_order:
        hits = subset_search(COLUMN_INDEX_PATH, COLUMN_META_PATH, q_vec, [table])
        ordered = sorted(
            hits,
            key=lambda hit: (
                not schema_info.is_metric_column(table, hit[1]["column"]),  # metrics first
                -hit[0],                                                     # then similarity
            ),
        )
        out.extend(
            {**record, "table": canonical.get(table.upper(), table)}
            for _, record in ordered
        )
    return out
