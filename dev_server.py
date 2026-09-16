"""
dev_server.py — FastAPI server launcher for Windows
(Data Variance API — backend.main:app)

Host, port and reverse-proxy prefix all come from the root .env via
backend.config, so VERSION is the only switch: 5.5 starts on 8002, 6.0 on
8003. Nothing about the port is decided here.
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

from backend.config import APP_VERSION, SERVER_HOST, SERVER_PORT

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

if __name__ == "__main__":

    # Reload runs a watcher process that spawns the real server as a CHILD.
    # Under Task Scheduler only the watcher is the tracked process, so
    # stopping the task can leave the child alive still holding the port —
    # which then looks like "the backend restarts itself" and blocks the next
    # start with WinError 10048. Off by default so a scheduled task is one
    # process that stops cleanly; opt in for interactive work with
    #   DEV_SERVER_RELOAD=1 python dev_server.py
    reload_enabled = os.getenv("DEV_SERVER_RELOAD", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }

    print("\n[DEV SERVER] Starting Data Variance FastAPI backend...")
    print(f"[DEV SERVER] VERSION={APP_VERSION} -> http://{SERVER_HOST}:{SERVER_PORT}")
    print(f"[DEV SERVER] Reload watching {'enabled' if reload_enabled else 'disabled'}")

    options = {
        "host": SERVER_HOST,
        "port": SERVER_PORT,
        "log_level": "info",
    }

    if reload_enabled:
        print("[DEV SERVER] Watching only: backend/")
        options.update(
            reload=True,
            # ONLY watch backend/, and keep the exclude list SMALL on Windows.
            reload_dirs=[os.path.join(ROOT_DIR, "backend")],
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
        )

    print()
    uvicorn.run("backend.main:app", **options)
