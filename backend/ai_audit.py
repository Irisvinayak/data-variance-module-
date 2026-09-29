# ai_audit.py — a structured audit trail for every LLM call.
#
# WHY THIS EXISTS
# The prose log records only that an LLM was called and how many characters
# went in and out:
#
#   [nlp.sql_generator] Calling Ollama | model=sqlcoder | prompt_chars=4210
#   [nlp.sql_generator] Ollama responded | model=sqlcoder | response_chars=180
#
# which is not enough to answer the question an operator actually asks after a
# surprising answer: *what did the model produce, and why was it accepted?*
# The generated SQL was never logged on the success path, the rejected SQL was
# never logged at all, the retry was indistinguishable from the first attempt
# (identical text), and there was no latency, no validation verdict and no
# correlation to the request that caused it.
#
# This module writes one JSON object per LLM call to logs/ai/<date>.jsonl —
# one line per record, so it can be grepped, tailed, or read with
# `json.loads` line by line without a parser for the prose format.
#
# It is ADDITIVE: the existing prose lines are untouched, so anything that
# reads today's logs keeps working.
#
# CONTENT WARNING: records contain the user's natural-language query and the
# generated SQL, by design — that is the point of an audit trail. The SQL
# carries embedded literals (the prompt instructs the model to inline values),
# so reporting values appear here. No credentials are recorded. Retention is
# shared with the main log via DV_LOG_RETENTION_DAYS. See docs/logging.md.

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from .logging_config import (
    LOG_DIR,
    LOG_RETENTION_DAYS,
    DailyFileHandler,
    login_id_var,
    request_id_var,
    tenant_id_var,
)

logger = logging.getLogger(__name__)

AI_LOG_DIR: str = os.getenv("DV_AI_LOG_DIR", os.path.join(LOG_DIR, "ai"))

# Writing the audit trail must never take a request down, so every failure in
# here is swallowed after one warning. An unavailable audit log is a
# degradation, not an outage.
_lock = threading.Lock()
_handler: DailyFileHandler | None = None
_warned = False


class _JsonlHandler(DailyFileHandler):
    """DailyFileHandler writing <date>.jsonl instead of <date>.log.

    Reuses the parent's open-before-commit rollover and retention pruning so
    the audit stream cannot develop its own variant of the bug the main log
    handler had.
    """

    suffix = ".jsonl"


def _get_handler() -> DailyFileHandler | None:
    global _handler, _warned
    if _handler is not None:
        return _handler
    with _lock:
        if _handler is None:
            try:
                _handler = _JsonlHandler(AI_LOG_DIR, LOG_RETENTION_DAYS)
                _handler.setFormatter(logging.Formatter("%(message)s"))
            except Exception as exc:
                if not _warned:
                    logger.warning(
                        "[ai_audit] cannot open the AI audit log at %s (%s) — "
                        "LLM calls will not be audited this run",
                        AI_LOG_DIR, exc,
                    )
                    _warned = True
                return None
    return _handler


def record(
    *,
    caller: str,
    model: str,
    query: str | None = None,
    login_id: str | None = None,
    tenant_id: str | None = None,
    **fields: Any,
) -> None:
    """Append one audit record. Never raises.

    `caller` names the code path ("sql_generator.generate_sql"), `model` the
    Ollama model. Everything else is free-form and merged in as-is, so a call
    site can add what it knows (latency_ms, sql, valid, retried, ...) without
    this module having to know about it first.
    """
    handler = _get_handler()
    if handler is None:
        return

    entry: dict[str, Any] = {
        "ts":         datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "request_id": request_id_var.get(),
        "login_id":   login_id  if login_id  is not None else login_id_var.get(),
        "tenant_id":  tenant_id if tenant_id is not None else tenant_id_var.get(),
        "caller":     caller,
        "model":      model,
    }
    if query is not None:
        entry["query"] = query
    entry.update(fields)

    try:
        # default=str so an unexpected non-serialisable value degrades to its
        # repr instead of losing the whole record.
        line = json.dumps(entry, ensure_ascii=False, default=str)
        rec = logging.LogRecord(
            name="ai_audit", level=logging.INFO, pathname=__file__, lineno=0,
            msg=line, args=(), exc_info=None,
        )
        # handle(), not emit(): handle() takes the handler's lock, and records
        # arrive from concurrent threadpool workers.
        handler.handle(rec)
    except Exception as exc:
        global _warned
        if not _warned:
            logger.warning("[ai_audit] failed to write an audit record: %s", exc)
            _warned = True
