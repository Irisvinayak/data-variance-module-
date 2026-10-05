"""Extra coverage for the "solved, testing incomplete" items.

H-15  the truncation flag survives the HTTP layer and the NL column trimming
H-24  the fallback cap holds under concurrency; pool-creation back-off expires;
      a failed NLS setup on a direct connection does not leak a slot or a session
M-36  start-up warm-up: background, non-blocking, failure-proof, one model load
L-03  CORS: allowed origin/method/headers pass; others are refused

Stand-ins only: no Oracle, no model download, no network. Where a number would need
a real database or the real 1.3 GB model it is measured separately and not claimed here.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import oracledb
import pytest
from fastapi.testclient import TestClient

from backend.api import nlp as nlp_api
from backend.auth.deps import require_login
from backend.config import CORS_ORIGINS, RequestContext
from backend.config import settings as cfg
from backend.data import db, service
from backend.main import app

CTX = RequestContext(login_id="tester", tenant_id="")


@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


# ── H-15 ─────────────────────────────────────────────────────────────────────

COMPUTE_BODY = {
    "return_id": "2041", "table_mapping_path": "TableMapping.xml", "table_name": "T",
    "reporting_date": "31-MAR-2025", "reporting_period": 1, "comparison_mode": "vs_current",
}


def test_compute_response_carries_the_truncation_flag(client, monkeypatch):
    monkeypatch.setattr(service, "compute_variance", lambda **kw: {
        "rows": [{"identifier": "x"}], "columns": ["A"],
        "truncated": True, "truncation_notice": "cut off",
    })
    body = client.post("/variance/compute", json=COMPUTE_BODY).json()
    assert body["truncated"] is True and body["truncation_notice"] == "cut off"


def test_compute_response_without_truncation_has_no_flag(client, monkeypatch):
    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"rows": [], "columns": ["A"]})
    assert "truncated" not in client.post("/variance/compute", json=COMPUTE_BODY).json()


def test_nl_column_trimming_keeps_the_truncation_flag():
    computed = {
        "columns": ["A", "B"],
        "rows": [{"identifier": "x", "current": {"A": 1, "B": 2}, "previous": {"p1": {"A": 1, "B": 2}}}],
        "truncated": True, "truncation_notice": "cut off",
    }
    out = nlp_api._restrict_result_columns(computed, ["A"])
    assert out["truncated"] is True and out["truncation_notice"] == "cut off"
    assert out["columns"] == ["A"]


# ── H-24 ─────────────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, fail_on_nls=False):
        self.fail_on_nls = fail_on_nls
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if self.fail_on_nls:
            raise oracledb.DatabaseError("ORA-00911 nls failed")

    def close(self):
        pass


class _Conn:
    def __init__(self, fail_on_nls=False):
        self.closed = False
        self._cur = _Cur(fail_on_nls)

    def cursor(self):
        return self._cur

    def close(self):
        self.closed = True


def _no_pool(monkeypatch):
    monkeypatch.setattr(db, "_get_pool", lambda: (_ for _ in ()).throw(RuntimeError("pool broken")))


def test_fallback_cap_holds_under_concurrent_load(monkeypatch):
    _no_pool(monkeypatch)
    cap = 3
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(cap))
    opened, lock = [], threading.Lock()

    def connect(**kw):
        with lock:
            opened.append(1)
        return _Conn()

    monkeypatch.setattr(db.oracledb, "connect", connect)
    barrier = threading.Barrier(24)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            db._acquire()
            outcomes.append("ok")
        except db.PoolBusyError:
            outcomes.append("busy")

    threads = [threading.Thread(target=worker) for _ in range(24)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert outcomes.count("ok") == cap and outcomes.count("busy") == 24 - cap
    assert len(opened) == cap                                   # never more direct sessions than the cap


def test_concurrent_queries_release_every_slot(monkeypatch):
    _no_pool(monkeypatch)
    monkeypatch.setattr(db, "_MISSING_CONFIG", [])
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(2))
    conns = []

    def connect(**kw):
        c = _Conn()
        c._cur.description = (("A",),)
        c._cur.fetchmany = lambda n: []
        conns.append(c)
        return c

    monkeypatch.setattr(db.oracledb, "connect", connect)
    results = []

    def worker():
        results.append(db.execute_query("SELECT 1 FROM T")[2])   # error element

    for _round in range(5):
        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
    assert db._fallback_slots.acquire(blocking=False) and db._fallback_slots.acquire(blocking=False)
    assert all(c.closed for c in conns)                         # no leaked sessions
    assert any(r is None for r in results)                      # real work got through


def test_a_failed_nls_setup_closes_the_direct_session_and_frees_the_slot(monkeypatch):
    _no_pool(monkeypatch)
    bad = _Conn(fail_on_nls=True)
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: bad)
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(1))
    try:
        db._acquire()
    except Exception:
        pass                                                    # either outcome is fine; no leak is the point
    assert db._fallback_slots.acquire(blocking=False), "slot leaked"
    # A half-configured session must not stay open and unaccounted for.
    assert bad.closed


def test_pool_creation_is_retried_once_the_back_off_has_expired(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_failed_until", 0.0)

    def create(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise oracledb.DatabaseError("DPY-6005")
        return object()

    monkeypatch.setattr(db.oracledb, "create_pool", create)
    with pytest.raises(oracledb.DatabaseError):
        db._get_pool()
    assert db._pool_failed_until > time.monotonic()             # a back-off window was set
    with pytest.raises(RuntimeError, match="cooling down"):
        db._get_pool()
    monkeypatch.setattr(db, "_pool_failed_until", time.monotonic() - 1)    # window elapsed
    assert db._get_pool() is not None and len(calls) == 2


def test_pool_busy_during_a_query_is_reported_in_the_controlled_form(monkeypatch):
    monkeypatch.setattr(db, "_MISSING_CONFIG", [])

    def busy():
        raise db.PoolBusyError("busy")

    monkeypatch.setattr(db, "_acquire", busy)
    _cols, _rows, err = db.execute_query("SELECT 1 FROM T")
    assert err and "ORA-" not in err and "busy" in err.lower()


def test_limits_have_sane_defaults():
    assert db.DB_POOL_WAIT_TIMEOUT_MS == 30000
    assert db.DB_FALLBACK_MAX == 5


# ── M-36 ─────────────────────────────────────────────────────────────────────

def test_startup_launches_a_background_warmup_when_enabled(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_warm():
        started.set()
        release.wait(10)

    monkeypatch.setattr(cfg, "NLP_WARMUP", True)
    monkeypatch.setattr("backend.nlp.warmup.warm_nlp", slow_warm)
    t0 = time.monotonic()
    with TestClient(app) as c:                                  # lifespan runs on enter
        assert started.wait(5)
        assert time.monotonic() - t0 < 5                        # startup did not wait for the warm-up
        assert c.get("/health").status_code == 200              # and the app already serves requests
        names = [t.name for t in threading.enumerate()]
        assert "nlp-warmup" in names
        release.set()


def test_startup_does_not_warm_up_when_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(cfg, "NLP_WARMUP", False)
    monkeypatch.setattr("backend.nlp.warmup.warm_nlp", lambda: called.append(1))
    with TestClient(app) as c:
        c.get("/health")
    time.sleep(0.1)
    assert called == []


def test_a_crashing_warmup_does_not_stop_the_app(monkeypatch):
    def boom():
        raise RuntimeError("model download failed")

    monkeypatch.setattr(cfg, "NLP_WARMUP", True)
    monkeypatch.setattr("backend.nlp.warmup.warm_nlp", boom)
    with TestClient(app) as c:
        time.sleep(0.1)
        assert c.get("/health").status_code == 200


def test_the_embedding_model_is_constructed_once_even_when_warmup_and_requests_race(monkeypatch):
    from backend.nlp import embedder

    built = []

    class FakeModel:
        def __init__(self, name):
            time.sleep(0.05)                                    # widen the race window
            built.append(name)

        def encode(self, texts, normalize_embeddings=True):
            import numpy as np
            return np.ones((len(texts), 4), dtype="float32")

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(embedder, "_model", None)

    from backend.nlp import warmup
    threads = [threading.Thread(target=warmup.warm_nlp)] + [
        threading.Thread(target=embedder.embed_query, args=("loans",)) for _ in range(6)
    ]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(built) == 1
    embedder.embed_query("after warm-up")                       # the first "real" request reuses it
    assert len(built) == 1


# ── L-03 ─────────────────────────────────────────────────────────────────────

ORIGIN = CORS_ORIGINS[0]


def preflight(client, method="POST", headers="authorization,content-type,x-tenant-id", origin=ORIGIN):
    return client.options("/variance/compute", headers={
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    })


def test_configured_origin_gets_get_post_and_the_headers_the_ui_sends(client):
    for method in ("GET", "POST"):
        r = preflight(client, method=method)
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == ORIGIN
    allowed = r.headers["access-control-allow-headers"].lower()
    for h in ("authorization", "content-type", "x-tenant-id"):
        assert h in allowed
    assert "PUT" not in r.headers["access-control-allow-methods"]


def test_unknown_origin_is_refused(client):
    r = preflight(client, origin="https://evil.example.com")
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_unneeded_methods_are_refused(client, method):
    r = preflight(client, method=method)
    assert r.status_code == 400


def test_unneeded_headers_are_refused(client):
    r = preflight(client, headers="x-custom-evil,authorization")
    assert r.status_code == 400


def test_simple_request_from_an_unknown_origin_gets_no_cors_headers(client):
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_simple_request_from_the_configured_origin_is_allowed(client):
    r = client.get("/health", headers={"Origin": ORIGIN})
    assert r.headers.get("access-control-allow-origin") == ORIGIN
