# config.py — Standalone configuration for the Data Variance application.
# All file paths and DB credentials are read from environment variables.
# Override any value by setting the variable in your shell or .env file.

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the project root (one level above this file's directory).
# override=True ensures .env values always win over OS-level environment variables.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

# ── Oracle DB settings ─────────────────────────────────────────────────────────
DB_HOST     : str = os.getenv("DV_DB_HOST",     "3.6.209.141")
DB_PORT     : int = int(os.getenv("DV_DB_PORT", "1521"))
DB_SERVICE  : str = os.getenv("DV_DB_SERVICE",  "XE")
DB_USER     : str = os.getenv("DV_DB_USER",     "SOUTHINDIANBANK")
DB_PASSWORD : str = os.getenv("DV_DB_PASSWORD", "southindianbank1123")
DB_MAX_ROWS : int = int(os.getenv("DV_DB_MAX_ROWS", "5000"))

# ── Base path ──────────────────────────────────────────────────────────────────
# Root directory of the Repo5.5 installation. All XML/instance paths below
# are resolved relative to this. Override with DV_BASE_PATH in your .env.
BASE_PATH: str = os.getenv("DV_BASE_PATH", r"D:\Repo6")
    
# ── Tenant XML ─────────────────────────────────────────────────────────────────
# Global tenant registry — lives directly under BASE_PATH.
# Per-tenant files (user.xml, department.xml) are resolved at runtime from:
#   BASE_PATH / <TenantId> / Database / user.xml
#   BASE_PATH / <TenantId> / Database / department.xml
XML_TENANT_PATH: str = os.getenv(
    "DV_XML_TENANT_PATH",
    os.path.join(BASE_PATH, "XML_Tenant.xml"),
)

# ── Role-access XML (global — shared across all tenants) ──────────────────────
XML_ROLE_ACCESS_PATH: str = os.getenv(
    "DV_XML_ROLE_ACCESS_PATH",
    os.path.join(BASE_PATH, r"Database\XML_RoleAccess.xml"),
)

# ── Variance XML file paths ────────────────────────────────────────────────────
RETURNS_XML_PATH: str = os.getenv(
    "DV_RETURNS_XML_PATH",
    os.path.join(BASE_PATH, r"Database\Returns.xml"),
)

IS_SP_TABLE_DATA_ENABLED: bool = os.getenv(
    "DV_IS_SP_TABLE_DATA_ENABLED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

DP_TABLE_SCHEMA: str = os.getenv("DV_DP_SCHEMA", "CRILC").strip()

NON_XBRL_RETURNS_XML_PATH: str = os.getenv(
    "DV_NON_XBRL_RETURNS_XML_PATH",
    os.path.join(BASE_PATH, r"Database\NonXBRLReturns.xml"),
)

TABLE_MAPPING_BASE_DIR: str = os.getenv(
    "DV_TABLE_MAPPING_BASE_DIR",
    os.path.join(BASE_PATH, "Database"),
)

INSTANCE_BASE_DIR: str = os.getenv(
    "DV_INSTANCE_BASE_DIR",
    os.path.join(BASE_PATH, "Instance"),
)

# ── Server settings ────────────────────────────────────────────────────────────
SERVER_HOST : str = os.getenv("DV_SERVER_HOST", "0.0.0.0")
SERVER_PORT : int = int(os.getenv("DV_SERVER_PORT", "8000"))

CORS_ORIGINS : list[str] = [
    o.strip()
    for o in os.getenv("DV_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001").split(",")
    if o.strip()
]