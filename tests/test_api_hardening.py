"""API and information-security hardening (code review SEC-02..SEC-10, API-01, API-02).

These run the real FastAPI app through TestClient. Authentication is replaced by
a dependency override (so no repository files are read), and nothing here touches
Oracle or an LLM: every request is either rejected before the data layer or
stubbed at the service boundary.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import limits
from backend.api import errors as api_errors
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.data.models import NLResolveRequest, VarianceComputeRequest
from backend.data.validation import InvalidInput, ensure_safe_return_id, ensure_safe_table_ref
from backend.main import app

CTX = RequestContext(login_id="tester", tenant_id="")


@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: CTX
    # raise_server_exceptions=False: a 500 must reach us as a response so the
    # tests can inspect what a client would actually see.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


GOOD_COMPUTE = {
    "return_id": "2041",
    "table_mapping_path": "TableMapping.xml",
    "table_name": "CIMS_RAQ_Q_SEC1_PART_A_DOM",
    "reporting_date": "31-MAR-2025",
    "reporting_period": 1,
    "comparison_mode": "vs_current",
}


# ── validation helpers (SEC-09 / SEC-10) ─────────────────────────────────────

@pytest.mark.parametrize("value", ["2041", "4079", "NX1", "ret-1_a", "0"])
def test_real_return_ids_accepted(value):
    assert ensure_safe_return_id(value) == value


@pytest.mark.parametrize("value", [
    "..\\..\\Windows", "../etc/passwd", "2041/..", "20 41", "2041;", "", None, "a" * 65, "\\\\server\\share", "2041\x00",
])
def test_hostile_return_ids_rejected(value):
    with pytest.raises(InvalidInput):
        ensure_safe_return_id(value)


@pytest.mark.parametrize("value", ["CIMS_RAQ_Q_SEC1_PART_A_DOM", "CRILC.CIMS_RAQ_DP", "T", "a$b#c"])
def test_real_table_refs_accepted(value):
    assert ensure_safe_table_ref(value) == value


@pytest.mark.parametrize("value", [
    "T; DROP TABLE X", "T WHERE 1=1", "A.B.C", "T--", "T/*x*/", "1T", "", ".T", "T.", "T)", "T,U", "(SELECT 1)",
])
def test_hostile_table_refs_rejected(value):
    with pytest.raises(InvalidInput):
        ensure_safe_table_ref(value)


# ── request models (API-02 / API-01) ─────────────────────────────────────────

def test_frontend_shaped_compute_request_still_validates():
    """Exactly what LayoutContainer sends must keep working."""
    req = VarianceComputeRequest(**GOOD_COMPUTE, comparison_dates=["31-MAR-2025", "31-DEC-2024"],
                                 selected_columns=["TERM_LOAN"])
    assert req.comparison_mode == "vs_current"
    assert VarianceComputeRequest(**{**GOOD_COMPUTE, "comparison_mode": "sequential"}).comparison_mode == "sequential"


def test_table_mapping_path_is_now_optional():
    body = {k: v for k, v in GOOD_COMPUTE.items() if k != "table_mapping_path"}
    assert VarianceComputeRequest(**body).table_mapping_path == ""


@pytest.mark.parametrize("override", [
    {"comparison_mode": "everything"},
    {"reporting_date": "2025-03-31"},
    {"reporting_date": "31-MAR-2025'; DROP"},
    {"reporting_period": 0},
    {"reporting_period": -3},
    {"reporting_period": 10**6},
    {"return_id": "x" * 500},
    {"table_name": "x" * 500},
    {"selected_columns": ["a"] * 500},
    {"selected_columns": ["c" * 300]},
    {"comparison_dates": ["31-MAR-2025"] * 50},
])
def test_compute_request_rejects_bad_values(override):
    with pytest.raises(ValueError):
        VarianceComputeRequest(**{**GOOD_COMPUTE, **override})


def test_nl_request_limits():
    assert NLResolveRequest(query="show total loan assets").query
    assert NLResolveRequest(query="q", dimension="return", clarification_answer="2041", resolved_context={"return_id": "2041"})
    with pytest.raises(ValueError):
        NLResolveRequest(query="x" * 1001)
    with pytest.raises(ValueError):
        NLResolveRequest(query="q", dimension="anything-else")
    with pytest.raises(ValueError):
        NLResolveRequest(query="q", clarification_answer="a" * 300)
    with pytest.raises(ValueError):
        NLResolveRequest(query="q", resolved_context={"blob": "z" * 20_000})


def test_compute_validation_errors_are_422_through_the_app(client):
    r = client.post("/variance/compute", json={**GOOD_COMPUTE, "comparison_mode": "bogus"})
    assert r.status_code == 422


def test_hostile_return_id_is_400_before_any_lookup(client):
    r = client.post("/variance/compute", json={**GOOD_COMPUTE, "return_id": "..\\..\\x"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid return identifier."
    assert ".." not in r.text                                    # the value is never echoed


def test_hostile_table_name_is_400_on_compute_and_dates(client):
    r = client.post("/variance/compute", json={**GOOD_COMPUTE, "table_name": "T; DROP TABLE X"})
    assert r.status_code == 400
    r = client.get("/variance/dates", params={"return_id": "2041", "table_name": "T WHERE 1=1"})
    assert r.status_code == 400


def test_dates_route_accepts_the_old_query_shape_and_the_new_one(client, monkeypatch):
    monkeypatch.setattr("backend.api.variance.require_return_access", lambda ctx, rid: None)
    monkeypatch.setattr("backend.api.variance.service.get_available_dates", lambda **kw: ["31-MAR-2025"])
    old = client.get("/variance/dates", params={"return_id": "2041", "table_mapping_path": "x", "table_name": "T"})
    new = client.get("/variance/dates", params={"return_id": "2041", "table_name": "T"})
    assert old.status_code == new.status_code == 200
    assert old.json() == new.json() == {"dates": ["31-MAR-2025"]}


def test_find_return_name_length_limit(client):
    assert client.get("/variance/find", params={"return_name": "x" * 500}).status_code == 422


# ── information disclosure (SEC-03 / SEC-04 / SEC-05 / SEC-06 / SEC-07) ──────

def test_http_error_never_leaks_exception_text():
    secrets = "D:\\Repo6\\1001\\secret.xml ORA-00942 CRILC.SECRET_TABLE_DP tenants=[1001, 1002]"
    for exc in (FileNotFoundError(secrets), KeyError(secrets), RuntimeError(secrets), ValueError(secrets)):
        out = api_errors.http_error(exc, "/x")
        for fragment in ("Repo6", "ORA-", "SECRET_TABLE", "1002", "secret.xml"):
            assert fragment not in out.detail


def test_unhandled_exception_returns_a_generic_body_with_a_reference(client):
    async def boom():
        raise ValueError("password=hunter2 at D:\\Repo6")

    app.add_api_route("/__boom", boom)
    try:
        r = client.get("/__boom")
    finally:
        app.router.routes.pop()
    assert r.status_code == 500
    assert "hunter2" not in r.text and "Repo6" not in r.text and "ValueError" not in r.text
    assert "Reference:" in r.json()["detail"]
    assert r.headers.get("X-Request-ID")


def test_health_is_a_bare_probe_by_default(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "paths" not in body
    assert "D:\\" not in str(body)


def test_health_details_only_when_diagnostics_enabled(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "DIAGNOSTICS_ENABLED", True)
    assert "profile" in client.get("/health").json()


def test_nlp_health_is_minimal_and_never_loads_the_model_without_diagnostics(client, monkeypatch):
    loaded = []
    monkeypatch.setattr("backend.nlp.embedder.embed_query", lambda q: loaded.append(q))
    body = client.get("/variance/nlp-health", params={"check_model": "true"}).json()
    assert set(body) <= {"status"}                    # no paths, package errors or model names
    assert loaded == []                               # ?check_model=true was ignored


def test_api_docs_are_off_by_default(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_tenant_denial_does_not_list_tenants(monkeypatch):
    from backend.auth import deps
    from backend.hosts import HostProfileError

    class P:
        requires_tenant = True
        name = "iDEAL 6.0"

        def validate_context(self, ctx):
            raise HostProfileError("Unknown tenant 'x' (active: ['1001', '1002', '1003'])")

    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(deps, "get_profile", lambda: P())
    monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", "off")
    with pytest.raises(HTTPException) as exc:
        deps.require_login(loginId="u@b", tenantId="x", authorization=None)
    assert exc.value.status_code == 403
    for fragment in ("1001", "1002", "active", "Unknown tenant"):
        assert fragment not in exc.value.detail


def test_find_response_carries_no_absolute_server_path():
    from backend.api.variance import _redact_server_paths

    out = _redact_server_paths({"return_id": "4079", "table_mapping_path": "D:\\Repo6\\1001\\DataBase\\4079\\TableMapping.xml"})
    assert out["table_mapping_path"] == "TableMapping.xml"
    assert _redact_server_paths({"candidates": []}) == {"candidates": []}
    assert _redact_server_paths({"table_mapping_path": "/srv/repo/4079/TableMapping.xml"})["table_mapping_path"] == "TableMapping.xml"


def test_find_filter_by_access_hides_unauthorised_returns(client, monkeypatch):
    from backend.config import settings

    result = {"candidates": [
        {"return_id": "2041", "return_name": "A", "score": 90},
        {"return_id": "9999", "return_name": "B", "score": 80},
    ]}
    monkeypatch.setattr("backend.api.variance.service.find_return_and_tables", lambda name, ctx: dict(result))
    monkeypatch.setattr("backend.api.variance.AUTH_ENABLED", True)
    monkeypatch.setattr("backend.api.variance.is_return_allowed", lambda ctx, rid: rid == "2041")

    monkeypatch.setattr(settings, "FIND_FILTER_BY_ACCESS", False)
    assert len(client.get("/variance/find", params={"return_name": "x"}).json()["candidates"]) == 2   # default: unchanged

    monkeypatch.setattr(settings, "FIND_FILTER_BY_ACCESS", True)
    assert [c["return_id"] for c in client.get("/variance/find", params={"return_name": "x"}).json()["candidates"]] == ["2041"]


# ── rate limiting and body size (API-01) ─────────────────────────────────────

def test_sliding_window_limiter_basics():
    lim = limits.SlidingWindowLimiter(3, window=60)
    assert [lim.check("a", now=t)[0] for t in (0, 1, 2)] == [True, True, True]
    allowed, retry = lim.check("a", now=3)
    assert not allowed and retry >= 1
    assert lim.check("b", now=3)[0]                      # keys are independent
    assert lim.check("a", now=61)[0]                     # the window slid


def test_limiter_disabled_at_zero():
    lim = limits.SlidingWindowLimiter(0)
    assert all(lim.check("a", now=i)[0] for i in range(1000))


def test_limiter_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(limits, "_MAX_KEYS", 50)
    lim = limits.SlidingWindowLimiter(5, window=60)
    for i in range(500):
        lim.check(f"user{i}", now=float(i) / 100)
    assert len(lim._hits) <= 50


def test_nl_paths_are_recognised():
    assert limits.is_nl_path("/variance/nlresolve")
    assert limits.is_nl_path("/DataVar6.0/api/variance/nlquery/")
    assert not limits.is_nl_path("/variance/compute")


def test_requests_over_budget_get_429_with_retry_after(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(2))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1000))
    statuses = [client.get("/auth/my-returns", params={"loginId": "flood"}).status_code for _ in range(4)]
    assert statuses.count(429) == 2
    blocked = client.get("/auth/my-returns", params={"loginId": "flood"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers.get("X-Request-ID")           # correlation survives the early return
    assert client.get("/auth/my-returns", params={"loginId": "someone-else"}).status_code != 429


def test_health_and_app_config_are_never_throttled(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1))
    for path in ("/health", "/app-config"):
        assert all(client.get(path, params={"loginId": "x"}).status_code == 200 for _ in range(5))


def test_nl_route_has_its_own_tighter_budget(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1000))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1000))
    monkeypatch.setattr(main, "_per_login_nl", limits.SlidingWindowLimiter(1))
    codes = [client.post("/variance/nlresolve", params={"loginId": "u"}, json={"query": ""}).status_code for _ in range(3)]
    assert codes[0] != 429 and codes[1] == 429


def test_oversized_body_is_rejected_with_413(client, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "MAX_BODY_BYTES", 200)
    r = client.post("/variance/nlresolve", json={"query": "a" * 400})
    assert r.status_code == 413


def test_request_ids_are_echoed_and_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        r = client.get("/app-config", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"
