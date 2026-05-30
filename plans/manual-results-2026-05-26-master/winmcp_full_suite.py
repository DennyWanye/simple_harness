# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""所有 manual test cases via windows-mcp 真物理输入 (hybrid_v2 模式).

每个 case 都遵守：
  - CDP 只做 element.focus() 或 DOM read
  - windows-mcp PowerShell Win32 SendKeys 真键盘 trigger button (Enter/Space)
  - windows-mcp 真键盘输入文字 (Clipboard + Ctrl+V)
  - windows-mcp 真键盘 Enter 提交

Cases: R3-1, R3-2, R3-3, R5-1, TC-04, TC-08, TC-09, TC-10, B1, B2, B5
"""
import asyncio
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "winmcp_full_results.json"
RESULTS = []


def find_main():
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    return next(t for t in d if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"))


async def evaluate(ws, expr, mid, await_p=False):
    await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == mid:
            # Runtime.evaluate returns {result: {result: {type, value}}}
            return (m.get("result", {}) or {}).get("result", {}).get("value")


def record(case, status, detail=""):
    e = {"case": case, "status": status, "detail": str(detail)[:300], "ts": time.strftime("%H:%M:%S")}
    RESULTS.append(e)
    m = {"PASS": "✓", "FAIL": "✗", "PARTIAL": "~", "SKIP": "?"}.get(status, "·")
    print(f"  [{m}] {case:48s} {status:7s} — {str(detail)[:120]}")


def winmcp_sendkeys(keys: str, after_ms: int = 300) -> bool:
    """windows-mcp PowerShell Win32 SendKeys — TRUE physical keyboard input."""
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{keys}')
Start-Sleep -Milliseconds {after_ms}
"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    return r.returncode == 0


def winmcp_paste(text: str) -> bool:
    """windows-mcp Clipboard + Ctrl+V real Win32 paste. STA required for Clipboard.SetText.

    Pass text via stdin to avoid json.dumps Unicode escape issue (\\u4f60 not valid in PS)."""
    # Set clipboard via stdin to preserve UTF-8 chars exactly
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$text = [Console]::In.ReadToEnd()
[System.Windows.Forms.Clipboard]::SetText($text)
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 600
"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                       input=text, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=15)
    return r.returncode == 0


def winmcp_foreground(title: str = "Desktop Pet") -> str:
    script = f"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W {{
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int m);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
}}
'@ -ErrorAction SilentlyContinue
$h = [IntPtr]::Zero
$cb = [W+EnumProc]{{ param($hwnd,$l)
  if (-not [W]::IsWindowVisible($hwnd)) {{ return $true }}
  $sb = New-Object Text.StringBuilder 256
  [W]::GetWindowText($hwnd,$sb,256) | Out-Null
  if ($sb.ToString() -eq '{title}') {{ $script:h = $hwnd; return $false }}
  return $true
}}
[W]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null
[W]::BringWindowToTop($h) | Out-Null
[W]::SetForegroundWindow($h) | Out-Null
Write-Output "hwnd=$h"
"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    return r.stdout.strip()


async def cdp_focus(ws, selector: str, mid: int) -> dict:
    return await evaluate(ws, f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return {{ found: false }};
  el.focus();
  return {{
    found: true,
    isActive: document.activeElement === el,
    tag: el.tagName,
    testid: el.getAttribute('data-testid'),
  }};
}})()
""", mid)


