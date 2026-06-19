# SendInput 真点击 (WebView2/Tauri 圣杯) — 物理像素坐标。
# 用法: powershell -File sendinput-click.ps1 -X 3268 -Y 1502
param([int]$X, [int]$Y)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SI {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v);
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
  public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;
  public static void Click(int x, int y) {
    try { SetProcessDpiAwareness(2); } catch {}
    SetCursorPos(x, y);
    System.Threading.Thread.Sleep(60);
    var arr = new INPUT[2];
    arr[0].type = 0; arr[0].u.mi.dwFlags = LEFTDOWN;
    arr[1].type = 0; arr[1].u.mi.dwFlags = LEFTUP;
    SendInput(2, arr, Marshal.SizeOf(typeof(INPUT)));
  }
}
"@
[SI]::Click($X, $Y)
Write-Host "SendInput click at ($X,$Y) done"
