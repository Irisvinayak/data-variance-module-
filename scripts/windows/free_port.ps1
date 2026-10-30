<#
.SYNOPSIS
  Called by run_backend.bat before every start: frees the backend's port when a stale
  copy of THIS backend still holds it (the "address already in use" / WinError 10048
  failure). Never touches a process that is not this backend.
  Exit 0 = port free, 1 = port still held by something else.
#>
param(
    [Parameter(Mandatory)] [ValidateSet("55", "60")] [string] $Version,
    [string] $LogFile
)
. (Join-Path $PSScriptRoot "dv_common.ps1")
$port = Get-DvPort $Version
if (Clear-DvPort -Port $port -LogFile $LogFile) { exit 0 } else { exit 1 }
