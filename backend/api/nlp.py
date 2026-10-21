# nlp.py — the natural-language path: /variance/nlresolve, /variance/nlquery

from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.deps import require_login, require_return_access, require_tables_access
from ..auth.service import get_allowed_form_ids
from ..config import AUTH_ENABLED, RequestContext
from ..config import settings as cfg
from ..data import service
from ..data.calculate_variance import is_no_data_error, result_error
from ..data.db import execute_query
from ..data.validation import InvalidInput, ensure_safe_return_id, ensure_safe_table_ref
from ..data.models import NLResolveRequest
from ..data.report_lookup import parse_returns
from ..nlp.analysis import analyze as analyze_result
from ..nlp.operation_parser import parse_operation
from .errors import http_error, server_error_detail
from .schemas import NlQueryResponse, NlResolveResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Handlers are `def`, not `async def`, on purpose — see backend/api/variance.py.


def _allowed_return_ids(ctx: RequestContext) -> set | None:
    """The caller's allowed return ids, or None when auth is bypassed.

    None and an empty set mean different things downstream: None is "no auth
    scoping", an empty set is "this user may access nothing"."""
    return (get_allowed_form_ids(ctx) or set()) if AUTH_ENABLED else None


def _return_row(return_id: str, ctx: RequestContext) -> dict | None:
    """The returns-master row for `return_id`, or None."""
    return next((r for r in parse_returns(ctx) if r.get("Id") == return_id), None)


def _table_hint_text(table_name: str) -> str | None:
    """The table's embedding-index text, used to disambiguate which return owns
    it — table names are not unique across returns, and without the hint a
    shared table (e.g. one used by both the Quarterly and Annual variant of a
    return) resolves by fallback rules to whichever claimant sorts first, which
    can carry the wrong report_freq into compute_variance. Same call shape
    retriever.py uses; see return_lookup._select_candidate."""
    from ..nlp.index_store import meta_by_table
    from ..nlp.nlp_config import TABLE_INDEX_PATH, TABLE_META_PATH

    records = meta_by_table(TABLE_INDEX_PATH, TABLE_META_PATH).get(table_name.upper(), [])
    return " ".join(r.get("text", "") for r in records) or None


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


def _target_columns(computed: dict, spec, fallback: list, table_name: str) -> list:
    """The computed columns matching the names in spec.target_columns (by identifier
    or schema description). Unmatched names become notes on `spec`; when nothing
    matches at all, `fallback` (the resolver's choice) is kept."""
    from ..nlp import schema_info
    from ..nlp.analysis import resolve_target_columns

    available = computed.get("columns") or []
    keep, missing = resolve_target_columns(
        spec.target_columns, available, lambda c: schema_info.column_description(table_name, c))
    if missing:
        shown = ", ".join(schema_info.column_description(table_name, c) or c for c in available)
        for name in missing:
            spec.notes.append(f"Column '{name}' was not found in this table. Available columns: {shown}.")
    return keep or fallback


# Sentinel `clarification_answer` value meaning "don't ask me, just proceed
# with your best guess" — both dimensions below treat it identically: skip
# the pin/ambiguity-gate and hand the (possibly still-ambiguous) shortlist
# straight to resolve_intent, letting the LLM pick on its own.
_SKIP_ANSWER = "__skip__"

# `clarification_answer` for the "date" dimension meaning "latest date, compared
# with the previous available one" — the same as skipping the question.
_LATEST_DATE_ANSWER = "latest"
_DATE_ANSWER_RE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")


def _build_date_clarification(
    query: str, cands: list, return_id: str, table_name: str, limit: int,
) -> dict:
    """Ask which reporting date to analyse when the query names none.

    `cands` are the table's real dates, newest first. Each option is compared
    with the previous available date; the first option is the default the
    pipeline would otherwise have used silently. resolved_context pins the
    return and table so the answer re-enters with no ambiguity left to resolve."""
    from ..nlp.date_resolver import _fmt

    options = []
    for i, d in enumerate(cands[:limit]):
        prev = cands[i + 1] if i + 1 < len(cands) else None
        vs = f" vs {_fmt(prev)}" if prev else " (no earlier date)"
        options.append({
            "id": _LATEST_DATE_ANSWER if i == 0 else _fmt(d),
            "label": (f"Latest: {_fmt(d)}" if i == 0 else _fmt(d)) + vs,
        })
    return {
        "needs_clarification": True,
        "dimension": "date",
        "question": (
            "You didn't mention a date. Which reporting date do you want? "
            "Skip to use the latest date compared with the previous available date."
        ),
        "options": options,
        "skippable": True,
        "allow_other": False,
        "confidence": 1.0,
        "resolved_context": {"query": query, "return_id": return_id, "table": table_name},
    }

