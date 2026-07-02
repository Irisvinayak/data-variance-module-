# main.py — Standalone FastAPI application for the Data Variance feature.
# Run with:  uvicorn backend.main:app --port 8002 --reload
#   or:      python -m uvicorn backend.main:app --port 8002 --reload

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .config import API_BASE_PATH, SERVER_HOST, SERVER_PORT, CORS_ORIGINS
from .models import VarianceComputeRequest
from . import service
from .db import execute_query
from .auth_deps import require_login, require_return_access

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
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
    result = service.find_return_and_tables(return_name)
    if result.get("error"):
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
    # ── Step 2: check this specific return is in the user's allowed set ────────
    require_return_access(login_id, payload.return_id)

    logger.info(
        "[main] POST /variance/compute | login_id=%s | return_id=%s | table=%s | date=%s | periods=%s",
        login_id, payload.return_id, payload.table_name,
        payload.reporting_date, payload.reporting_period,
    )

    try:
        res = service.compute_variance(
            return_id=payload.return_id,
            return_tbl_path=payload.table_mapping_path,
            table_name=payload.table_name,
            reporting_date=payload.reporting_date,
            reporting_period=payload.reporting_period,
            execute_query_fn=execute_query,
            connection_string=None,
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