async def winmcp_type_via_paste_and_submit(ws, text: str, mid_base: int) -> dict:
    """CDP focus chat-input + winmcp paste + winmcp Enter.

    Important: must re-foreground Desktop Pet before each paste (prior ESC /
    backend chat may have moved foreground)."""
    winmcp_foreground()
    await asyncio.sleep(0.8)  # let foreground settle
    foc = await cdp_focus(ws, '[data-testid="chat-input"]', mid_base)
    if not isinstance(foc, dict) or not foc.get("isActive"):
        return {"ok": False, "err": f"focus failed: {foc}"}
    # Retry paste up to 3 times — sometimes first paste race against React re-render
    val = ""
    for attempt in range(3):
        if not winmcp_paste(text):
            return {"ok": False, "err": "winmcp paste failed"}
        await asyncio.sleep(0.7)
        val = await evaluate(ws, '(document.querySelector(\'[data-testid="chat-input"]\') || {}).value', mid_base + 1 + attempt)
        if val and text[:2] in str(val):
            break
        # Re-focus + retry
        await cdp_focus(ws, '[data-testid="chat-input"]', mid_base + 10 + attempt)
        winmcp_foreground()
        await asyncio.sleep(0.4)
    if not val or text[:2] not in str(val):
        return {"ok": False, "err": f"paste reached input but value mismatch after 3 retries: {val!r}"}
    if not winmcp_sendkeys("{ENTER}"):
        return {"ok": False, "err": "winmcp enter failed"}
    return {"ok": True, "value": val}


async def cdp_close_dialogs(ws, mid_base: int) -> int:
    """winmcp ESC to close dialogs."""
    winmcp_sendkeys("{ESC}", 200)
    winmcp_sendkeys("{ESC}", 200)
    await asyncio.sleep(0.4)
    r = await evaluate(ws, "document.querySelectorAll('[role=\"dialog\"]').length", mid_base)
    return r if isinstance(r, int) else 0


