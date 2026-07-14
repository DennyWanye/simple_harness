param(
    [Parameter(Mandatory = $true)]
    [string]$Out,
    [string]$ProcessName = "deskpet"
)

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DeskPetWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    public static extern IntPtr SetProcessDpiAwarenessContext(IntPtr value);
}
'@

# CopyFromScreen consumes physical pixels.  Match GetWindowRect to the target
# monitor's physical coordinate space on mixed-DPI Windows desktops.
[void][DeskPetWindowCapture]::SetProcessDpiAwarenessContext([IntPtr](-4))

$process = Get-Process -Name $ProcessName -ErrorAction Stop |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Sort-Object StartTime -Descending |
    Select-Object -First 1
if ($null -eq $process) {
    throw "No visible $ProcessName window found"
}

$rect = New-Object DeskPetWindowCapture+RECT
if (-not [DeskPetWindowCapture]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) {
    throw "GetWindowRect failed for PID $($process.Id)"
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) {
    throw "Invalid window bounds ${width}x${height}"
}

$parent = Split-Path -Parent $Out
if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
Write-Output "saved $Out (${width}x${height}) pid=$($process.Id)"
