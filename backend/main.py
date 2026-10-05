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
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import ROUTERS
from .api.errors import HOST_PROFILE_DETAIL, server_error_detail
from .config import API_BASE_PATH, CORS_ORIGINS, SERVER_HOST, SERVER_PORT
from .config import settings as cfg
from .hosts import HostProfileError
from .limits import SlidingWindowLimiter, is_nl_path
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
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9-]+")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .security_posture import log_security_posture

    # One log line per unsafe security setting; refuses to start only when the
    # operator set DV_REQUIRE_AUTH=true and auth is off (code review M-04).
    log_security_posture()
    # Load the ~1.3 GB embedding model in the background so the first NL request
    # does not pay for it (it measured 115 s). The thread never blocks startup
    # and never raises; the import inside is lazy so a deployment without the
    # NLP packages still boots (code review RAG-05).
    if cfg.NLP_WARMUP:
        from .nlp.warmup import warm_nlp

        threading.Thread(target=warm_nlp, name="nlp-warmup", daemon=True).start()
    yield


# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    lifespan=lifespan,
    title="Data Variance API",
    version="1.0.0",
    description="Standalone Data Variance analysis — no chatbot dependency.",
    root_path=API_BASE_PATH or "",
    # The interactive docs and the schema are an unauthenticated map of every
    # route (code review SEC-07). Off unless DV_ENABLE_API_DOCS=true.
    docs_url="/docs" if cfg.API_DOCS_ENABLED else None,
    redoc_url="/redoc" if cfg.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if cfg.API_DOCS_ENABLED else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # With a "*" origin Starlette echoes back whatever Origin the browser
    # sent, so credentials plus a wildcard would let any site make
    # credentialed calls. Identity travels in the query string, not cookies,
    # so nothing here needs credentials in that configuration.
    allow_credentials="*" not in CORS_ORIGINS,
    # The API only uses GET and POST; Authorization carries the optional JWT.
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID", "X-Tenant-Id"],
)


# ── Request budget and body size (code review API-01) ────────────────────────
# Declared BEFORE the correlation middleware below on purpose: Starlette makes
# the last-declared middleware the outermost, so this one runs inside it and its
# 413/429 responses still carry X-Request-ID.
_per_login = SlidingWindowLimiter(cfg.RATE_LIMIT_PER_MIN)
_per_login_nl = SlidingWindowLimiter(cfg.RATE_LIMIT_NL_PER_MIN)
_global = SlidingWindowLimiter(cfg.RATE_LIMIT_PER_MIN * 10 if cfg.RATE_LIMIT_PER_MIN > 0 else 0)


@app.middleware("http")
async def request_limits_middleware(request: Request, call_next):
    if request.url.path.rstrip("/").endswith(("/health", "/app-config", "/ready")):
        return await call_next(request)              # probes and bootstrap are never throttled

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cfg.MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "The request body is too large."})

    key = (request.query_params.get("loginId") or "").strip().lower() or "anonymous"
    checks = [(_global, "global"), (_per_login, key)]
    if is_nl_path(request.url.path):
        checks.append((_per_login_nl, key))
    for limiter, k in checks:
        allowed, retry_after = limiter.check(k)
        if not allowed:
            logger.warning("[main] 429 %s %s | rate limit (%s)", request.method, request.url.path, k)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please wait a moment and try again."},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


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
    # Only [A-Za-z0-9-] survives (code review L-04): the value is written to the log and
    # echoed in a response header, so CR/LF or other characters would let a caller forge log
    # lines. Anything else is replaced by a fresh id.
    inbound = (request.headers.get("X-Request-ID") or "").strip()[:32]
    request_id = inbound if inbound and _REQUEST_ID_RE.fullmatch(inbound) else new_request_id()
    request_id_var.set(request_id)
    request.state.request_id = request_id        # for handlers that run outside this context
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
        content={"detail": HOST_PROFILE_DETAIL},
    )


# Catch-all. Several stages run outside any route's own try block — notably
# /variance/nlresolve's NLP stage (the function-level backend.nlp imports,
# retrieval, intent resolution). Starlette's default reply to those is a
# plain-text "Internal Server Error" with no JSON body, and the frontend reads
# `body.detail` (frontend/src/api.js), so the real cause — usually a missing
# NLP dependency, an absent artifacts/nlp-index/ index, or an embedding model
# that can't be downloaded — would be invisible. Here the traceback goes to
# the log and the client gets a fixed message plus the request id (SEC-04).
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "[main] 500 %s %s | unhandled exception", request.method, request.url.path,
    )
    request_id = getattr(request.state, "request_id", None)
    # This handler runs OUTSIDE the correlation middleware (Starlette's outermost
    # layer), so that middleware's header never reaches this response; set it here
    # so a user can still quote the id from the response headers.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": server_error_detail(request_id)},
        headers={"X-Request-ID": request_id} if request_id else None,
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
