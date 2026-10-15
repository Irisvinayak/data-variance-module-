# db.py — Oracle connection pool for the standalone Data Variance backend.
# Extracted from the chatbot's sql_agent/executor.py with all FAISS / LLM
# dependencies removed.  Only execute_query() is exported.

from __future__ import annotations

from ..envutil import env_int
import contextvars
import logging
import threading
import time
from typing import Any, Optional

import oracledb

from ..config import DB_HOST, DB_PORT, DB_SERVICE, DB_USER, DB_PASSWORD, DB_MAX_ROWS
from .. import metrics
from ..logging_config import request_id_var

logger = logging.getLogger(__name__)

_pool: oracledb.ConnectionPool | None = None

# Guards _pool creation. Routes run in FastAPI's threadpool, so several
# first-requests can reach _get_pool() concurrently; without this each one sees
# `_pool is None` and calls create_pool(), and every pool but the last is
# orphaned - real Oracle sessions that are never closed and never reachable,
# plus N times the connect latency.
_pool_lock = threading.Lock()

# Pure function of three module constants, so there is no reason to rebuild it
# per pool creation or per fallback connect.
_DSN: str = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)

# Sized against the request threadpool, not left at 5. FastAPI runs the `def`
# routes in a threadpool of ~40; with max=5 the 6th concurrent query blocks on
# acquire.
DB_POOL_MIN: int = env_int("DV_DB_POOL_MIN", "1")
DB_POOL_MAX: int = env_int("DV_DB_POOL_MAX", "20")

# How long a request waits for a free pooled connection before giving up
# (code review DATA-02). The default pool mode waits FOREVER, so a pool that is
# exhausted by slow queries turned every later request into a hung worker.
DB_POOL_WAIT_TIMEOUT_MS: int = env_int("DV_DB_POOL_WAIT_TIMEOUT_MS", "30000")

# The direct-connect fallback exists for a pool that cannot be created or used
# (network blip, pool broken). It used to be UNBOUNDED, so overload quietly
# became unlimited Oracle sessions. Capped now; and it is not used at all when
# the pool is merely busy (that is a queue, not a fault).
DB_FALLBACK_MAX: int = max(env_int("DV_DB_FALLBACK_MAX", "5"), 1)
_fallback_slots = threading.BoundedSemaphore(DB_FALLBACK_MAX)

# After create_pool fails, do not retry it on every request for a while: each
# retry is another failed connect on the hot path.
_POOL_RETRY_SEC = 30.0
_pool_failed_until: float = 0.0

# Per-round-trip ceiling for a query, in milliseconds. Set on the CONNECTION:
# oracledb has no cursor-level timeout, and the `cursor.callTimeout = 60_000`
# this replaces only created a stray Python attribute, so no query was ever
# bounded and a runaway one pinned a threadpool worker and a pooled session.
DB_CALL_TIMEOUT_MS: int = env_int("DV_DB_CALL_TIMEOUT_MS", "60000")

_MISSING_CONFIG = [
    name for name, value in (
        ("DV_DB_HOST", DB_HOST), ("DV_DB_USER", DB_USER), ("DV_DB_PASSWORD", DB_PASSWORD),
    ) if not value
]

# ── Truncation signal (code review DATA-01) ───────────────────────────────────
# execute_query() returns (columns, rows, error) and every caller unpacks that
# triple, so the contract cannot grow a field. Instead the cap being hit is
# recorded in a context variable the caller can read after the call. It is
# sticky within a request: a caller resets it first, runs its queries, then
# asks was_truncated().
_truncated_var: contextvars.ContextVar[bool] = contextvars.ContextVar("dv_db_truncated", default=False)


def reset_truncation() -> None:
    _truncated_var.set(False)


def was_truncated() -> bool:
    """True if any query since reset_truncation() hit the row cap."""
    return _truncated_var.get()


# What the client sees. The Oracle/driver text goes to the log only: it can
# carry host names, schema and table names, and SQL (code review SEC-04).
def _client_error(summary: str) -> str:
    return f"{summary} Reference: {request_id_var.get() or '-'}"


MSG_NOT_CONFIGURED = "The database is not configured."
MSG_UNAVAILABLE = "The database is temporarily unavailable."
MSG_BUSY = "The database is busy. Please try again shortly."
MSG_QUERY_FAILED = "The database query could not be completed."


class PoolBusyError(RuntimeError):
    """No pooled connection became free within DB_POOL_WAIT_TIMEOUT_MS."""