def _build_return_clarification(query: str, ctx: RequestContext, restrict_to: list | None = None) -> dict:
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
    from ..nlp import indexed_returns

    returns = list(parse_returns(ctx))
    allowed = _allowed_return_ids(ctx)
    if allowed is not None:
        returns = [r for r in returns if str(r.get("Id")) in allowed]

    # Only offer returns the embedding index actually covers. Every option in
    # this list is a promise that picking it produces an answer, and a return
    # with no vectors cannot keep that promise: _shortlist_for_return() builds
    # its column candidates from the embedding index, so an uncovered return
    # yields tables with zero columns and intent_resolver falls through to an
    # untrimmed whole-table result. Listing all 281 authorized returns when 3
    # are indexed made picking a dud the overwhelmingly likely outcome.
    # (No-op when coverage can't be determined — see indexed_returns.)
    returns = indexed_returns.filter_returns(returns, ctx=ctx)

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
    if not options:
        # Coverage is known and nothing the user can access is in it. Say so
        # plainly rather than rendering an empty picker the user can only
        # cancel out of.
        question = (
            "None of the returns you have access to have been indexed for "
            "natural-language search yet. Please use the manual return/table "
            "selection above."
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


def _twin_return_ids(shortlist: dict) -> list:
    """Return ids whose shortlisted table has exactly the same columns as the
    top table — the same form section shipped in several returns (every QCB
    return has an identical FILING_INFO table). Scores cannot separate such
    twins, so a query that names no return must be asked which one it means.
    Empty unless at least two different returns hold a twin."""
    from ..nlp.index_store import meta_by_table
    from ..nlp.nlp_config import COLUMN_INDEX_PATH, COLUMN_META_PATH

    tables = shortlist.get("tables") or []
    if len(tables) < 2:
        return []
    grouped = meta_by_table(COLUMN_INDEX_PATH, COLUMN_META_PATH)

    def cols(t: dict) -> frozenset:
        return frozenset(str(c.get("column", "")).upper() for c in grouped.get(t["table"].upper(), []))

    top_cols = cols(tables[0])
    if not top_cols:
        return []
    ids: list = []
    for t in tables:
        rid = t.get("return_id")
        if rid and rid not in ids and cols(t) == top_cols:
            ids.append(rid)
    return ids if len(ids) > 1 else []


def _shortlist_for_return(
    return_id: str,
    query: str,
    ctx: RequestContext,
    *,
    analysis=None,
) -> dict | None:
    """Stage 2 of return-scoped resolution: the return is already decided
    (named in the query, or picked from the "which return?" clarification), so
    rank ITS tables and their columns against what the query actually asks for.

    `query` and `ctx` are REQUIRED, not optional. This function used to
    take only `return_id` and ignore the query entirely — tables in
    table-mapping-XML order, every column of every table in raw index order,
    and a confidence hardcoded to 0.5/1.0. Downstream, intent_resolver shows
    the LLM only the first INTENT_MAX_COLS_PER_TABLE columns while validating
    against the full list, so a correct column past that cap in an arbitrary
    order was unreachable, not merely deprioritised. Making the query a
    required parameter is what stops that shape being reintroduced.

    Tables come from indexed_returns, NOT from service.find_return_and_tables.
    That distinction is load-bearing: find_return_and_tables resolves all three
    returns that currently have embeddings (CIMS_RAQ(Quarterly),
    CIMS_ALE_Domestic/Oversease(Quarterly)) to a Mapping_1.xml whose rows carry
    no TableName attribute at all, so its table list came back EMPTY and this
    function returned None — which the callers turn into
    "Selected return is no longer available." (main.py's 404 at the
    dimension=="return" branch). In other words, answering the "which return?"
    question was guaranteed to fail for exactly the returns the NLP layer can
    serve. return_lookup, which indexed_returns is built from, carries the
    XML_Query.xml fallback that recovers those tables — 26 for CIMS_RAQ where
    find_return_and_tables finds 0.

    find_return_and_tables is still called, but only for `table_mapping_path`,
    which compute_variance needs and which it does resolve correctly.
    """
    from ..nlp import indexed_returns, return_lookup
    from ..nlp.nlp_config import SCOPED_RETRIEVAL_ENABLED
    from ..nlp.scoped_retriever import rank_within_tables

    # return_id can come straight from the client (a "which return?" answer, or
    # resolved_context echoed back), so it is not pre-authorized the way the
    # retriever's shortlist is. Check before any of its tables are ranked or
    # offered back as clarification options.
    require_return_access(ctx, return_id)

    return_row = _return_row(return_id, ctx)
    if return_row is None:
        return None

    table_names = indexed_returns.tables_for_return(return_id, ctx)
    if not table_names:
        logger.warning(
            "[api.nlp] _shortlist_for_return | return_id=%s | the embedding index covers "
            "none of this return's tables — nothing to resolve against",
            return_id,
        )
        return None

    # Per-table metadata (filter_col / report_freq) resolved the same way
    # retriever.py resolves it — see _table_hint_text.
    tables: list = []
    for name in table_names:
        ret = return_lookup.get_return_for_table(
            name, hint_text=_table_hint_text(name), ctx=ctx,
        ) or {}
        tables.append({
            "table":       name,
            "return_id":   return_id,
            "return_name": return_row.get("Name", ""),
            "filter_col":  ret.get("filter_col") or "RDATE",
            "report_freq": ret.get("report_freq") or return_row.get("RepFreq") or "M",
        })

    if not SCOPED_RETRIEVAL_ENABLED:
        return _unranked_shortlist(tables)

    return rank_within_tables(query, tables, ctx, analysis=analysis)


def _unranked_shortlist(tables: list) -> dict:
    """The pre-ranking shortlist shape: tables in whatever order they arrived,
    no column candidates, and a confidence that is a table COUNT rather than a
    measurement. Retained only as the DV_NLP_SCOPED_RETRIEVAL=false escape
    hatch, so scoped ranking can be switched off without a deploy."""
    ambiguous = len(tables) > 1
    return {
        "tables": tables,
        "columns": [],
        "matched_labels": [],
        "table_confidence": 0.5 if ambiguous else 1.0,
        "table_ambiguous": ambiguous,
    }


def _shortlist_for_table(
    table_name: str,
    query: str,
    ctx: RequestContext,
    *,
    analysis=None,
) -> dict | None:
    """Single-table shortlist, once the user has picked (or a prior step
    pinned) one specific table by name.

    The table choice is settled here, so ranking has nothing to choose between
    — but the COLUMNS still do, and they used to come back as the table's whole
    column list in raw index order. That is the list intent_resolver truncates
    to INTENT_MAX_COLS_PER_TABLE before showing the LLM, so ordering them by
    the query is what makes the right column reachable at all.
    """
    from ..nlp import return_lookup
    from ..nlp.nlp_config import SCOPED_RETRIEVAL_ENABLED
    from ..nlp.scoped_retriever import rank_within_tables

    ret = return_lookup.get_return_for_table(
        table_name, hint_text=_table_hint_text(table_name), ctx=ctx,
    )
    if not ret or not ret.get("return_id"):
        return None
    # table_name is a client-supplied clarification answer: any table in the
    # index can be named here, so its owning return must be checked.
    require_return_access(ctx, ret["return_id"])

    tables = [{"table": table_name, **ret}]
    if not SCOPED_RETRIEVAL_ENABLED:
        shortlist = _unranked_shortlist(tables)
    else:
        shortlist = rank_within_tables(query, tables, ctx, analysis=analysis)

    # One pinned table is not ambiguous by construction — the user, or a prior
    # stage, named it. Overriding the computed confidence is deliberate and
    # asymmetric with _shortlist_for_return: with a single candidate the score
    # would be measuring "does the query match this table", a question nobody
    # downstream is asking, and a low answer could only turn an explicit choice
    # back into another question.
    shortlist["table_confidence"] = 1.0
    shortlist["table_ambiguous"] = False
    return shortlist


# ── NLP stage instrumentation ─────────────────────────────────────────────────
# /variance/nlresolve's NLP work (module imports, retrieval, intent
# resolution) runs BEFORE the route's own try: block, so until now a failure
# there produced a bare 500 with no indication of WHICH stage broke — the
# request's own "POST /variance/nlresolve" line was the last thing in the log.
# This logs one line on failure only: nothing extra on the success path, since
# each stage already logs its own outcome (nlp.retriever, nlp.intent_resolver,
# nlp.date_resolver). The exception is re-raised untouched so the existing
# handlers still decide the status code.
def _nlp_stage(stage: str, ctx: RequestContext, query: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise                       # a deliberate 404/400 — not a stage failure
    except Exception as exc:
        logger.error(
            "[api.nlp] /variance/nlresolve | %s | query=%r | stage=%s FAILED | %s: %s",
            ctx, query, stage, type(exc).__name__, exc, exc_info=True,
        )
        raise


# ── POST /variance/nlresolve ───────────────────────────────────────────────────
def _nlresolve_core(
    payload: NLResolveRequest,
    ctx: RequestContext,
    *,
    spec_override=None,
    date_query: str | None = None,
    dates_override: list | None = None,
) -> dict:
    """Resolve a natural-language query AND compute the result in one shot.

    Embedding-based retrieval (backend/nlp/retriever.py) shortlists candidate
    tables/columns and filters them to what ctx's department is already
    allowed to access, then an LLM (backend/nlp/intent_resolver.py) picks the
    best match from that authorized shortlist only. backend/nlp/date_resolver.py
    then turns any date/period intent in the query (or its absence, defaulting
    to the latest actual submission) into a concrete reporting_date/period, and
    this route calls the existing, unmodified service.compute_variance() with
    it — the same call /variance/compute makes — so the NLP bar shows a real
    computed result immediately, with no extra manual date entry/click.
    """
    # Imported here rather than at module scope so the ~1.3GB embedding model
    # and FAISS aren't pulled in by the non-NLP routes. The cost is that a
    # missing NLP dependency surfaces per-request instead of at startup, so
    # name the culprit explicitly — this is how a missing rank-bm25 /
    # faiss-cpu / sentence-transformers on a fresh deployment shows up.
    try:
        from ..nlp import schema_info
        from ..nlp.retriever import get_relevant_schema
        from ..nlp.intent_resolver import resolve_intent
        from ..nlp.date_resolver import resolve_reporting_date
        from ..nlp.query_analyzer import analyze_query
        from ..nlp.nlp_config import CONFIDENCE_ASK_FLOOR, CONFIDENCE_AUTO_PROCEED
    except Exception as exc:
        logger.error(
            "[api.nlp] /variance/nlresolve | %s | NLP module import FAILED | %s: %s "
            "| the NL path needs faiss-cpu, sentence-transformers and rank-bm25 "
            "(pip install -r requirements.txt) — check GET /variance/nlp-health",
            ctx, type(exc).__name__, exc, exc_info=True,
        )
        raise

    query = payload.query.strip()
    logger.info(
        "[api.nlp] POST /variance/nlresolve | %s | query=%r | dimension=%r | answer=%r",
        ctx, query, payload.dimension, payload.clarification_answer,
    )

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    # ── One end-to-end read of the query, up front ────────────────────────────
    # Everything the query STATES is pulled out here once and reused by every
    # stage below, instead of each stage re-scanning the raw string with its
    # own rules: which return it names (matched only against returns the
    # embedding index covers), the date/period phrase, the domestic/overseas
    # scope, and — what's left — the text that actually describes the metric.
    # Retrieval embeds that leftover rather than the whole sentence, so
    # "show me the variance in total loan assets for CIMS_RAQ as of 31-Mar-2025"
    # searches for "total loan assets" and treats the return and the date as
    # the exact facts they are. See backend/nlp/query_analyzer.py.
    allowed_ids = _allowed_return_ids(ctx)

    analysis = _nlp_stage(
        # _nlp_stage's own `ctx` parameter is for its logging only, so the ctx
        # the analyzer reads the repository with must go through *args like the
        # other stages below do — passing it as ctx=ctx would bind to
        # _nlp_stage itself and never reach analyze_query.
        "query_analysis", ctx, query,
        analyze_query, query, allowed_ids, ctx,
    )
    interpretation = analysis.to_interpretation()

    # ── The query names a return this pipeline cannot answer ─────────────────
    # Refuse, rather than falling through to unscoped retrieval. Measured
    # before this guard existed: "total number of staff for CIMS_ROR" answered
    # from CIMS_RAQ at confidence 0.94, and "CRILC borrower details" from
    # CIMS_RAQ at 0.90 — confidently, about a completely different return. The
    # confidence gate cannot catch that class: the score is genuinely high, it
    # is just high about the wrong thing. Naming a return is an exact statement
    # of intent, so the only honest answers are "here it is" or "I can't".
    if analysis.unindexed_return_names and not analysis.return_ids:
        named = analysis.unindexed_return_names[0]
        logger.info(
            "[api.nlp] /variance/nlresolve | %s | query=%r | names return %r which has "
            "no embeddings -> refusing rather than answering from another return",
            ctx, query, named,
        )
        return {
            "needs_clarification": True,
            "dimension": "return",
            "question": (
                f"{named} has not been indexed for natural-language search yet, so I "
                f"can't answer questions about it here. Use the Return/Table/Date "
                f"controls above for {named}, or ask me about one of these instead:"
            ),
            "options": _build_return_clarification(query, ctx)["options"],
            "skippable": False,
            "allow_other": False,
            "confidence": 0.0,
            "resolved_context": {"query": query},
            "interpretation": interpretation,
        }

    resolved_context = payload.resolved_context or {}
    pinned_return_id = resolved_context.get("return_id")
    answer = payload.clarification_answer

    # Clarification ids come back from the browser, so check their SHAPE before
    # they reach any lookup or path (code review SEC-09). Real option ids are
    # return ids and table names, which always pass; "__skip__" is handled below.
    try:
        if pinned_return_id:
            ensure_safe_return_id(pinned_return_id)
        if answer and answer != _SKIP_ANSWER:
            if payload.dimension == "return":
                ensure_safe_return_id(answer)
            elif payload.dimension == "table":
                ensure_safe_table_ref(answer)
            elif payload.dimension == "date" and answer != _LATEST_DATE_ANSWER and not _DATE_ANSWER_RE.match(answer):
                raise InvalidInput("Invalid reporting date.")
        if resolved_context.get("table"):
            ensure_safe_table_ref(resolved_context["table"])
    except InvalidInput as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.dimension == "return" and answer:
        if answer == _SKIP_ANSWER:
            # No return picked — best-effort: fall back to whatever the
            # free-form retrieval found, even below the confidence floor
            # that originally triggered this prompt. If it found literally
            # nothing, there's nothing to guess with.
            shortlist = _nlp_stage("retrieval", ctx, query, get_relevant_schema, query, ctx, analysis)
            if not shortlist["tables"]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Still couldn't find a matching return/table for this query — please add more detail.",
                )
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | return clarification skipped -> best-effort guess",
                ctx, query,
            )
        else:
            shortlist = _nlp_stage(
                "scoped_retrieval", ctx, query,
                _shortlist_for_return, answer, query, ctx, analysis=analysis,
            )
            if shortlist is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected return is no longer available.")
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | return answered -> return_id=%s (%d table(s))",
                ctx, query, answer, len(shortlist["tables"]),
            )
            if shortlist["table_ambiguous"]:
                return {
                    **_build_table_clarification(
                        query, shortlist, shortlist["table_confidence"], return_id=answer,
                    ),
                    "interpretation": interpretation,
                }

    elif payload.dimension == "table" and answer:
        if answer == _SKIP_ANSWER:
            # No specific table picked — best-effort: let resolve_intent's
            # LLM choose freely from the (still-ambiguous) shortlist, scoped
            # to whichever return was already pinned if one was.
            shortlist = (
                _nlp_stage(
                    "scoped_retrieval", ctx, query,
                    _shortlist_for_return, pinned_return_id, query, ctx, analysis=analysis,
                ) if pinned_return_id
                else _nlp_stage("retrieval", ctx, query, get_relevant_schema, query, ctx, analysis)
            )
            if shortlist is None or not shortlist["tables"]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No accessible return/table matches this query.",
                )
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | table clarification skipped -> best-effort guess",
                ctx, query,
            )
        else:
            shortlist = _nlp_stage(
                "scoped_retrieval", ctx, query,
                _shortlist_for_table, answer, query, ctx, analysis=analysis,
            )
            if shortlist is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Selected option is no longer valid for this query.",
                )
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | table answered -> table=%s",
                ctx, query, answer,
            )

    elif payload.dimension == "date" and answer:
        # Answering "which date?": the return and table were settled on the
        # previous round and echoed back in resolved_context — re-pin them so
        # nothing is re-guessed. Only the date changes.
        pinned_table = resolved_context.get("table")
        shortlist = (
            _nlp_stage("scoped_retrieval", ctx, query, _shortlist_for_table, pinned_table, query, ctx, analysis=analysis)
            if pinned_table else None
        )
        if shortlist is None and pinned_return_id:
            shortlist = _nlp_stage(
                "scoped_retrieval", ctx, query,
                _shortlist_for_return, pinned_return_id, query, ctx, analysis=analysis,
            )
        if shortlist is None or not shortlist["tables"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The selected data is no longer available — please ask again.",
            )

    else:
        # First pass for this query — no clarification answer yet.
        # The query names a return, but the mention fits more than one — most
        # often the same return in several reporting-frequency or
        # domestic/overseas variants (e.g. "ALE" matching both
        # CIMS_ALE_Domestic(Quarterly) and CIMS_ALE Oversease(Quarterly)).
        # Asked BEFORE retrieval's own confidence gate because picking wrong
        # here is silent and consequential: report_freq drives
        # get_previous_dates(), so the wrong variant computes comparison
        # periods at the wrong interval and returns confidently wrong numbers.
        # The options are exactly the variants the query matched, so this is a
        # two- or three-item question, not a browse through the full list.
        if len(analysis.return_ids) > 1:
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | names %d return "
                "variants %s -> asking which",
                ctx, query, len(analysis.return_ids), analysis.return_names,
            )
            return {
                **_build_return_clarification(query, ctx, restrict_to=analysis.return_ids),
                "interpretation": interpretation,
            }

        # The query names exactly one return: search ONLY that return's
        # embeddings. Free-form retrieval would rank every return's tables and
        # can still answer from another return when the named one scores low.
        # Ambiguity left inside the return is asked about below, scoped to it.
        scoped_return_id = analysis.return_ids[0] if len(analysis.return_ids) == 1 else None
        shortlist = None
        if scoped_return_id:
            shortlist = _nlp_stage(
                "scoped_retrieval", ctx, query,
                _shortlist_for_return, scoped_return_id, query, ctx, analysis=analysis,
            )
            if shortlist is not None and shortlist["tables"]:
                logger.info(
                    "[api.nlp] /variance/nlresolve | %s | query=%r | names return_id=%s "
                    "-> searching only its %d table(s)",
                    ctx, query, scoped_return_id, len(shortlist["tables"]),
                )
                if shortlist["table_ambiguous"]:
                    return {
                        **_build_table_clarification(
                            query, shortlist, shortlist["table_confidence"], return_id=scoped_return_id,
                        ),
                        "interpretation": interpretation,
                    }
            else:
                shortlist = None

        if shortlist is None:
            shortlist = _nlp_stage("retrieval", ctx, query, get_relevant_schema, query, ctx, analysis)
            table_confidence = shortlist.get("table_confidence", 0.0)
            table_ambiguous = shortlist.get("table_ambiguous", False)

            weak_retrieval = not shortlist["tables"] or table_confidence < CONFIDENCE_ASK_FLOOR
            if weak_retrieval:
                # Retrieval alone isn't confident there's a real match — before
                # asking a broad "which return?" question, check whether the
                # query TEXT already names a known return explicitly. A literal
                # name mention is stronger evidence than a coincidental (or
                # coincidentally absent) embedding score.
                named_return_ids = analysis.return_ids

                if len(named_return_ids) == 1:
                    named_shortlist = _nlp_stage(
                        "scoped_retrieval", ctx, query,
                        _shortlist_for_return, named_return_ids[0], query, ctx,
                        analysis=analysis,
                    )
                    if named_shortlist is not None:
                        logger.info(
                            "[api.nlp] /variance/nlresolve | %s | query=%r | "
                            "query names return_id=%s directly -> using it instead of asking",
                            ctx, query, named_return_ids[0],
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
                        "[api.nlp] /variance/nlresolve | %s | query=%r | no usable table/return signal "
                        "(table_confidence=%.3f, query_related_return_ids=%s) -> asking for return",
                        ctx, query, table_confidence, query_related_return_ids,
                    )
                    return {
                        **_build_return_clarification(
                            query, ctx,
                            restrict_to=query_related_return_ids or None,
                        ),
                        "interpretation": interpretation,
                    }

            twins = _twin_return_ids(shortlist)
            if twins:
                logger.info(
                    "[api.nlp] /variance/nlresolve | %s | query=%r | top table has identical twins "
                    "in returns %s -> asking which return",
                    ctx, query, twins,
                )
                return {
                    **_build_return_clarification(query, ctx, restrict_to=twins),
                    "interpretation": interpretation,
                }

            if table_ambiguous or table_confidence < CONFIDENCE_AUTO_PROCEED:
                logger.info(
                    "[api.nlp] /variance/nlresolve | %s | query=%r | table ambiguous "
                    "(confidence=%.3f, tied=%s) -> asking clarification",
                    ctx, query, table_confidence, table_ambiguous,
                )
                return {
                    **_build_table_clarification(query, shortlist, table_confidence),
                    "interpretation": interpretation,
                }

    # Default to 1.0 for shortlists this route itself pinned down to exactly
    # one table (_shortlist_for_return/_shortlist_for_table, or the "return"
    # skip's best-effort guess) — those didn't go through the ambiguity
    # scoring above, so there's no lower number to report here.
    final_confidence = shortlist.get("table_confidence", 1.0)

    # sql_agent's H-02 order: authorize the tables the LLM will pick from before
    # any LLM call, whichever branch (free-form or client-named) built them.
    require_tables_access(ctx, shortlist["tables"])

    resolution = _nlp_stage(
        "intent_resolution", ctx, query, resolve_intent, query, shortlist,
        analysis=analysis,
    )
    if resolution is None:
        logger.warning("[api.nlp] 404 /variance/nlresolve | %s | query=%r | intent resolution failed", ctx, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Could not resolve this query to a known table/column.",
        )

    # Final authorization gate, whichever branch produced the shortlist. The
    # free-form retriever filters to allowed returns, but the clarification
    # branches above build their shortlist from client-supplied ids, and
    # compute_variance does no access check of its own.
    require_return_access(ctx, resolution["return_id"])

    return_row = _return_row(resolution["return_id"], ctx)
    if return_row is None:
        logger.warning("[api.nlp] 404 /variance/nlresolve | %s | resolved return_id=%s not found", ctx, resolution["return_id"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resolved return not found.")

    found = service.find_return_and_tables(return_row.get("Name", ""), ctx)
    if result_error(found) or found.get("candidates") or not found.get("table_mapping_path"):
        logger.warning("[api.nlp] 404 /variance/nlresolve | %s | return_name=%r | table mapping unresolved", ctx, return_row.get("Name"))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resolved return could not be mapped to a table-mapping file.",
        )
    # compute_variance runs against found["return_id"], which is re-derived by
    # name search above, so gate the id that is actually used.
    if found["return_id"] != resolution["return_id"]:
        require_return_access(ctx, found["return_id"])

    resolved_table = next(
        (t for t in found.get("tables", []) if (t.get("table_name") or "").upper() == resolution["table_name"].upper()),
        None,
    )
    if resolved_table is None:
        # NOT an error: find_return_and_tables() derives its table list from
        # the return's table-mapping XML, but backend/nlp/return_lookup.py
        # deliberately also registers tables from XML_Query.xml for returns
        # whose mapping file is missing or ships rows with empty TableName
        # attributes (the CIMS_ALE family). Every table resolved through that
        # fallback is absent from the mapping list BY DEFINITION, so gating on
        # it here 404'd them unconditionally — measured at 21 of 25 tables
        # reachable via the xml_query source.
        #
        # Nothing downstream actually needs the mapping row: only
        # resolved_table["table_name"] is ever read, filter_col/report_freq
        # come from the retrieval metadata above, and service's
        # _get_table_metadata() carries the very same XML_Query.xml fallback
        # (added when this bit /variance/dates and compute_variance). So
        # carry the resolved name forward and let that fallback do its job —
        # if it has no entry either, compute_variance raises FileNotFoundError
        # and the existing handler turns it into a 404 with a real reason.
        logger.info(
            "[api.nlp] /variance/nlresolve | %s | table=%r not in return %s's "
            "table mapping — proceeding via XML_Query.xml fallback",
            ctx, resolution["table_name"], found["return_id"],
        )
        resolved_table = {"table_name": resolution["table_name"]}

    # filter_col/report_freq were already resolved live during retrieval
    # (backend/nlp/return_lookup.py) — reuse them rather than re-looking up.
    shortlist_table_meta = next(
        (t for t in shortlist["tables"] if t["table"].upper() == resolution["table_name"].upper()), {}
    )
    filter_col = shortlist_table_meta.get("filter_col") or "RDATE"
    report_freq = shortlist_table_meta.get("report_freq") or found.get("report_freq") or "M"

    # No date in the query: ask which one, before showing anything. Skipping
    # (or picking "latest") gives the latest date vs the previous available one.
    from ..nlp.nlp_config import ASK_DATE_WHEN_MISSING, DATE_OPTIONS_LIMIT

    from ..nlp.nlp_config import STRICT_PERIODS

    date_answer = answer if payload.dimension == "date" else None
    from ..nlp.date_resolver import _candidate_dates, query_mentions_date

    # A follow-up turn reads its dates from its own text (or keeps the previous
    # turn's dates), not from the whole conversation.
    dq = date_query if date_query is not None else query
    date_stated = (query_mentions_date(dq) if date_query is not None
                   else (analysis.has_date_intent or query_mentions_date(query)))

    if (ASK_DATE_WHEN_MISSING and date_answer is None and dates_override is None
            and not date_stated):

        cands = _nlp_stage(
            "date_candidates", ctx, query,
            _candidate_dates, found["return_id"], resolved_table["table_name"], filter_col, ctx,
        )
        if len(cands) > 1:
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | query=%r | no date in query -> asking which (%d available)",
                ctx, query, len(cands),
            )
            return {
                **_build_date_clarification(
                    query, cands, found["return_id"], resolved_table["table_name"], DATE_OPTIONS_LIMIT,
                ),
                "interpretation": interpretation,
            }
    anchor_override = (
        date_answer if date_answer and date_answer not in (_SKIP_ANSWER, _LATEST_DATE_ANSWER) else None
    )

    try:
        if dates_override and not date_stated:
            # Follow-up with no date of its own: the previous turn's periods.
            reporting_date, reporting_period = dates_override[0], max(len(dates_override) - 1, 1)
            comparison_dates, date_notes = list(dates_override), []
        else:
            reporting_date, reporting_period, comparison_dates, date_notes = resolve_reporting_date(
                dq, found["return_id"], resolved_table["table_name"], filter_col, report_freq,
                ctx=ctx, anchor_override=anchor_override,
                **({"strict": True} if STRICT_PERIODS and date_stated and not anchor_override else {}),
            )
    except ValueError as exc:
        logger.warning("[api.nlp] 404 /variance/nlresolve | %s | date resolution failed: %s", ctx, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # A trend needs the whole window of dates, not the usual current + previous.
    from ..nlp.nlp_config import TREND_ENABLED, TREND_MAX_PERIODS
    try:
        pre_spec = spec_override if spec_override is not None else parse_operation(query)
    except Exception:
        pre_spec = None
    if TREND_ENABLED and pre_spec is not None and pre_spec.operation == "trend" and not anchor_override:
        from ..nlp.date_resolver import resolve_series_dates
        try:
            series_dates, series_notes = resolve_series_dates(
                dq, found["return_id"], resolved_table["table_name"], filter_col, report_freq,
                ctx=ctx, max_periods=TREND_MAX_PERIODS,
                strict=bool(STRICT_PERIODS and date_stated),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if len(series_dates) > 1:
            comparison_dates, reporting_date = series_dates, series_dates[0]
            reporting_period = len(series_dates) - 1
            date_notes = list(series_notes)

    logger.info(
        "[api.nlp] POST /variance/nlresolve | %s | query=%r | return_id=%s | table=%s | "
        "columns=%s | reporting_date=%s | reporting_period=%s",
        ctx, query, found["return_id"], resolved_table["table_name"],
        resolution["selected_columns"], reporting_date, reporting_period,
    )

    def _compute(dates: list) -> dict:
        # selected_columns is deliberately NOT passed here — that parameter
        # also drives compute_variance's SQL SELECT list, so restricting it
        # to just the asked-for column would silently drop the filter_col/
        # identifier columns row-matching needs (confirmed: it causes a
        # false "no data found" once the SELECT no longer includes RDATE).
        # Instead we let it compute every numeric column exactly like the
        # manual wizard does, then restrict the RESULT to the asked-for
        # column(s) below via _restrict_result_columns.
        return service.compute_variance(
            return_id=found["return_id"],
            table_name=resolved_table["table_name"],
            reporting_date=dates[0],
            reporting_period=reporting_period,
            execute_query_fn=execute_query,
            connection_string=None,
            selected_columns=None,
            # Real dates from the table (date_resolver), newest first.
            comparison_dates=dates,
            ctx=ctx,
        )

    try:
        computed = _compute(comparison_dates)
        # The anchor exists in the date list but its rows can still come back
        # empty (e.g. a filter on the physical table). One retry on the next
        # real date keeps the query answering instead of dead-ending.
        # A period the user named is never swapped for another (strict periods):
        # "no data for what you asked" is the honest answer.
        if (is_no_data_error(result_error(computed)) and STRICT_PERIODS and date_stated
                and not anchor_override):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Data is not available for {comparison_dates[0]} in this table.",
            )
        if is_no_data_error(result_error(computed)) and len(comparison_dates) > 1:
            retry_dates = comparison_dates[1:]
            logger.info(
                "[api.nlp] /variance/nlresolve | %s | no rows for %s — retrying from %s",
                ctx, comparison_dates[0], retry_dates[0],
            )
            retried = _compute(retry_dates)
            if not result_error(retried):
                date_notes = list(date_notes) + [
                    f"No rows for {comparison_dates[0]}; showing {retry_dates[0]} instead."
                ]
                computed, comparison_dates, reporting_date = retried, retry_dates, retry_dates[0]
    except Exception as exc:
        raise http_error(exc, "/variance/nlresolve", ctx) from exc

    if result_error(computed):
        logger.warning("[api.nlp] 500 /variance/nlresolve | %s | compute_variance error: %s", ctx, result_error(computed))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result_error(computed))

    try:
        spec = spec_override if spec_override is not None else parse_operation(query)
    except Exception:
        logger.exception("[api.nlp] /variance/nlresolve | %s | operation parse failed; returning plain variance", ctx)
        spec = None

    from ..nlp.nlp_config import STRICT_NAMES, TREND_ENABLED

    # "The trend of Assets": a column when one matches, otherwise an element.
    if spec is not None and spec.target_any:
        from ..nlp.analysis import resolve_target_columns
        hit, _missing = resolve_target_columns(
            spec.target_any, computed.get("columns") or [],
            lambda c: schema_info.column_description(resolved_table["table_name"], c))
        if hit:
            spec.target_columns = list(spec.target_any)
        else:
            spec.target_elements = list(spec.target_any)
        spec.target_any = []

    # Columns the user NAMED ("the Asset column", "Assets, Liabilities and Capital")
    # win over the resolver's pick; a name matching nothing is said, not dropped.
    if spec is not None and spec.target_columns:
        try:
            before = len(spec.notes)
            picked = _target_columns(
                computed, spec, [], resolved_table["table_name"])
        except Exception:
            logger.exception("[api.nlp] /variance/nlresolve | %s | column targeting failed; keeping resolver columns", ctx)
            picked, before = [], len(spec.notes)
        if picked:
            resolution["selected_columns"] = picked
        elif STRICT_NAMES and len(spec.notes) > before:
            # Every column the user named is unknown: refuse rather than answer
            # about columns they did not ask for.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=" ".join(spec.notes[before:]))

    # "All monetary / numeric columns": every numeric column, not the resolver's pick.
    if spec is not None and spec.all_columns and not spec.target_columns:
        resolution["selected_columns"] = list(computed.get("columns") or resolution["selected_columns"])

    computed = _restrict_result_columns(computed, resolution["selected_columns"])

    # What the user wants DONE with the numbers (filter, rank, unchanged, new/removed…).
    # Additive and fail-safe: an analysis problem must never cost the user their table.
    try:
        if spec is not None and spec.operation == "trend":
            if TREND_ENABLED:
                from ..nlp.series import analyze_series
                computed = analyze_series(computed, spec, comparison_dates)
        elif spec is not None:
            computed = analyze_result(computed, spec)
    except Exception:
        logger.exception("[api.nlp] /variance/nlresolve | %s | analysis stage failed; returning plain variance", ctx)

    logger.info(
        "[api.nlp] 200 /variance/nlresolve | %s | return_id=%s | table=%s | rows=%d",
        ctx, found["return_id"], resolved_table["table_name"], len(computed.get("rows", [])),
    )

    return {
        **computed,
        "return_id":          found["return_id"],
        "return_name":        found["return_name"],
        "report_freq":        report_freq,
        # File name only: the browser echoes it back but the server ignores it, so
        # the absolute repository path was pure disclosure (code review SEC-06).
        "table_mapping_path": os.path.basename(str(found["table_mapping_path"]).replace("\\", "/")),
        "confidence":         round(final_confidence, 3),
        # "llm" when the model's choice was used, "fallback" when the deterministic
        # resolver stood in (code review M-41). Additive.
        "resolution_mode":    resolution.get("mode"),
        # What the query was understood to mean, echoed back so the UI can
        # show it rather than the resolution being a black box the user can
        # only judge by whether the numbers look right (frontend:
        # ControlBar's NlpInterpretation). Also names the resolved column(s),
        # which the analyzer can't know until intent resolution has run.
        "interpretation": {
            **interpretation,
            "resolved_columns": resolution["selected_columns"],
            # The human labels for those columns, from schema.json. End users
            # have no knowledge of the schema behind a return, so the UI shows
            # these ("Total Loan Assets") rather than the identifiers
            # ("TOTAL_LOAN_ASSETS"). Falls back to the identifier when a column
            # has no description, so the field is never empty.
            "resolved_column_labels": [
                schema_info.column_description(resolved_table["table_name"], col) or col
                for col in resolution["selected_columns"]
            ],
            "reporting_date":   reporting_date,
            # What was asked for vs what was actually compared, and why they
            # differ — so a substituted date is never silent.
            "comparison_periods": reporting_period,
            "requested_periods":  reporting_period,
            "used_periods":       max(len(comparison_dates) - 1, 0),
            "comparison_dates":   comparison_dates,
            "date_notes":         date_notes,
        },
        # Where every number came from, so a result can be checked by hand.
        "provenance": _provenance(computed, found, resolved_table["table_name"], comparison_dates),
    }


