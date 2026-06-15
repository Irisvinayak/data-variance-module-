from __future__ import annotations

import logging

from fastapi import HTTPException, Query, status

from .auth_service import get_allowed_form_ids, is_return_allowed

logger = logging.getLogger(__name__)


def require_login(
    loginId: str = Query(
        default="",
        description="Login ID passed by the .NET host application via query param.",
    )
) -> str:
    clean = loginId.strip()
    if not clean:
        logger.warning("[AUTH_DEP] Request rejected — loginId query param is missing/blank")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="loginId query parameter is required. "
                   "The .NET host must pass ?loginId=<your_login> in the URL.",
        )

    allowed = get_allowed_form_ids(clean)

    if allowed is None:
        logger.warning("[AUTH_DEP] REJECTED — login_id=%r not found in XML_User.xml", clean)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{clean}' is not authorised to use this application.",
        )

    logger.debug(
        "[AUTH_DEP] login_id=%r authenticated | %d form(s) in allowed set",
        clean, len(allowed),
    )
    return clean


def require_return_access(login_id: str, return_id: str) -> None:
    if not is_return_allowed(login_id, return_id):
        logger.warning(
            "[AUTH_DEP] ACCESS DENIED | login_id=%r | return_id=%r", login_id, return_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User '{login_id}' does not have access to return '{return_id}'. "
                "Contact your administrator to update your department's access list."
            ),
        )