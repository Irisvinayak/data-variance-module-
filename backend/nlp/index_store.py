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
from typing import Any, Dict, List, Tuple

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

    index, meta = load_index(index_path, meta_path)
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
    so this stops scaling with total corpus size."""
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
        grouped.setdefault(record["table"], []).append(record)

    with _cache_lock:
        _grouped_cache[key] = (mtime, grouped)
    return grouped


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
