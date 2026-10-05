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
