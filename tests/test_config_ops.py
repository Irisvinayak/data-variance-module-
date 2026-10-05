"""Configuration and operations fixes: M-01, M-08, M-27, M-32, L-17, L-22, L-26, L-30."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backend.api import meta
from backend.config import RequestContext
from backend.hosts import base as hosts_base
from backend.hosts import ideal_55, ideal_60
from backend.main import app

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── M-01: one shared path must not serve every tenant ────────────────────────

@pytest.fixture
def overrides(monkeypatch):
    def apply(**values):
        merged = dict.fromkeys(hosts_base.PATH_OVERRIDES, "")
        merged.update(values)
        monkeypatch.setattr(hosts_base, "PATH_OVERRIDES", merged)
        monkeypatch.setattr(hosts_base.HostProfile, "_warned_overrides", set())

    monkeypatch.setattr(hosts_base, "_ALLOW_SHARED_OVERRIDE", False)
    return apply


P60 = ideal_60.Ideal60Profile if hasattr(ideal_60, "Ideal60Profile") else None


def _profile60():
    for name in dir(ideal_60):
        obj = getattr(ideal_60, name)
        if isinstance(obj, type) and issubclass(obj, hosts_base.HostProfile) and obj is not hosts_base.HostProfile:
            return obj()
    raise AssertionError("no 6.0 profile")


def _profile55():
    for name in dir(ideal_55):
        obj = getattr(ideal_55, name)
        if isinstance(obj, type) and issubclass(obj, hosts_base.HostProfile) and obj is not hosts_base.HostProfile:
            return obj()
    raise AssertionError("no 5.5 profile")


T1, T2 = RequestContext("u", "1001"), RequestContext("u", "1002")


def test_a_plain_per_tenant_override_is_ignored_on_a_tenant_scoped_host(overrides, caplog):
    overrides(user_xml=r"D:\shared\User.xml")
    p = _profile60()
    derived = os.path.join(p.db_dir(T1), "User.xml")
    assert p.user_xml_path(T1) == derived                       # NOT the shared file
    assert p.user_xml_path(T2) != p.user_xml_path(T1)           # each tenant keeps its own
    assert "multi-tenant" in caplog.text


def test_the_warning_is_logged_once(overrides, caplog):
    overrides(user_xml=r"D:\shared\User.xml")
    p = _profile60()
    for _ in range(5):
        p.user_xml_path(T1)
    assert caplog.text.count("path override for 'user_xml'") == 1


def test_a_tenant_placeholder_is_filled_per_request(overrides):
    overrides(user_xml=r"D:\Repo\{tenant}\Custom\User.xml")
    p = _profile60()
    assert p.user_xml_path(T1) == r"D:\Repo\1001\Custom\User.xml"
    assert p.user_xml_path(T2) == r"D:\Repo\1002\Custom\User.xml"


@pytest.mark.parametrize("tenant", ["", "..\\..\\x", "a/b", "1001; rm", "x" * 80])
def test_an_unsafe_tenant_id_is_never_substituted_into_the_override(overrides, tenant):
    overrides(user_xml=r"D:\Repo\{tenant}\User.xml")
    p = _profile60()
    ctx = RequestContext("u", tenant)
    out = p.user_xml_path(ctx)
    # Falls back to the normally derived path (tenant ids are validated against the
    # registry elsewhere); the override template is not used at all.
    assert out == os.path.join(p.db_dir(ctx), "User.xml")
    assert "{tenant}" not in out and not out.startswith("D:\\Repo\\")


def test_a_global_file_override_still_applies_to_every_tenant(overrides):
    overrides(tenant_xml=r"D:\Repo\XML_Tenant.xml")
    assert _profile60().tenant_xml_path() == r"D:\Repo\XML_Tenant.xml"


def test_single_tenant_5_5_is_unaffected(overrides):
    overrides(user_xml=r"D:\Repo5.5\Database\XML_User.xml")
    assert _profile55().user_xml_path(RequestContext("u", "")) == r"D:\Repo5.5\Database\XML_User.xml"


def test_the_escape_hatch_restores_the_old_behaviour(overrides, monkeypatch):
    overrides(user_xml=r"D:\shared\User.xml")
    monkeypatch.setattr(hosts_base, "_ALLOW_SHARED_OVERRIDE", True)
    assert _profile60().user_xml_path(T1) == r"D:\shared\User.xml"


def test_no_override_means_the_derived_path(overrides):
    overrides()
    p = _profile60()
    assert p.user_xml_path(T1) == os.path.join(p.db_dir(T1), "User.xml")


# ── M-08: real OS variables can be made to win over .env ─────────────────────

def _settings_value(extra_env: dict) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DV_DOTENV_OVERRIDE"}
    env.update(PYTHONUTF8="1", **extra_env)
    code = "import backend.config.settings as s, os; print(s.DOTENV_OVERRIDE, os.environ.get('VERSION'))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    return out.stdout.strip()


def test_dotenv_still_wins_by_default():
    flag, version = _settings_value({"VERSION": "6.0"}).split()
    assert flag == "True"
    assert version == "5.5"                                       # the repository's .env says 5.5 and beat the OS value


def test_the_operator_can_make_the_os_environment_win():
    flag, version = _settings_value({"VERSION": "6.0", "DV_DOTENV_OVERRIDE": "false"}).split()
    assert flag == "False" and version == "6.0"


# ── M-32: one log folder per version by default ──────────────────────────────

def test_default_log_dir_is_per_version():
    from backend.logging_config import default_log_dir

    a, b = default_log_dir("5.5"), default_log_dir("6.0")
    assert a != b and a.endswith(os.path.join("logs", "5.5")) and b.endswith(os.path.join("logs", "6.0"))
    hostile = default_log_dir("../../etc")
    # One harmless path component directly under logs/: no separators survive, so it cannot climb out.
    assert os.path.dirname(hostile).endswith("logs") and os.path.basename(hostile) not in ("..", ".")
    assert default_log_dir("").endswith("default")


def test_dv_log_dir_still_overrides_everything():
    env = {**os.environ, "DV_LOG_DIR": os.path.join(str(ROOT), "logs", "custom"), "PYTHONUTF8": "1"}
    out = subprocess.run([sys.executable, "-c", "import backend.logging_config as l; print(l.LOG_DIR)"],
                         capture_output=True, text=True, env=env, cwd=ROOT, check=False).stdout.strip()
    assert out.endswith("custom")


# ── L-17: /ready ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(meta, "_ready_cache", {"at": 0.0, "value": None})
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_ready_when_the_database_answers(client, monkeypatch):
    monkeypatch.setattr(meta, "_probe_database", lambda: True)
    monkeypatch.setattr(meta, "_probe_llm", lambda: True)
    r = client.get("/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready", "database": True, "llm": True}


def test_not_ready_with_a_503_when_the_database_is_down(client, monkeypatch):
    monkeypatch.setattr(meta, "_probe_database", lambda: False)
    monkeypatch.setattr(meta, "_probe_llm", lambda: True)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and r.json()["database"] is False


def test_an_unreachable_llm_is_reported_but_does_not_fail_readiness(client, monkeypatch):
    monkeypatch.setattr(meta, "_probe_database", lambda: True)
    monkeypatch.setattr(meta, "_probe_llm", lambda: False)
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["llm"] is False and r.json()["status"] == "ready"


def test_the_answer_is_cached_so_a_probe_cannot_hammer_oracle(client, monkeypatch):
    calls = []
    monkeypatch.setattr(meta, "_probe_database", lambda: calls.append(1) or True)
    monkeypatch.setattr(meta, "_probe_llm", lambda: True)
    for _ in range(10):
        client.get("/ready")
    assert len(calls) == 1


def test_the_response_carries_no_hosts_paths_or_error_text(client, monkeypatch):
    monkeypatch.setattr(meta, "_probe_database", lambda: False)
    monkeypatch.setattr(meta, "_probe_llm", lambda: False)
    body = client.get("/ready").json()
    assert set(body) == {"status", "database", "llm"}


def test_the_database_probe_treats_errors_and_exceptions_as_not_ready(monkeypatch):
    monkeypatch.setattr("backend.data.db.execute_query", lambda sql: ([], [], "ORA-12541"))
    assert meta._probe_database() is False

    def boom(sql):
        raise RuntimeError("pool gone")

    monkeypatch.setattr("backend.data.db.execute_query", boom)
    assert meta._probe_database() is False
    monkeypatch.setattr("backend.data.db.execute_query", lambda sql: (["X"], [(1,)], None))
    assert meta._probe_database() is True


def test_ready_is_never_throttled(client, monkeypatch):
    from backend import limits
    import backend.main as main

    monkeypatch.setattr(meta, "_probe_database", lambda: True)
    monkeypatch.setattr(meta, "_probe_llm", lambda: True)
    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1))
    assert all(client.get("/ready", params={"loginId": "x"}).status_code == 200 for _ in range(5))


# ── M-27: launcher and registration script ───────────────────────────────────

def test_the_launcher_restarts_a_crashed_backend_but_not_a_clean_exit():
    text = (ROOT / "scripts" / "windows" / "run_backend.bat").read_text(encoding="utf-8")
    assert ":run" in text and "goto run" in text and "MAXRESTARTS" in text
    assert 'if "%RC%"=="0" exit /b 0' in text
    assert "ping -n" in text and "timeout /t" not in text.replace("REM", "")   # timeout.exe needs a console


def test_the_registration_script_reads_ports_and_only_kills_python():
    text = (ROOT / "scripts" / "windows" / "register_tasks.ps1").read_text(encoding="utf-8")
    assert '$ports = @{ "55" = 8004; "60" = 8003 }' not in text
    assert "Get-DvPort" in text and "DV_SERVER_PORT_" in text
    assert 'ProcessName -like "python*"' in text
    assert "Start-Sleep -Seconds 20" not in text and "AddSeconds(120)" in text


# ── L-22 / L-26 / L-30: repository hygiene ───────────────────────────────────

def test_gitignore_does_not_swallow_config_env_templates():
    out = subprocess.run(["git", "check-ignore", "-q", "config/env/.env.70.example"], cwd=ROOT, check=False)
    assert out.returncode == 1                                    # 1 = not ignored
    assert subprocess.run(["git", "check-ignore", "-q", "env/secrets.txt"], cwd=ROOT, check=False).returncode == 0


def test_env_example_has_no_public_llm_address_and_no_dead_setting():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "3.109.51.228" not in text
    assert "DOTNET_API_URL" not in text


def test_env_example_says_proxy_target_does_not_affect_builds():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NO" in text.split("VITE_PROXY_TARGET")[1][:400] and "npm run build" in text.split("VITE_PROXY_TARGET")[1][:400]
