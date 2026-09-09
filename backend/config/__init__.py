# backend.config — the public configuration surface.
#
# Import settings and RequestContext from here. Deliberately does NOT re-export
# backend.hosts.get_profile: hosts/ imports backend.config.settings, so pulling
# hosts in here would create an import cycle through this package's __init__.
# Ask for a profile with `from ..hosts import get_profile` instead.

from __future__ import annotations

from .context import ANONYMOUS, RequestContext
from .settings import (
    API_BASE_PATH,
    APP_VERSION,
    AUTH_ENABLED,
    AUTH_TTL_SEC,
    BASE_PATH,
    BASE_PATH_55,
    BASE_PATH_60,
    BASE_PATH_OVERRIDE,
    CORS_ORIGINS,
    DB_HOST,
    DB_MAX_ROWS,
    DB_PASSWORD,
    DB_PORT,
    DB_SERVICE,
    DB_USER,
    DP_TABLE_SCHEMA,
    IS_SP_TABLE_DATA_ENABLED,
    PATH_OVERRIDES,
    SERVER_HOST,
    SERVER_PORT,
    is_legacy_mode,
    is_tenant_aware_mode,
)

__all__ = [
    "ANONYMOUS",
    "API_BASE_PATH",
    "APP_VERSION",
    "AUTH_ENABLED",
    "AUTH_TTL_SEC",
    "BASE_PATH",
    "BASE_PATH_55",
    "BASE_PATH_60",
    "BASE_PATH_OVERRIDE",
    "CORS_ORIGINS",
    "DB_HOST",
    "DB_MAX_ROWS",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_SERVICE",
    "DB_USER",
    "DP_TABLE_SCHEMA",
    "IS_SP_TABLE_DATA_ENABLED",
    "PATH_OVERRIDES",
    "RequestContext",
    "SERVER_HOST",
    "SERVER_PORT",
    "is_legacy_mode",
    "is_tenant_aware_mode",
]
