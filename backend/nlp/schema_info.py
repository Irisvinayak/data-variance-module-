# schema_info.py — column TYPE and human DESCRIPTION, read from schema.json.
#
# schema.json ships in INDEX_DIR alongside the FAISS files (same external build
# tool) and carries, per column: name, type, nullable, and a human description.
# Verified against schema2.json — a 1911-table dump of the real database — all
# 51 embedded tables are present with IDENTICAL column sets, so this file is a
# trustworthy mirror of the Oracle schema, not a stale side-copy.
#
# Until now only sql_generator.py (the separate raw-SQL /variance/nlquery
# route) read it; the intent-resolution path ignored it entirely. That was
# costly, because the file answers a question retrieval cannot: *is this column
# a measure, or a label?* Of 527 columns, 389 are number, 87 varchar2 and 51
# date. Embedding similarity alone happily ranks a label above a measure —
# measured examples: "provisions on npa" ranks movement_provision_npa
# (varchar2) first, and "outstanding amount standard assets" ranks
# assets (varchar2) in positions 1, 2 AND 3. Those are row-label columns;
# picking one as the answer column yields a variance table with no numbers in
# it, because compute only ever computes metrics over numeric columns.

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, Optional, Tuple

from ..calculate_variance import _is_excluded_value_col
from .nlp_config import SCHEMA_JSON_PATH

logger = logging.getLogger(__name__)

# {(TABLE_UPPER, COLUMN_UPPER): {"type", "description"}} — mtime-invalidated,
# matching index_store's caching convention so a freshly-dropped output/ folder
# is picked up with no restart.
_cache: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None
_cache_mtime: float = -1.0
_lock = threading.Lock()


def _current_mtime() -> float:
    try:
        return os.path.getmtime(SCHEMA_JSON_PATH)
    except OSError:
        return -1.0


def _build() -> Dict[Tuple[str, str], Dict[str, Any]]:
    try:
        with open(SCHEMA_JSON_PATH, "r", encoding="utf-8") as fh:
            tables = json.load(fh)
    except (OSError, ValueError) as exc:
        # Absent or malformed schema.json must degrade to "no type information"
        # — every caller treats None as unknown and falls back to behaving
        # exactly as it did before this module existed.
        logger.warning(
            "[nlp.schema_info] Could not read %s (%s) — column type/description "
            "hints are unavailable this run", SCHEMA_JSON_PATH, exc,
        )
        return {}

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for table in tables:
        tname = (table.get("table") or "").upper()
        if not tname:
            continue
        for col in table.get("columns") or []:
            cname = (col.get("name") or "").upper()
            if not cname:
                continue
            # "number(20,2)" -> "number"; the precision never matters here.
            raw_type = (col.get("type") or "").split("(")[0].strip().lower()
            out[(tname, cname)] = {
                "type": raw_type or None,
                "description": col.get("description") or None,
            }
    logger.info("[nlp.schema_info] Loaded type/description for %d column(s)", len(out))
    return out


def _get() -> Dict[Tuple[str, str], Dict[str, Any]]:
    global _cache, _cache_mtime
    mtime = _current_mtime()
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        _cache = _build()
        _cache_mtime = mtime
        return _cache


def _entry(table: str, column: str) -> Optional[Dict[str, Any]]:
    return _get().get(((table or "").upper(), (column or "").upper()))


def column_type(table: str, column: str) -> Optional[str]:
    """'number' | 'varchar2' | 'date' | None when unknown."""
    entry = _entry(table, column)
    return entry["type"] if entry else None


def column_description(table: str, column: str) -> Optional[str]:
    """The human label for a column, e.g. 'A. Accounts with banks in India'.

    This is what the UI should show the user instead of a column identifier —
    end users have no knowledge of the schema behind a return."""
    entry = _entry(table, column)
    return entry["description"] if entry else None


# Words that appear in the DATA's own vocabulary — column descriptions and row
# labels — and therefore cannot identify a RETURN on their own.
_vocab_cache: Optional[frozenset] = None
_vocab_mtime: float = -1.0
_WORD_RE = re.compile(r"[a-z0-9]+")


def data_vocabulary() -> frozenset:
    """Every word used in this corpus's column descriptions and row labels.

    Purpose: tell a return IDENTIFIER ("RAQ", "CRILC", "DNBS03") apart from
    ordinary business vocabulary ("advances", "credit", "exposure"). Matching a
    query against 281 return names by distinctive-token overlap cannot do this
    on its own, because over that many names an ordinary financial word IS a
    rare token of some return's name — measured false positives: "gross
    advances" matched CIMS_CB_Advances_Investments, "debit entries" matched
    CIMS_DPSS07_Usage_Credit_Debit_Cards, "non-funded exposure" matched
    Crilc_Non_CIMS_NBFC. A word the data itself uses to describe its own
    measures is a poor identifier by definition.

    Harvested from COLUMN descriptions and ROW labels only — deliberately NOT
    table descriptions, which embed the return name they belong to ("... CIMS
    banking supervisory regulatory return raq quarterly ...") and would
    therefore classify "raq" and "ale" as business vocabulary, blocking the
    very mentions this is meant to allow.

    Verified against the current corpus: of 10 measured false-positive tokens
    all 10 are in here, and of 12 real return identifiers none are.
    """
    global _vocab_cache, _vocab_mtime
    mtime = _current_mtime()
    if _vocab_cache is not None and _vocab_mtime == mtime:
        return _vocab_cache

    words: set = set()
    for entry in _get().values():
        words.update(_WORD_RE.findall((entry.get("description") or "").lower()))

    # Row labels live in the FAISS metadata, not schema.json.
    try:
        from .index_store import all_meta
        from .nlp_config import ROW_LABEL_INDEX_PATH, ROW_LABEL_META_PATH
        for record in all_meta(ROW_LABEL_INDEX_PATH, ROW_LABEL_META_PATH):
            words.update(_WORD_RE.findall(str(record.get("value") or "").lower()))
    except Exception as exc:
        logger.warning("[nlp.schema_info] Could not read row labels for vocabulary: %s", exc)

    _vocab_cache = frozenset(words)
    _vocab_mtime = mtime
    logger.info("[nlp.schema_info] Data vocabulary: %d word(s)", len(_vocab_cache))
    return _vocab_cache


def looks_like_return_identifier(token: str) -> bool:
    """Could `token` plausibly be part of a RETURN's name rather than a
    description of the data? False for anything the corpus uses to describe its
    own measures. Empty vocabulary (no schema.json) returns True for
    everything, i.e. degrades to the un-filtered behaviour."""
    vocab = data_vocabulary()
    if not vocab:
        return True
    return token.lower() not in vocab


def is_metric_column(table: str, column: str) -> bool:
    """Could this column be the ANSWER to "show me <measure>"?

    True only for numeric columns that aren't serial numbers. Reuses
    calculate_variance._is_excluded_value_col so "is this a value column" has
    ONE definition shared with the code that actually computes the variance —
    a second, drifting copy here would let the NLP layer promise a column that
    compute then refuses to measure.

    Unknown columns return True: when schema.json is missing or a column isn't
    in it, this must not suppress a candidate that retrieval found. It is a
    ranking hint, never a gate.
    """
    kind = column_type(table, column)
    if kind is None:
        return True
    return kind.startswith("number") and not _is_excluded_value_col(column)
