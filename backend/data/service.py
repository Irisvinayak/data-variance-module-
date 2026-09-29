# service.py — Orchestration layer for the standalone Data Variance application.
# Zero dependencies on the chatbot backend — all imports are local or stdlib.

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable

from ..config import (
    ANONYMOUS,
    IS_SP_TABLE_DATA_ENABLED,
    DP_TABLE_SCHEMA,
    RequestContext,
)
from ..hosts import get_profile
from .xml_loader import load_xml_tree
from . import query_xml_lookup
from .report_lookup import (
    parse_returns, get_is_excel_by_return_code,
    search_returns_scored, AUTO_SELECT_THRESHOLD,
)
from .calculate_variance import (
    calculate_variance, format_oracle_date, validate_reporting_date,
)

logger = logging.getLogger(__name__)


def _distinct_dates_desc(
    execute_query_fn: Callable,
    table_name: str,
    filter_col: str,
    limit: int,
    before: Optional[datetime] = None,
) -> tuple[list, Optional[str]]:
    """Up to `limit` distinct `filter_col` values, newest first, as (rows, err).

    Tries FETCH FIRST (12c+) and falls back to a ROWNUM wrapper for older
    Oracle. The fallback applies DISTINCT and ORDER BY *inside* the wrapper:
    the other way round, ROWNUM caps raw rows before de-duplication, so a
    table with many rows on its latest date collapses to a single date.
    """
    where = (
        f" WHERE {filter_col} < TO_DATE('{format_oracle_date(before)}', 'DD-MON-YYYY')"
        if before is not None else ""
    )
    inner = f"SELECT DISTINCT {filter_col} FROM {table_name}{where} ORDER BY {filter_col} DESC"
    _, rows, err = execute_query_fn(f"{inner} FETCH FIRST {int(limit)} ROWS ONLY")
    if err:
        _, rows, err = execute_query_fn(
            f"SELECT {filter_col} FROM ({inner}) WHERE ROWNUM <= {int(limit)}"
        )
    return rows, err


def _as_date_string(value: Any) -> str:
    return format_oracle_date(value) if hasattr(value, "strftime") else str(value)


def _ancestor_named(path: str, name: str, max_depth: int = 15) -> Optional[str]:
    """The nearest ancestor directory of `path` whose basename is `name`."""
    probe = os.path.dirname(path)
    for _ in range(max_depth):
        if os.path.basename(probe) == name:
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:                   # reached filesystem root
            return None
        probe = parent
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostic helper — logs Oracle table state when 0 rows are returned
# ─────────────────────────────────────────────────────────────────────────────

def _run_table_diagnostics(
    table_name: str,
    filter_col: str,
    execute_query_fn: Callable,
) -> None:
    # Step 1: row count
    try:
        sql = f"SELECT COUNT(*) AS CNT FROM {table_name}"
        _, rows, err = execute_query_fn(sql)
        if err:
            logger.warning("[DIAG] Step1 could not count rows: %s", err)
        else:
            cnt = rows[0][0] if rows else "N/A"
            logger.info("[DIAG] Step1 — table=%s total_rows=%s", table_name, cnt)
    except Exception as exc:
        logger.warning("[DIAG] Step1 EXCEPTION: %s", exc)

    # Step 2: distinct date values
    try:
        rows, _ = _distinct_dates_desc(execute_query_fn, table_name, filter_col, 10)
        if rows:
            logger.info("[DIAG] Step2 — distinct %s values: %s",
                        filter_col, [str(r[0]) for r in rows])
        else:
            logger.info("[DIAG] Step2 — no distinct date values found")
    except Exception as exc:
        logger.warning("[DIAG] Step2 EXCEPTION: %s", exc)

    logger.info("[DIAG] diagnostics complete for %s", table_name)


# ─────────────────────────────────────────────────────────────────────────────
# Table-mapping loader
# ─────────────────────────────────────────────────────────────────────────────

