# lexical_search.py — BM25 + QA-pairs signals, additive to retriever.py's
# dense FAISS search. Built by the SAME external tool that drops FAISS
# indices into backend/output/ (nlp_config.INDEX_DIR) — this module only
# ever reads bm25_table_index.pkl / qa_pairs.json, never builds them.
#
# Why these exist: dense cosine similarity smooths over exact structural
# markers — two near-identical tables (e.g. a "Part A"/"Part B" pair) can
# score within noise of each other. BM25 is exact-term-frequency scoring,
# a different and complementary kind of match, not a replacement for the
# dense signal. QA-pairs strong-match reuses known-good verified questions
# to pin a table when this question is essentially a duplicate of one of
# them. Both signals must degrade to a silent no-op if their data files
# are missing — same missing-file contract index_store.search() already
# uses — so this module never assumes the files exist.

from __future__ import annotations

import difflib
import json
import logging
import os
import pickle
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_bm25_cache: Dict[str, Tuple[float, Any, List[Dict[str, Any]]]] = {}
_qa_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
# index_paths whose load already failed — keeps the warning above to one
# line per path instead of one per NL query.
_bm25_failed: set[str] = set()
_cache_lock = threading.Lock()


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _load_bm25_cached(index_path: str) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
    if not os.path.isfile(index_path):
        return None, []

    mtime = os.path.getmtime(index_path)
    with _cache_lock:
        cached = _bm25_cache.get(index_path)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]

    # The pickle holds a rank_bm25.BM25Okapi instance, so unpickling it
    # imports rank_bm25 — a missing package raises ModuleNotFoundError right
    # here, from a file-exists path the isfile() guard above has already
    # waved through. BM25 is an ADDITIVE signal (see this module's header and
    # nlp_config's BM25_INDEX_PATH note): its absence is supposed to cost
    # ranking quality, never the whole query. So any load failure degrades to
    # the same silent no-op as an absent file, logged once per index_path so
    # a genuinely broken deployment is still visible in the log.
    try:
        with open(index_path, "rb") as fh:
            data = pickle.load(fh)
        bm25, records = data["bm25"], data["records"]
    except Exception as exc:
        with _cache_lock:
            already_warned = index_path in _bm25_failed
            _bm25_failed.add(index_path)
        if not already_warned:
            logger.warning(
                "[nlp.lexical_search] BM25 index %s could not be loaded "
                "(%s: %s) — continuing without the BM25 signal. If this is a "
                "missing package, install it: pip install rank-bm25",
                index_path, type(exc).__name__, exc,
            )
        return None, []

    with _cache_lock:
        _bm25_cache[index_path] = (mtime, bm25, records)
        _bm25_failed.discard(index_path)
    logger.info("[nlp.lexical_search] Loaded %d BM25 record(s) from %s", len(records), index_path)
    return bm25, records


def search_bm25(
    index_path: str,
    query_text: str,
    top_k: int,
) -> List[Tuple[float, Dict[str, Any]]]:
    """BM25 lexical search over the table-document corpus. Returns
    (raw_bm25_score, record) tuples sorted descending, capped at top_k.
    Raw score is returned AS-IS — it is unbounded (roughly 0-20+) and not
    on cosine's 0-1 scale, so the caller must fuse it via rank position
    (RRF), not by comparing this raw score directly against a cosine
    score. Returns [] if the index file is absent — a true no-op."""
    bm25, records = _load_bm25_cached(index_path)
    if bm25 is None or not records:
        return []

    tokens = _tokenize(query_text)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)
    ranked_idx = sorted(range(len(records)), key=lambda i: scores[i], reverse=True)

    results = []
    for i in ranked_idx[:top_k]:
        if scores[i] <= 0.0:
            continue
        results.append((float(scores[i]), records[i]))
    return results


def _load_qa_cached(qa_path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(qa_path):
        return []

    mtime = os.path.getmtime(qa_path)
    with _cache_lock:
        cached = _qa_cache.get(qa_path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    with open(qa_path, "r", encoding="utf-8") as fh:
        records = json.load(fh)

    with _cache_lock:
        _qa_cache[qa_path] = (mtime, records)
    logger.info("[nlp.lexical_search] Loaded %d QA pair(s) from %s", len(records), qa_path)
    return records


def search_qa_strong_match(
    qa_path: str,
    query: str,
    threshold: float,
    *,
    qa_index_path: Optional[str] = None,
    qa_meta_path: Optional[str] = None,
    query_vector: Any = None,
    prefilter_top_n: int = 20,
) -> Optional[str]:
    """Returns the table name of a stored QA pair whose question is a
    near-duplicate (difflib ratio >= threshold) of `query`, or None if
    no pair meets the threshold or the file is absent. Deliberately never
    returns/uses the stored "sql" field — reusing verified SQL directly is
    sql_generator.py's territory, out of scope here. Compares against the
    RAW query (not normalize_query()'d) since stored questions are natural,
    non-normalized text, same as what a user actually typed.

    difflib's per-comparison cost (string-similarity, not a cheap vector/term
    lookup) makes a full scan the first thing to slow down as QA pairs scale
    up. If qa_index_path/qa_meta_path/query_vector are all supplied and that
    FAISS index exists, narrow to the `prefilter_top_n` nearest by cosine
    similarity first and only difflib-score those — an approximation (a
    literal-text match could in principle score outside the embedding
    pre-filter's top-N), but at threshold>=0.95 a near-duplicate question is
    virtually always also embedding-similar, so the practical risk is low.
    Any of the three being omitted, or the index file being absent, falls
    back to the original full-corpus scan — same missing-file degrade
    contract as every other signal here."""
    records = _load_qa_cached(qa_path)
    if not records:
        return None

    candidates = records
    if qa_index_path and qa_meta_path and query_vector is not None:
        from .index_store import search as _faiss_search  # local import: keeps this module's non-prefilter path free of a hard FAISS dependency

        hits = _faiss_search(qa_index_path, qa_meta_path, query_vector, prefilter_top_n)
        if hits:
            candidates = [meta for _, meta in hits]

    query_lower = query.strip().lower()
    best_ratio = 0.0
    best_table = None
    for rec in candidates:
        question = rec.get("question", "")
        if not question:
            continue
        ratio = difflib.SequenceMatcher(None, query_lower, question.strip().lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_table = rec.get("table")

    if best_table and best_ratio >= threshold:
        return best_table
    return None
