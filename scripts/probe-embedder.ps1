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
$outLog = Join-Path $tmp "out.log"
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $exe
$psi.WorkingDirectory = Split-Path $exe
$psi.RedirectStandardError = $true
$psi.RedirectStandardOutput = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.Environment.Clear()
$seen = @{}
Get-ChildItem Env: | ForEach-Object {
  $key = $_.Name.ToUpperInvariant()
  if (-not $seen.ContainsKey($key)) {
    $seen[$key] = $true
    $psi.Environment[$_.Name] = $_.Value
  }
}
$p = [System.Diagnostics.Process]::Start($psi)
$stderrTask = $p.StandardError.ReadToEndAsync()
$stdoutTask = $p.StandardOutput.ReadToEndAsync()
Write-Host "pid=$($p.Id) running 75s (waiting for embedder worker)..."
Start-Sleep -Seconds 75
taskkill /F /T /PID $p.Id 2>$null | Out-Null
$p.WaitForExit(3000) | Out-Null
[System.IO.File]::WriteAllText($errLog, $stderrTask.Result)
[System.IO.File]::WriteAllText($outLog, $stdoutTask.Result)
Start-Sleep 1
$L = Get-Content $errLog
Write-Host "`n===== embedder / mock / datasets signals ====="
$hits = $L | Select-String "embedder|is_mock|BGE-M3|No module named|datasets|p4_embedder_ready|mock"
if($hits){ $hits | ForEach-Object { Write-Host "  $_" } } else { Write-Host "  (no embedder lines captured -- may need longer)" }
Remove-Item $tmp -Recurse -Force -EA 0
