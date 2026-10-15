"""C-01: tenant binding, URL-borne credentials, and signing-secret handling.

Complements tests/test_identity.py (token verification itself). Everything here
runs the real FastAPI app; only the user/department lookup and the host profile
are stubbed. Tokens are synthetic, signed with a throwaway test key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from backend.auth import deps

SECRET = "c01-unit-test-key-not-a-real-secret-0123456789"
ISS = "iDealWebAPI"
AUD = "https://example.test"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def backend_py_files():
    """Backend source files, pruning the virtualenv instead of walking it."""
    for folder, dirs, files in os.walk(ROOT / "backend"):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "node_modules")]
        for name in files:
            if name.endswith(".py"):
                yield pathlib.Path(folder) / name


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(claims: dict, secret: str = SECRET) -> str:
    h = _b64(json.dumps({"alg": "HS512", "typ": "JWT"}).encode())
    p = _b64(json.dumps(claims).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha512).digest()
    return f"{h}.{p}.{_b64(sig)}"


def claims(**over) -> dict:
    c = {"LoginId": "user@bank.test", "TenantId": "1001", "iss": ISS, "aud": AUD, "exp": time.time() + 600}
    c.update(over)
    return c


def bearer(**over) -> dict:
    return {"Authorization": f"Bearer {make_token(claims(**over))}"}


class TenantProfile:
    """A tenant-scoped host, like iDEAL 6.0: the tenant is required."""

    requires_tenant = True
    name = "iDEAL 6.0 (test)"

    def validate_context(self, ctx):
        pass


class PlainProfile:
    """An untenanted host, like iDEAL 5.5."""

    requires_tenant = False
    name = "iDEAL 5.5 (test)"

    def validate_context(self, ctx):
        pass


def configure(monkeypatch, mode, profile, auth_enabled=True):
    monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", mode)
    monkeypatch.setattr(deps.cfg, "JWT_SECRET", SECRET)
    monkeypatch.setattr(deps.cfg, "JWT_ALGORITHMS", ("HS512",))
    monkeypatch.setattr(deps.cfg, "JWT_ISSUER", ISS)
    monkeypatch.setattr(deps.cfg, "JWT_AUDIENCE", AUD)
    monkeypatch.setattr(deps.cfg, "JWT_LEEWAY_SEC", 0)
    monkeypatch.setattr(deps, "AUTH_ENABLED", auth_enabled)
    monkeypatch.setattr(deps, "get_profile", lambda: profile)
    monkeypatch.setattr(deps, "get_allowed_form_ids", lambda ctx: {"4079"})
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: {"4079"})


@pytest.fixture
def client60(monkeypatch):
    from backend.main import app

    configure(monkeypatch, "required", TenantProfile())
    return TestClient(app)


@pytest.fixture
def client55(monkeypatch):
    from backend.main import app

    configure(monkeypatch, "off", PlainProfile(), auth_enabled=False)
    return TestClient(app)


def whoami(client, headers=None, **params):
    return client.get("/auth/my-returns", params=params, headers=headers or {})


# ── 6.0: the tenant is bound to the verified token ───────────────────────────

def test_valid_token_and_correct_tenant_is_allowed(client60):
    r = whoami(client60, {**bearer(), "X-Tenant-Id": "1001"})
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] == "1001" and r.json()["login_id"] == "user@bank.test"


def test_valid_token_alone_still_works(client60):
    """Existing authenticated use: the token carries login and tenant."""
    r = whoami(client60, bearer())
    assert r.status_code == 200 and r.json()["tenant_id"] == "1001"


def test_missing_token_is_401(client60):
    r = whoami(client60, loginId="user@bank.test", tenantId="1001")
    assert r.status_code == 401
    body = r.json()
    assert body["detail"] == "Authentication failed." and body["code"] == "unauthenticated"


def test_invalid_token_is_401(client60):
    bad = {"Authorization": f"Bearer {make_token(claims(), secret='not-the-secret')}"}
    assert whoami(client60, bad).status_code == 401


def test_expired_token_is_401(client60):
    assert whoami(client60, bearer(exp=time.time() - 3600)).status_code == 401


def test_wrong_tenant_in_header_is_403_and_echoes_nothing(client60):
    r = whoami(client60, {**bearer(), "X-Tenant-Id": "1002"})
    assert r.status_code == 403
    assert "1001" not in r.text and "1002" not in r.text


def test_wrong_tenant_in_query_is_403(client60):
    assert whoami(client60, bearer(), tenantId="1002").status_code == 403


def test_matching_tenant_in_both_places_is_allowed(client60):
    assert whoami(client60, {**bearer(), "X-Tenant-Id": "1001"}, tenantId="1001").status_code == 200


def test_one_wrong_tenant_among_matching_ones_is_still_403(client60):
    assert whoami(client60, {**bearer(), "X-Tenant-Id": "1001"}, tenantId="9999").status_code == 403


def test_header_name_case_does_not_matter(client60):
    assert whoami(client60, {**bearer(), "x-tenant-id": "1002"}).status_code == 403


def test_token_without_a_tenant_claim_is_401_even_if_a_tenant_is_supplied(client60):
    c = claims()
    del c["TenantId"]
    r = whoami(client60, {"Authorization": f"Bearer {make_token(c)}", "X-Tenant-Id": "1001"}, tenantId="1001")
    assert r.status_code == 401, "a client-supplied tenant must never fill in for a missing claim"


def test_missing_tenant_is_401_without_a_token(monkeypatch):
    """Legacy URL identity (identity mode off) still has to name a tenant."""
    from backend.main import app

    configure(monkeypatch, "off", TenantProfile())
    r = whoami(TestClient(app), loginId="user@bank.test")
    assert r.status_code == 401


def test_tenant_not_known_to_the_host_is_403_with_a_generic_message(monkeypatch):
    from backend.hosts import HostProfileError
    from backend.main import app

    class Strict(TenantProfile):
        def validate_context(self, ctx):
            raise HostProfileError("Unknown tenant (active: ['1001', '1002', '1003'])")

    configure(monkeypatch, "required", Strict())
    r = whoami(TestClient(app), bearer())
    assert r.status_code == 403
    assert "1002" not in r.text and "active" not in r.text.lower()


# ── 5.5: untenanted host and URL credentials ─────────────────────────────────

def test_5_5_normal_request_still_works(client55):
    r = whoami(client55, loginId="iris810")
    assert r.status_code == 200 and r.json()["login_id"] == "iris810"


def test_5_5_tenant_rules_do_not_apply(monkeypatch):
    """No tenants in 5.5: a token with an empty tenant plus a stray header must work."""
    from backend.main import app

    configure(monkeypatch, "optional", PlainProfile())
    r = whoami(TestClient(app), {**bearer(TenantId=""), "X-Tenant-Id": "1001"})
    assert r.status_code == 200


CREDENTIAL_PARAMS = ["token", "jwt", "access_token", "aspSession", "session", "sessionId", "_at"]


@pytest.mark.parametrize("param", CREDENTIAL_PARAMS)
def test_a_credential_in_the_query_string_does_not_authenticate_when_required(client60, param):
    r = client60.get("/auth/my-returns", params={"loginId": "user@bank.test", "tenantId": "1001", param: make_token(claims())})
    assert r.status_code == 401, f"?{param}= must never authenticate a request"


@pytest.mark.parametrize("param", CREDENTIAL_PARAMS)
def test_a_credential_in_the_query_string_is_ignored_not_adopted(client55, param):
    """Legacy identity stays exactly as the URL says; a token-looking parameter changes nothing."""
    r = client55.get("/auth/my-returns", params={"loginId": "iris810", param: make_token(claims(LoginId="other@bank.test"))})
    assert r.status_code == 200 and r.json()["login_id"] == "iris810"


def test_no_backend_module_consumes_a_session_credential():
    """The 5.5 session cookie value used to travel in the URL as aspSession; the
    backend must never read such a parameter."""
    needles = ("aspSession", "asp_session", ".AspNetCore.Session")
    offenders = [
        str(p.relative_to(ROOT))
        for p in backend_py_files()
        if any(n in p.read_text(encoding="utf-8", errors="ignore") for n in needles)
    ]
    assert offenders == [], offenders


# ── the signing secret: environment only, never exposed ─────────────────────

def _run_probe(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=False)


def test_secret_is_read_from_the_environment():
    out = _run_probe("import backend.config.settings as s; print(repr(s.JWT_SECRET))",
                     {**os.environ, "DV_JWT_SECRET": "value-from-the-environment-123"})
    assert "value-from-the-environment-123" in out.stdout, out.stderr[-400:]


def test_there_is_no_default_secret():
    env = {k: v for k, v in os.environ.items() if k != "DV_JWT_SECRET"}
    out = _run_probe("import backend.config.settings as s; print(repr(s.JWT_SECRET))", env)
    assert out.stdout.strip() in ("''", '""'), "an unset secret must stay empty (fail closed), never default to a value"


def test_an_unset_secret_means_tokens_are_refused_not_accepted(monkeypatch):
    from backend.main import app

    configure(monkeypatch, "required", TenantProfile())
    monkeypatch.setattr(deps.cfg, "JWT_SECRET", "")
    assert whoami(TestClient(app), bearer()).status_code == 401


def test_no_signing_secret_is_hard_coded_in_the_backend():
    assignment = re.compile(r"""(?ix)(?<![A-Za-z0-9_])(jwt_secret|signing_secret|secret_key|secretkey)[ ]*[:=][ ]*(?:str[ ]*=[ ]*)?["'][^"'\s]{12,}["']""")
    offenders = []
    for p in backend_py_files():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if assignment.search(line) and "os.getenv" not in line and "os.environ" not in line:
                offenders.append(f"{p.relative_to(ROOT)}:{n}")
    assert offenders == [], offenders


def test_env_example_ships_no_secret_value():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*#?\s*DV_JWT_SECRET\s*=\s*(\S*)", line)
        if m:
            assert m.group(1) == "" or m.group(1).startswith("#"), "DV_JWT_SECRET must be blank in the template"


def test_secret_bearing_env_files_are_not_tracked_by_git():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    tracked = subprocess.run(["git", "ls-files", ".env", "frontend/.env"], capture_output=True, text=True, cwd=ROOT, check=False).stdout.split()
    assert tracked == [], f"secret-bearing env files are tracked: {tracked}"


def test_gitignore_covers_env_files():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignore]


def test_the_secret_never_reaches_logs_or_responses(client60, caplog):
    with caplog.at_level(logging.DEBUG):
        responses = [
            whoami(client60, {"Authorization": f"Bearer {make_token(claims(), secret='x')}"}),   # forged
            whoami(client60, bearer(exp=time.time() - 99)),                                       # expired
            whoami(client60, {"Authorization": "Bearer not.a.jwt"}),                              # garbage
            whoami(client60, {**bearer(), "X-Tenant-Id": "1002"}),                                # wrong tenant
            whoami(client60, bearer()),                                                           # success
            client60.get("/app-config"),
            client60.get("/health"),
        ]
    everything = caplog.text + " ".join(r.text for r in responses) + " ".join(str(dict(r.headers)) for r in responses)
    assert SECRET not in everything
    assert make_token(claims()).split(".")[2] not in caplog.text        # not even a token signature is logged


def test_diagnostics_never_expose_the_secret(monkeypatch):
    from backend.config import settings
    from backend.main import app

    monkeypatch.setattr(settings, "DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(deps.cfg, "JWT_SECRET", SECRET)
    client = TestClient(app)
    for path in ("/health", "/app-config"):
        assert SECRET not in client.get(path).text


# ── H-01 / C-01: what "required" mode guarantees once it is switched on ──────

def test_required_mode_answers_only_for_the_token_holder_whatever_the_url_says(client60, monkeypatch):
    """The enumeration H-01 describes: asking /auth/my-returns about someone else's login."""
    monkeypatch.setattr(deps, "get_allowed_form_ids", lambda ctx: {"4079"} if ctx.login_id == "user@bank.test" else {"SECRET-9"})
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", deps.get_allowed_form_ids)
    r = whoami(client60, bearer(), loginId="victim@bank.test", tenantId="1001")
    assert r.status_code == 200
    body = r.json()
    assert body["login_id"] == "user@bank.test" and body["allowed_forms"] == ["4079"]
    assert "SECRET-9" not in r.text and "victim" not in r.text


