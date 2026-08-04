# Save a screenshot of the pet window region to the screenshots dir.
# Usage: powershell -File save-shot.ps1 -Name "IDEM-1-01-send1"
param([string]$Name = "shot")
Add-Type -AssemblyName System.Drawing
$dir = 'G:\projects\deskpet\plans\manual-results-2026-06-25-problem-pipeline-prod\screenshots'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
# Pet window region (left monitor). Capture x=3040..3680, y=600..1560 (screen px).
$x = 3040; $y = 600; $w = 640; $h = 960
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$path = Join-Path $dir "$Name.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $path"
