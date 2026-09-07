# indexed_returns.py — the authoritative answer to "which returns/tables does
# the embedding index actually cover?"
#
# Why this exists: the embedding index in backend/output/ is built by an
# EXTERNAL tool and covers only a SUBSET of the returns in Returns.xml — at
# time of writing, 51 tables belonging to 3 returns out of 281. Every other
# return is invisible to the NLP layer: there is no vector for its tables, no
# vector for its columns, no row labels, no BM25 entry.
#
# retriever.py already only ever surfaces indexed tables (it can only return
# what the FAISS search returned), but two other paths did NOT respect that
# boundary and walked Returns.xml directly:
#
#   - main.py's _find_named_return_ids(): a query mentioning a return by name
#     could pin a return with zero embeddings.
#   - main.py's _build_return_clarification(): the "which return did you
#     mean?" picker listed EVERY authorized return.
#
# Both could hand the user an option that cannot possibly produce an answer:
# _shortlist_for_return() would build a shortlist whose tables come from the
# XML but whose column list comes from the embedding index — i.e. empty — and
# intent_resolver's deterministic fallback would then resolve to a whole
# untrimmed table (or, if the return has no mapping either, a bare 404 with a
# misleading "Could not resolve this query" message). The user picked a real
# return from a real list and got nonsense.
#
# So: this module derives the covered set ONCE from the index metadata itself
# (never from a hand-maintained list, which would silently rot the moment the
# external tool rebuilds the index), and every "what can the user choose
# between?" path filters through it.

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Set

from . import return_lookup
from .index_store import all_meta
from .nlp_config import TABLE_INDEX_PATH, TABLE_META_PATH

logger = logging.getLogger(__name__)

# Cache keyed on the table index's mtime, matching index_store's own
# invalidation rule — dropping in a freshly-rebuilt backend/output/ folder is
# picked up on the next request with no restart, exactly like the FAISS
# indices themselves.
_cache: Optional[Dict[str, Any]] = None
_cache_mtime: float = -1.0
_lock = threading.Lock()


def _current_mtime() -> float:
    try:
        return max(os.path.getmtime(TABLE_INDEX_PATH), os.path.getmtime(TABLE_META_PATH))
    except OSError:
        return -1.0


def _build() -> Dict[str, Any]:
    """Resolve every indexed table to its owning return, exactly the way
    retriever.py does (same return_lookup call, same metadata text as the
    disambiguation hint), so the coverage set here can never disagree with
    what retrieval will actually produce."""
    records = all_meta(TABLE_INDEX_PATH, TABLE_META_PATH)

    tables_upper: Set[str] = set()
    return_ids: Set[str] = set()
    tables_by_return: Dict[str, Set[str]] = {}
    unresolved: List[str] = []

    for record in records:
        table = record.get("table")
        if not table:
            continue
        tables_upper.add(table.upper())
        # hint_text matters: table names are not unique across returns, and
        # the index text names the return it was built against. Without it a
        # CIMS_RAQ(Quarterly) table can resolve to CIMS_RAQ(Annually) and this
        # module would report coverage for a return that has none.
        ret = return_lookup.get_return_for_table(table, hint_text=record.get("text") or None)
        if not ret or not ret.get("return_id"):
            unresolved.append(table)
            continue
        rid = str(ret["return_id"])
        return_ids.add(rid)
        tables_by_return.setdefault(rid, set()).add(table.upper())

    logger.info(
        "[nlp.indexed_returns] Embedding coverage: %d table(s) across %d return(s) %s%s",
        len(tables_upper), len(return_ids), sorted(return_ids),
        f" | {len(unresolved)} table(s) with unresolvable return_id: {unresolved[:5]}"
        if unresolved else "",
    )
    return {
        "tables_upper": tables_upper,
        "return_ids": return_ids,
        "tables_by_return": tables_by_return,
    }


def _get() -> Dict[str, Any]:
    global _cache, _cache_mtime
    mtime = _current_mtime()
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        _cache = _build()
        _cache_mtime = mtime
        return _cache


def indexed_return_ids() -> Set[str]:
    """The return_ids the embedding index actually covers, as strings.

    An EMPTY set means the index is missing/unreadable entirely — callers
    MUST treat that as "coverage unknown" and NOT filter, otherwise a
    misconfigured INDEX_DIR would silently reduce every clarification list to
    zero options instead of surfacing as the load error it is (which
    GET /variance/nlp-health already reports properly)."""
    return set(_get()["return_ids"])


def indexed_table_names() -> Set[str]:
    """Every indexed table name, UPPERCASED (the index stores them lowercase,
    this app's XML uppercase — see index_store.meta_by_table)."""
    return set(_get()["tables_upper"])


def tables_for_return(return_id: str) -> List[str]:
    """The indexed table names belonging to one return, UPPERCASED.

    This is the reliable source of a return's tables for the NLP layer, and it
    is deliberately preferred over service.find_return_and_tables(): that
    resolves several returns — including all three that currently HAVE
    embeddings — to a Mapping_1.xml whose rows carry no TableName attribute at
    all, yielding zero usable tables. return_lookup (which this is built from)
    carries an XML_Query.xml fallback for exactly that case, which is why it
    finds 26 tables for CIMS_RAQ(Quarterly) where find_return_and_tables finds
    none. See _build().

    Empty list when the return has no indexed tables — including when coverage
    itself is unknown, since there is nothing to scope to in that case.
    """
    return sorted(_get()["tables_by_return"].get(str(return_id), ()))


def has_embeddings(return_id: str) -> bool:
    """False only when coverage is KNOWN and this return isn't in it — see
    indexed_return_ids() on why an empty coverage set means 'don't filter'."""
    covered = indexed_return_ids()
    if not covered:
        return True
    return str(return_id) in covered


def filter_returns(returns: List[Dict[str, Any]], id_key: str = "Id") -> List[Dict[str, Any]]:
    """Narrow a list of Returns.xml rows to just those with embeddings.

    Returns the list UNCHANGED when coverage is unknown (empty set), so a
    missing index degrades to today's unfiltered behavior rather than an
    empty picker."""
    covered = indexed_return_ids()
    if not covered:
        logger.warning(
            "[nlp.indexed_returns] No embedding coverage resolved (index missing or "
            "unreadable?) — NOT filtering the %d candidate return(s); check "
            "GET /variance/nlp-health",
            len(returns),
        )
        return returns

    kept = [r for r in returns if str(r.get(id_key)) in covered]
    if len(kept) < len(returns):
        logger.info(
            "[nlp.indexed_returns] Narrowed %d return(s) -> %d with embeddings",
            len(returns), len(kept),
        )
    return kept


def filter_table_names(table_names: List[str]) -> List[str]:
    """Narrow a list of table names to just the indexed ones (case-insensitive).
    Unchanged when coverage is unknown, same rationale as filter_returns."""
    covered = indexed_table_names()
    if not covered:
        return table_names
    return [t for t in table_names if t.upper() in covered]


def invalidate() -> None:
    """Force a rebuild on the next call (tests / admin action)."""
    global _cache, _cache_mtime
    with _lock:
        _cache = None
        _cache_mtime = -1.0
