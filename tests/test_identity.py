"""Server-side identity verification (code review C-01 / SEC-01 / FE-01).

Tokens are built here with the same HMAC scheme the iDEAL 6.0 host uses
(HS512, claims LoginId / TenantId), so these tests exercise the real contract
without needing the host or any secret from it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.auth import deps
from backend.auth.identity import (
    TokenError,
    extract_bearer,
    identity_from_claims,
    verify_jwt,
)

SECRET = "unit-test-secret-not-a-real-key"
ISS = "iDealWebAPI"
AUD = "https://example.test"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(claims: dict, secret: str = SECRET, alg: str = "HS512", header: dict | None = None) -> str:
    head = header if header is not None else {"alg": alg, "typ": "JWT"}
    h = _b64(json.dumps(head).encode())
    p = _b64(json.dumps(claims).encode())
    if alg == "none":
        return f"{h}.{p}.x"
    digest = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}[alg]
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), digest).digest()
    return f"{h}.{p}.{_b64(sig)}"


def good_claims(**over) -> dict:
    c = {"LoginId": "user@bank.test", "TenantId": "1001", "iss": ISS, "aud": AUD, "exp": time.time() + 600}
    c.update(over)
    return c


def verify(token: str, **over):
    kwargs = dict(secret=SECRET, algorithms=("HS512",), issuer=ISS, audience=AUD, leeway=0)
    kwargs.update(over)
    return verify_jwt(token, **kwargs)


# ── verify_jwt ────────────────────────────────────────────────────────────────

def test_valid_token_verifies():
    claims = verify(make_token(good_claims()))
    assert claims["LoginId"] == "user@bank.test"


def test_wrong_secret_rejected():
    with pytest.raises(TokenError, match="signature"):
        verify(make_token(good_claims(), secret="some-other-secret"))


def test_tampered_payload_rejected():
    token = make_token(good_claims())
    h, p, s = token.split(".")
    forged = _b64(json.dumps(good_claims(LoginId="admin@bank.test")).encode())
    with pytest.raises(TokenError, match="signature"):
        verify(f"{h}.{forged}.{s}")


def test_alg_none_rejected():
    with pytest.raises(TokenError, match="not allowed"):
        verify(make_token(good_claims(), alg="none"))


def test_algorithm_downgrade_rejected():
    """A correctly signed HS256 token must not pass when only HS512 is allowed."""
    with pytest.raises(TokenError, match="not allowed"):
        verify(make_token(good_claims(), alg="HS256"))


def test_configured_alternative_algorithm_accepted():
    assert verify(make_token(good_claims(), alg="HS256"), algorithms=("HS256", "HS512"))


def test_expired_token_rejected():
    with pytest.raises(TokenError, match="expired"):
        verify(make_token(good_claims(exp=time.time() - 10)))


def test_expiry_respects_leeway():
    assert verify(make_token(good_claims(exp=time.time() - 10)), leeway=60)


def test_token_without_exp_rejected():
    claims = good_claims()
    del claims["exp"]
    with pytest.raises(TokenError, match="exp"):
        verify(make_token(claims))


def test_non_numeric_exp_rejected():
    with pytest.raises(TokenError, match="exp"):
        verify(make_token(good_claims(exp="never")))
    with pytest.raises(TokenError, match="exp"):
        verify(make_token(good_claims(exp=True)))


def test_nan_and_infinite_exp_rejected():
    """json.loads accepts NaN/Infinity; a signed token must not use them to never expire."""
    h = _b64(json.dumps({"alg": "HS512"}).encode())
    for raw in (b'{"LoginId":"a","exp":NaN}', b'{"LoginId":"a","exp":Infinity}'):
        p = _b64(raw)
        sig = _b64(hmac.new(SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha512).digest())
        with pytest.raises(TokenError, match="exp"):
            verify(f"{h}.{p}.{sig}")


def test_non_ascii_token_is_a_token_error_not_a_crash():
    """A non-ASCII segment raised UnicodeEncodeError, i.e. a 500 instead of a 401."""
    good = make_token(good_claims())
    h, p, s = good.split(".")
    for bad in (f"{h}.é{p}.{s}", f"é{h}.{p}.{s}", f"{h}.{p}.é{s}"):
        with pytest.raises(TokenError):
            verify(bad)


def test_non_ascii_token_gets_401_through_the_dependency(identity_cfg):
    identity_cfg("optional")
    h, p, s = make_token(good_claims()).split(".")
    with pytest.raises(HTTPException) as exc:
        deps._verified_identity(f"Bearer {h}.é{p}.{s}", _Profile(False))
    assert exc.value.status_code == 401


def test_not_before_in_future_rejected():
    with pytest.raises(TokenError, match="not yet valid"):
        verify(make_token(good_claims(nbf=time.time() + 600)))


def test_issuer_and_audience_checked_when_configured():
    with pytest.raises(TokenError, match="issuer"):
        verify(make_token(good_claims(iss="someone-else")))
    with pytest.raises(TokenError, match="audience"):
        verify(make_token(good_claims(aud="https://elsewhere.test")))
    # audience may be a list
    assert verify(make_token(good_claims(aud=["x", AUD])))


def test_issuer_and_audience_skipped_when_not_configured():
    assert verify(make_token(good_claims(iss="anything", aud="anything")), issuer="", audience="")


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", "..", "a..c", "!!!.???.***"])
def test_malformed_tokens_rejected(token):
    with pytest.raises(TokenError):
        verify(token)


def test_no_secret_configured_fails_closed():
    with pytest.raises(TokenError, match="secret"):
        verify(make_token(good_claims()), secret="")


def test_no_supported_algorithm_fails_closed():
    with pytest.raises(TokenError, match="algorithm"):
        verify(make_token(good_claims()), algorithms=("RS256",))


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("Bearer abc.def.ghi", "abc.def.ghi"),
    ("bearer   abc.def.ghi  ", "abc.def.ghi"),
    ("Basic abc", None), ("Bearer", None), ("Bearer   ", None), ("", None), (None, None),
])
def test_extract_bearer(header, expected):
    assert extract_bearer(header) == expected


def test_identity_from_claims_requires_login():
    with pytest.raises(TokenError, match="LoginId"):
        identity_from_claims({"TenantId": "1"}, login_claim="LoginId", tenant_claim="TenantId", requires_tenant=False)


def test_identity_from_claims_tenant_only_when_required():
    who = identity_from_claims({"LoginId": "a@b"}, login_claim="LoginId", tenant_claim="TenantId", requires_tenant=False)
    assert who.tenant_id == ""
    with pytest.raises(TokenError, match="TenantId"):
        identity_from_claims({"LoginId": "a@b"}, login_claim="LoginId", tenant_claim="TenantId", requires_tenant=True)


# ── the dependency, per mode ──────────────────────────────────────────────────

class _Profile:
    def __init__(self, requires_tenant):
        self.requires_tenant = requires_tenant


@pytest.fixture
def identity_cfg(monkeypatch):
    def configure(mode, **over):
        monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", mode)
        monkeypatch.setattr(deps.cfg, "JWT_SECRET", over.get("secret", SECRET))
        monkeypatch.setattr(deps.cfg, "JWT_ALGORITHMS", ("HS512",))
        monkeypatch.setattr(deps.cfg, "JWT_ISSUER", ISS)
        monkeypatch.setattr(deps.cfg, "JWT_AUDIENCE", AUD)
        monkeypatch.setattr(deps.cfg, "JWT_LEEWAY_SEC", 0)
    return configure


def test_mode_off_ignores_even_a_bad_token(identity_cfg):
    identity_cfg("off")
    assert deps._verified_identity("Bearer garbage", _Profile(True)) is None


def test_mode_optional_without_token_uses_url_identity(identity_cfg):
    identity_cfg("optional")
    assert deps._verified_identity(None, _Profile(False)) is None


def test_mode_optional_valid_token_gives_claims(identity_cfg):
    identity_cfg("optional")
    out = deps._verified_identity(f"Bearer {make_token(good_claims())}", _Profile(True))
    assert out == ("user@bank.test", "1001")


def test_mode_optional_bad_token_is_401_not_a_silent_fallback(identity_cfg):
    identity_cfg("optional")
    for bad in (make_token(good_claims(), secret="wrong"), make_token(good_claims(exp=time.time() - 99)), "junk"):
        with pytest.raises(HTTPException) as exc:
            deps._verified_identity(f"Bearer {bad}", _Profile(True))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Authentication failed."      # never the reason


def test_mode_required_without_token_is_401(identity_cfg):
    identity_cfg("required")
    with pytest.raises(HTTPException) as exc:
        deps._verified_identity(None, _Profile(False))
    assert exc.value.status_code == 401


def test_mode_required_with_unset_secret_fails_closed(identity_cfg):
    identity_cfg("required", secret="")
    with pytest.raises(HTTPException) as exc:
        deps._verified_identity(f"Bearer {make_token(good_claims())}", _Profile(False))
    assert exc.value.status_code == 401


def test_tenant_claim_required_under_a_tenant_scoped_host(identity_cfg):
    identity_cfg("optional")
    claims = good_claims()
    del claims["TenantId"]
    with pytest.raises(HTTPException) as exc:
        deps._verified_identity(f"Bearer {make_token(claims)}", _Profile(True))
    assert exc.value.status_code == 401


# ── end to end through the real app ───────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from backend.main import app

    # Only the user lookup is stubbed; verification, the dependency and routing
    # are the real thing.
    monkeypatch.setattr(deps, "AUTH_ENABLED", False)
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: {"2041"})
    monkeypatch.setattr(deps, "get_allowed_form_ids", lambda ctx: {"2041"})
    yield TestClient(app)


def test_url_identity_still_works_by_default(client, monkeypatch):
    monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", "off")
    r = client.get("/auth/my-returns", params={"loginId": "iris810"})
    assert r.status_code == 200
    assert r.json()["login_id"] == "iris810"


def test_verified_token_overrides_a_spoofed_url(client, identity_cfg):
    identity_cfg("optional")
    token = make_token(good_claims(LoginId="real.user@bank.test", TenantId=""))
    r = client.get(
        "/auth/my-returns",
        params={"loginId": "someone.else@bank.test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["login_id"] == "real.user@bank.test"       # the token won, not the URL


def test_forged_token_gets_401_through_the_app(client, identity_cfg):
    identity_cfg("optional")
    r = client.get(
        "/auth/my-returns",
        params={"loginId": "admin"},
        headers={"Authorization": f"Bearer {make_token(good_claims(), secret='attacker')}"},
    )
    assert r.status_code == 401
    assert "signature" not in r.text.lower()


def test_required_mode_rejects_url_only_identity(client, identity_cfg):
    identity_cfg("required")
    r = client.get("/auth/my-returns", params={"loginId": "iris810"})
    assert r.status_code == 401
