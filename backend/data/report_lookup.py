# report_lookup.py — Parse Returns.xml and find matching returns.
# Standalone version: only the functions needed by the Data Variance service.
# All instance-log / render-document logic from the chatbot has been removed.

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from ..config import ANONYMOUS, RequestContext
from ..hosts import get_profile
from .xml_loader import load_xml_tree, max_mtime

logger = logging.getLogger(__name__)

# ── TTL cache ──────────────────────────────────────────────────────────────────

_returns_ttl = float(os.getenv("DV_RETURNS_TTL_SEC", "3600"))


class _TTLCache:
    """TTL cache with one slot per tenant, also invalidated by source mtime.

    Keyed rather than single-slot because the returns master is a different
    file per tenant under iDEAL 6.0; one slot would serve tenant 1002 the
    return list of whichever tenant happened to load first. The key is the
    empty string under 5.5, so that host keeps exactly one slot as before.

    The mtime check exists so an external edit to Returns.xml (e.g. someone
    adding a return) is picked up on the very next call instead of waiting up
    to _ttl seconds — the TTL is kept only as a safety net.
    """

    __slots__ = ("_ttl", "_data", "_ts", "_mtime")

    def __init__(self, ttl: float) -> None:
        self._ttl   = ttl
        self._data:  dict = {}
        self._ts:    dict = {}
        self._mtime: dict = {}

    def loaded_at(self, key: str = "") -> float:
        return self._ts.get(key, 0.0)

    def peek(self, key: str = "") -> tuple:
        """(data, mtime) as last stored, ignoring freshness — for reload diffs."""
        return self._data.get(key), self._mtime.get(key)

    def discard(self, key: str = "") -> None:
        self._data.pop(key, None)

    def get(self, key: str = "", mtime: float | None = None):
        data = self._data.get(key)
        if data is None:
            return None
        if (time.monotonic() - self._ts.get(key, 0.0)) >= self._ttl:
            return None
        if mtime is not None and self._mtime.get(key) != mtime:
            return None
        return data

    def set(self, data, key: str = "", mtime: float = 0.0):
        self._data[key]  = data
        self._ts[key]    = time.monotonic()
        self._mtime[key] = mtime
        return data

    def clear(self) -> None:
        self._data.clear()
        self._ts.clear()
        self._mtime.clear()


_returns_cache          = _TTLCache(ttl=_returns_ttl)
_norm_cache             = _TTLCache(ttl=_returns_ttl)
_non_xbrl_returns_cache = _TTLCache(ttl=_returns_ttl)


# ── Parsers ────────────────────────────────────────────────────────────────────

def _parse_returns_master(ctx: RequestContext, cache: _TTLCache, non_xbrl: bool) -> tuple:
    """One attribute dict per return row of a returns master, deduplicated by
    Name and cached per tenant. The filename and row element come from the
    host profile: 5.5 is <Returns>/<Return>, 6.0 is <Document>/<Row>."""
    profile = get_profile()
    profile.validate_context(ctx)
    path    = profile.non_xbrl_returns_xml_path(ctx) if non_xbrl else profile.returns_xml_path(ctx)
    row_tag = profile.returns_row_tag

    mtime = max_mtime(path)
    cached = cache.get(ctx.tenant_id, mtime)
    if cached is not None:
        return cached

    prev_rows, prev_mtime = cache.peek(ctx.tenant_id)
    reason = (
        "first load" if prev_mtime is None
        else "file changed" if prev_mtime != mtime
        else "ttl expired"
    )

    root = load_xml_tree(path, os.path.basename(path))
    if root is None:
        return ()

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for el in root.findall(row_tag):
        name = el.attrib.get("Name", "").strip()
        if name and name not in seen:
            seen.add(name)
            row = dict(el.attrib)
            # One frequency for every consumer (variance engine, date list, NLP):
            # an unusable RepFreq ("x" in 6.0, or blank) is filled from PeriodId.
            # The file's own value is kept as RepFreqRaw for diagnostics.
            freq = profile.resolve_frequency(row, ctx)
            if freq and freq != (row.get("RepFreq") or "").strip().upper():
                row["RepFreqRaw"] = row.get("RepFreq", "")
                row["RepFreq"] = freq
            rows.append(row)

    result = tuple(rows)
    logger.info(
        "Loaded %d unique %sreturn(s) from %s | tenant=%r | row_tag=<%s> | "
        "reload_reason=%s | mtime %s -> %s",
        len(rows), "non-XBRL " if non_xbrl else "", path, ctx.tenant_id, row_tag,
        reason, prev_mtime, mtime,
    )
    if reason == "file changed" and prev_rows is not None:
        prev_names = {r.get("Name", "") for r in prev_rows}
        new_names  = {r.get("Name", "") for r in rows}
        added   = sorted(new_names - prev_names)
        removed = sorted(prev_names - new_names)
        if added or removed:
            logger.info(
                "[report_lookup] %s master changed | tenant=%r | added=%s | removed=%s",
                "non-XBRL returns" if non_xbrl else "returns", ctx.tenant_id, added, removed,
            )
    return cache.set(result, ctx.tenant_id, mtime)


