# DeskPet 装机诊断 — 在"出问题的那台 Windows"上跑。
# 目的：一次性查清 ① 实际安装版本/位置（更新有没有真的应用）
#       ② config.toml 落在哪、有没有 relay-cloud provider（provider 为何不回来）
#       ③ backend.log 里的 config_loaded 路径 / provider 注册 / /v1/models 状态
#       ④ 有没有多份安装残留 / 残留的 DESKPET_* 环境变量。
# 不打印任何 api_key 明文。把全部输出贴回给 Claude 即可。

$ErrorActionPreference = "SilentlyContinue"
function H($t){ Write-Host "`n===== $t =====" -ForegroundColor Cyan }

H "0. 当前时间 / 用户"
Get-Date; "user = $env:USERNAME"

H "1. 注册表：已安装的 DeskPet（NSIS per-user，HKCU）"
$uninst = @(
  "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
  "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
  "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$found = $false
foreach ($u in $uninst) {
  Get-ItemProperty $u | Where-Object {
    $_.DisplayName -match "deskpet|桌宠|DeskPet"
  } | ForEach-Object {
    $found = $true
    "DisplayName    : $($_.DisplayName)"
    "DisplayVersion : $($_.DisplayVersion)"
    "InstallLocation: $($_.InstallLocation)"
    "InstallDate    : $($_.InstallDate)"
    "Hive           : $u"
    "---"
  }
}
if (-not $found) { Write-Host "（注册表没找到 DeskPet 安装条目）" -ForegroundColor Yellow }

H "2. 磁盘上所有 deskpet.exe / deskpet-backend.exe（版本 + 修改时间）"
$roots = @("$env:LOCALAPPDATA","$env:APPDATA","$env:ProgramFiles","${env:ProgramFiles(x86)}","C:\","D:\","E:\","F:\") |
  Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
foreach ($r in $roots) {
  Get-ChildItem -Path $r -Recurse -Include deskpet.exe,deskpet-backend.exe -Depth 6 -File -Force |
    ForEach-Object {
      $v = $_.VersionInfo.ProductVersion
      "{0,-60} ver={1,-16} mtime={2}" -f $_.FullName, $v, $_.LastWriteTime
    }
}

H "3. 正在运行的 deskpet / backend 进程（从哪个 exe 起的）"
Get-Process deskpet,deskpet-backend,python -ErrorAction SilentlyContinue |
  Select-Object Name, Id, StartTime, Path | Format-Table -AutoSize

H "4. config.toml 候选位置（mtime / 大小 / 是否含 relay-cloud；不打印 key）"
$cfgDirs = @(
  "$env:APPDATA\deskpet",
  "$env:LOCALAPPDATA\deskpet"
)
# 加上每个安装目录下的 userdata
foreach ($u in $uninst) {
  Get-ItemProperty $u | Where-Object { $_.DisplayName -match "deskpet|桌宠|DeskPet" } |
    ForEach-Object { if ($_.InstallLocation) { $cfgDirs += (Join-Path $_.InstallLocation "userdata") } }
}
if ($env:DESKPET_USER_DATA_DIR) { $cfgDirs += $env:DESKPET_USER_DATA_DIR }
$cfgDirs = $cfgDirs | Where-Object { $_ } | Select-Object -Unique
foreach ($d in $cfgDirs) {
  $cfg = Join-Path $d "config.toml"
  if (Test-Path $cfg) {
    $fi = Get-Item $cfg
    $txt = Get-Content $cfg -Raw
    $hasRelay = if ($txt -match "relay-cloud") { "YES" } else { "no" }
    $hasProviders = if ($txt -match "\[\[providers\]\]") { "YES" } else { "no" }
    $hasLlm = if ($txt -match "\[llm\]") { "YES" } else { "no" }
    "FOUND  $cfg"
    "   mtime=$($fi.LastWriteTime)  size=$($fi.Length)B  relay-cloud=$hasRelay  [[providers]]=$hasProviders  [llm]=$hasLlm"
  } else {
    "absent $cfg"
  }
}

H "5. backend.log：config_loaded 路径 / provider 注册 / /v1/models 状态（各取最近几条）"
$logCandidates = @()
foreach ($d in $cfgDirs) { $logCandidates += (Join-Path $d "logs\backend.log"); $logCandidates += (Join-Path (Split-Path $d) "logs\backend.log") }
foreach ($u in $uninst) {
  Get-ItemProperty $u | Where-Object { $_.DisplayName -match "deskpet|桌宠|DeskPet" } |
    ForEach-Object { if ($_.InstallLocation) { $logCandidates += (Join-Path $_.InstallLocation "logs\backend.log"); $logCandidates += (Join-Path $_.InstallLocation "backend\logs\backend.log") } }
}
$logCandidates = $logCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
if (-not $logCandidates) { Write-Host "（没找到 backend.log，下面按时间扫一遍磁盘）" -ForegroundColor Yellow
  foreach ($r in $roots) { Get-ChildItem -Path $r -Recurse -Filter backend.log -Depth 7 -File -Force | ForEach-Object { $logCandidates += $_.FullName } }
  $logCandidates = $logCandidates | Select-Object -Unique
}
foreach ($lg in $logCandidates) {
  $fi = Get-Item $lg
  Write-Host "--- $lg  (mtime=$($fi.LastWriteTime), size=$($fi.Length)B) ---" -ForegroundColor Green
  $lines = Get-Content $lg
  "  [config_loaded]";        $lines | Select-String "config_loaded" | Select-Object -Last 2 | ForEach-Object { "    $_" }
  "  [provider_registry]";    $lines | Select-String "provider_registry_ready" | Select-Object -Last 2 | ForEach-Object { "    $_" }
  "  [relay_provider_ensured]"; $lines | Select-String "relay_provider_ensured" | Select-Object -Last 2 | ForEach-Object { "    $_" }
  "  [/v1/models 状态]";      $lines | Select-String "v1/models" | Select-Object -Last 3 | ForEach-Object { "    $_" }
  "  [empty_api_key / Bearer / 401]"; $lines | Select-String "empty_api_key|Illegal header|Bearer|401 Unauthorized|all_providers_failed" | Select-Object -Last 4 | ForEach-Object { "    $_" }
}

H "6. 残留的 DESKPET_* 环境变量（User + Machine）"
"User scope:"
[Environment]::GetEnvironmentVariables("User").GetEnumerator()    | Where-Object { $_.Key -like "DESKPET_*" } | ForEach-Object { "  $($_.Key) = $($_.Value)" }
"Machine scope:"
[Environment]::GetEnvironmentVariables("Machine").GetEnumerator() | Where-Object { $_.Key -like "DESKPET_*" } | ForEach-Object { "  $($_.Key) = $($_.Value)" }

H "诊断结束 — 把以上全部输出贴回给 Claude"
