# index_store.py — loads/searches the FAISS vector store at runtime.
# Building (write side) now happens outside this project entirely — an
# external tool produces table_index.faiss/column_index.faiss/
# row_label_index.faiss + their *_meta.pkl files and they get dropped into
# backend/output/ (nlp_config.INDEX_DIR). This module only ever reads them.
#
# Indices are cached in memory per (index_path, meta_path) after first load —
# search() used to call faiss.read_index()+pickle.load() from disk on EVERY
# query (x3 per request: table/column/row-label), which is pure waste since
# the files only change when the external tool rebuilds them. Cache entries
# are invalidated by file mtime, so dropping in a freshly-rebuilt output/
# folder is picked up on the next request with no restart needed.

from __future__ import annotations

import logging
import os
import pickle
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

import faiss
import numpy as np

logger = logging.getLogger(__name__)

_cache: Dict[str, Tuple[float, faiss.Index, List[Dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def load_index(index_path: str, meta_path: str) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    """Load an index fresh from disk, bypassing the cache. Prefer search()
    for normal use — this is exposed mainly for tooling/tests."""
    index = faiss.read_index(index_path)
    with open(meta_path, "rb") as fh:
        meta = pickle.load(fh)
    return index, meta


def _load_cached(index_path: str, meta_path: str) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    mtime = max(os.path.getmtime(index_path), os.path.getmtime(meta_path))
    key = index_path

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]

    # A present-but-unreadable index (truncated .faiss, a .pkl pickled
    # against a package that isn't installed here, a dimension mismatch
    # from a rebuild) raises from deep inside faiss/pickle with no mention
    # of which file was at fault — and every caller above only ever logged
    # the ABSENT case. Name the file, then re-raise unchanged.
    try:
        index, meta = load_index(index_path, meta_path)
    except Exception as exc:
        logger.error(
            "[nlp.index_store] FAILED to load index=%s meta=%s | %s: %s",
            index_path, meta_path, type(exc).__name__, exc,
        )
        raise
    with _cache_lock:
        _cache[key] = (mtime, index, meta)
    logger.info("[nlp.index_store] Loaded %d record(s) from %s (cached in memory)", len(meta), index_path)
    return index, meta


def all_meta(index_path: str, meta_path: str) -> List[Dict[str, Any]]:
    """Return the full cached metadata list for an index, unfiltered by any
    query/score — used when a table needs its complete column list rather
    than just whatever a top-k similarity search happened to surface."""
    if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
        return []
    _, meta = _load_cached(index_path, meta_path)
    return meta


_grouped_cache: Dict[str, Tuple[float, Dict[str, List[Dict[str, Any]]]]] = {}


def meta_by_table(index_path: str, meta_path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Same records as all_meta(), grouped by each record's "table" key and
    cached alongside the index (same mtime invalidation). Callers that only
    need one or a few tables' records (e.g. retriever.py's column-backfill
    path) previously did `for c in all_meta(...): if c["table"] in ...` —
    an O(total corpus size) linear scan on EVERY triggering query, regardless
    of how few tables were actually being looked up. Grouping once (still
    O(n), but cached) turns every subsequent lookup into an O(1) dict get,
    so this stops scaling with total corpus size.

    KEYS ARE UPPERCASED — always look up with `table_name.upper()`. The
    externally-built index stores table names lowercased
    (e.g. "cims_raq_q_sec1_part_a_dom") while this app's own table-mapping
    XML uses uppercase (TableName="CIMS_RAQ_Q_SEC1_PART_A_DOM"), so a
    caller holding an XML-derived name silently got zero columns back from
    a case-sensitive lookup — which downstream surfaced as
    "Could not resolve this query to a known table/column". Normalizing the
    key here makes the two naming conventions interoperable for every
    caller instead of each having to remember to fold case.

    NOTE the records themselves still carry their ORIGINAL (index-cased)
    "table" value. A caller that compares `record["table"]` against its own
    canonical name must rewrite it — see main.py's _shortlist_for_return."""
    if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
        return {}

    mtime = max(os.path.getmtime(index_path), os.path.getmtime(meta_path))
    key = index_path
    with _cache_lock:
        cached = _grouped_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    _, meta = _load_cached(index_path, meta_path)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in meta:
        grouped.setdefault(record["table"].upper(), []).append(record)

    with _cache_lock:
        _grouped_cache[key] = (mtime, grouped)
    return grouped


class _NotReconstructable(RuntimeError):
    """The index isn't a flat one, so its raw vectors can't be read back."""


# Reconstructed vector matrices, keyed and invalidated exactly like _cache /
# _grouped_cache above.  {index_path: (mtime, matrix, {TABLE_UPPER: [rows]})}
_vector_cache: Dict[str, Tuple[float, "np.ndarray", Dict[str, List[int]]]] = {}


def _load_vectors_cached(index_path: str, meta_path: str):
    """The index's raw vectors as one (ntotal, d) float32 matrix, plus a
    table -> row-indices map.

    Only works for flat indices (IndexFlat*), which is what this project's
    external build tool currently produces — all three indices are IndexFlatIP
    holding normalized vectors, so an inner product IS the cosine similarity.
    Raises _NotReconstructable for anything else so the caller can fall back.
    """
    mtime = max(os.path.getmtime(index_path), os.path.getmtime(meta_path))
    with _cache_lock:
        cached = _vector_cache.get(index_path)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]

    index, meta = _load_cached(index_path, meta_path)
    if not isinstance(index, faiss.IndexFlat):
        raise _NotReconstructable(f"{type(index).__name__} is not a flat index")

    matrix = index.reconstruct_n(0, index.ntotal).astype("float32", copy=False)
    rows_by_table: Dict[str, List[int]] = {}
    for i, record in enumerate(meta):
        rows_by_table.setdefault(record["table"].upper(), []).append(i)

    with _cache_lock:
        _vector_cache[index_path] = (mtime, matrix, rows_by_table)
    logger.info(
        "[nlp.index_store] Reconstructed %d vector(s) from %s for subset search",
        len(matrix), index_path,
    )
    return matrix, rows_by_table


def subset_search(
    index_path: str,
    meta_path: str,
    query_vector: np.ndarray,
    table_names: Iterable[str],
    k: Optional[int] = None,
    min_score: float = 0.0,
) -> List[Tuple[float, Dict[str, Any]]]:
    """Exact similarity search RESTRICTED to records belonging to `table_names`.

    search() cannot express this. FAISS has no per-query id filter on a flat
    index, so the only way through search() is k=ntotal followed by a
    post-filter — which pays for the full scan anyway, AND applies min_score
    *inside*, where the caller can no longer tell "not in my scope" apart from
    "below the threshold". This matters because the scoped caller
    (scoped_retriever.py) needs the COMPLETE column list of its tables: that
    list is what intent_resolver validates against, so a silently dropped
    column becomes unselectable rather than merely low-ranked.

    Since the indices are flat and their vectors normalized, the restricted
    ranking is exactly `subset_matrix @ q` — a ~2MB matvec for the largest
    (527x1024) index. Cost scales with the SCOPE, not the corpus, which is what
    keeps this viable as the corpus grows past today's 51 tables.

    Table names are matched case-insensitively: the externally-built index
    stores them lowercase while this app's XML uses uppercase (see
    meta_by_table). `k=None` means "return every matching record".
    """
    if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
        logger.warning("[nlp.index_store] Index not found: %s", index_path)
        return []

    wanted = {str(t).upper() for t in table_names}
    if not wanted:
        return []

    _, meta = _load_cached(index_path, meta_path)
    if not meta:
        return []

    try:
        matrix, rows_by_table = _load_vectors_cached(index_path, meta_path)
    except _NotReconstructable as exc:
        # A future rebuild as IVF/PQ/HNSW lands here: degrade to the slow path
        # rather than breaking retrieval outright.
        logger.warning(
            "[nlp.index_store] %s — falling back to full search + post-filter for %s",
            exc, index_path,
        )
        hits = search(index_path, meta_path, query_vector, len(meta), min_score=min_score)
        filtered = [(sc, rec) for sc, rec in hits if rec["table"].upper() in wanted]
        return filtered[:k] if k else filtered

    rows: List[int] = []
    for name in wanted:
        rows.extend(rows_by_table.get(name, []))
    if not rows:
        return []

    rows.sort()
    q = np.asarray(query_vector, dtype="float32").reshape(-1)
    sims = matrix[rows] @ q

    scored = [
        (float(score), meta[row])
        for score, row in zip(sims, rows)
        if score >= min_score
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:k] if k else scored


def search(
    index_path: str,
    meta_path: str,
    query_vector: np.ndarray,
    k: int,
    min_score: float = 0.0,
) -> List[Tuple[float, Dict[str, Any]]]:
    """Search a FAISS index (cached in memory after first load), returning
    only hits above min_score."""
    if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
        logger.warning("[nlp.index_store] Index not found: %s", index_path)
        return []

    index, meta = _load_cached(index_path, meta_path)
    if not meta:
        return []

    effective_k = min(k, len(meta))
    q_vec = np.array([query_vector], dtype="float32")
    distances, indices = index.search(q_vec, effective_k)

    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx != -1 and dist >= min_score:
            results.append((float(dist), meta[idx]))
    return results
