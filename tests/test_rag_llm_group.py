"""Retrieval / LLM fixes: M-18, M-19, M-41, L-14, L-18. Stand-ins only; no model, no network."""

from __future__ import annotations

import pytest
import requests

from backend.nlp import intent_resolver, sql_generator
from backend.nlp.llm_guard import make_session
from backend.nlp.sql_generator import validate_sql

TABLES = [{"table": "allowed_t"}]
COLUMNS = [{"table": "allowed_t", "column": "code"}, {"table": "allowed_t", "column": "amount"}]


@pytest.fixture(autouse=True)
def _schema(monkeypatch):
    monkeypatch.setattr(
        sql_generator, "_load_all_columns",
        lambda names: [{"table": "allowed_t", "column": c} for c in ("code", "amount", "row_label")],
    )


# ── M-18: functions and literals are not "hallucinated columns" ──────────────

@pytest.mark.parametrize("sql", [
    "SELECT ROUND(amount, 2) FROM allowed_t",
    "SELECT CAST(amount AS NUMBER(10,2)) FROM allowed_t",
    "SELECT DECODE(code, 'A', 1, 0) FROM allowed_t",
    "SELECT ABS(amount) FROM allowed_t",
    "SELECT CASE WHEN amount > 0 THEN 'pos' ELSE 'neg' END AS sign FROM allowed_t",
    "SELECT SUM(amount) AS total_amount FROM allowed_t GROUP BY code",
    "SELECT NVL(amount, 0) x FROM allowed_t ORDER BY x DESC",
    "SELECT code, 'Total: Assets (net)' AS label FROM allowed_t",
])
def test_valid_functions_and_literals_are_accepted(sql):
    ok, reason = validate_sql(sql, TABLES, COLUMNS)
    assert ok, reason


@pytest.mark.parametrize("sql,name", [
    ("SELECT bogus FROM allowed_t", "bogus"),
    ("SELECT ROUND(bogus, 2) FROM allowed_t", "bogus"),
    ("SELECT a.code, a.nope FROM allowed_t a", "nope"),
    ("SELECT CASE WHEN missing_col > 0 THEN 1 ELSE 0 END FROM allowed_t", "missing_col"),
])
def test_a_genuinely_unknown_column_is_still_rejected(sql, name):
    ok, reason = validate_sql(sql, TABLES, COLUMNS)
    assert not ok and "Hallucinated columns" in reason and name in reason


@pytest.mark.parametrize("sql", [
    "SELECT code FROM allowed_t JOIN all_users ON 1=1",
    "SELECT ROUND((SELECT username FROM all_users WHERE rownum = 1)) FROM allowed_t",
    "SELECT code FROM allowed_t UNION SELECT username FROM dba_users",
    "SELECT UTL_HTTP.REQUEST('http://x') FROM allowed_t",
    "DROP TABLE allowed_t",
])
def test_the_security_boundary_is_unchanged(sql):
    assert not validate_sql(sql, TABLES, COLUMNS)[0]


# ── M-19: num_predict can be raised without a code change ────────────────────

def _options(monkeypatch, profile, env=None):
    sent = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "SELECT 1"}

    monkeypatch.setattr(sql_generator, "_session",
                        type("S", (), {"post": staticmethod(lambda url, json=None, timeout=None: sent.update(json) or R())})())
    monkeypatch.setattr(sql_generator, "_breaker", sql_generator.CircuitBreaker(60))
    if env is None:
        monkeypatch.delenv("DV_SQL_NUM_PREDICT", raising=False)
    else:
        monkeypatch.setenv("DV_SQL_NUM_PREDICT", env)
    sql_generator._call_ollama("p", "m", profile, attempt=1)
    return sent.get("options", {})


def test_profile_num_predict_is_used_by_default(monkeypatch):
    assert _options(monkeypatch, {"num_predict": 128})["num_predict"] == 128


def test_env_override_raises_the_cap(monkeypatch):
    assert _options(monkeypatch, {"num_predict": 128}, env="512")["num_predict"] == 512


@pytest.mark.parametrize("bad", ["abc", "0", "-5", ""])
def test_a_bad_override_is_ignored(monkeypatch, bad):
    assert _options(monkeypatch, {"num_predict": 128}, env=bad)["num_predict"] == 128


# ── M-41: the response says whether the model or the fallback decided ────────