def test_required_mode_refuses_every_url_only_request(client60):
    for params in ({"loginId": "victim@bank.test", "tenantId": "1001"}, {"loginId": "victim@bank.test"}, {}):
        assert whoami(client60, **params).status_code == 401


def test_required_mode_rejects_a_token_for_another_tenant_even_with_a_matching_url(client60):
    r = whoami(client60, {**bearer(), "X-Tenant-Id": "1002"}, tenantId="1002")
    assert r.status_code == 403


def test_required_mode_rejects_expired_wrong_issuer_wrong_audience_and_bad_signature(client60):
    assert whoami(client60, bearer(exp=time.time() - 3600)).status_code == 401
    assert whoami(client60, bearer(iss="someone-else")).status_code == 401
    assert whoami(client60, bearer(aud="https://other.example")).status_code == 401
    forged = {"Authorization": f"Bearer {make_token(claims(), secret='a-different-key-0123456789abcdefghij')}"}
    assert whoami(client60, forged).status_code == 401


def test_required_mode_on_an_untenanted_host_needs_no_tenant_claim(monkeypatch):
    from backend.main import app

    configure(monkeypatch, "required", PlainProfile())
    c = TestClient(app)
    token = make_token({"LoginId": "iris810", "iss": ISS, "aud": AUD, "exp": time.time() + 600})
    r = c.get("/auth/my-returns", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["login_id"] == "iris810" and r.json()["tenant_id"] == ""


def test_the_identity_checks_are_the_same_before_and_after_auth_is_enabled(monkeypatch):
    """DV_AUTH_ENABLED only adds the entitlement check; the token rules are identical."""
    from backend.main import app

    for auth_enabled in (False, True):
        configure(monkeypatch, "required", TenantProfile(), auth_enabled=auth_enabled)
        c = TestClient(app)
        assert c.get("/auth/my-returns", params={"loginId": "x", "tenantId": "1001"}).status_code == 401
        assert c.get("/auth/my-returns", headers=bearer()).status_code == 200
