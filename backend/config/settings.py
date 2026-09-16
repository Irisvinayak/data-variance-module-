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

# ── Per-instance overlay (running two host versions side by side) ─────────────
# The app is one-process-per-version by design: VERSION is read once at import
# time and every setting below (DB, paths, port, ...) is fixed for the life of
# the process. Serving 5.5 and 6.0 from the SAME process is not supported —
# whichever VERSION it started with silently answers requests meant for the
# other host's repo/DB.
#
# To run both at once, start two backend processes on two ports, each pointed
# at its own overlay .env file via a real OS environment variable named
# DV_ENV_FILE (set on the process launch, not inside a .env — it must exist
# before this module ever loads one). The overlay is loaded AFTER the base
# .env with override=True, so it only needs to carry the handful of keys that
# must differ per instance (VERSION, DV_SERVER_PORT, DV_API_BASE_PATH); every
# other setting (DB_*_55/_60, BASE_PATH_55/_60, ...) is already shared and
# version-keyed in the base .env. See .env.55.example / .env.60.example.
_ENV_OVERLAY = os.environ.get("DV_ENV_FILE", "").strip()
if _ENV_OVERLAY:
    load_dotenv(dotenv_path=_ENV_OVERLAY, override=True)


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
# Resolved per version, the same way BASE_PATH is below: 5.5's CIMS returns and
# 6.0's QCB returns live in different Oracle schemas (in this deployment,
# distinct DBs entirely — DV_DB_USER=crilc vs idealcrilc). Without a per-version
# split here, flipping VERSION alone leaves the OTHER host's DB connection in
# place, and every query 500s with ORA-00942 ("table or view does not exist")
# for a table that is perfectly real — just in the schema the wrong host is
# pointed at. That symptom looks like a data problem; it is a config one.
#
#   DV_DB_*      — explicit override, wins for either version
#   DV_DB_*_55   — used when VERSION is 5.5
#   DV_DB_*_60   — used when VERSION is 6.0
#
# A bare DV_DB_* (no suffix) is also honoured as an implicit "same DB for both"
# convenience for a single-database deployment, so an existing .env with only
# unsuffixed DV_DB_* keeps working unchanged.

def _db_setting(name: str, default: str) -> tuple[str, str, str]:
    """(resolved_for_current_version, value_55, value_60) for one DB_* setting."""
    override = os.getenv(f"DV_DB_{name}", "").strip()
    v55 = (os.getenv(f"DV_DB_{name}_55", "").strip() or override or default)
    v60 = (os.getenv(f"DV_DB_{name}_60", "").strip() or override or default)
    resolved = v55 if is_legacy_mode() else v60
    return resolved, v55, v60


DB_HOST, DB_HOST_55, DB_HOST_60 = _db_setting("HOST", "3.6.209.141")
_DB_PORT_S, _DB_PORT_55_S, _DB_PORT_60_S = _db_setting("PORT", "1521")
DB_PORT    : int = int(_DB_PORT_S)
DB_PORT_55 : int = int(_DB_PORT_55_S)
DB_PORT_60 : int = int(_DB_PORT_60_S)
DB_SERVICE, DB_SERVICE_55, DB_SERVICE_60 = _db_setting("SERVICE", "XE")
DB_USER, DB_USER_55, DB_USER_60 = _db_setting("USER", "SOUTHINDIANBANK")
DB_PASSWORD, DB_PASSWORD_55, DB_PASSWORD_60 = _db_setting("PASSWORD", "southindianbank1123")
_DB_MAXR_S, _DB_MAXR_55_S, _DB_MAXR_60_S = _db_setting("MAX_ROWS", "5000")
DB_MAX_ROWS    : int = int(_DB_MAXR_S)
DB_MAX_ROWS_55 : int = int(_DB_MAXR_55_S)
DB_MAX_ROWS_60 : int = int(_DB_MAXR_60_S)

# ── Base path ──────────────────────────────────────────────────────────────────
# Root of the iDEAL repository installation. In 5.5 this is the repo itself
# (D:\Repo5.5); in 6.0 it is the parent of the per-tenant folders (D:\Repo6).
# The profile decides how to build paths beneath it.
#
# Resolved per version so that flipping VERSION is the ONLY edit needed: both
# repositories can be declared once and the right one is picked automatically.
#   DV_BASE_PATH        — explicit override, wins for either version
#   DV_BASE_PATH_55     — used when VERSION is 5.5
#   DV_BASE_PATH_60     — used when VERSION is 6.0
# Without this, changing VERSION alone would leave 6.0 pointed at the 5.5 repo,
# where every lookup fails in a way that looks like a permissions problem.

# Each profile picks its own root from these, so a profile's base path always
# matches the profile rather than whatever VERSION happened to be at import
# time. BASE_PATH_OVERRIDE, when set, wins for either version.
BASE_PATH_OVERRIDE: str = os.getenv("DV_BASE_PATH", "").strip()
BASE_PATH_55: str = os.getenv("DV_BASE_PATH_55", "").strip() or r"D:\Repo5.5"
BASE_PATH_60: str = os.getenv("DV_BASE_PATH_60", "").strip() or r"D:\Repo6"

# The root for the CONFIGURED version. Convenience for logging and for callers
# that legitimately mean "this deployment's repository"; path resolution goes
# through the profile's own base_path, never this.
BASE_PATH: str = BASE_PATH_OVERRIDE or (BASE_PATH_55 if is_legacy_mode() else BASE_PATH_60)