# ── Table-mapping resolution cache ──────────────────────────────────────────
# _load_table_mapping() below does a lot of expensive work per call — it
# probes a dozen+ candidate paths and, if none hit, walks/lists whole
# directory trees looking for a "*mapping*.xml" file — every one of those is
# a filesystem (often network-share) round trip. Every OTHER lookup module in
# this codebase caches its equivalent expensive file resolution (index_store.
# py's FAISS indices, lexical_search.py's BM25/QA files, report_lookup.py's
# Returns.xml parse, return_lookup.py's per-table mapping build) — this one
# was the exception, and it's called at least twice per single
# /variance/nlresolve request (main.py's find_return_and_tables, then again
# inside compute_variance's _get_table_metadata), so every request re-did the
# full candidate/directory-scan dance from scratch. TTL-cached here the same
# way report_lookup.py caches Returns.xml itself, keyed by (return_id,
# tbl_path) since a given return's mapping file/location essentially never
# changes between deploys.
_TABLE_MAPPING_TTL = float(os.getenv("DV_TABLE_MAPPING_TTL_SEC", "3600"))
_mapping_cache: dict[tuple[str, str], tuple[float, Any, str]] = {}
_mapping_cache_lock = threading.Lock()


def _load_table_mapping(
    return_id: str, tbl_path: str, ctx: RequestContext = ANONYMOUS
):
    # Tenant is part of the key: return 2029 resolves to a different file
    # per tenant under 6.0, and a shared key would serve one tenant the
    # other one's mapping.
    cache_key = (ctx.tenant_id, str(return_id), tbl_path or "")
    now = time.monotonic()
    with _mapping_cache_lock:
        cached = _mapping_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < _TABLE_MAPPING_TTL:
        return cached[1], cached[2]

    # ── Startup diagnostics (verbose — only useful when actively debugging a
    # missing table-mapping file, not on every normal call) ───────────────────
    profile = get_profile()
    profile.validate_context(ctx)
    # Bound to locals once per call. The candidate/scan logic below is long
    # and references these repeatedly; resolving them here keeps one place
    # where "where does this host keep its files" is decided.
    mapping_base  = profile.table_mapping_base_dir(ctx)
    instance_base = profile.instance_base_dir(ctx)
    returns_xml   = profile.returns_xml_path(ctx)

    logger.debug("[service] _load_table_mapping called")
    logger.debug("[service]   return_id          = %r", return_id)
    logger.debug("[service]   tbl_path           = %r", tbl_path)
    logger.debug("[service]   profile            = %s", profile.name)
    logger.debug("[service]   mapping_base       = %r", mapping_base)
    logger.debug("[service]   instance_base      = %r", instance_base)
    logger.debug("[service]   returns_xml        = %r", returns_xml)

    return_dir_from_config = os.path.normpath(
        os.path.join(mapping_base, str(return_id))
    )
    logger.debug(
        "[service]   return_dir (mapping_base/return_id) = %r  isdir=%s",
        return_dir_from_config,
        os.path.isdir(return_dir_from_config),
    )

    candidates: list[str] = []

    # ── Candidate 1: absolute tbl_path as-is ─────────────────────────────────
    if tbl_path and os.path.isabs(tbl_path):
        candidates.append(tbl_path)
        logger.debug("[service] tbl_path is absolute — added as first candidate")

    # ── Candidates 2-5: tbl_path relative to known base directories ──────────
    if tbl_path:
        candidates.append(os.path.join(mapping_base, str(return_id), tbl_path))
        candidates.append(os.path.join(mapping_base, tbl_path))
        candidates.append(os.path.join(instance_base, str(return_id), tbl_path))

        if returns_xml:
            candidates.append(
                os.path.join(os.path.dirname(returns_xml), str(return_id), tbl_path)
            )

    # ── Candidates 6+: well-known fixed filenames in the return folder ────────
    # These are always tried regardless of whether TblPath is set in Returns.xml,
    # so returns with a missing/empty TblPath can still be resolved via the
    # directory scan below.
    for fixed_name in (
        "TableMapping.xml",
        "TabelMapping.xml",   # legacy typo variant kept for back-compat
        "tablemapping.xml",
        "Tablemapping.xml",
        "TABLEMAPPING.XML",
    ):
        candidates.append(
            os.path.join(mapping_base, str(return_id), fixed_name)
        )

    # ── Directory scan ────────────────────────────────────────────────────────
    # Collect every directory that might be the return's root folder.
    # We derive them from multiple sources so that spaces / trailing separators
    # in mapping_base can't silently break os.path.isdir.
    scan_dirs: list[str] = []

    # Source A: straight join of config dir + return_id
    scan_dirs.append(return_dir_from_config)

    if tbl_path:
        # Sources B and C: walk UP from the deepest tbl_path-based candidate
        # (under the mapping base, then the instance base) to the folder whose
        # basename == str(return_id). Immune to trailing/double separators.
        for base in (mapping_base, instance_base):
            deep = os.path.normpath(os.path.join(base, str(return_id), tbl_path))
            found_dir = _ancestor_named(deep, str(return_id))
            if found_dir:
                scan_dirs.append(found_dir)

    # Deduplicate scan dirs
    seen_dirs: set = set()
    unique_scan_dirs: list[str] = []
    for d in scan_dirs:
        nd = os.path.normpath(d)
        if nd not in seen_dirs:
            seen_dirs.add(nd)
            unique_scan_dirs.append(nd)

    for scan_dir in unique_scan_dirs:
        logger.debug(
            "[service] Dir-scan: checking dir=%r  isdir=%s",
            scan_dir, os.path.isdir(scan_dir),
        )
        if not os.path.isdir(scan_dir):
            continue
        try:
            found_files = os.listdir(scan_dir)
            logger.debug(
                "[service] Dir-scan: listed %d file(s) in %r", len(found_files), scan_dir
            )
            for fname in found_files:
                if "mapping" in fname.lower() and fname.lower().endswith(".xml"):
                    full = os.path.join(scan_dir, fname)
                    candidates.append(full)
                    logger.debug("[service] Dir-scan hit: %r", full)
        except OSError as exc:
            logger.warning("[service] Dir-scan OSError for dir=%r: %s", scan_dir, exc)

    # ── Deduplicate candidates (preserve insertion order) ─────────────────────
    seen_paths: set = set()
    deduped: list[str] = []
    for c in candidates:
        if not c:
            continue
        norm = os.path.normpath(c)
        if norm not in seen_paths:
            seen_paths.add(norm)
            deduped.append(norm)

    logger.debug("[service] Total unique candidates to probe: %d", len(deduped))

    # ── Probe each candidate ──────────────────────────────────────────────────
    for norm in deduped:
        exists = os.path.exists(norm)
        logger.debug("[service] Checking mapping path=%s  exists=%s", norm, exists)
        if exists:
            logger.debug("[service] Found mapping=%s", norm)
            root = load_xml_tree(norm, label=f"Table mapping for return {return_id}")
            with _mapping_cache_lock:
                _mapping_cache[cache_key] = (now, root, norm)
            return root, norm

    # ── Nothing found — fall back to the canonical path (will log an error) ───
    fallback = os.path.normpath(
        os.path.join(mapping_base, str(return_id), tbl_path or "TableMapping.xml")
    )
    # WARNING, not ERROR: a missing mapping file is an expected, handled
    # condition for a large minority of returns — backend/nlp/return_lookup.py
    # recovers their tables from XML_Query.xml and logs the real outcome
    # per return. At ERROR, one NLP cache rebuild emitted ~60 of these plus
    # ~60 duplicates from load_xml_tree below, burying genuine errors.
    logger.warning(
        "[service] Mapping file not found after checking %d candidate(s). "
        "Using fallback=%s",
        len(deduped), fallback,
    )
    # Skip the load when the fallback is a path we just probed and know is
    # absent — load_xml_tree would only log its own ERROR for it and return
    # None, which is exactly what we assign here. Still load it when the
    # fallback wasn't among the candidates, so a present-but-malformed file
    # keeps reporting itself.
    if fallback in seen_paths:
        root = None
    else:
        root = load_xml_tree(fallback, label=f"Table mapping for return {return_id}")
    with _mapping_cache_lock:
        _mapping_cache[cache_key] = (now, root, fallback)
    return root, fallback


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def find_return_and_tables(
    return_input: str, ctx: RequestContext = ANONYMOUS
) -> dict[str, Any]:
    """Find a return by name and list available tables from its mapping XML.

    Response variants:
      • Exact / unique high-confidence match  → normal result dict
      • Multiple plausible matches            → {"candidates": [...]}
      • Nothing found                         → {"error": "..."}
    """
    logger.info("[service] Finding return=%s", return_input)

    scored = search_returns_scored(return_input, ctx)
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

            # has_mapping is always True: an empty TblPath does not mean there
            # is no mapping file — _load_table_mapping()'s fixed-name
            # candidates and directory scan resolve it once the user selects.
            candidates.append({
                "score":       item["score"],
                "return_id":   rid,
                "return_name": item["return"].get("Name", ""),
                "report_freq": item["return"].get("RepFreq", ""),
                "tbl_path":    tbl_path,
                "has_mapping": True,  # resolved by _load_table_mapping on select
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

    # tbl_path may be empty — _load_table_mapping handles that via fixed-name
    # fallbacks and directory scan, so we no longer hard-stop here.
    root, resolved_path = _load_table_mapping(return_id, tbl_path, ctx)

    tables = []
    if root is not None:
        for el in root.findall("Row"):
            tables.append({
                "table_name":            el.attrib.get("TableName"),
                "filter_col":            el.attrib.get("FilterColumn"),
                "primary_column":        el.attrib.get("PrimaryColumn"),
                "comp_filter_col_name":  el.attrib.get("CompFilterColName"),
                **el.attrib,
            })

    # ── Fallback: parse the return's XML_Query.xml ───────────────────────────
    # Covers BOTH ways a return can end up with no usable table list, because
    # both produce the same symptom — an empty Table dropdown:
    #
    #   (a) the mapping file loaded but declares no TableName anywhere. When
    #       Returns.xml carries no TblPath, resolution lands on Mapping_1.xml,
    #       whose <Row> elements are CELL mappings (Cell, ClmnUnitId, Code,
    #       DataType, Dim, ...) with no TableName attribute at all.
    #   (b) no mapping file exists anywhere for the return, which used to be a
    #       hard error returned from this function before anything else was
    #       tried.
    #
    # XML_Query.xml describes a return's tables in its SELECT statements: every
    # FROM/JOIN target is a table, and RptDtClmnName gives the reporting-date
    # column. See query_xml_lookup._build.
    #
    # This is not a new mechanism — backend/nlp/return_lookup.py and
    # _get_table_metadata() below already carry exactly this fallback, and
    # _get_table_metadata explicitly handles root-is-None the same way. This
    # function was the one place that gave up first. Measured on the current
    # deployment: 36 returns load a mapping that declares no TableName, and
    # this recovers the real tables for 20 of them (2007 CIMS_NRD-CSR -> 3,
    # 2044/2064 CIMS_RCA3 -> 29 each, 2035/2058 CIMS_ALE monthly -> 25/22).
    # Case (b) currently recovers nothing, because the 239 returns with no
    # mapping file have no XML_Query.xml either — it is handled for symmetry,
    # so a deployment that ships one without the other works.
    if not any(t.get("table_name") for t in tables):
        query_label = query_xml_lookup.query_xml_label(ctx)
        recovered = query_xml_lookup.tables_for_return(return_id, ctx)
        if recovered:
            tables = [
                {
                    "table_name":           name,
                    "filter_col":           meta.get("filter_col"),
                    "primary_column":       None,
                    "comp_filter_col_name": None,
                    "source":               query_label,
                }
                for name, meta in recovered.items()
            ]
            logger.info(
                "[service] return_id=%s (%s) — %s; recovered %d table(s) from %s",
                return_id, r.get("Name"),
                "no table-mapping file" if root is None
                else f"mapping file {resolved_path} declares no TableName attributes",
                len(tables), query_label,
            )
        elif root is None:
            # Nothing anywhere describes this return's tables. Only NOW is it
            # an error — and the message names both files that were tried, so
            # whoever fixes the config knows where to put the data.
            profile = get_profile()
            return {
                "error": (
                    f"Return '{r.get('Name', return_input)}' (Id={return_id}): "
                    f"no table mapping file and no {query_label}. "
                    f"Checked TblPath={tbl_path!r} and standard fallback locations "
                    f"under {profile.table_mapping_base_dir(ctx)}, plus "
                    f"{list(profile.query_xml_candidates(ctx, return_id))}."
                )
            }
        else:
            logger.warning(
                "[service] return_id=%s (%s) — mapping file %s declares no TableName "
                "attributes and %s has no fallback either. The table dropdown for "
                "this return will be empty.",
                return_id, r.get("Name"), resolved_path,
                query_label,
            )

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
    ctx: RequestContext = ANONYMOUS,
) -> dict[str, Any]:
    root, _ = _load_table_mapping(return_id, tbl_path, ctx)
    tname_up = table_name.strip().upper()

    if root is not None:
        for el in root.findall("Row"):
            xml_name = (el.attrib.get("TableName") or "").strip().upper()
            if xml_name == tname_up:
                comp       = el.attrib.get("CompFilterColName", "")
                comp_cols  = [c.strip().upper() for c in comp.split("|") if c.strip()]
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

    # ── Fallback: XML_Query.xml ───────────────────────────────────────────────
    # Reached when the mapping file is missing entirely (root is None) OR it
    # loaded but doesn't define this table — the latter is common because a
    # number of returns ship mapping rows with empty TableName attributes
    # (e.g. the CIMS_ALE returns). Those returns still describe their tables
    # in XML_Query.xml's SELECT statements, which is where RptDtClmnName gives
    # us the filter_col this function exists to provide. Without this, every
    # such table 404'd out of /variance/dates and compute_variance.
    fallback = query_xml_lookup.get_table_metadata(return_id, table_name, ctx)
    if fallback is not None:
        logger.info(
            "[service] Table %r resolved via %s fallback (return_id=%s, filter_col=%s)",
            table_name, query_xml_lookup.query_xml_label(ctx), return_id, fallback["filter_col"],
        )
        return fallback

    if root is None:
        raise FileNotFoundError(
            f"Table mapping not found for return {return_id}, and no "
            f"{query_xml_lookup.query_xml_label(ctx)} entry for table '{table_name}'."
        )

    available = [el.attrib.get("TableName", "") for el in root.findall("Row")]
    raise KeyError(
        f"Table '{table_name}' not found in mapping. Available: {available}"
    )


def resolve_physical_table_name(
    return_id: str,
    table_name: str,
    return_meta: Optional[dict[str, Any]] = None,
    ctx: RequestContext = ANONYMOUS,
) -> str:
    """is_excel -> optional '_DP' suffix -> optional DP_TABLE_SCHEMA prefix.

    `return_meta` lets a caller that already parsed Returns.xml (e.g.
    compute_variance) skip a redundant get_is_excel_by_return_code() XML
    scan; pass None to have it looked up here instead."""
    is_excel = (
        str(return_meta.get("IsExcel", "false")).strip().lower() == "true"
        if return_meta
        else get_is_excel_by_return_code(return_id, ctx=ctx)
    )
    if IS_SP_TABLE_DATA_ENABLED and not is_excel:
        dp_name = f"{table_name}_DP"
        return f"{DP_TABLE_SCHEMA}.{dp_name}" if DP_TABLE_SCHEMA else dp_name
    return table_name


def _return_meta(return_id: str, ctx: RequestContext) -> Optional[dict[str, Any]]:
    """The returns-master row for `return_id`, or None."""
    return next((r for r in parse_returns(ctx) if r.get("Id") == str(return_id)), None)


def _trusted_tbl_path(return_meta: Optional[dict[str, Any]]) -> str:
    """TblPath exactly as the returns master declares it.

    The mapping file for a compute/dates request is always located from this,
    never from a path sent by the client. /variance/find echoes the resolved
    `table_mapping_path` to the browser and the browser sends it back, but
    _load_table_mapping() treats an absolute tbl_path as its first candidate —
    so honouring the echoed value let a caller point the server at any XML
    file it can reach, including another return's mapping (reading a table the
    caller is not entitled to under a return id they are) or a UNC path. The
    return's own TblPath resolves to the same file /variance/find reported.
    """
    return (return_meta.get("TblPath") or "").strip() if return_meta else ""


def _actual_previous_dates(
    resolved_table_name: str,
    filter_col: str,
    reporting_date: str,
    periods: int,
    execute_query_fn: Callable,
) -> list[str]:
    """The `periods` reporting dates immediately before `reporting_date`.

    Reads the dates that exist in the table instead of deriving them from
    RepFreq, so "previous period" means "the previous submission" — which
    is what the user selecting a period count actually means, and what the
    date dropdown already shows them.

    Returns [] when the table cannot be queried or has no earlier date, so
    the caller falls back to the original frequency arithmetic rather than
    failing.
    """
    try:
        anchor = datetime.strptime(reporting_date.strip().upper(), "%d-%b-%Y")
    except (ValueError, AttributeError):
        return []

    rows, err = _distinct_dates_desc(
        execute_query_fn, resolved_table_name, filter_col,
        max(int(periods), 1), before=anchor,
    )
    if err:
        logger.warning(
            "[service] Could not read previous reporting dates for %s (%s) — "
            "falling back to frequency arithmetic",
            resolved_table_name, err,
        )
        return []

    return [_as_date_string(r[0]) for r in rows or [] if r[0] is not None]


# Ceiling on the date dropdown — decades of monthly filings.
_MAX_AVAILABLE_DATES = 500


def get_available_dates(
    return_id: str,
    table_name: str,
    execute_query_fn: Callable,
    ctx: RequestContext = ANONYMOUS,
) -> list[str]:
    """List every distinct value of the table's filter (date) column that
    actually has data AND is a canonical period-end for the return's OWN
    reporting frequency, newest first.

    Why the frequency filter matters: the physical table a return maps to is
    often SHARED across several returns/variants that file the same table on
    different schedules — e.g. CIMS_RAQ_Q_GEN_INFO is filed into by both
    CIMS_RAQ(Quarterly) (RepFreq=Q) and CIMS_RAQ(Annually) (RepFreq=A). A plain
    DISTINCT over the column mixes their submission dates, so selecting the
    Annually return's own table used to offer 28-FEB-2025 and 31-JAN-2025 in
    its date dropdown — dates that return can never legitimately report on.
    Restricting to dates validate_reporting_date() accepts for THIS return's
    RepFreq removes that cross-return noise, so a return reporting Quarterly
    shows at most 4 dates/year, Annually shows only 31-Mar, Monthly shows up
    to 12/year, matching how the return actually files.

    This is also what makes the "Invalid Reporting Date According To
    Frequency" rejection effectively unreachable through ordinary UI use: the
    dropdown/checkbox list this feeds (ControlBar's DateField) can no longer
    offer a date validate_reporting_date() would reject. The check itself
    stays in calculate_variance as defense-in-depth for direct API calls; this
    function's job is to keep the ordinary path from ever hitting it.

    A return with no declared RepFreq (68 of them in this deployment) skips
    the filter entirely and always sees the unfiltered list — there is no
    frequency to validate against. For a return that DOES declare one, if
    filtering would remove every date (its real submissions genuinely never
    land on a canonical one), the unfiltered list is used instead — so an
    unusual return degrades to today's exact behaviour rather than leaving the
    wizard with an empty, dead-ended dropdown.

    Raises FileNotFoundError/KeyError exactly like compute_variance does when
    the table mapping or table itself can't be resolved. Returns [] (not an
    error) when the table resolves fine but genuinely has no rows yet.
    """
    return_meta = _return_meta(return_id, ctx)
    table_meta = _get_table_metadata(return_id, _trusted_tbl_path(return_meta), table_name, ctx)
    filter_col = table_meta["filter_col"]
    resolved_table_name = resolve_physical_table_name(
        return_id, table_name, return_meta=return_meta, ctx=ctx
    )

    rows, err = _distinct_dates_desc(
        execute_query_fn, resolved_table_name, filter_col, _MAX_AVAILABLE_DATES,
    )
    if err:
        raise RuntimeError(f"{err} | table_queried={resolved_table_name}")

    values = [row[0] for row in rows if row[0] is not None]

    # Same lookup compute_variance() itself uses to decide report_freq for this
    # return — one source of truth, not a second copy of the RepFreq census.
    report_freq = ((return_meta.get("RepFreq") or "").strip().upper() if return_meta else "")

    if report_freq:
        canonical = [v for v in values if validate_reporting_date(v, report_freq)]
        if canonical:
            dropped = len(values) - len(canonical)
            if dropped:
                logger.info(
                    "[service] get_available_dates | return_id=%s (freq=%s) | table=%s | "
                    "dropped %d non-canonical date(s) not valid for this frequency",
                    return_id, report_freq, table_name, dropped,
                )
            values = canonical
        else:
            logger.warning(
                "[service] get_available_dates | return_id=%s (freq=%s) | table=%s | "
                "NONE of its %d submission date(s) are canonical for this frequency — "
                "showing the unfiltered list rather than an empty dropdown",
                return_id, report_freq, table_name, len(values),
            )

    return [_as_date_string(v) for v in values]


def compute_variance(
    return_id: str,
    table_name: str,
    reporting_date: str,
    reporting_period: int,
    execute_query_fn: Callable,
    connection_string: Optional[str] = None,
    selected_columns: Optional[list[str]] = None,
    comparison_mode: str = "vs_current",
    comparison_dates: Optional[list[str]] = None,
    ctx: RequestContext = ANONYMOUS,
) -> dict[str, Any]:
    """Orchestrate full variance computation for one table.

    `comparison_dates` (optional) names the exact reporting dates to compare
    instead of deriving them from `reporting_period` — see
    calculate_variance()'s docstring. Passed straight through; this layer makes
    no decisions about it."""
    logger.info("[service] compute_variance started")

    return_meta = _return_meta(return_id, ctx)
    report_freq = (
        (return_meta.get("RepFreq") or "").strip().upper()
        if return_meta
        else ""
    ) or "M"

    logger.info(
        "[service] return_id=%s | freq=%s | table=%s | date=%s | periods=%s",
        return_id, report_freq, table_name, reporting_date, reporting_period,
    )

    # ctx passed even though return_meta is always set here (so the
    # ctx-dependent is_excel lookup is not reached today) — leaving it off
    # makes correctness depend on a caller invariant that nothing enforces.
    resolved_table_name = resolve_physical_table_name(
        return_id, table_name, return_meta=return_meta, ctx=ctx
    )

    logger.debug("[table_resolution] ReturnCode=%s", return_id)
    logger.debug("[table_resolution] IsSpTableDataEnabled=%s", IS_SP_TABLE_DATA_ENABLED)
    logger.debug("[table_resolution] ReturnFoundInXml=%s", return_meta is not None)
    logger.debug("[table_resolution] OriginalTable=%s", table_name)
    logger.debug("[table_resolution] FinalReportName=%s", resolved_table_name)

    table_meta = _get_table_metadata(return_id, _trusted_tbl_path(return_meta), table_name, ctx)

    metadata = {
        "filter_col":            table_meta["filter_col"],
        "comp_filter_col_names": table_meta["comp_filter_col_names"],
        "report_freq":           report_freq,
        "is_single":             table_meta.get("is_single", False),
        "return_code_col":       table_meta.get("return_code_col"),
        "freq_col":              table_meta.get("freq_col"),
        "freq_val":              table_meta.get("freq_val"),
    }

    def get_table_metadata_fn(_return_code, _table_name, _is_non_xbrl):
        return metadata

    def execute_query_adapter(query, _conn_str=None):
        logger.debug("[service] Executing Oracle query:\n%s", query)
        cols, rows, err = execute_query_fn(query)

        if err:
            logger.error(
                "[service] QUERY FAILED\n"
                "  return_id        = %s\n"
                "  original_table   = %s\n"
                "  resolved_table   = %s\n"
                "  reporting_date   = %s\n"
                "  reporting_period = %s\n"
                "  sp_table_enabled = %s\n"
                "  error            = %s",
                return_id, table_name, resolved_table_name,
                reporting_date, reporting_period,
                IS_SP_TABLE_DATA_ENABLED, err,
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

    # No explicit dates from the caller -> resolve the period count against
    # the table's real reporting dates. Without this, calculate_variance
    # derives them from RepFreq, which is daily for the QCB returns even
    # though they file monthly, so every comparison lands on an empty date.
    effective_comparison_dates = comparison_dates
    if not effective_comparison_dates:
        previous = _actual_previous_dates(
            resolved_table_name, metadata["filter_col"], reporting_date,
            reporting_period, execute_query_fn,
        )
        if previous:
            effective_comparison_dates = [reporting_date] + previous
            logger.info(
                "[service] previous period(s) resolved from actual data: %s vs %s "
                "(RepFreq=%s arithmetic bypassed)",
                reporting_date, previous, report_freq,
            )
        else:
            logger.info(
                "[service] no earlier reporting date found in %s — using "
                "RepFreq=%s arithmetic", resolved_table_name, report_freq,
            )

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
        comparison_dates=effective_comparison_dates,
    )