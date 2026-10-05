"""
dev_server.py — FastAPI server launcher for Windows
(Data Variance API — backend.main:app)

Host, port and reverse-proxy prefix all come from the root .env via
backend.config, so VERSION is the only switch: 5.5 starts on 8004, 6.0 on
8003. Nothing about the port is decided here.
"""

from __future__ import annotations

import logging
import os
import sys
import uvicorn

# -------------------------------------------------------------------
# Add project root to Python path
# -------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.config import APP_VERSION, SERVER_HOST, SERVER_PORT  # noqa: E402 - needs ROOT_DIR on sys.path
from backend.config.settings import AUTH_ENABLED, is_loopback_host  # noqa: E402


def _warn_insecure_bind() -> bool:
    """Warn loudly when auth is off AND the port is network-reachable.

    With DV_AUTH_ENABLED=false every caller is trusted as whoever the query
    string names, so that mode is only safe when nothing but this machine (IIS)
    can connect (code review C-01).

    Deliberately a WARNING, not a refusal: existing deployments run with this
    combination today, and stopping them on the next restart would take the
    application down. The fix is a one-line .env change the operator makes on
    their own schedule. Set DV_STRICT_BIND=true to turn the warning into a
    hard stop once every server has been changed.

    Returns True when the configuration is insecure.
    """
    if AUTH_ENABLED or is_loopback_host(SERVER_HOST):
        return False
    message = (
        "\n[DEV SERVER] SECURITY WARNING: DV_AUTH_ENABLED=false while "
        f"DV_SERVER_HOST={SERVER_HOST} is reachable from the network.\n"
        "             Anyone who can reach this port could act as any user.\n"
        "             Recommended .env change (IIS already proxies to it):\n"
        "               DV_SERVER_HOST=127.0.0.1\n"
    )
    print(message, file=sys.stderr)
    logging.getLogger("backend.security").warning(
        "C-01: auth disabled on network-reachable host %s:%s - set DV_SERVER_HOST=127.0.0.1",
        SERVER_HOST, SERVER_PORT,
    )
    if os.getenv("DV_STRICT_BIND", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("[DEV SERVER] DV_STRICT_BIND=true -> not starting.", file=sys.stderr)
        sys.exit(3)
    return True

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

if __name__ == "__main__":

    _warn_insecure_bind()

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
        # log_config=None stops uvicorn installing its OWN handlers on the
        # `uvicorn`, `uvicorn.error` and `uvicorn.access` loggers with
        # propagate=False. That default meant uvicorn's output never reached
        # backend/logging_config.py's handlers, so the log FILE contained no
        # HTTP status or latency line for any request — every response code
        # had to be inferred from hand-written [main] lines. With this, those
        # loggers propagate to root and are formatted, dated and pruned like
        # everything else.
        "log_config": None,
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
