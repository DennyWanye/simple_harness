# SendInput real left-click at screen coords (WebView2 ignores legacy mouse_event; needs SendInput).
# Usage: powershell -File click-at.ps1 -X 3543 -Y 1464
param([int]$X, [int]$Y)
$sig = @'
using System;
using System.Runtime.InteropServices;
public class W32C {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MI mi; }
    [StructLayout(LayoutKind.Sequential)] public struct MI { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr ex; }
    public const uint LEFTDOWN=0x0002, LEFTUP=0x0004;
    public static void Click(int x, int y) {
        SetCursorPos(x,y); System.Threading.Thread.Sleep(80);
        var a=new INPUT[2]; a[0].type=0; a[0].mi.dwFlags=LEFTDOWN; a[1].type=0; a[1].mi.dwFlags=LEFTUP;
        SendInput(2,a,Marshal.SizeOf(typeof(INPUT)));
    }
}
'@
Add-Type -TypeDefinition $sig
[W32C]::Click($X, $Y)
Write-Output "clicked: $X,$Y"
