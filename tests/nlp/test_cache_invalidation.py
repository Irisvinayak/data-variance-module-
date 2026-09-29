"""Every invalidate() must leave its cache usable.

backend/nlp/return_lookup.invalidate() used to rebind its per-tenant dicts to
None/0.0:

    global _cache, _cache_ts
    with _lock:
        _cache = None          # _cache is a dict, keyed by tenant
        _cache_ts = 0.0        # so is _cache_ts

The next _get_lookup() then raised
`TypeError: argument of type 'NoneType' is not iterable`. Nothing called
invalidate(), so it never fired in production - it was waiting for the first
caller. These caches are a documented test/admin seam, so "nothing calls it"
is not a reason to leave it broken.

This walks the whole family rather than the one that was wrong, because they
are hand-written copies of the same stale-while-revalidate scaffolding and
the next copy can drift the same way.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.auth import service as auth_service      # noqa: E402
from backend.data import query_xml_lookup  # noqa: E402
from backend.nlp import indexed_returns, return_lookup    # noqa: E402

INVALIDATORS = [
    pytest.param(auth_service.invalidate, id="auth.service.invalidate"),
    pytest.param(auth_service.invalidate_role_cache, id="auth.service.invalidate_role_cache"),
    pytest.param(query_xml_lookup.invalidate, id="data.query_xml_lookup.invalidate"),
    pytest.param(indexed_returns.invalidate, id="nlp.indexed_returns.invalidate"),
    pytest.param(return_lookup.invalidate, id="nlp.return_lookup.invalidate"),
]


@pytest.mark.parametrize("invalidate", INVALIDATORS)
def test_invalidate_is_callable_and_repeatable(invalidate):
    """It must not raise, and calling it twice must be as safe as once."""
    import inspect

    # Some take a ctx (the auth ones are per-login), some take nothing.
    if inspect.signature(invalidate).parameters:
        from backend.config import ANONYMOUS
        invalidate(ANONYMOUS)
        invalidate(ANONYMOUS)
    else:
        invalidate()
        invalidate()


@pytest.mark.parametrize(
    "module", [query_xml_lookup, indexed_returns, return_lookup],
    ids=lambda m: m.__name__.rsplit(".", 1)[-1],
)
def test_invalidate_leaves_the_caches_as_usable_mappings(module):
    """The bug was a TYPE change: a per-key dict was rebound to None, and the
    next read blew up on `in`. So assert the concrete contract - after
    invalidate() the caches are still empty MAPPINGS you can read - rather than
    "same type as before", which passes vacuously once an earlier test has
    already corrupted the module.
    """
    import inspect

    fn = module.invalidate
    if inspect.signature(fn).parameters:
        from backend.config import ANONYMOUS
        fn(ANONYMOUS)
    else:
        fn()

    checked = 0
    for name in ("_cache", "_cache_ts"):
        if not hasattr(module, name):
            continue
        value = getattr(module, name)
        assert value is not None, (
            f"{module.__name__}.{name} is None after invalidate(); the next "
            f"read will raise TypeError"
        )
        # The operations the module itself performs on it right after a miss.
        assert hasattr(value, "get") and hasattr(value, "__contains__"), (
            f"{module.__name__}.{name} is a {type(value).__name__}, not a mapping"
        )
        assert "some-tenant" not in value      # the `in` that used to raise
        assert value.get("some-tenant") is None
        checked += 1

    assert checked, f"{module.__name__} exposes no _cache/_cache_ts to check"
