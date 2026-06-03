# Multi-monitor cross-DPI repeated-drag test for DeskPet.
# Validates the pin_size fix: logical window size must stay constant (no drift)
# across many Samsung<->Xiaomi crossings (old bug drifted 375x610 -> 360x657 ->
# narrower than character -> pet shows only half).
#
# Grab point is derived from window_geometry.json (pet's authoritative physical
# outer position + logical size), NOT GetWindowRect(MainWindowHandle) — the
# process MainWindowHandle can point at a non-pet window (WebView2 host/devtools).
#
# Usage: powershell -ExecutionPolicy Bypass -File multi_drag_test.ps1 [crossings]
param([int]$Crossings = 10)

Add-Type @"
using System; using System.Runtime.InteropServices;
public class M {
  [DllImport("user32.dll")] public static extern IntPtr SetProcessDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int n);
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MOUSEINPUT mi; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
  const uint MOVE=0x0001, ABS=0x8000, VDESK=0x4000, LDOWN=0x0002, LUP=0x0004;
  static int VX,VY,VW,VH;
  public static void Init(){ SetProcessDpiAwarenessContext((IntPtr)(-4)); VX=GetSystemMetrics(76);VY=GetSystemMetrics(77);VW=GetSystemMetrics(78);VH=GetSystemMetrics(79); }
  static void Send(uint f,int x,int y,bool mv){ var a=new INPUT[1]; a[0].type=0; if(mv){ a[0].mi.dx=(int)((double)(x-VX)*65535.0/(VW-1)); a[0].mi.dy=(int)((double)(y-VY)*65535.0/(VH-1)); } a[0].mi.dwFlags=f; SendInput(1,a,Marshal.SizeOf(typeof(INPUT))); }
  public static void MoveTo(int x,int y){ Send(MOVE|ABS|VDESK,x,y,true); }
  public static void Down(){ Send(LDOWN,0,0,false); }
  public static void Up(){ Send(LUP,0,0,false); }
}
"@
[M]::Init()
$json = "G:\projects\deskpet\backend\userdata\window_geometry.json"
function PetState {
  $g = Get-Content $json -Raw | ConvertFrom-Json
  $scale = if ($g.x -lt 3840) { 1.5 } else { 1.0 }
  $pw = [int]($g.width * $scale); $ph = [int]($g.height * $scale)
  [pscustomobject]@{ x=$g.x; y=$g.y; w=$g.width; h=$g.height; cx=[int]($g.x + $pw/2); cy=[int]($g.y + $ph/2); mon=if($g.x -lt 3840){"Samsung"}else{"Xiaomi"} }
}
function Drag($gx,$gy,$ex,$ey) {
  [M]::MoveTo($gx,$gy); Start-Sleep -Milliseconds 220
  [M]::Down(); Start-Sleep -Milliseconds 160
  for($k=1;$k -le 4;$k++){ [M]::MoveTo($gx+$k*6,$gy); Start-Sleep -Milliseconds 30 }
  $steps=40
  for($k=1;$k -le $steps;$k++){ $x=[int]($gx+($ex-$gx)*$k/$steps); $y=[int]($gy+($ey-$gy)*$k/$steps); [M]::MoveTo($x,$y); Start-Sleep -Milliseconds 20 }
  Start-Sleep -Milliseconds 150; [M]::Up()
}
$s = PetState
"{0,-5} {1,-16} {2,-14} {3}" -f "drag","from->to","logical WxH","grab->result pos"
"init  {0,-16} {1,-14} pos=({2},{3})" -f $s.mon, "$($s.w)x$($s.h)", $s.x, $s.y
for($d=1; $d -le $Crossings; $d++) {
  $s = PetState
  if($s.x -lt 3840){ $ex=4700;$ey=450; $dir="Samsung->Xiaomi" } else { $ex=1500;$ey=600; $dir="Xiaomi->Samsung" }
  Drag $s.cx $s.cy $ex $ey
  Start-Sleep -Milliseconds 1300   # wait debounce(800)+snap(250) flush
  $s2 = PetState
  $moved = if([Math]::Abs($s2.x - $s.x) -gt 200){"MOVED"}else{"no-move"}
  "{0,-5} {1,-16} {2,-14} grab({3},{4})->({5},{6}) {7} [{8}]" -f $d, $dir, "$($s2.w)x$($s2.h)", $s.cx, $s.cy, $s2.x, $s2.y, $moved, $s2.mon
}
"=== DONE. logical WxH must stay 360x600 throughout (pinned, no drift). ==="
