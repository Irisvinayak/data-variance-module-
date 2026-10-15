# llm_guard.py — circuit breaker and concurrency cap for calls to the LLM proxy.
#
# The intent path already had a cooldown (intent_resolver). The SQL-generation
# path did not (code review LLM-04): during an Ollama outage every request held
# a threadpool worker for the full timeout, plus a retry, which starved the
# manual routes that need no LLM at all. This module gives that path the same
# protection, plus a ceiling on how many LLM calls run at once.

from __future__ import annotations

from ..envutil import env_float, env_int
import threading
import time


def note_if_cut_off(caller: str, body, model: str, attempt: int) -> bool:
    """Log and count a reply that stopped because it hit the token cap (num_predict).

    Ollama reports `done_reason: "length"` in that case. A SQL statement or JSON object cut off
    mid-way then fails validation and costs a retry, but nothing used to say WHY - which is the
    evidence needed before anyone raises the cap (code review M-19). Observation only: the
    reply is returned unchanged and no limit is touched. Returns True when it was cut off."""
    import logging

    from .. import metrics

    if isinstance(body, dict) and body.get("done_reason") == "length":
        logging.getLogger(__name__).warning(
            "[nlp.%s] reply stopped at the token cap (done_reason=length) | model=%s | attempt=%d | "
            "see DV_SQL_NUM_PREDICT / DV_NLP_INTENT_NUM_PREDICT", caller, model, attempt,
        )
        metrics.inc("dv_llm_truncated_total", caller=caller)
        return True
    return False


def make_session(pool_size: int | None = None):
    """A requests.Session whose connection pool matches the server's concurrency.

    The default pool keeps 10 connections per host; the API runs requests in a thread
    pool, so more simultaneous LLM calls than that open throw-away connections and log
    "Connection pool is full" (code review L-18). DV_LLM_HTTP_POOL overrides the size."""

    import requests
    from requests.adapters import HTTPAdapter

    size = pool_size or max(env_int("DV_LLM_HTTP_POOL", "40"), 10)
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class CircuitBreaker:
    """Open for `cooldown_sec` after a transport failure; callers fail fast."""

    def __init__(self, cooldown_sec: float) -> None:
        self.cooldown_sec = cooldown_sec
        self._until = 0.0
        self._lock = threading.Lock()

    def in_cooldown(self) -> bool:
        with self._lock:
            return time.monotonic() < self._until

    def trip(self) -> None:
        with self._lock:
            self._until = time.monotonic() + self.cooldown_sec

    def reset(self) -> None:
        with self._lock:
            self._until = 0.0


# Calls to the shared Ollama proxy that may be in flight at once from this
# process. The proxy serialises generation anyway; queueing more here only parks
# threadpool workers.
LLM_MAX_CONCURRENT: int = max(env_int("DV_LLM_MAX_CONCURRENT", "4"), 1)
LLM_SLOT_WAIT_SEC: float = env_float("DV_LLM_SLOT_WAIT_SEC", "5")
llm_slots = threading.BoundedSemaphore(LLM_MAX_CONCURRENT)
