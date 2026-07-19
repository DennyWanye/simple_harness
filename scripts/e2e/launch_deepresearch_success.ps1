[CmdletBinding()]
param(
    [string]$ResultRoot = "",
    [int]$BackendPort = 8100,
    [int]$VitePort = 5173
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "launch_deepresearch_failure.ps1"
& $launcher -Profile success -ResultRoot $ResultRoot -BackendPort $BackendPort -VitePort $VitePort
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
