param([string]$name)
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$dir = "G:\projects\deskpet\testcase\2026-06-21-task-drift-v2-phaseB\screenshots"
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
$path = Join-Path $dir "$name.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $path"
