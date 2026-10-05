@echo off
REM ---------------------------------------------------------------------------
REM run_backend.bat  -  start ONE Data Variance backend instance.
REM
REM   run_backend.bat 55   ->  iDEAL 5.5 on port 8004
REM   run_backend.bat 60   ->  iDEAL 6.0 on port 8003
REM
REM Used by the scheduled tasks registered by register_tasks.ps1, and fine to
REM run by hand. Both instances share the root .env (DB, paths, ports are all
REM version-keyed there); this script only picks the VERSION via a per-instance
REM overlay (config\env\.env.55 / .env.60, see backend/config/settings.py
REM DV_ENV_FILE) and gives each instance its own log folder, because two
REM processes writing one daily log file collide on Windows file locks.
REM ---------------------------------------------------------------------------
setlocal

set "VER=%~1"
if /I not "%VER%"=="55" if /I not "%VER%"=="60" (
    echo Usage: %~nx0 55^|60
    exit /b 2
)

REM Project root = two levels above this script (scripts\windows\ -> root).
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

REM Per-instance overlay. Created from the tracked template on first run, so a
REM fresh checkout needs no manual copy step.
set "OVERLAY=%ROOT%\config\env\.env.%VER%"
if not exist "%OVERLAY%" copy /Y "%ROOT%\config\env\.env.%VER%.example" "%OVERLAY%" >nul
set "DV_ENV_FILE=%OVERLAY%"

REM Separate logs per instance: logs\55\ and logs\60\ (the AI audit trail
REM follows DV_LOG_DIR into logs\<ver>\ai\).
set "DV_LOG_DIR=%ROOT%\logs\%VER%"
if not exist "%DV_LOG_DIR%" mkdir "%DV_LOG_DIR%"

REM Never autoreload under Task Scheduler: the reloader's child process
REM survives "End task" and keeps the port (WinError 10048 on next start).
set "DEV_SERVER_RELOAD=0"

REM Python: prefer the project venv, then backend\.venv, then PATH.
set "PY=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if exist "%ROOT%\backend\.venv\Scripts\python.exe" if not exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\backend\.venv\Scripts\python.exe"

REM Console output (startup errors included) goes to a per-instance file, so a
REM task that dies at launch leaves evidence instead of a vanished window.
REM Restart loop (code review M-27): Task Scheduler's own restart-on-failure only covers a
REM task that fails to LAUNCH, not a server that crashes later. A clean exit (code 0, e.g.
REM an operator stop) ends the loop; a crash is retried after a pause, up to MAXRESTARTS
REM times in a row, then the task ends with the last error so it is visible in Task Scheduler.
set /a MAXRESTARTS=20
set /a ATTEMPT=0

:run
echo [%DATE% %TIME%] starting VERSION=%VER% with %PY% (attempt %ATTEMPT%) >> "%DV_LOG_DIR%\service-console.log"
"%PY%" dev_server.py >> "%DV_LOG_DIR%\service-console.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] exited with code %RC% >> "%DV_LOG_DIR%\service-console.log"
if "%RC%"=="0" exit /b 0
set /a ATTEMPT+=1
if %ATTEMPT% GTR %MAXRESTARTS% (
    echo [%DATE% %TIME%] giving up after %MAXRESTARTS% restarts >> "%DV_LOG_DIR%\service-console.log"
    exit /b %RC%
)
REM ping is the pause: timeout.exe refuses to run without a console (Task Scheduler)
ping -n 11 127.0.0.1 >nul
goto run
