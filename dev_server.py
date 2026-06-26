"""
dev_server.py — Stable FastAPI development server for Windows
(Data Variance API — backend.main:app)
"""

from __future__ import annotations

import os
import sys
import uvicorn

# -------------------------------------------------------------------
# Add project root to Python path
# -------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

if __name__ == "__main__":

    print("\n[DEV SERVER] Starting Data Variance FastAPI backend...")
    print("[DEV SERVER] Reload watching enabled")
    print("[DEV SERVER] Watching only: backend/")
    print("[DEV SERVER] Excluding logs, frontend, pycache, temp files\n")

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8002,

        # Auto reload
        reload=True,

        # ONLY watch backend folder
        reload_dirs=[
            os.path.join(ROOT_DIR, "backend")
        ],

        # IMPORTANT:
        # Keep this SMALL on Windows
        reload_excludes=[
            "logs",
            "frontend",
            "__pycache__",
            ".git",
            ".venv",
            "temp",
            "tmp",
        ],

        reload_delay=1.0,

        log_level="info",
    )