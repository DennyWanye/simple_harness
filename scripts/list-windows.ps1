# List ALL visible top-level windows (>80x80) with owning process + rect.
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WL {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$rows = @()
$cb = [WL+EnumWindowsProc]{
  param($h,$l)
  if([WL]::IsWindowVisible($h)){
    $r = New-Object WL+RECT; [void][WL]::GetWindowRect($h, [ref]$r)
    $w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
    if($w -gt 80 -and $hh -gt 80){
      $procId = 0; [void][WL]::GetWindowThreadProcessId($h, [ref]$procId)
      $pname = (Get-Process -Id $procId -EA 0).ProcessName
      $sb = New-Object System.Text.StringBuilder 256; [void][WL]::GetWindowTextW($h, $sb, 256)
      $script:rows += [pscustomobject]@{Proc=$pname; PID=$procId; Pos="($($r.Left),$($r.Top))"; Size="$($w)x$($hh)"; Title=$sb.ToString(); H=$h}
    }
  }
  return $true
}
[void][WL]::EnumWindows($cb, [IntPtr]::Zero)
$rows | Where-Object { $_.Proc -match 'deskpet|webview|msedge' -or $_.Title -match 'eskPet|esk Pet' } |
  Format-Table Proc,PID,Pos,Size,Title,H -AutoSize
"--- all (first 25) ---"
$rows | Select-Object -First 25 | Format-Table Proc,PID,Pos,Size,Title -AutoSize
