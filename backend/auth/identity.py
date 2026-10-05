# identity.py — verify the host's signed JWT and derive identity from its claims.
#
# Why this exists: identity used to be whatever ?loginId=&tenantId= said (code
# review C-01 / SEC-01). The iDEAL 6.0 host signs a JWT (HMAC, claims LoginId and
# TenantId); verifying it here lets the backend trust a *signed* identity rather
# than an editable URL.
#
# Standard library only (hmac/hashlib/base64/json): the algorithm set is tiny,
# and writing it out keeps the security-critical rules visible in one place:
#   * the allowed algorithms are an explicit allow-list — "none" and any
#     algorithm not configured is rejected, so a token cannot choose its own
#     verification method;
#   * the signature is compared in constant time;
#   * exp is REQUIRED (a token that never expires is rejected);
#   * issuer / audience are checked whenever they are configured;
#   * every failure raises TokenError and the caller answers a generic 401 —
#     the reason goes to the log, never to the client.
#
# What this cannot do: the 6.0 host also enforces a single active session per
# user through an in-memory cache inside the .NET process. That check cannot be
# reproduced here, so a token that was superseded by a newer login stays valid
# until it expires. That is a host-side property, not something to guess at.

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HASHES = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


class TokenError(Exception):
    """The token is missing, malformed, expired, mis-signed or for another audience.

    str(exc) is for the LOG only; the HTTP layer never forwards it.
    """


@dataclass(frozen=True)
class VerifiedIdentity:
    login_id: str
    tenant_id: str
    claims: dict


def _b64url_decode(part: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenError("invalid base64url segment") from exc


def _json_segment(part: str, what: str) -> dict:
    try:
        value = json.loads(_b64url_decode(part))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenError(f"{what} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise TokenError(f"{what} is not a JSON object")
    return value


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    """The token from an `Authorization: Bearer <token>` header, else None."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def verify_jwt(
    token: str,
    *,
    secret: str,
    algorithms: tuple[str, ...],
    issuer: str = "",
    audience: str = "",
    leeway: int = 300,
    now: Optional[float] = None,
) -> dict:
    """Return the verified claims, or raise TokenError."""
    if not secret:
        raise TokenError("no signing secret configured (DV_JWT_SECRET)")
    allowed = [a for a in algorithms if a in _HASHES]
    if not allowed:
        raise TokenError("no supported algorithm configured")

    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenError("not a three-part JWT")
    header_b64, payload_b64, signature_b64 = parts

    header = _json_segment(header_b64, "header")
    alg = str(header.get("alg", "")).upper()
    if alg not in allowed:
        raise TokenError(f"algorithm {alg or '<missing>'!r} is not allowed")

    # A real JWT segment is base64url (ASCII). Anything else is a malformed
    # token and must be a TokenError (-> 401), not an unhandled UnicodeEncodeError
    # (-> 500).
    try:
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    except UnicodeEncodeError as exc:
        raise TokenError("token contains non-ASCII characters") from exc
    expected = hmac.new(secret.encode("utf-8"), signing_input, _HASHES[alg]).digest()
    if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
        raise TokenError("signature mismatch")

    claims = _json_segment(payload_b64, "payload")
    ts = time.time() if now is None else now

    exp = claims.get("exp")
    # json.loads accepts NaN/Infinity; with NaN every comparison below is False,
    # so a signed token carrying exp=NaN would never "expire".
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        raise TokenError("exp claim missing or not a finite number")
    if ts > exp + leeway:
        raise TokenError("token expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and not isinstance(nbf, bool) and ts + leeway < nbf:
        raise TokenError("token not yet valid")

    if issuer and claims.get("iss") != issuer:
        raise TokenError("issuer mismatch")
    if audience:
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if audience not in auds:
            raise TokenError("audience mismatch")
    return claims


def identity_from_claims(
    claims: dict, *, login_claim: str, tenant_claim: str, requires_tenant: bool
) -> VerifiedIdentity:
    login = claims.get(login_claim)
    login = str(login).strip() if login is not None else ""
    if not login:
        raise TokenError(f"token has no {login_claim} claim")
    tenant_raw: Any = claims.get(tenant_claim)
    tenant = str(tenant_raw).strip() if tenant_raw is not None else ""
    if requires_tenant and not tenant:
        raise TokenError(f"token has no {tenant_claim} claim, which this host requires")
    return VerifiedIdentity(login_id=login, tenant_id=tenant, claims=claims)
