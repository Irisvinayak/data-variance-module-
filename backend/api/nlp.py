# nlp.py — the natural-language path: /variance/nlresolve, /variance/nlquery
#
# Split out of backend/main.py, which had grown to ~1400 lines holding every
# route. Route bodies are unchanged; only the decorator and the imports moved.
# backend/main.py mounts this router, so the URLs are identical.

from __future__ import annotations

import logging

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth.deps import require_login
from ..config import RequestContext
from ..data import service
from ..data.db import execute_query
from ..data.models import NLResolveRequest
from ..data.report_lookup import parse_returns
from ..hosts import HostProfileError
from .errors import http_error

logger = logging.getLogger(__name__)

router = APIRouter()


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
    from ..auth.service import get_allowed_form_ids
    from ..config import AUTH_ENABLED

    from ..nlp import indexed_returns

    returns = list(parse_returns(ctx))
    if AUTH_ENABLED:
        allowed = get_allowed_form_ids(ctx) or set()
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
    from ..nlp.index_store import meta_by_table
    from ..nlp.nlp_config import SCOPED_RETRIEVAL_ENABLED, TABLE_INDEX_PATH, TABLE_META_PATH
    from ..nlp.scoped_retriever import rank_within_tables

    return_row = next((r for r in parse_returns(ctx) if r.get("Id") == return_id), None)
    if return_row is None:
        return None

    table_names = indexed_returns.tables_for_return(return_id, ctx)
    if not table_names:
        logger.warning(
            "[main] _shortlist_for_return | return_id=%s | the embedding index covers "
            "none of this return's tables — nothing to resolve against",
            return_id,
        )
        return None

    # Per-table metadata (filter_col / report_freq) resolved the same way
    # retriever.py resolves it, with the index text as the disambiguation hint
    # — table names are not unique across returns and the hint is what stops a
    # Quarterly table resolving to its Annually sibling, which would compute
    # comparison periods a year apart on quarterly data.
    hint_records = meta_by_table(TABLE_INDEX_PATH, TABLE_META_PATH)
    tables: list = []
    for name in table_names:
        hint = " ".join(r.get("text", "") for r in hint_records.get(name.upper(), [])) or None
        ret = return_lookup.get_return_for_table(name, hint_text=hint, ctx=ctx) or {}
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
    from ..nlp.index_store import meta_by_table
    from ..nlp.nlp_config import (
        SCOPED_RETRIEVAL_ENABLED, TABLE_INDEX_PATH, TABLE_META_PATH,
    )
    from ..nlp.scoped_retriever import rank_within_tables

    # Resolve the return WITH the table's own index metadata as a hint —
    # table names are not unique across returns, and without the hint a
    # shared table (e.g. one used by both the Quarterly and Annual variant of
    # a return) resolves by fallback rules to whichever claimant sorts first,
    # which can carry the wrong report_freq into compute_variance. Same call
    # shape retriever.py uses. See return_lookup._select_candidate.
    hint_records = meta_by_table(TABLE_INDEX_PATH, TABLE_META_PATH).get(table_name.upper(), [])
    hint_text = " ".join(r.get("text", "") for r in hint_records) or None

    ret = return_lookup.get_return_for_table(table_name, hint_text=hint_text, ctx=ctx)
    if not ret or not ret.get("return_id"):
        return None

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
            "[main] /variance/nlresolve | %s | query=%r | stage=%s FAILED | %s: %s",
            ctx, query, stage, type(exc).__name__, exc, exc_info=True,
        )
        raise


