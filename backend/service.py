from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from .config import (
    TABLE_MAPPING_BASE_DIR,
    INSTANCE_BASE_DIR,
    RETURNS_XML_PATH,
    IS_SP_TABLE_DATA_ENABLED,
    DP_TABLE_SCHEMA,
    get_tenant_table_mapping_base_dir,
    get_tenant_returns_xml_path,
)
from .xml_loader import load_xml_tree
from .report_lookup import (
    find_matching_reports, _parse_returns, get_is_excel_by_return_code,
    search_returns_scored, AUTO_SELECT_THRESHOLD,
)
from .calculate_variance import calculate_variance

logger = logging.getLogger(__name__)


def _load_table_mapping(return_id: str, tbl_path: str, tenant_id: str = ""):
    # Resolve base dirs — use tenant-specific if available
    mapping_base = (
        get_tenant_table_mapping_base_dir(tenant_id)
        if tenant_id
        else TABLE_MAPPING_BASE_DIR
    )
    returns_xml = (
        get_tenant_returns_xml_path(tenant_id)
        if tenant_id
        else RETURNS_XML_PATH
    )

    logger.info("[service] _load_table_mapping | return_id=%r | tenant_id=%r | tbl_path=%r", return_id, tenant_id, tbl_path)
    logger.info("[service] mapping_base=%r", mapping_base)

    return_dir = os.path.normpath(os.path.join(mapping_base, str(return_id)))
    logger.info("[service] return_dir=%r  isdir=%s", return_dir, os.path.isdir(return_dir))

    candidates: List[str] = []

    if tbl_path and os.path.isabs(tbl_path):
        candidates.append(tbl_path)

    if tbl_path:
        candidates.append(os.path.join(mapping_base, str(return_id), tbl_path))
        candidates.append(os.path.join(mapping_base, tbl_path))
        candidates.append(os.path.join(INSTANCE_BASE_DIR, str(return_id), tbl_path))
        if returns_xml:
            candidates.append(os.path.join(os.path.dirname(returns_xml), str(return_id), tbl_path))

    for fixed_name in ("TableMapping.xml", "TabelMapping.xml", "tablemapping.xml", "Tablemapping.xml", "TABLEMAPPING.XML"):
        candidates.append(os.path.join(mapping_base, str(return_id), fixed_name))

    # Directory scan
    scan_dirs = [return_dir]
    seen_dirs: set = set()
    unique_scan_dirs = []
    for d in scan_dirs:
        nd = os.path.normpath(d)
        if nd not in seen_dirs:
            seen_dirs.add(nd)
            unique_scan_dirs.append(nd)

    for scan_dir in unique_scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        try:
            for fname in os.listdir(scan_dir):
                if "mapping" in fname.lower() and fname.lower().endswith(".xml"):
                    candidates.append(os.path.join(scan_dir, fname))
        except OSError as exc:
            logger.warning("[service] Dir-scan OSError dir=%r: %s", scan_dir, exc)

    # Deduplicate and probe
    seen_paths: set = set()
    for c in candidates:
        if not c:
            continue
        norm = os.path.normpath(c)
        if norm in seen_paths:
            continue
        seen_paths.add(norm)
        if os.path.exists(norm):
            logger.info("[service] Found mapping=%s", norm)
            root = load_xml_tree(norm, label=f"Table mapping for return {return_id}")
            return root, norm

    fallback = os.path.normpath(
        os.path.join(mapping_base, str(return_id), tbl_path or "TableMapping.xml")
    )
    logger.error("[service] Mapping not found — fallback=%s", fallback)
    root = load_xml_tree(fallback, label=f"Table mapping for return {return_id}")
    return root, fallback


def find_return_and_tables(return_input: str, tenant_id: str = "") -> Dict[str, Any]:
    logger.info("[service] find_return_and_tables | input=%r | tenant_id=%r", return_input, tenant_id)

    scored = search_returns_scored(return_input, tenant_id=tenant_id)
    if not scored:
        return {"error": f"Return '{return_input}' not found."}

    top_score   = scored[0]["score"]
    top_matches = [item for item in scored if item["score"] == top_score]

    if top_score >= AUTO_SELECT_THRESHOLD and len(top_matches) == 1:
        r = top_matches[0]["return"]
    elif len(scored) > 1:
        unique_ids: set = set()
        candidates = []
        for item in scored:
            rid = item["return"].get("Id", "")
            if rid in unique_ids:
                continue
            unique_ids.add(rid)
            tbl_path = (item["return"].get("TblPath") or "").strip()
            candidates.append({
                "score":       item["score"],
                "return_id":   rid,
                "return_name": item["return"].get("Name", ""),
                "report_freq": item["return"].get("RepFreq", ""),
                "tbl_path":    tbl_path,
                "has_mapping": True,
            })
        return {"candidates": candidates, "query": return_input}
    else:
        r = scored[0]["return"]

    return_id = r.get("Id")
    tbl_path  = (r.get("TblPath") or "").strip()
    root, resolved_path = _load_table_mapping(return_id, tbl_path, tenant_id=tenant_id)

    if root is None:
        return {"error": f"Return '{r.get('Name', return_input)}' table mapping not found."}

    tables = []
    for el in root.findall("Row"):
        tables.append({
            "table_name":           el.attrib.get("TableName"),
            "filter_col":           el.attrib.get("FilterColumn"),
            "primary_column":       el.attrib.get("PrimaryColumn"),
            "comp_filter_col_name": el.attrib.get("CompFilterColName"),
            "display_label_col":    el.attrib.get("DisplayLabelColumn") or None,
            **el.attrib,
        })

    return {
        "return_id":          return_id,
        "return_name":        r.get("Name"),
        "report_freq":        r.get("RepFreq", ""),
        "tbl_path":           tbl_path,
        "table_mapping_path": resolved_path,
        "tables":             tables,
    }


