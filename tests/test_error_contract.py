"""M-24: one additive error shape. Every consumer that reads only `detail` / `error` must keep working."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import limits
from backend.api import error_contract as ec
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.data import service
from backend.hosts import HostProfileError
from backend.main import app

CTX = RequestContext(login_id="tester", tenant_id="")
BODY = {"return_id": "2041", "table_mapping_path": "x.xml", "table_name": "T",
        "reporting_date": "31-MAR-2025", "reporting_period": 1, "comparison_mode": "vs_current"}


@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


# ── code mapping ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,code", [
    (400, "invalid_request"), (401, "unauthenticated"), (403, "forbidden"), (404, "not_found"),
    (405, "method_not_allowed"), (413, "payload_too_large"), (422, "validation_error"),
    (429, "rate_limited"), (503, "unavailable"), (500, "server_error"), (502, "server_error"), (418, "error"),
])
def test_status_to_code(status, code):
    assert ec.code_for(status) == code


def test_a_no_data_message_gets_its_own_code_whatever_the_status():
    assert ec.code_for(404, "No data found for T on 31-MAR-2025") == "no_data"
    assert ec.code_for(500, "No data found for T") == "no_data"
    assert ec.code_for(404, "Something else") == "not_found"


@pytest.mark.parametrize("message,code", [
    ("No data found for T on 31-MAR-2025. Dates available in DB: x", "no_data"),
    ("Invalid reporting date format: bad", "invalid_input"),
    ("Invalid Reporting Date According To Frequency.", "invalid_input"),
    ("The data could not be retrieved. Please try again or contact support.", "query_failed"),
    ("Cannot build row identifier for table 'T': CompFilterColName is empty", "mapping_incomplete"),
    ("something unforeseen", "error"), (None, "error"), ("", "error"),
])
def test_error_result_messages_to_codes(message, code):
    assert ec.error_code_for_message(message) == code


def test_error_body_shape():
    assert ec.error_body(404, "gone", "rid-1") == {"detail": "gone", "code": "not_found", "request_id": "rid-1"}
    assert ec.error_body(404, "gone", None) == {"detail": "gone", "code": "not_found"}
    assert ec.error_body(400, "x", "r", code="custom")["code"] == "custom"


# ── every kind of HTTP error carries it, and `detail` is unchanged ───────────

def test_unknown_route_is_404_with_the_contract(client):
    r = client.get("/no/such/route")
    body = r.json()
    assert r.status_code == 404 and body["detail"] == "Not Found" and body["code"] == "not_found"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_wrong_method_is_405(client):
    r = client.delete("/health")
    assert r.status_code == 405 and r.json()["code"] == "method_not_allowed"


def test_validation_errors_keep_their_detail_list(client):
    r = client.post("/variance/compute", json={**BODY, "reporting_period": 0})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "validation_error"
    assert isinstance(body["detail"], list) and body["detail"][0]["msg"]          # what the UI already joins
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_a_route_raised_http_error_is_passed_through_with_a_code(client, monkeypatch):
    monkeypatch.setattr("backend.api.variance.service.find_return_and_tables",
                        lambda name, ctx: {"error": "Return 'x' not found."})
    r = client.get("/variance/find", params={"return_name": "x"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Return 'x' not found." and r.json()["code"] == "not_found"


def test_a_no_data_http_error_has_the_no_data_code(client, monkeypatch):
    monkeypatch.setattr("backend.api.variance.service.find_return_and_tables",
                        lambda name, ctx: {"error": "No data found for x"})
    assert client.get("/variance/find", params={"return_name": "x"}).json()["code"] == "no_data"


def test_headers_set_on_the_exception_are_kept(monkeypatch):
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/_t/limited")
    def limited():
        raise HTTPException(status_code=429, detail="slow down", headers={"Retry-After": "7"})

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/_t/limited")
    assert r.headers["Retry-After"] == "7" and r.json()["code"] == "rate_limited"
    app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/_t/limited"]


def test_auth_failures_have_codes(monkeypatch):
    app.dependency_overrides.pop(require_login, None)
    from backend.auth import deps

    monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", "required")
    monkeypatch.setattr(deps.cfg, "JWT_SECRET", "test-only-secret-" + "x" * 32)   # "required" now refuses to start without one
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/auth/my-returns", params={"loginId": "x"})
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated" and r.json()["detail"]


def test_an_inbound_request_id_is_echoed_in_the_body(client):
    r = client.get("/no/such/route", headers={"X-Request-ID": "trace-77"})
    assert r.json()["request_id"] == "trace-77" == r.headers["X-Request-ID"]


def test_the_body_size_limit_response(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main.cfg, "MAX_BODY_BYTES", 10)
    r = client.post("/variance/compute", json=BODY)
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large" and r.json()["detail"]


def test_the_rate_limit_response_keeps_retry_after(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1000))
    client.get("/auth/my-returns", params={"loginId": "flood"})
    r = client.get("/auth/my-returns", params={"loginId": "flood"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1


def test_a_host_profile_error_is_400_tenant_unavailable_without_the_operator_text(client, monkeypatch):
    def boom(**kw):
        raise HostProfileError("Tenant '9' is not active (active: ['1001'])")

    monkeypatch.setattr(service, "compute_variance", boom)
    r = client.post("/variance/compute", json=BODY)
    assert r.status_code == 400 and r.json()["code"] == "tenant_unavailable"
    assert "1001" not in r.text


def test_an_unhandled_error_is_500_with_a_reference_and_a_code(monkeypatch):
    app.dependency_overrides[require_login] = lambda: CTX

    def boom(**kw):
        raise ValueError("secret detail ORA-00942")

    monkeypatch.setattr(service, "compute_variance", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/variance/compute", json=BODY)
    app.dependency_overrides.pop(require_login, None)
    body = r.json()
    assert r.status_code == 500 and body["code"] == "server_error" and "Reference:" in body["detail"]
    assert "ORA-" not in r.text and body["request_id"] in body["detail"]


# ── the HTTP-200 {"error": ...} style gets a code beside it ──────────────────

@pytest.mark.parametrize("message,code", [
    ("No data found for T on 31-MAR-2025", "no_data"),
    ("Invalid Reporting Date According To Frequency.", "invalid_input"),
    ("The data could not be retrieved. Please try again or contact support.", "query_failed"),
])
def test_compute_error_results_carry_error_code_and_keep_error(client, monkeypatch, message, code):
    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"error": message})
    r = client.post("/variance/compute", json=BODY)
    assert r.status_code == 200                                # the status is deliberately unchanged
    assert r.json() == {"error": message, "error_code": code}


def test_a_successful_compute_gains_no_error_keys(client, monkeypatch):
    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"rows": [], "columns": ["A"]})
    body = client.post("/variance/compute", json=BODY).json()
    assert "error" not in body and "error_code" not in body