# ── Table-data behaviour ───────────────────────────────────────────────────────
# Also per-version: the two hosts' Oracle schemas differ (CRILC vs IDEALCRILC
# in this deployment), and _resolve_physical_table_name prefixes every query
# with DP_TABLE_SCHEMA — a stale value here means every table name 6.0 builds
# is qualified with 5.5's schema (or vice versa), which is the same
# "looks like missing data, is actually a stale switch" failure as DB_HOST.
def _flag_versioned(name: str, default: str) -> tuple[bool, bool, bool]:
    override = os.getenv(f"DV_{name}", "").strip()
    v55 = os.getenv(f"DV_{name}_55", "").strip() or override or default
    v60 = os.getenv(f"DV_{name}_60", "").strip() or override or default
    truthy = lambda v: v.strip().lower() in {"1", "true", "yes", "on"}
    return (truthy(v55) if is_legacy_mode() else truthy(v60)), truthy(v55), truthy(v60)


IS_SP_TABLE_DATA_ENABLED, IS_SP_TABLE_DATA_ENABLED_55, IS_SP_TABLE_DATA_ENABLED_60 = (
    _flag_versioned("IS_SP_TABLE_DATA_ENABLED", "false")
)


def _schema_setting(default_55: str, default_60: str) -> tuple[str, str, str]:
    override = os.getenv("DV_DP_SCHEMA", "").strip()
    v55 = os.getenv("DV_DP_SCHEMA_55", "").strip() or override or default_55
    v60 = os.getenv("DV_DP_SCHEMA_60", "").strip() or override or default_60
    return (v55 if is_legacy_mode() else v60), v55, v60


DP_TABLE_SCHEMA, DP_TABLE_SCHEMA_55, DP_TABLE_SCHEMA_60 = _schema_setting("CRILC", "IDEALCRILC")

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

# Tenant assumed when auth is bypassed and the caller sent no tenantId.
#
# Only consulted while AUTH_ENABLED is false. Under 6.0 every repository path is
# rooted at a tenant, so "no auth" does not imply "no tenant" the way it does
# under 5.5 — without this, disabling auth for local work made every request
# fail on a missing tenantId, which is a confusing way to learn that the dev
# bypass does not cover path resolution. Ignored entirely when auth is on, so it
# can never widen access in a real deployment.
DEV_TENANT_ID: str = os.getenv("DV_DEV_TENANT_ID", "").strip()

# ── Per-version routing/server settings ────────────────────────────────────────
# Port, reverse-proxy prefix and CORS origins differ per host for the same
# reason the DB and repository root do: the two hosts are two separate IIS
# sites, reached under different virtual directories, and — when both are
# deployed on one box — answered by two backend processes that cannot share a
# port. Keying them the same way as DV_DB_*/DV_BASE_PATH means VERSION stays
# the only edit: flipping it moves the port, the root_path and the allowed
# origins together.
#
# It also makes the side-by-side overlay files (.env.55 / .env.60, selected
# with DV_ENV_FILE) trivial — an overlay that sets only VERSION now lands on
# the right port automatically, instead of having to restate it and risk two
# instances racing for the same socket.
#
#   DV_<NAME>      — explicit override, wins for either version
#   DV_<NAME>_55   — used when VERSION is 5.5
#   DV_<NAME>_60   — used when VERSION is 6.0
def _versioned(name: str, default_55: str, default_60: str) -> tuple[str, str, str]:
    """(resolved_for_current_version, value_55, value_60) for one DV_* setting."""
    override = os.getenv(f"DV_{name}", "").strip()
    v55 = os.getenv(f"DV_{name}_55", "").strip() or override or default_55
    v60 = os.getenv(f"DV_{name}_60", "").strip() or override or default_60
    return (v55 if is_legacy_mode() else v60), v55, v60


# ── API base path ──────────────────────────────────────────────────────────────
# Set when served behind a reverse proxy, so FastAPI generates correct URLs:
# /Datavariance/api under the 5.5 site, /DataVar6.0/api under the 6.0 site.
# Defaults stay empty so a direct-to-uvicorn deployment is unaffected.
API_BASE_PATH, API_BASE_PATH_55, API_BASE_PATH_60 = _versioned("API_BASE_PATH", "", "")

# ── Server settings ────────────────────────────────────────────────────────────
# 5.5 -> 8002, 6.0 -> 8003. Distinct by default so both can run at once on one
# box without configuration; 8000/8001 are avoided because they collide with
# other iDEAL services already on these servers.
SERVER_HOST: str = os.getenv("DV_SERVER_HOST", "0.0.0.0")

_PORT, _PORT_55, _PORT_60 = _versioned("SERVER_PORT", "8002", "8003")
SERVER_PORT: int = int(_PORT)
SERVER_PORT_55: int = int(_PORT_55)
SERVER_PORT_60: int = int(_PORT_60)

# ── CORS origins ───────────────────────────────────────────────────────────────
_DEFAULT_CORS = "http://localhost:5173,http://localhost:3001"
_CORS, _CORS_55, _CORS_60 = _versioned("CORS_ORIGINS", _DEFAULT_CORS, _DEFAULT_CORS)


def _origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


CORS_ORIGINS: list[str] = _origins(_CORS)
CORS_ORIGINS_55: list[str] = _origins(_CORS_55)
CORS_ORIGINS_60: list[str] = _origins(_CORS_60)
