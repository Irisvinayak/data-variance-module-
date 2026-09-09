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

from ..auth.service import get_allowed_form_ids
from ..config import AUTH_ENABLED, RequestContext
from . import return_lookup
from .embedder import embed_query
from .index_store import meta_by_table, search
from .lexical_search import search_bm25, search_qa_strong_match
from .query_normalizer import normalize_query
from .query_analyzer import QueryAnalysis, analyze_query
from . import confidence as confidence_mod
from . import ranking
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
    RRF_K,
    ROW_LABEL_INDEX_PATH,
    ROW_LABEL_META_PATH,
    TABLE_INDEX_PATH,
    TABLE_META_PATH,
    TIE_EPSILON,
    TOP_K_COLUMNS,
    TOP_K_LABELS,
    TOP_K_TABLES,
)

logger = logging.getLogger(__name__)

# Backup/duplicate table copies (e.g. CIMS_RAQ_M_SEC9_SENSEC_PARTB_bckup,
# ..._BK, ..._bkup) are near-identical to their primary table and otherwise
# eat shortlist slots that should go to genuinely different candidates.
_BACKUP_SUFFIX_RE = re.compile(r"_(bckup|bkup|bk)$", re.IGNORECASE)

# Moved to backend/nlp/ranking.py so scoped_retriever.py can reuse the exact
# same fusion arithmetic rather than growing a second copy that drifts. Aliased
# here (rather than call sites rewritten wholesale) so every existing reference
# in this file — and anything importing them — keeps working unchanged.
_rrf = ranking.rrf
_tokens = ranking.tokens
_lexical_overlap = ranking.lexical_overlap


