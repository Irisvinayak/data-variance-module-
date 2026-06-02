# main.py — Standalone FastAPI application for the Data Variance feature.
# Run with:  uvicorn backend.main:app --port 8002 --reload
#   or:      python -m uvicorn backend.main:app --port 8002 --reload

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .config import SERVER_HOST, SERVER_PORT, CORS_ORIGINS
from .models import VarianceComputeRequest
from . import service
from .db import execute_query

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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["Meta"])
async def health():
    return {"status": "ok"}


# ── GET /variance/find ─────────────────────────────────────────────────────────
@app.get("/variance/find", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_find(return_name: str) -> dict:
    """Find a return by name and list available tables from the table-mapping XML."""
    result = service.find_return_and_tables(return_name)
    if result.get("error"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )
    return result


# ── POST /variance/compute ─────────────────────────────────────────────────────
@app.post("/variance/compute", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_compute(payload: VarianceComputeRequest) -> dict:
    """Compute variance for the given return / table / date / periods."""
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
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return res


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
