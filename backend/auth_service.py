# auth_deps.py — FastAPI dependency injectors for authorization.
#
# How it plugs into routes:
#
#   Step 1 — require_login(loginId query param)
#             Validates the user exists in XML_User.xml.
#             Returns the clean login_id string on success.
#             Raises 401 if loginId is missing.
#             Raises 403 if the user is not found in XML_User.xml.
#
#   Step 2 — require_return_access(login_id, return_id)
#             Checks the return_id is in the user's Forms/NXForms list.
#             Raises 403 with a clear message if not.
#             Call this inline inside route handlers after require_login resolves.
#
# Example usage in main.py:
#
#   from .auth_deps import require_login, require_return_access
#
#   @app.get("/variance/find")
#   async def variance_find(
#       return_name: str,
#       login_id: str = Depends(require_login),
#   ): ...
#
#   @app.post("/variance/compute")
#   async def variance_compute(
#       payload: VarianceComputeRequest,
#       login_id: str = Depends(require_login),
#   ):
#       require_return_access(login_id, payload.return_id)
#       ...

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Query, status

from .auth_service import get_allowed_form_ids, is_return_allowed

logger = logging.getLogger(__name__)


def require_login(
    loginId: str = Query(
        default="",
        description="Login ID passed by the .NET host application via query param.",
    )
) -> str:
    """FastAPI Dependency — validates loginId query param and confirms user exists.

    The .NET app passes this as ?loginId=iris810 in the iframe URL.
    FastAPI automatically injects it from the query string.

    Raises
    ------
    401  loginId param is missing or blank.
    403  loginId not found in XML_User.xml.

    Returns
    -------
    str  The validated, stripped login_id on success.
    """
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
        # User not in XML_User.xml
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
    """Raise HTTP 403 if the user does not have access to return_id.

    Call this inline inside a route handler after require_login resolves:

        require_return_access(login_id, payload.return_id)

    Raises
    ------
    403  return_id is not in the user's Forms or NXForms list.
    """
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