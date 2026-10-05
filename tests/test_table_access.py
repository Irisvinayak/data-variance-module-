"""require_tables_access: sql_agent's H-02 gate, ported to the variance module.

The gate is host-agnostic (it consumes get_allowed_form_ids), so the 5.5 and 6.0
cases differ only in the shape of the department master value the allow-list is
parsed from -- covered at the bottom through the real _split_ids.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.auth import deps
from backend.auth.service import _split_ids
from backend.config import RequestContext

CTX = RequestContext(login_id="user@bank.test", tenant_id="")


def _tbl(name, rid):
    return {"table": name, "return_id": rid}


@pytest.fixture
def allow(monkeypatch):
    def _set(ids, enabled=True):
        monkeypatch.setattr(deps, "AUTH_ENABLED", enabled)
        monkeypatch.setattr(deps, "get_allowed_form_ids", lambda ctx: ids)
    return _set


def test_allowed_returns_pass(allow):
    allow({"2001", "2007"})
    deps.require_tables_access(CTX, [_tbl("T1", "2001"), _tbl("T2", "2007")])


def test_return_outside_allow_list_is_denied(allow):
    allow({"2001"})
    with pytest.raises(HTTPException) as e:
        deps.require_tables_access(CTX, [_tbl("T1", "2999")])
    assert e.value.status_code == 403


def test_mixed_allowed_and_denied_is_denied_in_full(allow):
    allow({"2001"})
    with pytest.raises(HTTPException) as e:
        deps.require_tables_access(CTX, [_tbl("T1", "2001"), _tbl("T2", "2999")])
    assert e.value.status_code == 403


@pytest.mark.parametrize("rid", [None, "", "  "])
def test_unresolved_return_fails_closed(allow, rid):
    allow({"2001"})
    with pytest.raises(HTTPException) as e:
        deps.require_tables_access(CTX, [_tbl("T1", rid)])
    assert e.value.status_code == 403


def test_empty_table_list_fails_closed(allow):
    allow({"2001"})
    with pytest.raises(HTTPException):
        deps.require_tables_access(CTX, [])


def test_unknown_user_is_denied(allow):
    allow(None)
    with pytest.raises(HTTPException) as e:
        deps.require_tables_access(CTX, [_tbl("T1", "2001")])
    assert e.value.status_code == 403


def test_known_user_with_no_grants_is_denied(allow):
    allow(set())
    with pytest.raises(HTTPException):
        deps.require_tables_access(CTX, [_tbl("T1", "2001")])


def test_auth_disabled_bypasses(allow):
    allow(None, enabled=False)
    deps.require_tables_access(CTX, [_tbl("T1", None)])


@pytest.mark.parametrize(
    "host, raw, delim",
    [
        ("5.5", "2001|2007|2035", "|"),
        ("6.0", "2029,4089,4070", ","),
    ],
)
def test_gate_over_each_hosts_allow_list_shape(allow, host, raw, delim):
    ids = _split_ids(raw, delim)
    allow(ids)
    first = sorted(ids)[0]
    deps.require_tables_access(CTX, [_tbl("T1", first)])
    with pytest.raises(HTTPException):
        deps.require_tables_access(CTX, [_tbl("T1", first), _tbl("T2", "9999")])
