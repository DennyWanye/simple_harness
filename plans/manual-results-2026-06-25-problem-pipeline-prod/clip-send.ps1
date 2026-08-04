# Focus input -> Ctrl+V (paste whatever is already in clipboard) -> click send. All via SendInput.
# Clipboard must be pre-set (use windows-mcp Clipboard tool, which handles Chinese correctly).
# Usage: powershell -File clip-send.ps1 -FX 2804 -FY 1464 -SX 2931 -SY 1464
param([int]$FX, [int]$FY, [int]$SX, [int]$SY)

$sig = @'
using System;
using System.Runtime.InteropServices;
public class W32C {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MK u; }
    [StructLayout(LayoutKind.Explicit)] public struct MK { [FieldOffset(0)] public MI mi; [FieldOffset(0)] public KI ki; }
    [StructLayout(LayoutKind.Sequential)] public struct MI { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr ex; }
    [StructLayout(LayoutKind.Sequential)] public struct KI { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr ex; }
    public const uint LEFTDOWN=0x0002, LEFTUP=0x0004, KEYUP=0x0002;
    public static void Click(int x, int y) {
        SetCursorPos(x,y); System.Threading.Thread.Sleep(60);
        var a=new INPUT[2]; a[0].type=0; a[0].u.mi.dwFlags=LEFTDOWN; a[1].type=0; a[1].u.mi.dwFlags=LEFTUP;
        SendInput(2,a,Marshal.SizeOf(typeof(INPUT)));
    }
    public static void Key(ushort vk, bool ctrl) {
        var l=new System.Collections.Generic.List<INPUT>();
        if(ctrl){var c=new INPUT();c.type=1;c.u.ki.wVk=0x11;l.Add(c);}
        var d=new INPUT();d.type=1;d.u.ki.wVk=vk;l.Add(d);
        var u=new INPUT();u.type=1;u.u.ki.wVk=vk;u.u.ki.dwFlags=KEYUP;l.Add(u);
        if(ctrl){var cu=new INPUT();cu.type=1;cu.u.ki.wVk=0x11;cu.u.ki.dwFlags=KEYUP;l.Add(cu);}
        SendInput((uint)l.Count,l.ToArray(),Marshal.SizeOf(typeof(INPUT)));
    }
}
'@
Add-Type -TypeDefinition $sig

[W32C]::Click($FX, $FY)
Start-Sleep -Milliseconds 300
[W32C]::Key(0x56, $true)   # Ctrl+V
Start-Sleep -Milliseconds 400
[W32C]::Click($SX, $SY)
Write-Output "paste-sent"