def _refinement_question(spec, query: str) -> dict | None:
    """Ask instead of guessing (scenario group 26): what 'highest variation' is
    measured by, what 'significant' means, which period 'Compare March' is against.
    None when nothing needs asking. Asked before any DB work."""
    from datetime import date as _date

    from ..hosts import get_profile
    from ..nlp import conversation as conv
    from ..nlp.nlp_config import ASK_MEASURE, ASK_THRESHOLD, SIGNIFICANT_PCT_DEFAULT

    if spec is None:
        return None
    if ASK_MEASURE and spec.operation == "rank" and spec.measure == "ambiguous":
        return {
            "dimension": "measure",
            "question": "Should the highest/lowest variation be measured by percentage change or by absolute change (amount)?",
            "options": conv.MEASURE_OPTIONS, "allow_other": False,
            "skip_label": "percentage change",
        }
    if ASK_THRESHOLD and spec.significant and spec.threshold is None:
        return {
            "dimension": "threshold",
            "question": (f"What threshold should count as a significant variation? Pick one, or type a "
                         f"percentage or an amount (e.g. 15% or 5 lakh). Skip to use more than "
                         f"{SIGNIFICANT_PCT_DEFAULT:g}%."),
            "options": conv.THRESHOLD_OPTIONS, "allow_other": True,
        }
    month = conv.needs_period_question(query, get_profile().fiscal_year_start_month, _date.today())
    if month:
        return {
            "dimension": "period",
            "question": f"Which period would you like to compare {month} against?",
            "options": conv.PERIOD_OPTIONS, "allow_other": False,
        }
    return None


