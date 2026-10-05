"""M-04 / H-01 / C-01: the start-up security posture, and H-05's find filter.

The posture log changes no behaviour by default; it makes an unsafe combination
visible, and DV_REQUIRE_AUTH=true lets an operator refuse to start with auth off.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from backend import security_posture as sp
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.config import settings as cfg
from backend.main import app

SAFE = dict(auth_enabled=True, identity_mode="required", identity_mode_invalid=False, jwt_secret_set=True)


def test_a_fully_safe_configuration_has_no_warnings():
    assert sp.posture_warnings(**SAFE) == []


def test_auth_disabled_is_called_out():
    out = sp.posture_warnings(**{**SAFE, "auth_enabled": False})
    assert any("DV_AUTH_ENABLED=false" in w and "ALL authorisation is off" in w for w in out)


def test_identity_off_is_called_out_with_its_consequence():
    out = sp.posture_warnings(**{**SAFE, "identity_mode": "off", "jwt_secret_set": False})
    assert len(out) == 1 and "DV_IDENTITY_MODE=off" in out[0] and "my-returns" in out[0]


def test_a_mistyped_identity_mode_is_not_silent():
    out = sp.posture_warnings(**{**SAFE, "identity_mode": "off", "identity_mode_invalid": True})
    assert any("unrecognised" in w for w in out)


@pytest.mark.parametrize("mode", ["optional", "required"])
def test_a_token_mode_without_a_secret_is_called_out(mode):
    out = sp.posture_warnings(**{**SAFE, "identity_mode": mode, "jwt_secret_set": False})
    assert any("DV_JWT_SECRET is empty" in w for w in out)
    assert any("every request is rejected" in w for w in out) == (mode == "required")


def _settings(monkeypatch, **values):
    from backend.nlp import nlp_config

    # A safe LLM endpoint unless a test says otherwise, so these tests count only the
    # warnings they are about.
    monkeypatch.setattr(nlp_config, "OLLAMA_URL", "https://ollama.example.com/api/generate")
    monkeypatch.setattr(nlp_config, "OLLAMA_URL_IS_DEFAULT", False)
    monkeypatch.setattr(cfg, "REQUIRE_EXPLICIT_LLM_URL", False)
    for k, v in values.items():
        monkeypatch.setattr(cfg, k, v)


def test_log_reports_booleans_only_never_the_secret(monkeypatch, caplog):
    _settings(monkeypatch, AUTH_ENABLED=True, IDENTITY_MODE="required", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="super-secret-value-123", REQUIRE_AUTH=False)
    with caplog.at_level(logging.INFO, logger="backend.security"):
        assert sp.log_security_posture() == []
    assert "jwt_secret_set=True" in caplog.text
    assert "super-secret-value-123" not in caplog.text


def test_unsafe_settings_are_logged_as_warnings_but_do_not_stop_startup_by_default(monkeypatch, caplog):
    _settings(monkeypatch, AUTH_ENABLED=False, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="", REQUIRE_AUTH=False)
    with caplog.at_level(logging.WARNING, logger="backend.security"):
        warnings = sp.log_security_posture()                    # does not raise
    assert len(warnings) == 2
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 2


def test_require_auth_refuses_to_start_with_auth_off(monkeypatch):
    _settings(monkeypatch, AUTH_ENABLED=False, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="", REQUIRE_AUTH=True)
    with pytest.raises(RuntimeError, match="DV_REQUIRE_AUTH"):
        sp.log_security_posture()


def test_require_auth_is_satisfied_when_auth_is_on(monkeypatch):
    _settings(monkeypatch, AUTH_ENABLED=True, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="", REQUIRE_AUTH=True)
    sp.log_security_posture()


def test_require_auth_is_off_by_default():
    assert cfg._flag("DV_REQUIRE_AUTH_NOT_SET_IN_ANY_ENV", "false") is False


def test_the_application_start_up_runs_the_check(monkeypatch):
    _settings(monkeypatch, AUTH_ENABLED=False, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="", REQUIRE_AUTH=True, NLP_WARMUP=False)
    with pytest.raises(RuntimeError, match="DV_REQUIRE_AUTH"):
        with TestClient(app):
            pass


def test_the_application_still_starts_by_default(monkeypatch):
    _settings(monkeypatch, AUTH_ENABLED=False, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False,
              JWT_SECRET="", REQUIRE_AUTH=False, NLP_WARMUP=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


# ── H-05: /variance/find entitlement filter (DV_FIND_FILTER_BY_ACCESS) ───────

CTX = RequestContext(login_id="tester", tenant_id="")


@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


def _find(client, monkeypatch, result, flag=True, auth=True):
    monkeypatch.setattr("backend.api.variance.service.find_return_and_tables", lambda name, ctx: dict(result))
    monkeypatch.setattr("backend.api.variance.AUTH_ENABLED", auth)
    monkeypatch.setattr("backend.api.variance.is_return_allowed", lambda ctx, rid: rid == "2041")
    monkeypatch.setattr(cfg, "FIND_FILTER_BY_ACCESS", flag)
    return client.get("/variance/find", params={"return_name": "x"})


SINGLE_OK = {"return_id": "2041", "return_name": "A", "table_mapping_path": "D:/Repo/2041/Map.xml", "tables": []}
SINGLE_DENIED = {"return_id": "9999", "return_name": "B", "table_mapping_path": "D:/Repo/9999/Map.xml", "tables": []}
CANDIDATES_DENIED = {"candidates": [{"return_id": "9998"}, {"return_id": "9999"}]}


def test_find_filter_single_allowed_return_passes(client, monkeypatch):
    r = _find(client, monkeypatch, SINGLE_OK)
    assert r.status_code == 200 and r.json()["return_id"] == "2041"
    assert r.json()["table_mapping_path"] == "Map.xml"          # server path still redacted


def test_find_filter_single_denied_return_looks_like_it_does_not_exist(client, monkeypatch):
    r = _find(client, monkeypatch, SINGLE_DENIED)
    assert r.status_code == 404 and "9999" not in r.text and "Repo" not in r.text


def test_find_filter_all_candidates_denied_is_404(client, monkeypatch):
    assert _find(client, monkeypatch, CANDIDATES_DENIED).status_code == 404


def test_find_filter_off_by_default_leaves_results_untouched(client, monkeypatch):
    r = _find(client, monkeypatch, SINGLE_DENIED, flag=False)
    assert r.status_code == 200 and r.json()["return_id"] == "9999"


def test_find_filter_does_not_apply_when_auth_is_disabled(client, monkeypatch):
    assert _find(client, monkeypatch, SINGLE_DENIED, auth=False).status_code == 200


# ── C-01: the rollout pre-flight script ──────────────────────────────────────

import importlib.util  # noqa: E402
import pathlib  # noqa: E402
import types  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_identity_config", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_identity_config.py")
check_identity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_identity)

GOOD = dict(IDENTITY_MODE="required", IDENTITY_MODE_INVALID=False, JWT_SECRET="x" * 48,
            JWT_ISSUER="iDealWebAPI", JWT_AUDIENCE="https://ideal.example.com", AUTH_ENABLED=True)


def _cfg(**over):
    return types.SimpleNamespace(**{**GOOD, **over})


def test_preflight_passes_a_ready_configuration():
    assert check_identity.check(_cfg(), require=True) == []


def test_preflight_flags_each_missing_piece():
    assert any("not 'required'" in p for p in check_identity.check(_cfg(IDENTITY_MODE="optional"), require=True))
    assert any("not set" in p for p in check_identity.check(_cfg(JWT_SECRET=""), require=True))
    assert any("shorter than 32" in p for p in check_identity.check(_cfg(JWT_SECRET="short"), require=True))
    assert any("ISSUER" in p for p in check_identity.check(_cfg(JWT_ISSUER=""), require=True))
    assert any("AUDIENCE" in p for p in check_identity.check(_cfg(JWT_AUDIENCE=""), require=True))
    assert any("placeholder" in p for p in check_identity.check(_cfg(JWT_AUDIENCE="https://localhost:7072"), require=True))
    assert any("authorisation is off" in p for p in check_identity.check(_cfg(AUTH_ENABLED=False), require=True))
    assert any("not one of" in p for p in check_identity.check(_cfg(IDENTITY_MODE_INVALID=True)))


def test_preflight_in_off_mode_without_require_only_reports_auth():
    assert check_identity.check(_cfg(IDENTITY_MODE="off", JWT_SECRET="", JWT_ISSUER="", JWT_AUDIENCE="")) == []


def test_preflight_never_prints_the_secret(monkeypatch, capsys):
    monkeypatch.setattr(cfg, "JWT_SECRET", "THE-REAL-SECRET-VALUE-0123456789ABCDEFGH")
    monkeypatch.setattr(cfg, "IDENTITY_MODE", "required")
    code = check_identity.main(["--require"])
    out = capsys.readouterr().out
    assert "THE-REAL-SECRET" not in out and "(40 bytes)" in out
    assert code in (0, 1)


# ── H-07: the LLM endpoint ───────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://ollama.example.com/api/generate",
    "http://localhost:11434/api/generate",
    "http://127.0.0.1:11434/api/generate",
    "http://10.1.2.3:11434/api/generate",
    "http://192.168.0.7/OllamaProxy/api/generate",
    "http://ollama.corp.internal:11434/api/generate",
])
def test_safe_llm_endpoints_have_no_warning(url):
    assert sp.llm_url_risk(url) is None


@pytest.mark.parametrize("url,needle", [
    ("http://3.109.51.228/OllamaProxy/api/generate", "public address 3.109.51.228"),
    ("http://ollama.example.com/api/generate", "plain http"),
    ("", "no host"),
])
def test_risky_llm_endpoints_are_called_out(url, needle):
    assert needle in sp.llm_url_risk(url)


def test_the_builtin_default_is_called_out_even_though_it_still_works():
    assert "not set" in sp.llm_url_risk("http://3.109.51.228/OllamaProxy/api/generate", is_default=True)


def test_require_explicit_llm_url_refuses_to_start_on_the_default(monkeypatch):
    from backend.nlp import nlp_config

    _settings(monkeypatch, AUTH_ENABLED=True, IDENTITY_MODE="off", IDENTITY_MODE_INVALID=False, JWT_SECRET="", REQUIRE_AUTH=False)
    monkeypatch.setattr(nlp_config, "OLLAMA_URL_IS_DEFAULT", True)
    monkeypatch.setattr(cfg, "REQUIRE_EXPLICIT_LLM_URL", True)
    with pytest.raises(RuntimeError, match="DV_NLP_OLLAMA_URL"):
        sp.log_security_posture()
    monkeypatch.setattr(cfg, "REQUIRE_EXPLICIT_LLM_URL", False)
    sp.log_security_posture()                                  # default behaviour: warn, do not stop


def test_posture_warnings_include_the_llm_endpoint():
    out = sp.posture_warnings(**SAFE, llm_url="http://3.109.51.228/x", llm_url_is_default=False)
    assert any("clear text" in w for w in out)
    assert sp.posture_warnings(**SAFE) == []                   # omitted -> not judged