# ── POST /variance/nlresolve ───────────────────────────────────────────────────
@router.post("/variance/nlresolve", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_nlresolve(
    payload: NLResolveRequest,
    ctx: RequestContext = Depends(require_login),
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
            "[main] /variance/nlresolve | %s | NLP module import FAILED | %s: %s "
            "| the NL path needs faiss-cpu, sentence-transformers and rank-bm25 "
            "(pip install -r requirements.txt) — check GET /variance/nlp-health",
            ctx, type(exc).__name__, exc, exc_info=True,
        )
        raise

    query = payload.query.strip()
    logger.info(
        "[main] POST /variance/nlresolve | %s | query=%r | dimension=%r | answer=%r",
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
    from ..auth.service import get_allowed_form_ids as _get_allowed
    from ..config import AUTH_ENABLED as _auth_on

    # None (not an empty set) when auth is bypassed: analyze_query reads None as
    # "no auth scoping" and an empty set as "this user may access nothing".
    _allowed_ids = (_get_allowed(ctx) or set()) if _auth_on else None

    analysis = _nlp_stage(
        # _nlp_stage's own `ctx` parameter is for its logging only, so the ctx
        # the analyzer reads the repository with must go through *args like the
        # other stages below do — passing it as ctx=ctx would bind to
        # _nlp_stage itself and never reach analyze_query.
        "query_analysis", ctx, query,
        analyze_query, query, _allowed_ids, ctx,
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
            "[main] /variance/nlresolve | %s | query=%r | names return %r which has "
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
                "[main] /variance/nlresolve | %s | query=%r | return clarification skipped -> best-effort guess",
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
                "[main] /variance/nlresolve | %s | query=%r | return answered -> return_id=%s (%d table(s))",
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
                "[main] /variance/nlresolve | %s | query=%r | table clarification skipped -> best-effort guess",
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
                "[main] /variance/nlresolve | %s | query=%r | table answered -> table=%s",
                ctx, query, answer,
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
                "[main] /variance/nlresolve | %s | query=%r | names %d return "
                "variants %s -> asking which",
                ctx, query, len(analysis.return_ids), analysis.return_names,
            )
            return {
                **_build_return_clarification(query, ctx, restrict_to=analysis.return_ids),
                "interpretation": interpretation,
            }

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
                        "[main] /variance/nlresolve | %s | query=%r | "
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
                    "[main] /variance/nlresolve | %s | query=%r | no usable table/return signal "
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

        if table_ambiguous or table_confidence < CONFIDENCE_AUTO_PROCEED:
            logger.info(
                "[main] /variance/nlresolve | %s | query=%r | table ambiguous "
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

    resolution = _nlp_stage(
        "intent_resolution", ctx, query, resolve_intent, query, shortlist,
        analysis=analysis,
    )
    if resolution is None:
        logger.warning("[main] 404 /variance/nlresolve | %s | query=%r | intent resolution failed", ctx, query)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Could not resolve this query to a known table/column.",
        )

    return_row = next(
        (r for r in parse_returns(ctx) if r.get("Id") == resolution["return_id"]), None
    )
    if return_row is None:
        logger.warning("[main] 404 /variance/nlresolve | %s | resolved return_id=%s not found", ctx, resolution["return_id"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resolved return not found.")

    found = service.find_return_and_tables(return_row.get("Name", ""), ctx)
    if found.get("error") or found.get("candidates") or not found.get("table_mapping_path"):
        logger.warning("[main] 404 /variance/nlresolve | %s | return_name=%r | table mapping unresolved", ctx, return_row.get("Name"))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resolved return could not be mapped to a table-mapping file.",
        )

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
            "[main] /variance/nlresolve | %s | table=%r not in return %s's "
            "table mapping — proceeding via %s fallback",
            ctx, resolution["table_name"], found["return_id"],
            "XML_Query.xml",
        )
        resolved_table = {"table_name": resolution["table_name"]}

    # filter_col/report_freq were already resolved live during retrieval
    # (backend/nlp/return_lookup.py) — reuse them rather than re-looking up.
    shortlist_table_meta = next(
        (t for t in shortlist["tables"] if t["table"].upper() == resolution["table_name"].upper()), {}
    )
    filter_col = shortlist_table_meta.get("filter_col") or "RDATE"
    report_freq = shortlist_table_meta.get("report_freq") or found.get("report_freq") or "M"

    try:
        reporting_date, reporting_period, comparison_dates = resolve_reporting_date(
            query, found["return_id"], resolved_table["table_name"], filter_col, report_freq,
            ctx=ctx,
        )
    except ValueError as exc:
        logger.warning("[main] 404 /variance/nlresolve | %s | date resolution failed: %s", ctx, exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    logger.info(
        "[main] POST /variance/nlresolve | %s | query=%r | return_id=%s | table=%s | "
        "columns=%s | reporting_date=%s | reporting_period=%s",
        ctx, query, found["return_id"], resolved_table["table_name"],
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
            # Real dates from the table when the resolver found any, so the
            # comparison lands on rows that exist. Empty list -> omitted, and
            # compute_variance falls back to deriving them from reporting_period
            # exactly as before.
            comparison_dates=comparison_dates or None,
            ctx=ctx,
        )
    except Exception as exc:
        raise http_error(exc, "/variance/nlresolve", ctx) from exc

    if computed.get("error"):
        logger.warning("[main] 500 /variance/nlresolve | %s | compute_variance error: %s", ctx, computed["error"])
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=computed["error"])

    computed = _restrict_result_columns(computed, resolution["selected_columns"])

    logger.info(
        "[main] 200 /variance/nlresolve | %s | return_id=%s | table=%s | rows=%d",
        ctx, found["return_id"], resolved_table["table_name"], len(computed.get("rows", [])),
    )

    return {
        **computed,
        "return_id":          found["return_id"],
        "return_name":        found["return_name"],
        "report_freq":        report_freq,
        "table_mapping_path": found["table_mapping_path"],
        "confidence":         round(final_confidence, 3),
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
            "comparison_periods": reporting_period,
        },
    }


# ── POST /variance/nlquery ─────────────────────────────────────────────────────
@router.post("/variance/nlquery", status_code=status.HTTP_200_OK, tags=["Variance"])
async def variance_nlquery(
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
    """
    from ..nlp.retriever import get_relevant_schema
    from ..nlp.sql_generator import generate_sql

    query = payload.query.strip()
    logger.info("[main] POST /variance/nlquery | %s | query=%r", ctx, query)

    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query must not be empty.")

    shortlist = get_relevant_schema(query, ctx)
    if not shortlist["tables"]:
        logger.warning("[main] 404 /variance/nlquery | %s | query=%r | no authorized table matched", ctx, query)
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
            "[main] 422 /variance/nlquery | %s | query=%r | warnings=%s",
            ctx, query, result.get("warnings"),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not generate a valid SQL query: {result.get('warnings')}",
        )

    logger.info(
        "[main] /variance/nlquery generated SQL | %s | query=%r | sql=%s",
        ctx, query, result["sql"],
    )

    columns, rows, err = execute_query(result["sql"])
    if err:
        logger.error("[main] 500 /variance/nlquery | %s | sql=%s | %s", ctx, result["sql"], err)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err)

    logger.info("[main] 200 /variance/nlquery | %s | rows=%d", ctx, len(rows))
    return {"sql": result["sql"], "columns": columns, "rows": rows}
