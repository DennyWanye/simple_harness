# Save a screenshot of the pet window region to the screenshots dir
param([string]$Name = "shot")
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$dir = 'G:\projects\deskpet\plans\manual-results-2026-06-24-problem-pipeline\screenshots'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
# Pet window region (left monitor, approx). Capture x=3050..3650, y=620..1520 (screen px)
$x = 3040; $y = 600; $w = 640; $h = 960
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$path = Join-Path $dir "$Name.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $path"
