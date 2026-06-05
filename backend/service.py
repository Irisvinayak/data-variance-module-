# service.py — Orchestration layer for the standalone Data Variance application.
# Zero dependencies on the chatbot backend — all imports are local or stdlib.

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
)
from .xml_loader import load_xml_tree
from .report_lookup import (
    find_matching_reports, _parse_returns, get_is_excel_by_return_code,
    search_returns_scored, AUTO_SELECT_THRESHOLD,
)
from .calculate_variance import calculate_variance

logger = logging.getLogger(__name__)


def _resolve_report_table_name(
    return_id: str,
    table_name: str,
    is_non_xbrl: bool = False,
) -> str:
    """
    Mirror of the .NET table-name resolution block:

        bool isExcel = GetIsExcelByReturnCode(returnCode, isNonXbrl);
        string reportName = tableName;
        if (isSpTableDataEnabled && !isExcel)
            reportName = tableName + "_DP";
    """
    is_excel = get_is_excel_by_return_code(return_id, is_non_xbrl=is_non_xbrl)

    report_name = table_name
    if IS_SP_TABLE_DATA_ENABLED and not is_excel:
        report_name = f"{table_name}_DP"

    # Required detailed logging (matches .NET diagnostic pattern)
    logger.info("[table_resolution] ReturnCode=%s", return_id)
    logger.info("[table_resolution] IsExcel=%s", is_excel)
    logger.info("[table_resolution] IsSpTableDataEnabled=%s", IS_SP_TABLE_DATA_ENABLED)
    logger.info("[table_resolution] OriginalTable=%s", table_name)
    logger.info("[table_resolution] FinalReportName=%s", report_name)

    return report_name


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostic helper — logs Oracle table state when 0 rows are returned
# ─────────────────────────────────────────────────────────────────────────────

def _run_table_diagnostics(
    table_name: str,
    filter_col: str,
    execute_query_fn: Callable,
) -> None:
    sep = "=" * 70

    # Step 1: row count
    try:
        sql = f"SELECT COUNT(*) AS CNT FROM {table_name}"
        cols, rows, err = execute_query_fn(sql)
        if err:
            logger.error("[DIAG] Step1 ERROR: %s", err)
        else:
            cnt = rows[0][0] if rows else "N/A"
            logger.error("[DIAG] Step1 — table=%s total_rows=%s", table_name, cnt)
    except Exception as exc:
        logger.error("[DIAG] Step1 EXCEPTION: %s", exc)

    # Step 2: distinct date values
    try:
        sql = (
            f"SELECT DISTINCT {filter_col} FROM {table_name} "
            f"ORDER BY {filter_col} DESC FETCH FIRST 10 ROWS ONLY"
        )
        cols, rows, err = execute_query_fn(sql)
        if err:
            sql = (
                f"SELECT DISTINCT {filter_col} FROM "
                f"(SELECT {filter_col} FROM {table_name} ORDER BY {filter_col} DESC) "
                f"WHERE ROWNUM <= 10"
            )
            cols, rows, err2 = execute_query_fn(sql)
        if rows:
            logger.error("[DIAG] Step2 — distinct %s values: %s",
                         filter_col, [str(r[0]) for r in rows])
        else:
            logger.error("[DIAG] Step2 — no distinct date values found")
    except Exception as exc:
        logger.error("[DIAG] Step2 EXCEPTION: %s", exc)

    logger.error("[DIAG] %s diagnostics complete.", sep)


