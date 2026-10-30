# dv_common.ps1 - helpers for free_port.ps1 (called by run_backend.bat).
# Dot-source it:  . (Join-Path $PSScriptRoot "dv_common.ps1")

$script:DvRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

# The port comes from the same settings the backend reads, so changing
# DV_SERVER_PORT_55 / _60 in .env needs no edit here. The overlay file wins over the
# root .env; defaults are the documented 8004 (5.5) and 8003 (6.0).
function Get-DvPort([string] $Version) {
    $default = if ($Version -eq "55") { 8004 } else { 8003 }
    $files = @((Join-Path $script:DvRoot ".env"), (Join-Path $script:DvRoot "config\env\.env.$Version"))
    $found = $null
    foreach ($f in $files) {
        if (-not (Test-Path $f)) { continue }
        foreach ($line in Get-Content $f) {
            if ($line -match "^\s*DV_SERVER_PORT_$Version\s*=\s*(\d+)\s*(#.*)?$") { $found = [int]$Matches[1] }
            elseif (($line -match "^\s*DV_SERVER_PORT\s*=\s*(\d+)\s*(#.*)?$") -and ($null -eq $found)) { $found = [int]$Matches[1] }
        }
    }
    if ($null -ne $found) { return $found } else { return $default }
}

# Python processes that are THIS project's backend: dev_server.py / backend.main under
# $DvRoot. Anything else (another app's python, IIS) is never touched.
function Get-DvBackendProcesses {
    $rootLower = $script:DvRoot.ToLower()
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cl = ($_.CommandLine + "").ToLower()
            ($cl -match "dev_server\.py|backend\.main") -and (
                $cl.Contains($rootLower) -or (($_.ExecutablePath + "").ToLower().StartsWith($rootLower)) -or
                ($cl -match "^\S*python\S*\s+dev_server\.py")
            )
        }
}

# Free $Port when a stale copy of this backend holds it. Returns $true when the port is
# free afterwards. A port held by something that is NOT this backend is logged and left alone.
function Clear-DvPort([int] $Port, [string] $LogFile) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { return $true }
    $ours = @(Get-DvBackendProcesses | ForEach-Object { $_.ProcessId })
    foreach ($l in $listeners) {
        $procId = $l.OwningProcess
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $isPython = $p -and $p.ProcessName -like "python*"
        if ($ours -contains $procId -or $isPython) {
            Write-DvLog $LogFile "port $Port held by stale python PID $procId - stopping it"
            # Also stop its parent/children python (uvicorn reloader leftovers).
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "python*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } else {
            $who = if ($p) { $p.ProcessName } else { "unknown" }
            Write-DvLog $LogFile "port $Port is held by '$who' (PID $procId), not this backend - left alone"
        }
    }
    Start-Sleep -Seconds 2
    return (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -eq 0)
}

function Write-DvLog([string] $LogFile, [string] $Message) {
    if (-not $LogFile) { Write-Host $Message; return }
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    # Retry: the file can be briefly locked by the backend's own append.
    for ($i = 0; $i -lt 5; $i++) {
        try { Add-Content -Path $LogFile -Value $line -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 300 }
    }
}
