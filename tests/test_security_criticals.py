"""Regression tests for the Critical findings in docs/ISSUES_STATUS.md.

C-02  validate_sql's JOIN check was disabled by a literal backspace byte.
C-03  validate_sql could be bypassed with a nested sub-query; /variance/nlquery
      is now disabled by default.
C-01  the backend binds to loopback by default and refuses to start with auth
      off on a network-reachable host.

validate_sql reads the allowed columns from the embedding folder's schema.json;
that lookup is stubbed so these tests need no index, database or LLM.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.nlp import sql_generator
from backend.nlp.sql_generator import validate_sql

ALLOWED = [{"table": "allowed_t"}]
COLUMNS = [{"table": "allowed_t", "column": "code"}, {"table": "allowed_t", "column": "amount"}]


@pytest.fixture(autouse=True)
def _stub_schema(monkeypatch):
    monkeypatch.setattr(
        sql_generator, "_load_all_columns",
        lambda names: [{"table": "allowed_t", "column": c} for c in ("code", "amount")],
    )


# ── Legitimate queries must still pass ────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT code, amount FROM allowed_t",
    "SELECT SUM(amount) FROM allowed_t WHERE code = 'X'",
    "SELECT TRIM(code), amount FROM allowed_t",
    "SELECT a.code FROM allowed_t a JOIN allowed_t b ON a.code = b.code",
])
def test_valid_queries_accepted(sql):
    ok, reason = validate_sql(sql, ALLOWED, COLUMNS)
    assert ok, reason


# ── C-02: tables after JOIN must be checked ──────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT code FROM allowed_t JOIN all_users ON 1=1",
    "SELECT code FROM allowed_t a JOIN all_users u ON a.code = u.code",
    "SELECT code FROM allowed_t LEFT JOIN dba_users d ON 1=1",
])
def test_join_to_unauthorized_table_rejected(sql):
    ok, reason = validate_sql(sql, ALLOWED, COLUMNS)
    assert not ok, f"accepted: {sql}"


def test_no_control_bytes_in_backend_sources():
    """The C-02 bug was an invisible backspace byte inside a regex literal."""
    import os

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    bad = set(range(0, 9)) | {11, 12} | set(range(14, 32))
    offenders = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]      # prune; never walk the virtualenv
        for name in files:
            path = pathlib.Path(folder) / name
            if name.endswith(".py") and any(b in bad for b in path.read_bytes()):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"control bytes found in: {offenders}"


# ── C-03: every table anywhere in the parsed tree must be allowed ───────────

@pytest.mark.parametrize("sql", [
    "SELECT TRIM((SELECT username FROM all_users WHERE rownum=1)) x, code FROM allowed_t",
    "SELECT EXTRACT(YEAR FROM (SELECT created FROM dba_users WHERE rownum=1)) y, code FROM allowed_t",
    "SELECT code FROM allowed_t WHERE code IN (SELECT username FROM all_users)",
    "SELECT code FROM allowed_t UNION SELECT username FROM all_users",
    "SELECT x FROM (SELECT code x FROM allowed_t UNION SELECT username FROM dba_users)",
    "SELECT (SELECT password FROM sys_user) p FROM allowed_t",
    "SELECT code FROM allowed_t, all_users",
    "SELECT code FROM allowed_t JOIN all_users ON 1=1",
])
def test_unauthorized_table_anywhere_in_tree_rejected(sql):
    ok, reason = validate_sql(sql, ALLOWED, COLUMNS)
    assert not ok, f"accepted: {sql}"
    assert "Hallucinated tables" in reason


@pytest.mark.parametrize("sql", [
    "SELECT * FROM allowed_t, TABLE(my_pkg.f())",
    "WITH x AS (SELECT * FROM all_users) SELECT code FROM allowed_t",
    "INSERT INTO allowed_t SELECT * FROM allowed_t",
    "SELECT code FROM allowed_t; DROP TABLE allowed_t",
    "SELECT utl_http.request('http://x') FROM allowed_t",
    "SELECT FROM WHERE",
])
def test_non_select_functions_and_unparsable_rejected(sql):
    ok, _ = validate_sql(sql, ALLOWED, COLUMNS)
    assert not ok, f"accepted: {sql}"


def test_nested_and_union_over_allowed_tables_still_accepted():
    """The generator's own multi-part prompt example must remain valid."""
    two = [{"table": "allowed_t"}, {"table": "part_b"}]
    for sql in (
        "SELECT SUM(val) FROM (SELECT amount AS val FROM allowed_t UNION ALL SELECT amount AS val FROM part_b)",
        "SELECT code FROM allowed_t WHERE code IN (SELECT code FROM part_b)",
        "SELECT code FROM allowed_t UNION ALL SELECT code FROM part_b",
    ):
        ok, reason = validate_sql(sql, two, COLUMNS)
        assert ok, f"{sql} -> {reason}"


def test_validator_fails_closed_when_parser_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("sqlglot"):
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    ok, reason = validate_sql("SELECT code FROM allowed_t", ALLOWED, COLUMNS)
    assert not ok and "unavailable" in reason.lower()


def test_nlquery_disabled_by_default(monkeypatch):
    from fastapi import HTTPException

    from backend.api import nlp as nlp_api
    from backend.config import settings
    from backend.config.context import RequestContext
    from backend.data.models import NLResolveRequest

    monkeypatch.setattr(settings, "NLQUERY_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        nlp_api.variance_nlquery(NLResolveRequest(query="total loan assets"), RequestContext("u", ""))
    assert exc.value.status_code == 404


# ── C-01: loopback default and the insecure-bind guard ───────────────────────

@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True),
    ("0.0.0.0", False), ("10.0.0.5", False),
])
def test_is_loopback_host(host, expected):
    from backend.config.settings import is_loopback_host
    assert is_loopback_host(host) is expected


def test_insecure_bind_warns_but_still_starts(monkeypatch, capsys):
    """Existing deployments (auth off on 0.0.0.0) must keep starting."""
    import dev_server

    monkeypatch.setattr(dev_server, "AUTH_ENABLED", False)
    monkeypatch.setattr(dev_server, "SERVER_HOST", "0.0.0.0")
    monkeypatch.delenv("DV_STRICT_BIND", raising=False)
    assert dev_server._warn_insecure_bind() is True        # no SystemExit
    assert "SECURITY WARNING" in capsys.readouterr().err


def test_insecure_bind_stops_only_in_strict_mode(monkeypatch):
    import dev_server

    monkeypatch.setattr(dev_server, "AUTH_ENABLED", False)
    monkeypatch.setattr(dev_server, "SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("DV_STRICT_BIND", "true")
    with pytest.raises(SystemExit) as exc:
        dev_server._warn_insecure_bind()
    assert exc.value.code == 3


@pytest.mark.parametrize("auth,host", [(False, "127.0.0.1"), (True, "0.0.0.0"), (True, "127.0.0.1")])
def test_safe_combinations_do_not_warn(monkeypatch, auth, host):
    import dev_server

    monkeypatch.setattr(dev_server, "AUTH_ENABLED", auth)
    monkeypatch.setattr(dev_server, "SERVER_HOST", host)
    monkeypatch.setenv("DV_STRICT_BIND", "true")
    assert dev_server._warn_insecure_bind() is False
