# report_lookup.py — Parse Returns.xml and find matching returns.
# Standalone version: only the functions needed by the Data Variance service.
# All instance-log / render-document logic from the chatbot has been removed.

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List

from .config import RETURNS_XML_PATH
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


_returns_cache = _TTLCache(ttl=_returns_ttl)
_norm_cache    = _TTLCache(ttl=_returns_ttl)


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


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


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


def find_matching_reports(user_input: str) -> List[Dict[str, Any]]:
    """Case-insensitive multi-strategy search against Name, ReturnId, AltName."""
    norm = _normalise(user_input)
    tokens = norm.split()
    nr = _normalised_returns()

    def _first(*candidates):
        for c in candidates:
            if c:
                return c
        return []

    exact_rid   = [r for n, ri, a, r in nr if ri == norm]
    exact_name  = [r for n, ri, a, r in nr if n  == norm]
    exact_alt   = [r for n, ri, a, r in nr if a  == norm]
    partial_name = [r for n, ri, a, r in nr if norm in n or n in norm]
    partial_alt  = [r for n, ri, a, r in nr if norm in a or a in norm]
    partial_rid  = [r for n, ri, a, r in nr if norm in ri or ri in norm]
    all_tok_name = [r for n, ri, a, r in nr if all(t in n for t in tokens)]
    all_tok_alt  = [r for n, ri, a, r in nr if all(t in a for t in tokens)]
    any_tok_name = [r for n, ri, a, r in nr if any(t in n for t in tokens)]
    any_tok_alt  = [r for n, ri, a, r in nr if any(t in a for t in tokens)]
    any_tok_rid  = [r for n, ri, a, r in nr if any(t in ri for t in tokens)]

    return _first(
        exact_rid, exact_name, exact_alt,
        partial_name, partial_alt, partial_rid,
        all_tok_name, all_tok_alt,
        any_tok_name, any_tok_alt, any_tok_rid,
    )
