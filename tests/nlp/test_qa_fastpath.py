"""QA fast path: a strong QA match answers without the LLM; anything doubtful uses it."""

from __future__ import annotations

import json

import pytest

from backend.nlp import intent_resolver, lexical_search

TABLE = "qcb_f010_rcm_in_qtr"
SQL = (
    "SELECT DESCRIPTION, TOTAL_LC, TOTAL_USD FROM qcb_f010_rcm_in_qtr "
    "WHERE DESCRIPTION = 'Total gap' AND RDATE = (SELECT MAX(RDATE) FROM qcb_f010_rcm_in_qtr)"
)


def _shortlist(qa_match, columns=("total_lc", "total_usd", "description", "rdate")):
    return {
        "tables": [{"table": TABLE, "return_id": "4079", "return_name": "QCB_F010"}],
        "columns": [{"table": TABLE, "column": c} for c in columns],
        "matched_labels": [],
        "qa_match": qa_match,
    }


def _match(sql=SQL):
    return {"table": TABLE, "question": "total gap in F010", "sql": sql, "ratio": 0.99}


@pytest.fixture
def no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the LLM must not be called on the QA fast path")

    monkeypatch.setattr(intent_resolver, "_call_ollama", boom)


def test_selected_columns_drop_identifiers_and_filter_columns():
    assert intent_resolver._qa_selected_columns(SQL) == ["TOTAL_LC", "TOTAL_USD"]


def test_selected_columns_ignore_order_by_and_keep_value_columns():
    sql = ("SELECT COUNTRY, SOVEREIGNS FROM qcb_f013_break_exposure WHERE COUNTRY <> 'Total' "
           "ORDER BY SOVEREIGNS DESC FETCH FIRST 1 ROWS ONLY")
    assert intent_resolver._qa_selected_columns(sql) == ["SOVEREIGNS"]


def test_strong_match_skips_the_llm(no_llm):
    result = intent_resolver.resolve_intent("total gap in F010", _shortlist(_match()))
    assert result["mode"] == "qa"
    assert result["table_name"] == TABLE and result["return_id"] == "4079"
    # the shortlist's own (lowercase) spelling comes back, not the SQL's uppercase
    assert result["selected_columns"] == ["total_lc", "total_usd"]


def test_missing_column_falls_back_to_the_llm(monkeypatch):
    called = []
    monkeypatch.setattr(intent_resolver, "_call_ollama", lambda *a, **k: called.append(1) or ('{"return_id":"4079","table_name":"%s","selected_columns":["total_lc"]}' % TABLE, 1))
    result = intent_resolver.resolve_intent("total gap in F010", _shortlist(_match(), columns=("total_lc",)))
    assert called and result["mode"] == "llm"


def test_no_qa_match_uses_the_llm(monkeypatch):
    called = []
    monkeypatch.setattr(intent_resolver, "_call_ollama", lambda *a, **k: called.append(1) or ('{"return_id":"4079","table_name":"%s","selected_columns":["total_usd"]}' % TABLE, 1))
    result = intent_resolver.resolve_intent("anything", _shortlist(None))
    assert called and result["mode"] == "llm"


def test_unresolved_return_id_is_not_answered_from_qa(monkeypatch):
    monkeypatch.setattr(intent_resolver, "_call_ollama", lambda *a, **k: ("not json", 1))
    shortlist = _shortlist(_match())
    shortlist["tables"][0]["return_id"] = None
    assert intent_resolver._resolve_from_qa(shortlist["qa_match"], shortlist) is None


def test_flag_off_restores_llm(monkeypatch):
    monkeypatch.setattr(intent_resolver, "QA_FASTPATH_ENABLED", False)
    assert intent_resolver._resolve_from_qa(_match(), _shortlist(_match())) is None


def test_search_qa_match_returns_record_and_wrapper_returns_table(tmp_path):
    path = tmp_path / "qa.json"
    path.write_text(json.dumps([{"table": TABLE, "question": "What is the total gap in F010?", "sql": SQL}]), encoding="utf-8")
    match = lexical_search.search_qa_match(str(path), "what is the total gap in f010?", 0.95)
    assert match["table"] == TABLE and match["sql"] == SQL and match["ratio"] >= 0.95
    assert lexical_search.search_qa_strong_match(str(path), "what is the total gap in f010?", 0.95) == TABLE
    assert lexical_search.search_qa_match(str(path), "something unrelated entirely", 0.95) is None
