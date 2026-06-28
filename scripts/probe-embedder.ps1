# Confirm frozen embedder loads REAL BGE-M3 (is_mock=False), no datasets miss.
$ErrorActionPreference = "SilentlyContinue"
$exe = "F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe"
"exe mtime = $((Get-Item $exe).LastWriteTime)"
$tmp = Join-Path $env:TEMP ("dp-emb-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$errLog = Join-Path $tmp "stderr.log"
$env:DESKPET_USER_DATA_DIR = $tmp
$env:DESKPET_DEV_MODE = "1"
$env:DESKPET_BACKEND_PORT = "8125"
$p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) `
    -RedirectStandardError $errLog -RedirectStandardOutput (Join-Path $tmp "out.log") `
    -PassThru -WindowStyle Hidden
Write-Host "pid=$($p.Id) running 32s (waiting for embedder worker)..."
Start-Sleep -Seconds 32
taskkill /F /T /PID $p.Id 2>$null | Out-Null
Start-Sleep 1
$L = Get-Content $errLog
Write-Host "`n===== embedder / mock / datasets signals ====="
$hits = $L | Select-String "embedder|is_mock|BGE-M3|No module named|datasets|p4_embedder_ready|mock"
if($hits){ $hits | ForEach-Object { Write-Host "  $_" } } else { Write-Host "  (no embedder lines captured -- may need longer)" }
Remove-Item $tmp -Recurse -Force -EA 0
