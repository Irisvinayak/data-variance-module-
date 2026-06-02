# config.py — Standalone configuration for the Data Variance application.
# All file paths and DB credentials are read from environment variables.
# Override any value by setting the variable in your shell or .env file.

from __future__ import annotations

import os
    
# ── Oracle DB settings ─────────────────────────────────────────────────────────
DB_HOST     : str = os.getenv("DV_DB_HOST",     "localhost")
DB_PORT     : int = int(os.getenv("DV_DB_PORT", "1521"))
DB_SERVICE  : str = os.getenv("DV_DB_SERVICE",  "ORCLCDB")
DB_USER     : str = os.getenv("DV_DB_USER",     "system")
DB_PASSWORD : str = os.getenv("DV_DB_PASSWORD", "")
DB_MAX_ROWS : int = int(os.getenv("DV_DB_MAX_ROWS", "5000"))

# ── XML file paths ─────────────────────────────────────────────────────────────
RETURNS_XML_PATH: str = os.getenv(
    "DV_RETURNS_XML_PATH",
    r"D:\Repo\Repo5.5 3\Repo5.5\Database\Returns.xml",
)

# Base directory that contains per-return table mapping XML files.
# Structure: {TABLE_MAPPING_BASE_DIR}\{return_id}\{TblPath}
TABLE_MAPPING_BASE_DIR: str = os.getenv(
    "DV_TABLE_MAPPING_BASE_DIR",
    r"D:\Repo\Repo5.5 3\Repo5.5\Database",
)

# Base directory for instance XML files.
INSTANCE_BASE_DIR: str = os.getenv(
    "DV_INSTANCE_BASE_DIR",
    r"D:\Repo\Repo5.5 3\Repo5.5\Instance",
)

# ── Server settings ─────────────────────────────────────────────────────────────
SERVER_HOST : str = os.getenv("DV_SERVER_HOST", "0.0.0.0")
SERVER_PORT : int = int(os.getenv("DV_SERVER_PORT", "8002"))

# Comma-separated list of allowed CORS origins.
CORS_ORIGINS : list[str] = [
    o.strip()
    for o in os.getenv("DV_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001").split(",")
    if o.strip()
]