SHORTLIST = {
    "tables": [{"table": "allowed_t", "return_id": "1", "return_name": "R"}],
    "columns": COLUMNS, "matched_labels": [],
}


def _llm(monkeypatch, text):
    monkeypatch.setattr(intent_resolver, "_in_cooldown", lambda: False)
    monkeypatch.setattr(intent_resolver, "_call_ollama", lambda prompt, attempt=1: (text, 5))


def test_a_grounded_model_answer_is_tagged_llm(monkeypatch):
    _llm(monkeypatch, '{"return_id": "1", "table_name": "allowed_t", "selected_columns": ["amount"]}')
    out = intent_resolver.resolve_intent("loans", SHORTLIST)
    assert out["mode"] == "llm" and out["selected_columns"] == ["amount"]


def test_an_ungrounded_model_answer_is_tagged_fallback(monkeypatch):
    _llm(monkeypatch, '{"return_id": "9", "table_name": "elsewhere", "selected_columns": ["x"]}')
    assert intent_resolver.resolve_intent("loans", SHORTLIST)["mode"] == "fallback"


def test_an_unreachable_model_is_tagged_fallback(monkeypatch):
    monkeypatch.setattr(intent_resolver, "_in_cooldown", lambda: False)

    def down(prompt, attempt=1):
        raise intent_resolver._TransportError("down")

    monkeypatch.setattr(intent_resolver, "_call_ollama", down)
    assert intent_resolver.resolve_intent("loans", SHORTLIST)["mode"] == "fallback"


def test_cooldown_is_tagged_fallback(monkeypatch):
    monkeypatch.setattr(intent_resolver, "_in_cooldown", lambda: True)
    assert intent_resolver.resolve_intent("loans", SHORTLIST)["mode"] == "fallback"


def test_tagging_keeps_the_existing_keys_and_none():
    assert intent_resolver._tag(None, "llm") is None
    assert intent_resolver._tag({"return_id": "1"}, "llm") == {"return_id": "1", "mode": "llm"}


# ── L-14: JSON extraction and type checks ────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! {"a": 1} hope that helps', {"a": 1}),
    ('{"a": 1} and also {"b": 2}', {"a": 1}),                 # two objects: the greedy span broke on this
    ('note {not json} then {"a": 1}', {"a": 1}),
    ('{"a": {"nested": [1, 2]}}', {"a": {"nested": [1, 2]}}),
    ('[{"a": 1}]', {"a": 1}),                                 # an object inside a list is still found
])
def test_the_first_json_object_is_extracted(raw, expected):
    assert intent_resolver._parse_json_response(raw) == expected


@pytest.mark.parametrize("raw", ["", "no json here", "{broken", "[1, 2, 3]", '"string"'])
def test_non_objects_give_none(raw):
    assert intent_resolver._parse_json_response(raw) is None


def test_selected_columns_must_be_a_list():
    parsed = {"return_id": "1", "table_name": "allowed_t", "selected_columns": "amount"}
    assert intent_resolver._validate_grounding(parsed, SHORTLIST) is None
    parsed["selected_columns"] = {"amount": 1}
    assert intent_resolver._validate_grounding(parsed, SHORTLIST) is None
    parsed["selected_columns"] = ["amount", 5, None, "code"]
    assert intent_resolver._validate_grounding(parsed, SHORTLIST)["selected_columns"] == ["amount", "code"]


# ── L-18: the shared session's pool matches the concurrency ──────────────────

def test_sessions_have_a_pool_larger_than_the_default():
    s = make_session()
    adapter = s.get_adapter("http://ollama.example:11434/api/generate")
    assert adapter._pool_maxsize >= 40 > 10
    assert s.get_adapter("https://x.example")._pool_maxsize >= 40      # both schemes are mounted
    assert isinstance(s, requests.Session)


def test_pool_size_is_configurable(monkeypatch):
    monkeypatch.setenv("DV_LLM_HTTP_POOL", "64")
    assert make_session().get_adapter("http://h")._pool_maxsize == 64
    monkeypatch.setenv("DV_LLM_HTTP_POOL", "2")                # never below the library default
    assert make_session().get_adapter("http://h")._pool_maxsize == 10


def test_both_modules_use_the_sized_session():
    assert sql_generator._session.get_adapter("http://h")._pool_maxsize >= 40
    assert intent_resolver._session.get_adapter("http://h")._pool_maxsize >= 40
