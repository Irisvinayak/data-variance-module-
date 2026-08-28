# query_xml_lookup.py — Fallback table discovery via a return's XML_Query.xml.
#
# WHY THIS EXISTS
# ---------------
# Everything in this app resolves "which tables belong to return X" from the
# return's table-mapping XML (<Row TableName=... FilterColumn=.../>), loaded by
# service._load_table_mapping(). But a large share of returns simply have no
# usable mapping file — either the TblPath in Returns.xml points at a file that
# isn't on disk, or the file exists but its <Row> elements carry empty
# TableName attributes (confirmed for the CIMS_ALE returns 2033/2035/2057/2058,
# whose mapping rows are all TableName="").
#
# The consequences were severe and spread across three features:
#   1. nlp/return_lookup.py skipped those returns entirely, so every table
#      under them resolved to return_id=None. retriever.py then DROPS such
#      tables unconditionally, so ~half the embedding index was unreachable —
#      which both emptied the NLP shortlist (making the "which return?" prompt
#      fall back to listing every return) and produced the
#      "Could not resolve this query to a known table/column" 404.
#   2. service._get_table_metadata() raised KeyError for those tables, so
#      GET /variance/dates 404'd and the manual date dropdown permanently
#      showed "No data found for this table".
#   3. compute_variance() couldn't run for them at all.
#
# Those same returns DO describe their tables — in XML_Query.xml, which holds
# the actual SELECT statements the product runs:
#
#   <Document>
#     <Row QueryId="1000" FormId="2033" RptDtClmnName="RDATE" ...>
#       <SelectQuery>Select ... From CIMS_ALE_Q_GEN_INFO Where ...</SelectQuery>
#     </Row>
#
# which carries exactly the three fields the mapping file would have provided:
#   FormId          -> return_id
#   RptDtClmnName   -> filter_col (the reporting-date column)
#   FROM <table>    -> the table name
#
# So this module parses that file as a fallback. It is ONLY consulted when the
# table-mapping route comes up empty — a return with a working mapping file is
# unaffected, and this never overrides mapping data.

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from .config import TABLE_MAPPING_BASE_DIR
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

XML_QUERY_FILENAME = "XML_Query.xml"

# Default reporting-date column. ~80% of rows carry RptDtClmnName="RDATE" and
# the remainder leave it empty; RDATE is this schema's universal convention
# (see nlp/date_resolver.py and main.py, which both already default to it).
_DEFAULT_FILTER_COL = "RDATE"

# Identifier immediately following FROM/JOIN. Deliberately does NOT match
# "FROM (" — a subquery has no table name to take here, and the real table
# appears on its own FROM further in, which this still picks up.
_FROM_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_$#]*)", re.IGNORECASE)

# Not real tables — Oracle's dummy table and words that can follow FROM in
# constructs we don't care about.
_NON_TABLES = {"DUAL", "SELECT", "TABLE", "LATERAL"}

_TTL = float(os.getenv("DV_XML_QUERY_TTL_SEC", "3600"))
_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


def _xml_query_path(return_id: str) -> str:
    return os.path.normpath(
        os.path.join(TABLE_MAPPING_BASE_DIR, str(return_id), XML_QUERY_FILENAME)
    )


def _extract_tables(select_query: str) -> list:
    """Table names referenced by a SELECT, in order of appearance."""
    if not select_query:
        return []
    names = []
    for raw in _FROM_RE.findall(select_query):
        name = raw.strip().upper()
        if name and name not in _NON_TABLES and name not in names:
            names.append(name)
    return names


def _build(return_id: str) -> Dict[str, Dict[str, Any]]:
    """{UPPERCASE table name: {"table_name", "filter_col", "return_id"}}"""
    path = _xml_query_path(return_id)
    if not os.path.isfile(path):
        logger.debug("[query_xml] No %s for return_id=%s (%s)", XML_QUERY_FILENAME, return_id, path)
        return {}

    root = load_xml_tree(path, label=f"{XML_QUERY_FILENAME} for return {return_id}")
    if root is None:
        return {}

    tables: Dict[str, Dict[str, Any]] = {}
    for row in root.findall("Row"):
        select_query = row.findtext("SelectQuery") or ""
        filter_col = (row.attrib.get("RptDtClmnName") or "").strip().upper()
        # FormId is the authoritative return_id for this query; fall back to
        # the folder's own return_id when the attribute is absent.
        form_id = (row.attrib.get("FormId") or "").strip() or str(return_id)

        for index, table_name in enumerate(_extract_tables(select_query)):
            existing = tables.get(table_name)
            # A table can appear across several queries; keep the first entry
            # but let a later row supply a filter_col if the first had none
            # (RptDtClmnName is blank on a minority of rows).
            if existing is not None:
                if not existing["filter_col"] and filter_col:
                    existing["filter_col"] = filter_col
                continue
            tables[table_name] = {
                "table_name": table_name,
                "filter_col": filter_col,
                "return_id": form_id,
                # Only the first table in a query is the one being reported on;
                # the rest are joins/lookups. Kept so callers can prefer
                # primary tables when a name is ambiguous.
                "is_primary": index == 0,
            }

    for meta in tables.values():
        if not meta["filter_col"]:
            meta["filter_col"] = _DEFAULT_FILTER_COL

    logger.info(
        "[query_xml] Parsed %s for return_id=%s -> %d table(s)",
        XML_QUERY_FILENAME, return_id, len(tables),
    )
    return tables


def tables_for_return(return_id: str) -> Dict[str, Dict[str, Any]]:
    """Cached {UPPERCASE table name: metadata} parsed from the return's
    XML_Query.xml. Empty dict when the file is absent/unparseable — callers
    must treat that as "no fallback available", not an error."""
    key = str(return_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
    if cached is not None and (now - cached[0]) < _TTL:
        return cached[1]

    built = _build(key)
    with _lock:
        _cache[key] = (now, built)
    return built


def get_table_metadata(return_id: str, table_name: str) -> Optional[Dict[str, Any]]:
    """Metadata for one table, or None if this return's XML_Query.xml doesn't
    mention it. Shaped to match service._get_table_metadata()'s return value
    so it can be used as a drop-in fallback."""
    meta = tables_for_return(return_id).get(table_name.strip().upper())
    if meta is None:
        return None
    return {
        "filter_col": meta["filter_col"],
        "comp_filter_col_names": [],
        "report_freq": None,
        "is_single": False,
        "return_code_col": None,
        "freq_col": None,
        "freq_val": None,
    }


def invalidate() -> None:
    """Drop the cache (tests / admin actions)."""
    with _lock:
        _cache.clear()
