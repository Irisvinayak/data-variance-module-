# security_posture.py - say, once at start-up, how much of the identity/authorisation
# protection is actually switched on (code review M-04, H-01, C-01).
#
# Every protection defaults to its OLD behaviour so existing servers keep working;
# the cost of that is that an unsafe setting is easy to leave on without noticing.
# This module turns each unsafe combination into one clear WARNING line in the log,
# and lets an operator opt in to refusing to start (DV_REQUIRE_AUTH=true) once the
# server has been moved to the safe settings.

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger("backend.security")


def llm_url_risk(url: str, is_default: bool = False) -> str | None:
    """Why `url` is a poor place to send users' questions, or None when it looks fine.

    Questions (which can contain business data) go to this endpoint. Plain http to an
    address that is not private/loopback sends them across the network unencrypted
    (code review H-07)."""
    if is_default:
        return ("DV_NLP_OLLAMA_URL is not set: the built-in default (a public IP over plain http) "
                "is in use. Set it to your own endpoint.")
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if not host:
        return "DV_NLP_OLLAMA_URL has no host."
    if parsed.scheme == "https":
        return None
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"DV_NLP_OLLAMA_URL uses plain http to {host!r}; prefer https or a private network."
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return None
    return f"DV_NLP_OLLAMA_URL sends questions in clear text (http) to the public address {host}."


def posture_warnings(
    *,
    auth_enabled: bool,
    identity_mode: str,
    identity_mode_invalid: bool,
    jwt_secret_set: bool,
    llm_url: str | None = None,
    llm_url_is_default: bool = False,
) -> list[str]:
    """Human-readable problems with the current security settings ([] when sound)."""
    out: list[str] = []
    if not auth_enabled:
        out.append(
            "DV_AUTH_ENABLED=false: ALL authorisation is off. Any caller who can reach this "
            "port is treated as whoever the query string names, and every return is open."
        )
    if identity_mode_invalid:
        out.append(
            "DV_IDENTITY_MODE has an unrecognised value and was treated as 'off'. "
            "Valid values: off, optional, required."
        )
    if identity_mode == "off":
        out.append(
            "DV_IDENTITY_MODE=off: the caller's login is taken from the URL and is not verified, "
            "so /auth/my-returns and every return check can be asked about any login."
        )
    elif not jwt_secret_set:
        out.append(
            f"DV_IDENTITY_MODE={identity_mode} but DV_JWT_SECRET is empty: every token will be "
            "refused (fail closed)"
            + (", so every request is rejected." if identity_mode == "required" else ".")
        )
    if llm_url is not None:
        risk = llm_url_risk(llm_url, llm_url_is_default)
        if risk:
            out.append(risk)
    return out


def log_security_posture() -> list[str]:
    """Log the current posture; raise when DV_REQUIRE_AUTH=true and auth is off."""
    from .config import settings as cfg
    from .nlp import nlp_config

    warnings = posture_warnings(
        auth_enabled=cfg.AUTH_ENABLED,
        identity_mode=cfg.IDENTITY_MODE,
        identity_mode_invalid=cfg.IDENTITY_MODE_INVALID,
        jwt_secret_set=bool(cfg.JWT_SECRET),
        llm_url=nlp_config.OLLAMA_URL,
        llm_url_is_default=nlp_config.OLLAMA_URL_IS_DEFAULT,
    )
    # Booleans only: the secret itself is never logged.
    logger.info(
        "[security] posture | auth_enabled=%s | identity_mode=%s | jwt_secret_set=%s | require_auth=%s",
        cfg.AUTH_ENABLED, cfg.IDENTITY_MODE, bool(cfg.JWT_SECRET), cfg.REQUIRE_AUTH,
    )
    for w in warnings:
        logger.warning("[security] %s", w)
    if cfg.REQUIRE_AUTH and not cfg.AUTH_ENABLED:
        raise RuntimeError("DV_REQUIRE_AUTH=true but DV_AUTH_ENABLED=false - refusing to start.")
    if cfg.REQUIRE_EXPLICIT_LLM_URL and nlp_config.OLLAMA_URL_IS_DEFAULT:
        raise RuntimeError("DV_REQUIRE_EXPLICIT_LLM_URL=true but DV_NLP_OLLAMA_URL is not set - refusing to start.")
    return warnings
