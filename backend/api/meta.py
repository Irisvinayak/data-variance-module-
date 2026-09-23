# meta.py — unauthenticated diagnostics: /health, /app-config, /variance/nlp-health
#
# Split out of backend/main.py, which had grown to ~1400 lines holding every
# route. Route bodies are unchanged; only the decorator and the imports moved.
# backend/main.py mounts this router, so the URLs are identical.

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /variance/nlp-health ──────────────────────────────────────────────────
# Deployment self-check for the NL query path, so diagnosing a 500 from
# /variance/nlresolve doesn't require shell access to read the server log.
# Reports each prerequisite separately: the three optional-but-required NLP
# packages, the embedding index files the external build tool drops into
# artifacts/nlp-index/, and whether the embedding model itself can actually load
# (the expensive one — a ~1.3GB download on first use, so it is only probed
# when ?check_model=true is passed).
@router.get("/variance/nlp-health", tags=["Meta"])
async def nlp_health(check_model: bool = False) -> dict:
    import importlib
    import os

    from ..nlp import nlp_config

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
    from ..config import settings as _settings

    index_tables = None
    try:
        with open(nlp_config.SCHEMA_JSON_PATH, encoding="utf-8") as fh:
            import json as _json
            index_tables = len(_json.load(fh))
    except Exception:
        pass

    return {
        "status":           "ok" if not problems else "degraded",
        "problems":         problems,
        "app_version":      _settings.APP_VERSION,
        "index_dir":        nlp_config.INDEX_DIR,
        "index_dir_source": (
            "DV_NLP_INDEX_DIR override"
            if nlp_config.INDEX_DIR_OVERRIDE
            else "VERSION=" + _settings.APP_VERSION
        ),
        "index_tables":     index_tables,
        "embed_model_name": nlp_config.EMBED_MODEL,
        "packages":         packages,
        "index_files":      index_files,
        "retriever_import": retriever_import,
        "embed_model":      embed_model,
    }


# ── Health check (no auth — used by infra/monitoring probes) ──────────────────
@router.get("/health", tags=["Meta"])
async def health():
    from ..config import ANONYMOUS, APP_VERSION
    from ..hosts import HostProfileError, get_profile

    info = {"status": "ok", "version": APP_VERSION}
    try:
        profile = get_profile()
        # ANONYMOUS resolves cleanly under 5.5, where paths do not depend on
        # identity. Under 6.0 it is rejected by design — there is no
        # tenant-free repository — so the paths are reported as unavailable
        # rather than guessed from an arbitrary tenant.
        info["profile"] = profile.name
        try:
            info["paths"] = profile.describe(ANONYMOUS)
        except HostProfileError as exc:
            info["paths"] = {"detail": f"tenant-scoped; not resolvable without a request ({exc})"}
    except HostProfileError as exc:
        info["status"] = "misconfigured"
        info["detail"] = str(exc)
    return info


# ── GET /app-config ────────────────────────────────────────────────────────────
@router.get("/app-config", tags=["Meta"])
async def app_config():
    """Which iDEAL host this backend is configured for.

    The React app fetches this once at startup to pick its authentication
    strategy, so VERSION in the root .env is the single switch for the whole
    project - backend and frontend - with no rebuild and no second setting to
    keep in sync. Unauthenticated by necessity (it runs before auth exists)
    and deliberately exposes nothing but the version and whether a tenant is
    required; /health carries the filesystem detail.
    """
    from ..config import APP_VERSION
    from ..hosts import HostProfileError, get_profile

    requires_tenant = False
    profile_name = None
    try:
        profile = get_profile()
        requires_tenant = profile.requires_tenant
        profile_name = profile.name
    except HostProfileError as exc:
        logger.error("[main] /app-config - profile unavailable: %s", exc)

    return {
        "version":         APP_VERSION,
        "profile":         profile_name,
        "requires_tenant": requires_tenant,
    }
