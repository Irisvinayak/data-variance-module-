# limits.py — request budget and body-size guard (code review API-01).
#
# Each NL request costs an embedding, an LLM call and several Oracle round
# trips, and nothing bounded how many a caller could send, so a small flood could
# exhaust the threadpool, the DB pool and the LLM proxy for everyone.
#
# Why in-process and keyed by login: the app runs as one process per host
# version behind IIS, so an in-process counter sees all of that version's
# traffic; and behind the IIS reverse proxy every client address is 127.0.0.1,
# so an address cannot tell two users apart. The login is client-asserted until
# DV_IDENTITY_MODE verifies it, so a global ceiling backs the per-login one.
#
# Defaults are generous on purpose (see settings.py): they stop floods, not
# normal use. Setting a limit to 0 disables it.

from __future__ import annotations

import threading
import time
from collections import deque

WINDOW_SEC = 60.0
_MAX_KEYS = 10_000          # bound memory under a flood of distinct logins


class SlidingWindowLimiter:
    """Allow at most `limit` events per key in any `window` seconds."""

    def __init__(self, limit: int, window: float = WINDOW_SEC) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Records the hit only when allowed."""
        if self.limit <= 0:
            return True, 0
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= _MAX_KEYS:
                    self._evict(t)
                q = self._hits[key] = deque()
            cutoff = t - self.window
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(q[0] + self.window - t) + 1)
            q.append(t)
            return True, 0

    def _evict(self, now: float) -> None:
        cutoff = now - self.window
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[k]
        if len(self._hits) >= _MAX_KEYS:        # still full: drop the oldest half
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[: _MAX_KEYS // 2]:
                del self._hits[k]


def is_nl_path(path: str) -> bool:
    """The routes that run the embedding model and an LLM — the expensive ones."""
    return path.rstrip("/").endswith(("/variance/nlresolve", "/variance/nlquery"))
