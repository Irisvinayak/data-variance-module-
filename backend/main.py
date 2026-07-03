from __future__ import annotations
import logging
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from .config import API_BASE_PATH, SERVER_HOST, SERVER_PORT, CORS_ORIGINS, AUTH_ENABLED
from .models import VarianceComputeRequest
from . import service
from .db import execute_query
from .auth_deps import require_login, require_return_access

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
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

@app.get("/health", tags=["Meta"])
async def health():
    return {"status": "ok", "auth_enabled": AUTH_ENABLED}


@app.get("/variance/find", status_code=200, tags=["Variance"])
async def variance_find(
    return_name: str,
    credentials: tuple = Depends(require_login),
) -> dict:
    login_id, tenant_id = credentials
    result = service.find_return_and_tables(return_name, tenant_id=tenant_id)
    if result.get("error"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    return result


@app.post("/variance/compute", status_code=200, tags=["Variance"])
async def variance_compute(
    payload: VarianceComputeRequest,
    credentials: tuple = Depends(require_login),
) -> dict:
    login_id, tenant_id = credentials
    require_return_access(login_id, tenant_id, payload.return_id)

    logger.info(
        "[main] POST /variance/compute | tenant_id=%s | login_id=%s | return_id=%s | table=%s",
        tenant_id, login_id, payload.return_id, payload.table_name,
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
            tenant_id=tenant_id,
            comparison_mode=payload.comparison_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {type(exc).__name__}: {exc}") from exc
    return res


@app.get("/auth/my-returns", status_code=200, tags=["Auth"])
async def my_returns(credentials: tuple = Depends(require_login)) -> dict:
    login_id, tenant_id = credentials
    from .auth_service import get_allowed_form_ids
    allowed = get_allowed_form_ids(login_id, tenant_id) or set()
    return {
        "tenant_id":     tenant_id,
        "login_id":      login_id,
        "allowed_count": len(allowed),
        "allowed_forms": sorted(allowed),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)