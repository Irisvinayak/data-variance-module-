"""Cache bounds and concurrency (code review CONC-01..03, PERF-01) and the
service-level truncation signal (DATA-01). Pure logic, stubbed boundaries; no
database, filesystem repository or model is touched."""

from __future__ import annotations

import threading
import time

import pytest

from backend.cache_util import put_bounded
from backend.data import db, service
from backend.data.report_lookup import _TTLCache


# ── CONC-01: bounded caches ──────────────────────────────────────────────────

def test_put_bounded_evicts_the_oldest_entries():
    cache: dict = {}
    for i in range(10):
        put_bounded(cache, f"k{i}", i, max_entries=4)
    assert list(cache) == ["k6", "k7", "k8", "k9"]


def test_put_bounded_refreshes_a_rewritten_key():
    cache: dict = {}
    for k in ("a", "b", "c"):
        put_bounded(cache, k, 1, max_entries=3)
    put_bounded(cache, "a", 2, max_entries=3)          # a is now the newest
    put_bounded(cache, "d", 1, max_entries=3)
    assert "a" in cache and "b" not in cache


def test_put_bounded_never_grows_past_the_limit_under_a_flood():
    cache: dict = {}
    for i in range(10_000):
        put_bounded(cache, f"login{i}", (None, 0.0, 0.0), max_entries=500)
    assert len(cache) == 500


def test_auth_cache_is_bounded(monkeypatch):
    from backend.auth import service as auth

    monkeypatch.setattr(auth, "_AUTH_CACHE_MAX", 25)
    monkeypatch.setattr(auth, "_forms_cache", {})
    monkeypatch.setattr(auth, "max_mtime", lambda *a: 1.0)
    monkeypatch.setattr(auth, "_resolve_allowed_forms", lambda ctx: None)       # an UNKNOWN login: cached too
    monkeypatch.setattr(auth, "get_profile", lambda: type("P", (), {
        "validate_context": lambda self, c: None,
        "user_xml_path": lambda self, c: "u", "department_xml_path": lambda self, c: "d"})())
    for i in range(200):
        auth.get_allowed_form_ids(auth.RequestContext(login_id=f"rnd{i}@x", tenant_id=""))
    assert len(auth._forms_cache) <= 25


# ── CONC-02: _TTLCache is coherent and thread-safe ───────────────────────────

def test_ttl_cache_semantics_are_unchanged():
    c = _TTLCache(ttl=60)
    assert c.get("t1") is None
    c.set([1, 2], "t1", mtime=5.0)
    assert c.get("t1", mtime=5.0) == [1, 2]
    assert c.get("t1", mtime=6.0) is None              # source file changed
    assert c.peek("t1") == ([1, 2], 5.0)
    assert c.loaded_at("t1") > 0 and c.loaded_at("missing") == 0.0
    c.discard("t1")
    assert c.get("t1") is None
    c.set([3], "t2")
    c.clear()
    assert c.peek("t2") == (None, None)


def test_ttl_cache_expires():
    c = _TTLCache(ttl=0.05)
    c.set("x", "k")
    assert c.get("k") == "x"
    time.sleep(0.08)
    assert c.get("k") is None


def test_ttl_cache_tenants_are_isolated_and_bounded():
    c = _TTLCache(ttl=60, max_keys=5)
    for i in range(20):
        c.set(f"returns-of-{i}", f"tenant{i}")
    assert c.get("tenant19") == "returns-of-19" and c.get("tenant0") is None
    assert len(c._entries) == 5


def test_ttl_cache_never_pairs_new_data_with_an_old_mtime():
    """The old three-dict layout could, under concurrency, return fresh data that
    passed an mtime check belonging to older data."""
    c = _TTLCache(ttl=60)
    errors: list = []
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            c.set(("data", n), "k", mtime=float(n))

    def reader():
        while not stop.is_set():
            data, mtime = c.peek("k")
            if data is not None and data[1] != int(mtime):
                errors.append((data, mtime))

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.4)
    stop.set()
    for t in threads:
        t.join()
    assert errors == []


# ── CONC-03: a cold build for one tenant must not block another ─────────────

def test_cold_build_for_one_tenant_does_not_block_another(monkeypatch):
    from backend.nlp import return_lookup as rl

    monkeypatch.setattr(rl, "_cache", {})
    monkeypatch.setattr(rl, "_cache_ts", {})
    release = threading.Event()
    started = threading.Event()

    def slow_build(ctx):
        if ctx.tenant_id == "1001":
            started.set()
            release.wait(5)                           # tenant 1001's cold build is slow
        return {f"table_of_{ctx.tenant_id}": []}

    monkeypatch.setattr(rl, "_build_lookup", slow_build)
    slow = threading.Thread(target=lambda: rl._get_lookup(rl.RequestContext("u", "1001")))
    slow.start()
    assert started.wait(2)

    result = {}
    fast = threading.Thread(target=lambda: result.update(v=rl._get_lookup(rl.RequestContext("u", "1002"))))
    fast.start()
    fast.join(timeout=2)
    finished_while_1001_still_building = not fast.is_alive()
    release.set()
    slow.join()
    fast.join()
    assert finished_while_1001_still_building, "tenant 1002 waited behind tenant 1001's build"
    assert "table_of_1002" in result["v"]


