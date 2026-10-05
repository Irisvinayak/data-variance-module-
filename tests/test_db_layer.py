"""Database-layer behaviour with FAKE connections (code review DATA-01..04, SEC-04).

IMPORTANT: there is no Oracle in this suite. Nothing here proves a real query
runs or returns correct data — these tests verify the Python logic around the
driver (row-cap handling, error hygiene, pool/fallback policy, session setup)
using stand-ins. Real execution is verified separately against a live database.
"""

from __future__ import annotations

import threading

import oracledb
import pytest

from backend.data import db


class FakeCursor:
    def __init__(self, rows=None, description=(("A",), ("B",)), raises=None):
        self.rows, self.description, self.raises = rows or [], description, raises
        self.executed: list[str] = []
        self.arraysize = 0
        self.closed = False

    def execute(self, sql):
        self.executed.append(sql)
        if self.raises:
            raise self.raises

    def fetchmany(self, n):
        return self.rows[:n]

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False
        self.call_timeout = None

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Pretend the DB is configured, and start every test with a clean flag."""
    monkeypatch.setattr(db, "_MISSING_CONFIG", [])
    db.reset_truncation()
    yield
    db.reset_truncation()


def run(monkeypatch, cursor, cap=3):
    monkeypatch.setattr(db, "DB_MAX_ROWS", cap)
    conn = FakeConn(cursor)
    monkeypatch.setattr(db, "_acquire", lambda: (conn, False))
    return db.execute_query("SELECT 1 FROM T"), conn


# ── DATA-01: the row cap is reported, never silent ───────────────────────────

def test_under_the_cap_is_not_truncated(monkeypatch):
    (cols, rows, err), conn = run(monkeypatch, FakeCursor(rows=[(1,), (2,)]), cap=3)
    assert err is None and rows == [(1,), (2,)] and not db.was_truncated()
    assert conn.closed


def test_exactly_at_the_cap_is_not_truncated(monkeypatch):
    (_, rows, err), _ = run(monkeypatch, FakeCursor(rows=[(1,), (2,), (3,)]), cap=3)
    assert err is None and len(rows) == 3 and not db.was_truncated()


def test_over_the_cap_returns_exactly_cap_rows_and_flags_it(monkeypatch):
    """Same rows as the old fetchmany(cap) returned — the contract is unchanged —
    plus an explicit signal that the result was cut."""
    (_, rows, err), _ = run(monkeypatch, FakeCursor(rows=[(1,), (2,), (3,), (4,), (5,)]), cap=3)
    assert err is None
    assert rows == [(1,), (2,), (3,)]
    assert db.was_truncated()


def test_truncation_flag_is_sticky_until_reset(monkeypatch):
    run(monkeypatch, FakeCursor(rows=[(i,) for i in range(10)]), cap=3)
    run(monkeypatch, FakeCursor(rows=[(1,)]), cap=3)          # a later small query must not clear it
    assert db.was_truncated()
    db.reset_truncation()
    assert not db.was_truncated()


def test_truncation_flag_is_isolated_per_thread(monkeypatch):
    seen = {}

    def worker():
        seen["other_thread"] = db.was_truncated()

    run(monkeypatch, FakeCursor(rows=[(i,) for i in range(10)]), cap=3)
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert db.was_truncated() and seen["other_thread"] is False


# ── SEC-04: the client never sees Oracle text, hosts, tables or SQL ──────────

LEAKS = ("ORA-", "CRILC", "SECRET_TABLE", "3.6.209.141", "SELECT", "password", "DPY-")


def assert_clean(err):
    assert err and "Reference:" in err
    for fragment in LEAKS:
        assert fragment not in err, err


def test_oracle_error_is_generic_to_the_client(monkeypatch):
    boom = oracledb.DatabaseError("ORA-00942: table or view CRILC.SECRET_TABLE does not exist")
    (_, _, err), conn = run(monkeypatch, FakeCursor(raises=boom))
    assert_clean(err)
    assert conn.closed                                         # still released on failure


def test_unexpected_error_is_generic_to_the_client(monkeypatch):
    (_, _, err), _ = run(monkeypatch, FakeCursor(raises=ValueError("password=secret SELECT * FROM SECRET_TABLE")))
    assert_clean(err)


def test_connection_failure_is_generic(monkeypatch):
    def fail():
        raise oracledb.DatabaseError("DPY-6005: cannot connect to 3.6.209.141:1521 user=crilc")

    monkeypatch.setattr(db, "_acquire", fail)
    _, _, err = db.execute_query("SELECT 1")
    assert_clean(err)


def test_pool_busy_gets_its_own_message(monkeypatch):
    def busy():
        raise db.PoolBusyError("pool exhausted")

    monkeypatch.setattr(db, "_acquire", busy)
    _, _, err = db.execute_query("SELECT 1")
    assert "busy" in err.lower() and "Reference:" in err


def test_missing_config_does_not_name_variables_to_the_client(monkeypatch):
    monkeypatch.setattr(db, "_MISSING_CONFIG", ["DV_DB_HOST", "DV_DB_PASSWORD"])
    _, _, err = db.execute_query("SELECT 1")
    assert "DV_DB" not in err and "not configured" in err


def test_cursor_close_failure_still_releases_the_connection(monkeypatch):
    class BadClose(FakeCursor):
        def close(self):
            raise oracledb.DatabaseError("DPY-1001: not connected")

    (_, _, err), conn = run(monkeypatch, BadClose(rows=[(1,)]))
    assert err is None and conn.closed


# ── DATA-02: bounded fallback, timed pool wait ───────────────────────────────

def test_pool_wait_is_timed_not_infinite(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_failed_until", 0.0)
    monkeypatch.setattr(db.oracledb, "create_pool", lambda **kw: seen.update(kw) or object())
    db._get_pool()
    assert seen["getmode"] == oracledb.POOL_GETMODE_TIMEDWAIT
    assert seen["wait_timeout"] == db.DB_POOL_WAIT_TIMEOUT_MS > 0
    assert seen["session_callback"] is db._nls_session_callback


def test_failed_pool_creation_is_not_retried_on_every_request(monkeypatch):
    calls = []

    def fail(**kw):
        calls.append(1)
        raise oracledb.DatabaseError("DPY-6005: no route to host")

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_failed_until", 0.0)
    monkeypatch.setattr(db.oracledb, "create_pool", fail)
    with pytest.raises(oracledb.DatabaseError):
        db._get_pool()
    with pytest.raises(RuntimeError, match="cooling down"):
        db._get_pool()
    assert len(calls) == 1


class _Boom(Exception):
    pass


def _busy_exc():
    err = oracledb.DatabaseError("DPY-4005: timed out waiting for the connection pool to return a connection")
    return err


def test_pool_busy_is_detected_and_never_falls_back(monkeypatch):
    direct = []

    class Pool:
        def acquire(self):
            raise _busy_exc()

    monkeypatch.setattr(db, "_get_pool", lambda: Pool())
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: direct.append(1))
    with pytest.raises(db.PoolBusyError):
        db._acquire()
    assert direct == []                                          # load is queued, not multiplied


def test_is_pool_busy_recognises_the_driver_error():
    assert db._is_pool_busy(_busy_exc())
    assert not db._is_pool_busy(oracledb.DatabaseError("ORA-12541: no listener"))


def test_fallback_applies_nls_settings_and_holds_a_slot(monkeypatch):
    fake = FakeConn(FakeCursor())
    monkeypatch.setattr(db, "_get_pool", lambda: (_ for _ in ()).throw(RuntimeError("pool broken")))
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: fake)
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(1))

    conn, used_fallback = db._acquire()
    assert conn is fake and used_fallback
    executed = " ".join(fake._cursor.executed)
    for setting in ("NLS_DATE_LANGUAGE", "NLS_DATE_FORMAT", "NLS_NUMERIC_CHARACTERS"):
        assert setting in executed                               # the direct path no longer skips NLS
    assert not db._fallback_slots.acquire(blocking=False)        # the single slot is taken


def test_fallback_is_capped(monkeypatch):
    monkeypatch.setattr(db, "_get_pool", lambda: (_ for _ in ()).throw(RuntimeError("pool broken")))
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: FakeConn(FakeCursor()))
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(2))
    db._acquire()
    db._acquire()
    with pytest.raises(db.PoolBusyError):
        db._acquire()                                            # a third direct session is refused


def test_fallback_slot_is_released_when_the_query_finishes(monkeypatch):
    fake = FakeConn(FakeCursor(rows=[(1,)]))
    monkeypatch.setattr(db, "_get_pool", lambda: (_ for _ in ()).throw(RuntimeError("pool broken")))
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: fake)
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(1))
    for _ in range(3):                                           # would deadlock/refuse if a slot leaked
        _, rows, err = db.execute_query("SELECT 1")
        assert err is None and rows
    assert fake.closed


def test_fallback_slot_is_released_when_the_direct_connect_fails(monkeypatch):
    monkeypatch.setattr(db, "_get_pool", lambda: (_ for _ in ()).throw(RuntimeError("pool broken")))
    monkeypatch.setattr(db.oracledb, "connect", lambda **kw: (_ for _ in ()).throw(oracledb.DatabaseError("DPY-6005")))
    monkeypatch.setattr(db, "_fallback_slots", threading.BoundedSemaphore(1))
    with pytest.raises(oracledb.DatabaseError):
        db._acquire()
    assert db._fallback_slots.acquire(blocking=False)            # not leaked


# ── DATA-04: the NLS session setup is actually applied ───────────────────────

def test_nls_session_callback_sets_date_and_number_formats():
    cursor = FakeCursor()
    db._nls_session_callback(FakeConn(cursor), None)
    sql = " | ".join(cursor.executed)
    assert "NLS_DATE_FORMAT" in sql and "DD-MON-YYYY" in sql
    assert "NLS_DATE_LANGUAGE" in sql and "AMERICAN" in sql
    assert "NLS_NUMERIC_CHARACTERS" in sql
    assert cursor.closed


def test_nls_session_callback_accepts_the_two_argument_pool_signature():
    """python-oracledb calls session_callback(conn, requested_tag); a required
    third argument once broke pooling entirely (commit ff0ba97)."""
    db._nls_session_callback(FakeConn(FakeCursor()), "tag")
    db._nls_session_callback(FakeConn(FakeCursor()), "tag", "actual")
