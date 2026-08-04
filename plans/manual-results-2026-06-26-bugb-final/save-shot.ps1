# Save a screenshot of the 消息 + pet window region to the screenshots dir.
# Usage: powershell -File save-shot.ps1 -Name "TC-A1-02-reply"
param([string]$Name = "shot")
Add-Type -AssemblyName System.Drawing
$dir = 'G:\projects\deskpet\plans\manual-results-2026-06-26-bugb-phase1\screenshots'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
# 消息 window (left) ~x1130-1740; pet window ~x1690-2400. Capture both.
$x = 1120; $y = 600; $w = 1300; $h = 960
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$path = Join-Path $dir "$Name.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $path"
