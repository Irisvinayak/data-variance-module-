"""Runs the REAL 6.0 NLP pipeline against the shipped QCB index and prints one JSON document.

Not a test module (leading underscore). tests/nlp/test_qcb_returns_60.py launches it in a
subprocess with VERSION=6.0, because settings.py reads VERSION once at import and the repo's
.env pins 5.5 - a fresh interpreter is the only way to get genuine 6.0 behaviour regardless of
what other tests imported first. Oracle and the LLM are never touched: retrieval only reads the
FAISS/BM25 files and the tenant's XML, and the route is stopped at intent resolution.
"""
from __future__ import annotations

import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import HTTPException  # noqa: E402

from backend.api import nlp as nlp_api  # noqa: E402
from backend.auth import deps as auth_deps  # noqa: E402
from backend.auth import service as auth_service  # noqa: E402
from backend.config import RequestContext, settings  # noqa: E402
from backend.data.models import NLResolveRequest  # noqa: E402
from backend.nlp import (  # noqa: E402
    indexed_returns, intent_resolver, nlp_config, retriever, return_lookup,
)
from backend.nlp.query_analyzer import analyze_query  # noqa: E402

LOGIN = os.environ["QCB_PROBE_LOGIN"]
TENANT = os.environ.get("QCB_PROBE_TENANT", "1001")
CTX = RequestContext(login_id=LOGIN, tenant_id=TENANT)

TABLES = [
    "qcb_f010_filing_info", "qcb_f010_rcm_in_qtr", "qcb_f010_rcm_out_qtr",
    "qcb_f013_filing_info", "qcb_f013_break_exposure",
    "qcb_f014_filing_info", "qcb_f014_funding_geo",
    "qcb_f015_filing_info", "qcb_f015_foreign_cur_nop", "qcb_f015_bank_cap_base",
]
QUERIES = [
    "F010", "show maturity ladder", "assets maturing overnight", "country wise funding",
    "net open position in USD", "breakdown of exposures by geography",
    "forex exposure limit", "total staff",
]


class _Stop(Exception):
    """Raised by the stand-in for resolve_intent so the route stops BEFORE the LLM/Oracle."""

    def __init__(self, shortlist):
        self.shortlist = shortlist


def _stub_intent(query, shortlist, analysis=None):
    raise _Stop(shortlist)


def _restrict(allowed):
    """Pretend the caller's department allows only `allowed` (every place the pipeline reads it)."""
    fn = lambda ctx=None: set(allowed)  # noqa: E731
    for mod in (auth_service, auth_deps, retriever, nlp_api):
        if hasattr(mod, "get_allowed_form_ids"):
            mod.get_allowed_form_ids = fn


def _route(query, dimension=None, answer=None, resolved_context=None):
    payload = NLResolveRequest(
        query=query, dimension=dimension, clarification_answer=answer, resolved_context=resolved_context,
    )
    try:
        out = nlp_api.variance_nlresolve(payload, CTX)
        return {"kind": "response", "body": out}
    except _Stop as stop:
        return {"kind": "reached_intent", "tables": [t["table"] for t in stop.shortlist["tables"]],
                "return_ids": sorted({str(t.get("return_id")) for t in stop.shortlist["tables"]})}
    except HTTPException as exc:
        return {"kind": "http", "status": exc.status_code}


