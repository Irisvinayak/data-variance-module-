"""M-42: response models document the routes without changing what they return."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import ComputeResponse
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.data import service
from backend.main import app

CTX = RequestContext(login_id="tester", tenant_id="")
COMPUTE_BODY = {
    "return_id": "2041", "table_mapping_path": "TableMapping.xml", "table_name": "T",
    "reporting_date": "31-MAR-2025", "reporting_period": 1, "comparison_mode": "vs_current",
}


@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


def _compute(client, monkeypatch, payload):
    monkeypatch.setattr(service, "compute_variance", lambda **kw: payload)
    r = client.post("/variance/compute", json=COMPUTE_BODY)
    assert r.status_code == 200
    return r.json()


# ── the schema is no longer empty ────────────────────────────────────────────

def test_openapi_describes_the_responses():
    spec = app.openapi()
    for path, method, model in [
        ("/variance/compute", "post", "ComputeResponse"),
        ("/variance/find", "get", "FindResponse"),
        ("/variance/dates", "get", "DatesResponse"),
        ("/auth/my-returns", "get", "MyReturnsResponse"),
        ("/health", "get", "HealthResponse"),
        ("/app-config", "get", "AppConfigResponse"),
    ]:
        schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(f"/{model}"), (path, schema)
    props = spec["components"]["schemas"]["ComputeResponse"]["properties"]
    assert {"rows", "columns", "truncated", "missing_periods", "duplicate_notice"} <= set(props)


# ── and the responses are exactly what they were ─────────────────────────────

def test_compute_returns_exactly_the_keys_the_service_returned(client, monkeypatch):
    payload = {"table_name": "T", "rows": [{"identifier": "A"}], "columns": ["X"]}
    assert _compute(client, monkeypatch, payload) == payload          # no extra nulls added


def test_unknown_keys_are_kept(client, monkeypatch):
    payload = {"rows": [], "columns": [], "something_new": {"a": 1}, "table_name": "T"}
    assert _compute(client, monkeypatch, payload)["something_new"] == {"a": 1}


def test_nulls_and_zeroes_the_service_returned_are_kept(client, monkeypatch):
    payload = {"rows": [{"identifier": "A", "current": {"AMT": None}, "previous": {}}], "columns": [], "truncated": False}
    out = _compute(client, monkeypatch, payload)
    assert out["rows"][0]["current"]["AMT"] is None and out["truncated"] is False


def test_the_error_style_response_still_passes(client, monkeypatch):
    assert _compute(client, monkeypatch, {"error": "No data found for T on 31-MAR-2025"}) == {
        "error": "No data found for T on 31-MAR-2025"}


def test_row_values_that_are_dates_still_serialise(client, monkeypatch):
    payload = {"rows": [{"identifier": "A", "current": {"RDATE": datetime(2025, 3, 31, 10, 30)}}], "columns": []}
    assert _compute(client, monkeypatch, payload)["rows"][0]["current"]["RDATE"].startswith("2025-03-31")


def test_the_added_data_quality_fields_pass_through(client, monkeypatch):
    payload = {"rows": [], "columns": [], "duplicate_identifiers": ["A"], "duplicate_notice": "n",
               "removed_rows": [{"identifier": "G", "previous": {"AMT": 1}}], "removed_notice": "m",
               "truncated": True, "truncation_notice": "t"}
    assert _compute(client, monkeypatch, payload) == payload


def test_a_large_result_is_not_mangled(client, monkeypatch):
    rows = [{"identifier": str(i), "current": {"AMT": i}, "previous": {"previous_1": {"AMT": {"value": i}}}} for i in range(3000)]
    out = _compute(client, monkeypatch, {"rows": rows, "columns": ["AMT"]})
    assert out["rows"] == rows


def test_my_returns_health_and_app_config_keep_their_shape(client, monkeypatch):
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: {"2", "10"})
    body = client.get("/auth/my-returns").json()
    assert set(body) == {"login_id", "tenant_id", "allowed_count", "allowed_forms"} and body["allowed_count"] == 2
    health = client.get("/health").json()
    assert health["status"] in ("ok", "misconfigured") and "version" in health
    assert set(client.get("/app-config").json()) == {"version", "profile", "requires_tenant"}


def test_find_returns_a_candidate_list_unchanged(client, monkeypatch):
    result = {"candidates": [{"return_id": "1", "return_name": "R1", "score": 90}]}
    monkeypatch.setattr("backend.api.variance.service.find_return_and_tables", lambda name, ctx: dict(result))
    assert client.get("/variance/find", params={"return_name": "R"}).json() == result


def test_dates_response_shape(client, monkeypatch):
    monkeypatch.setattr("backend.api.variance.service.get_available_dates", lambda *a, **k: ["31-MAR-2025"])
    r = client.get("/variance/dates", params={"return_id": "1", "table_name": "T", "table_mapping_path": "x.xml"})
    assert r.status_code == 200 and r.json() == {"dates": ["31-MAR-2025"]}


def test_the_model_itself_accepts_a_partial_dict():
    m = ComputeResponse(**{"rows": []})
    assert m.model_dump(exclude_unset=True) == {"rows": []}
