# main.py — Standalone FastAPI application for the Data Variance feature.
# Run with:  uvicorn backend.main:app --port 8002 --reload
#   or:      python -m uvicorn backend.main:app --port 8002 --reload

from __future__ import annotations

import logging
import re

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


# ── Catch-all exception handler ───────────────────────────────────────────────
# Individual routes guard their own known failure modes, but several stages
# deliberately run OUTSIDE those try blocks — notably /variance/nlresolve's
# NLP stage (the function-level backend.nlp imports, get_relevant_schema,
# resolve_intent), which happens before that route's own try:. Anything
# raised there used to escape to Starlette's default handler, which replies
# with plain-text "Internal Server Error" and NO JSON body. The frontend
# reads `body.detail` (see frontend/src/api.js) and, finding none, could
# only show a bare "NL resolve error (500)" — hiding the actual cause, which
# on a fresh server deployment is usually a missing NLP dependency
# (sentence-transformers / faiss-cpu / rank-bm25), an absent backend/output/
# embedding index, or an embedding model that can't be downloaded.
#
# This handler makes every such crash self-reporting: the full traceback goes
# to logs/<date>.log and the exception type/message reaches the client as a
# normal JSON `detail`.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "[main] 500 %s %s | unhandled exception", request.method, request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Unexpected server error: {type(exc).__name__}: {exc}"},
    )


# ── GET /variance/nlp-health ──────────────────────────────────────────────────
# Deployment self-check for the NL query path, so diagnosing a 500 from
# /variance/nlresolve doesn't require shell access to read the server log.
# Reports each prerequisite separately: the three optional-but-required NLP
# packages, the embedding index files the external build tool drops into
# backend/output/, and whether the embedding model itself can actually load
# (the expensive one — a ~1.3GB download on first use, so it is only probed
# when ?check_model=true is passed).
@app.get("/variance/nlp-health", tags=["Meta"])
async def nlp_health(check_model: bool = False) -> dict:
    import importlib
    import os

    from .nlp import nlp_config

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
            from .nlp.embedder import embed_query
            embed_query("health check")
            embed_model = "ok"
        except Exception as exc:
            embed_model = f"FAILED: {type(exc).__name__}: {exc}"

    problems = (
        [f"package {k}: {v}" for k, v in packages.items() if v != "ok"]
        + [f"index file {k}: {v}" for k, v in index_files.items() if v == "MISSING"]
        + ([f"retriever import: {retriever_import}"] if retriever_import != "ok" else [])
        + ([f"embedding model: {embed_model}"] if embed_model.startswith("FAILED") else [])
    )

    return {
        "status":           "ok" if not problems else "degraded",
        "problems":         problems,
        "index_dir":        nlp_config.INDEX_DIR,
        "embed_model_name": nlp_config.EMBED_MODEL,
        "packages":         packages,
        "index_files":      index_files,
        "retriever_import": retriever_import,
        "embed_model":      embed_model,
    }


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
            comparison_mode=payload.comparison_mode,
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


