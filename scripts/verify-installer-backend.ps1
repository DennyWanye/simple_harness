# Verify the bundled backend inside the beta.9 NSIS installer is the FRESH
# 6/28 backend (90393872 B), not the stale 6/8 one (89793962 B).
# Silent-install to an ISOLATED dir via NSIS /D, check, then uninstall+clean.
$ErrorActionPreference = "SilentlyContinue"
$exe = "G:\projects\deskpet\tauri-app\src-tauri\target\release\bundle\nsis\DeskPet_0.6.0-beta.9_x64-setup.exe"
$dst = "F:\dpverify-beta9"
$EXPECT_FRESH = 90393872
$STALE_6_8    = 89793962

if(Test-Path $dst){ Remove-Item $dst -Recurse -Force }
Write-Host "Installing (silent, /D=$dst) ..."
# NSIS: /D must be LAST and unquoted.
$p = Start-Process -FilePath $exe -ArgumentList '/S',"/D=$dst" -PassThru -Wait
Start-Sleep 3
# kill anything the installer may have launched
cmd /c "taskkill /F /IM deskpet.exe 2>nul 1>nul"
cmd /c "taskkill /F /IM deskpet-backend.exe 2>nul 1>nul"

$be = Join-Path $dst "backend\deskpet-backend.exe"
Write-Host "`n===== bundled backend check ====="
if(Test-Path $be){
  $sz = (Get-Item $be).Length
  $mt = (Get-Item $be).LastWriteTime
  Write-Host "installed backend: size=$sz  mtime=$mt"
  if($sz -eq $EXPECT_FRESH){ Write-Host "[PASS] FRESH 6/28 backend bundled (size matches $EXPECT_FRESH)" -ForegroundColor Green }
  elseif($sz -eq $STALE_6_8){ Write-Host "[FAIL] STALE 6/8 backend bundled!" -ForegroundColor Red }
  else { Write-Host "[??] size $sz matches neither known value (fresh=$EXPECT_FRESH stale=$STALE_6_8)" -ForegroundColor Yellow }
} else {
  Write-Host "[?] backend not at $be -- listing install dir:" -ForegroundColor Yellow
  Get-ChildItem $dst -Recurse -Filter deskpet-backend.exe -EA 0 | Select-Object FullName,Length,LastWriteTime
}

Write-Host "`n===== version exe in install dir ====="
$mainexe = Join-Path $dst "deskpet.exe"
if(Test-Path $mainexe){ "deskpet.exe ver=$((Get-Item $mainexe).VersionInfo.ProductVersion)" }

Write-Host "`n===== cleanup (uninstall + remove dir) ====="
$uninst = Join-Path $dst "uninstall.exe"
if(Test-Path $uninst){ Start-Process -FilePath $uninst -ArgumentList '/S' -Wait; Start-Sleep 3 }
cmd /c "taskkill /F /IM deskpet.exe 2>nul 1>nul"
if(Test-Path $dst){ Remove-Item $dst -Recurse -Force }
# remove any HKCU uninstall entry this verify-install created (match our dst)
Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -EA 0 |
  Where-Object { $_.DisplayName -match "DeskPet" -and $_.InstallLocation -match "dpverify" } |
  ForEach-Object { Remove-Item $_.PSPath -Recurse -Force; Write-Host "removed stray uninstall reg: $($_.PSChildName)" }
Write-Host "cleanup done. dst exists: $(Test-Path $dst)"
