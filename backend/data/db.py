# db.py — Oracle connection pool for the standalone Data Variance backend.
# Extracted from the chatbot's sql_agent/executor.py with all FAISS / LLM
# dependencies removed.  Only execute_query() is exported.

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

import oracledb

from ..config import DB_HOST, DB_PORT, DB_SERVICE, DB_USER, DB_PASSWORD, DB_MAX_ROWS

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
# acquire, and get_connection()'s fallback then opens UNPOOLED direct
# connections, so load overflow silently became unbounded Oracle sessions
# instead of a queue.
DB_POOL_MIN: int = int(os.getenv("DV_DB_POOL_MIN", "1"))
DB_POOL_MAX: int = int(os.getenv("DV_DB_POOL_MAX", "20"))

# Per-round-trip ceiling for a query, in milliseconds. Set on the CONNECTION:
# oracledb has no cursor-level timeout, and the `cursor.callTimeout = 60_000`
# this replaces only created a stray Python attribute, so no query was ever
# bounded and a runaway one pinned a threadpool worker and a pooled session.
DB_CALL_TIMEOUT_MS: int = int(os.getenv("DV_DB_CALL_TIMEOUT_MS", "60000"))

_MISSING_CONFIG = [
    name for name, value in (
        ("DV_DB_HOST", DB_HOST), ("DV_DB_USER", DB_USER), ("DV_DB_PASSWORD", DB_PASSWORD),
    ) if not value
]


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
    global _pool
    # Double-checked: the fast path stays lock-free once the pool exists.
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = oracledb.create_pool(
                user=DB_USER,
                password=DB_PASSWORD,
                dsn=_DSN,
                min=DB_POOL_MIN,
                max=DB_POOL_MAX,
                increment=1,
                session_callback=_nls_session_callback,
            )
            logger.info(
                "[db] connection pool created | min=%d max=%d | %s@%s:%s/%s",
                DB_POOL_MIN, DB_POOL_MAX, DB_USER, DB_HOST, DB_PORT, DB_SERVICE,
            )
    return _pool


def get_connection():
    """Acquire a connection from the pool, falling back to a direct connect."""
    try:
        conn = _get_pool().acquire()
        logger.debug("[db] Acquired connection from pool")
        return conn
    except Exception as pool_exc:
        logger.warning(
            "[db] Pool acquire failed (%s) — falling back to direct connect", pool_exc
        )
        try:
            conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=_DSN)
            logger.info("[db] Direct connection established (fallback)")
            return conn
        except oracledb.DatabaseError as direct_exc:
            logger.error(
                "[db] FATAL — direct connect also failed | host=%s port=%s service=%s user=%s | error=%s",
                DB_HOST, DB_PORT, DB_SERVICE, DB_USER, direct_exc,
            )
            raise


def execute_query(sql: str) -> tuple[list[str], list[Any], Optional[str]]:
    """Execute a SELECT query against Oracle DB.

    Returns
    -------
    (columns, rows, error)  — error is None on success.
    """
    if _MISSING_CONFIG:
        logger.error(
            "[db] Database is not configured — set %s (or the _55/_60 variant for "
            "this VERSION) in .env", ", ".join(_MISSING_CONFIG),
        )
        return [], [], f"Database is not configured: {', '.join(_MISSING_CONFIG)} missing"

    # ── Step 1: acquire connection ────────────────────────────────────────────
    try:
        conn = get_connection()
    except oracledb.DatabaseError as exc:
        logger.error(
            "[db] CONNECTION FAILED | host=%s port=%s service=%s user=%s | %s",
            DB_HOST, DB_PORT, DB_SERVICE, DB_USER, exc,
        )
        return [], [], f"Connection failed: {exc}"

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
        cursor.execute(clean_sql)
        columns = [col[0] for col in cursor.description]
        rows    = cursor.fetchmany(DB_MAX_ROWS)
        logger.info(
            "[db] Query OK | columns=%d | rows_fetched=%d | max_rows=%d",
            len(columns), len(rows), DB_MAX_ROWS,
        )
        if len(rows) == DB_MAX_ROWS:
            logger.warning(
                "[db] Row cap hit (%d) — result may be truncated. "
                "Increase DV_DB_MAX_ROWS if needed.",
                DB_MAX_ROWS,
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
        return [], [], f"Query execution failed: {exc}"

    except Exception as exc:
        logger.error(
            "[db] UNEXPECTED ERROR during query execution | type=%s | %s\nSQL was:\n%s",
            type(exc).__name__, exc, sql,
        )
        return [], [], f"Unexpected error: {exc}"

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
            conn.close()
