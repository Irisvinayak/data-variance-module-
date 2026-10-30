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
REM
REM Restart loop. It NEVER gives up on its own: a slow boot (database, network or disk
REM not ready yet) used to outlast the old 20-try limit and leave the backend down until
REM the next reboot. The pause grows 10 s -> 60 s so a long outage does not spin.
REM Any exit - crash or clean - is restarted; the only deliberate stop is the STOP file:
REM     create  logs\55\STOP  (or logs\60\STOP)  then End the task
REM and delete it again to allow starts. watchdog.ps1 honours the same file.
REM Before every start a stale copy of this backend holding the port is cleared
REM (free_port.ps1), so "address already in use" (WinError 10048) cannot block a start.
set "LOG=%DV_LOG_DIR%\service-console.log"
set "STOPFILE=%DV_LOG_DIR%\STOP"
set /a ATTEMPT=0
set /a PAUSE=10

:run
if exist "%STOPFILE%" (
    echo [%DATE% %TIME%] STOP file present - not starting >> "%LOG%"
    exit /b 0
)
REM Roll the console log at ~20 MB so it can never fill the disk.
for %%F in ("%LOG%") do if %%~zF GTR 20000000 move /Y "%LOG%" "%LOG%.old" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0free_port.ps1" -Version %VER% -LogFile "%LOG%"
echo [%DATE% %TIME%] starting VERSION=%VER% with %PY% (attempt %ATTEMPT%) >> "%LOG%"
"%PY%" dev_server.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] exited with code %RC% - restarting in %PAUSE% s >> "%LOG%"
set /a ATTEMPT+=1
REM ping is the pause: timeout.exe refuses to run without a console (Task Scheduler)
set /a PINGS=PAUSE+1
ping -n %PINGS% 127.0.0.1 >nul
set /a PAUSE=PAUSE*2
if %PAUSE% GTR 60 set /a PAUSE=60
goto run
