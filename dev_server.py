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
# Single instance: no orphaned / duplicate python processes (Windows)
# -------------------------------------------------------------------
# Two things left extra python.exe copies behind after a restart:
#   1. With reload on, uvicorn runs a watcher that spawns the real server as a
#      CHILD. When WinSW restarts the service (or the watcher dies), the child
#      could survive and keep the port.
#   2. A new start could run while the old copy still held the port, fail
#      with WinError 10048 and loop.
# Fix: (a) on start, stop any leftover python process still listening on our
# port; (b) put this process in a Job Object with KILL_ON_JOB_CLOSE, so every
# child it starts dies with it, whatever the way it exits (crash, os._exit,
# service stop, Task Manager).

_JOB_HANDLE = None  # kept open for the life of the process; closing it kills the tree


def _log(msg: str) -> None:
    print(f"[DEV SERVER] {msg}", flush=True)
    logging.getLogger("dev_server").info(msg)


def _listening_pids(port: int) -> set[int]:
    """PIDs listening on TCP `port` (IPv4 and IPv6), via iphlpapi."""
    import ctypes
    import socket
    import struct
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi")
    TCP_TABLE_OWNER_PID_LISTENER = 3
    pids: set[int] = set()
    # (address family, row size, offset of local port, offset of owning pid)
    for af, row_size, port_off, pid_off in ((2, 24, 8, 20), (23, 56, 20, 52)):
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, af,
                                     TCP_TABLE_OWNER_PID_LISTENER, 0)
        if not size.value:
            continue
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, af,
                                        TCP_TABLE_OWNER_PID_LISTENER, 0) != 0:
            continue
        (count,) = struct.unpack_from("<I", buf, 0)
        for i in range(count):
            base = 4 + i * row_size
            (raw_port,) = struct.unpack_from("<I", buf, base + port_off)
            (pid,) = struct.unpack_from("<I", buf, base + pid_off)
            if socket.ntohs(raw_port & 0xFFFF) == port and pid:
                pids.add(pid)
    return pids


def _free_port_from_stale_python(port: int, wait_s: float = 30.0) -> None:
    """Stop a leftover python.exe still holding `port`, then wait for the port.

    Only processes whose image name starts with "python" are touched; anything
    else on the port is reported and left alone (uvicorn will then fail to bind
    and WinSW retries).
    """
    if os.name != "nt":
        return
    import ctypes
    import time
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    access = 0x1000 | 0x0001 | 0x00100000  # QUERY_LIMITED_INFORMATION | TERMINATE | SYNCHRONIZE

    try:
        pids = _listening_pids(port) - {os.getpid()}
    except OSError as exc:
        _log(f"could not inspect port {port}: {exc}")
        return

    for pid in pids:
        handle = k32.OpenProcess(access, False, pid)
        if not handle:
            _log(f"port {port} held by PID {pid}; cannot open it (error {ctypes.get_last_error()})")
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(len(buf))
            name = ""
            if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(n)):
                name = os.path.basename(buf.value).lower()
            if not name.startswith("python"):
                _log(f"port {port} held by PID {pid} ({name or 'unknown'}) - not python, left alone")
                continue
            _log(f"port {port} held by stale {name} PID {pid} - stopping it")
            k32.TerminateProcess(handle, 1)
            k32.WaitForSingleObject(handle, 10_000)
        finally:
            k32.CloseHandle(handle)

    deadline = time.monotonic() + wait_s
    while _listening_pids(port) - {os.getpid()} and time.monotonic() < deadline:
        time.sleep(0.5)


def _kill_children_with_me() -> None:
    """Join a Job Object that kills every process in it when this one exits."""
    global _JOB_HANDLE
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.GetCurrentProcess.restype = wintypes.HANDLE

    job = k32.CreateJobObjectW(None, None)
    if not job:
        _log(f"Job Object not created (error {ctypes.get_last_error()}); children may outlive a restart")
        return
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) and \
        k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
    if not ok:
        _log(f"Job Object not applied (error {ctypes.get_last_error()}); children may outlive a restart")
        return
    _JOB_HANDLE = job
    _log("child processes are tied to this process (Job Object, kill-on-close)")


# -------------------------------------------------------------------
# Daily self-restart (WinSW <onfailure action="restart"> brings it back)
# -------------------------------------------------------------------

def _parse_restart_at(raw: str | None) -> tuple[int, int] | None:
    """DAILY_RESTART_AT: unset -> 02:10, empty -> disabled, bad -> disabled."""
    if raw is None:
        raw = "02:10"
    raw = raw.strip()
    if not raw:
        return None
    try:
        hh, mm = raw.split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        h = m = -1
    if not (0 <= h <= 23 and 0 <= m <= 59):
        _log(f"DAILY_RESTART_AT={raw!r} is not HH:MM - daily restart disabled")
        return None
    return h, m


def _seconds_until(h: int, m: int, now=None) -> float:
    from datetime import datetime, timedelta

    now = now or datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:  # passed (or exactly now) -> tomorrow; avoids an instant exit loop
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _start_daily_restart() -> None:
    import threading
    import time

    at = _parse_restart_at(os.environ.get("DAILY_RESTART_AT"))
    if at is None:
        _log("Daily restart disabled")
        return
    h, m = at

    def _run() -> None:
        time.sleep(_seconds_until(h, m))
        _log(f"Daily restart time reached ({h:02d}:{m:02d}); exiting so WinSW restarts the service")
        os._exit(1)  # non-zero -> <onfailure action="restart">; Job Object takes the children down too

    threading.Thread(target=_run, name="daily-restart", daemon=True).start()
    _log(f"Daily restart scheduled at {h:02d}:{m:02d}")

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

    _free_port_from_stale_python(SERVER_PORT)
    _kill_children_with_me()
    _start_daily_restart()

    print()
    uvicorn.run("backend.main:app", **options)
