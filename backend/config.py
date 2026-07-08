from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

# ── Oracle DB ──────────────────────────────────────────────────────────────────
DB_HOST     : str  = os.getenv("DV_DB_HOST",     "3.6.209.141")
DB_PORT     : int  = int(os.getenv("DV_DB_PORT", "1521"))
DB_SERVICE  : str  = os.getenv("DV_DB_SERVICE",  "XE")
DB_USER     : str  = os.getenv("DV_DB_USER",     "SOUTHINDIANBANK")
DB_PASSWORD : str  = os.getenv("DV_DB_PASSWORD", "southindianbank1123")
DB_MAX_ROWS : int  = int(os.getenv("DV_DB_MAX_ROWS", "5000"))

# ── Base path ──────────────────────────────────────────────────────────────────

def _resolve_base_path(explicit_path: str | None) -> str:
    candidates: list[str] = []
    if explicit_path:
        candidates.append(explicit_path)

    candidates.extend(
        [
            r"D:\Repo6\1001",
            r"D:\Repo6\1000",
            r"D:\Repo6\1002",
            r"D:\project_work\iDEAL Banking 6.0 - Alpha\Repo6\1001",
            r"D:\Repo\Repo5.5 3\Repo5.5",
            r"D:\Repo\Repo5.5 3\Repo5.5\1001",
        ]
    )

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isdir(candidate):
            for subdir in ("Database", "DataBase"):
                if os.path.exists(os.path.join(candidate, subdir, "Return.xml")):
                    return candidate

    for candidate in candidates:
        if not candidate or not os.path.isdir(candidate):
            continue
        for root, _, files in os.walk(candidate):
            if "Return.xml" in files:
                return os.path.dirname(root)

    return explicit_path or r"D:\Repo6"


BASE_PATH: str = _resolve_base_path(os.getenv("DV_BASE_PATH"))

# ── Global XML paths (not tenant-specific) ─────────────────────────────────────
XML_TENANT_PATH: str = os.getenv(
    "DV_XML_TENANT_PATH",
    os.path.join(BASE_PATH, "XML_Tenant.xml"),
)
XML_ROLE_ACCESS_PATH: str = os.getenv(
    "DV_XML_ROLE_ACCESS_PATH",
    os.path.join(BASE_PATH, "Database", "XML_RoleAccess.xml"),
)

# ── Global / fallback XML paths ────────────────────────────────────────────────
# These are used when no tenant_id is available.
# Tenant-specific paths are resolved at runtime via get_tenant_*() helpers below.
RETURNS_XML_PATH: str = os.getenv(
    "DV_RETURNS_XML_PATH",
    os.path.join(BASE_PATH, "Database", "Return.xml"),
)
NON_XBRL_RETURNS_XML_PATH: str = os.getenv(
    "DV_NON_XBRL_RETURNS_XML_PATH",
    os.path.join(BASE_PATH, "Database", "NonXBRLReturn.xml"),
)
TABLE_MAPPING_BASE_DIR: str = os.getenv(
    "DV_TABLE_MAPPING_BASE_DIR",
    os.path.join(BASE_PATH, "Database"),
)
INSTANCE_BASE_DIR: str = os.getenv(
    "DV_INSTANCE_BASE_DIR",
    os.path.join(BASE_PATH, "Instance"),
)

IS_SP_TABLE_DATA_ENABLED: bool = os.getenv(
    "DV_IS_SP_TABLE_DATA_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}

DP_TABLE_SCHEMA: str = os.getenv("DV_DP_SCHEMA", "CRILC").strip()

XML_DEPT_PATH: str = os.getenv(
    "DV_XML_DEPT_PATH",
    os.path.join(BASE_PATH, r"Database\XML_Dept.xml"),
)

# ── API base path ────────────────────────────────────────────────────────────
# Set DV_API_BASE_PATH=/Datavariance/api when the app is served behind a reverse proxy.
API_BASE_PATH: str = os.getenv("DV_API_BASE_PATH", "").strip()

# ── Application mode ───────────────────────────────────────────────────────────
# Set VERSION to 5.5 for legacy non-tenant mode or 6.0 for tenant-aware
# React + .NET API mode. The app also accepts DV_APP_VERSION as a fallback alias.

def _normalize_app_version(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value:
        return "6.0"
    if value.startswith("5"):
        return "5.5"
    if value.startswith("6"):
        return "6.0"
    return value


APP_VERSION: str = _normalize_app_version(
    os.getenv("VERSION") or os.getenv("DV_APP_VERSION") or os.getenv("APP_VERSION")
)


def is_legacy_mode(version: str | None = None) -> bool:
    resolved = _normalize_app_version(version or APP_VERSION)
    return resolved.startswith("5")


def is_tenant_aware_mode(version: str | None = None) -> bool:
    return not is_legacy_mode(version)

# ── Server settings ────────────────────────────────────────────────────────────
SERVER_HOST : str = os.getenv("DV_SERVER_HOST", "0.0.0.0")
SERVER_PORT : int = int(os.getenv("DV_SERVER_PORT", "8000"))

# ── Authentication toggle ───────────────────────────────────────────────────
# Set DV_AUTH_ENABLED=true to require loginId/auth checks.
# Set DV_AUTH_ENABLED=false to bypass auth for local/dev testing.
AUTH_ENABLED: bool = os.getenv("DV_AUTH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# ── CORS origins ─────────────────────────────────────────────────────────────
# Browser origins allowed to access this API. Configure with DV_CORS_ORIGINS in
# the root .env file. For local dev this typically includes the Vite app origin.
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv(
        "DV_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001"
    ).split(",")
    if o.strip()
]


# ── Tenant-specific path helpers ───────────────────────────────────────────────
# All per-tenant XML files live at:
#   BASE_PATH / <TenantId> / Database / <filename>

def get_tenant_db_dir(tenant_id: str) -> str:
    return os.path.join(BASE_PATH, str(tenant_id), "Database")

def get_tenant_returns_xml_path(tenant_id: str) -> str:
    return os.path.join(get_tenant_db_dir(tenant_id), "Return.xml")

def get_tenant_non_xbrl_returns_xml_path(tenant_id: str) -> str:
    return os.path.join(get_tenant_db_dir(tenant_id), "NonXBRLReturn.xml")

def get_tenant_table_mapping_base_dir(tenant_id: str) -> str:
    return get_tenant_db_dir(tenant_id)

def get_tenant_user_xml_path(tenant_id: str) -> str:
    return os.path.join(get_tenant_db_dir(tenant_id), "User.xml")

def get_tenant_department_xml_path(tenant_id: str) -> str:
    return os.path.join(get_tenant_db_dir(tenant_id), "Department.xml")