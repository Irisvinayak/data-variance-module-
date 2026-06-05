# report_lookup.py — Parse Returns.xml and find matching returns.
# Standalone version: only the functions needed by the Data Variance service.
# All instance-log / render-document logic from the chatbot has been removed.

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List

from .config import RETURNS_XML_PATH, NON_XBRL_RETURNS_XML_PATH
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

# ── TTL cache ──────────────────────────────────────────────────────────────────

_returns_ttl = float(os.getenv("DV_RETURNS_TTL_SEC", "3600"))


class _TTLCache:
    __slots__ = ("_ttl", "_data", "_ts")

    def __init__(self, ttl: float) -> None:
        self._ttl  = ttl
        self._data = None
        self._ts   = 0.0

    @property
    def loaded_at(self) -> float:
        return self._ts

    def get(self):
        if self._data is not None and (time.monotonic() - self._ts) < self._ttl:
            return self._data
        return None

    def set(self, data):
        self._data = data
        self._ts   = time.monotonic()
        return data


_returns_cache          = _TTLCache(ttl=_returns_ttl)
_norm_cache             = _TTLCache(ttl=_returns_ttl)
_non_xbrl_returns_cache = _TTLCache(ttl=_returns_ttl)


# ── Parsers ────────────────────────────────────────────────────────────────────

def _parse_returns() -> tuple:
    """Parse Returns.xml; return a tuple of attribute dicts, one per <Return>."""
    cached = _returns_cache.get()
    if cached is not None:
        return cached

    root = load_xml_tree(RETURNS_XML_PATH, "Returns.xml")
    if root is None:
        return ()

    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    for el in root.findall("Return"):
        name = el.attrib.get("Name", "").strip()
        if name and name not in seen:
            seen.add(name)
            rows.append(el.attrib)

    result = tuple(rows)
    logger.info("Loaded %d unique return(s) from Returns.xml", len(rows))
    return _returns_cache.set(result)


def _parse_non_xbrl_returns() -> tuple:
    """Parse NonXBRLReturns.xml; return a tuple of attribute dicts, one per <Return>."""
    cached = _non_xbrl_returns_cache.get()
    if cached is not None:
        return cached

    root = load_xml_tree(NON_XBRL_RETURNS_XML_PATH, "NonXBRLReturns.xml")
    if root is None:
        return ()

    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    for el in root.findall("Return"):
        name = el.attrib.get("Name", "").strip()
        if name and name not in seen:
            seen.add(name)
            rows.append(el.attrib)

    result = tuple(rows)
    logger.info("Loaded %d unique return(s) from NonXBRLReturns.xml", len(rows))
    return _non_xbrl_returns_cache.set(result)


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ── Stop-words stripped before keyword extraction ─────────────────────────────
_STOP_WORDS: frozenset = frozenset({
    "show", "me", "open", "get", "give", "find", "return", "returns",
    "the", "a", "an", "for", "of", "data", "filing", "report",
    "with", "in", "on", "at", "to", "from",
})


def extract_keyword(user_input: str) -> str:
    """
    Strip common stop-words and punctuation to surface the core search keyword.
    e.g. "Show me CIMS return"  →  "cims"
         "Open CIMS_RAQ filing" →  "cimsraq"
    """
    tokens = re.split(r"[\s_\-/]+", user_input.lower())
    significant = [_normalise(t) for t in tokens if t and _normalise(t) not in _STOP_WORDS]
    return "".join(significant) if significant else _normalise(user_input)


# ── Confidence scores ─────────────────────────────────────────────────────────
SCORE_EXACT       = 100   # normalised query == normalised field
SCORE_STARTS_WITH =  90   # normalised field starts with query
SCORE_CONTAINS    =  75   # normalised field contains query
SCORE_TOKEN_ALL   =  65   # all tokens found in field
SCORE_TOKEN_ANY   =  50   # at least one token found in field

AUTO_SELECT_THRESHOLD = 90   # auto-pick when top score >= this AND uniquely best


