# SendInput 圣杯：WebView2 真点击 + 可选 Ctrl+V/Enter（坐标走 env CLICK_X/CLICK_Y，动作走 env CLICK_ACT）
# CLICK_ACT: click | paste | enter | paste_enter  (默认 paste)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SI {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public InputUnion u; }
    [StructLayout(LayoutKind.Explicit)] public struct InputUnion {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;
    public const uint KEYUP = 0x0002;
    public const ushort VK_CTRL = 0x11, VK_V = 0x56, VK_ENTER = 0x0D;
    public static void Click(int x, int y) {
        SetCursorPos(x, y); System.Threading.Thread.Sleep(80);
        INPUT[] i = new INPUT[2];
        i[0].type = 0; i[0].u.mi.dwFlags = LEFTDOWN;
        i[1].type = 0; i[1].u.mi.dwFlags = LEFTUP;
        SendInput(2, i, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void Key(ushort vk, bool up) {
        INPUT[] i = new INPUT[1];
        i[0].type = 1; i[0].u.ki.wVk = vk;
        if (up) i[0].u.ki.dwFlags = KEYUP;
        SendInput(1, i, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void CtrlV() {
        Key(VK_CTRL,false); System.Threading.Thread.Sleep(30);
        Key(VK_V,false); System.Threading.Thread.Sleep(30);
        Key(VK_V,true); System.Threading.Thread.Sleep(30);
        Key(VK_CTRL,true);
    }
    public static void Enter() {
        Key(VK_ENTER,false); System.Threading.Thread.Sleep(30); Key(VK_ENTER,true);
    }
}
"@
$x = [int]$env:CLICK_X
$y = [int]$env:CLICK_Y
$act = $env:CLICK_ACT
if (-not $act) { $act = "paste" }
[SI]::Click($x, $y)
Start-Sleep -Milliseconds 250
if ($act -eq "paste" -or $act -eq "paste_enter") { [SI]::CtrlV(); Start-Sleep -Milliseconds 200 }
if ($act -eq "enter" -or $act -eq "paste_enter") { [SI]::Enter() }
"SendInput done act=$act at ($x,$y)"
