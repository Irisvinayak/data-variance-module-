<#
.SYNOPSIS
  Register (or re-register) Windows scheduled tasks that keep the Data Variance
  backends running:  "DataVariance-5.5" (port 8004) and "DataVariance-6.0" (port 8003).

.DESCRIPTION
  Each task runs scripts\windows\run_backend.bat <55|60> at system startup,
  whether or not anyone is logged on, restarts it if it crashes, and has no
  execution time limit (Task Scheduler's default of 3 days would otherwise kill
  the server).

  Run from an ELEVATED PowerShell on the server:

    powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
    powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -Versions 55
    powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -User "DOMAIN\svc_datavar"

.PARAMETER Versions
  Which instances to register. Default: both.

.PARAMETER User
  Account the tasks run as. Default SYSTEM. Use a real service account instead
  if the repository (DV_BASE_PATH_*) is on a network share - SYSTEM has no
  network credentials. You will be prompted for its password.

.PARAMETER StartNow
  Start the tasks immediately after registering (default: true).
#>
[CmdletBinding()]
param(
    [ValidateSet("55", "60")]
    [string[]] $Versions = @("55", "60"),
    [string]   $User = "SYSTEM",
    [bool]     $StartNow = $true
)

$ErrorActionPreference = "Stop"

$principalCheck = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated (Run as administrator) PowerShell."
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$bat  = Join-Path $PSScriptRoot "run_backend.bat"
if (-not (Test-Path $bat)) { throw "Launcher not found: $bat" }

# Port lookup, task names and the stale-process helpers are shared with the launcher
# and the watchdog.
. (Join-Path $PSScriptRoot "dv_common.ps1")
$ports = @{}
foreach ($v in @("55", "60")) { $ports[$v] = Get-DvPort $v }

if ($User -ne "SYSTEM") {
    $cred = Get-Credential -UserName $User -Message "Password for the account the Data Variance tasks run as"
}

foreach ($v in $Versions) {
    $name = Get-DvTaskName $v
    $port = $ports[$v]

    # Replace any previous registration and free the port, so the new task
    # does not fail with WinError 10048 against a manually started copy.
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    # Only a leftover Python process is stopped. Whatever else holds the port (IIS, another
    # service) is reported and left alone: the task will then fail loudly instead of this
    # script killing something unrelated.
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $holder = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            if ($holder -and $holder.ProcessName -like "python*") {
                Write-Host "  stopping python PID $($holder.Id) holding port $port"
                Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
            } else {
                $who = if ($holder) { $holder.ProcessName } else { "unknown" }
                Write-Warning "port $port is held by '$who' (PID $($_.OwningProcess)), not a python process - leaving it alone"
            }
        }

    $action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`" $v" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = "PT30S"   # give the network / IIS a moment after boot

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    if ($User -eq "SYSTEM") {
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description "Data Variance backend, iDEAL $($name.Split('-')[1]) on port $port ($root)" | Out-Null
    } else {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
            -Settings $settings -RunLevel Highest `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password `
            -Description "Data Variance backend, iDEAL $($name.Split('-')[1]) on port $port ($root)" | Out-Null
    }
    Write-Host "Registered $name  (port $port)"

    # Watchdog: every 5 minutes, checks GET /app-config and restarts the backend when it
    # is down for ANY reason (gave up at boot, clean exit, hung, stale port holder, task
    # stuck as "Running"). See watchdog.ps1. Runs as SYSTEM; it only needs local access.
    $wdName = "$name-Watchdog"
    if (Get-ScheduledTask -TaskName $wdName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $wdName -Confirm:$false
    }
    $wdScript = Join-Path $PSScriptRoot "watchdog.ps1"
    $wdAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$wdScript`" -Version $v" `
        -WorkingDirectory $root
    $wdTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)
    $wdBoot = New-ScheduledTaskTrigger -AtStartup
    $wdBoot.Delay = "PT5M"     # first check 5 min after boot, after the backend's own start
    $wdSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
    $wdPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $wdName -Action $wdAction -Trigger @($wdTrigger, $wdBoot) `
        -Settings $wdSettings -Principal $wdPrincipal `
        -Description "Restarts $name when http://127.0.0.1:$port/app-config stops answering" | Out-Null
    Write-Host "Registered $wdName  (every 5 min)"

    if ($StartNow) {
        Start-ScheduledTask -TaskName $name
        Write-Host "  started"
    }
}

if ($StartNow) {
    Write-Host "`nWaiting for the backends to come up (up to 120 s each; the embedding model can take a while)..."
    foreach ($v in $Versions) {
        $port = $ports[$v]
        $deadline = (Get-Date).AddSeconds(120)
        $up = $false
        while ((Get-Date) -lt $deadline) {
            try {
                $cfg = Invoke-RestMethod -Uri "http://127.0.0.1:$port/app-config" -TimeoutSec 5
                Write-Host ("  port {0}: OK  version={1}" -f $port, $cfg.version)
                $up = $true
                break
            } catch {
                Start-Sleep -Seconds 3
            }
        }
        if (-not $up) {
            Write-Host ("  port {0}: not answering after 120 s - check {1}\logs\{2}\service-console.log" -f $port, $root, $v)
        }
    }
}
