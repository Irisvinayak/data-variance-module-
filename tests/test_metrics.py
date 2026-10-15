"""L-17: in-process metrics. No Prometheus server and no external service is involved."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import limits, metrics
from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.config import settings as cfg
from backend.data import db
from backend.main import app
from backend.nlp import intent_resolver, sql_generator

CTX = RequestContext(login_id="secret.person@bank.test", tenant_id="")


@pytest.fixture(autouse=True)
def _clean():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cfg, "METRICS_ENABLED", True)
    app.dependency_overrides[require_login] = lambda: CTX
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


def counter(name, **labels):
    key = tuple(sorted((k, str(v)) for k, v in labels.items()))
    return metrics.snapshot()["counters"].get(name, {}).get(key, 0)


# ── the registry ─────────────────────────────────────────────────────────────

def test_counters_accumulate_per_label_set():
    metrics.inc("dv_x_total", a="1")
    metrics.inc("dv_x_total", 2, a="1")
    metrics.inc("dv_x_total", a="2")
    assert counter("dv_x_total", a="1") == 3 and counter("dv_x_total", a="2") == 1


def test_histogram_buckets_are_cumulative_with_sum_and_count():
    for seconds in (0.001, 0.2, 7.0):
        metrics.observe("dv_h_seconds", seconds, route="/r")
    text = metrics.render_prometheus()
    assert 'dv_h_seconds_bucket{route="/r",le="0.005"} 1' in text
    assert 'dv_h_seconds_bucket{route="/r",le="0.25"} 2' in text
    assert 'dv_h_seconds_bucket{route="/r",le="10"} 3' in text
    assert 'dv_h_seconds_bucket{route="/r",le="+Inf"} 3' in text
    assert 'dv_h_seconds_count{route="/r"} 3' in text
    assert 'dv_h_seconds_sum{route="/r"} 7.201000' in text


def test_render_has_help_and_type_lines_and_valid_label_escaping():
    metrics.inc("dv_http_requests_total", method="GET", route='/a"b', status=200)
    text = metrics.render_prometheus()
    assert "# TYPE dv_http_requests_total counter" in text and "# HELP dv_http_requests_total" in text
    assert 'route="/a\\"b"' in text


def test_an_empty_registry_renders_nothing_and_reset_clears():
    assert metrics.render_prometheus() == ""
    metrics.inc("dv_x_total")
    metrics.reset()
    assert metrics.render_prometheus() == ""


def test_the_timer_records_even_when_the_block_raises():
    with pytest.raises(ValueError):
        with metrics.timer("dv_t_seconds", k="v"):
            raise ValueError("boom")
    assert metrics.snapshot()["histograms"]["dv_t_seconds"][(("k", "v"),)][0] == 1


def test_updates_from_many_threads_are_not_lost():
    import threading

    def work():
        for _ in range(500):
            metrics.inc("dv_race_total")

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert counter("dv_race_total") == 4000


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_endpoint_is_404_until_it_is_enabled(monkeypatch):
    monkeypatch.setattr(cfg, "METRICS_ENABLED", False)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/metrics").status_code == 404
        c.get("/health")
    assert metrics.render_prometheus() == ""                      # nothing is even collected while off


def test_disabled_by_default():
    assert cfg._flag("DV_METRICS_NOT_SET_ANYWHERE", "false") is False


def test_enabled_endpoint_serves_prometheus_text(client):
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'dv_http_requests_total{method="GET",route="/health",status="200"} 1' in r.text
    assert "dv_http_request_seconds_count" in r.text


def test_the_route_label_is_the_template_not_the_url(client):
    client.get("/variance/dates", params={"return_id": "2041", "table_name": "SECRET_TABLE", "loginId": "bob@x.test"})
    text = client.get("/metrics").text
    assert 'route="/variance/dates"' in text
    for leak in ("SECRET_TABLE", "2041", "bob@x.test", "loginId"):
        assert leak not in text


def test_unmatched_paths_share_one_label(client):
    client.get("/no/such/one")
    client.get("/no/such/two")
    assert counter("dv_http_requests_total", method="GET", route="unmatched", status=404) == 2


def test_error_statuses_are_counted(client, monkeypatch):
    client.post("/variance/compute", json={"return_id": "1"})            # 422
    assert counter("dv_http_requests_total", method="POST", route="/variance/compute", status=422) == 1


def test_rate_limited_requests_are_counted(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1000))
    client.get("/auth/my-returns", params={"loginId": "u"})
    assert client.get("/auth/my-returns", params={"loginId": "u"}).status_code == 429
    assert counter("dv_http_rate_limited_total") == 1
    assert counter("dv_http_requests_total", method="GET", route="unmatched", status=429) == 1


def test_scraping_metrics_is_never_throttled(client, monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "_per_login", limits.SlidingWindowLimiter(1))
    monkeypatch.setattr(main, "_global", limits.SlidingWindowLimiter(1))
    assert all(client.get("/metrics").status_code == 200 for _ in range(5))


def test_the_metrics_route_is_not_in_the_public_schema():
    assert "/metrics" not in app.openapi()["paths"]


# ── database ─────────────────────────────────────────────────────────────────

class _Cur:
    description = (("A",),)
    arraysize = 0

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql):
        pass

    def fetchmany(self, n):
        return self.rows[:n]

    def close(self):
        pass


class _Conn:
    call_timeout = None

    def __init__(self, rows):
        self._c = _Cur(rows)

    def cursor(self):
        return self._c

    def close(self):
        pass


@pytest.fixture
def dbfix(monkeypatch):
    monkeypatch.setattr(db, "_MISSING_CONFIG", [])
    db.reset_truncation()
    yield
    db.reset_truncation()


def test_db_outcomes_ok_busy_unavailable_not_configured_error(monkeypatch, dbfix):
    monkeypatch.setattr(db, "_acquire", lambda: (_Conn([(1,)]), False))
    assert db.execute_query("SELECT 1 FROM T")[2] is None
    assert counter("dv_db_queries_total", outcome="ok") == 1

    def busy():
        raise db.PoolBusyError("x")

    monkeypatch.setattr(db, "_acquire", busy)
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_queries_total", outcome="busy") == 1

    def down():
        raise RuntimeError("no route to host 10.1.2.3")

    monkeypatch.setattr(db, "_acquire", down)
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_queries_total", outcome="unavailable") == 1

    monkeypatch.setattr(db, "_MISSING_CONFIG", ["DV_DB_HOST"])
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_queries_total", outcome="not_configured") == 1

    monkeypatch.setattr(db, "_MISSING_CONFIG", [])

    class Boom(_Conn):
        def cursor(self):
            raise RuntimeError("ORA-00942")

    monkeypatch.setattr(db, "_acquire", lambda: (Boom([]), False))
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_queries_total", outcome="error") == 1
    assert metrics.snapshot()["histograms"]["dv_db_query_seconds"][()][0] == 5


def test_a_truncated_result_is_counted(monkeypatch, dbfix):
    monkeypatch.setattr(db, "DB_MAX_ROWS", 2)
    monkeypatch.setattr(db, "_acquire", lambda: (_Conn([(1,), (2,), (3,)]), False))
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_result_truncated_total") == 1
    db.execute_query("SELECT 1 FROM T")                           # same request: the sticky flag is not counted twice
    assert counter("dv_db_result_truncated_total") == 1
    db.reset_truncation()                                         # a new request, a small result
    monkeypatch.setattr(db, "_acquire", lambda: (_Conn([(1,)]), False))
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_result_truncated_total") == 1
    db.reset_truncation()                                         # another new request that is cut off again
    monkeypatch.setattr(db, "_acquire", lambda: (_Conn([(1,), (2,), (3,)]), False))
    db.execute_query("SELECT 1 FROM T")
    assert counter("dv_db_result_truncated_total") == 2


def test_the_db_wrapper_returns_exactly_what_the_implementation_returns(monkeypatch, dbfix):
    monkeypatch.setattr(db, "_acquire", lambda: (_Conn([(7,)]), False))
    assert db.execute_query("SELECT 1 FROM T") == (["A"], [(7,)], None)


def test_no_sql_or_error_text_reaches_the_metrics(monkeypatch, dbfix):
    class Boom(_Conn):
        def cursor(self):
            raise RuntimeError("ORA-00942: table CRILC.SECRET_T does not exist")

    monkeypatch.setattr(db, "_acquire", lambda: (Boom([]), False))
    db.execute_query("SELECT secret_col FROM CRILC.SECRET_T")
    text = metrics.render_prometheus()
    for leak in ("SECRET", "ORA-", "SELECT", "CRILC"):
        assert leak not in text


# ── LLM ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module,caller", [(sql_generator, "sql"), (intent_resolver, "intent")])
def test_llm_calls_are_counted_by_outcome(monkeypatch, module, caller):
    monkeypatch.setattr(module, "_call_ollama_impl", lambda *a, **k: ("text", 5))
    assert module._call_ollama("p", "m", {}, attempt=1) == ("text", 5)
    assert counter("dv_llm_calls_total", caller=caller, outcome="ok") == 1

    def fail(*a, **k):
        raise RuntimeError("model unreachable at http://10.0.0.5")

    monkeypatch.setattr(module, "_call_ollama_impl", fail)
    with pytest.raises(RuntimeError):
        module._call_ollama("p", "m", {}, attempt=1)
    assert counter("dv_llm_calls_total", caller=caller, outcome="failed") == 1
    assert metrics.snapshot()["histograms"]["dv_llm_call_seconds"][(("caller", caller),)][0] == 2
    assert "10.0.0.5" not in metrics.render_prometheus()


def test_nl_resolution_modes_are_counted():
    intent_resolver._tag({"return_id": "1"}, "llm")
    intent_resolver._tag({"return_id": "1"}, "fallback")
    intent_resolver._tag({"return_id": "1"}, "fallback")
    intent_resolver._tag(None, "fallback")                         # nothing was resolved: not a resolution
    assert counter("dv_nl_resolutions_total", mode="llm") == 1
    assert counter("dv_nl_resolutions_total", mode="fallback") == 2
