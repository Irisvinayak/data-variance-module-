# main.py — the Data Variance FastAPI application.
#
# This file owns the app object, the CORS and request-correlation
# middleware, and the app-level exception handlers. The routes themselves
# live in backend/api/ — see that package's docstring for why.
#
# Run with:  python dev_server.py        (port follows VERSION in .env)
#      or:   uvicorn backend.main:app

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import ROUTERS
from .config import API_BASE_PATH, CORS_ORIGINS, SERVER_HOST, SERVER_PORT
from .hosts import HostProfileError
from .logging_config import (
    configure_logging,
    login_id_var,
    new_request_id,
    request_id_var,
    tenant_id_var,
)

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
    # With a "*" origin Starlette echoes back whatever Origin the browser
    # sent, so credentials plus a wildcard would let any site make
    # credentialed calls. Identity travels in the query string, not cookies,
    # so nothing here needs credentials in that configuration.
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request correlation ───────────────────────────────────────────────────────
# Stamps one id on every log line a request produces. Without it, lines from
# concurrent requests interleave with nothing to separate them: login_id is
# the only shared field and it is identical for two requests from one user —
# and most of the call tree (db.py, calculate_variance.py, the nlp modules)
# never receives even that.
#
# Identity is read from the query string rather than from require_login
# because that dependency is a `def`, so it runs in a threadpool worker whose
# context does NOT propagate back here. This middleware runs on the event loop
# in the request's own context, so what it sets is visible to the handler and
# to everything the handler calls, including the threadpool.
#
# An inbound X-Request-ID wins, so a trace started by the .NET host or a proxy
# carries through instead of being renamed at this boundary.
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = (request.headers.get("X-Request-ID") or "").strip()[:32] or new_request_id()
    request_id_var.set(request_id)
    login_id_var.set((request.query_params.get("loginId") or "").strip())
    tenant_id_var.set((request.query_params.get("tenantId") or "").strip())

    response = await call_next(request)
    # Echo it so a caller can quote the id when reporting a problem.
    response.headers["X-Request-ID"] = request_id
    return response


# ── App-level exception handlers ──────────────────────────────────────────────
# A HostProfileError means the request cannot address a repository — no tenant,
# an unknown tenant, an unprovisioned one. That is a configuration or
# authorisation fault with a message written for an operator, so it must not
# surface as "Unexpected server error"; 400 keeps it distinguishable from the
# 403s that require_login raises for a *known but disallowed* caller.
@app.exception_handler(HostProfileError)
async def host_profile_error_handler(request: Request, exc: HostProfileError) -> JSONResponse:
    logger.warning(
        "[main] 400 %s %s | host profile cannot serve this request | %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


# Catch-all. Several stages run outside any route's own try block — notably
# /variance/nlresolve's NLP stage (the function-level backend.nlp imports,
# retrieval, intent resolution). Starlette's default reply to those is a
# plain-text "Internal Server Error" with no JSON body, and the frontend reads
# `body.detail` (frontend/src/api.js), so the real cause — usually a missing
# NLP dependency, an absent artifacts/nlp-index/ index, or an embedding model
# that can't be downloaded — would be invisible. Here the traceback goes to
# the log and the exception type/message reaches the client as `detail`.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "[main] 500 %s %s | unhandled exception", request.method, request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Unexpected server error: {type(exc).__name__}: {exc}"},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
# Every route lives in backend/api/ (one module per functional area). Mounted
# here so this file owns only what an APIRouter cannot carry: the app object,
# the middleware and the app-level exception handlers.
for _router in ROUTERS:
    app.include_router(_router)


# ── Dev entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)
