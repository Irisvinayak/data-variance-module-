# api/ — the HTTP layer, one module per functional area.
#
# Each router owns the private helpers only it uses; errors.py holds the one
# shared exception-to-HTTP mapping. backend/main.py mounts these routers and
# owns the app object, the middleware and the app-level exception handlers
# (which an APIRouter cannot carry).
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
