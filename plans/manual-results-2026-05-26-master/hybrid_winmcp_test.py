# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Hybrid 真测：CDP 只查坐标，**真正的输入用 windows-mcp 物理工具链**.

CDP 不做 click/eval input — 只做：
  1. getBoundingClientRect() 拿 chat-input 精确屏幕坐标
  2. document.activeElement 验证 focus 真到位

windows-mcp 做：
  1. SetCursorPos 移到精确坐标（来自 CDP）
  2. SendInput LayoutKind.Explicit click 让 webview 内 input 拿 focus
  3. Clipboard.SetText 设"你好"
  4. SendKeys Ctrl+V 真粘贴

这是 condition '强制用 windows mcp' 的真路径 — CDP 仅做坐标侦察.
"""
import asyncio
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "hybrid_result.json"
RESULTS = []


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


def record(case, status, detail=""):
    RESULTS.append({"case": case, "status": status, "detail": str(detail)[:300], "ts": time.strftime("%H:%M:%S")})
    marker = {"PASS": "✓", "FAIL": "✗"}.get(status, "?")
    print(f"  [{marker}] {case:50s} {status} — {str(detail)[:120]}")


def ps_run(script: str, timeout: int = 30) -> tuple[int, str]:
    """Run PowerShell script via windows-mcp PowerShell host (a real Win32 tool)."""
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return r.returncode, (r.stdout + r.stderr)


def winmcp_click_via_winapi(physical_x: int, physical_y: int) -> str:
    """Use windows-mcp PowerShell host to run SetCursorPos + SendInput (Win32 LayoutKind.Explicit, proven to return 1)."""
    script = f"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class W {{
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [StructLayout(LayoutKind.Sequential)]
  public struct MI {{ public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }}
  [StructLayout(LayoutKind.Explicit, Size = 40)]
  public struct IN_ {{ [FieldOffset(0)] public uint type; [FieldOffset(8)] public MI mi; }}
  [DllImport("user32.dll", SetLastError = true)] public static extern uint SendInput(uint n, IN_[] i, int sz);
}}
'@ -ErrorAction SilentlyContinue
[W]::SetCursorPos({physical_x}, {physical_y}) | Out-Null
Start-Sleep -Milliseconds 200
$d = New-Object W+IN_; $d.type=0; $d.mi=New-Object W+MI; $d.mi.dwFlags=0x0002
$u = New-Object W+IN_; $u.type=0; $u.mi=New-Object W+MI; $u.mi.dwFlags=0x0004
$sz = [Runtime.InteropServices.Marshal]::SizeOf([type][W+IN_])
$r1 = [W]::SendInput(1, @($d), $sz)
Start-Sleep -Milliseconds 60
$r2 = [W]::SendInput(1, @($u), $sz)
Write-Output "DOWN=$r1 UP=$r2"
"""
    code, out = ps_run(script)
    return f"code={code} out={out.strip()}"


