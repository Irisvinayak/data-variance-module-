# settings.py — environment-derived configuration for the Data Variance app.
#
# This module reads .env and OS environment ONLY. It deliberately does NOT
# touch the disk: no os.path.isdir probing, no walking a repo looking for
# Return.xml. An earlier attempt (branch 3-intigreating-60-55) resolved
# BASE_PATH by scanning a hardcoded list of developer machine paths, which
# meant the app's behaviour depended on which folders happened to exist on the
# box. Configuration is declared here; where a given file lives for a given
# host version is answered by backend/hosts/ instead.
#
# Everything host-specific (which XML filename, which attribute name, which
# folder layout) belongs in a HostProfile — see backend/hosts/base.py.

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the project root (two levels above this file: backend/config/ -> backend/ -> root).
# override=True ensures .env values always win over OS-level environment variables.
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    override=True,
)


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# ── Application mode ───────────────────────────────────────────────────────────
# VERSION selects which iDEAL host this deployment is embedded in:
#   5.5 -> ASP.NET MVC, single-tenant repo   (D:\Repo5.5)
#   6.0 -> React + .NET API, multi-tenant    (D:\Repo6\<TenantId>)
# DV_APP_VERSION / APP_VERSION are accepted as aliases.

def _normalize_app_version(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return "5.5"          # default to the version currently in production
    if value.startswith("5"):
        return "5.5"
    if value.startswith("6"):
        return "6.0"
    return value


APP_VERSION: str = _normalize_app_version(
    os.getenv("VERSION") or os.getenv("DV_APP_VERSION") or os.getenv("APP_VERSION")
)


def is_legacy_mode(version: str | None = None) -> bool:
    """True for iDEAL 5.5 (single-tenant MVC host)."""
    return _normalize_app_version(version or APP_VERSION).startswith("5")


def is_tenant_aware_mode(version: str | None = None) -> bool:
    """True for iDEAL 6.0 (multi-tenant React + .NET API host)."""
    return not is_legacy_mode(version)


# ── Oracle DB settings ─────────────────────────────────────────────────────────
DB_HOST     : str = os.getenv("DV_DB_HOST",     "3.6.209.141")
DB_PORT     : int = int(os.getenv("DV_DB_PORT", "1521"))
DB_SERVICE  : str = os.getenv("DV_DB_SERVICE",  "XE")
DB_USER     : str = os.getenv("DV_DB_USER",     "SOUTHINDIANBANK")
DB_PASSWORD : str = os.getenv("DV_DB_PASSWORD", "southindianbank1123")
DB_MAX_ROWS : int = int(os.getenv("DV_DB_MAX_ROWS", "5000"))

# ── Base path ──────────────────────────────────────────────────────────────────
# Root of the iDEAL repository installation. In 5.5 this is the repo itself
# (D:\Repo5.5); in 6.0 it is the parent of the per-tenant folders (D:\Repo6).
# The profile decides how to build paths beneath it.
BASE_PATH: str = os.getenv("DV_BASE_PATH", r"D:\Repo5.5")

# ── Table-data behaviour ───────────────────────────────────────────────────────
IS_SP_TABLE_DATA_ENABLED: bool = _flag("DV_IS_SP_TABLE_DATA_ENABLED")
DP_TABLE_SCHEMA: str = os.getenv("DV_DP_SCHEMA", "CRILC").strip()

# ── Explicit path overrides ────────────────────────────────────────────────────
# Empty string means "not overridden — let the host profile derive it from
# BASE_PATH". These exist so a deployment with a non-standard layout can pin an
# individual file without having to write a new profile. Profiles read these via
# backend.hosts.base.HostProfile._override().
PATH_OVERRIDES: dict[str, str] = {
    "returns_xml":           os.getenv("DV_RETURNS_XML_PATH", "").strip(),
    "non_xbrl_returns_xml":  os.getenv("DV_NON_XBRL_RETURNS_XML_PATH", "").strip(),
    "table_mapping_base":    os.getenv("DV_TABLE_MAPPING_BASE_DIR", "").strip(),
    "instance_base":         os.getenv("DV_INSTANCE_BASE_DIR", "").strip(),
    "user_xml":              os.getenv("DV_XML_USER_PATH", "").strip(),
    "department_xml":        os.getenv("DV_XML_DEPT_PATH", "").strip(),
    "role_access_xml":       os.getenv("DV_XML_ROLE_ACCESS_PATH", "").strip(),
    "period_xml":            os.getenv("DV_XML_PERIOD_PATH", "").strip(),
    "tenant_xml":            os.getenv("DV_XML_TENANT_PATH", "").strip(),
}

# ── Auth ───────────────────────────────────────────────────────────────────────
# DV_AUTH_ENABLED=false bypasses login/return-access checks for local dev.
# NEVER false in production.
AUTH_ENABLED: bool = _flag("DV_AUTH_ENABLED", "true")

# TTL for the resolved login -> allowed-returns cache.
AUTH_TTL_SEC: float = float(os.getenv("AUTH_TTL_SEC", "3600"))

# ── API base path ──────────────────────────────────────────────────────────────
# Set DV_API_BASE_PATH=/Datavariance/api when served behind a reverse proxy.
API_BASE_PATH: str = os.getenv("DV_API_BASE_PATH", "").strip()

# ── Server settings ────────────────────────────────────────────────────────────
SERVER_HOST : str = os.getenv("DV_SERVER_HOST", "0.0.0.0")
SERVER_PORT : int = int(os.getenv("DV_SERVER_PORT", "8000"))

# ── CORS origins ───────────────────────────────────────────────────────────────
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv(
        "DV_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001"
    ).split(",")
    if o.strip()
]
