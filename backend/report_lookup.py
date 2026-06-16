from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from .config import (
    RETURNS_XML_PATH,
    NON_XBRL_RETURNS_XML_PATH,
    get_tenant_returns_xml_path,
    get_tenant_non_xbrl_returns_xml_path,
)
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

_returns_ttl = float(os.getenv("DV_RETURNS_TTL_SEC", "3600"))


class _TTLCache:
    __slots__ = ("_ttl", "_store")

    def __init__(self, ttl: float) -> None:
        self._ttl   = ttl
        self._store: dict = {}   # key → (data, timestamp)

    def get(self, key: str):
        entry = self._store.get(key)
        if entry and (time.monotonic() - entry[1]) < self._ttl:
            return entry[0]
        return None

    def set(self, key: str, data):
        self._store[key] = (data, time.monotonic())
        return data


_returns_cache          = _TTLCache(ttl=_returns_ttl)
_non_xbrl_returns_cache = _TTLCache(ttl=_returns_ttl)
_norm_cache             = _TTLCache(ttl=_returns_ttl)


# ── Parsers ────────────────────────────────────────────────────────────────────
#
# NOTE: Return.xml / NonXBRLReturn.xml both use <Document><Row .../></Document>
# as their structure (same as user.xml, department.xml, XML_Tenant.xml) — there
# is NO <Return> child tag. Previously these parsers called root.findall("Return"),
# which always matched zero elements and silently produced empty results for
# every search, regardless of keyword. Fixed to root.findall("Row").

def _parse_returns(tenant_id: str = "") -> tuple:
    cache_key = tenant_id or "__global__"
    cached = _returns_cache.get(cache_key)
    if cached is not None:
        logger.info("[report_lookup] _parse_returns CACHE HIT | tenant=%r | key=%r", tenant_id, cache_key)
        return cached

    path = (
        get_tenant_returns_xml_path(tenant_id)
        if tenant_id
        else RETURNS_XML_PATH
    )
    logger.info("[report_lookup] _parse_returns | tenant=%r | path=%s | exists=%s", 
                tenant_id, path, os.path.exists(path))

    root = load_xml_tree(path, "Return.xml")
    if root is None:
        logger.error("[report_lookup] ❌ Cannot load Return.xml | path=%s", path)
        return ()

    seen: set = set()
    rows: List[Dict[str, Any]] = []
    for el in root.findall("Row"):
        name = el.attrib.get("Name", "").strip()
        if name and name not in seen:
            seen.add(name)
            rows.append(el.attrib)

    result = tuple(rows)
    logger.info("[report_lookup] ✅ Loaded %d return(s) | tenant=%r | path=%s", 
                len(rows), tenant_id, path)
    
    # Log first 5 return names so we can see what's actually in the file
    sample = [r.get("Name", "") for r in rows[:5]]
    logger.info("[report_lookup] Sample return names: %s", sample)

    return _returns_cache.set(cache_key, result)


def _parse_non_xbrl_returns(tenant_id: str = "") -> tuple:
    cache_key = tenant_id or "__global__"
    cached = _non_xbrl_returns_cache.get(cache_key)
    if cached is not None:
        return cached

    path = (
        get_tenant_non_xbrl_returns_xml_path(tenant_id)
        if tenant_id
        else NON_XBRL_RETURNS_XML_PATH
    )
    root = load_xml_tree(path, "NonXBRLReturn.xml")
    if root is None:
        return ()

    seen: set = set()
    rows: List[Dict[str, Any]] = []
    for el in root.findall("Row"):
        name = el.attrib.get("Name", "").strip()
        if name and name not in seen:
            seen.add(name)
            rows.append(el.attrib)

    result = tuple(rows)
    logger.info("[report_lookup] Loaded %d non-xbrl return(s) | tenant=%r", len(rows), tenant_id)
    return _non_xbrl_returns_cache.set(cache_key, result)


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_STOP_WORDS: frozenset = frozenset({
    "show", "me", "open", "get", "give", "find", "return", "returns",
    "the", "a", "an", "for", "of", "data", "filing", "report",
    "with", "in", "on", "at", "to", "from",
})


def extract_keyword(user_input: str) -> str:
    tokens = re.split(r"[\s_\-/]+", user_input.lower())
    significant = [_normalise(t) for t in tokens if t and _normalise(t) not in _STOP_WORDS]
    return "".join(significant) if significant else _normalise(user_input)


SCORE_EXACT       = 100
SCORE_STARTS_WITH =  90
SCORE_CONTAINS    =  75
SCORE_TOKEN_ALL   =  65
SCORE_TOKEN_ANY   =  50
AUTO_SELECT_THRESHOLD = 90


def _score_row(norm_name: str, norm_rid: str, norm_alt: str, query: str, tokens: List[str]) -> int:
    fields = [f for f in (norm_name, norm_rid, norm_alt) if f]
    for f in fields:
        if f == query:             return SCORE_EXACT
    for f in fields:
        if f.startswith(query):    return SCORE_STARTS_WITH
    for f in fields:
        if query in f:             return SCORE_CONTAINS
    for f in fields:
        if tokens and all(t in f for t in tokens): return SCORE_TOKEN_ALL
    for f in fields:
        if tokens and any(t in f for t in tokens): return SCORE_TOKEN_ANY
    return 0


def search_returns_scored(user_input: str, tenant_id: str = "") -> List[Dict[str, Any]]:
    keyword = extract_keyword(user_input)
    tokens  = [t for t in re.split(r"[^a-z0-9]+", keyword) if t]
    
    logger.info("[report_lookup] search_returns_scored | input=%r | tenant_id=%r | keyword=%r | tokens=%s",
                user_input, tenant_id, keyword, tokens)

    rows = _parse_returns(tenant_id)
    logger.info("[report_lookup] Total returns to search: %d", len(rows))

    if not rows:
        logger.error("[report_lookup] ❌ No returns loaded — Returns.xml missing or empty | tenant=%r", tenant_id)
        return []

    scored: List[Dict[str, Any]] = []
    for r in rows:
        norm_name = _normalise(r.get("Name", ""))
        norm_rid  = _normalise(r.get("ReturnId", ""))
        norm_alt  = _normalise(r.get("AltName", ""))
        s = _score_row(norm_name, norm_rid, norm_alt, keyword, tokens)
        if s > 0:
            scored.append({"score": s, "return": r})

    scored.sort(key=lambda x: x["score"], reverse=True)
    
    logger.info("[report_lookup] search done | hits=%d | top_score=%s | top_name=%s",
                len(scored),
                scored[0]["score"] if scored else "N/A",
                scored[0]["return"].get("Name") if scored else "N/A")

    return scored


def find_matching_reports(user_input: str, tenant_id: str = "") -> List[Dict[str, Any]]:
    return [item["return"] for item in search_returns_scored(user_input, tenant_id)]


def get_is_excel_by_return_code(
    return_code: Any,
    is_non_xbrl: bool = False,
    tenant_id: str = "",
) -> bool:
    return_code_text = str(return_code).strip()
    if not return_code_text or return_code_text.lower() in ("none", "null"):
        return False

    source = (
        _parse_non_xbrl_returns(tenant_id)
        if is_non_xbrl
        else _parse_returns(tenant_id)
    )

    for row in source:
        if str(row.get("Id", "")).strip() == return_code_text:
            return str(row.get("IsExcel", "false")).strip().lower() == "true"
    for row in source:
        if str(row.get("ReturnId", "")).strip() == return_code_text:
            return str(row.get("IsExcel", "false")).strip().lower() == "true"

    return False