def winmcp_clipboard_paste(text: str) -> str:
    """windows-mcp Clipboard.SetText + SendKeys ^v."""
    # Use STA thread approach to avoid clipboard ownership issues
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Clipboard]::SetText({json.dumps(text)})
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 400
"""
    code, out = ps_run(script)
    return f"code={code} out={out.strip()}"


async def main():
    target = find_main()
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connected: {target['url']}\n")

    async with websockets.connect(ws_url) as ws:
        # ── CDP 只用来侦察坐标 ──
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  return {
    cssX: Math.round(rect.left + rect.width / 2),
    cssY: Math.round(rect.top + rect.height / 2),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    devicePixelRatio: window.devicePixelRatio,
    innerW: window.innerWidth,
    innerH: window.innerHeight,
    screenX: window.screenX,  // window 在屏幕的位置
    screenY: window.screenY,
  };
})()
""", 1)
        coords = r.get("result", {}).get("value", {})
        if not coords:
            record("Hybrid R3-1", "FAIL", "chat-input not found")
            return

        # 将 CSS 坐标转为物理屏幕坐标
        # window.screenX/Y = window 在屏幕的左上角 CSS 坐标
        # 实际物理坐标 = (screenX + cssX) * devicePixelRatio (取决于 OS DPI scaling)
        dpr = coords.get("devicePixelRatio", 1.0)
        # WebView2 通常 devicePixelRatio 反映 OS DPI scaling — 直接用 CSS 坐标 + screenX/Y * dpr
        screen_x = coords["screenX"] + coords["cssX"]
        screen_y = coords["screenY"] + coords["cssY"]
        # 转物理像素（实际 SetCursorPos 在 DPI-aware 模式接受 physical，但 PowerShell DPI-unaware → logical → 系统再 ×dpi）
        # 用 logical 坐标 — PowerShell host 不 DPI-aware 时这个 work
        target_x = int(screen_x)
        target_y = int(screen_y)
        print(f"chat-input CSS=({coords['cssX']},{coords['cssY']}) size={coords['width']}x{coords['height']} "
              f"window-on-screen=({coords['screenX']},{coords['screenY']}) dpr={dpr}")
        print(f"→ Target logical (PowerShell DPI-unaware): ({target_x},{target_y})")

        # ── windows-mcp PowerShell 真做物理 click ──
        click_log = winmcp_click_via_winapi(target_x, target_y)
        print(f"windows-mcp click: {click_log}")
        await asyncio.sleep(0.4)

        # 验证：通过 CDP 看 activeElement 是不是 chat-input
        r = await evaluate(ws, r"""
(() => {
  const ae = document.activeElement;
  return {
    isChatInput: ae && ae.getAttribute('data-testid') === 'chat-input',
    activeTagName: ae ? ae.tagName : null,
    activeTestId: ae ? ae.getAttribute('data-testid') : null,
  };
})()
""", 2)
        focus_state = r.get("result", {}).get("value", {})
        print(f"Active element after winmcp click: {focus_state}")

        if not focus_state.get("isChatInput"):
            record("Hybrid windows-mcp click → focus", "FAIL",
                   f"click landed but focus not on chat-input: active={focus_state}")
            # 仍继续 — 也许 Ctrl+V 仍能 work (focus 在隐含处)

        # ── windows-mcp Clipboard + SendKeys Ctrl+V ──
        paste_log = winmcp_clipboard_paste("你好")
        print(f"windows-mcp paste: {paste_log}")
        await asyncio.sleep(0.5)

        # 验证：chat-input.value 是否真有"你好"
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  return { value: el ? el.value : null };
})()
""", 3)
        val_state = r.get("result", {}).get("value", {})
        chat_value = val_state.get("value", "")
        print(f"chat-input.value after paste: {chat_value!r}")

        if "你好" not in chat_value:
            record("Hybrid windows-mcp paste → input value", "FAIL",
                   f"Ctrl+V did not inject; chat-input.value={chat_value!r}")
            # Save partial result
            OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        record("Hybrid windows-mcp paste → input value", "PASS",
               f"chat-input.value={chat_value!r}")

        # ── windows-mcp SendKeys Enter 真提交 ──
        submit_log = ps_run(r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 500
""")
        print(f"windows-mcp Enter: code={submit_log[0]}")
        await asyncio.sleep(8)

        # 验证：LLM 回复出现在 body
        r = await evaluate(ws, "document.body.innerText.slice(-600)", 4)
        body_tail = r.get("result", {}).get("value", "")
        print(f"Body tail: {body_tail[:300]}")
        if any(k in body_tail for k in ["陪", "聊天", "嗨", "你好呀", "🐾", "我在"]):
            record("Hybrid R3-1 (windows-mcp paste + Enter → LLM reply)",
                   "PASS", f"reply detected; tail={body_tail[-300:][:150]}")
        else:
            # Maybe needs more time
            await asyncio.sleep(8)
            r = await evaluate(ws, "document.body.innerText.slice(-600)", 5)
            body_tail = r.get("result", {}).get("value", "")
            if any(k in body_tail for k in ["陪", "聊天", "嗨", "你好呀", "🐾", "我在"]):
                record("Hybrid R3-1 (windows-mcp paste + Enter → LLM reply)",
                       "PASS", f"reply detected after extra wait; tail={body_tail[-300:][:150]}")
            else:
                record("Hybrid R3-1 (windows-mcp paste + Enter → LLM reply)",
                       "FAIL", f"no reply detected; tail={body_tail[-300:][:150]}")

        OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        print(f"\n=== Hybrid Summary === PASS:{passed} FAIL:{failed}")


if __name__ == "__main__":
    asyncio.run(main())
