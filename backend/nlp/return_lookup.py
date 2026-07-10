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
import time
from typing import Any, Dict, Optional

from ..report_lookup import _parse_returns
from ..service import _load_table_mapping

logger = logging.getLogger(__name__)

_TTL = float(os.getenv("DV_NLP_RETURN_LOOKUP_TTL_SEC", "3600"))
_cache: Dict[str, Any] | None = None
_cache_ts: float = 0.0


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
            root, _path = _load_table_mapping(return_id, tbl_path)
        except Exception as exc:
            logger.debug(
                "[nlp.return_lookup] Skipping return_id=%s (%s): %s", return_id, return_name, exc,
            )
            continue
        if root is None:
            continue

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

    logger.info("[nlp.return_lookup] Built lookup for %d table(s) across returns", len(lookup))
    return lookup


def _get_lookup() -> Dict[str, Dict[str, Any]]:
    global _cache, _cache_ts
    if _cache is None or (time.monotonic() - _cache_ts) >= _TTL:
        _cache = _build_lookup()
        _cache_ts = time.monotonic()
    return _cache


def get_return_for_table(table_name: str) -> Optional[Dict[str, Any]]:
    """Return {"return_id", "return_name", "report_freq", "filter_col",
    "comp_filter_col_names"} for `table_name`, or None if it isn't tied to
    any known return."""
    key = _strip_dp_suffix(table_name).upper()
    return _get_lookup().get(key)


def invalidate() -> None:
    """Force the next get_return_for_table() call to re-read the XML."""
    global _cache
    _cache = None
