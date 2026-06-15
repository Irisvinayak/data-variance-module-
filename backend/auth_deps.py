from __future__ import annotations

import logging
from typing import Annotated

from fastapi import HTTPException, Query, status

from .auth_service import get_allowed_form_ids, is_return_allowed

logger = logging.getLogger(__name__)


def require_login(
    loginId: str = Query(
        default="",
        description="Login ID passed by the .NET host application via query param.",
    ),
    tenantId: str = Query(
        default="",
        description="Tenant ID passed by the .NET host application via query param.",
    ),
) -> tuple[str, str]:
    """
    Dependency that validates both loginId and tenantId query params.

    Returns (login_id, tenant_id) on success.
    Raises 401 if either param is missing/blank.
    Raises 403 if the user is not found within the tenant's user.xml.
    """
    clean_login  = loginId.strip()
    clean_tenant = tenantId.strip()

    if not clean_login:
        logger.warning("[AUTH_DEP] Request rejected — loginId query param is missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "loginId query parameter is required. "
                "The .NET host must pass ?loginId=<your_login> in the URL."
            ),
        )

    if not clean_tenant:
        logger.warning("[AUTH_DEP] Request rejected — tenantId query param is missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "tenantId query parameter is required. "
                "The .NET host must pass ?tenantId=<tenant_id> in the URL."
            ),
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
        "[AUTH_DEP] login_id=%r tenant_id=%r authenticated | %d form(s) in allowed set",
        clean_login, clean_tenant, len(allowed),
    )
    return clean_login, clean_tenant


def require_return_access(login_id: str, tenant_id: str, return_id: str) -> None:
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