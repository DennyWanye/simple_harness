# SendInput "grail" for BOTH mouse and keyboard — WebView2 ignores old
# mouse_event AND old SendKeys journal injection. Focus input via SendInput
# click, set clipboard (STA), paste via SendInput Ctrl+V.
param(
  [int]$InX = 3336, [int]$InY = 1326,
  [string]$Text = "",
  [switch]$ClickOnly
)
Add-Type -AssemblyName System.Windows.Forms
$sig = @'
using System;
using System.Runtime.InteropServices;
public class SI {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c,string n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx,dy; public uint mouseData,dwFlags,time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk,wScan; public uint dwFlags,time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  public const uint LEFTDOWN=0x0002, LEFTUP=0x0004, KEYUP=0x0002;
  static int SZ { get { return Marshal.SizeOf(typeof(INPUT)); } }
  public static void Click(int x,int y){
    SetCursorPos(x,y); System.Threading.Thread.Sleep(80);
    var a=new INPUT[2];
    a[0].type=0; a[0].u.mi.dwFlags=LEFTDOWN;
    a[1].type=0; a[1].u.mi.dwFlags=LEFTUP;
    SendInput(2,a,SZ);
  }
  static INPUT Key(ushort vk, bool up){ var i=new INPUT(); i.type=1; i.u.ki.wVk=vk; if(up) i.u.ki.dwFlags=KEYUP; return i; }
  public static void CtrlV(){
    var a=new INPUT[]{ Key(0x11,false), Key(0x56,false), Key(0x56,true), Key(0x11,true) };
    SendInput((uint)a.Length,a,SZ);
  }
}
'@
Add-Type -TypeDefinition $sig

$h = [SI]::FindWindow($null, "Desktop Pet")
if ($h -ne [IntPtr]::Zero) { [SI]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 200 }

if (-not $ClickOnly) { [System.Windows.Forms.Clipboard]::SetText($Text); Start-Sleep -Milliseconds 150 }

[SI]::Click($InX,$InY)
Start-Sleep -Milliseconds 350
if (-not $ClickOnly) {
  [SI]::CtrlV()
  Start-Sleep -Milliseconds 400
  Write-Host "sendinput-ctrlv done; clip-len=$($Text.Length)"
} else {
  Write-Host "click-only done at ($InX,$InY)"
}
