# auth.py — /auth/my-returns

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from ..auth.deps import require_login
from ..config import RequestContext

logger = logging.getLogger(__name__)

router = APIRouter()

# Handlers are `def`, not `async def`, on purpose — see backend/api/variance.py.


# ── GET /auth/my-returns ───────────────────────────────────────────────────────
@router.get("/auth/my-returns", status_code=status.HTTP_200_OK, tags=["Auth"])
def my_returns(ctx: RequestContext = Depends(require_login)) -> dict:
    """Return the list of return IDs the current user is allowed to access.

    Useful for debugging access issues.
    Remove or restrict to internal IPs in production.

    Example: GET /auth/my-returns?loginId=iris810
    """
    from ..auth.service import get_allowed_form_ids
    allowed = get_allowed_form_ids(ctx) or set()
    return {
        "login_id":      ctx.login_id,
        "tenant_id":     ctx.tenant_id,
        "allowed_count": len(allowed),
        "allowed_forms": sorted(allowed),
    }