def _normalised_returns() -> tuple:
    if _norm_cache.loaded_at < _returns_cache.loaded_at:
        _norm_cache._data = None
    cached = _norm_cache.get()
    if cached is not None:
        return cached
    result = tuple(
        (
            _normalise(r.get("Name", "")),
            _normalise(r.get("ReturnId", "")),
            _normalise(r.get("AltName", "")),
            r,
        )
        for r in _parse_returns()
        if r.get("Name", "")
    )
    return _norm_cache.set(result)


def _score_row(norm_name: str, norm_rid: str, norm_alt: str, query: str, tokens: List[str]) -> int:
    """Return the highest confidence score for this row against the query."""
    fields = [f for f in (norm_name, norm_rid, norm_alt) if f]

    for f in fields:
        if f == query:
            return SCORE_EXACT
    for f in fields:
        if f.startswith(query):
            return SCORE_STARTS_WITH
    for f in fields:
        if query in f:
            return SCORE_CONTAINS
    for f in fields:
        if tokens and all(t in f for t in tokens):
            return SCORE_TOKEN_ALL
    for f in fields:
        if tokens and any(t in f for t in tokens):
            return SCORE_TOKEN_ANY
    return 0


def search_returns_scored(user_input: str) -> List[Dict[str, Any]]:
    """
    Score every return against *user_input* and return all candidates with
    score > 0, sorted descending by score.

    Each item in the returned list is a dict with keys:
        score       int   — confidence score
        return      dict  — raw return attributes from Returns.xml
    """
    keyword = extract_keyword(user_input)
    tokens  = [t for t in re.split(r"[^a-z0-9]+", keyword) if t]
    nr      = _normalised_returns()

    scored: List[Dict[str, Any]] = []
    for norm_name, norm_rid, norm_alt, r in nr:
        s = _score_row(norm_name, norm_rid, norm_alt, keyword, tokens)
        if s > 0:
            scored.append({"score": s, "return": r})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def find_matching_reports(user_input: str) -> List[Dict[str, Any]]:
    """
    Backward-compatible wrapper — returns a flat list of raw return dicts.
    Preserves original call-sites that only want the raw list.
    """
    scored = search_returns_scored(user_input)
    return [item["return"] for item in scored]


def get_is_excel_by_return_code(return_code: Any, is_non_xbrl: bool = False) -> bool:
    """
    Mirror of .NET GetIsExcelByReturnCode().

    Reads Returns.xml (or NonXBRLReturns.xml when is_non_xbrl=True),
    finds the <Return> element whose Id matches return_code, and returns
    the boolean value of its IsExcel attribute (defaults to False).

    Lookup order:
      1. Primary  — Id attribute (exact match, mirrors .NET)
      2. Fallback — ReturnId attribute (alternate field name used in some XMLs)
    """
    return_code_text = str(return_code).strip()
    # Guard against None / "None" / "null" coming from callers
    if not return_code_text or return_code_text.lower() in ("none", "null"):
        logger.warning(
            "[table_resolution] get_is_excel_by_return_code called with empty/null "
            "return_code=%r — defaulting IsExcel=False",
            return_code,
        )
        return False

    xml_label = "NonXBRLReturns.xml" if is_non_xbrl else "Returns.xml"
    source = _parse_non_xbrl_returns() if is_non_xbrl else _parse_returns()

    # Primary lookup: Id attribute (same as .NET)
    for row in source:
        if str(row.get("Id", "")).strip() == return_code_text:
            val = str(row.get("IsExcel", "false")).strip().lower()
            logger.debug(
                "[table_resolution] Found by Id=%r in %s → IsExcel=%s",
                return_code_text, xml_label, val,
            )
            return val == "true"

    # Fallback lookup: ReturnId attribute (some XMLs use this instead of Id)
    for row in source:
        if str(row.get("ReturnId", "")).strip() == return_code_text:
            val = str(row.get("IsExcel", "false")).strip().lower()
            logger.debug(
                "[table_resolution] Found by ReturnId=%r in %s → IsExcel=%s",
                return_code_text, xml_label, val,
            )
            return val == "true"

    logger.warning(
        "[table_resolution] return_code=%r not found in %s — "
        "defaulting IsExcel=False (table will get _DP suffix if IsSpTableDataEnabled=True)",
        return_code_text, xml_label,
    )
    return False