def _nls_session_callback(conn, requested_tag, actual_tag=None):
    """Set NLS parameters once per new physical connection.

    `actual_tag` is optional: python-oracledb's pool invokes this callback as
    `session_callback(connection, requested_tag)` -- two positional args, not
    three -- confirmed against the installed oracledb (thin mode) source at
    connection.py:907 (`pool.session_callback(self, params_impl.tag)`).
    A 3-required-arg signature meant EVERY pooled acquire raised
    `TypeError: _nls_session_callback() missing 1 required positional
    argument: 'actual_tag'`, which was silently caught by get_connection()'s
    except block and treated as "pool acquire failed" -> fell back to an
    unpooled direct connect for every single query. That defeated pooling
    entirely and, more importantly, meant the NLS session setup below (date
    format, decimal point) was NEVER actually applied to any connection.
    This predates the pool-locking fix; it was equally broken before."""
    cursor = conn.cursor()
    for stmt in [
        "ALTER SESSION SET NLS_DATE_LANGUAGE  = 'AMERICAN'",
        "ALTER SESSION SET NLS_DATE_FORMAT    = 'DD-MON-YYYY'",
        "ALTER SESSION SET NLS_NUMERIC_CHARACTERS = '.,'",
    ]:
        cursor.execute(stmt)
    cursor.close()


def _get_pool() -> oracledb.ConnectionPool:
    global _pool, _pool_failed_until
    # Double-checked: the fast path stays lock-free once the pool exists.
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            if time.monotonic() < _pool_failed_until:
                raise RuntimeError("connection pool unavailable (recent create failure, cooling down)")
            try:
                _pool = oracledb.create_pool(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    dsn=_DSN,
                    min=DB_POOL_MIN,
                    max=DB_POOL_MAX,
                    increment=1,
                    session_callback=_nls_session_callback,
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=DB_POOL_WAIT_TIMEOUT_MS,
                )
            except Exception:
                _pool_failed_until = time.monotonic() + _POOL_RETRY_SEC
                raise
            logger.info(
                "[db] connection pool created | min=%d max=%d wait_timeout_ms=%d | %s@%s:%s/%s",
                DB_POOL_MIN, DB_POOL_MAX, DB_POOL_WAIT_TIMEOUT_MS,
                DB_USER, DB_HOST, DB_PORT, DB_SERVICE,
            )
    return _pool


def _is_pool_busy(exc: BaseException) -> bool:
    """True for python-oracledb's "timed out waiting for the pool" (DPY-4005)."""
    error_obj = exc.args[0] if getattr(exc, "args", None) else exc
    if getattr(error_obj, "full_code", "") == "DPY-4005":
        return True
    return "timed out waiting for the connection pool" in str(exc)


def _acquire() -> tuple[Any, bool]:
    """(connection, used_fallback). A fallback connection holds one of the
    DB_FALLBACK_MAX slots, which the caller must release after closing it."""
    try:
        conn = _get_pool().acquire()
        logger.debug("[db] Acquired connection from pool")
        return conn, False
    except Exception as pool_exc:
        if _is_pool_busy(pool_exc):
            # Busy is load, not a fault: queueing more sessions behind a full
            # pool is exactly what made overload unbounded.
            logger.error("[db] Pool exhausted — no connection within %d ms", DB_POOL_WAIT_TIMEOUT_MS)
            raise PoolBusyError("pool exhausted") from pool_exc
        logger.warning("[db] Pool unusable (%s) — falling back to a direct connect", pool_exc)

    if not _fallback_slots.acquire(blocking=False):
        logger.error("[db] Direct-connect fallback is at its limit (%d)", DB_FALLBACK_MAX)
        raise PoolBusyError("fallback limit reached")
    conn = None
    try:
        conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=_DSN)
        # The pool applies NLS through its session callback; a direct connection
        # skips that, so without this its date format differed from every
        # pooled connection's.
        _nls_session_callback(conn, None)
        logger.info("[db] Direct connection established (fallback)")
        return conn, True
    except Exception as direct_exc:
        if conn is not None:
            # Connected but the NLS setup failed: never hand out (or leak) a
            # session whose date/number formats are not the ones the code assumes.
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - best effort, the original error matters
                pass
        _fallback_slots.release()
        logger.error(
            "[db] FATAL — direct connect also failed | host=%s port=%s service=%s user=%s | error=%s",
            DB_HOST, DB_PORT, DB_SERVICE, DB_USER, direct_exc,
        )
        raise


def get_connection():
    """Acquire a pooled connection (kept for callers outside this module).

    Prefer execute_query(). The fallback slot bookkeeping lives in _acquire()
    and execute_query(), so this returns only the connection.
    """
    conn, used_fallback = _acquire()
    if used_fallback:
        _fallback_slots.release()   # the caller owns closing it; do not leak a slot
    return conn


