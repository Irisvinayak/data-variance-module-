# api/ — the HTTP layer, one module per functional area.
#
# backend/main.py used to hold every route plus every private helper and had
# grown to ~1400 lines. The split is along the lines the code already had:
# every private helper served exactly one route (/variance/nlresolve), so
# there is no shared-helper module — each router owns what only it uses.
#
# URLs, tags, status codes and response shapes are unchanged; main.py mounts
# these routers and remains the only place that owns the app object, the
# middleware and the app-level exception handlers (which an APIRouter cannot
# carry).
#
# IMPORTANT: the `from ..nlp...` imports inside the nlp router's handlers are
# function-local on purpose — they keep the ~1.3GB embedding model and FAISS
# out of a deployment that only uses the manual routes. Do not hoist them to
# module scope: including the router at startup would then import them
# eagerly, and a box without sentence-transformers would fail to boot instead
# of degrading to "the NLP routes 500, everything else works".

from __future__ import annotations

from fastapi import APIRouter

from . import auth, meta, nlp, variance

#: Mounted by backend/main.py in this order.
ROUTERS: tuple[APIRouter, ...] = (
    meta.router,
    variance.router,
    nlp.router,
    auth.router,
)

__all__ = ["ROUTERS"]