# ─────────────────────────────────────────────────────────────────────────────
# Table-mapping loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_table_mapping(return_id: str, tbl_path: str):
    candidates: List[str] = []

    if os.path.isabs(tbl_path):
        candidates.append(tbl_path)

    candidates.append(os.path.join(TABLE_MAPPING_BASE_DIR, str(return_id), tbl_path))
    candidates.append(os.path.join(TABLE_MAPPING_BASE_DIR, tbl_path))
    candidates.append(os.path.join(INSTANCE_BASE_DIR, str(return_id), tbl_path))

    if RETURNS_XML_PATH:
        candidates.append(
            os.path.join(os.path.dirname(RETURNS_XML_PATH), str(return_id), tbl_path)
        )

    for fixed_name in ("TableMapping.xml", "TabelMapping.xml"):
        candidates.append(
            os.path.join(TABLE_MAPPING_BASE_DIR, str(return_id), fixed_name)
        )

    for c in candidates:
        if not c:
            continue
        norm = os.path.normpath(c)
        logger.info("[service] Checking mapping path=%s", norm)
        if os.path.exists(norm):
            logger.info("[service] Found mapping=%s", norm)
            root = load_xml_tree(norm, label=f"Table mapping for return {return_id}")
            return root, norm

    fallback = os.path.normpath(
        os.path.join(TABLE_MAPPING_BASE_DIR, str(return_id), tbl_path)
    )
    logger.error("[service] Mapping file not found. Using fallback=%s", fallback)
    root = load_xml_tree(fallback, label=f"Table mapping for return {return_id}")
    return root, fallback


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def find_return_and_tables(return_input: str) -> Dict[str, Any]:
    """Find a return by name and list available tables from its mapping XML.

    Response variants:
      • Exact / unique high-confidence match  → normal result dict
      • Multiple plausible matches            → {"candidates": [...]}
      • Nothing found                         → {"error": "..."}
    """
    logger.info("[service] Finding return=%s", return_input)

    scored = search_returns_scored(return_input)
    if not scored:
        return {"error": f"Return '{return_input}' not found."}

    top_score   = scored[0]["score"]
    top_matches = [item for item in scored if item["score"] == top_score]

    # Auto-select only when the single best result is clearly above the threshold
    # AND no other row shares the same score.
    if top_score >= AUTO_SELECT_THRESHOLD and len(top_matches) == 1:
        r = top_matches[0]["return"]
    elif len(scored) > 1:
        # Return all unique candidates so the frontend can show a pick-list.
        # Only include returns that have TblPath (can actually be queried) first,
        # then fallback rows without TblPath, so the UI can flag them.
        unique_ids: set = set()
        candidates = []
        for item in scored:
            rid = item["return"].get("Id", "")
            if rid in unique_ids:
                continue
            unique_ids.add(rid)
            candidates.append({
                "score":       item["score"],
                "return_id":   rid,
                "return_name": item["return"].get("Name", ""),
                "report_freq": item["return"].get("RepFreq", ""),
                "tbl_path":    (item["return"].get("TblPath") or "").strip(),
                "has_mapping": bool((item["return"].get("TblPath") or "").strip()),
            })
        logger.info(
            "[service] Ambiguous query=%r → %d candidates (top_score=%d)",
            return_input, len(candidates), top_score,
        )
        return {"candidates": candidates, "query": return_input}
    else:
        r = scored[0]["return"]

    return_id = r.get("Id")
    tbl_path  = (r.get("TblPath") or "").strip()

    if not tbl_path:
        return {
            "error": (
                f"Return '{r.get('Name', return_input)}' (Id={return_id}) does not define "
                "TblPath in Returns.xml — it has no table-mapping metadata."
            )
        }

    root, resolved_path = _load_table_mapping(return_id, tbl_path)

    if root is None:
        return {"error": f"Table mapping file not found at {resolved_path}"}

    tables = []
    for el in root.findall("Row"):
        tables.append({
            "table_name":           el.attrib.get("TableName"),
            "filter_col":          el.attrib.get("FilterColumn"),
            "primary_column":      el.attrib.get("PrimaryColumn"),
            "comp_filter_col_name": el.attrib.get("CompFilterColName"),
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


def _get_table_metadata(
    return_id: str,
    tbl_path: str,
    table_name: str,
) -> Dict[str, Any]:
    root, _ = _load_table_mapping(return_id, tbl_path)

    if root is None:
        raise FileNotFoundError("Table mapping not found")

    tname_up = table_name.strip().upper()

    for el in root.findall("Row"):
        xml_name = (el.attrib.get("TableName") or "").strip().upper()
        if xml_name == tname_up:
            comp      = el.attrib.get("CompFilterColName", "")
            comp_cols = [c.strip().upper() for c in comp.split("|") if c.strip()]
            filter_col = (el.attrib.get("FilterColumn") or "").strip().upper()
            return {
                "filter_col":            filter_col,
                "comp_filter_col_names": comp_cols,
                "report_freq":           None,
                "is_single":             el.attrib.get("IsSingle", "false").lower() == "true",
                "return_code_col":       (el.attrib.get("ReturnCodeColumn") or "").upper() or None,
                "freq_col":              (el.attrib.get("FreqColumn") or "").upper() or None,
                "freq_val":              el.attrib.get("FreqValue"),
            }

    available = [el.attrib.get("TableName", "") for el in root.findall("Row")]
    raise KeyError(
        f"Table '{table_name}' not found in mapping. Available: {available}"
    )


def compute_variance(
    return_id: str,
    return_tbl_path: str,
    table_name: str,
    reporting_date: str,
    reporting_period: int,
    execute_query_fn: Callable,
    connection_string: Optional[str] = None,
    selected_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Orchestrate full variance computation for one table."""
    logger.info("[service] compute_variance started")

    # Resolve report frequency from Returns.xml
    parsed      = _parse_returns()
    return_meta = next((r for r in parsed if r.get("Id") == str(return_id)), None)
    report_freq = (
        (return_meta.get("RepFreq") or "").strip().upper()
        if return_meta
        else ""
    ) or "M"

    logger.info(
        "[service] return_id=%s | freq=%s | table=%s | date=%s | periods=%s",
        return_id, report_freq, table_name, reporting_date, reporting_period,
    )

    # ── Table-name resolution (mirrors .NET IsExcel / IsSpTableDataEnabled logic) ──
    # Use return_meta already fetched above instead of a second XML lookup.
    is_excel = (
        str(return_meta.get("IsExcel", "false")).strip().lower() == "true"
        if return_meta
        else get_is_excel_by_return_code(return_id)
    )
    report_name = table_name
    if IS_SP_TABLE_DATA_ENABLED and not is_excel:
        dp_name = f"{table_name}_DP"
        # Prefix with the owning schema so Oracle resolves the table correctly
        # e.g. CRILC.CIMS_RAQ_Q_SEC1_PART_A_DOM_DP
        report_name = f"{DP_TABLE_SCHEMA}.{dp_name}" if DP_TABLE_SCHEMA else dp_name
    resolved_table_name = report_name

    logger.info("[table_resolution] ReturnCode=%s", return_id)
    logger.info("[table_resolution] IsExcel=%s", is_excel)
    logger.info("[table_resolution] IsSpTableDataEnabled=%s", IS_SP_TABLE_DATA_ENABLED)
    logger.info("[table_resolution] ReturnFoundInXml=%s", return_meta is not None)
    logger.info("[table_resolution] OriginalTable=%s", table_name)
    logger.info("[table_resolution] FinalReportName=%s", resolved_table_name)

    table_meta = _get_table_metadata(return_id, return_tbl_path, table_name)

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
        logger.info("[service] Executing Oracle query:\n%s", query)
        cols, rows, err = execute_query_fn(query)

        if err:
            logger.error(
                "[service] QUERY FAILED\n"
                "  return_id        = %s\n"
                "  original_table   = %s\n"
                "  resolved_table   = %s\n"
                "  reporting_date   = %s\n"
                "  reporting_period = %s\n"
                "  is_excel         = %s\n"
                "  sp_table_enabled = %s\n"
                "  error            = %s",
                return_id, table_name, resolved_table_name,
                reporting_date, reporting_period,
                is_excel, IS_SP_TABLE_DATA_ENABLED, err,
            )
            raise RuntimeError(
                f"{err} | table_queried={resolved_table_name} "
                f"| original_table={table_name}"
            )

        if not rows:
            logger.error("[service] Zero rows — firing diagnostics")
            _run_table_diagnostics(
                table_name=resolved_table_name,
                filter_col=metadata["filter_col"],
                execute_query_fn=execute_query_fn,
            )

        cols_up = [c.upper() for c in cols]
        return [
            {cols_up[i]: rows[ri][i] for i in range(len(cols_up))}
            for ri in range(len(rows))
        ]

    return calculate_variance(
        return_code=return_id,
        table_name=resolved_table_name,
        reporting_date=reporting_date,
        get_table_metadata_fn=get_table_metadata_fn,
        execute_query_fn=execute_query_adapter,
        connection_string=connection_string,
        reporting_period=reporting_period,
        selected_columns=selected_columns,
    )
