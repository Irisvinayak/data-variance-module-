# main.py — Standalone FastAPI application for the Data Variance feature.
# Run with:  uvicorn backend.main:app --port 8002 --reload
#   or:      python -m uvicorn backend.main:app --port 8002 --reload

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .config import API_BASE_PATH, SERVER_HOST, SERVER_PORT, CORS_ORIGINS
from .logging_config import configure_logging
from .models import NLResolveRequest, VarianceComputeRequest
from . import service
from .db import execute_query
from .auth_deps import require_login, require_return_access
from .report_lookup import _parse_returns

# ── Logging ────────────────────────────────────────────────────────────────────
# One line per meaningful boundary (API request, LLM call, auth decision) at
# INFO; per-row/per-candidate-path internals are DEBUG throughout the
# codebase. Writes to console + logs/<date>.log — see logging_config.py.
configure_logging()
logger = logging.getLogger(__name__)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Data Variance API",
    version="1.0.0",
    description="Standalone Data Variance analysis — no chatbot dependency.",
    root_path=API_BASE_PATH or "",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check (no auth — used by infra/monitoring probes) ──────────────────
@app.get("/health", tags=["Meta"])
async def health():
    return {"status": "ok"}


# ── GET /variance/find ─────────────────────────────────────────────────────────
@app.get("/variance/find", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_find(
    return_name: str,
    login_id: str = Depends(require_login),     # ← validates loginId param & user exists
) -> dict:
    """Find a return by name.

    Requires ?loginId= query param. User must exist in XML_User.xml.

    Returns one of:
      • Normal result  — return_id, tables, etc.  (exact / unique match)
      • Candidates     — {candidates: [...]}       (multiple matches)
      • 404            — {detail: "..."}           (nothing found)

    Note: search results are NOT filtered by the user's allowed forms here.
    The access check happens at /variance/compute time, giving a clear 403.
    If you want to hide inaccessible returns from search results, see the
    commented-out block below.
    """
    logger.info("[main] GET /variance/find | login_id=%s | return_name=%r", login_id, return_name)

    result = service.find_return_and_tables(return_name)
    if result.get("error"):
        logger.warning("[main] 404 /variance/find | return_name=%r | %s", return_name, result["error"])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )

    # ── Optional: filter search results to user's allowed returns only ─────────
    # Uncomment if you want the search list itself to be access-controlled.
    #
    # from .auth_service import get_allowed_form_ids
    # allowed = get_allowed_form_ids(login_id) or set()
    # if "candidates" in result:
    #     result["candidates"] = [
    #         c for c in result["candidates"]
    #         if str(c.get("return_id", "")) in allowed
    #     ]
    #     if not result["candidates"]:
    #         raise HTTPException(
    #             status_code=status.HTTP_403_FORBIDDEN,
    #             detail=f"No accessible returns found matching '{return_name}'.",
    #         )
    # elif result.get("return_id") and str(result["return_id"]) not in allowed:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail=f"You do not have access to return '{result.get('return_name')}'.",
    #     )

    return result


