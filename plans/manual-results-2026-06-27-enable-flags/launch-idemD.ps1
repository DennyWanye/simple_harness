# IDEM-D fresh-install launcher: DESKPET_USER_DATA_DIR 指向空目录，触发 seed。
$ErrorActionPreference = 'Continue'
$env:DESKPET_BACKEND_DIR   = 'G:\projects\deskpet\backend'
$env:DESKPET_PYTHON        = 'G:\projects\deskpet\backend\.venv\Scripts\python.exe'
$env:DESKPET_DEV_MODE      = '1'
$env:DESKPET_USER_DATA_DIR = 'G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\idemD-userdata'
$env:DESKPET_BACKEND_PORT  = '8100'
$env:DESKPET_VITE_PORT     = '5173'
Remove-Item Env:\HTTP_PROXY  -ErrorAction SilentlyContinue
Remove-Item Env:\HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:\http_proxy  -ErrorAction SilentlyContinue
Remove-Item Env:\https_proxy -ErrorAction SilentlyContinue
$env:NO_PROXY  = '*'
$env:no_proxy  = '*'
$envFile = 'G:\projects\deskpet\.env'
if (Test-Path $envFile) {
    $raw = Get-Content $envFile -Raw -Encoding UTF8
    if ($raw -match 'DESKPET_CLOUD_API_KEY\s*=\s*(tsk_[A-Za-z0-9]+)') {
        $env:DESKPET_CLOUD_API_KEY = $matches[1]
    }
}
$logDir = 'G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags'
Set-Location 'G:\projects\deskpet\tauri-app'
& npx tauri dev *> "$logDir\idemD-run1.log"
