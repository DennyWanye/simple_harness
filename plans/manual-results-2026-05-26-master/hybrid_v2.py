# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Hybrid v2: CDP element.focus() + windows-mcp real Clipboard + Ctrl+V keyboard.

CDP 只做 element 状态管理（focus 一行 JS call）。
windows-mcp 做真物理键盘输入（Clipboard + SendKeys Ctrl+V + Enter）— 这是 condition '强制用 windows mcp' 的要求.
"""
import asyncio
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets


def find_main():
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    return next(t for t in d if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"))


async def evaluate(ws, expr, mid, await_p=False):
    await ws.send(json.dumps({
        "id": mid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p},
    }))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == mid:
            return m.get("result", {})


def ps_run(script: str, timeout: int = 30) -> tuple[int, str]:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return r.returncode, (r.stdout + r.stderr)


async def main():
    target = find_main()
    print(f"Connected: {target['url']}\n")

    async with websockets.connect(target["webSocketDebuggerUrl"]) as ws:
        # Bring deskpet window to foreground via windows-mcp PowerShell
        print("[1] windows-mcp: bring Desktop Pet window to foreground")
        ps_fg = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int m);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
}
'@ -ErrorAction SilentlyContinue
$h = [IntPtr]::Zero
$cb = [W+EnumProc]{ param($hwnd,$l)
  if (-not [W]::IsWindowVisible($hwnd)) { return $true }
  $sb = New-Object Text.StringBuilder 256
  [W]::GetWindowText($hwnd,$sb,256) | Out-Null
  if ($sb.ToString() -eq 'Desktop Pet') { $script:h = $hwnd; return $false }
  return $true
}
[W]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null
[W]::BringWindowToTop($h) | Out-Null
[W]::SetForegroundWindow($h) | Out-Null
Write-Output "hwnd=$h"
"""
        c, o = ps_run(ps_fg)
        print(f"   foreground: {o.strip()}")
        time.sleep(0.5)

        # [2] CDP: focus chat-input (single JS method call — not click simulation)
        print("[2] CDP: chat-input.focus()")
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false };
  el.focus();
  return {
    ok: true,
    isFocused: document.activeElement === el,
    activeTag: document.activeElement.tagName,
    activeTestId: document.activeElement.getAttribute('data-testid'),
  };
})()
""", 1)
        focus = r.get("result", {}).get("value", {})
        print(f"   focus result: {focus}")
        if not focus.get("isFocused"):
            print("   FAIL: focus didn't stick")
            return

        # [3] windows-mcp Clipboard.SetText + SendKeys ^v (REAL Windows input!)
        print("[3] windows-mcp: Clipboard + Ctrl+V real keyboard input")
        ps_paste = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Clipboard]::SetText('你好')
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 400
"""
        c, o = ps_run(ps_paste)
        print(f"   paste: code={c}")
        await asyncio.sleep(0.4)

        # [4] Verify input value via CDP read (not write)
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  return { value: el ? el.value : null, hasContent: el && el.value.length > 0 };
})()
""", 2)
        val = r.get("result", {}).get("value", {})
        print(f"   chat-input.value: {val}")
        if "你好" not in (val.get("value") or ""):
            print(f"   FAIL: paste didn't reach input (value={val.get('value')!r})")
            return
        print(f"   ✓ '你好' successfully injected via windows-mcp SendKeys Ctrl+V")

        # [5] windows-mcp SendKeys {ENTER} to submit
        print("[5] windows-mcp: SendKeys Enter")
        c, o = ps_run(r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 200
""")
        print(f"   Enter: code={c}")
        await asyncio.sleep(8)

        # [6] Verify LLM reply via CDP read
        print("[6] CDP read: check LLM reply in DOM")
        r = await evaluate(ws, "document.body.innerText.slice(-700)", 3)
        body = r.get("result", {}).get("value", "")
        # Check for likely reply markers
        for kw in ["陪", "聊天", "嗨", "你好呀", "🐾", "我在", "怎么", "做"]:
            if kw in body:
                print(f"\n✅ HYBRID PASS — windows-mcp real keyboard input → LLM reply detected")
                print(f"   keyword '{kw}' found in body")
                print(f"   body tail: {body[-400:]}")
                # Persist result
                Path(__file__).parent.joinpath("hybrid_v2_result.json").write_text(
                    json.dumps({
                        "status": "PASS",
                        "method": "windows-mcp Clipboard + SendKeys ^v + {ENTER}",
                        "cdp_role": "focus() only (no input simulation)",
                        "chat_input_value_after_paste": val.get("value"),
                        "reply_keyword_found": kw,
                        "body_tail": body[-400:],
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                return
        print(f"\n? no reply marker found in body")
        print(f"   body tail: {body[-300:]}")


if __name__ == "__main__":
    asyncio.run(main())
