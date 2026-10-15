# logging_config.py — one place that decides WHERE logs go and HOW MUCH gets
# logged by default.
#
# Policy:
#   - Default level is INFO: one log line per meaningful boundary (an API
#     request received/finished, an LLM call made, an auth decision) —
#     NOT per-row / per-column / per-candidate-path internals. Those are
#     demoted to DEBUG throughout the codebase so they only show up when
#     DV_LOG_LEVEL=DEBUG is set.
#   - WARNING/ERROR are for anything risk-relevant: access denied, no data
#     found, invalid input, an exception — these always show regardless of
#     the configured level.
#   - Logs go to both the console (dev convenience) and a logs/ folder, one
#     file per calendar date (e.g. logs/2026-07-09.log), created
#     automatically the first time a log line is emitted on a new date.
#
# Every line also carries a REQUEST ID (see request_id_var below). Without it
# the log lines of one HTTP request could only be correlated by login_id —
# which collides whenever two requests from the same user overlap, and which
# most of the call tree (db, calculate_variance, the LLM modules) never
# receives at all. The id is injected by a logging.Filter, so no call site
# has to pass it.

from __future__ import annotations

import logging
import os
import re
import uuid
from contextvars import ContextVar
from datetime import date, timedelta
from .config.settings import APP_VERSION
from .envutil import env_int

def default_log_dir(version: str) -> str:
    """<project>/logs/<version>. 5.5 and 6.0 run as separate processes on one machine; with a
    shared logs/<date>.log their lines interleave and the daily rollover collides
    (code review M-32). DV_LOG_DIR still overrides this completely."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    safe = "".join(ch for ch in str(version) if ch.isalnum() or ch in "._-") or "default"
    return os.path.join(root, "logs", safe)


LOG_DIR: str = os.getenv("DV_LOG_DIR") or default_log_dir(APP_VERSION)
LOG_LEVEL: str = os.getenv("DV_LOG_LEVEL", "INFO").strip().upper()

# Daily log files are never overwritten, so without a retention rule logs/
# grows without bound. 0 disables pruning.
LOG_RETENTION_DAYS: int = env_int("DV_LOG_RETENTION_DAYS", 30) if os.getenv("DV_LOG_RETENTION_DAYS", "30").strip() else 0   # blank = keep forever

_FORMAT = "%(asctime)s | %(levelname)s | %(request_id)s | %(name)s | %(message)s"

# ── Request correlation ────────────────────────────────────────────────────────
# Set once per HTTP request by the middleware in backend/main.py. A ContextVar
# rather than a thread-local because the routes run in Starlette's threadpool:
# anyio copies the caller's context into the worker thread, so the id set on
# the event loop is visible to the blocking route body and everything it calls.
# Outside a request (startup, scripts, tests) it reads "-".
request_id_var: ContextVar[str] = ContextVar("dv_request_id", default="-")

# The caller's identity, carried the same way and for the same reason: the
# whole compute + LLM + SQL tail (db.py, calculate_variance.py, the nlp
# modules) is deliberately host- and request-agnostic and never receives a
# RequestContext, so without this the AI audit trail could not say WHO a
# generated query belonged to. Set from the query string by the middleware in
# backend/main.py; "" outside a request.
login_id_var:  ContextVar[str] = ContextVar("dv_login_id",  default="")
tenant_id_var: ContextVar[str] = ContextVar("dv_tenant_id", default="")


def new_request_id() -> str:
    """A short id — long enough not to collide within a log file, short enough
    to keep the line readable."""
    return uuid.uuid4().hex[:8]


class _RequestIdFilter(logging.Filter):
    """Injects request_id into every record so _FORMAT can reference it.

    A Filter rather than a LoggerAdapter because it applies to records from
    third-party libraries too, which never go through our call sites.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# ── Optional identity redaction (code review M-09) ────────────────────────────
# Under 6.0 the login is an e-mail address, and it ends up in the log through the request
# context, the access-log query string and error lines, kept for LOG_RETENTION_DAYS.
# DV_LOG_REDACT_IDENTITY=true replaces each identity with a STABLE pseudonym (u-<8 hex>):
# the same person gets the same token on every line, so a problem can still be followed
# through the log, but the log no longer names them. Off by default: operators who debug
# by login name keep working until they choose otherwise.
_REDACT_IDENTITY = os.environ.get("DV_LOG_REDACT_IDENTITY", "").strip().lower() in {"1", "true", "yes", "on"}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_LOGIN_PARAM_RE = re.compile(r"(?i)\b(login_?id)(=|%3D)([^&\s'\"|,)]+)")
_LOGIN_FIELD_RE = re.compile(r"(login_id=)(['\"])(.*?)\2")


def pseudonym(value: str) -> str:
    """Stable, non-reversible stand-in for an identity: 'u-' + 8 hex of its SHA-256."""
    import hashlib

    return "u-" + hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:8]


def redact_identity(text: str) -> str:
    text = _LOGIN_FIELD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{pseudonym(m.group(3))}{m.group(2)}" if m.group(3) else m.group(0), text)
    text = _LOGIN_PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{pseudonym(m.group(3))}", text)
    return _EMAIL_RE.sub(lambda m: pseudonym(m.group(0)), text)