def get_relevant_schema(
    query: str, ctx: RequestContext, analysis: "QueryAnalysis | None" = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Embed `query`, search the table/column FAISS indices, RRF-fuse them into
    a ranked table shortlist, then filter that shortlist down to only tables
    belonging to return_ids the caller's department is allowed to access
    (reusing auth.service.get_allowed_form_ids — no auth logic duplicated here).

    When AUTH_ENABLED=false (dev bypass, same flag auth/deps.py already
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
    # `analysis` is the end-to-end query read (backend/nlp/query_analyzer.py):
    # it has already pulled the return name, the date/period phrase and the
    # request boilerplate OUT of the query, leaving metric_text — just the
    # words that describe the DATA. That is what gets embedded and BM25'd,
    # because the removed parts actively hurt retrieval: a date matches table
    # descriptions that happen to cite a year, and the return name matches
    # every table under that return equally (they all embed it), so both add
    # score without discriminating. Callers that don't supply one get the old
    # behavior — normalize the whole query and search that.
    if analysis is None:
        analysis = analyze_query(query)

    normalized_query = analysis.metric_text
    if normalized_query != query:
        logger.info(
            "[nlp.retriever] search text: %r -> %r (return=%s, date=%r stripped)",
            query, normalized_query, analysis.return_names or None, analysis.date_text,
        )
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

    # The table holding the #1 hit in each individual signal. This turned out
    # to be the single strongest predictor of a correct answer, and it is NOT
    # what the fused sum measures:
    #
    #   measured over the 60-query benchmark (see nlp_config.RRF_K) --
    #     table leads >= 1 signal   -> 81% correct  (n=57)
    #     table leads no signal     ->  0% correct  (n=3)
    #     table present in 3 signals -> 90% correct (n=31)
    #     table present in 4 signals -> 62% correct (n=29)
    #
    # i.e. BREADTH across signals is a NEGATIVE indicator — a generic table
    # (dates, codes, "total" columns) surfaces in every signal at mediocre rank
    # and accumulates score without ever being the best answer to anything,
    # while the genuinely right table often owns exactly one signal decisively.
    # Summed RRF rewards the former, so confidence.py is given the leader set
    # explicitly rather than trying to infer evidence quality from the sum.
    signal_leaders: set = set()

    # Primary signal #1: matching columns. Score by each table's BEST-ranked
    # column hit only (first occurrence in the already-sorted hit list) — NOT
    # summed across every column that table happens to have in the results.
    # Summing let a table with many loosely-relevant columns (e.g. 8 generic
    # risk/finance column names all embedding "close enough") out-accumulate
    # a table with a single, precisely-matching column, which is backwards:
    # depth of one exact match should beat breadth of several mediocre ones.
    ranking.fuse_best_hit_per_table(
        col_hits, 2.0, scores,
        all_table_meta=all_table_meta, signal_leaders=signal_leaders,
    )

    # Primary signal #2: matching row-label values (e.g. RISK_CATEGORY="Standard") —
    # weighted equally with columns since a value match is just as strong evidence
    # of the right table as a column-name match. Same best-hit-only rule applies.
    ranking.fuse_best_hit_per_table(
        label_hits, 2.0, scores,
        all_table_meta=all_table_meta, signal_leaders=signal_leaders,
    )

    # Secondary/supporting signal: table description similarity — breaks ties
    # and surfaces tables whose only good match is the description, but no
    # longer dominates over an actual column/value hit.
    texts_by_table: Dict[str, List[str]] = {}
    for rank, (_, t) in enumerate(table_hits):
        if rank == 0:
            signal_leaders.add(t["table"])   # best table-description hit
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
    ranking.fuse_best_hit_per_table(
        bm25_hits, BM25_SIGNAL_WEIGHT, scores,
        all_table_meta=all_table_meta, signal_leaders=signal_leaders,
        texts_by_table=texts_by_table,
    )

    # Signal: QA strong-match — if this question is a near-duplicate of a
    # known verified example, pin its table. The bonus (10.0) dwarfs any
    # realistic RRF sum (~0.1 max), so it wins the sort at the cutoff below
    # without a separate "force to front" code path. If it turns out
    # unauthorized/return-id-unresolved, the existing drop/auth logic further
    # down removes it exactly like any other candidate — no special-casing.
    # NOTE the vector passed here is of the FULL normalized question, not
    # metric_text. QA pairs are whole questions ("what is the total loan
    # exposure as of March 2025?"), so the strong-match comparison — and the
    # embedding prefilter that narrows which pairs get compared — must see the
    # whole question too. Prefiltering on the stripped metric text could push
    # the one genuinely-matching pair out of the top-N before difflib ever
    # scored it. Only paid for when the analyzer actually stripped something.
    qa_vec = q_vec if analysis.metric_text == analysis.normalized else embed_query(analysis.normalized)
    qa_table = (
        search_qa_strong_match(
            QA_PAIRS_PATH, query, QA_STRONG_MATCH_THRESHOLD,
            qa_index_path=QA_INDEX_PATH, qa_meta_path=QA_META_PATH,
            query_vector=qa_vec, prefilter_top_n=QA_PREFILTER_TOP_N,
        )
        if QA_STRONG_MATCH_THRESHOLD
        else None
    )
    canonical_qa_table: str | None = None
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
    # Also kept per-table (not just folded into `scores`) since confidence.py
    # reuses it as a small standalone signal on the winning candidate.
    lexical_overlap_by_table: Dict[str, float] = {}
    for tbl in scores:
        overlap = _lexical_overlap(query_tokens, " ".join(texts_by_table.get(tbl, [])))
        lexical_overlap_by_table[tbl] = overlap
        scores[tbl] += overlap * 0.03

    # Snapshot the fused score with the QA strong-match bonus (+10.0) removed,
    # for confidence scoring only — that bonus deliberately dwarfs every other
    # signal so it can win the ranking below, but it would otherwise saturate
    # confidence.table_confidence()'s normalization to a meaningless 0/1 split.
    scores_for_confidence: Dict[str, float] = dict(scores)
    if canonical_qa_table and canonical_qa_table in scores_for_confidence:
        scores_for_confidence[canonical_qa_table] -= QA_STRONG_MATCH_BONUS

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
    scores_for_confidence = {tbl: s for tbl, s in scores_for_confidence.items() if tbl in deduped_table_names}

    # NOTE: deliberately NOT truncated to TOP_K_TABLES yet. return_id
    # resolution and the authorization filter both run over the FULL scored
    # candidate pool first, and only what survives is cut to top-K below.
    #
    # Truncating first (the previous behaviour) is the classic post-filtering
    # anti-pattern: with many returns indexed, a user entitled to a handful of
    # them would get a top-5 composed entirely of tables they cannot see, the
    # filter would empty it, and the request surfaced as "I couldn't tell which
    # return your query is about" — indistinguishable from a genuine no-match
    # even though the right content had been retrieved. The pool here is
    # bounded by the per-signal hit budgets (~108 tables) regardless of corpus
    # size, and get_return_for_table is an O(1) lookup into a cached dict, so
    # resolving the whole pool is cheap.
    ranked_candidates = sorted(scores, key=scores.__getitem__, reverse=True)

    # ── Resolve return_id live via this app's own XML — never trust whatever
    # (if anything) the embedding metadata itself carries for return_id/name.
    # The metadata TEXT is still passed as a disambiguation hint: table names
    # are not unique across returns, and the index text names the return it
    # was built for, which is what stops e.g. a CIMS_RAQ(Quarterly) table
    # resolving to CIMS_RAQ(Annually). See return_lookup._select_candidate.
    for tbl in ranked_candidates:
        meta = all_table_meta[tbl]
        hint = " ".join(texts_by_table.get(tbl, []))
        ret = return_lookup.get_return_for_table(tbl, hint_text=hint or None)
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
    unresolved = [tbl for tbl in ranked_candidates if all_table_meta[tbl].get("return_id") is None]
    if unresolved:
        logger.warning(
            "[nlp.retriever] query=%r | dropping %d table(s) with unresolved return_id: %s",
            query, len(unresolved), unresolved[:10],
        )
    ranked_candidates = [tbl for tbl in ranked_candidates if tbl not in unresolved]

    # ── Named-return scope ────────────────────────────────────────────────────
    # The query named a return outright (see query_analyzer._match_named_returns).
    # That is an EXACT statement of intent, strictly stronger than any
    # similarity score, so tables from other returns are dropped rather than
    # merely out-ranked — otherwise a high-scoring table from an unrelated
    # return can still win the top-K and answer a question the user explicitly
    # scoped elsewhere. Applied AFTER return_id resolution (that's what gives
    # each table a return_id to compare) and BEFORE the auth filter and top-K
    # cut, so the K slots are spent inside the named return.
    #
    # Skipped when it would empty the pool: the query naming a return does not
    # guarantee that return holds the metric asked for, and a degraded
    # cross-return answer beats no answer at all — the confidence gate in
    # main.py still decides whether that's good enough to auto-proceed.
    named_return_ids = set(analysis.return_ids)
    if named_return_ids:
        scoped = [
            tbl for tbl in ranked_candidates
            if str(all_table_meta[tbl].get("return_id")) in named_return_ids
        ]
        if scoped:
            logger.info(
                "[nlp.retriever] query=%r names return(s) %s -> scoping %d candidate(s) to %d",
                query, sorted(named_return_ids), len(ranked_candidates), len(scoped),
            )
            ranked_candidates = scoped
        else:
            logger.warning(
                "[nlp.retriever] query=%r names return(s) %s but no retrieved table belongs "
                "to them — searching across all returns instead",
                query, sorted(named_return_ids),
            )

    # ── Authorization filter — reuse the existing, untouched auth function ────
    # Runs over the whole candidate pool, BEFORE the top-K cut below.
    if not AUTH_ENABLED:
        logger.warning(
            "[nlp.retriever] AUTH_DISABLED — skipping return-access filtering for query=%r",
            query,
        )
        authorized_candidates = list(ranked_candidates)
    else:
        allowed_returns = get_allowed_form_ids(ctx) or set()

        def _is_authorized(table_meta: Dict[str, Any]) -> bool:
            rid = table_meta.get("return_id")
            return rid is not None and str(rid) in allowed_returns

        authorized_candidates = [
            tbl for tbl in ranked_candidates if _is_authorized(all_table_meta[tbl])
        ]

    if len(authorized_candidates) < len(ranked_candidates):
        logger.info(
            "[nlp.retriever] %s | dropped %d unauthorized table(s) from the "
            "candidate pool (%d remain before top-%d cut)",
            ctx, len(ranked_candidates) - len(authorized_candidates),
            len(authorized_candidates), TOP_K_TABLES,
        )

    # ── Top-K cut, applied last so it selects among tables the user can
    # actually access rather than being spent on ones they cannot.
    ranked_tables = authorized_candidates[:TOP_K_TABLES]
    tables = [all_table_meta[tbl] for tbl in ranked_tables]
    authorized_table_names = {t["table"] for t in tables}

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
            # .upper() — meta_by_table's keys are uppercased. `tbl` here is
            # already index-cased so the records' own "table" value matches
            # it as-is (no rewrite needed, unlike main.py's XML-derived
            # callers). See meta_by_table's docstring.
            for c in grouped_columns.get(tbl.upper(), []):
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

    # ── Table-resolution confidence (backend/nlp/confidence.py) ───────────────
    # Ordered, authorized-only (table, score) pairs, QA bonus excluded — see
    # scores_for_confidence above. Preserves ranked_tables' relative order.
    authorized_ranked = [
        (tbl, scores_for_confidence[tbl]) for tbl in ranked_tables if tbl in authorized_table_names
    ]
    qa_hit = bool(authorized_ranked) and canonical_qa_table == authorized_ranked[0][0]
    table_confidence, table_ambiguous = confidence_mod.table_confidence(
        authorized_ranked, qa_hit, lexical_overlap_by_table, TIE_EPSILON,
        signal_leaders=signal_leaders,
    )

    logger.info(
        "[nlp.retriever] query=%r | %s | %d authorized table(s), %d column(s), %d label(s) | "
        "table_confidence=%.3f | table_ambiguous=%s | top=%s | leads_a_signal=%s",
        query, ctx, len(tables), len(columns), len(matched_labels),
        table_confidence, table_ambiguous,
        tables[0]["table"] if tables else None,
        bool(tables) and tables[0]["table"] in signal_leaders,
    )
    return {
        "tables": tables,
        "columns": columns,
        "matched_labels": matched_labels,
        "table_confidence": table_confidence,
        "table_ambiguous": table_ambiguous,
    }
