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
# they like. Verifying the token server-side is Phase 4 of docs/integration-plan.md
# (see section 4) and belongs here, in the profile-aware layer.

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, Query, status

from ..config import AUTH_ENABLED, DEV_TENANT_ID, RequestContext
from ..config import settings as cfg
from ..hosts import HostProfileError, get_profile
from .identity import TokenError, extract_bearer, identity_from_claims, verify_jwt
from .service import get_allowed_form_ids, is_return_allowed

logger = logging.getLogger(__name__)

_GENERIC_TENANT_DENIED = "This tenant is not authorised to use this application."


def _unauthorized(detail: str = "Authentication failed.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verified_identity(authorization: str | None, profile) -> tuple[str, str] | None:
    """(login_id, tenant_id) from a VERIFIED bearer token, or None to use the URL.

    Governed by DV_IDENTITY_MODE (settings.py):
      off       -> the header is ignored entirely (today's behaviour);
      optional  -> a token that is present MUST verify; none -> None (URL identity);
      required  -> no verified token means 401.
    A token that is present but does not verify is always a 401, never a silent
    fallback to the URL: otherwise an attacker (or an expired token) would be
    downgraded to the unauthenticated path instead of being refused.
    """
    mode = cfg.IDENTITY_MODE
    if mode == "off":
        return None

    token = extract_bearer(authorization)
    if token is None:
        if mode == "required":
            logger.warning("[AUTH_DEP] REJECTED — DV_IDENTITY_MODE=required and no bearer token")
            raise _unauthorized()
        return None

    try:
        claims = verify_jwt(
            token,
            secret=cfg.JWT_SECRET,
            algorithms=cfg.JWT_ALGORITHMS,
            issuer=cfg.JWT_ISSUER,
            audience=cfg.JWT_AUDIENCE,
            leeway=cfg.JWT_LEEWAY_SEC,
        )
        who = identity_from_claims(
            claims,
            login_claim=cfg.JWT_LOGIN_CLAIM,
            tenant_claim=cfg.JWT_TENANT_CLAIM,
            requires_tenant=profile.requires_tenant,
        )
    except TokenError as exc:
        # The reason is for operators only; the client always sees the same 401.
        logger.warning("[AUTH_DEP] REJECTED — bearer token not accepted: %s", exc)
        raise _unauthorized() from exc
    return who.login_id, who.tenant_id


# Emitted once per process rather than once per request: "auth is disabled" is
# a property of the deployment, not of any one call. Repeating it three times
# per request buried genuine WARNINGs (an access denial, a missing user) in
# noise that never changes.
_auth_disabled_announced = False


def _announce_auth_disabled() -> None:
    global _auth_disabled_announced
    if _auth_disabled_announced:
        return
    _auth_disabled_announced = True
    logger.warning(
        "[AUTH_DEP] AUTH_DISABLED (DV_AUTH_ENABLED=false) — login validation and "
        "return-access checks are BYPASSED for every request in this process. "
        "This must never be the case in production."
    )


def require_login(
    loginId: str = Query(
        default="",
        description="Login ID passed by the .NET host application via query param.",
    ),
    tenantId: str = Query(
        default="",
        description="Tenant ID passed by the iDEAL 6.0 host. Ignored under 5.5.",
    ),
    authorization: str | None = Header(
        default=None,
        description="Optional 'Bearer <jwt>' from the host. Verified when DV_IDENTITY_MODE is optional/required.",
    ),
    x_tenant_id: str | None = Header(
        default=None,
        alias="X-Tenant-Id",
        description="Tenant the caller believes it belongs to. Never trusted on its own: "
                    "with a verified token it must equal the token's tenant (403 otherwise).",
    ),
) -> RequestContext:
    """Validate the caller and return the context the data layer needs."""
    profile = get_profile()
    verified = _verified_identity(authorization, profile)
    if verified is not None:
        # The tenant is whatever the VERIFIED token says. A tenant supplied any
        # other way (the X-Tenant-Id header, the ?tenantId= parameter) is only a
        # claim to be checked against it: a mismatch is a request for another
        # tenant's data and is refused (403), not quietly rewritten (code review
        # C-01). Only meaningful under a tenant-scoped host; 5.5 has no tenants.
        if profile.requires_tenant:
            for source, supplied in (("X-Tenant-Id header", x_tenant_id), ("tenantId parameter", tenantId)):
                if supplied and supplied.strip() and supplied.strip() != verified[1]:
                    logger.warning(
                        "[AUTH_DEP] REJECTED — %s does not match the tenant in the verified token", source
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="The supplied tenant does not match your authenticated tenant.",
                    )
        # The login is not a security boundary here (the token's wins, and a
        # differing URL value is only logged): hosts format it differently.
        if loginId and loginId.strip().lower() != verified[0].lower():
            logger.warning("[AUTH_DEP] query-string loginId differs from the verified token; using the token")
        ctx = RequestContext(login_id=verified[0], tenant_id=verified[1])
    else:
        ctx = RequestContext(login_id=loginId, tenant_id=tenantId)

    if not AUTH_ENABLED:
        # The bypass skips *authorisation*, but path resolution still needs a
        # tenant under 6.0. Fill it from DV_DEV_TENANT_ID so local work behaves,
        # and say so loudly rather than letting a later failure look unrelated.
        if profile.requires_tenant and not ctx.tenant_id:
            if not DEV_TENANT_ID:
                logger.error(
                    "[AUTH_DEP] AUTH_DISABLED and no tenantId supplied, but %s is "
                    "tenant-scoped and DV_DEV_TENANT_ID is unset",
                    profile.name,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"{profile.name} needs a tenant to locate its repository. "
                        "Auth is disabled (DV_AUTH_ENABLED=false) and no tenantId was "
                        "sent, so set DV_DEV_TENANT_ID in .env (e.g. 1001) or pass "
                        "?tenantId= on the request."
                    ),
                )
            ctx = RequestContext(login_id=ctx.login_id, tenant_id=DEV_TENANT_ID)
            logger.debug(
                "[AUTH_DEP] AUTH_DISABLED — assuming DV_DEV_TENANT_ID=%r | %s",
                DEV_TENANT_ID, ctx,
            )

        _announce_auth_disabled()
        logger.debug("[AUTH_DEP] AUTH_DISABLED — bypassing login validation | %s", ctx)
        # Authorisation is skipped, but a tenant the host does not know (or one with no
        # repository) used to surface as a 500 from a missing folder somewhere later.
        # Same fixed 403 as the authenticated path (code review L-13).
        try:
            profile.validate_context(ctx)
        except HostProfileError as exc:
            logger.warning("[AUTH_DEP] REJECTED (auth disabled) — %s | %s", exc, ctx)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=_GENERIC_TENANT_DENIED
            ) from exc
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
        # The profile's message can name every active tenant; it goes to the
        # log for operators and the client gets a fixed sentence (SEC-03).
        logger.warning("[AUTH_DEP] REJECTED — %s | %s", exc, ctx)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_GENERIC_TENANT_DENIED
        ) from exc

    allowed = get_allowed_form_ids(ctx)
    if allowed is None:
        logger.warning("[AUTH_DEP] REJECTED — login_id=%r not found in user master", ctx.login_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user is not authorised to use this application.",
        )

    logger.debug("[AUTH_DEP] %s authenticated | %d form(s) in allowed set", ctx, len(allowed))
    return ctx