# ── POST /variance/compute ─────────────────────────────────────────────────────
@app.post("/variance/compute", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_compute(
    payload: VarianceComputeRequest,
    login_id: str = Depends(require_login),     # ← step 1: user must exist in XML_User.xml
) -> dict:
    """Compute variance for the given return / table / date / periods.

    Auth flow:
      1. require_login  — confirms loginId param is present and user exists
      2. require_return_access — confirms user's dept has this return in Forms/NXForms
    """
    logger.info(
        "[main] POST /variance/compute | login_id=%s | return_id=%s | table=%s | date=%s | periods=%s",
        login_id, payload.return_id, payload.table_name,
        payload.reporting_date, payload.reporting_period,
    )

    # ── Step 2: check this specific return is in the user's allowed set ────────
    require_return_access(login_id, payload.return_id)

    try:
        res = service.compute_variance(
            return_id=payload.return_id,
            return_tbl_path=payload.table_mapping_path,
            table_name=payload.table_name,
            reporting_date=payload.reporting_date,
            reporting_period=payload.reporting_period,
            execute_query_fn=execute_query,
            connection_string=None,
            selected_columns=payload.selected_columns,
        )
        logger.info(
            "[main] compute_variance SUCCESS | login_id=%s | return_id=%s | table=%s",
            login_id, payload.return_id, payload.table_name,
        )
    except FileNotFoundError as exc:
        logger.error("[main] 404 FileNotFoundError | %s", exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KeyError as exc:
        logger.error("[main] 404 KeyError | %s", exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("[main] 500 RuntimeError | %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[main] 500 Unhandled exception | return_id=%s", payload.return_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
        ) from exc

    return res


def _restrict_result_columns(computed: dict, requested_columns: list) -> dict:
    """Restrict a compute_variance() result to just the column(s) the NL query
    asked about. compute_variance() itself always computes every numeric
    column — selected_columns is intentionally never passed to it (see the
    comment at the call site) — so this trims the RESULT after the fact.
    Falls back to returning `computed` unchanged if none of the requested
    columns were actually among the computed ones (safer to show everything
    than to silently show nothing)."""
    if not requested_columns:
        return computed

    requested_upper = {c.upper() for c in requested_columns}
    available = computed.get("columns") or []
    keep = [c for c in available if c.upper() in requested_upper]
    if not keep:
        return computed

    keep_upper = {c.upper() for c in keep}
    available_upper = {c.upper() for c in available}

    filtered_rows = []
    for row in computed.get("rows", []):
        new_previous = {
            period_key: {c: m for c, m in metrics.items() if c.upper() in keep_upper}
            for period_key, metrics in row.get("previous", {}).items()
        }
        filtered_rows.append({**row, "previous": new_previous})

    # Keep every non-numeric/label column (e.g. PERIOD_DELINQUENCY — it was
    # never in `available` since that list is numeric-only) plus the
    # requested numeric ones; drop the unrequested numeric ones.
    display_columns = [
        c for c in computed.get("display_columns", [])
        if c.upper() in keep_upper or c.upper() not in available_upper
    ]

    return {**computed, "columns": keep, "display_columns": display_columns, "rows": filtered_rows}


# ── POST /variance/nlresolve ───────────────────────────────────────────────────
@app.post("/variance/nlresolve", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_nlresolve(
    payload: NLResolveRequest,
    login_id: str = Depends(require_login),
) -> dict:
    """Resolve a natural-language query AND compute the result in one shot.

    Embedding-based retrieval (backend/nlp/retriever.py) shortlists candidate
    tables/columns and filters them to what login_id's department is already
    allowed to access, then an LLM (backend/nlp/intent_resolver.py) picks the
    best match from that authorized shortlist only. backend/nlp/date_resolver.py
    then turns any date/period intent in the query (or its absence, defaulting
    to the latest actual submission) into a concrete reporting_date/period, and
    this route calls the existing, unmodified service.compute_variance() with
    it — the same call /variance/compute makes — so the NLP bar shows a real
    computed result immediately, with no extra manual date entry/click.
    """
    from .nlp.retriever import get_relevant_schema
    from .nlp.intent_resolver import resolve_intent
    from .nlp.date_resolver import resolve_reporting_date

    query = payload.query.strip()
    logger.info("[main] POST /variance/nlresolve | login_id=%s | query=%r", login_id, query)

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    shortlist = get_relevant_schema(query, login_id)
    if not shortlist["tables"]:
        logger.warning("[main] 404 /variance/nlresolve | login_id=%s | query=%r | no authorized table matched", login_id, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No accessible return/table matches this query.",
        )

    resolution = resolve_intent(query, shortlist)
    if resolution is None:
        logger.warning("[main] 404 /variance/nlresolve | login_id=%s | query=%r | intent resolution failed", login_id, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Could not resolve this query to a known table/column.",
        )

    return_row = next(
        (r for r in _parse_returns() if r.get("Id") == resolution["return_id"]), None
    )
    if return_row is None:
        logger.warning("[main] 404 /variance/nlresolve | login_id=%s | resolved return_id=%s not found", login_id, resolution["return_id"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resolved return not found.")

    found = service.find_return_and_tables(return_row.get("Name", ""))
    if found.get("error") or found.get("candidates") or not found.get("table_mapping_path"):
        logger.warning("[main] 404 /variance/nlresolve | login_id=%s | return_name=%r | table mapping unresolved", login_id, return_row.get("Name"))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resolved return could not be mapped to a table-mapping file.",
        )

    resolved_table = next(
        (t for t in found.get("tables", []) if (t.get("table_name") or "").upper() == resolution["table_name"].upper()),
        None,
    )
    if resolved_table is None:
        logger.warning(
            "[main] 404 /variance/nlresolve | login_id=%s | table=%r no longer in return's mapping",
            login_id, resolution["table_name"],
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resolved table is no longer present in this return's table mapping.",
        )

    # filter_col/report_freq were already resolved live during retrieval
    # (backend/nlp/return_lookup.py) — reuse them rather than re-looking up.
    shortlist_table_meta = next(
        (t for t in shortlist["tables"] if t["table"].upper() == resolution["table_name"].upper()), {}
    )
    filter_col = shortlist_table_meta.get("filter_col") or "RDATE"
    report_freq = shortlist_table_meta.get("report_freq") or found.get("report_freq") or "M"

    try:
        reporting_date, reporting_period = resolve_reporting_date(
            query, found["return_id"], resolved_table["table_name"], filter_col, report_freq,
        )
    except ValueError as exc:
        logger.warning("[main] 404 /variance/nlresolve | login_id=%s | date resolution failed: %s", login_id, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    logger.info(
        "[main] POST /variance/nlresolve | login_id=%s | query=%r | return_id=%s | table=%s | "
        "columns=%s | reporting_date=%s | reporting_period=%s",
        login_id, query, found["return_id"], resolved_table["table_name"],
        resolution["selected_columns"], reporting_date, reporting_period,
    )

    try:
        # selected_columns is deliberately NOT passed here — that parameter
        # also drives compute_variance's SQL SELECT list, so restricting it
        # to just the asked-for column would silently drop the filter_col/
        # identifier columns row-matching needs (confirmed: it causes a
        # false "no data found" once the SELECT no longer includes RDATE).
        # Instead we let it compute every numeric column exactly like the
        # manual wizard does, then restrict the RESULT to the asked-for
        # column(s) below via _restrict_result_columns.
        computed = service.compute_variance(
            return_id=found["return_id"],
            return_tbl_path=found["table_mapping_path"],
            table_name=resolved_table["table_name"],
            reporting_date=reporting_date,
            reporting_period=reporting_period,
            execute_query_fn=execute_query,
            connection_string=None,
            selected_columns=None,
        )
    except FileNotFoundError as exc:
        logger.error("[main] 404 /variance/nlresolve | login_id=%s | %s", login_id, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KeyError as exc:
        logger.error("[main] 404 /variance/nlresolve | login_id=%s | %s", login_id, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("[main] 500 /variance/nlresolve | login_id=%s | %s", login_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[main] 500 /variance/nlresolve | login_id=%s | unhandled exception", login_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
        ) from exc

    if computed.get("error"):
        logger.warning("[main] 500 /variance/nlresolve | login_id=%s | compute_variance error: %s", login_id, computed["error"])
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=computed["error"])

    computed = _restrict_result_columns(computed, resolution["selected_columns"])

    logger.info(
        "[main] 200 /variance/nlresolve | login_id=%s | return_id=%s | table=%s | rows=%d",
        login_id, found["return_id"], resolved_table["table_name"], len(computed.get("rows", [])),
    )

    return {
        **computed,
        "return_id":          found["return_id"],
        "return_name":        found["return_name"],
        "report_freq":        report_freq,
        "table_mapping_path": found["table_mapping_path"],
    }


# ── POST /variance/nlquery ─────────────────────────────────────────────────────
@app.post("/variance/nlquery", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_nlquery(
    payload: NLResolveRequest,
    login_id: str = Depends(require_login),
) -> dict:
    """Free-form NL -> SQL -> execution, mirroring sql_agent's /api/query.

    Unlike /variance/nlresolve (which only ever lets the LLM pick names from
    an authorized shortlist, then reuses the existing compute_variance to
    build the SQL), this endpoint lets the LLM write the actual SQL text via
    backend/nlp/sql_generator.py — no period-comparison, no visualization,
    just raw query results. The two safety nets that make this narrower than
    sql_agent's own version: the shortlist is pre-filtered to login_id's
    authorized returns before the LLM ever sees a table name, and
    validate_sql() rejects anything outside that shortlist, any non-SELECT,
    and any DML/DDL keyword before backend/db.execute_query ever runs it.
    """
    from .nlp.retriever import get_relevant_schema
    from .nlp.sql_generator import generate_sql

    query = payload.query.strip()
    logger.info("[main] POST /variance/nlquery | login_id=%s | query=%r", login_id, query)

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    shortlist = get_relevant_schema(query, login_id)
    if not shortlist["tables"]:
        logger.warning("[main] 404 /variance/nlquery | login_id=%s | query=%r | no authorized table matched", login_id, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No accessible return/table matches this query.",
        )

    result = generate_sql(
        query, shortlist["tables"], shortlist["columns"],
        matched_labels=shortlist.get("matched_labels"),
    )
    if not result.get("sql") or result.get("warnings"):
        logger.warning(
            "[main] 422 /variance/nlquery | login_id=%s | query=%r | warnings=%s",
            login_id, query, result.get("warnings"),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not generate a valid SQL query: {result.get('warnings')}",
        )

    logger.info(
        "[main] /variance/nlquery generated SQL | login_id=%s | query=%r | sql=%s",
        login_id, query, result["sql"],
    )

    columns, rows, err = execute_query(result["sql"])
    if err:
        logger.error("[main] 500 /variance/nlquery | login_id=%s | sql=%s | %s", login_id, result["sql"], err)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err)

    logger.info("[main] 200 /variance/nlquery | login_id=%s | rows=%d", login_id, len(rows))
    return {"sql": result["sql"], "columns": columns, "rows": rows}


# ── GET /auth/my-returns ───────────────────────────────────────────────────────
@app.get("/auth/my-returns", status_code=status.HTTP_200_OK, tags=["Auth"])
async def my_returns(login_id: str = Depends(require_login)) -> dict:
    """Return the list of return IDs the current user is allowed to access.

    Useful for debugging access issues.
    Remove or restrict to internal IPs in production.

    Example: GET /auth/my-returns?loginId=iris810
    """
    from .auth_service import get_allowed_form_ids
    allowed = get_allowed_form_ids(login_id) or set()
    return {
        "login_id":      login_id,
        "allowed_count": len(allowed),
        "allowed_forms": sorted(allowed),
    }


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)