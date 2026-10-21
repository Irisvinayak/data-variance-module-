"""The whole /variance/nlresolve route, once under the 5.5 host profile and once under 6.0.

Oracle, the embedding index and the LLM are replaced by stand-ins; everything else is the
real code: query analysis output -> operation parser -> the real date_resolver (so the
fiscal-year convention of the profile matters) -> the real calculate_variance -> the real
analysis stage -> the route's response. The two hosts differ in the fiscal year (5.5 Apr-Mar,
6.0 Jan-Dec) and in nothing else the NLP path touches, so the same prompt must give the same
analysis and a different date where the fiscal year decides it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("faiss")
pytest.importorskip("rank_bm25")

import backend.hosts as hosts  # noqa: E402
from backend.api import nlp as nlp_api  # noqa: E402
from backend.config import ANONYMOUS  # noqa: E402
from backend.data import calculate_variance as cv  # noqa: E402
from backend.data.models import NLResolveRequest  # noqa: E402
from backend.hosts.ideal_55 import Ideal55Profile  # noqa: E402
from backend.hosts.ideal_60 import Ideal60Profile  # noqa: E402
from backend.nlp import (  # noqa: E402
    date_resolver, intent_resolver, nlp_config, query_analyzer, retriever, schema_info,
)

DATES = [datetime(2025, 3, 31), datetime(2024, 12, 31), datetime(2024, 9, 30),
         datetime(2024, 6, 30), datetime(2024, 3, 31), datetime(2023, 12, 31)]

#            previous  current
VALUES = {
    "A": (100, 150),     # +50 / +50%
    "B": (200, 180),     # -20 / -10%
    "C": (300, 300),     # unchanged
    "D": (0, 40),        # new from zero
    "E": (500, 0),       # became zero
    "F": (1000, 1005),   # +5 / +0.5%
}


def _rows():
    out = []
    for i, d in enumerate(DATES):
        for key, (prev, curr) in VALUES.items():
            out.append({"RDATE": d, "ID": key, "AMT": curr if i == 0 else prev})
    return out


@pytest.fixture(params=[("5.5", Ideal55Profile, 4), ("6.0", Ideal60Profile, 1)], ids=["v5.5", "v6.0"])
def route(request, monkeypatch):
    version, profile_cls, fy_start = request.param
    profile = profile_cls()
    assert profile.default_fiscal_year_start_month == fy_start
    monkeypatch.setattr(hosts, "get_profile", lambda *a, **k: profile)

    # These tests are about the analysis; the "which date?" question has its own
    # tests in test_nlresolve_clarification.py.
    monkeypatch.setattr(nlp_config, "ASK_DATE_WHEN_MISSING", False)
    monkeypatch.setattr(nlp_api, "_allowed_return_ids", lambda ctx: None)
    monkeypatch.setattr(nlp_api, "require_tables_access", lambda *a, **k: None)
    monkeypatch.setattr(nlp_api, "require_return_access", lambda *a, **k: None)
    monkeypatch.setattr(nlp_api, "_return_row", lambda rid, ctx: {"Name": "CIMS_X"})

    monkeypatch.setattr(query_analyzer, "analyze_query", lambda q, allowed, ctx=None: query_analyzer.QueryAnalysis(
        raw=q, normalized=q, metric_text=q))
    monkeypatch.setattr(retriever, "get_relevant_schema", lambda q, ctx, analysis=None: {
        "tables": [{"table": "T", "return_id": "R1", "filter_col": "RDATE", "report_freq": "Q"}],
        "table_confidence": 0.95, "table_ambiguous": False})
    monkeypatch.setattr(intent_resolver, "resolve_intent", lambda q, shortlist, analysis=None: {
        "return_id": "R1", "table_name": "T", "selected_columns": ["AMT"], "mode": "fallback"})
    monkeypatch.setattr(schema_info, "column_description", lambda *a, **k: None)
    monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: [d.date() for d in DATES])

    monkeypatch.setattr(nlp_api.service, "find_return_and_tables", lambda name, ctx: {
        "return_id": "R1", "return_name": "CIMS_X", "table_mapping_path": "maps/x.xml",
        "tables": [{"table_name": "T"}], "report_freq": "Q"})

    def _compute(**kw):
        meta = {"filter_col": "RDATE", "comp_filter_col_names": ["ID"], "is_single": False, "report_freq": "Q"}
        return cv.calculate_variance(
            return_code="R1", table_name=kw["table_name"], reporting_date=kw["reporting_date"],
            get_table_metadata_fn=lambda *a, **k: meta, execute_query_fn=lambda q, *a: _rows(),
            comparison_dates=kw["comparison_dates"])
    monkeypatch.setattr(nlp_api.service, "compute_variance", _compute)

    def ask(query):
        return nlp_api.variance_nlresolve(NLResolveRequest(query=query), ANONYMOUS)
    ask.version, ask.fy_start = version, fy_start
    return ask


def ids(res):
    return [r["identifier"] for r in res["rows"]]


# ── the plain comparison is unchanged by the new stage ────────────────────────

def test_plain_comparison_returns_every_row_with_a_summary(route):
    res = route("total loans")
    assert sorted(ids(res)) == list("ABCDEF")
    assert res["interpretation"]["comparison_dates"][:2] == ["31-MAR-2025", "31-DEC-2024"]
    assert "2 increased" in res["summary"] and "1 unchanged" in res["summary"]
    assert res["return_id"] == "R1" and res["columns"] == ["AMT"]


def test_every_row_carries_a_status_per_column(route):
    res = route("Compare the current data with the previous period.")
    status = {r["identifier"]: r["analysis"]["cells"]["AMT"]["status"] for r in res["rows"]}
    assert status == {"A": "increased", "B": "decreased", "C": "unchanged",
                      "D": "new", "E": "became_zero", "F": "increased"}


# ── use-case groups through the full route ────────────────────────────────────

@pytest.mark.parametrize("prompt,expected", [
    ("Show only increased values.", {"A", "D", "F"}),
    ("Show only decreased values.", {"B", "E"}),
    ("Which elements have changed?", {"A", "B", "D", "E", "F"}),
    ("Show variations greater than 10%.", {"A", "E"}),
    ("Show elements with variation greater than 20%.", {"A", "E"}),
    ("Find elements with more than 50% increase.", set()),
    ("Show elements where the value decreased by more than 5%.", {"B", "E"}),
    ("Identify changes below -15%.", {"E"}),
    ("Show variations between 10% and 50%.", {"A", "B"}),
    ("Show absolute changes above 100", {"E"}),
    ("Show changes above ₹1 lakh", set()),
    ("Show unchanged values.", {"C"}),
    ("Which elements have no variation?", {"C"}),
    ("Show newly populated elements.", {"D"}),
    ("Which elements became zero?", {"E"}),
])
def test_filters(route, prompt, expected):
    assert set(ids(route(prompt))) == expected


@pytest.mark.parametrize("prompt,expected", [
    ("Give me the top 2 absolute increases.", ["A", "D"]),
    ("Give me the top 2 absolute decreases.", ["E", "B"]),
    ("Which element has the highest absolute increase?", ["A"]),
    ("Which element experienced the largest absolute decrease?", ["E"]),
    ("Show top 2 elements with highest percentage variation.", ["E", "A"]),
    ("Show the bottom 1 elements by absolute variation.", ["C"]),
])
def test_ranking(route, prompt, expected):
    assert ids(route(prompt)) == expected


def test_ambiguous_measure_is_disclosed_not_guessed_silently(route, monkeypatch):
    # With the question switched off, the old behaviour: % is assumed, and said.
    monkeypatch.setattr(nlp_config, "ASK_MEASURE", False)
    res = route("Show top 3 elements with highest variation.")
    assert any("read as percentage" in n for n in res["analysis_notes"])
    assert res["analysis"]["effective_measure"] == "pct"


def test_ambiguous_measure_is_asked(route):
    res = route("Show top 3 elements with highest variation.")
    assert res["needs_clarification"] and res["dimension"] == "measure"
    assert {o["id"] for o in res["options"]} == {"pct", "abs"}


def test_applied_filters_and_summary_describe_what_was_done(route):
    res = route("Show variations greater than 10%.")
    assert res["applied_filters"] == ["percentage change greater than 10%"]
    assert res["summary"].startswith("2 of 6 rows match")
    assert res["analysis"]["matched_rows"] == 2 and res["analysis"]["total_rows"] == 6


def test_zero_base_is_reported_not_ranked_by_percent(route):
    res = route("Show variations greater than 10%.")
    assert any("rose from zero" in n for n in res["analysis_notes"])
    assert "D" not in ids(res)


def test_column_summary_totals(route):
    cs = route("Show column-wise variation.")["column_summary"]
    assert cs[0]["column"] == "AMT"
    assert cs[0]["total_previous"] == "2100.00" and cs[0]["total_current"] == "1675.00"
    assert cs[0]["new"] == 1 and cs[0]["became_zero"] == 1 and cs[0]["unchanged"] == 1


# ── periods: where the two hosts legitimately differ ──────────────────────────

def test_fiscal_quarter_follows_the_host_fiscal_year(route):
    res = route("Q1 FY25 total loans")
    # 5.5: FY25 = Apr-2024..Mar-2025 so Q1 = Apr-Jun 2024; 6.0: FY25 = calendar 2025 so Q1 = Jan-Mar 2025.
    want = "30-JUN-2024" if route.fy_start == 4 else "31-MAR-2025"
    assert res["interpretation"]["reporting_date"] == want


def test_calendar_quarter_is_the_same_on_both_hosts(route):
    assert route("Q4 2024 total loans")["interpretation"]["reporting_date"] == "31-DEC-2024"


def test_explicit_dates_are_the_same_on_both_hosts(route):
    res = route("Compare 31-Dec-2024 with 30-Sep-2024")
    assert res["interpretation"]["comparison_dates"][:2] == ["31-DEC-2024", "30-SEP-2024"]


def test_yoy_and_previous_quarter(route):
    assert route("YoY total loans")["interpretation"]["comparison_dates"][:2] == ["31-MAR-2025", "31-MAR-2024"]
    assert route("previous quarter total loans")["interpretation"]["comparison_dates"][:2] == [
        "31-MAR-2025", "31-DEC-2024"]


# ── safety: the new stage can never cost the user their table ─────────────────

def test_trend_query_returns_the_plain_variance_when_switched_off(route, monkeypatch):
    monkeypatch.setattr(nlp_config, "TREND_ENABLED", False)
    res = route("Show the trend of Assets for the last 12 months.")
    assert sorted(ids(res)) == list("ABCDEF") and "analysis" not in res


def test_hinglish_query_still_answers_with_the_plain_table(route):
    assert sorted(ids(route("Current aur previous data ka variation dikhao."))) == list("ABCDEF")


def test_an_analysis_failure_falls_back_to_the_plain_table(route, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("analysis bug")
    monkeypatch.setattr(nlp_api, "analyze_result", boom)
    res = route("Show only increased values.")
    assert sorted(ids(res)) == list("ABCDEF") and "summary" not in res


def test_empty_query_is_rejected(route):
    with pytest.raises(Exception) as exc:
        route("   ")
    assert getattr(exc.value, "status_code", 400) == 400
