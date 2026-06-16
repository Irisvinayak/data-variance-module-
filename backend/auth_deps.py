from __future__ import annotations
import logging
from fastapi import HTTPException, Query, status
from .auth_service import get_allowed_form_ids, is_return_allowed
from .config import AUTH_ENABLED

logger = logging.getLogger(__name__)


def require_login(
    loginId:  str = Query(default=""),
    tenantId: str = Query(default=""),
) -> tuple[str, str]:
    clean_login  = loginId.strip()
    clean_tenant = tenantId.strip()

    # ── Dev bypass ────────────────────────────────────────────────────────────
    if not AUTH_ENABLED:
        logger.warning(
            "[AUTH_DEP] ⚠ AUTH DISABLED (DV_AUTH_ENABLED=false) — "
            "skipping login check for login_id=%r tenant_id=%r",
            clean_login, clean_tenant,
        )
        return clean_login or "dev_user", clean_tenant or "dev_tenant"

    if not clean_login:
        logger.warning("[AUTH_DEP] Request rejected — loginId missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="loginId query parameter is required.",
        )
    if not clean_tenant:
        logger.warning("[AUTH_DEP] Request rejected — tenantId missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="tenantId query parameter is required.",
        )

    allowed = get_allowed_form_ids(clean_login, clean_tenant)
    if allowed is None:
        logger.warning(
            "[AUTH_DEP] REJECTED — login_id=%r not found in tenant=%r user.xml",
            clean_login, clean_tenant,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{clean_login}' is not authorised to use this application.",
        )

    logger.debug(
        "[AUTH_DEP] login_id=%r tenant_id=%r authenticated | %d form(s)",
        clean_login, clean_tenant, len(allowed),
    )
    return clean_login, clean_tenant


def require_return_access(login_id: str, tenant_id: str, return_id: str) -> None:
    if not AUTH_ENABLED:
        logger.warning(
            "[AUTH_DEP] ⚠ AUTH DISABLED — skipping return access check for return_id=%r",
            return_id,
        )
        return

    if not is_return_allowed(login_id, tenant_id, return_id):
        logger.warning(
            "[AUTH_DEP] ACCESS DENIED | tenant_id=%r | login_id=%r | return_id=%r",
            tenant_id, login_id, return_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User '{login_id}' does not have access to return '{return_id}'. "
                "Contact your administrator to update your department's access list."
            ),
        )