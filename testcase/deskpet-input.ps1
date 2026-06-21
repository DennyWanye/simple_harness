# DeskPet SendInput 圣杯 — 真 OS 鼠标点击 + 中文剪贴板粘贴 + 发送。
# WebView2/Chromium 忽略老式 mouse_event，必须用 SendInput。
# 用法:
#   powershell -File deskpet-input.ps1 -Action click -X 3450 -Y 1230
#   powershell -File deskpet-input.ps1 -Action send  -X 3450 -Y 1230 -Text "中文" -X2 3672 -Y2 1383
#   powershell -File deskpet-input.ps1 -Action paste -X 3450 -Y 1230 -Text "中文" -Enter
param(
  [string]$Action = "send",
  [int]$X = 0, [int]$Y = 0,
  [string]$Text = "",
  [int]$X2 = 0, [int]$Y2 = 0,
  [switch]$Enter
)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WInput {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v);
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
  public const uint MOUSE=0, KEYBD=1, LEFTDOWN=0x0002, LEFTUP=0x0004, KEYUP=0x0002;
  public static void Click(int x, int y){
    SetCursorPos(x,y); System.Threading.Thread.Sleep(60);
    var i=new INPUT[2];
    i[0].type=MOUSE; i[0].u.mi.dwFlags=LEFTDOWN;
    i[1].type=MOUSE; i[1].u.mi.dwFlags=LEFTUP;
    SendInput(2,i,Marshal.SizeOf(typeof(INPUT)));
  }
  public static void Key(ushort vk, bool ctrl){
    var list=new System.Collections.Generic.List<INPUT>();
    if(ctrl){ var d=new INPUT(); d.type=KEYBD; d.u.ki.wVk=0x11; list.Add(d); }     // Ctrl down
    var k1=new INPUT(); k1.type=KEYBD; k1.u.ki.wVk=vk; list.Add(k1);
    var k2=new INPUT(); k2.type=KEYBD; k2.u.ki.wVk=vk; k2.u.ki.dwFlags=KEYUP; list.Add(k2);
    if(ctrl){ var u=new INPUT(); u.type=KEYBD; u.u.ki.wVk=0x11; u.u.ki.dwFlags=KEYUP; list.Add(u); } // Ctrl up
    var arr=list.ToArray(); SendInput((uint)arr.Length,arr,Marshal.SizeOf(typeof(INPUT)));
  }
}
"@
try { [WInput]::SetProcessDpiAwareness(2) | Out-Null } catch {}

function Set-Clip([string]$t){
  if ([string]::IsNullOrEmpty($t)) { return }  # 空=用现有剪贴板(由 windows-mcp 预设)
  try { Set-Clipboard -Value $t } catch {}
}

switch ($Action) {
  "click" { [WInput]::Click($X,$Y); Write-Output "clicked ($X,$Y)" }
  "paste" {
    Set-Clip $Text
    [WInput]::Click($X,$Y); Start-Sleep -Milliseconds 250
    [WInput]::Key(0x56,$true)            # Ctrl+V
    if ($Enter) { Start-Sleep -Milliseconds 200; [WInput]::Key(0x0D,$false) }  # Enter
    Write-Output "pasted@($X,$Y) enter=$Enter"
  }
  "send" {
    Set-Clip $Text
    [WInput]::Click($X,$Y); Start-Sleep -Milliseconds 300
    [WInput]::Key(0x56,$true); Start-Sleep -Milliseconds 300   # Ctrl+V
    if ($X2 -gt 0 -and $Y2 -gt 0) { [WInput]::Click($X2,$Y2) } # 点发送按钮
    else { [WInput]::Key(0x0D,$false) }                        # 或回车
    Write-Output "sent@in($X,$Y) send($X2,$Y2) text-len=$($Text.Length)"
  }
}
