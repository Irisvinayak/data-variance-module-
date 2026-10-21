"""/variance/nlresolve end to end for the conversation layer: refinement questions
(measure / threshold / period), follow-ups that refine the previous answer, drill-down,
trend and anomaly, and refusals instead of silent substitutes (scenario groups 11-13,
23-27). Same stand-ins as test_nlresolve_versions.py; everything else is real code."""
from __future__ import annotations

import pytest

pytest.importorskip("faiss")
pytest.importorskip("rank_bm25")

from fastapi import HTTPException  # noqa: E402

from backend.api import nlp as nlp_api  # noqa: E402
from backend.config import ANONYMOUS  # noqa: E402
from backend.data.models import NLResolveRequest  # noqa: E402
from backend.nlp import nlp_config  # noqa: E402

from tests.nlp.test_nlresolve_versions import ids, route  # noqa: E402,F401  (fixture)


@pytest.fixture
def ask(route, monkeypatch):  # noqa: F811
    # A follow-up keeps the previous table: the table-scoped shortlist stand-in.
    monkeypatch.setattr(nlp_api, "_shortlist_for_table", lambda t, q, ctx, analysis=None: {
        "tables": [{"table": t, "return_id": "R1", "filter_col": "RDATE", "report_freq": "Q"}],
        "table_confidence": 1.0, "table_ambiguous": False})

    def _ask(query, dimension=None, answer=None, context=None, state=None):
        return nlp_api.variance_nlresolve(NLResolveRequest(
            query=query, dimension=dimension, clarification_answer=answer,
            resolved_context=context, conversation_state=state), ANONYMOUS)
    _ask.route = route
    return _ask


# ── 26: ask instead of guessing ───────────────────────────────────────────────
def test_measure_question_then_answer(ask):
    q = "Show highest variation."
    res = ask(q)
    assert res["dimension"] == "measure"
    res = ask(q, "measure", "abs", res["resolved_context"])
    assert res["analysis"]["effective_measure"] == "abs"
    assert ids(res) == ["E"]                     # -500 is the largest absolute change


def test_threshold_question_then_answer(ask):
    q = "Show significant changes."
    res = ask(q)
    assert res["dimension"] == "threshold" and res["allow_other"]
    res = ask(q, "threshold", "20", res["resolved_context"])
    assert sorted(ids(res)) == ["A", "E"]        # +50%, -100%; B is only -10%


def test_threshold_skip_uses_the_default(ask):
    res = ask("Show significant changes.", "threshold", "__skip__", {"query": "Show significant changes."})
    assert sorted(ids(res)) == ["A", "E"]          # > 10%: B is exactly -10%
    assert f"{nlp_config.SIGNIFICANT_PCT_DEFAULT:g}%" in " ".join(res["applied_filters"])


def test_threshold_free_text_amount(ask):
    res = ask("Show significant changes.", "threshold", "30", {"query": "Show significant changes."})
    assert sorted(ids(res)) == ["A", "E"]


def test_invalid_threshold_answer_is_rejected(ask):
    with pytest.raises(HTTPException) as exc:
        ask("Show significant changes.", "threshold", "lots", {"query": "x"})
    assert exc.value.status_code == 400


def test_bare_month_asks_which_period(ask):
    res = ask("Compare March.")
    assert res["dimension"] == "period"
    res = ask("Compare March.", "period", "last_year", res["resolved_context"])
    assert res["interpretation"]["comparison_dates"][:2] == ["31-MAR-2025", "31-MAR-2024"]


# ── 27: refuse, never substitute ──────────────────────────────────────────────
def test_period_without_data_is_refused(ask):
    with pytest.raises(HTTPException) as exc:
        ask("Compare current data with 2020.")
    assert exc.value.status_code == 404 and "not available" in exc.value.detail


def test_period_without_data_falls_back_when_strict_is_off(ask, monkeypatch):
    monkeypatch.setattr(nlp_config, "STRICT_PERIODS", False)
    res = ask("Compare current data with 2020.")
    assert res["interpretation"]["date_notes"]


def test_unknown_column_is_refused(ask):
    with pytest.raises(HTTPException) as exc:
        ask("Show me variation in the Goodwill column.")
    assert exc.value.status_code == 404 and "Goodwill" in exc.value.detail


def test_unknown_element_says_not_found(ask):
    res = ask("Show variation for element Goodwill.")
    assert res["rows"] == [] and "not found" in res["summary"]


# ── 24: the follow-up conversation ────────────────────────────────────────────
def test_follow_up_flow(ask):
    first = ask("Compare current and previous period.")
    assert "conversation_state" in first and sorted(ids(first)) == list("ABCDEF")

    top = ask("Show top 10 by percentage.", state=first["conversation_state"])
    assert top["analysis"]["operation"] == "rank"

    dec = ask("Only decreases.", state=top["conversation_state"])
    assert set(ids(dec)) == {"B", "E"}

    big = ask("More than 20%.", state=dec["conversation_state"])
    assert ids(big) == ["E"]                     # B fell only 10%

    detail = ask("Show details of the first one.", state=big["conversation_state"])
    d = detail["drill_down"]
    assert d["identifier"] == "E" and d["columns"][0]["change"] == "-500.00"
    assert d["columns"][0]["pct_change"] == "-100.00"
    # the period stayed the same throughout
    assert detail["interpretation"]["comparison_dates"] == first["interpretation"]["comparison_dates"]

    hist = ask("Compare the same element for the last 3 quarters.", state=detail["conversation_state"])
    assert hist["series"]["items"] and {i["identifier"] for i in hist["series"]["items"]} == {"E"}
    assert len(hist["series"]["dates"]) == 4


def test_switch_measure_instead(ask):
    first = ask("Show top 2 by percentage.")
    switched = ask("Now show absolute instead of percentage.", state=first["conversation_state"])
    assert switched["analysis"]["effective_measure"] == "abs"
    assert ids(switched) == ["E", "A"]


def test_pointing_at_a_missing_item_is_refused(ask):
    first = ask("Show top 1 by percentage.")
    with pytest.raises(HTTPException) as exc:
        ask("Show details of the third one.", state=first["conversation_state"])
    assert exc.value.status_code == 404


def test_new_question_without_state_starts_over(ask):
    res = ask("Only decreases.")
    assert set(ids(res)) == {"B", "E"} and "followup_of" not in res["interpretation"]


# ── 11 / 12: trend and anomaly ────────────────────────────────────────────────
def test_trend_over_last_quarters(ask):
    res = ask("Show quarter-on-quarter variation for the last 4 quarters.")
    assert len(res["series"]["dates"]) == 5
    a = next(i for i in res["series"]["items"] if i["identifier"] == "A")
    assert a["values"][-1] == "150.00" and a["changes"][-1] == "50.00"


def test_continuously_increased(ask):
    res = ask("Which elements have continuously increased over the last 3 periods?")
    # every element is flat until the latest step, so none rose at every step
    assert res["series"]["items"] == [] and res["rows"] == []


def test_anomaly_states_its_rule(ask):
    res = ask("Find unusual variations.")
    assert any(n.startswith("Rule:") for n in res["analysis_notes"])


def test_provenance_is_present(ask):
    res = ask("Compare current and previous period.")
    p = res["provenance"]
    assert p["current_date"] == "31-MAR-2025" and p["previous_dates"] == ["31-DEC-2024"]
    assert "Percentage change" in p["formula"]
