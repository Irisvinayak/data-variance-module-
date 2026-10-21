"""/variance/nlresolve clarification flow: a named return scopes retrieval to that
return only, ambiguity is asked about before anything is shown, and a query with no
date asks which reporting date (skip = latest vs previous available)."""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("faiss")
pytest.importorskip("rank_bm25")

from fastapi import HTTPException  # noqa: E402

import backend.hosts as hosts  # noqa: E402
from backend.api import nlp as nlp_api  # noqa: E402
from backend.config import ANONYMOUS  # noqa: E402
from backend.data.models import NLResolveRequest  # noqa: E402
from backend.hosts.ideal_60 import Ideal60Profile  # noqa: E402
from backend.nlp import (  # noqa: E402
    date_resolver, intent_resolver, nlp_config, query_analyzer, retriever, schema_info,
)

DATES = [date(2025, 3, 31), date(2024, 12, 31), date(2024, 9, 30), date(2024, 6, 30)]
ONE_TABLE = {"tables": [{"table": "T", "return_id": "R1", "filter_col": "RDATE", "report_freq": "Q"}],
             "columns": [], "table_confidence": 0.95, "table_ambiguous": False}
TWO_TABLES = {"tables": [{"table": "T", "return_id": "R1"}, {"table": "U", "return_id": "R1"}],
              "columns": [], "table_confidence": 0.5, "table_ambiguous": True}


@pytest.fixture
def env(monkeypatch):
    state = {"return_ids": [], "has_date": False, "scoped": ONE_TABLE, "free_calls": 0, "scoped_calls": []}
    monkeypatch.setattr(nlp_config, "ASK_DATE_WHEN_MISSING", True)
    monkeypatch.setattr(hosts, "get_profile", lambda *a, **k: Ideal60Profile())
    monkeypatch.setattr(nlp_api, "_allowed_return_ids", lambda ctx: None)
    monkeypatch.setattr(nlp_api, "require_tables_access", lambda *a, **k: None)
    monkeypatch.setattr(nlp_api, "require_return_access", lambda *a, **k: None)
    monkeypatch.setattr(nlp_api, "_return_row", lambda rid, ctx: {"Name": "CIMS_X"})
    monkeypatch.setattr(nlp_api, "_build_return_clarification",
                        lambda q, ctx, restrict_to=None: {"needs_clarification": True, "dimension": "return",
                                                          "options": [{"id": r} for r in restrict_to or []]})
    monkeypatch.setattr(query_analyzer, "analyze_query", lambda q, allowed, ctx=None: query_analyzer.QueryAnalysis(
        raw=q, normalized=q, metric_text=q, return_ids=list(state["return_ids"]),
        has_date_intent=state["has_date"]))

    def _free(q, ctx, analysis=None):
        state["free_calls"] += 1
        return ONE_TABLE
    monkeypatch.setattr(retriever, "get_relevant_schema", _free)

    def _scoped(rid, q, ctx, analysis=None):
        state["scoped_calls"].append(rid)
        return state["scoped"]
    monkeypatch.setattr(nlp_api, "_shortlist_for_return", _scoped)
    monkeypatch.setattr(nlp_api, "_shortlist_for_table", lambda t, q, ctx, analysis=None: {
        **ONE_TABLE, "tables": [{**ONE_TABLE["tables"][0], "table": t}]})
    monkeypatch.setattr(intent_resolver, "resolve_intent", lambda q, sl, analysis=None: {
        "return_id": "R1", "table_name": sl["tables"][0]["table"], "selected_columns": ["AMT"], "mode": "fallback"})
    monkeypatch.setattr(schema_info, "column_description", lambda *a, **k: None)
    monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: list(DATES))
    monkeypatch.setattr(nlp_api.service, "find_return_and_tables", lambda name, ctx: {
        "return_id": "R1", "return_name": "CIMS_X", "table_mapping_path": "maps/x.xml",
        "tables": [{"table_name": "T"}], "report_freq": "Q"})

    def _compute(**kw):
        state["compared"] = kw["comparison_dates"]
        return {"columns": ["AMT"], "display_columns": ["AMT"], "rows": []}
    monkeypatch.setattr(nlp_api.service, "compute_variance", _compute)

    def ask(query, dimension=None, answer=None, context=None):
        return nlp_api.variance_nlresolve(NLResolveRequest(
            query=query, dimension=dimension, clarification_answer=answer, resolved_context=context), ANONYMOUS)
    state["ask"] = ask
    return state


def test_named_return_searches_only_that_return(env):
    env["return_ids"], env["has_date"] = ["R1"], True
    res = env["ask"]("total loans in CIMS_X")
    assert env["scoped_calls"] == ["R1"] and env["free_calls"] == 0
    assert "rows" in res


def test_ambiguity_inside_named_return_is_asked_scoped_to_it(env):
    env["return_ids"], env["scoped"] = ["R1"], TWO_TABLES
    res = env["ask"]("loans in CIMS_X")
    assert res["dimension"] == "table"
    assert res["resolved_context"]["return_id"] == "R1"
    assert env["free_calls"] == 0


def test_several_matching_returns_are_asked_first(env):
    env["return_ids"] = ["R1", "R2"]
    res = env["ask"]("ALE loans")
    assert res["dimension"] == "return"
    assert [o["id"] for o in res["options"]] == ["R1", "R2"]
    assert env["free_calls"] == 0 and env["scoped_calls"] == []


def test_no_date_asks_which_date_latest_first(env):
    res = env["ask"]("total loans")
    assert res["dimension"] == "date" and res["skippable"]
    assert res["options"][0] == {"id": "latest", "label": "Latest: 31-MAR-2025 vs 31-DEC-2024"}
    assert res["options"][1]["id"] == "31-DEC-2024"
    assert res["resolved_context"] == {"query": "total loans", "return_id": "R1", "table": "T"}
    assert "compared" not in env          # nothing computed before the answer


@pytest.mark.parametrize("answer", ["__skip__", "latest"])
def test_skip_or_latest_compares_latest_with_previous(env, answer):
    ctx = {"query": "total loans", "return_id": "R1", "table": "T"}
    res = env["ask"]("total loans", "date", answer, ctx)
    assert env["compared"] == ["31-MAR-2025", "31-DEC-2024"]
    assert res["interpretation"]["reporting_date"] == "31-MAR-2025"
    assert env["free_calls"] == 0          # return and table stay pinned


def test_picked_date_compares_with_its_previous(env):
    ctx = {"query": "total loans", "return_id": "R1", "table": "T"}
    env["ask"]("total loans", "date", "30-SEP-2024", ctx)
    assert env["compared"] == ["30-SEP-2024", "30-JUN-2024"]


def test_unknown_or_malformed_date_is_rejected(env):
    ctx = {"query": "total loans", "return_id": "R1", "table": "T"}
    with pytest.raises(HTTPException) as e:
        env["ask"]("total loans", "date", "01-JAN-2020", ctx)
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        env["ask"]("total loans", "date", "1=1; drop", ctx)
    assert e.value.status_code == 400


def test_date_in_query_is_not_asked(env):
    env["has_date"] = True
    res = env["ask"]("total loans as of 31-Dec-2024")
    assert "rows" in res


def test_quarter_phrase_counts_as_a_date(env):
    res = env["ask"]("total loans for Q3")
    assert "rows" in res