def require_tables_access(ctx: RequestContext, tables: list[dict]) -> None:
    """Gate a set of retrieved tables before any SQL is generated or run.

    Same contract as the sql_agent's H-02 gate: every table is mapped to the
    return that owns it, and EVERY one of those returns must be in the caller's
    department allow-list (Forms + NXForms under 5.5, ReturnId + NXReturnId under
    6.0 -- get_allowed_form_ids already unions both and is host-agnostic).

    Fail closed: a table whose return cannot be determined is denied, never
    assumed allowed, and a request spanning an allowed and a disallowed return
    is denied in full rather than partially answered.
    """
    if not AUTH_ENABLED:
        _announce_auth_disabled()
        return

    allowed = get_allowed_form_ids(ctx)
    if allowed is None:
        logger.warning("[AUTH_DEP] TABLE ACCESS DENIED (user not found) | %s", ctx)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user is not authorised to use this application.",
        )

    return_ids = {str(t.get("return_id") or "").strip() for t in tables}
    if not tables or "" in return_ids:
        logger.warning(
            "[AUTH_DEP] TABLE ACCESS DENIED (return unresolved) | %s | tables=%s",
            ctx, [t.get("table") for t in tables],
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not determine which return this question refers to, "
                   "so access cannot be confirmed. Please name the return in your question.",
        )

    if not return_ids <= allowed:
        logger.warning(
            "[AUTH_DEP] TABLE ACCESS DENIED | %s | return_ids=%s | denied=%s",
            ctx, sorted(return_ids), sorted(return_ids - allowed),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You do not have access to this return. "
                "Contact your administrator to update your department's access list."
            ),
        )


def require_return_access(ctx: RequestContext, return_id: str) -> None:
    if not AUTH_ENABLED:
        _announce_auth_disabled()
        logger.debug("[AUTH_DEP] AUTH_DISABLED — bypassing return access validation")
        return

    if not is_return_allowed(ctx, return_id):
        logger.warning("[AUTH_DEP] ACCESS DENIED | %s | return_id=%r", ctx, return_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You do not have access to this return. "
                "Contact your administrator to update your department's access list."
            ),
        )
