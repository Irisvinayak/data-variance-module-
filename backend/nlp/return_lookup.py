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
from typing import Any, Dict, List, Optional

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


def _build_lookup() -> Dict[str, List[Dict[str, Any]]]:
    """Walk every <Return> in Returns.xml and build
    {UPPERCASE base table name (no _DP suffix): [candidate return metadata, ...]}.

    A table name is NOT unique across returns — 96 names in this dataset are
    claimed by more than one return (52 of them by returns with DIFFERENT
    RepFreq), because the same physical table is shared by e.g. the Monthly /
    Quarterly / Annual variants of a return, and because a query may JOIN a
    table that another return owns. This used to store a single dict per name,
    so the last (or, on the XML_Query path, the first) writer silently won and
    the winner depended on document order in Returns.xml.

    That was not cosmetic: report_freq rides on this resolution and drives
    calculate_variance.get_previous_dates(), so a table resolving to the
    Annual variant of its return computed comparison periods a YEAR apart on
    quarterly data — wrong numbers, no error. The return_id is also what
    retriever.py auth-checks against, so a wrong winner could both wrongly
    grant and wrongly deny access.

    Every claimant is therefore kept here, and the choice is deferred to
    get_return_for_table(), which can use the caller's hint text to pick
    correctly. See _select_candidate()."""
    lookup: Dict[str, List[Dict[str, Any]]] = {}

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
                # A mapping-file row is an explicit ownership declaration, so
                # it always counts as primary (unlike an XML_Query FROM/JOIN
                # reference, which may just be a lookup join).
                lookup.setdefault(key, []).append({
                    "return_id": return_id,
                    "return_name": return_name,
                    "report_freq": report_freq,
                    "filter_col": el.attrib.get("FilterColumn", ""),
                    "comp_filter_col_names": [c.strip() for c in comp_raw.split("|") if c.strip()],
                    "source": "mapping",
                    "is_primary": True,
                })
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
                # Recorded as one more CANDIDATE rather than skipped-if-present:
                # the old "if base_key in lookup: continue" made this path
                # first-writer-wins while the mapping path above was
                # last-writer-wins, so ownership flipped depending on which
                # code path a return happened to take. _select_candidate()
                # now weighs mapping vs XML_Query explicitly.
                lookup.setdefault(base_key, []).append({
                    "return_id": return_id,
                    "return_name": return_name,
                    "report_freq": report_freq,
                    "filter_col": meta["filter_col"],
                    "comp_filter_col_names": [],
                    "source": "xml_query",
                    # False => the table was only JOINed by this return's
                    # queries, i.e. someone else almost certainly owns it.
                    "is_primary": bool(meta.get("is_primary")),
                })
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

    contested = {k: v for k, v in lookup.items() if len(v) > 1}
    logger.info(
        "[nlp.return_lookup] Built lookup for %d table(s) across returns "
        "(%d claimed by more than one return)",
        len(lookup), len(contested),
    )
    if contested:
        sample = list(contested.items())[:5]
        logger.info(
            "[nlp.return_lookup] Contested table names (sample): %s",
            {k: [(c["return_id"], c["report_freq"], c["source"]) for c in v] for k, v in sample},
        )
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


def _get_lookup() -> Dict[str, List[Dict[str, Any]]]:
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


def _select_candidate(
    table_name: str,
    candidates: List[Dict[str, Any]],
    hint_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Pick which claiming return actually owns `table_name`.

    `hint_text` is any text the caller already associates with this table —
    in practice the embedding index's own metadata text, which embeds the
    return name it was built for (e.g. a table record reads
    "cims_raq_q_sec1_part_a_dom | CIMS_RAQ(Quarterly) | ..."). That is the
    strongest available signal, because it states what the vectors were
    actually built against, and it is what makes the difference between
    resolving CIMS_RAQ tables to 2041 (Quarterly) rather than 2065
    (Annually) — the latter silently produced year-apart comparison periods
    on quarterly data.

    Order of preference:
      1. a candidate whose return_name appears verbatim in `hint_text`
      2. a "primary" claim (mapping-file row, or the FROM target of an
         XML_Query SELECT) over a mere JOIN reference
      3. a mapping-file claim over an XML_Query-derived one
      4. lowest return_id — arbitrary, but STABLE. The previous behaviour
         depended on Returns.xml document order, so adding an unrelated
         return could silently re-own existing tables.
    """
    if len(candidates) == 1:
        return candidates[0]

    if hint_text:
        haystack = hint_text.lower()
        named = [
            c for c in candidates
            if c.get("return_name") and c["return_name"].strip().lower() in haystack
        ]
        if len(named) == 1:
            return named[0]
        if named:
            candidates = named  # narrowed, still tied — fall through to the rest

    ranked = sorted(
        candidates,
        key=lambda c: (
            not c.get("is_primary"),           # primary first
            c.get("source") != "mapping",      # mapping first
            str(c.get("return_id") or ""),     # stable tie-break
        ),
    )
    winner = ranked[0]
    if len(ranked) > 1:
        logger.warning(
            "[nlp.return_lookup] Table %r is claimed by %d returns %s — resolved to "
            "return_id=%s (freq=%s) by fallback rules%s. If this is wrong, the "
            "embedding metadata for this table should name its return.",
            table_name, len(ranked),
            [(c["return_id"], c["report_freq"], c["source"]) for c in ranked],
            winner["return_id"], winner["report_freq"],
            "" if hint_text else " (no hint_text supplied by caller)",
        )
    return winner


def get_return_for_table(
    table_name: str, hint_text: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return {"return_id", "return_name", "report_freq", "filter_col",
    "comp_filter_col_names"} for `table_name`, or None if it isn't tied to
    any known return.

    Pass `hint_text` whenever the caller has text associated with the table
    (e.g. its embedding-index metadata) — table names are not unique across
    returns and the hint is what disambiguates them. See _select_candidate."""
    key = _strip_dp_suffix(table_name).upper()
    candidates = _get_lookup().get(key)
    if not candidates:
        return None
    return _select_candidate(table_name, candidates, hint_text)


def candidates_for_table(table_name: str) -> List[Dict[str, Any]]:
    """Every return claiming `table_name` (diagnostics / ambiguity reports)."""
    return list(_get_lookup().get(_strip_dp_suffix(table_name).upper(), []))


def invalidate() -> None:
    """Force the next get_return_for_table() call to re-read the XML
    synchronously (bypasses stale-while-revalidate — the caller explicitly
    wants a guaranteed-fresh read, e.g. tests or an admin action)."""
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0
