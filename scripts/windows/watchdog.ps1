<#
.SYNOPSIS
  Keeps one Data Variance backend alive. Run every 5 minutes by the
  "DataVariance-<ver>-Watchdog" scheduled task (register_tasks.ps1).

.DESCRIPTION
  Asks the backend GET /app-config. When it does not answer it recovers, whatever the
  reason the backend is down:
    * task not running (gave up, clean exit, never started at boot)   -> start it
    * task "Running" but nothing answers (stuck cmd, hung python,
      port held by a stale copy)                                      -> stop it, free the port, start it
  Guards against fighting a backend that is merely slow:
    * a backend started less than StartupGraceMinutes ago is left alone (model loading);
    * a running backend must fail FailuresBeforeRestart checks in a row before it is restarted.
  A deliberate stop is respected: create logs\<ver>\STOP and the watchdog does nothing.
  Everything it does is written to logs\<ver>\watchdog.log.
#>
param(
    [Parameter(Mandatory)] [ValidateSet("55", "60")] [string] $Version,
    [int] $StartupGraceMinutes = 4,
    [int] $FailuresBeforeRestart = 2
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "dv_common.ps1")

$logDir = Join-Path $script:DvRoot "logs\$Version"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "watchdog.log"
$stateFile = Join-Path $logDir "watchdog.state"
$task = Get-DvTaskName $Version
$port = Get-DvPort $Version

# Keep the watchdog log small: roll over at 2 MB.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 2MB)) { Move-Item $log "$log.old" -Force }

if (Test-Path (Join-Path $logDir "STOP")) { exit 0 }

if (Test-DvHealthy -Port $port) {
    if (Test-Path $stateFile) { Remove-Item $stateFile -Force -ErrorAction SilentlyContinue }
    exit 0
}

$info = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if (-not $info) { Write-DvLog $log "task $task is not registered - run register_tasks.ps1"; exit 1 }

# Still starting up? Give the embedding model time to load.
$procs = @(Get-DvBackendProcesses)
$youngest = $procs | ForEach-Object { $_.CreationDate } | Sort-Object -Descending | Select-Object -First 1
if ($youngest -and ((Get-Date) - $youngest).TotalMinutes -lt $StartupGraceMinutes) { exit 0 }

$failures = 1
if (Test-Path $stateFile) { $failures = [int](Get-Content $stateFile -ErrorAction SilentlyContinue) + 1 }

if ($info.State -eq "Running" -and $procs.Count -gt 0 -and $failures -lt $FailuresBeforeRestart) {
    Set-Content $stateFile $failures
    Write-DvLog $log "port $port not answering (check $failures of $FailuresBeforeRestart) - will restart if it persists"
    exit 0
}

Write-DvLog $log ("port $port not answering; task state={0}; backend processes={1} -> restarting" -f $info.State, $procs.Count)
if ($info.State -eq "Running") {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Clear-DvPort -Port $port -LogFile $log | Out-Null
Start-ScheduledTask -TaskName $task
Remove-Item $stateFile -Force -ErrorAction SilentlyContinue
Write-DvLog $log "task $task started"
exit 0
