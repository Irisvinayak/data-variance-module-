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

from backend.config import SERVER_HOST, SERVER_PORT

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

if __name__ == "__main__":

    # Reload spawns a separate reloader process + child server process.
    # That's convenient for interactive dev (edit-and-reload) but breaks
    # process lifecycle under Task Scheduler / service hosting: only the
    # reloader is the tracked process, so stopping the task can leave the
    # child server running and holding port 8000.
    #
    # Default: reload OFF (single process, clean start/stop — Task Scheduler).
    # Opt in for local dev with:  DEV_SERVER_RELOAD=1 python dev_server.py
    reload_enabled = os.getenv("DEV_SERVER_RELOAD", "0").strip().lower() in {"1", "true", "yes", "on"}

    print("\n[DEV SERVER] Starting Data Variance FastAPI backend...")
    print(f"[DEV SERVER] Reload watching {'enabled' if reload_enabled else 'disabled'}")
    if reload_enabled:
        print("[DEV SERVER] Watching only: backend/")
        print("[DEV SERVER] Excluding logs, frontend, pycache, temp files\n")

    uvicorn.run(
        "backend.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,

        reload=reload_enabled,

        # ONLY watch backend folder (only relevant when reload_enabled)
        reload_dirs=[
            os.path.join(ROOT_DIR, "backend")
        ] if reload_enabled else None,

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
        ] if reload_enabled else None,

        reload_delay=1.0 if reload_enabled else None,

        log_level="info",
    )