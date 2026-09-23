# auth.py — /auth/my-returns
#
# Split out of backend/main.py, which had grown to ~1400 lines holding every
# route. Route bodies are unchanged; only the decorator and the imports moved.
# backend/main.py mounts this router, so the URLs are identical.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from ..auth.deps import require_login
from ..config import RequestContext

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /auth/my-returns ───────────────────────────────────────────────────────
@router.get("/auth/my-returns", status_code=status.HTTP_200_OK, tags=["Auth"])
async def my_returns(ctx: RequestContext = Depends(require_login)) -> dict:
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
