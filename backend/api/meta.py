# meta.py — unauthenticated diagnostics: /health, /app-config, /variance/nlp-health

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import ANONYMOUS, APP_VERSION
from ..config import settings as cfg
from ..hosts import HostProfileError, get_profile
from .schemas import AppConfigResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Handlers are `def`, not `async def`, on purpose — see backend/api/variance.py.


# ── GET /variance/nlp-health ──────────────────────────────────────────────────
# Deployment self-check for the NL query path, so diagnosing a 500 from
# /variance/nlresolve doesn't require shell access to read the server log.
# Reports each prerequisite separately: the three optional-but-required NLP
# packages, the embedding index files the external build tool drops into
# artifacts/nlp-index/, and whether the embedding model itself can actually load
# (the expensive one — a ~1.3GB download on first use, so it is only probed
# when ?check_model=true is passed).
@router.get("/variance/nlp-health", tags=["Meta"])
def nlp_health(check_model: bool = False) -> dict:
    from ..nlp import nlp_config

    # Forcing the ~1.3 GB model to load, and the paths/package errors below, are
    # operator tools, not public output (code review SEC-05). Without
    # DV_DIAGNOSTICS_ENABLED=true this is a bare status and never loads the model.
    diagnostics = cfg.DIAGNOSTICS_ENABLED
    check_model = check_model and diagnostics

    packages: dict[str, str] = {}
    for mod in ("faiss", "sentence_transformers", "rank_bm25", "numpy"):
        try:
            importlib.import_module(mod)
            packages[mod] = "ok"
        except Exception as exc:
            packages[mod] = f"MISSING: {type(exc).__name__}: {exc}"

    # Required vs optional mirrors each loader's own behavior: the FAISS
    # table/column indexes are load-or-die, while the BM25/QA signals
    # degrade to a silent no-op when absent (see lexical_search.py).
    required_files = {
        "table_index.faiss":  nlp_config.TABLE_INDEX_PATH,
        "table_meta.pkl":     nlp_config.TABLE_META_PATH,
        "column_index.faiss": nlp_config.COLUMN_INDEX_PATH,
        "column_meta.pkl":    nlp_config.COLUMN_META_PATH,
        "schema.json":        nlp_config.SCHEMA_JSON_PATH,
    }
    optional_files = {
        "bm25_table_index.pkl":  nlp_config.BM25_INDEX_PATH,
        "qa_pairs.json":         nlp_config.QA_PAIRS_PATH,
        "qa_index.faiss":        nlp_config.QA_INDEX_PATH,
        "qa_meta.pkl":           nlp_config.QA_META_PATH,
        "row_label_index.faiss": nlp_config.ROW_LABEL_INDEX_PATH,
        "row_label_meta.pkl":    nlp_config.ROW_LABEL_META_PATH,
    }
    index_files = {
        name: ("ok" if os.path.isfile(path) else "MISSING")
        for name, path in required_files.items()
    }
    index_files.update({
        name: ("ok" if os.path.isfile(path) else "absent (optional)")
        for name, path in optional_files.items()
    })

    # Import the retriever exactly the way /variance/nlresolve does — this is
    # the import that fails first when a package above is missing.
    try:
        importlib.import_module("backend.nlp.retriever")
        retriever_import = "ok"
    except Exception as exc:
        retriever_import = f"FAILED: {type(exc).__name__}: {exc}"

    embed_model = "not checked (pass ?check_model=true)"
    if check_model:
        try:
            from ..nlp.embedder import embed_query
            embed_query("health check")
            embed_model = "ok"
        except Exception as exc:
            embed_model = f"FAILED: {type(exc).__name__}: {exc}"

    # Reported first and on its own: a whole folder missing for the configured
    # version otherwise shows up as five separate "index file MISSING" lines,
    # which reads like a broken build rather than "this deployment has no
    # embeddings for the version it is set to". The two need different fixes.
    dir_problem = nlp_config.index_dir_problem()

    problems = (
        ([dir_problem] if dir_problem else [])
        + [f"package {k}: {v}" for k, v in packages.items() if v != "ok"]
        + [f"index file {k}: {v}" for k, v in index_files.items() if v == "MISSING"]
        + ([f"retriever import: {retriever_import}"] if retriever_import != "ok" else [])
        + ([f"embedding model: {embed_model}"] if embed_model.startswith("FAILED") else [])
    )

    # Which index folder is in use is decided by VERSION: 5.5 covers the CIMS
    # returns, 6.0 the QCB ones. They share no tables, so serving one host from
    # the other's index does NOT fail loudly - retrieval just returns the
    # closest wrong table and the generated SQL names something that does not
    # exist for that host. Reporting the version beside the folder is what makes
    # that mismatch visible instead of silent.
    index_tables = None
    try:
        with open(nlp_config.SCHEMA_JSON_PATH, encoding="utf-8") as fh:
            index_tables = len(json.load(fh))
    except (OSError, ValueError):
        pass  # already reported above as a MISSING/unreadable index file

    # Broader than the file-presence check above: build-stamp checksums, vector/
    # record counts, and the embedding model the build recorded (RAG-01/RAG-02).
    index_problems: list[str] = []
    if diagnostics:
        try:
            from ..nlp import index_manifest

            index_problems = index_manifest.verify_index_folder(nlp_config.INDEX_DIR, nlp_config.EMBED_MODEL)
        except Exception as exc:  # noqa: BLE001 - a report, never a failure
            index_problems = [f"index verification failed: {type(exc).__name__}: {exc}"]
        problems = problems + [f"index: {p}" for p in index_problems]

    if not diagnostics:
        if problems:
            logger.warning("[meta] /variance/nlp-health degraded | %s", problems)
        return {"status": "ok" if not problems else "degraded"}

    return {
        "status":           "ok" if not problems else "degraded",
        "problems":         problems,
        "app_version":      APP_VERSION,
        "index_dir":        nlp_config.INDEX_DIR,
        "index_dir_source": (
            "DV_NLP_INDEX_DIR override"
            if nlp_config.INDEX_DIR_OVERRIDE
            else "VERSION=" + APP_VERSION
        ),
        "index_tables":     index_tables,
        "embed_model_name": nlp_config.EMBED_MODEL,
        "packages":         packages,
        "index_files":      index_files,
        "retriever_import": retriever_import,
        "embed_model":      embed_model,
    }


