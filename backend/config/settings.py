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
# version-keyed in the base .env. See config/env/.env.55.example / config/env/.env.60.example.
_ENV_OVERLAY = os.environ.get("DV_ENV_FILE", "").strip()
if _ENV_OVERLAY:
    load_dotenv(dotenv_path=_ENV_OVERLAY, override=True)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "false") -> bool:
    return _truthy(os.getenv(name, default))


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


# ── Oracle DB settings ─────────────────────────────────────────────────────────
# Resolved per version, the same way BASE_PATH is below: 5.5's CIMS returns and
# 6.0's QCB returns live in different Oracle schemas (in this deployment,
# distinct DBs entirely — DV_DB_USER=crilc vs idealcrilc). Without a per-version
# split here, flipping VERSION alone leaves the OTHER host's DB connection in
# place, and every query 500s with ORA-00942 ("table or view does not exist")
# for a table that is perfectly real — just in the schema the wrong host is
# pointed at. That symptom looks like a data problem; it is a config one.
#
#   DV_DB_*_55   — used when VERSION is 5.5
#   DV_DB_*_60   — used when VERSION is 6.0
#   DV_DB_*      — fallback for either version when the suffixed key is unset,
#                  so a single-database .env with only unsuffixed keys works.

def _versioned(name: str, default_55: str, default_60: str | None = None) -> str:
    """The DV_<name> setting for the configured VERSION.

    Precedence: DV_<name>_55 / DV_<name>_60 (whichever matches VERSION), then
    the unsuffixed DV_<name> (an explicit "same value for both hosts"), then
    the default for that version.
    """
    override = os.getenv(f"DV_{name}", "").strip()
    suffix, default = ("55", default_55) if is_legacy_mode() else (
        "60", default_55 if default_60 is None else default_60
    )
    return os.getenv(f"DV_{name}_{suffix}", "").strip() or override or default


# No defaults for host or credentials: a missing value must fail loudly at the
# first query ("database is not configured") rather than silently connecting
# somewhere a developer's machine happened to point once.
DB_HOST: str = _versioned("DB_HOST", "")
DB_PORT: int = int(_versioned("DB_PORT", "1521"))
DB_SERVICE: str = _versioned("DB_SERVICE", "XE")
DB_USER: str = _versioned("DB_USER", "")
DB_PASSWORD: str = _versioned("DB_PASSWORD", "")
DB_MAX_ROWS: int = int(_versioned("DB_MAX_ROWS", "5000"))

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

# Month (1-12) the fiscal year starts in, used to read "FY25" / "Q1FY25" in
# natural-language queries. Empty -> the host profile's default (5.5: April,
# Indian FY; 6.0: January, QCB reports on the calendar year).
_fy_raw = os.getenv("DV_FISCAL_YEAR_START_MONTH", "").strip()
FISCAL_YEAR_START_MONTH_OVERRIDE: int | None = (
    int(_fy_raw) if _fy_raw.isdigit() and 1 <= int(_fy_raw) <= 12 else None
)

# ── Table-data behaviour ───────────────────────────────────────────────────────
# Also per-version: the two hosts' Oracle schemas differ (CRILC vs IDEALCRILC
# in this deployment), and resolve_physical_table_name prefixes every query
# with DP_TABLE_SCHEMA — a stale value here means every table name 6.0 builds
# is qualified with 5.5's schema (or vice versa), which is the same
# "looks like missing data, is actually a stale switch" failure as DB_HOST.
IS_SP_TABLE_DATA_ENABLED: bool = _truthy(_versioned("IS_SP_TABLE_DATA_ENABLED", "false"))
DP_TABLE_SCHEMA: str = _versioned("DP_SCHEMA", "CRILC", "IDEALCRILC")

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
# Keyed with _versioned() above, like DV_DB_* and DV_BASE_PATH_*.

# ── API base path ──────────────────────────────────────────────────────────────
# Set when served behind a reverse proxy, so FastAPI generates correct URLs:
# /Datavariance/api under the 5.5 site, /DataVar6.0/api under the 6.0 site.
# Defaults stay empty so a direct-to-uvicorn deployment is unaffected.
API_BASE_PATH: str = _versioned("API_BASE_PATH", "")

# ── Server settings ────────────────────────────────────────────────────────────
# 5.5 -> 8002, 6.0 -> 8003. Distinct by default so both can run at once on one
# box without configuration; 8000/8001 are avoided because they collide with
# other iDEAL services already on these servers.
SERVER_HOST: str = os.getenv("DV_SERVER_HOST", "0.0.0.0")

SERVER_PORT: int = int(_versioned("SERVER_PORT", "8002", "8003"))

# ── CORS origins ───────────────────────────────────────────────────────────────
_DEFAULT_CORS = "http://localhost:5173,http://localhost:3001"
CORS_ORIGINS: list[str] = [
    o.strip() for o in _versioned("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]