# ── Secrets: always redacted (code review M-09) ───────────────────────────────
# Nothing in this code base logs a password or token on purpose, but a log line can still
# carry one by accident: an access-log query string, a driver error that quotes a connect
# string, an exception that echoes a header. Unlike identity (above), secrets have no
# debugging value, so this filter is ALWAYS on and not configurable. It replaces only the
# value, never the surrounding line, so the line stays useful.
_SECRET_PARTS = (
    # Authorization: Bearer <token>   /   Bearer <token>
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), lambda m: f"{m.group(1)} ***"),
    # a JWT anywhere (three base64url segments starting with the header's "eyJ")
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), lambda m: "***jwt***"),
    # password=..., "password": "...", token: ..., api_key=..., a session-cookie value, DV_JWT_SECRET=...
    (
        re.compile(
            r"(?i)(?P<k>['\"]?\b(?:[a-z0-9_]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
            r"auth[_-]?token|jwt|asp[_-]?session|session[_-]?id)|token|_at)['\"]?\s*[:=]\s*)"
            r"(?:(?P<q>['\"])[^'\"]*(?P=q)|[^'\"&\s,;|)}]+)"
        ),
        lambda m: m.group("k") + (f"{m.group('q')}***{m.group('q')}" if m.group("q") else "***"),
    ),
    # Oracle easy-connect / SQL*Plus style  user/password@host
    (re.compile(r"\b([A-Za-z0-9_$#]+)/([^\s@/'\"]+)@([A-Za-z0-9_.:/-]+)"), lambda m: f"{m.group(1)}/***@{m.group(3)}"),
)


def redact_secrets(text: str) -> str:
    for pattern, repl in _SECRET_PARTS:
        text = pattern.sub(repl, text)
    return text


class _RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never lose a log line over a bad format string
            return True
        cleaned = redact_secrets(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


class _RedactIdentityFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never lose a log line over a bad format string
            return True
        record.msg = redact_identity(message)
        record.args = ()
        return True


class DailyFileHandler(logging.Handler):
    """Writes to logs/<YYYY-MM-DD>.log, switching to a fresh file the first
    time a record is emitted after midnight — no restart needed."""

    #: Extension of the per-day file; subclasses change only this.
    suffix: str = ".log"

    def __init__(self, log_dir: str, retention_days: int = 0):
        super().__init__()
        self._log_dir = log_dir
        self._retention_days = retention_days
        self._current_date: str | None = None
        self._stream = None
        os.makedirs(self._log_dir, exist_ok=True)

    def _ensure_current_file(self) -> None:
        today = date.today().isoformat()
        if today == self._current_date and self._stream is not None:
            return

        path = os.path.join(self._log_dir, f"{today}{self.suffix}")
        # Open the NEW file before touching any state. Assigning _current_date
        # first (as this used to) meant a failed open latched the handler into
        # a state where every later emit short-circuited on the date check and
        # then wrote to a None/closed stream — silently killing file logging
        # for the rest of the day. Open first, commit after.
        stream = open(path, "a", encoding="utf-8")  # noqa: SIM115 - kept open as the handler stream

        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        self._stream = stream
        self._current_date = today
        self._prune()

    def _prune(self) -> None:
        """Delete log files older than the retention window. Best-effort: a
        failure to prune must never stop the app from logging."""
        if self._retention_days <= 0:
            return
        cutoff = date.today() - timedelta(days=self._retention_days)
        try:
            for name in os.listdir(self._log_dir):
                match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(?:log|jsonl)", name)
                if not match:
                    continue
                if date.fromisoformat(match.group(1)) < cutoff:
                    os.remove(os.path.join(self._log_dir, name))
        except Exception:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_current_file()
            if self._stream is None:
                return
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
        super().close()


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        _adopt_uvicorn_loggers()
        return  # already configured (e.g. uvicorn --reload re-importing main)

    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    formatter = logging.Formatter(_FORMAT)
    request_id_filter = _RequestIdFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(request_id_filter)
    console.addFilter(_RedactSecretsFilter())
    if _REDACT_IDENTITY:
        console.addFilter(_RedactIdentityFilter())
    root.addHandler(console)

    file_handler = DailyFileHandler(LOG_DIR, LOG_RETENTION_DAYS)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_id_filter)
    file_handler.addFilter(_RedactSecretsFilter())
    if _REDACT_IDENTITY:
        file_handler.addFilter(_RedactIdentityFilter())
    root.addHandler(file_handler)

    # Third-party libraries that log every HTTP HEAD/GET at INFO (e.g. the
    # one-time HuggingFace model download) — quiet unless something's wrong.
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _adopt_uvicorn_loggers()


def _adopt_uvicorn_loggers() -> None:
    """Let uvicorn's own loggers reach our handlers.

    uvicorn installs `uvicorn`, `uvicorn.error` and `uvicorn.access` with their
    own handlers and propagate=False, and never touches the root logger. The
    result was that the log FILE contained no HTTP status or latency line for
    any request — every response code had to be inferred from hand-written
    [main] lines. Clearing their handlers and re-enabling propagation routes
    them through the same format, file and retention as everything else.
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
