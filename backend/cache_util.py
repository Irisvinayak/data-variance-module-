# cache_util.py — keep the module-level caches from growing without bound.
#
# Several caches here are plain dicts keyed by login or return id. They expire by
# TTL, but expiry only happens when a key is looked up again, so a stream of
# distinct keys (random loginId values — and an unknown login is cached too)
# grew them for the life of the process (code review CONC-01).

from __future__ import annotations

from itertools import islice
from typing import Any, MutableMapping


def put_bounded(cache: MutableMapping, key: Any, value: Any, max_entries: int) -> None:
    """Store `value` and evict the oldest entries beyond `max_entries`.

    Dicts keep insertion order, so the front of the dict is the oldest write.
    The caller must already hold whatever lock guards `cache`; this does no
    locking of its own so it can sit inside an existing critical section.
    """
    cache.pop(key, None)            # re-inserting moves a refreshed key to the back
    cache[key] = value
    overflow = len(cache) - max_entries
    if overflow > 0:
        for stale in list(islice(iter(cache), overflow)):
            cache.pop(stale, None)