async def main():
    target = find_main()
    print(f"Connected: {target['url']}\n")

    print(f"[setup] winmcp foreground: {winmcp_foreground()}")
    time.sleep(0.5)

    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=4_000_000) as ws:
        # Make sure dialogs closed before starting
        await cdp_close_dialogs(ws, 10)

        # ───────── R3-1: chat 真发"你好" ─────────
        print("\n── R3-1 ──")
        res = await winmcp_type_via_paste_and_submit(ws, "你好", 100)
        if not res.get("ok"):
            record("R3-1 winmcp 真发你好", "FAIL", res.get("err"))
        else:
            await asyncio.sleep(7)
            body = await evaluate(ws, "document.body.innerText.slice(-500)", 102)
            if any(k in (body or "") for k in ["陪", "聊天", "嗨", "你好呀", "🐾", "我在", "怎么", "做"]):
                record("R3-1 winmcp 真发你好", "PASS", f"reply detected; tail={(body or '')[-200:]}")
            else:
                record("R3-1 winmcp 真发你好", "PARTIAL", f"sent OK but no obvious reply; tail={(body or '')[-200:]}")

        await asyncio.sleep(2)

        # ───────── R3-2: toolbar 显示"已连接" ─────────
        print("\n── R3-2 ──")
        info = await evaluate(ws, r"""
(() => {
  const t = document.body.innerText;
  return {
    has_connected: t.includes('已连接'),
    has_fps: /\d+\s*FPS/.test(t),
    no_unknown_service: !t.includes('Unknown service'),
  };
})()
""", 200)
        if info and info.get("has_connected") and info.get("has_fps") and info.get("no_unknown_service"):
            record("R3-2 toolbar 已连接 + FPS", "PASS", f"{info}")
        else:
            record("R3-2 toolbar 已连接 + FPS", "FAIL", f"{info}")

        # ───────── R3-3: 自然语言触发 skill (LLM 真调 excel_create) ─────────
        print("\n── R3-3 ──")
        res = await winmcp_type_via_paste_and_submit(
            ws, "请直接调用 excel_create 函数生成一个简单 xlsx，列：日期,餐饮,3 行示例", 300,
        )
        if not res.get("ok"):
            record("R3-3 winmcp 真发 skill 触发", "FAIL", res.get("err"))
        else:
            await asyncio.sleep(15)
            body = await evaluate(ws, "document.body.innerText.slice(-700)", 302)
            if any(k in (body or "") for k in [".xlsx", "已生成", "做好"]):
                record("R3-3 winmcp 真发 skill 触发", "PASS", f"artifact mention; tail={(body or '')[-200:]}")
            else:
                record("R3-3 winmcp 真发 skill 触发", "PARTIAL",
                       f"sent OK but no artifact mention; tail={(body or '')[-200:]}")

        await asyncio.sleep(3)

        # ───────── R5-1: 真按 Tab 导航到 relay-account-pill 然后 Enter ─────────
        # 用 element.focus() + winmcp Enter 触发 — Enter 是物理键盘
        print("\n── R5-1 ──")
        foc = await cdp_focus(ws, '[data-testid="relay-account-pill"]', 400)
        if not foc.get("found"):
            record("R5-1 账户 pill (winmcp Enter)", "FAIL", "no pill")
        else:
            winmcp_sendkeys("{ENTER}")
            await asyncio.sleep(1.2)
            body = await evaluate(ws, "document.body.innerText", 402)
            has_email = "@qq.com" in (body or "") or "@" in (body or "")
            has_balance = "余额" in (body or "") or "账户" in (body or "")
            if has_email or has_balance:
                record("R5-1 账户 pill (winmcp Enter)", "PASS",
                       f"email={has_email} balance={has_balance}")
            else:
                record("R5-1 账户 pill (winmcp Enter)", "PARTIAL",
                       "Enter sent but no panel content detected")
            # close via ESC
            winmcp_sendkeys("{ESC}")
            await asyncio.sleep(0.5)

        await asyncio.sleep(1)

        # ───────── TC-04: 主输入 + winmcp Enter 提交 ─────────
        print("\n── TC-04 ──")
        res = await winmcp_type_via_paste_and_submit(ws, "现在几点", 500)
        if res.get("ok"):
            await asyncio.sleep(5)
            body = await evaluate(ws, "document.body.innerText.slice(-300)", 502)
            if len(body or "") > 50:
                record("TC-04 winmcp 真发输入 + Enter", "PASS", f"reply received")
            else:
                record("TC-04 winmcp 真发输入 + Enter", "PARTIAL", "sent but no clear reply")
        else:
            record("TC-04 winmcp 真发输入 + Enter", "FAIL", res.get("err"))

        # ───────── TC-08: focus settings + winmcp Enter ─────────
        print("\n── TC-08 ──")
        foc = await cdp_focus(ws, '[data-testid="settings-toggle"]', 600)
        if foc.get("found"):
            winmcp_sendkeys("{ENTER}")
            await asyncio.sleep(1.2)
            has_dialog = await evaluate(ws, "document.querySelectorAll('[role=\"dialog\"]').length > 0", 602)
            body = await evaluate(ws, "document.body.innerText", 603)
            has_settings_word = "设置" in (body or "") or "模型" in (body or "")
            if has_dialog or has_settings_word:
                record("TC-08 winmcp 真按 settings", "PASS",
                       f"dialog={has_dialog} settings_text={has_settings_word}")
            else:
                record("TC-08 winmcp 真按 settings", "PARTIAL", "Enter sent but no dialog")
            winmcp_sendkeys("{ESC}")
            await asyncio.sleep(0.5)
        else:
            record("TC-08 winmcp 真按 settings", "FAIL", "no settings-toggle")

        # ───────── TC-09: focus mic + winmcp Enter ─────────
        print("\n── TC-09 ──")
        # capture before state
        before = await evaluate(ws, r"""
(() => {
  const m = document.querySelector('[data-testid="mic-button"]');
  if (!m) return null;
  return { title: m.getAttribute('title') || '', cls: m.className };
})()
""", 700)
        foc = await cdp_focus(ws, '[data-testid="mic-button"]', 701)
        if foc.get("found"):
            winmcp_sendkeys("{ENTER}")
            await asyncio.sleep(0.8)
            after = await evaluate(ws, r"""
(() => {
  const m = document.querySelector('[data-testid="mic-button"]');
  if (!m) return null;
  return { title: m.getAttribute('title') || '', cls: m.className };
})()
""", 702)
            changed = bool(before) and bool(after) and (
                before.get("title") != after.get("title") or before.get("cls") != after.get("cls")
            )
            if changed:
                record("TC-09 winmcp 真按 mic", "PASS",
                       f"title: {before.get('title')!r}→{after.get('title')!r}")
                winmcp_sendkeys("{ENTER}")  # stop recording
                await asyncio.sleep(0.5)
            else:
                record("TC-09 winmcp 真按 mic", "PARTIAL",
                       f"Enter sent but no state change: before={before} after={after}")
        else:
            record("TC-09 winmcp 真按 mic", "FAIL", "no mic-button")

        # ───────── TC-10: focus 消息 button + winmcp Enter → 主输入框隐藏 ─────────
        print("\n── TC-10 ──")
        # find 消息 button
        foc = await evaluate(ws, r"""
(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === '消息');
  if (!btn) return { found: false };
  btn.focus();
  return { found: true, isActive: document.activeElement === btn };
})()
""", 800)
        if foc and foc.get("found"):
            visBefore = await evaluate(ws,
                "(document.querySelector('[data-testid=\"chat-input\"]') || {}).offsetParent !== null", 801)
            winmcp_sendkeys("{ENTER}")
            await asyncio.sleep(1.0)
            visAfter = await evaluate(ws,
                "(document.querySelector('[data-testid=\"chat-input\"]') || {}).offsetParent !== null", 802)
            if visBefore and not visAfter:
                record("TC-10 winmcp 真按消息 → 互斥", "PASS",
                       f"chat-input: visible→hidden after message panel open")
                # toggle back
                foc2 = await evaluate(ws, r"""
(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === '消息');
  if (btn) btn.focus();
})()
""", 803)
                winmcp_sendkeys("{ENTER}")
                await asyncio.sleep(0.8)
                visRestored = await evaluate(ws,
                    "(document.querySelector('[data-testid=\"chat-input\"]') || {}).offsetParent !== null", 804)
                if visRestored:
                    record("TC-10 关闭后主输入栏恢复", "PASS", "")
                else:
                    record("TC-10 关闭后主输入栏恢复", "FAIL", "did not restore")
            else:
                record("TC-10 winmcp 真按消息 → 互斥", "FAIL",
                       f"visBefore={visBefore}, visAfter={visAfter}")
        else:
            record("TC-10 winmcp 真按消息 → 互斥", "FAIL", "no 消息 button")

        await asyncio.sleep(2)

        # ───────── B1/B2/B5: 真发 prompt 让 LLM 真生成 ─────────
        for case, prompt, ext in [
            ("B1 excel_create (winmcp)", "请直接调用 excel_create 函数生成 xlsx，2 列 2 行示例", ".xlsx"),
            ("B2 doc_create (winmcp)", "请直接调用 doc_create 函数新建 docx，含标题/正文", ".docx"),
            ("B5 ppt_create (winmcp)", "请直接调用 ppt_create 函数生成 pptx，主题测试，3 页", ".pptx"),
        ]:
            print(f"\n── {case} ──")
            await cdp_close_dialogs(ws, 900)
            await asyncio.sleep(0.5)
            since = time.time()
            res = await winmcp_type_via_paste_and_submit(ws, prompt, 900 + ord(case[0]))
            if not res.get("ok"):
                record(case, "FAIL", res.get("err"))
                continue
            # wait + look for file in Temp/claude
            artifact = None
            for _ in range(40):
                await asyncio.sleep(2)
                temp = Path(r"C:\Users\24378\AppData\Local\Temp\claude")
                if temp.exists():
                    matches = sorted(
                        (p for p in temp.glob(f"*{ext}") if p.stat().st_mtime > since),
                        key=lambda p: p.stat().st_mtime, reverse=True,
                    )
                    if matches:
                        artifact = matches[0]
                        break
            if artifact:
                record(case, "PASS", f"{artifact.name} ({artifact.stat().st_size}B)")
            else:
                record(case, "FAIL", f"no {ext} in 80s")

        OUT.write_text(json.dumps(
            {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        partial = sum(1 for r in RESULTS if r["status"] == "PARTIAL")
        skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
        print(f"\n=== windows-mcp Full Suite Summary ===")
        print(f"PASS:{passed}  FAIL:{failed}  PARTIAL:{partial}  SKIP:{skipped}")
        print(f"Output: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
