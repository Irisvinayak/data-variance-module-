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
BASE_PATH: str = os.getenv("DV_BASE_PATH", r"D:\Repo\Repo5.5 3\Repo5.5")

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

# ── Auth XML file paths ────────────────────────────────────────────────────────
# Used by auth_service.py to resolve:
#   loginId → DepartmentId   (XML_User.xml)
#   DeptId  → Forms/NXForms  (XML_Dept.xml)
#   RoleId  → CreateInstance (XML_RoleAccess.xml)
#
# These files live in the same Database folder as Returns.xml.
# Override with DV_XML_USER_PATH / DV_XML_DEPT_PATH / DV_XML_ROLE_ACCESS_PATH
# in your .env if they are in a different location.

XML_USER_PATH: str = os.getenv(
    "DV_XML_USER_PATH",
    os.path.join(BASE_PATH, r"Database\XML_User.xml"),
)

XML_DEPT_PATH: str = os.getenv(
    "DV_XML_DEPT_PATH",
    os.path.join(BASE_PATH, r"Database\XML_Dept.xml"),
)

XML_ROLE_ACCESS_PATH: str = os.getenv(
    "DV_XML_ROLE_ACCESS_PATH",
    os.path.join(BASE_PATH, r"Database\XML_RoleAccess.xml"),
)

# ── Server settings ────────────────────────────────────────────────────────────
SERVER_HOST : str = os.getenv("DV_SERVER_HOST", "0.0.0.0")
SERVER_PORT : int = int(os.getenv("DV_SERVER_PORT", "8002"))

CORS_ORIGINS : list[str] = [
    o.strip()
    for o in os.getenv("DV_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001").split(",")
    if o.strip()
]