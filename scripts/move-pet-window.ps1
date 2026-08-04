# Find deskpet.exe top-level windows (incl. frameless/topmost) via EnumWindows
# and move the visible one to a clearly-visible spot on the primary monitor.
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$pids = (Get-Process deskpet -EA 0).Id
$found = @()
$cb = [W+EnumWindowsProc]{
  param($h,$l)
  $pid2 = 0; [void][W]::GetWindowThreadProcessId($h, [ref]$pid2)
  if($pids -contains $pid2 -and [W]::IsWindowVisible($h)){
    $r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r)
    $w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top
    if($w -gt 50 -and $hgt -gt 50){ $script:found += [pscustomobject]@{H=$h; X=$r.Left; Y=$r.Top; W=$w; Hgt=$hgt} }
  }
  return $true
}
[void][W]::EnumWindows($cb, [IntPtr]::Zero)

"== deskpet 可见顶层窗口 =="
$found | ForEach-Object { "hwnd=$($_.H) pos=($($_.X),$($_.Y)) size=$($_.W)x$($_.Hgt)" }

# move the largest visible window to primary-monitor center-ish (800, 300)
$target = $found | Sort-Object { $_.W * $_.Hgt } -Descending | Select-Object -First 1
if($target){
  $SWP_NOSIZE = 0x0001; $SWP_NOZORDER = 0x0004; $SWP_SHOWWINDOW = 0x0040
  [void][W]::SetWindowPos($target.H, [IntPtr]::Zero, 800, 300, 0, 0, ($SWP_NOSIZE -bor $SWP_NOZORDER -bor $SWP_SHOWWINDOW))
  [void][W]::SetForegroundWindow($target.H)
  "== moved hwnd=$($target.H) -> (800,300) on PRIMARY monitor =="
} else {
  "== 没找到可见 deskpet 窗口(可能最小化/隐藏到托盘) =="
}