@router.post(
    "/variance/nlresolve", status_code=status.HTTP_200_OK, tags=["Variance"],
    response_model=NlResolveResponse, response_model_exclude_unset=True,
)
def variance_nlresolve(
    payload: NLResolveRequest,
    ctx: RequestContext = Depends(require_login),
) -> dict:
    """The natural-language route: _nlresolve_core plus the conversation layer.

    Around the one-shot resolution this adds (all additive; switches in nlp_config):
      * refinement questions — measure / threshold / period — whose answers are
        appended to the query and carried in resolved_context["refine"];
      * follow-ups — with conversation_state from the previous answer, a turn like
        "only decreases" or "details of the first one" refines that answer on the
        same return, table and dates;
      * conversation_state on every answer, and drill_down for a drilled element.
    """
    from ..nlp import conversation as conv
    from ..nlp.nlp_config import CONVERSATION_ENABLED, SIGNIFICANT_PCT_DEFAULT

    typed = (payload.query or "").strip()
    rc = dict(payload.resolved_context or {})
    refine = conv.clean_refine(rc.get("refine"))
    dimension, answer = payload.dimension, payload.clarification_answer

    if dimension in ("measure", "threshold", "period"):
        try:
            refine.append(conv.refinement_text(dimension, answer, _SKIP_ANSWER, SIGNIFICANT_PCT_DEFAULT))
        except conv.InvalidAnswer as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        payload = NLResolveRequest(query=typed, conversation_state=payload.conversation_state)
        dimension = None

    state = conv.clean_state(payload.conversation_state) if CONVERSATION_ENABLED else None
    plan = None
    if state and typed and conv.is_followup(typed):
        plan = conv.plan_followup(typed, state)
        if plan["error"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=plan["error"])
    if plan is not None and state.get("table") and state.get("return_id") and not dimension:
        try:
            ensure_safe_return_id(state["return_id"])
            ensure_safe_table_ref(state["table"])
        except InvalidInput as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    query = conv.effective_query(plan["query"] if plan else typed, refine)
    spec = plan["spec"] if plan else (parse_operation(query) if typed else None)
    if refine and spec is not None and plan is not None:
        # A refinement answered inside a follow-up applies to the merged spec too.
        spec = conv._merge(spec, parse_operation(". ".join(refine)), ". ".join(refine))

    def _with_context(res: dict) -> dict:
        """Clarifications carry the refinements and the conversation forward."""
        if res.get("needs_clarification"):
            ctx_out = dict(res.get("resolved_context") or {})
            if refine:
                ctx_out["refine"] = refine
            res = {**res, "resolved_context": ctx_out}
            if state:
                res["conversation_state"] = payload.conversation_state
        return res

    if not dimension and typed:
        q = _refinement_question(spec, conv.effective_query(plan["date_query"] if plan else typed, refine))
        if q:
            return _with_context({
                "needs_clarification": True, "skippable": True, "confidence": 1.0,
                "resolved_context": {"query": typed}, **{k: v for k, v in q.items() if k != "skip_label"},
            })

    kwargs: dict = {"spec_override": spec}
    core_payload = payload.model_copy(update={"query": query})
    if plan is not None:
        kwargs["date_query"] = conv.effective_query(plan["date_query"], refine)
        kwargs["dates_override"] = state.get("dates") or None
        if plan["pin_table"] and state.get("table") and not dimension:
            core_payload = NLResolveRequest(
                query=query, dimension="table", clarification_answer=state["table"],
                resolved_context={"query": query, "return_id": state.get("return_id")},
            )

    res = _nlresolve_core(core_payload, ctx, **kwargs)
    if res.get("needs_clarification"):
        return _with_context(res)

    turns = plan["turns"] if plan else [conv.effective_query(typed, refine)]
    focus = plan["focus"] if plan else None
    out = {**res, "conversation_state": conv.new_state(
        turns, res, res.get("return_id"), res.get("table_name") or (res.get("provenance") or {}).get("table"), focus)}
    if plan is not None:
        out["interpretation"] = {**(out.get("interpretation") or {}), "followup_of": state["turns"][-1]}
    if plan is not None and plan["drill"] and focus is not None:
        detail = conv.drill_down(res, focus)
        if detail:
            out["drill_down"] = detail
    return out