def parse_returns(ctx: RequestContext = ANONYMOUS) -> tuple:
    """Parse the XBRL returns master; one attribute dict per return row."""
    return _parse_returns_master(ctx, _returns_cache, non_xbrl=False)


def _parse_non_xbrl_returns(ctx: RequestContext = ANONYMOUS) -> tuple:
    """Parse the non-XBRL returns master; one attribute dict per return row."""
    return _parse_returns_master(ctx, _non_xbrl_returns_cache, non_xbrl=True)


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


def _normalised_returns(ctx: RequestContext = ANONYMOUS) -> tuple:
    key = ctx.tenant_id
    if _norm_cache.loaded_at(key) < _returns_cache.loaded_at(key):
        _norm_cache.discard(key)
    cached = _norm_cache.get(key)
    if cached is not None:
        return cached
    result = tuple(
        (
            _normalise(r.get("Name", "")),
            _normalise(r.get("ReturnId", "")),
            _normalise(r.get("AltName", "")),
            # The return's numeric Id, so "6001" finds return 6001. ReturnId
            # above is a different thing — a short code like "R145" — and on
            # its own left the id a user actually sees unsearchable.
            _normalise(r.get("Id", "")),
            # The stop-word-stripped form of the name. extract_keyword() strips
            # words like "of"/"to"/"for" from the QUERY, so a name that
            # contains one could never match itself exactly: searching
            # "Credit to Women(Excel)" yielded "creditwomenexcel" while the
            # field held "credittowomenexcel". Storing both forms makes an
            # exact-name search score as exact. This matters beyond tidiness —
            # the UI resolves an ambiguous search by re-querying with the
            # chosen candidate's full name, which failed outright for the 25
            # such names in 5.5 and for most 6.0 names, whose titles are
            # prose ("QCB_F014_Breakdown of Funding by geography").
            extract_keyword(r.get("Name", "")),
            r,
        )
        for r in parse_returns(ctx)
        if r.get("Name", "")
    )
    return _norm_cache.set(result, key)


def _score_row(
    norm_name: str,
    norm_rid: str,
    norm_alt: str,
    norm_id: str,
    norm_kw: str,
    query: str,
    tokens: list[str],
) -> int:
    """Return the highest confidence score for this row against the query."""
    # dict.fromkeys keeps order while dropping duplicates — norm_kw equals
    # norm_name for any name without stop words (most of them), and a
    # duplicated field would otherwise just be scored twice for no gain.
    fields = [f for f in dict.fromkeys((norm_name, norm_rid, norm_alt, norm_id, norm_kw)) if f]

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


def search_returns_scored(
    user_input: str, ctx: RequestContext = ANONYMOUS
) -> list[dict[str, Any]]:
    """
    Score every return against *user_input* and return all candidates with
    score > 0, sorted descending by score.

    Each item in the returned list is a dict with keys:
        score       int   — confidence score
        return      dict  — raw return attributes from Returns.xml
    """
    keyword = extract_keyword(user_input)
    tokens  = [t for t in re.split(r"[^a-z0-9]+", keyword) if t]
    nr      = _normalised_returns(ctx)

    scored: list[dict[str, Any]] = []
    for norm_name, norm_rid, norm_alt, norm_id, norm_kw, r in nr:
        s = _score_row(norm_name, norm_rid, norm_alt, norm_id, norm_kw, keyword, tokens)
        if s > 0:
            scored.append({"score": s, "return": r})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def get_is_excel_by_return_code(
    return_code: Any, is_non_xbrl: bool = False, ctx: RequestContext = ANONYMOUS
) -> bool:
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

    profile   = get_profile()
    xml_label = os.path.basename(
        profile.non_xbrl_returns_xml_path(ctx) if is_non_xbrl
        else profile.returns_xml_path(ctx)
    )
    source = _parse_non_xbrl_returns(ctx) if is_non_xbrl else parse_returns(ctx)

    # Id first (same as .NET), then ReturnId, which some XMLs use instead.
    for attr in ("Id", "ReturnId"):
        for row in source:
            if str(row.get(attr, "")).strip() == return_code_text:
                val = str(row.get("IsExcel", "false")).strip().lower()
                logger.debug(
                    "[table_resolution] Found by %s=%r in %s → IsExcel=%s",
                    attr, return_code_text, xml_label, val,
                )
                return val == "true"

    logger.warning(
        "[table_resolution] return_code=%r not found in %s — "
        "defaulting IsExcel=False (table will get _DP suffix if IsSpTableDataEnabled=True)",
        return_code_text, xml_label,
    )
    return False
