"""H-08: /variance/nlquery - enable switch, confidence gate, SQL hiding, generic errors.

The real FastAPI app runs through TestClient. Retrieval, the LLM and Oracle are
replaced by stand-ins (nothing here touches a database or a model), so this proves
the route's logic, not real query execution.

The last group does NOT stub generate_sql: only the model's *reply* is faked, so
the real SQL validator sits between that reply and execute_query.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api import nlp as nlp_api
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.config import settings as cfg
from backend.main import app
from backend.nlp import retriever, sql_generator

CTX = RequestContext(login_id="tester", tenant_id="")
URL = "/variance/nlquery"
BODY = {"query": "total loans"}

TABLE = {"table": "allowed_t", "return_id": "2041", "return_name": "RAQ", "filter_col": "RDATE"}
COLUMNS = [{"table": "allowed_t", "column": "code"}, {"table": "allowed_t", "column": "amount"}]


def shortlist(confidence=0.9, ambiguous=False, tables=None):
    return {
        "tables": [TABLE] if tables is None else tables,
        "columns": COLUMNS,
        "table_confidence": confidence,
        "table_ambiguous": ambiguous,
    }


class Calls:
    def __init__(self):
        self.generate = 0
        self.execute = []
        self.access = []


@pytest.fixture
def env(monkeypatch):
    """Enabled route, high-confidence shortlist, working SQL + DB stand-ins."""
    calls = Calls()
    monkeypatch.setattr(cfg, "NLQUERY_ENABLED", True)
    monkeypatch.setattr(cfg, "NLQUERY_RETURN_SQL", False)
    monkeypatch.setattr(retriever, "get_relevant_schema", lambda q, ctx: env.shortlist)
    monkeypatch.setattr("backend.nlp.date_resolver._candidate_dates", lambda *a, **k: [])

    def fake_generate(query, tables, columns, **kw):
        calls.generate += 1
        return env.generated

    monkeypatch.setattr(sql_generator, "generate_sql", fake_generate)

    def fake_execute(sql):
        calls.execute.append(sql)
        return env.executed

    monkeypatch.setattr(nlp_api, "execute_query", fake_execute)
    monkeypatch.setattr(nlp_api, "require_tables_access", lambda ctx, tables: calls.access.append(tables))

    env.shortlist = shortlist()
    env.generated = {"sql": "SELECT code, amount FROM allowed_t", "warnings": []}
    env.executed = (["CODE", "AMOUNT"], [("A", 1)], None)

    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as client:
        env.client = client
        env.calls = calls
        yield env
    app.dependency_overrides.pop(require_login, None)


def post(env):
    return env.client.post(URL, json=BODY)


# ── enable switch ────────────────────────────────────────────────────────────

def test_route_is_404_when_disabled_and_does_no_work(env, monkeypatch):
    monkeypatch.setattr(cfg, "NLQUERY_ENABLED", False)
    r = post(env)
    assert r.status_code == 404 and r.json()["detail"] == "Not Found"
    assert env.calls.generate == 0 and env.calls.execute == [] and env.calls.access == []


def test_route_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DV_NLQUERY_ENABLED", raising=False)
    assert cfg._flag("DV_NLQUERY_ENABLED", "false") is False
    assert cfg._flag("DV_NLQUERY_RETURN_SQL", "false") is False


# ── confidence gate ──────────────────────────────────────────────────────────

def test_high_confidence_proceeds(env):
    r = post(env)
    assert r.status_code == 200
    assert r.json() == {"columns": ["CODE", "AMOUNT"], "rows": [["A", 1]]}
    assert env.calls.generate == 1 and len(env.calls.execute) == 1


def test_medium_confidence_asks_instead_of_generating_sql(env):
    env.shortlist = shortlist(confidence=0.45)          # between ASK_FLOOR 0.35 and PROCEED 0.60
    r = post(env)
    assert r.status_code == 200
    assert r.json()["needs_clarification"] is True
    assert env.calls.generate == 0 and env.calls.execute == []


def test_ambiguous_tables_ask_even_with_high_confidence(env):
    env.shortlist = shortlist(confidence=0.95, ambiguous=True)
    r = post(env)
    assert r.json()["needs_clarification"] is True
    assert env.calls.generate == 0 and env.calls.execute == []


def test_low_confidence_is_rejected_without_generating_sql(env):
    env.shortlist = shortlist(confidence=0.10)
    r = post(env)
    assert r.status_code == 404
    assert "allowed_t" not in r.text
    assert env.calls.generate == 0 and env.calls.execute == []


def test_empty_shortlist_is_404(env):
    env.shortlist = shortlist(tables=[])
    assert post(env).status_code == 404
    assert env.calls.generate == 0


def test_blank_query_is_rejected(env):
    r = env.client.post(URL, json={"query": "   "})
    assert r.status_code in (400, 422)
    assert env.calls.generate == 0


# ── authorisation happens before the model is called ─────────────────────────

def test_table_access_denied_stops_before_the_llm(env, monkeypatch):
    def deny(ctx, tables):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(nlp_api, "require_tables_access", deny)
    r = post(env)
    assert r.status_code == 403
    assert env.calls.generate == 0 and env.calls.execute == []


def test_access_is_checked_on_exactly_the_shortlisted_tables(env):
    post(env)
    assert env.calls.access == [[TABLE]]


# ── SQL hiding ───────────────────────────────────────────────────────────────

def test_sql_is_not_returned_by_default(env):
    body = post(env).json()
    assert "sql" not in body
    assert "SELECT" not in str(body)


def test_sql_is_returned_only_when_the_operator_enables_it(env, monkeypatch):
    monkeypatch.setattr(cfg, "NLQUERY_RETURN_SQL", True)
    assert post(env).json()["sql"] == "SELECT code, amount FROM allowed_t"


# ── generic errors ───────────────────────────────────────────────────────────

def test_database_error_is_generic_and_leaks_neither_sql_nor_oracle_text(env):
    env.executed = ([], [], "ORA-00942: table or view does not exist CRILC.SECRET_T")
    r = post(env)
    assert r.status_code == 500
    for leak in ("ORA-", "SECRET_T", "allowed_t", "SELECT", "CRILC"):
        assert leak not in r.text


def test_validation_failure_is_422_generic_and_never_executes(env):
    env.generated = {"sql": "", "warnings": ["table 'dba_users' is not in the allowed shortlist"]}
    r = post(env)
    assert r.status_code == 422
    assert "dba_users" not in r.text and "shortlist" not in r.text
    assert env.calls.execute == []


def test_a_sql_with_warnings_is_not_executed_even_if_text_is_present(env):
    env.generated = {"sql": "SELECT 1 FROM dual", "warnings": ["rejected after retry"]}
    assert post(env).status_code == 422
    assert env.calls.execute == []


# ── the real validator still sits between the model and the database ─────────

_real_generate = sql_generator.generate_sql     # captured before any test patches it


@pytest.fixture
def real_validator(env, monkeypatch):
    """Put the real generate_sql (and so the real validate_sql) back."""
    monkeypatch.setattr(sql_generator, "generate_sql", _real_generate)
    monkeypatch.setattr(sql_generator, "_load_all_columns",
                        lambda names: [{"table": "allowed_t", "column": c} for c in ("code", "amount")])
    return env


@pytest.mark.parametrize("model_reply", [
    "SELECT * FROM dba_users",
    "DROP TABLE allowed_t",
    "SELECT code FROM allowed_t JOIN all_users ON 1=1",
    "SELECT code FROM allowed_t WHERE code IN (SELECT username FROM all_users)",
    "SELECT code FROM allowed_t; DELETE FROM allowed_t",
    "UPDATE allowed_t SET code = 'x'",
])
def test_model_written_sql_that_breaks_the_rules_never_reaches_oracle(real_validator, monkeypatch, model_reply):
    env = real_validator
    monkeypatch.setattr(sql_generator, "_call_ollama", lambda *a, **k: (model_reply, 1.0))
    r = post(env)
    assert r.status_code == 422
    assert env.calls.execute == []
    assert "all_users" not in r.text and "dba_users" not in r.text


def test_model_written_valid_sql_runs(real_validator, monkeypatch):
    env = real_validator
    monkeypatch.setattr(sql_generator, "_call_ollama",
                        lambda *a, **k: ("SELECT code, amount FROM allowed_t", 1.0))
    r = post(env)
    assert r.status_code == 200
    assert env.calls.execute == ["SELECT code, amount FROM allowed_t"]