def _get_table_metadata(return_id: str, tbl_path: str, table_name: str, tenant_id: str = "") -> Dict[str, Any]:
    root, _ = _load_table_mapping(return_id, tbl_path, tenant_id=tenant_id)
    if root is None:
        raise FileNotFoundError("Table mapping not found")

    tname_up = table_name.strip().upper()
    for el in root.findall("Row"):
        if (el.attrib.get("TableName") or "").strip().upper() == tname_up:
            comp      = el.attrib.get("CompFilterColName", "")
            comp_cols = [c.strip().upper() for c in comp.split("|") if c.strip()]
            return {
                "filter_col":            (el.attrib.get("FilterColumn") or "").strip().upper(),
                "comp_filter_col_names": comp_cols,
                "report_freq":           None,
                "is_single":             el.attrib.get("IsSingle", "false").lower() == "true",
                "return_code_col":       (el.attrib.get("ReturnCodeColumn") or "").upper() or None,
                "freq_col":              (el.attrib.get("FreqColumn") or "").upper() or None,
                "freq_val":              el.attrib.get("FreqValue"),
            }

    available = [el.attrib.get("TableName", "") for el in root.findall("Row")]
    raise KeyError(f"Table '{table_name}' not found in mapping. Available: {available}")


def _run_table_diagnostics(table_name: str, filter_col: str, execute_query_fn: Callable) -> None:
    try:
        _, rows, err = execute_query_fn(f"SELECT COUNT(*) AS CNT FROM {table_name}")
        logger.error("[DIAG] table=%s total_rows=%s err=%s", table_name, rows[0][0] if rows else "N/A", err)
    except Exception as exc:
        logger.error("[DIAG] count query failed: %s", exc)


def compute_variance(
    return_id: str,
    return_tbl_path: str,
    table_name: str,
    reporting_date: str,
    reporting_period: int,
    execute_query_fn: Callable,
    connection_string: Optional[str] = None,
    selected_columns: Optional[List[str]] = None,
    tenant_id: str = "",
    comparison_mode: str = "vs_current",
) -> Dict[str, Any]:
    logger.info("[service] compute_variance | return_id=%s | tenant_id=%r", return_id, tenant_id)

    parsed      = _parse_returns(tenant_id)
    return_meta = next((r for r in parsed if r.get("Id") == str(return_id)), None)
    report_freq = ((return_meta.get("RepFreq") or "").strip().upper() if return_meta else "") or "M"

    is_excel = (
        str(return_meta.get("IsExcel", "false")).strip().lower() == "true"
        if return_meta
        else get_is_excel_by_return_code(return_id, tenant_id=tenant_id)
    )

    report_name = table_name
    if IS_SP_TABLE_DATA_ENABLED and not is_excel:
        dp_name     = f"{table_name}_DP"
        report_name = f"{DP_TABLE_SCHEMA}.{dp_name}" if DP_TABLE_SCHEMA else dp_name
    resolved_table_name = report_name

    table_meta = _get_table_metadata(return_id, return_tbl_path, table_name, tenant_id=tenant_id)
    metadata = {
        "filter_col":            table_meta["filter_col"],
        "comp_filter_col_names": table_meta["comp_filter_col_names"],
        "report_freq":           report_freq,
        "is_single":             table_meta.get("is_single", False),
        "return_code_col":       table_meta.get("return_code_col"),
        "freq_col":              table_meta.get("freq_col"),
        "freq_val":              table_meta.get("freq_val"),
    }

    def get_table_metadata_fn(rc, tn, isnon):
        return metadata

    def execute_query_adapter(query, conn_str=None):
        cols, rows, err = execute_query_fn(query)
        if err:
            raise RuntimeError(f"{err} | table={resolved_table_name}")
        if not rows:
            _run_table_diagnostics(resolved_table_name, metadata["filter_col"], execute_query_fn)
        cols_up = [c.upper() for c in cols]
        return [{cols_up[i]: rows[ri][i] for i in range(len(cols_up))} for ri in range(len(rows))]

    return calculate_variance(
        return_code=return_id,
        table_name=resolved_table_name,
        reporting_date=reporting_date,
        get_table_metadata_fn=get_table_metadata_fn,
        execute_query_fn=execute_query_adapter,
        connection_string=connection_string,
        reporting_period=reporting_period,
        selected_columns=selected_columns,
        comparison_mode=comparison_mode,
    )