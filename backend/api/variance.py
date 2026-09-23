# variance.py — the manual variance path: /variance/find, /compute, /dates
#
# Split out of backend/main.py, which had grown to ~1400 lines holding every
# route. Route bodies are unchanged; only the decorator and the imports moved.
# backend/main.py mounts this router, so the URLs are identical.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.deps import require_login, require_return_access
from ..config import RequestContext
from ..data import service
from ..data.db import execute_query
from ..data.models import VarianceComputeRequest
from ..hosts import HostProfileError
from .errors import http_error

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /variance/find ─────────────────────────────────────────────────────────
@router.get("/variance/find", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_find(
    return_name: str,
    ctx: RequestContext = Depends(require_login),     # ← validates loginId param & user exists
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
    logger.info("[main] GET /variance/find | %s | return_name=%r", ctx, return_name)

    result = service.find_return_and_tables(return_name, ctx)
    if result.get("error"):
        logger.warning("[main] 404 /variance/find | return_name=%r | %s", return_name, result["error"])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )

    # ── Optional: filter search results to user's allowed returns only ─────────
    # Uncomment if you want the search list itself to be access-controlled.
    #
    # from .auth.service import get_allowed_form_ids
    # allowed = get_allowed_form_ids(ctx) or set()
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
# The manual UI offers 1-3 comparison periods (ControlBar's Periods chips), and
# the result table renders one column group per period — a longer list would
# push the table past what fits on screen. Explicitly named comparison dates
# are held to the same ceiling, and it is enforced HERE rather than only in the
# UI: the endpoint is reachable directly, and each extra date adds a full
# period's worth of rows to the query and the response.
MAX_COMPARISON_DATES = 3


@router.post("/variance/compute", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_compute(
    payload: VarianceComputeRequest,
    ctx: RequestContext = Depends(require_login),     # ← step 1: user must exist in XML_User.xml
) -> dict:
    """Compute variance for the given return / table / date / periods.

    Auth flow:
      1. require_login  — confirms loginId param is present and user exists
      2. require_return_access — confirms user's dept has this return in Forms/NXForms
    """
    logger.info(
        "[main] POST /variance/compute | %s | return_id=%s | table=%s | date=%s | "
        "periods=%s | comparison_dates=%s",
        ctx, payload.return_id, payload.table_name,
        payload.reporting_date, payload.reporting_period, payload.comparison_dates,
    )

    # ── Step 2: check this specific return is in the user's allowed set ────────
    require_return_access(ctx, payload.return_id)

    comparison_dates = [d.strip().upper() for d in (payload.comparison_dates or []) if d and d.strip()]
    if comparison_dates:
        # Deduped BEFORE the cap so picking the same date twice can't consume a
        # slot (calculate_variance dedupes too, but rejecting 4 dates that are
        # really 3 would be wrong).
        unique_dates = list(dict.fromkeys(comparison_dates))
        if len(unique_dates) > MAX_COMPARISON_DATES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"At most {MAX_COMPARISON_DATES} comparison dates may be selected "
                    f"(got {len(unique_dates)})."
                ),
            )
        for value in unique_dates:
            try:
                datetime.strptime(value, "%d-%b-%Y")
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid comparison date {value!r} — expected DD-MON-YYYY.",
                ) from exc
        comparison_dates = unique_dates

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
            comparison_mode=payload.comparison_mode,
            comparison_dates=comparison_dates or None,
            ctx=ctx,
        )
        logger.info(
            "[main] compute_variance SUCCESS | %s | return_id=%s | table=%s",
            ctx, payload.return_id, payload.table_name,
        )
    except Exception as exc:
        raise http_error(exc, "/variance/compute", ctx) from exc

    return res


# ── GET /variance/dates ─────────────────────────────────────────────────────────
@router.get("/variance/dates", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_dates(
    return_id: str,
    table_mapping_path: str,
    table_name: str,
    ctx: RequestContext = Depends(require_login),
) -> dict:
    """List every reporting date that actually has data for this return/table,
    newest first — lets the manual UI offer a dropdown of real submission
    dates instead of a free calendar picker.
    """
    logger.info(
        "[main] GET /variance/dates | %s | return_id=%s | table=%s",
        ctx, return_id, table_name,
    )

    require_return_access(ctx, return_id)

    try:
        dates = service.get_available_dates(
            return_id=return_id,
            return_tbl_path=table_mapping_path,
            table_name=table_name,
            execute_query_fn=execute_query,
            ctx=ctx,
        )
    except Exception as exc:
        raise http_error(exc, "/variance/dates", ctx) from exc

    return {"dates": dates}
