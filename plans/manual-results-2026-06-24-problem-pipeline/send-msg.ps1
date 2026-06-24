# Send one chat message to DeskPet: SendInput click input -> clipboard paste -> SendInput click send button
# Usage: powershell -File send-msg.ps1 -Text "..."
param([string]$Text)

$sig = @'
using System;
using System.Runtime.InteropServices;
public class W32B {
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

# 1) focus input
[W32B]::Click(3339, 1320)
Start-Sleep -Milliseconds 250
# 2) set clipboard (STA) + paste
$rs=[runspacefactory]::CreateRunspace(); $rs.ApartmentState='STA'; $rs.ThreadOptions='ReuseThread'; $rs.Open()
$ps=[powershell]::Create(); $ps.Runspace=$rs
[void]$ps.AddScript("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetText([string]`$args[0])").AddArgument($Text)
$ps.Invoke() | Out-Null; $ps.Dispose(); $rs.Close()
Start-Sleep -Milliseconds 150
[W32B]::Key(0x56, $true)   # Ctrl+V
Start-Sleep -Milliseconds 350
# 3) click send button
[W32B]::Click(3543, 1464)
Write-Output "sent: $Text"
