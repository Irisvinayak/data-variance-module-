# retriever.py — Runtime retrieval: NL query -> authorized table/column shortlist.
# Consumes the vector store (embedder/index_store) and auth_service (existing,
# untouched department-access function) but is not part of either — this is
# the seam between "vectors" and "what this specific user is allowed to see".
#
# The embedding index itself is built by an EXTERNAL tool and just dropped
# into backend/output/ (nlp_config.INDEX_DIR) — its records only carry
# {"text", "table"} / {"text", "table", "column"}, no return_id. So this
# module never trusts return_id from the embedding metadata; it always
# resolves table_name -> return_id live via return_lookup.py (this app's own
# Returns.xml + table-mapping XML), which is also what auth/compute_variance
# already trust. That keeps the embedding index swappable/rebuildable by any
# tool without ever touching authorization.

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from ..auth_service import get_allowed_form_ids
from ..config import AUTH_ENABLED
from . import return_lookup
from .embedder import embed_query
from .index_store import meta_by_table, search
from .lexical_search import search_bm25, search_qa_strong_match
from .query_normalizer import normalize_query
from .nlp_config import (
    BM25_INDEX_PATH,
    BM25_SIGNAL_WEIGHT,
    BM25_TOP_K,
    COLUMN_INDEX_PATH,
    COLUMN_META_PATH,
    MIN_COLUMN_SCORE,
    MIN_TABLE_SCORE,
    QA_INDEX_PATH,
    QA_META_PATH,
    QA_PAIRS_PATH,
    QA_PREFILTER_TOP_N,
    QA_STRONG_MATCH_BONUS,
    QA_STRONG_MATCH_THRESHOLD,
    ROW_LABEL_INDEX_PATH,
    ROW_LABEL_META_PATH,
    TABLE_INDEX_PATH,
    TABLE_META_PATH,
    TOP_K_COLUMNS,
    TOP_K_LABELS,
    TOP_K_TABLES,
)

logger = logging.getLogger(__name__)

