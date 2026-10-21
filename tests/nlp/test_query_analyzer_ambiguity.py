"""Return-name matching and date-phrase fixes found by probing the real 5.5/6.0 indexes."""
from __future__ import annotations

import pytest

from backend.api import nlp as nlp_api
from backend.nlp import query_analyzer as qa
from backend.nlp import schema_info

CORPUS = [
    {"Id": "13", "Name": "QCB_F013_Breakdown of exposures by geography"},
    {"Id": "14", "Name": "QCB_F014_Breakdown of Funding by geography"},
    {"Id": "15", "Name": "QCB_F015_Foreign Currency Net Open Positions"},
]


@pytest.fixture(autouse=True)
def _identifiers(monkeypatch):
    monkeypatch.setattr(schema_info, "looks_like_return_identifier", lambda tok: True)


# Unrelated returns, so document frequency behaves as over the real corpus.
FULL = CORPUS + [{"Id": f"x{i}", "Name": f"QCB_F9{i:02d}_Other report {i}"} for i in range(20)]


def names(q):
    return sorted(r["Id"] for r in qa._match_named_returns(q, CORPUS, FULL))


def test_filler_word_does_not_name_a_return():
    assert names("show sovereigns by country") == []


def test_shared_name_words_tie_both_returns():
    assert names("breakdown by geography") == ["13", "14"]


@pytest.mark.parametrize("q,rid", [
    ("breakdown of exposures by geography", "13"),
    ("breakdown of funding by geography", "14"),
    ("retail in f014", "14"),
])
def test_distinguishing_word_picks_one(q, rid):
    assert names(q) == [rid]


@pytest.mark.parametrize("q,date_text", [
    ("term loans in cims_raq for q3", "for q3"),
    ("interest rate current vs previous", "current vs previous"),
    ("compare with previous", "previous"),
])
def test_date_phrases_are_taken_out_of_the_metric(q, date_text):
    rest, found = qa._extract_date_text(q)
    assert found == date_text and date_text not in rest


def test_current_alone_is_a_column_word_not_a_date():
    assert qa._extract_date_text("balances in current accounts")[1] is None


@pytest.mark.parametrize("tok,is_code", [("f020", True), ("bsr1", False), ("q3", False), ("fy24", False)])
def test_return_code_shape(tok, is_code):
    assert bool(qa._RETURN_CODE_RE.match(tok) and not qa._NOT_RETURN_CODE_RE.match(tok)) is is_code


def test_identical_tables_in_two_returns_are_twins(monkeypatch):
    cols = {"A": ["COUNTRY", "BANKS"], "B": ["COUNTRY", "BANKS"], "C": ["X"]}
    monkeypatch.setattr("backend.nlp.index_store.meta_by_table",
                        lambda *a: {k: [{"column": c} for c in v] for k, v in cols.items()})
    sl = {"tables": [{"table": "A", "return_id": "13"}, {"table": "B", "return_id": "14"},
                     {"table": "C", "return_id": "15"}]}
    assert nlp_api._twin_return_ids(sl) == ["13", "14"]
    sl["tables"] = sl["tables"][::-1]
    assert nlp_api._twin_return_ids(sl) == []