def execute_query(sql: str, params: Optional[dict[str, Any]] = None) -> tuple[list[str], list[Any], Optional[str]]:
    """Execute a SELECT against Oracle; see _execute_query_impl for the contract.

    `params` (optional) are named bind variables for the placeholders in `sql`; values are
    never part of the statement text. Omitted = exactly the previous behaviour.

    A thin wrapper that records outcome and latency (backend/metrics.py, code review L-17).
    The metric labels are fixed outcome names; no SQL, table or error text is recorded."""
    started = time.perf_counter()
    already_truncated = was_truncated()          # the flag is sticky for the rest of the request
    result = _execute_query_impl(sql, params)
    error = result[2]
    if error is None:
        outcome = "ok"
    elif error == _client_error(MSG_BUSY):
        outcome = "busy"
    elif error == _client_error(MSG_UNAVAILABLE):
        outcome = "unavailable"
    elif error == _client_error(MSG_NOT_CONFIGURED):
        outcome = "not_configured"
    else:
        outcome = "error"
    metrics.inc("dv_db_queries_total", outcome=outcome)
    metrics.observe("dv_db_query_seconds", time.perf_counter() - started)
    if outcome == "ok" and was_truncated() and not already_truncated:
        metrics.inc("dv_db_result_truncated_total")           # once per request, at the query that hit the cap
    return result


def _execute_query_impl(
    sql: str, params: Optional[dict[str, Any]] = None,
) -> tuple[list[str], list[Any], Optional[str]]:
    """Execute a SELECT query against Oracle DB.

    Returns
    -------
    (columns, rows, error)  — error is None on success. `error` is written for
    the client: it never contains Oracle text, host names, table names or SQL
    (those are logged). If the row cap was hit, was_truncated() reports it.
    """
    if _MISSING_CONFIG:
        logger.error(
            "[db] Database is not configured — set %s (or the _55/_60 variant for "
            "this VERSION) in .env", ", ".join(_MISSING_CONFIG),
        )
        return [], [], _client_error(MSG_NOT_CONFIGURED)

    # ── Step 1: acquire connection ────────────────────────────────────────────
    used_fallback = False
    try:
        conn, used_fallback = _acquire()
    except PoolBusyError:
        return [], [], _client_error(MSG_BUSY)
    except Exception as exc:  # noqa: BLE001 - oracledb.DatabaseError and pool/driver faults alike
        logger.error(
            "[db] CONNECTION FAILED | host=%s port=%s service=%s user=%s | %s",
            DB_HOST, DB_PORT, DB_SERVICE, DB_USER, exc,
        )
        return [], [], _client_error(MSG_UNAVAILABLE)

    # ── Step 2: execute query ─────────────────────────────────────────────────
    cursor = None
    try:
        conn.call_timeout = DB_CALL_TIMEOUT_MS
        cursor = conn.cursor()
        # Default arraysize is 100, so fetching DB_MAX_ROWS=5000 took ~50 network
        # round-trips. Purely a transport batching hint - same rows, same order.
        cursor.arraysize = min(DB_MAX_ROWS, 1000)
        clean_sql = sql.rstrip().rstrip(";")
        logger.debug("[db] Executing SQL:\n%s", clean_sql)
        if params:
            cursor.execute(clean_sql, params)
        else:
            cursor.execute(clean_sql)
        columns = [col[0] for col in cursor.description]
        # One row past the cap is fetched ONLY to learn whether the cap cut the
        # result; it is never returned. Rows 1..DB_MAX_ROWS are exactly what the
        # old fetchmany(DB_MAX_ROWS) returned.
        rows = cursor.fetchmany(DB_MAX_ROWS + 1)
        if len(rows) > DB_MAX_ROWS:
            rows = rows[:DB_MAX_ROWS]
            _truncated_var.set(True)
            logger.warning(
                "[db] Row cap hit (%d) — result is TRUNCATED and any figures derived "
                "from it may be incomplete. Increase DV_DB_MAX_ROWS if needed.",
                DB_MAX_ROWS,
            )
        logger.info(
            "[db] Query OK | columns=%d | rows_fetched=%d | max_rows=%d",
            len(columns), len(rows), DB_MAX_ROWS,
        )
        return columns, rows, None

    except oracledb.DatabaseError as exc:
        # Extract Oracle error code for faster diagnosis
        error_obj = exc.args[0] if exc.args else exc
        ora_code  = getattr(error_obj, "code", "N/A")
        ora_msg   = getattr(error_obj, "message", str(exc))
        logger.error(
            "[db] ORACLE ERROR | ORA-%s | %s\nSQL was:\n%s",
            ora_code, ora_msg, sql,
        )
        return [], [], _client_error(MSG_QUERY_FAILED)

    except Exception as exc:
        logger.error(
            "[db] UNEXPECTED ERROR during query execution | type=%s | %s\nSQL was:\n%s",
            type(exc).__name__, exc, sql,
        )
        return [], [], _client_error(MSG_QUERY_FAILED)

    finally:
        # cursor.close() CAN raise - DPY-1001 on a dead connection, ORA-03113
        # after a network blip, i.e. exactly when things are already going
        # wrong. When it did, conn.close() was skipped and the connection was
        # never returned to the pool; a handful of those exhausted the pool for
        # the life of the process. Nesting guarantees the release.
        try:
            if cursor is not None:
                cursor.close()
        except Exception as close_exc:
            logger.warning("[db] cursor.close() failed: %s", close_exc)
        finally:
            try:
                conn.close()
            finally:
                if used_fallback:
                    _fallback_slots.release()