# ── GET /variance/dates ─────────────────────────────────────────────────────────
@app.get("/variance/dates", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_dates(
    return_id: str,
    table_mapping_path: str,
    table_name: str,
    login_id: str = Depends(require_login),
) -> dict:
    """List every reporting date that actually has data for this return/table,
    newest first — lets the manual UI offer a dropdown of real submission
    dates instead of a free calendar picker.
    """
    logger.info(
        "[main] GET /variance/dates | login_id=%s | return_id=%s | table=%s",
        login_id, return_id, table_name,
    )

    require_return_access(login_id, return_id)

    try:
        dates = service.get_available_dates(
            return_id=return_id,
            return_tbl_path=table_mapping_path,
            table_name=table_name,
            execute_query_fn=execute_query,
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
        logger.exception("[main] 500 Unhandled exception | return_id=%s", return_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
        ) from exc

    return {"dates": dates}


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


# Sentinel `clarification_answer` value meaning "don't ask me, just proceed
# with your best guess" — both dimensions below treat it identically: skip
# the pin/ambiguity-gate and hand the (possibly still-ambiguous) shortlist
# straight to resolve_intent, letting the LLM pick on its own.
_SKIP_ANSWER = "__skip__"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Words too generic to count as "the query named this return" on their own
# (return names in this domain are things like "DNBS-01 Monthly Return",
# "CRILC" — matching on "monthly"/"return"/"report" alone would false-positive
# on almost every query). Real product/section names still match fine.
_GENERIC_NAME_TOKENS = {
    "return", "report", "monthly", "quarterly", "fortnightly", "weekly",
    "annual", "statement", "form", "data", "summary", "the", "of", "and", "for",
}


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _find_named_return_ids(query: str, login_id: str) -> list:
    """Literal name-mention check, layered in FRONT of the embedding-
    confidence gate below: does the query text itself contain a
    recognizable, non-generic word from any authorized return's Name (e.g.
    the user typed "CRILC" or "DNBS01")? This catches the case a pure
    embedding-similarity score can miss/get lucky on, and lets us skip the
    "which return?" prompt entirely when the answer is already spelled out
    in the query.

    Returns the list of matching return_ids (authorized only) — empty if
    the query names no known return at all, exactly one if it's specific,
    more than one if several returns share the mentioned word."""
    from .auth_service import get_allowed_form_ids
    from .config import AUTH_ENABLED

    query_tokens = _tokens(query) - _GENERIC_NAME_TOKENS
    if not query_tokens:
        return []

    returns = list(_parse_returns())
    if AUTH_ENABLED:
        allowed = get_allowed_form_ids(login_id) or set()
        returns = [r for r in returns if str(r.get("Id")) in allowed]

    matches = []
    for r in returns:
        if not r.get("Id") or not r.get("Name"):
            continue
        name_tokens = _tokens(r["Name"]) - _GENERIC_NAME_TOKENS
        if name_tokens and (query_tokens & name_tokens):
            matches.append(r["Id"])
    return matches


def _build_return_clarification(query: str, login_id: str, restrict_to: list | None = None) -> dict:
    """Build a needs_clarification response for the "no table/return signal
    at all" case (today's old 404) — asks the user to pick a return, rather
    than failing outright.

    When `restrict_to` is given (return_ids the query already narrowed down
    to — either a literal name mention via _find_named_return_ids, or the
    return_ids behind the query's own (low-confidence) embedding shortlist —
    see the call site in variance_nlresolve), the options are narrowed to
    just those instead of every authorized return. `restrict_to` is only
    ever omitted/empty as a last resort, when the query matched nothing at
    all — otherwise showing the user's entire authorized return list (which
    can be long) for a query that already gave SOME signal would defeat the
    point of asking.

    `allow_other: True` is always included so the frontend can offer an
    "Others" free-text box (see ControlBar.jsx's NlpReturnPicker) for a user
    whose intended return isn't among the narrowed options."""
    from .auth_service import get_allowed_form_ids
    from .config import AUTH_ENABLED

    returns = list(_parse_returns())
    if AUTH_ENABLED:
        allowed = get_allowed_form_ids(login_id) or set()
        returns = [r for r in returns if str(r.get("Id")) in allowed]
    if restrict_to:
        restrict_set = set(restrict_to)
        returns = [r for r in returns if r.get("Id") in restrict_set]

    options = [
        {"id": r["Id"], "label": r["Name"]}
        for r in returns
        if r.get("Id") and r.get("Name")
    ]
    options.sort(key=lambda o: o["label"].lower())

    question = (
        "I found a few returns that might match what you typed. Which one did you mean?"
        if restrict_to
        else "I couldn't tell which return your query is about. Which one did you mean?"
    )
    return {
        "needs_clarification": True,
        "dimension": "return",
        "question": question,
        "options": options,
        "skippable": True,
        "allow_other": True,
        "confidence": 0.0,
        "resolved_context": {"query": query},
    }


def _build_table_clarification(
    query: str, shortlist: dict, table_confidence: float, return_id: str | None = None,
) -> dict:
    """Build a needs_clarification response for table/section-level
    ambiguity — either the free-form cross-return case (backend/nlp/
    retriever.py's table_ambiguous) or the narrower case after a return has
    already been pinned (via a prior "return" clarification answer), which
    passes `return_id` through so a subsequent skip/answer on THIS prompt
    stays scoped to that same return (see resolved_context handling in
    variance_nlresolve) instead of falling back to free-form retrieval.

    Deliberately does NOT surface the candidate table names to the user —
    they're internal schema details, not something a business user should
    have to pick between. Instead this always just asks for more descriptive
    detail about the data they're after (frontend: ControlBar.jsx's
    NlpClarificationPanel renders a single free-text box, no option list),
    which gets folded back into the query and re-resolved from scratch. The
    candidate tables are kept in `options` purely for callers other than the
    shipped UI (e.g. direct API use/tests) that may still want to answer by
    table name — see the dimension=="table" branch in variance_nlresolve.

    `resolved_context` is just echoed back to the client — there is no
    server-side session store; the follow-up request resends it verbatim
    alongside the user's pick (see NLResolveRequest.clarification_answer/
    resolved_context)."""
    candidates = shortlist["tables"][:8]
    options = [
        {
            "id": t["table"],
            "label": f"{t.get('return_name') or t.get('return_id') or 'Unknown return'} — {t['table']}",
        }
        for t in candidates
    ]
    question = (
        "I'm not fully confident which data you mean yet — "
        "can you describe it in a bit more detail (e.g. the specific metric, "
        "section, or return)?"
    )

    resolved_context: dict = {"query": query}
    if return_id:
        resolved_context["return_id"] = return_id

    return {
        "needs_clarification": True,
        "dimension": "table",
        "question": question,
        "options": options,
        "skippable": True,
        "confidence": round(table_confidence, 3),
        "resolved_context": resolved_context,
    }


def _shortlist_for_return(return_id: str) -> dict | None:
    """Build a minimal shortlist scoped to every table under one specific
    return, for when the user has explicitly named the return (either via
    the "return" clarification, or in a future phase directly in the
    query) but the query itself gave no table-level signal to rank among
    them. Column candidates are pulled straight from the embedding index's
    per-table grouping (same helper retriever.py's own backfill logic
    uses) rather than re-running a query-scoped FAISS search, since there's
    no query signal to search with here — intent_resolver still needs a
    full column list to pick from."""
    from .nlp.index_store import meta_by_table
    from .nlp.nlp_config import COLUMN_INDEX_PATH, COLUMN_META_PATH

    return_row = next((r for r in _parse_returns() if r.get("Id") == return_id), None)
    if return_row is None:
        return None

    found = service.find_return_and_tables(return_row.get("Name", ""))
    if found.get("error") or found.get("candidates") or not found.get("table_mapping_path"):
        return None

    tables = [
        {
            "table":        t["table_name"],
            "return_id":    found["return_id"],
            "return_name":  found["return_name"],
            "filter_col":   t.get("filter_col") or "RDATE",
            "report_freq":  found.get("report_freq") or "M",
        }
        for t in found.get("tables", []) if t.get("table_name")
    ]
    if not tables:
        return None

    # meta_by_table() keys are uppercased; `tables` here come from the
    # table-mapping XML, and each record's own "table" value is index-cased
    # (lowercase), so it's rewritten to the XML name — otherwise every
    # downstream `c["table"] == t["table"]` comparison (_build_prompt,
    # _resolve_deterministic, _validate_grounding) misses and the shortlist
    # looks column-less. See meta_by_table's docstring.
    grouped_columns = meta_by_table(COLUMN_INDEX_PATH, COLUMN_META_PATH)
    seen_cols: set = set()
    columns: list = []
    for t in tables:
        for c in grouped_columns.get(t["table"].upper(), []):
            key = (t["table"], c["column"])
            if key not in seen_cols:
                seen_cols.add(key)
                columns.append({**c, "table": t["table"]})

    ambiguous = len(tables) > 1
    return {
        "tables": tables,
        "columns": columns,
        "matched_labels": [],
        "table_confidence": 0.5 if ambiguous else 1.0,
        "table_ambiguous": ambiguous,
    }


def _shortlist_for_table(table_name: str) -> dict | None:
    """Build a minimal single-table shortlist once the user has picked (or
    a prior step pinned) one specific table by name — used both for the
    "table" clarification's non-skip answer and to re-derive a table's
    return_id/report_freq when it's needed but wasn't already in scope."""
    from .nlp import return_lookup
    from .nlp.index_store import meta_by_table
    from .nlp.nlp_config import (
        COLUMN_INDEX_PATH, COLUMN_META_PATH, TABLE_INDEX_PATH, TABLE_META_PATH,
    )

    # Case-normalized lookup + record rewrite, same reason as
    # _shortlist_for_return above (`table_name` may be XML-cased).
    grouped_columns = meta_by_table(COLUMN_INDEX_PATH, COLUMN_META_PATH)

    # Resolve the return WITH the table's own index metadata as a hint —
    # table names are not unique across returns, and without the hint a
    # shared table (e.g. one used by both the Quarterly and Annual variant of
    # a return) resolves by fallback rules to whichever claimant sorts first,
    # which can carry the wrong report_freq into compute_variance. Same call
    # shape retriever.py uses. See return_lookup._select_candidate.
    hint_records = meta_by_table(TABLE_INDEX_PATH, TABLE_META_PATH).get(table_name.upper(), [])
    hint_text = " ".join(r.get("text", "") for r in hint_records) or None

    ret = return_lookup.get_return_for_table(table_name, hint_text=hint_text)
    if not ret or not ret.get("return_id"):
        return None
    return {
        "tables": [{"table": table_name, **ret}],
        "columns": [
            {**c, "table": table_name}
            for c in grouped_columns.get(table_name.upper(), [])
        ],
        "matched_labels": [],
        "table_confidence": 1.0,
        "table_ambiguous": False,
    }


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
    from .nlp.nlp_config import CONFIDENCE_ASK_FLOOR, CONFIDENCE_AUTO_PROCEED

    query = payload.query.strip()
    logger.info(
        "[main] POST /variance/nlresolve | login_id=%s | query=%r | dimension=%r | answer=%r",
        login_id, query, payload.dimension, payload.clarification_answer,
    )

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    resolved_context = payload.resolved_context or {}
    pinned_return_id = resolved_context.get("return_id")
    answer = payload.clarification_answer

    if payload.dimension == "return" and answer:
        if answer == _SKIP_ANSWER:
            # No return picked — best-effort: fall back to whatever the
            # free-form retrieval found, even below the confidence floor
            # that originally triggered this prompt. If it found literally
            # nothing, there's nothing to guess with.
            shortlist = get_relevant_schema(query, login_id)
            if not shortlist["tables"]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Still couldn't find a matching return/table for this query — please add more detail.",
                )
            logger.info(
                "[main] /variance/nlresolve | login_id=%s | query=%r | return clarification skipped -> best-effort guess",
                login_id, query,
            )
        else:
            shortlist = _shortlist_for_return(answer)
            if shortlist is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected return is no longer available.")
            logger.info(
                "[main] /variance/nlresolve | login_id=%s | query=%r | return answered -> return_id=%s (%d table(s))",
                login_id, query, answer, len(shortlist["tables"]),
            )
            if shortlist["table_ambiguous"]:
                return _build_table_clarification(query, shortlist, shortlist["table_confidence"], return_id=answer)

    elif payload.dimension == "table" and answer:
        if answer == _SKIP_ANSWER:
            # No specific table picked — best-effort: let resolve_intent's
            # LLM choose freely from the (still-ambiguous) shortlist, scoped
            # to whichever return was already pinned if one was.
            shortlist = _shortlist_for_return(pinned_return_id) if pinned_return_id else get_relevant_schema(query, login_id)
            if shortlist is None or not shortlist["tables"]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No accessible return/table matches this query.",
                )
            logger.info(
                "[main] /variance/nlresolve | login_id=%s | query=%r | table clarification skipped -> best-effort guess",
                login_id, query,
            )
        else:
            shortlist = _shortlist_for_table(answer)
            if shortlist is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Selected option is no longer valid for this query.",
                )
            logger.info(
                "[main] /variance/nlresolve | login_id=%s | query=%r | table answered -> table=%s",
                login_id, query, answer,
            )

    else:
        # First pass for this query — no clarification answer yet.
        shortlist = get_relevant_schema(query, login_id)
        table_confidence = shortlist.get("table_confidence", 0.0)
        table_ambiguous = shortlist.get("table_ambiguous", False)

        weak_retrieval = not shortlist["tables"] or table_confidence < CONFIDENCE_ASK_FLOOR
        if weak_retrieval:
            # Retrieval alone isn't confident there's a real match — before
            # asking a broad "which return?" question, check whether the
            # query TEXT already names a known return explicitly. A literal
            # name mention is stronger evidence than a coincidental (or
            # coincidentally absent) embedding score.
            named_return_ids = _find_named_return_ids(query, login_id)

            if len(named_return_ids) == 1:
                named_shortlist = _shortlist_for_return(named_return_ids[0])
                if named_shortlist is not None:
                    logger.info(
                        "[main] /variance/nlresolve | login_id=%s | query=%r | "
                        "query names return_id=%s directly -> using it instead of asking",
                        login_id, query, named_return_ids[0],
                    )
                    shortlist = named_shortlist
                    table_confidence = shortlist["table_confidence"]
                    table_ambiguous = shortlist["table_ambiguous"]
                    weak_retrieval = False

            if weak_retrieval:
                # Narrow the options to returns the query actually gave SOME
                # signal for — a literal name mention first, else whichever
                # return_ids the query's own (too-low-confidence-to-auto-
                # proceed) embedding shortlist already surfaced. Only when
                # neither found anything at all does this fall through to
                # None, i.e. the full authorized-return list, as a last
                # resort — see _build_return_clarification's docstring.
                query_related_return_ids = list(named_return_ids)
                if not query_related_return_ids:
                    seen: set = set()
                    for t in shortlist["tables"]:
                        rid = t.get("return_id")
                        if rid and rid not in seen:
                            seen.add(rid)
                            query_related_return_ids.append(rid)

                logger.info(
                    "[main] /variance/nlresolve | login_id=%s | query=%r | no usable table/return signal "
                    "(table_confidence=%.3f, query_related_return_ids=%s) -> asking for return",
                    login_id, query, table_confidence, query_related_return_ids,
                )
                return _build_return_clarification(
                    query, login_id,
                    restrict_to=query_related_return_ids or None,
                )

        if table_ambiguous or table_confidence < CONFIDENCE_AUTO_PROCEED:
            logger.info(
                "[main] /variance/nlresolve | login_id=%s | query=%r | table ambiguous "
                "(confidence=%.3f, tied=%s) -> asking clarification",
                login_id, query, table_confidence, table_ambiguous,
            )
            return _build_table_clarification(query, shortlist, table_confidence)

    # Default to 1.0 for shortlists this route itself pinned down to exactly
    # one table (_shortlist_for_return/_shortlist_for_table, or the "return"
    # skip's best-effort guess) — those didn't go through the ambiguity
    # scoring above, so there's no lower number to report here.
    final_confidence = shortlist.get("table_confidence", 1.0)

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
        "confidence":         round(final_confidence, 3),
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