# deps.py — FastAPI dependencies that turn host-supplied query params into a
# validated RequestContext.
#
# Both iDEAL hosts pass identity in the query string, so one dependency serves
# both: 5.5 sends only ?loginId=, 6.0 sends ?loginId=&tenantId=. Whether
# tenantId is required is the profile's call, not this module's.
#
# SECURITY NOTE: these values are taken on trust from the query string. Under
# 6.0 the React app decodes the JWT client-side and forwards the claims as
# plain params, so anyone can call the API directly with any loginId/tenantId
# they like. Verifying the token server-side is Phase 4 of INTEGRATION_PLAN.md
# (see section 4) and belongs here, in the profile-aware layer.

from __future__ import annotations

import logging

from fastapi import HTTPException, Query, status

from ..config import AUTH_ENABLED, RequestContext
from ..hosts import HostProfileError, get_profile
from .service import get_allowed_form_ids, is_return_allowed

logger = logging.getLogger(__name__)


def require_login(
    loginId: str = Query(
        default="",
        description="Login ID passed by the .NET host application via query param.",
    ),
    tenantId: str = Query(
        default="",
        description="Tenant ID passed by the iDEAL 6.0 host. Ignored under 5.5.",
    ),
) -> RequestContext:
    """Validate the caller and return the context the data layer needs."""
    ctx = RequestContext(login_id=loginId, tenant_id=tenantId)
    profile = get_profile()

    if not AUTH_ENABLED:
        logger.warning("[AUTH_DEP] AUTH_DISABLED — bypassing login validation | %s", ctx)
        return ctx

    if not ctx.login_id:
        logger.warning("[AUTH_DEP] Request rejected — loginId query param is missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required. Please provide a valid loginId query parameter.",
        )

    if profile.requires_tenant and not ctx.tenant_id:
        logger.warning("[AUTH_DEP] Request rejected — tenantId required by %s", profile.name)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required. Please provide a valid tenantId query parameter.",
        )

    # Surfaces an unusable tenant as a clear 403 rather than letting it become
    # an empty result set from a folder that does not exist.
    try:
        profile.validate_context(ctx)
    except HostProfileError as exc:
        logger.warning("[AUTH_DEP] REJECTED — %s | %s", exc, ctx)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    allowed = get_allowed_form_ids(ctx)
    if allowed is None:
        logger.warning("[AUTH_DEP] REJECTED — login_id=%r not found in user master", ctx.login_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.login_id}' is not authorised to use this application.",
        )

    logger.debug("[AUTH_DEP] %s authenticated | %d form(s) in allowed set", ctx, len(allowed))
    return ctx


def require_return_access(ctx: RequestContext, return_id: str) -> None:
    if not AUTH_ENABLED:
        logger.warning("[AUTH_DEP] AUTH_DISABLED — bypassing return access validation")
        return

    if not is_return_allowed(ctx, return_id):
        logger.warning("[AUTH_DEP] ACCESS DENIED | %s | return_id=%r", ctx, return_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User '{ctx.login_id}' does not have access to return '{return_id}'. "
                "Contact your administrator to update your department's access list."
            ),
        )