# ── Health check (no auth — used by infra/monitoring probes) ──────────────────
# ── GET /ready ────────────────────────────────────────────────────────────────
# Readiness, as opposed to /health's liveness: can this instance actually do its job?
# Reports two booleans only (no hosts, paths or error text). 503 when the database is
# unreachable; the LLM is reported but never makes the instance "not ready", because the
# NL path degrades to the deterministic resolver without it (code review L-17).
# Unauthenticated and cheap: the answer is cached for READY_CACHE_SEC so a probe cannot
# be used to hammer Oracle.
READY_CACHE_SEC = 10.0
_ready_lock = threading.Lock()
_ready_cache: dict = {"at": 0.0, "value": None}


def _probe_database() -> bool:
    from ..data.db import execute_query

    try:
        _cols, _rows, err = execute_query("SELECT 1 FROM DUAL")
        return err is None
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


def _probe_llm() -> bool:
    """True when the LLM host answers at all (any HTTP status): reachable, not necessarily healthy."""
    from urllib.parse import urlparse

    import requests

    from ..nlp import nlp_config

    parsed = urlparse(nlp_config.OLLAMA_URL)
    if not parsed.scheme or not parsed.netloc:
        return False
    try:
        requests.get(f"{parsed.scheme}://{parsed.netloc}/", timeout=2)
        return True
    except requests.exceptions.RequestException:
        return False


def _readiness() -> dict:
    now = time.monotonic()
    with _ready_lock:
        cached = _ready_cache["value"]
        if cached is not None and now - _ready_cache["at"] < READY_CACHE_SEC:
            return cached
    value = {"database": _probe_database(), "llm": _probe_llm()}
    with _ready_lock:
        _ready_cache.update(at=time.monotonic(), value=value)
    return value


@router.get("/ready", tags=["Meta"])
def ready():
    checks = _readiness()
    body = {"status": "ready" if checks["database"] else "not_ready", **checks}
    return JSONResponse(status_code=200 if checks["database"] else 503, content=body)


@router.get("/health", tags=["Meta"], response_model=HealthResponse, response_model_exclude_unset=True)
def health():
    info = {"status": "ok", "version": APP_VERSION}
    try:
        profile = get_profile()
        # ANONYMOUS resolves cleanly under 5.5, where paths do not depend on
        # identity. Under 6.0 it is rejected by design — there is no
        # tenant-free repository — so the paths are reported as unavailable
        # rather than guessed from an arbitrary tenant.
        info["profile"] = profile.name
        # Resolved filesystem paths are operator detail: shown only when
        # DV_DIAGNOSTICS_ENABLED=true (code review SEC-05).
        if cfg.DIAGNOSTICS_ENABLED:
            try:
                info["paths"] = profile.describe(ANONYMOUS)
            except HostProfileError as exc:
                info["paths"] = {"detail": f"tenant-scoped; not resolvable without a request ({exc})"}
    except HostProfileError as exc:
        logger.error("[meta] /health - profile unavailable: %s", exc)
        info["status"] = "misconfigured"
    return info


# ── GET /app-config ────────────────────────────────────────────────────────────
@router.get("/app-config", tags=["Meta"], response_model=AppConfigResponse, response_model_exclude_unset=True)
def app_config():
    """Which iDEAL host this backend is configured for.

    The React app fetches this once at startup to pick its authentication
    strategy, so VERSION in the root .env is the single switch for the whole
    project - backend and frontend - with no rebuild and no second setting to
    keep in sync. Unauthenticated by necessity (it runs before auth exists)
    and deliberately exposes nothing but the version and whether a tenant is
    required; /health carries the filesystem detail.
    """
    requires_tenant = False
    profile_name = None
    try:
        profile = get_profile()
        requires_tenant = profile.requires_tenant
        profile_name = profile.name
    except HostProfileError as exc:
        logger.error("[meta] /app-config - profile unavailable: %s", exc)

    return {
        "version":         APP_VERSION,
        "profile":         profile_name,
        "requires_tenant": requires_tenant,
    }
