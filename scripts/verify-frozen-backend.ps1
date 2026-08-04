param(
  [string]$Exe = "F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe",
  [int]$StartupSeconds = 75,
  [int]$Port = 8123
)

# Frozen-backend smoke plus AC-24 dependency guard. ASCII-only because Windows
# PowerShell may decode -File input with the active legacy code page.
$ErrorActionPreference = "Stop"
$ForbiddenPackages = @("langgraph", "langchain_core")
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$failed = $false

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
  throw "Frozen backend not found: $Exe"
}

Write-Host "===== source/package guards ====="
$spec = Join-Path $backendRoot "deskpet-backend.spec"
if (Test-Path -LiteralPath $spec) {
  $specText = [System.IO.File]::ReadAllText($spec)
  foreach ($package in $ForbiddenPackages) {
    if ($specText -match [regex]::Escape($package)) {
      Write-Host "[FAIL] spec still references $package" -ForegroundColor Red
      $failed = $true
    }
  }
}

$removedHook = Join-Path $backendRoot "pyinstaller_runtime_hook_langgraph.py"
if (Test-Path -LiteralPath $removedHook) {
  Write-Host "[FAIL] removed runtime hook still exists" -ForegroundColor Red
  $failed = $true
}

$distRoot = Split-Path -Parent $Exe
$bundledForbidden = Get-ChildItem -LiteralPath $distRoot -Recurse -Force |
  Where-Object {
    $name = $_.Name.ToLowerInvariant()
    ($name -match "^langgraph([.\-_]|$)") -or
    ($name -match "^langchain_core([.\-_]|$)")
  }
if ($bundledForbidden) {
  Write-Host "[FAIL] removed packages found in frozen distribution:" -ForegroundColor Red
  $bundledForbidden | ForEach-Object { Write-Host "       $($_.FullName)" }
  $failed = $true
} else {
  Write-Host "[PASS] no removed workflow packages in frozen distribution" -ForegroundColor Green
}

if ($failed) {
  exit 1
}

$tmp = Join-Path $env:TEMP ("deskpet-frozen-verify-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$errLog = Join-Path $tmp "stderr.log"
$outLog = Join-Path $tmp "stdout.log"

$env:DESKPET_USER_DATA_DIR = $tmp
$env:DESKPET_DEV_MODE = "1"
$env:DESKPET_BACKEND_PORT = [string]$Port

Write-Host "exe       = $Exe"
Write-Host "exe mtime = $((Get-Item -LiteralPath $Exe).LastWriteTime)"
Write-Host "tmp       = $tmp  port=$Port"

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $Exe
$psi.WorkingDirectory = Split-Path -Parent $Exe
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

$process = $null
try {
  $process = [System.Diagnostics.Process]::Start($psi)
  $stderrTask = $process.StandardError.ReadToEndAsync()
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  Write-Host "pid       = $($process.Id) -- running ${StartupSeconds}s to capture startup log..."
  Start-Sleep -Seconds $StartupSeconds

  if (-not $process.HasExited) {
    & taskkill /F /T /PID $process.Id 2>&1 | Out-Null
  }
  $process.WaitForExit(3000) | Out-Null
  [System.IO.File]::WriteAllText($errLog, $stderrTask.Result)
  [System.IO.File]::WriteAllText($outLog, $stdoutTask.Result)

  $all = @()
  if (Test-Path -LiteralPath $errLog) { $all += Get-Content -LiteralPath $errLog }
  if (Test-Path -LiteralPath $outLog) { $all += Get-Content -LiteralPath $outLog }

  function Probe([string]$Label, [string]$Pattern) {
    $matches = $all | Select-String $Pattern | Select-Object -First 2
    if ($matches) {
      Write-Host "[HAS] $Label" -ForegroundColor Green
      $matches | ForEach-Object { Write-Host "      $_" }
      return $true
    }
    Write-Host "[MISSING] $Label" -ForegroundColor Yellow
    return $false
  }

  Write-Host "`n===== startup probes ====="
  $started = Probe "config_loaded (backend started)" "config_loaded"
  [void](Probe "provider_registry_ready" "provider_registry_ready")
  [void](Probe "native workflow invocation marker" "engine_kind=deskpet-native|engine_kind.{0,4}deskpet-native")
  foreach ($package in $ForbiddenPackages) {
    if ($all | Select-String "ModuleNotFoundError.*$package|No module named.*$package") {
      Write-Host "[FAIL] startup attempted removed package: $package" -ForegroundColor Red
      $failed = $true
    }
  }
  if (-not $started) { $failed = $true }

  Write-Host "`n===== stderr head 30 lines ====="
  if (Test-Path -LiteralPath $errLog) { Get-Content -LiteralPath $errLog -TotalCount 30 }
} finally {
  if ($process -and -not $process.HasExited) {
    & taskkill /F /T /PID $process.Id 2>&1 | Out-Null
  }
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failed) {
  exit 1
}
Write-Host "[PASS] frozen backend dependency/startup smoke" -ForegroundColor Green
