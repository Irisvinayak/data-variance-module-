# return_lookup.py — resolves table_name -> return metadata (return_id,
# return_name, filter_col, ...) via this module's OWN Returns.xml + table-
# mapping XML, at retrieval time.
#
# Why this exists: embeddings can now be built by ANY external tool and just
# dropped into backend/output/ (see nlp_config.INDEX_DIR) — e.g. the pasted
# table_meta.pkl/column_meta.pkl records only carry {"text", "table"}, no
# return_id at all. retriever.py used to expect return_id to already be
# baked into the embedding metadata; that only worked when this project's own
# (now-removed) build pipeline produced it. This module decouples the two:
# the embedding index only needs to know table/column names, and THIS module
# independently maps a table name back to a return_id via the same XML this
# app already trusts for everything else (auth, compute_variance).

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .. import query_xml_lookup
from ..report_lookup import _parse_returns
from ..service import _load_table_mapping

logger = logging.getLogger(__name__)

_TTL = float(os.getenv("DV_NLP_RETURN_LOOKUP_TTL_SEC", "3600"))
_cache: Dict[str, Any] | None = None
_cache_ts: float = 0.0
_lock = threading.Lock()
_rebuild_in_progress = False


def _strip_dp_suffix(table_name: str) -> str:
    return table_name[:-3] if table_name.upper().endswith("_DP") else table_name


def _build_lookup() -> Dict[str, Dict[str, Any]]:
    """Walk every <Return> in Returns.xml, load its table-mapping XML, and
    build {UPPERCASE base table name (no _DP suffix): return metadata}."""
    lookup: Dict[str, Dict[str, Any]] = {}

    for ret in _parse_returns():
        return_id = ret.get("Id")
        return_name = ret.get("Name", "")
        report_freq = ret.get("RepFreq", "")
        tbl_path = (ret.get("TblPath") or "").strip()
        if not return_id:
            continue

        try:
            root, resolved_path = _load_table_mapping(return_id, tbl_path)
        except Exception as exc:
            logger.warning(
                "[nlp.return_lookup] Skipping return_id=%s (%s) — _load_table_mapping raised: %s",
                return_id, return_name, exc,
            )
            root, resolved_path = None, None

        rows_registered = 0
        if root is not None:
            for el in root.findall("Row"):
                table_name = (el.attrib.get("TableName") or "").strip()
                if not table_name:
                    continue
                key = _strip_dp_suffix(table_name).upper()
                comp_raw = el.attrib.get("CompFilterColName", "")
                lookup[key] = {
                    "return_id": return_id,
                    "return_name": return_name,
                    "report_freq": report_freq,
                    "filter_col": el.attrib.get("FilterColumn", ""),
                    "comp_filter_col_names": [c.strip() for c in comp_raw.split("|") if c.strip()],
                }
                rows_registered += 1

        # ── Fallback: XML_Query.xml ───────────────────────────────────────────
        # Registering ZERO tables means this return contributes nothing, and
        # retriever.py unconditionally DROPS any table whose return_id won't
        # resolve — so those tables become permanently unreachable by the NLP
        # layer (empty shortlist -> the "which return?" prompt degrades to
        # listing every return, plus a "Could not resolve this query to a
        # known table/column" 404). Two distinct causes both land here: the
        # mapping file is missing (root is None), or it parsed but every
        # TableName attribute is empty (the CIMS_ALE returns). Either way the
        # return's SELECT statements in XML_Query.xml still name its tables.
        if rows_registered == 0:
            for key, meta in query_xml_lookup.tables_for_return(return_id).items():
                base_key = _strip_dp_suffix(key).upper()
                # Never override a table another return already mapped
                # properly — the mapping file stays authoritative.
                if base_key in lookup:
                    continue
                lookup[base_key] = {
                    "return_id": return_id,
                    "return_name": return_name,
                    "report_freq": report_freq,
                    "filter_col": meta["filter_col"],
                    "comp_filter_col_names": [],
                }
                rows_registered += 1
            if rows_registered:
                logger.info(
                    "[nlp.return_lookup] return_id=%s (%s) — no usable table mapping "
                    "(tbl_path=%r, tried %r); recovered %d table(s) from %s",
                    return_id, return_name, tbl_path, resolved_path,
                    rows_registered, query_xml_lookup.XML_QUERY_FILENAME,
                )
            else:
                logger.warning(
                    "[nlp.return_lookup] Skipping return_id=%s (%s) — no table mapping "
                    "(tbl_path=%r, tried %r) and no %s fallback. Every table under this "
                    "return is unresolvable via get_return_for_table().",
                    return_id, return_name, tbl_path, resolved_path,
                    query_xml_lookup.XML_QUERY_FILENAME,
                )

    logger.info("[nlp.return_lookup] Built lookup for %d table(s) across returns", len(lookup))
    return lookup


def _rebuild_in_background() -> None:
    """Runs _build_lookup() off the request thread and swaps the cache in
    when done. Any exception just leaves the stale cache in place — a table
    lookup failing entirely is worse than serving slightly-stale metadata."""
    global _cache, _cache_ts, _rebuild_in_progress
    try:
        new_lookup = _build_lookup()
        with _lock:
            _cache = new_lookup
            _cache_ts = time.monotonic()
    except Exception:
        logger.exception("[nlp.return_lookup] Background cache rebuild failed — keeping stale cache")
    finally:
        with _lock:
            _rebuild_in_progress = False


def _get_lookup() -> Dict[str, Dict[str, Any]]:
    """As the number of returns grows, _build_lookup()'s O(returns) XML
    read+parse cost grows with it. Rebuilding synchronously on whichever
    user's request happens to land right after the TTL expires — the
    original behavior — turns that growth into a periodic, user-facing
    latency spike, and with no locking, concurrent requests at that moment
    would each kick off their own redundant rebuild (thundering herd).

    Fix: once a cache exists, an expired cache is still served immediately
    (return metadata a few minutes stale is harmless) while exactly one
    background thread refreshes it — every other caller during that window
    gets the same stale-but-fast answer instead of paying for or piling up
    rebuilds. Only the very first call (no cache yet) blocks, since there's
    nothing valid to serve in the meantime."""
    global _cache, _cache_ts, _rebuild_in_progress

    if _cache is None:
        with _lock:
            if _cache is None:  # re-check: another thread may have built it while we waited for the lock
                _cache = _build_lookup()
                _cache_ts = time.monotonic()
        return _cache

    if (time.monotonic() - _cache_ts) >= _TTL:
        with _lock:
            if not _rebuild_in_progress:
                _rebuild_in_progress = True
                threading.Thread(target=_rebuild_in_background, daemon=True).start()

    return _cache


def get_return_for_table(table_name: str) -> Optional[Dict[str, Any]]:
    """Return {"return_id", "return_name", "report_freq", "filter_col",
    "comp_filter_col_names"} for `table_name`, or None if it isn't tied to
    any known return."""
    key = _strip_dp_suffix(table_name).upper()
    return _get_lookup().get(key)


def invalidate() -> None:
    """Force the next get_return_for_table() call to re-read the XML
    synchronously (bypasses stale-while-revalidate — the caller explicitly
    wants a guaranteed-fresh read, e.g. tests or an admin action)."""
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0
