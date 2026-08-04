# Pipeline E2E - WebView2 SendInput click + clipboard paste helper (KB grail snippet)
# Usage: powershell -File ui-input.ps1 -X 3339 -Y 1320 -Text "..." [-Send]
param(
    [int]$X,
    [int]$Y,
    [string]$Text = "",
    [switch]$Send,
    [switch]$ClickOnly
)

Add-Type -AssemblyName System.Windows.Forms

$sig = @'
using System;
using System.Runtime.InteropServices;
public class W32 {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MOUSEKEYBD u; }
    [StructLayout(LayoutKind.Explicit)] public struct MOUSEKEYBD {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    public const uint MOUSE = 0, KEYBD = 1;
    public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;
    public const uint KEYUP = 0x0002;
    public static void Click(int x, int y) {
        SetCursorPos(x, y); System.Threading.Thread.Sleep(60);
        var a = new INPUT[2];
        a[0].type = MOUSE; a[0].u.mi.dwFlags = LEFTDOWN;
        a[1].type = MOUSE; a[1].u.mi.dwFlags = LEFTUP;
        SendInput(2, a, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void Key(ushort vk, bool ctrl) {
        var list = new System.Collections.Generic.List<INPUT>();
        if (ctrl) { var c = new INPUT(); c.type = KEYBD; c.u.ki.wVk = 0x11; list.Add(c); }
        var d = new INPUT(); d.type = KEYBD; d.u.ki.wVk = vk; list.Add(d);
        var up = new INPUT(); up.type = KEYBD; up.u.ki.wVk = vk; up.u.ki.dwFlags = KEYUP; list.Add(up);
        if (ctrl) { var cu = new INPUT(); cu.type = KEYBD; cu.u.ki.wVk = 0x11; cu.u.ki.dwFlags = KEYUP; list.Add(cu); }
        SendInput((uint)list.Count, list.ToArray(), Marshal.SizeOf(typeof(INPUT)));
    }
}
'@
Add-Type -TypeDefinition $sig

# 1) SendInput real click to focus input
[W32]::Click($X, $Y)
Start-Sleep -Milliseconds 250

if ($ClickOnly) { Write-Output "clicked ($X,$Y)"; exit 0 }

# 2) Chinese via clipboard (STA), then SendInput Ctrl+V
if ($Text -ne "") {
    $rs = [runspacefactory]::CreateRunspace()
    $rs.ApartmentState = 'STA'; $rs.ThreadOptions = 'ReuseThread'; $rs.Open()
    $ps = [powershell]::Create(); $ps.Runspace = $rs
    [void]$ps.AddScript("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetText([string]`$args[0])").AddArgument($Text)
    $ps.Invoke() | Out-Null; $ps.Dispose(); $rs.Close()
    Start-Sleep -Milliseconds 150
    [W32]::Key(0x56, $true)
    Start-Sleep -Milliseconds 200
}

# 3) optional send (Enter)
if ($Send) {
    Start-Sleep -Milliseconds 150
    [W32]::Key(0x0D, $false)
}
Write-Output "done X=$X Y=$Y send=$Send"
