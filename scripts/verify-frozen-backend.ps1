# Runtime verify: does the 6/28 frozen backend contain this fix?
# (checks startup log for new-code-only fields). ASCII-only to avoid
# powershell -File GBK mojibake on Chinese chars.
$ErrorActionPreference = "SilentlyContinue"
$exe = "F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe"
$tmp = Join-Path $env:TEMP ("deskpet-frozen-verify-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$errLog = Join-Path $tmp "stderr.log"
$outLog = Join-Path $tmp "stdout.log"

$env:DESKPET_USER_DATA_DIR = $tmp
$env:DESKPET_DEV_MODE       = "1"
$env:DESKPET_BACKEND_PORT   = "8123"

Write-Host "exe       = $exe"
Write-Host "exe mtime = $((Get-Item $exe).LastWriteTime)"
Write-Host "tmp       = $tmp  port=8123"

$p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) `
    -RedirectStandardError $errLog -RedirectStandardOutput $outLog `
    -PassThru -WindowStyle Hidden
Write-Host "pid       = $($p.Id) -- running 22s to capture startup log..."
Start-Sleep -Seconds 22

taskkill /F /T /PID $p.Id 2>&1 | Out-Null
Start-Sleep 1

Write-Host "`n===== fix-only fields check ====="
$all = @()
if (Test-Path $errLog) { $all += Get-Content $errLog }
if (Test-Path $outLog) { $all += Get-Content $outLog }
function Probe($label,$pat){
  $m = $all | Select-String $pat | Select-Object -First 2
  if($m){ Write-Host "[HAS] $label" -ForegroundColor Green; $m|%{ Write-Host "      $_" } }
  else  { Write-Host "[MISSING] $label" -ForegroundColor Yellow }
}
Probe "config_loaded portable=/env_pinned= (new observability fields)" "config_loaded.*(portable=|env_pinned=)"
Probe "provider_registry_ready (new log line)" "provider_registry_ready"
Probe "config_loaded (any -- backend actually started)" "config_loaded"

Write-Host "`n===== stderr head 30 lines ====="
if (Test-Path $errLog) { Get-Content $errLog -TotalCount 30 } else { "(no stderr)" }

Remove-Item $tmp -Recurse -Force -EA 0
