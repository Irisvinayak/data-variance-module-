@echo off
REM ---------------------------------------------------------------------------
REM run_backend.bat  -  start ONE Data Variance backend instance.
REM
REM   run_backend.bat 55   ->  iDEAL 5.5 on port 8004
REM   run_backend.bat 60   ->  iDEAL 6.0 on port 8003
REM
REM Started at boot by the Task Scheduler task DataVariance-<ver>, and fine to
REM run by hand. Self-contained: no other script is needed. Both instances
REM share the root .env (DB, paths, ports are all version-keyed there); this
REM script only picks the VERSION via a per-instance overlay
REM (config\env\.env.55 / .env.60, see backend/config/settings.py DV_ENV_FILE)
REM and gives each instance its own log folder, because two processes writing
REM one daily log file collide on Windows file locks.
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

REM Port: DV_SERVER_PORT_<ver> from .env (overlay wins), else the documented default.
if "%VER%"=="55" (set "PORT=8004") else (set "PORT=8003")
for %%F in ("%ROOT%\.env" "%OVERLAY%") do if exist "%%~F" (
    for /f "tokens=2 delims==# " %%P in ('findstr /r /b /c:"DV_SERVER_PORT_%VER% *=" "%%~F"') do set "PORT=%%P"
)

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

REM Restart loop. It NEVER gives up on its own: a slow boot (database, network or disk
REM not ready yet) must not leave the backend down until the next reboot. The pause
REM grows 10 s -> 60 s so a long outage does not spin.
REM The only deliberate stop is the STOP file:
REM     create  logs\55\STOP  (or logs\60\STOP)  then End the task
REM and delete it again to allow starts.
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
REM Free the port when a stale python copy still holds it ("address already in
REM use" / WinError 10048). Only python is stopped; anything else is left alone.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -EA SilentlyContinue; if ($p -and $p.ProcessName -like 'python*') { Add-Content -Path '%LOG%' -Value ('port %PORT% held by stale python PID ' + $p.Id + ' - stopping it'); Stop-Process -Id $p.Id -Force; Start-Sleep -Seconds 2 } }"
echo [%DATE% %TIME%] starting VERSION=%VER% on port %PORT% with %PY% (attempt %ATTEMPT%) >> "%LOG%"
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