def test_concurrent_first_requests_for_one_tenant_build_once(monkeypatch):
    from backend.nlp import return_lookup as rl

    monkeypatch.setattr(rl, "_cache", {})
    monkeypatch.setattr(rl, "_cache_ts", {})
    builds = []

    def build(ctx):
        builds.append(1)
        time.sleep(0.1)
        return {"t": []}

    monkeypatch.setattr(rl, "_build_lookup", build)
    threads = [threading.Thread(target=lambda: rl._get_lookup(rl.RequestContext("u", "1001"))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1


# ── PERF-01: the per-table column map follows the schema cache ──────────────

def test_columns_for_table_is_correct_and_rebuilt_with_the_cache(monkeypatch):
    from backend.nlp import schema_info

    monkeypatch.setattr(schema_info, "_cols_source", None)
    first = {("T1", "A"): {}, ("T1", "B"): {}, ("T2", "C"): {}}
    monkeypatch.setattr(schema_info, "_get", lambda: first)
    assert schema_info.columns_for_table("t1") == frozenset({"A", "B"})
    assert schema_info.columns_for_table("T2") == frozenset({"C"})
    assert schema_info.columns_for_table("nope") == frozenset()
    assert schema_info.columns_for_table(None) == frozenset()

    second = {("T1", "Z"): {}}                          # the schema cache was rebuilt
    monkeypatch.setattr(schema_info, "_get", lambda: second)
    assert schema_info.columns_for_table("T1") == frozenset({"Z"})


# ── DATA-01 at the service level: the flag reaches the response, additively ──

@pytest.fixture
def stubbed_service(monkeypatch):
    """compute_variance with everything outside its own logic stubbed."""
    monkeypatch.setattr(service, "_return_meta", lambda rid, ctx: {"RepFreq": "Q"})
    monkeypatch.setattr(service, "resolve_physical_table_name", lambda *a, **k: "SCH.T_DP")
    monkeypatch.setattr(service, "_trusted_tbl_path", lambda meta: "")
    monkeypatch.setattr(service, "_get_table_metadata", lambda *a, **k: {
        "filter_col": "RDATE", "comp_filter_col_names": [], "is_single": False})
    monkeypatch.setattr(service, "_actual_previous_dates", lambda *a, **k: [])
    db.reset_truncation()
    yield
    db.reset_truncation()


def _compute(execute_query_fn):
    return service.compute_variance(
        return_id="2041", table_name="T", reporting_date="31-MAR-2025", reporting_period=1,
        execute_query_fn=execute_query_fn, comparison_dates=["31-MAR-2025"],
    )


def test_truncated_result_is_flagged_without_changing_existing_keys(monkeypatch, stubbed_service):
    def fake_calculate(**kw):
        kw["execute_query_fn"]("SELECT 1")
        return {"table_name": "SCH.T_DP", "rows": [{"identifier": "x"}], "columns": ["A"]}

    def execute(sql):
        db._truncated_var.set(True)                    # the DB layer hit its cap
        return ["A"], [(1,)], None

    monkeypatch.setattr(service, "calculate_variance", fake_calculate)
    out = _compute(execute)
    assert out["truncated"] is True and "cut off" in out["truncation_notice"]
    assert out["rows"] == [{"identifier": "x"}] and out["columns"] == ["A"]          # nothing else changed


def test_untruncated_result_has_no_new_keys(monkeypatch, stubbed_service):
    monkeypatch.setattr(service, "calculate_variance", lambda **kw: {"rows": [], "columns": []})
    out = _compute(lambda sql: (["A"], [(1,)], None))
    assert "truncated" not in out and "truncation_notice" not in out


def test_an_error_result_is_not_decorated_with_a_truncation_flag(monkeypatch, stubbed_service):
    db._truncated_var.set(True)
    monkeypatch.setattr(service, "calculate_variance", lambda **kw: {"error": "No data found for X on Y"})
    monkeypatch.setattr(service, "reset_truncation", lambda: None)       # keep the pre-set flag for this check
    assert "truncated" not in _compute(lambda sql: (["A"], [], None))


def test_the_flag_does_not_leak_between_requests(monkeypatch, stubbed_service):
    monkeypatch.setattr(service, "calculate_variance", lambda **kw: {"rows": []})
    db._truncated_var.set(True)                         # left over from an earlier request in this thread
    assert "truncated" not in _compute(lambda sql: (["A"], [(1,)], None))   # compute_variance resets it first


def test_zero_row_diagnostics_are_off_by_default(monkeypatch, stubbed_service):
    """DATA-03: an empty result must not trigger COUNT(*) / DISTINCT over the table."""
    from backend.config import settings

    queries: list[str] = []

    def fake_calculate(**kw):
        kw["execute_query_fn"]("SELECT * FROM T")
        return {"error": "No data found"}

    def execute(sql):
        queries.append(sql)
        return ["A"], [], None

    monkeypatch.setattr(service, "calculate_variance", fake_calculate)
    monkeypatch.setattr(settings, "TABLE_DIAGNOSTICS", False)
    _compute(execute)
    assert queries == ["SELECT * FROM T"]               # exactly the one real query

    queries.clear()
    monkeypatch.setattr(settings, "TABLE_DIAGNOSTICS", True)
    _compute(execute)
    assert any("COUNT(*)" in q for q in queries)        # opt-in still works


def test_service_rejects_hostile_identifiers_for_every_caller():
    from backend.data.validation import InvalidInput

    with pytest.raises(InvalidInput):
        service.compute_variance("..\\x", "T", "31-MAR-2025", 1, lambda q: ([], [], None))
    with pytest.raises(InvalidInput):
        service.get_available_dates("2041", "T; DROP TABLE X", lambda q: ([], [], None))
    with pytest.raises(InvalidInput):
        service._distinct_dates_desc(lambda q: ([], [], None), "T WHERE 1=1", "RDATE", 5)