def _provenance(computed: dict, found: dict, table_name: str, comparison_dates: list) -> dict:
    from ..nlp.analysis import FORMULA_TEXT

    counts = (computed.get("analysis") or {}).get("counts") or {}
    return {
        "return_id": found.get("return_id"),
        "return_name": found.get("return_name"),
        "table": table_name,
        "current_date": comparison_dates[0] if comparison_dates else None,
        "previous_dates": list(comparison_dates[1:]),
        "rows_in_result": len(computed.get("rows") or []),
        "rows_compared": (computed.get("analysis") or {}).get("total_rows", len(computed.get("rows") or [])),
        "new_from_zero": counts.get("new"),
        "became_zero": counts.get("became_zero"),
        "removed_rows": len(computed.get("removed_rows") or []),
        "duplicate_keys": len(computed.get("duplicate_identifiers") or []),
        "truncated": bool(computed.get("truncated")),
        "formula": FORMULA_TEXT,
    }


# ── POST /variance/nlquery ─────────────────────────────────────────────────────
@router.post(
    "/variance/nlquery", status_code=status.HTTP_200_OK, tags=["Variance"],
    response_model=NlQueryResponse, response_model_exclude_unset=True,
)
def variance_nlquery(
    payload: NLResolveRequest,
    ctx: RequestContext = Depends(require_login),
) -> dict:
    """Free-form NL -> SQL -> execution, mirroring sql_agent's /api/query.

    Unlike /variance/nlresolve (which only ever lets the LLM pick names from
    an authorized shortlist, then reuses the existing compute_variance to
    build the SQL), this endpoint lets the LLM write the actual SQL text via
    backend/nlp/sql_generator.py — no period-comparison, no visualization,
    just raw query results. The two safety nets that make this narrower than
    sql_agent's own version: the shortlist is pre-filtered to ctx's
    authorized returns before the LLM ever sees a table name, and
    validate_sql() rejects anything outside that shortlist, any non-SELECT,
    and any DML/DDL keyword before backend/db.execute_query ever runs it.

    DISABLED BY DEFAULT (DV_NLQUERY_ENABLED=false): the shipped UI never calls
    this route, and LLM-authored SQL runs on the same Oracle account as every
    other query, so its regex validator is the only boundary (code review
    C-03). Enable it only together with a read-only DB user granted just the
    indexed tables.
    """
    from ..config.settings import NLQUERY_ENABLED

    if not NLQUERY_ENABLED:
        logger.warning("[api.nlp] 404 /variance/nlquery | %s | route disabled (DV_NLQUERY_ENABLED=false)", ctx)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    from ..nlp.retriever import get_relevant_schema
    from ..nlp.sql_generator import generate_sql

    query = payload.query.strip()
    logger.info("[api.nlp] POST /variance/nlquery | %s | query=%r", ctx, query)

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    from ..nlp.nlp_config import CONFIDENCE_ASK_FLOOR, CONFIDENCE_AUTO_PROCEED

    shortlist = get_relevant_schema(query, ctx)

    # Same confidence gate as /nlresolve (code review LLM-06): a question the
    # retriever cannot place with confidence is asked about, not sent to a
    # model that will write SQL for whatever table scored highest.
    table_confidence = shortlist.get("table_confidence", 0.0)
    if shortlist["tables"] and (
        shortlist.get("table_ambiguous", False) or table_confidence < CONFIDENCE_AUTO_PROCEED
    ) and table_confidence >= CONFIDENCE_ASK_FLOOR:
        return _build_table_clarification(query, shortlist, table_confidence)
    if shortlist["tables"] and table_confidence < CONFIDENCE_ASK_FLOOR:
        shortlist = {**shortlist, "tables": []}

    if not shortlist["tables"]:
        logger.warning("[api.nlp] 404 /variance/nlquery | %s | query=%r | no authorized table matched", ctx, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No accessible return/table matches this query.",
        )

    # Authorize the tables BEFORE any LLM call (sql_agent's H-02 order): the
    # retriever already pre-filters, but this route lets a model write SQL, so
    # the boundary is re-checked on exactly the tables that SQL may touch.
    require_tables_access(ctx, shortlist["tables"])

    # Relative phrases ("last quarter") resolve against the newest date the
    # top table actually has, not the wall clock — the data lags the calendar,
    # and a today-anchored range can select nothing at all.
    today_date = None
    top = shortlist["tables"][0]
    try:
        from ..nlp.date_resolver import _candidate_dates
        cands = _candidate_dates(top["return_id"], top["table"], top.get("filter_col") or "RDATE", ctx)
        today_date = cands[0] if cands else None
    except Exception as exc:
        logger.info("[api.nlp] /variance/nlquery | %s | no data date for %s (%s) — using today", ctx, top.get("table"), exc)

    result = generate_sql(
        query, shortlist["tables"], shortlist["columns"],
        today_date=today_date,
        matched_labels=shortlist.get("matched_labels"),
    )
    if not result.get("sql") or result.get("warnings"):
        logger.warning(
            "[api.nlp] 422 /variance/nlquery | %s | query=%r | warnings=%s",
            ctx, query, result.get("warnings"),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            # The warnings name tables and validator rules; they stay in the log.
            detail="Could not generate a valid SQL query for this question. Please rephrase it.",
        )

    logger.info(
        "[api.nlp] /variance/nlquery generated SQL | %s | query=%r | sql=%s",
        ctx, query, result["sql"],
    )

    columns, rows, err = execute_query(result["sql"])  # validated by generate_sql
    if err:
        logger.error("[api.nlp] 500 /variance/nlquery | %s | sql=%s | %s", ctx, result["sql"], err)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=server_error_detail())

    logger.info("[api.nlp] 200 /variance/nlquery | %s | rows=%d", ctx, len(rows))
    response = {"columns": columns, "rows": rows}
    # The generated SQL reveals schema and validator behaviour: only on request
    # of the operator (DV_NLQUERY_RETURN_SQL=true), never to a normal client.
    if cfg.NLQUERY_RETURN_SQL:
        response["sql"] = result["sql"]
    return response
