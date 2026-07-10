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

from __future__ import annotations

import logging
import os
from datetime import date

LOG_DIR: str = os.getenv(
    "DV_LOG_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"),
)
LOG_LEVEL: str = os.getenv("DV_LOG_LEVEL", "INFO").strip().upper()

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class DailyFileHandler(logging.Handler):
    """Writes to logs/<YYYY-MM-DD>.log, switching to a fresh file the first
    time a record is emitted after midnight — no restart needed."""

    def __init__(self, log_dir: str):
        super().__init__()
        self._log_dir = log_dir
        self._current_date: str | None = None
        self._stream = None
        os.makedirs(self._log_dir, exist_ok=True)

    def _ensure_current_file(self) -> None:
        today = date.today().isoformat()
        if today == self._current_date:
            return
        if self._stream is not None:
            self._stream.close()
        self._current_date = today
        path = os.path.join(self._log_dir, f"{today}.log")
        self._stream = open(path, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_current_file()
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
        super().close()


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. uvicorn --reload re-importing main)

    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = DailyFileHandler(LOG_DIR)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Third-party libraries that log every HTTP HEAD/GET at INFO (e.g. the
    # one-time HuggingFace model download) — quiet unless something's wrong.
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