# Backup/duplicate table copies (e.g. CIMS_RAQ_M_SEC9_SENSEC_PARTB_bckup,
# ..._BK, ..._bkup) are near-identical to their primary table and otherwise
# eat shortlist slots that should go to genuinely different candidates.
_BACKUP_SUFFIX_RE = re.compile(r"_(bckup|bkup|bk)$", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _rrf(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank + 1)


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def _lexical_overlap(query_tokens: set, text: str) -> float:
    """Fraction of query tokens also present in `text` — a cheap lexical
    signal layered on top of cosine similarity. Pure embedding similarity
    can't tell "TOT_EXPO_DOM" (literal term match) apart from a merely
    topic-adjacent column at nearly the same cosine score; exact word overlap
    is a cheap, precise tie-breaker for exactly that situation."""
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def get_relevant_schema(query: str, login_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Embed `query`, search the table/column FAISS indices, RRF-fuse them into
    a ranked table shortlist, then filter that shortlist down to only tables
    belonging to return_ids `login_id`'s department is allowed to access
    (reusing auth_service.get_allowed_form_ids — no auth logic duplicated here).

    When AUTH_ENABLED=false (dev bypass, same flag auth_deps.py already
    honors), the authorization filter is skipped entirely — every retrieved
    table is treated as authorized, same as require_login/require_return_access
    do in that mode.

    Returns {"tables": [...], "columns": [...], "matched_labels": [...]} — all
    three possibly empty. matched_labels feeds sql_generator.build_prompt()'s
    "STORAGE FORMAT: VERTICAL" row-label rules.
    """
    # Normalize known domain typos/abbreviations ("exposer"->"exposure", "std"->
    # "standard", ...) before embedding — cosine similarity over sentence
    # embeddings is sensitive to misspelled/informal terms landing the query
    # vector in the wrong neighborhood. The ORIGINAL query is still what's
    # logged/shown/sent to intent_resolver; only the text fed to the embedder
    # and the lexical-overlap check below is normalized.
    normalized_query = normalize_query(query)
    if normalized_query != query:
        logger.info("[nlp.retriever] query normalized: %r -> %r", query, normalized_query)
    query_tokens = _tokens(normalized_query)

    q_vec = embed_query(normalized_query)

    # ── Column-first / label-first retrieval ──────────────────────────────────
    # Queries name a metric or a value ("total risk assets for standard"), not
    # a table description — so columns and row-label values are searched FIRST
    # and drive table ranking. Table-description similarity is searched last
    # and only acts as a secondary/supporting signal, otherwise a table whose
    # description happens to echo the query's words out-ranks the table that
    # actually contains the matching column/value (e.g. a column literally
    # named STANDARD_DOM winning over the correct RISK_CATEGORY='Standard'
    # row in a different, better-fitting table).
    col_hits = search(COLUMN_INDEX_PATH, COLUMN_META_PATH, q_vec, TOP_K_COLUMNS * 4, min_score=MIN_COLUMN_SCORE)
    label_hits = search(ROW_LABEL_INDEX_PATH, ROW_LABEL_META_PATH, q_vec, TOP_K_LABELS * 3)
    table_hits = search(TABLE_INDEX_PATH, TABLE_META_PATH, q_vec, TOP_K_TABLES * 3, min_score=MIN_TABLE_SCORE)
    bm25_hits = search_bm25(BM25_INDEX_PATH, normalized_query, BM25_TOP_K) if BM25_SIGNAL_WEIGHT else []

    all_table_meta: Dict[str, Dict[str, Any]] = {}
    scores: Dict[str, float] = {}

    # Primary signal #1: matching columns. Score by each table's BEST-ranked
    # column hit only (first occurrence in the already-sorted hit list) — NOT
    # summed across every column that table happens to have in the results.
    # Summing let a table with many loosely-relevant columns (e.g. 8 generic
    # risk/finance column names all embedding "close enough") out-accumulate
    # a table with a single, precisely-matching column, which is backwards:
    # depth of one exact match should beat breadth of several mediocre ones.
    col_tables_scored: set = set()
    for rank, (_, c) in enumerate(col_hits):
        tbl = c["table"]
        all_table_meta.setdefault(tbl, {"table": tbl})
        if tbl in col_tables_scored:
            continue
        col_tables_scored.add(tbl)
        scores[tbl] = scores.get(tbl, 0.0) + _rrf(rank) * 2.0

    # Primary signal #2: matching row-label values (e.g. RISK_CATEGORY="Standard") —
    # weighted equally with columns since a value match is just as strong evidence
    # of the right table as a column-name match. Same best-hit-only rule applies.
    label_tables_scored: set = set()
    for rank, (_, lbl) in enumerate(label_hits):
        tbl = lbl["table"]
        all_table_meta.setdefault(tbl, {"table": tbl})
        if tbl in label_tables_scored:
            continue
        label_tables_scored.add(tbl)
        scores[tbl] = scores.get(tbl, 0.0) + _rrf(rank) * 2.0

    # Secondary/supporting signal: table description similarity — breaks ties
    # and surfaces tables whose only good match is the description, but no
    # longer dominates over an actual column/value hit.
    texts_by_table: Dict[str, List[str]] = {}
    for rank, (_, t) in enumerate(table_hits):
        all_table_meta[t["table"]] = {**all_table_meta.get(t["table"], {}), **t}
        scores[t["table"]] = scores.get(t["table"], 0.0) + _rrf(rank) * 1.0
        texts_by_table.setdefault(t["table"], []).append(t.get("text", ""))
    for _, c in col_hits:
        texts_by_table.setdefault(c["table"], []).append(c.get("text", ""))
    for _, lbl in label_hits:
        texts_by_table.setdefault(lbl["table"], []).append(lbl.get("text", ""))

    # Signal: BM25 lexical/term-frequency match. Dense cosine smooths over
    # exact structural markers (e.g. near-identical "Part A"/"Part B" tables
    # scoring within noise of each other); BM25 catches the literal term
    # instead. Fused via RRF RANK (not raw score) since raw BM25 (~0-20+)
    # isn't on cosine's scale (0-1) — comparing them directly would let BM25
    # dominate or vanish depending on corpus size. Same best-hit-only-per-
    # table rule as columns/labels above.
    bm25_tables_scored: set = set()
    for rank, (_, bt) in enumerate(bm25_hits):
        tbl = bt["table"]
        all_table_meta.setdefault(tbl, {"table": tbl})
        texts_by_table.setdefault(tbl, []).append(bt.get("text", ""))
        if tbl in bm25_tables_scored:
            continue
        bm25_tables_scored.add(tbl)
        scores[tbl] = scores.get(tbl, 0.0) + _rrf(rank) * BM25_SIGNAL_WEIGHT

    # Signal: QA strong-match — if this question is a near-duplicate of a
    # known verified example, pin its table. The bonus (10.0) dwarfs any
    # realistic RRF sum (~0.1 max), so it wins the sort at the cutoff below
    # without a separate "force to front" code path. If it turns out
    # unauthorized/return-id-unresolved, the existing drop/auth logic further
    # down removes it exactly like any other candidate — no special-casing.
    qa_table = (
        search_qa_strong_match(
            QA_PAIRS_PATH, query, QA_STRONG_MATCH_THRESHOLD,
            qa_index_path=QA_INDEX_PATH, qa_meta_path=QA_META_PATH,
            query_vector=q_vec, prefilter_top_n=QA_PREFILTER_TOP_N,
        )
        if QA_STRONG_MATCH_THRESHOLD
        else None
    )
    if qa_table:
        # qa_pairs.json's table names may differ in case from the FAISS/BM25
        # meta's table names (e.g. "CIMS_..." vs "cims_..." — different build
        # pipelines). Reuse an existing case-insensitively-matching key if the
        # table was already scored by another signal, so this doesn't create
        # a duplicate table entry under a different case.
        existing = next((t for t in scores if t.upper() == qa_table.upper()), None)
        canonical_qa_table = existing or qa_table
        all_table_meta.setdefault(canonical_qa_table, {"table": canonical_qa_table})
        scores[canonical_qa_table] = scores.get(canonical_qa_table, 0.0) + QA_STRONG_MATCH_BONUS
        logger.info(
            "[nlp.retriever] query=%r | QA strong-match pinned table=%r", query, canonical_qa_table
        )

    # Tertiary signal: exact lexical term overlap — a small, bounded tie-breaker
    # (max contribution ~0.03, on par with one primary rrf hit) layered on top
    # of cosine similarity, not a replacement for it. See _lexical_overlap.
    for tbl in scores:
        overlap = _lexical_overlap(query_tokens, " ".join(texts_by_table.get(tbl, [])))
        scores[tbl] += overlap * 0.03

    # Dedupe backup/duplicate table copies (same section, "_bckup"/"_bkup"/"_BK"
    # suffix) down to their best-scoring variant BEFORE the top-K cutoff, so
    # 2-3 near-identical copies of one section don't crowd out a genuinely
    # different candidate table.
    canonical_best: Dict[str, str] = {}
    for tbl, score in scores.items():
        canon = _BACKUP_SUFFIX_RE.sub("", tbl)
        if canon not in canonical_best or scores[canonical_best[canon]] < score:
            canonical_best[canon] = tbl
    deduped_table_names = set(canonical_best.values())
    scores = {tbl: s for tbl, s in scores.items() if tbl in deduped_table_names}

    ranked_tables = sorted(scores, key=scores.__getitem__, reverse=True)[:TOP_K_TABLES]

    # ── Resolve return_id live via this app's own XML — never trust whatever
    # (if anything) the embedding metadata itself carries for return_id/name.
    for tbl in ranked_tables:
        meta = all_table_meta[tbl]
        ret = return_lookup.get_return_for_table(tbl)
        if ret:
            meta.update(ret)
        else:
            meta.setdefault("return_id", None)
            meta.setdefault("return_name", None)

    # A table whose return_id never resolved (return_lookup.get_return_for_table()
    # found no loadable table-mapping file for it) can never be used downstream —
    # main.py's later _parse_returns() lookup and compute_variance() both need a
    # real return_id. Drop these unconditionally, BEFORE the auth filter, so they
    # never reach intent_resolver as a candidate — this matters especially with
    # AUTH_ENABLED=false, where the auth filter below is skipped entirely and would
    # otherwise let an unusable table through untouched.
    unresolved = [tbl for tbl in ranked_tables if all_table_meta[tbl].get("return_id") is None]
    if unresolved:
        logger.warning(
            "[nlp.retriever] query=%r | dropping %d table(s) with unresolved return_id: %s",
            query, len(unresolved), unresolved,
        )
    ranked_tables = [tbl for tbl in ranked_tables if tbl not in unresolved]

    # ── Authorization filter — reuse the existing, untouched auth function ────
    if not AUTH_ENABLED:
        logger.warning(
            "[nlp.retriever] AUTH_DISABLED — skipping return-access filtering for query=%r",
            query,
        )
        tables = [all_table_meta[tbl] for tbl in ranked_tables]
    else:
        allowed_returns = get_allowed_form_ids(login_id) or set()

        def _is_authorized(table_meta: Dict[str, Any]) -> bool:
            rid = table_meta.get("return_id")
            return rid is not None and str(rid) in allowed_returns

        tables = [all_table_meta[tbl] for tbl in ranked_tables if _is_authorized(all_table_meta[tbl])]
    authorized_table_names = {t["table"] for t in tables}

    if len(tables) < len(ranked_tables):
        logger.info(
            "[nlp.retriever] login_id=%r | dropped %d unauthorized table(s) from shortlist",
            login_id, len(ranked_tables) - len(tables),
        )

    columns = [c for _, c in col_hits if c["table"] in authorized_table_names]
    seen_cols = set()
    unique_columns = []
    for c in columns:
        key = (c["table"], c["column"])
        if key not in seen_cols:
            seen_cols.add(key)
            unique_columns.append(c)
    columns = unique_columns[: TOP_K_COLUMNS * 2]

    # ── Backfill: a table can be shortlisted purely on a table-description or
    # row-label hit, with none of its own columns making the top-k column
    # search. Without this, intent_resolver sees that table with zero
    # candidate columns and can never validly select it, silently discarding
    # an otherwise-correct match. Pull the table's full column list instead.
    tables_with_columns = {c["table"] for c in columns}
    tables_missing_columns = authorized_table_names - tables_with_columns
    if tables_missing_columns:
        grouped_columns = meta_by_table(COLUMN_INDEX_PATH, COLUMN_META_PATH)
        for tbl in tables_missing_columns:
            for c in grouped_columns.get(tbl, []):
                key = (c["table"], c["column"])
                if key not in seen_cols:
                    seen_cols.add(key)
                    columns.append(c)

    matched_labels = [lbl for _, lbl in label_hits if lbl["table"] in authorized_table_names]
    seen_labels = set()
    unique_labels = []
    for lbl in matched_labels:
        key = (lbl["table"], lbl["column"], lbl["value"])
        if key not in seen_labels:
            seen_labels.add(key)
            unique_labels.append(lbl)
    matched_labels = unique_labels[:TOP_K_LABELS]

    logger.info(
        "[nlp.retriever] query=%r | login_id=%r | %d authorized table(s), %d column(s), %d label(s)",
        query, login_id, len(tables), len(columns), len(matched_labels),
    )
    return {"tables": tables, "columns": columns, "matched_labels": matched_labels}
