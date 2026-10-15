"""Security hygiene: M-07, M-09, L-01, L-02, L-04, L-13. No database, no network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from backend import logging_config as lc
from backend.auth import deps
from backend.config import RequestContext
from backend.data import xml_loader
from backend.hosts import HostProfileError, ideal_60
from backend.nlp import index_manifest, index_store, lexical_search

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── M-07: pickles are loaded only when they are what the operator pinned ─────

def _pin_tool():
    spec = importlib.util.spec_from_file_location("pin_index_hashes", ROOT / "scripts" / "pin_index_hashes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_trust_cache(monkeypatch):
    monkeypatch.setattr(index_manifest, "_trust_cache", {})
    monkeypatch.delenv("DV_REQUIRE_INDEX_MANIFEST", raising=False)


def _write(folder, name, data=b"x"):
    path = folder / name
    path.write_bytes(data)
    return str(path)


def test_without_a_manifest_behaviour_is_unchanged(tmp_path):
    p = _write(tmp_path, "table_meta.pkl")
    assert index_manifest.pickle_is_trusted(p) == (True, "no manifest")


def test_with_a_manifest_a_matching_file_is_trusted_and_a_changed_one_is_not(tmp_path):
    p = _write(tmp_path, "table_meta.pkl", b"original")
    _pin_tool().main([str(tmp_path)])
    assert index_manifest.pickle_is_trusted(p) == (True, "pinned")
    time.sleep(0.01)
    pathlib.Path(p).write_bytes(b"swapped!")                   # the attack: a replaced pickle
    ok, why = index_manifest.pickle_is_trusted(p)
    assert not ok and "does not match" in why


def test_a_file_the_manifest_does_not_list_is_not_trusted(tmp_path):
    _write(tmp_path, "a.pkl")
    _pin_tool().main([str(tmp_path)])
    extra = _write(tmp_path, "extra_meta.pkl", b"new")
    ok, why = index_manifest.pickle_is_trusted(extra)
    assert not ok and "not listed" in why


def test_a_broken_manifest_fails_closed(tmp_path):
    p = _write(tmp_path, "a.pkl")
    (tmp_path / "pickle_manifest.json").write_text("{not json", encoding="utf-8")
    assert index_manifest.pickle_is_trusted(p)[0] is False


def test_require_manifest_refuses_unpinned_pickles(tmp_path, monkeypatch):
    p = _write(tmp_path, "a.pkl")
    monkeypatch.setenv("DV_REQUIRE_INDEX_MANIFEST", "true")
    ok, why = index_manifest.pickle_is_trusted(p)
    assert not ok and "DV_REQUIRE_INDEX_MANIFEST" in why


def test_the_index_store_never_unpickles_an_untrusted_file(tmp_path, monkeypatch):
    class Boom:
        def __reduce__(self):
            return (os.system, ("echo pwned > pwned.txt",))

    import pickle

    meta = _write(tmp_path, "table_meta.pkl", pickle.dumps(Boom()))
    (tmp_path / "pickle_manifest.json").write_text(json.dumps({"files": {"table_meta.pkl": "0" * 64}}), encoding="utf-8")
    monkeypatch.setattr(index_store.faiss, "read_index", lambda p: object())
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(index_store.IndexInconsistentError, match="not loaded"):
            index_store.load_index("ignored.faiss", meta)
    finally:
        os.chdir(cwd)
    assert not (tmp_path / "pwned.txt").exists()               # the payload never ran


def test_the_bm25_loader_degrades_instead_of_unpickling_an_untrusted_file(tmp_path, monkeypatch, caplog):
    import pickle

    path = _write(tmp_path, "bm25.pkl", pickle.dumps({"bm25": None, "records": []}))
    (tmp_path / "pickle_manifest.json").write_text(json.dumps({"files": {"bm25.pkl": "f" * 64}}), encoding="utf-8")
    monkeypatch.setattr(lexical_search, "_bm25_cache", {})
    monkeypatch.setattr(lexical_search, "_bm25_failed", set())
    with caplog.at_level(logging.WARNING):
        assert lexical_search.search_bm25(path, "loans", 5) == []
    assert "not loaded" in caplog.text


def test_pinned_files_load_normally(tmp_path, monkeypatch):
    import pickle

    meta = _write(tmp_path, "table_meta.pkl", pickle.dumps([{"table": "t"}]))
    _pin_tool().main([str(tmp_path)])
    monkeypatch.setattr(index_store.faiss, "read_index", lambda p: "INDEX")
    assert index_store.load_index("x.faiss", meta) == ("INDEX", [{"table": "t"}])


def test_the_pin_tool_pins_checks_and_detects_drift(tmp_path, capsys):
    tool = _pin_tool()
    _write(tmp_path, "a.pkl", b"1")
    _write(tmp_path, "b.faiss", b"2")
    _write(tmp_path, "notes.txt", b"not pinned")
    assert tool.main([str(tmp_path), "--check"]) == 1          # nothing pinned yet
    assert tool.main([str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / "pickle_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"a.pkl", "b.faiss"}
    assert manifest["files"]["a.pkl"] == hashlib.sha256(b"1").hexdigest()
    assert tool.main([str(tmp_path), "--check"]) == 0
    (tmp_path / "a.pkl").write_bytes(b"changed")
    assert tool.main([str(tmp_path), "--check"]) == 1
    (tmp_path / "c.pkl").write_bytes(b"new")
    capsys.readouterr()
    tool.main([str(tmp_path), "--check"])
    assert "c.pkl is not pinned" in capsys.readouterr().out
    assert tool.main([str(tmp_path / "missing")]) == 2


def test_the_shipped_indexes_still_load_because_nothing_is_pinned_by_default():
    folder = ROOT / "artifacts" / "nlp-index" / "ideal-60"
    if not folder.is_dir():
        pytest.skip("index not present")
    for pkl in folder.glob("*.pkl"):
        assert index_manifest.pickle_is_trusted(str(pkl))[0] is True


# ── M-09: identity can be pseudonymised in the log ───────────────────────────

def test_pseudonyms_are_stable_and_hide_the_value():
    a = lc.pseudonym("Vaibhav@IrisIndia.net")
    assert a == lc.pseudonym("vaibhav@irisindia.net") and a.startswith("u-") and len(a) == 10
    assert "vaibhav" not in a and a != lc.pseudonym("someone@else.net")


def test_redaction_covers_urls_context_reprs_and_bare_emails():
    line = ("GET /variance/find?loginId=vaibhav@irisindia.net&tenantId=1001 | "
            "RequestContext(login_id='vaibhav@irisindia.net', tenant_id='1001') error for a.b@x.co")
    out = lc.redact_identity(line)
    assert "vaibhav" not in out and "a.b@x.co" not in out and "irisindia" not in out
    assert "tenantId=1001" in out and "tenant_id='1001'" in out         # the tenant is not personal data
    token = lc.pseudonym("vaibhav@irisindia.net")
    assert out.count(token) == 2                                         # same person, same token, everywhere
    assert lc.pseudonym('a.b@x.co') in out                               # a different person, a different token


def test_text_without_identity_is_untouched():
    line = "[service] compute started | table=CIMS_RAQ | rows=120 | 31-MAR-2025"
    assert lc.redact_identity(line) == line


def test_the_filter_redacts_formatted_messages_and_keeps_the_record():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "login %s asked %s", ("bob@bank.test", "total loans"), None)
    assert lc._RedactIdentityFilter().filter(rec) is True
    assert "bob@bank.test" not in rec.getMessage() and "total loans" in rec.getMessage()


def test_the_filter_never_loses_a_line_over_a_bad_format():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "needs %s %s", ("only-one",), None)
    assert lc._RedactIdentityFilter().filter(rec) is True


# ── L-01: a deactivated tenant is refused as soon as the file changes ────────

def test_the_tenant_registry_is_reloaded_when_the_file_changes(tmp_path, monkeypatch):
    registry_file = tmp_path / "XML_Tenant.xml"

    def write(status):
        registry_file.write_text(
            f'<Tenants><Row TenantId="1001" Status="{status}"/></Tenants>', encoding="utf-8")

    write("true")
    profile = ideal_60.Ideal60Profile() if hasattr(ideal_60, "Ideal60Profile") else next(
        obj() for name, obj in vars(ideal_60).items()
        if isinstance(obj, type) and issubclass(obj, ideal_60.HostProfile) and obj is not ideal_60.HostProfile)
    cls = type(profile)
    monkeypatch.setattr(profile, "tenant_xml_path", lambda: str(registry_file))
    cls.invalidate_tenant_registry()
    assert "1001" in profile._active_tenants()
    write("false")
    os.utime(registry_file, (time.time() + 5, time.time() + 5))     # a real edit changes the mtime
    assert "1001" not in profile._active_tenants()                   # well inside AUTH_TTL_SEC
    cls.invalidate_tenant_registry()


def test_an_unchanged_file_is_served_from_the_cache(tmp_path, monkeypatch):
    registry_file = tmp_path / "XML_Tenant.xml"
    registry_file.write_text('<Tenants><Row TenantId="1001" Status="true"/></Tenants>', encoding="utf-8")
    profile = next(obj() for name, obj in vars(ideal_60).items()
                   if isinstance(obj, type) and issubclass(obj, ideal_60.HostProfile) and obj is not ideal_60.HostProfile)
    monkeypatch.setattr(profile, "tenant_xml_path", lambda: str(registry_file))
    loads = []
    real = ideal_60.load_xml_tree
    monkeypatch.setattr(ideal_60, "load_xml_tree", lambda *a, **k: loads.append(1) or real(*a, **k))
    type(profile).invalidate_tenant_registry()
    for _ in range(5):
        profile._active_tenants()
    assert len(loads) == 1
    type(profile).invalidate_tenant_registry()


# ── L-02: hostile XML is refused, normal XML still loads ─────────────────────

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<Returns><Row Id="&lol3;"/></Returns>"""

EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]>
<Returns><Row Id="&x;"/></Returns>"""


def test_entity_expansion_is_refused(tmp_path, caplog):
    f = tmp_path / "Return.xml"
    f.write_bytes(BILLION_LAUGHS)
    with caplog.at_level(logging.ERROR):
        assert xml_loader.load_xml_tree(str(f)) is None
    assert "forbidden XML construct" in caplog.text


def test_an_external_entity_is_refused(tmp_path):
    f = tmp_path / "Return.xml"
    f.write_bytes(EXTERNAL_ENTITY)
    assert xml_loader.load_xml_tree(str(f)) is None


def test_ordinary_repository_xml_still_loads(tmp_path):
    f = tmp_path / "Return.xml"
    f.write_bytes('<?xml version="1.0" encoding="utf-8"?><Returns><Row Id="1" Name="A &amp; B"/></Returns>'.encode())
    root = xml_loader.load_xml_tree(str(f))
    assert root is not None and root.find("Row").attrib["Name"] == "A & B"


def test_the_duplicate_declaration_repair_still_works(tmp_path):
    f = tmp_path / "Return.xml"
    f.write_bytes(b'<?xml version="1.0"?><Returns><?xml version="1.0"?><Row Id="1"/></Returns>')
    assert xml_loader.load_xml_tree(str(f)).find("Row").attrib["Id"] == "1"


def test_defusedxml_is_a_declared_requirement():
    assert "defusedxml" in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert xml_loader._SafeET is not None


# ── L-04: the inbound request id cannot forge log lines ──────────────────────

@pytest.fixture
def client():
    from backend.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_a_clean_request_id_is_kept(client):
    r = client.get("/health", headers={"X-Request-ID": "trace-1234-abcd"})
    assert r.headers["X-Request-ID"] == "trace-1234-abcd"


@pytest.mark.parametrize("hostile", [
    "abc def", "abc;rm", "a\u0000b", "id%0d%0aINFO fake line", "<script>", "../../x", "a|b", "x=1&y=2",
])
def test_an_unsafe_request_id_is_replaced(client, hostile):
    r = client.get("/health", headers={"X-Request-ID": hostile})
    echoed = r.headers["X-Request-ID"]
    assert echoed != hostile and echoed
    assert all(ch.isalnum() or ch == "-" for ch in echoed)


def test_a_missing_or_over_long_request_id_gets_a_fresh_one(client):
    assert client.get("/health").headers["X-Request-ID"]
    long_id = "a" * 100
    assert client.get("/health", headers={"X-Request-ID": long_id}).headers["X-Request-ID"] != long_id


# ── L-13: auth disabled + an unusable tenant is a 403, not a 500 ─────────────

class _Profile:
    requires_tenant = True
    name = "iDEAL 6.0 (test)"

    def __init__(self, ok):
        self.ok = ok

    def validate_context(self, ctx):
        if not self.ok:
            raise HostProfileError(f"Tenant {ctx.tenant_id!r} is not an active tenant (active: ['1001','1002'])")


def _bypass_client(monkeypatch, profile):
    from backend.main import app

    monkeypatch.setattr(deps, "AUTH_ENABLED", False)
    monkeypatch.setattr(deps, "get_profile", lambda: profile)
    monkeypatch.setattr(deps.cfg, "IDENTITY_MODE", "off")
    return TestClient(app, raise_server_exceptions=False)


def test_an_unknown_tenant_with_auth_disabled_is_a_fixed_403(monkeypatch):
    c = _bypass_client(monkeypatch, _Profile(ok=False))
    r = c.get("/auth/my-returns", params={"loginId": "dev", "tenantId": "9999"})
    assert r.status_code == 403
    assert "1001" not in r.text and "9999" not in r.text and "active" not in r.text   # no tenant list leaked


def test_a_valid_tenant_with_auth_disabled_still_passes(monkeypatch):
    c = _bypass_client(monkeypatch, _Profile(ok=True))
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: set())
    assert c.get("/auth/my-returns", params={"loginId": "dev", "tenantId": "1001"}).status_code == 200


def test_the_bypass_with_an_untenanted_host_is_unchanged(monkeypatch):
    class Plain(_Profile):
        requires_tenant = False

    c = _bypass_client(monkeypatch, Plain(ok=True))
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: set())
    assert c.get("/auth/my-returns", params={"loginId": "dev"}).status_code == 200


def test_context_values_reach_the_helper_unchanged(monkeypatch):
    seen = []

    class Recording(_Profile):
        def validate_context(self, ctx):
            seen.append(RequestContext(ctx.login_id, ctx.tenant_id))

    c = _bypass_client(monkeypatch, Recording(ok=True))
    monkeypatch.setattr("backend.auth.service.get_allowed_form_ids", lambda ctx: set())
    c.get("/auth/my-returns", params={"loginId": "dev", "tenantId": "1001"})
    assert seen and seen[0].tenant_id == "1001"


# ── M-09: secrets are always redacted, whatever the identity setting ─────────

@pytest.mark.parametrize("line,gone", [
    ("Authorization: Bearer abcdefghijklmnop.qrstuv.wxyz1234", "abcdefghijklmnop"),
    ("auth failed for Bearer eyJhbGciOiJIUzUxMiJ9.eyJMb2dpbklkIjoieCJ9.sig123456", "eyJhbGci"),
    ("GET /x?loginId=a&password=hunter2&tenantId=1001", "hunter2"),
    ('cfg {"password": "p@ss w0rd", "user": "crilc"}', "p@ss w0rd"),
    ("pwd='abc def' next", "abc def"),
    ("connect crilc/Secr3t!@3.6.209.141:1521/XE failed", "Secr3t"),
    ("token=eyJhbGciOiJIUzUxMiJ9.eyJMb2dpbklkIjoieCJ9.sig123456 ok", "eyJhbGci"),
    ("DV_JWT_SECRET=supersecretvalue1234 loaded", "supersecretvalue1234"),
    ("redirect aspSession=ABCDEF123456&uid=7", "ABCDEF123456"),
    ("legacy url ?_at=abc123def", "abc123def"),
    ("api_key: sk-live-12345", "sk-live-12345"),
])
def test_secret_values_are_removed_from_log_text(line, gone):
    out = lc.redact_secrets(line)
    assert gone not in out and "***" in out


@pytest.mark.parametrize("line", [
    "[service] compute started | table=T | rows=120 | token_count=5 | 31-MAR-2025",
    "SELECT * FROM T WHERE RC = 'x' AND AMOUNT > 5",
    "[db] Acquired connection from pool",
    "GET /variance/find?loginId=iris810&tenantId=1001 200 OK",
    "[nlp.retriever] top table=cims_raq_q_sec1 confidence=0.82",
    "path D:/Repo6/1001/Database/User.xml loaded",
])
def test_ordinary_log_lines_are_left_alone(line):
    assert lc.redact_secrets(line) == line


def test_the_secret_filter_keeps_the_line_and_the_record():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "login %s with password=%s", ("bob", "hunter2"), None)
    assert lc._RedactSecretsFilter().filter(rec) is True
    assert rec.getMessage() == "login bob with password=***"


def test_a_clean_record_is_not_rewritten():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "rows=%d", (5,), None)
    lc._RedactSecretsFilter().filter(rec)
    assert rec.args == (5,) and rec.msg == "rows=%d"


def test_the_secret_filter_never_loses_a_line_over_a_bad_format():
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "needs %s %s", ("one",), None)
    assert lc._RedactSecretsFilter().filter(rec) is True


def test_both_handlers_get_the_secret_filter_without_any_setting(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    monkeypatch.setattr(lc, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(lc, "_REDACT_IDENTITY", False)
    try:
        lc.configure_logging()
        added = [h for h in root.handlers if h not in saved]
        assert len(added) == 2
        for handler in added:
            assert any(isinstance(f, lc._RedactSecretsFilter) for f in handler.filters)
            assert not any(isinstance(f, lc._RedactIdentityFilter) for f in handler.filters)
    finally:
        for h in root.handlers[:]:
            h.close()
        root.handlers[:] = saved


def test_a_real_log_call_is_redacted_end_to_end(tmp_path, monkeypatch, capsys):
    handler = logging.StreamHandler()
    handler.addFilter(lc._RedactSecretsFilter())
    stream = __import__("io").StringIO()
    handler.setStream(stream)
    log = logging.getLogger("m09-e2e")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    log.info("db user=%s password=%s dsn=%s", "crilc", "Sup3rSecret", "host/XE")
    log.removeHandler(handler)
    out = stream.getvalue()
    assert "Sup3rSecret" not in out and "user=crilc" in out and "dsn=host/XE" in out
