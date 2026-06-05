# db.py — Oracle connection pool for the standalone Data Variance backend.
# Extracted from the chatbot's sql_agent/executor.py with all FAISS / LLM
# dependencies removed.  Only execute_query() is exported.

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

import oracledb

from .config import DB_HOST, DB_PORT, DB_SERVICE, DB_USER, DB_PASSWORD, DB_MAX_ROWS

logger = logging.getLogger(__name__)

_pool: oracledb.ConnectionPool | None = None


def _nls_session_callback(conn, requested_tag, actual_tag):
    """Set NLS parameters once per new physical connection."""
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
    if _pool is None:
        dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)
        _pool = oracledb.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=dsn,
            min=1,
            max=5,
            increment=1,
            session_callback=_nls_session_callback,
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
            dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)
            conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn)
            logger.info("[db] Direct connection established (fallback)")
            return conn
        except oracledb.DatabaseError as direct_exc:
            logger.error(
                "[db] FATAL — direct connect also failed | host=%s port=%s service=%s user=%s | error=%s",
                DB_HOST, DB_PORT, DB_SERVICE, DB_USER, direct_exc,
            )
            raise


def execute_query(sql: str) -> Tuple[List[str], List[Any], Optional[str]]:
    """Execute a SELECT query against Oracle DB.

    Returns
    -------
    (columns, rows, error)  — error is None on success.
    """
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
        cursor = conn.cursor()
        cursor.callTimeout = 60_000  # 60-second timeout
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
        if cursor is not None:
            cursor.close()
        conn.close()