def main() -> dict:
    out: dict = {"version": settings.APP_VERSION, "index_dir": nlp_config.INDEX_DIR,
                 "auth_enabled": settings.AUTH_ENABLED}

    out["lookup"] = {t: (return_lookup.get_return_for_table(t, ctx=CTX) or {}).get("return_id") for t in TABLES}
    out["coverage"] = sorted(indexed_returns.indexed_return_ids(CTX))
    out["tables_by_return"] = {r: indexed_returns.tables_for_return(r, CTX) for r in out["coverage"]}
    out["allowed"] = sorted(auth_service.get_allowed_form_ids(CTX) or [])
    out["allowed_unknown_login"] = sorted(
        auth_service.get_allowed_form_ids(RequestContext(login_id="nobody@irisindia.net", tenant_id=TENANT)) or []
    )

    analysis, retrieval = {}, {}
    for q in QUERIES:
        a = analyze_query(q, None, CTX)
        analysis[q] = {"return_ids": a.return_ids, "unindexed": a.unindexed_return_names}
        r = retriever.get_relevant_schema(q, CTX, a)
        retrieval[q] = {
            "tables": [t["table"] for t in r["tables"]],
            "return_ids": [str(t.get("return_id")) for t in r["tables"]],
            "confidence": r["table_confidence"], "ambiguous": r["table_ambiguous"],
            "qa_match": (r.get("qa_match") or {}).get("table"),
        }
    out["analysis"], out["retrieval"] = analysis, retrieval

    # QA near-duplicate -> verified pair pins its table, and the fast path answers without an LLM.
    with open(nlp_config.QA_PAIRS_PATH, encoding="utf-8") as fh:
        qa = json.load(fh)
    import random
    rng = random.Random(11)
    qa_stats = {"sampled": 0, "matched": 0, "matched_right_table": 0, "matched_right_return": 0,
                "fast_ok": 0, "fast_wrong_table": 0, "fast_none": 0, "per_return": {}}
    guard = None
    for x in rng.sample(qa, 60):
        a = analyze_query(x["question"], None, CTX)
        r = retriever.get_relevant_schema(x["question"], CTX, a)
        qa_stats["sampled"] += 1
        m = r.get("qa_match")
        code = x["table"].split("_")[1].upper()
        pr = qa_stats["per_return"].setdefault(code, {"sampled": 0, "matched": 0, "fast_ok": 0})
        pr["sampled"] += 1
        if not m:
            continue
        qa_stats["matched"] += 1
        pr["matched"] += 1
        qa_stats["matched_right_table"] += m["table"].lower() == x["table"]
        qa_stats["matched_right_return"] += m["table"].split("_")[1] == x["table"].split("_")[1]
        fast = intent_resolver._resolve_from_qa(m, r)
        if fast is None:
            qa_stats["fast_none"] += 1
            continue
        qa_stats["fast_ok"] += 1
        pr["fast_ok"] += 1
        if fast["table_name"].lower() != x["table"]:
            qa_stats["fast_wrong_table"] += 1
        if guard is None:
            guard = {
                "question": x["question"], "table": x["table"], "return_id": fast["return_id"],
                "columns": fast["selected_columns"],
                # same verified pair, but its table is missing from the shortlist -> must NOT answer
                "table_not_in_shortlist": intent_resolver._resolve_from_qa(
                    m, {**r, "tables": [t for t in r["tables"] if t["table"].lower() != x["table"]]}),
                # a column the pair reads is not in the shortlist -> must NOT answer partially
                "column_missing": intent_resolver._resolve_from_qa(
                    m, {**r, "columns": [c for c in r["columns"] if c["column"].upper() != fast["selected_columns"][0].upper()]}),
                "no_match": intent_resolver._resolve_from_qa(None, r),
            }
            was = intent_resolver.QA_FASTPATH_ENABLED
            intent_resolver.QA_FASTPATH_ENABLED = False
            try:
                guard["disabled_flag"] = intent_resolver._resolve_from_qa(m, r)
            finally:
                intent_resolver.QA_FASTPATH_ENABLED = was
    out["qa"] = {"stats": qa_stats, "guard": guard}

    # An index table whose return cannot be resolved must never reach a shortlist.
    unmapped = "qcb_f015_bank_cap_base"
    out["unmapped_table_in_any_result"] = any(
        unmapped in v["tables"] for v in retrieval.values()
    )

    # Route: stop at intent resolution and observe which tables would reach it.
    intent_resolver.resolve_intent = _stub_intent
    route = {}
    route["named_f014"] = _route("country wise funding by geography")
    route["ambiguous_total_staff"] = _route("total staff")
    route["return_answer_f015"] = _route("net open position in USD", "return", "4092")
    route["return_skip"] = _route("assets maturing overnight", "return", "__skip__")
    route["table_answer"] = _route("assets maturing overnight", "table", "qcb_f010_rcm_in_qtr")
    route["bad_return_id_shape"] = _route("x", "return", "../etc/passwd")
    route["empty_query"] = _route("   ")

    # Authorization: department restricted to F010 only.
    _restrict({"4091"})
    out["restricted_allowed"] = ["4091"]
    out["restricted_retrieval"] = {
        q: [str(t.get("return_id")) for t in retriever.get_relevant_schema(q, CTX, analyze_query(q, {"4091"}, CTX))["tables"]]
        for q in ("breakdown of exposures by geography", "net open position in USD", "show maturity ladder")
    }
    route["restricted_answer_unauthorized_return"] = _route("net open position in USD", "return", "4092")
    route["restricted_table_answer_unauthorized"] = _route("net open position", "table", "qcb_f015_foreign_cur_nop")
    route["restricted_pinned_unauthorized"] = _route(
        "net open position", "table", "__skip__", {"return_id": "4092"})
    route["restricted_answer_authorized_return"] = _route("show maturity ladder", "return", "4091")
    out["route"] = route
    return out


if __name__ == "__main__":
    print("@@JSON@@" + json.dumps(main(), default=str))
