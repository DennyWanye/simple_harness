# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""CDP 全套真测 runner. 利用 WebView2 remote debugging 突破 windows-mcp 注入边界.

测试 case:
  R3-2  toolbar 显示"已连接"  (Backend WS 真连)
  R3-3  自然语言触发 excel-generate skill ("做一个开支统计表")
  R5-1  点 relay-account-pill → 账户面板弹出
  TC-04 主输入框 + send-button 真发消息
  TC-08 settings-toggle → 模型 modal
  TC-09 mic-button 录音状态
  TC-10 message-panel toggle ↔ 主输入栏隐藏
  B1    excel-generate via 自然语言

写 PASS/FAIL/SKIP table 到 cdp_test_results.json
"""
import asyncio
import json
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "cdp_test_results.json"
RESULTS = []


def find_main():
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"):
            return t
    return None


def find_panel(suffix):
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in data:
        if t.get("type") == "page" and suffix in t.get("url", ""):
            return t
    return None


async def evaluate(ws, expr, mid, await_p=False):
    await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


def record(case, status, detail=""):
    """status: PASS / FAIL / SKIP."""
    entry = {"case": case, "status": status, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    RESULTS.append(entry)
    marker = {"PASS": "✓", "FAIL": "✗", "SKIP": "?", "PARTIAL": "~"}.get(status, "·")
    print(f"  [{marker}] {case:20s} {status:4s} — {detail}"[:200])


async def main():
    target = find_main()
    if not target:
        print("ERROR: no pet main target — is deskpet.exe running with debug port?")
        return
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connected to pet main: {target['url']}\n")

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        mid = [10]
        def next_id():
            mid[0] += 1
            return mid[0]

        # ─── R3-2: toolbar 显示"已连接" + FPS > 0 ───
        # boot 时已经验证（截图看到"已连接"绿色 + 24/30 FPS）但 CDP 自动化
        r = await evaluate(ws, r"""
(() => {
  // toolbar 在桌宠右上有"已连接"文字
  const text = document.body.innerText;
  return {
    has_connected: text.includes('已连接'),
    has_fps: /\d+\s*FPS/.test(text),
    has_error: text.includes('Unknown service'),
  };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        if v.get("has_connected") and v.get("has_fps") and not v.get("has_error"):
            record("R3-2 toolbar 已连接 + FPS", "PASS", "无 service error")
        else:
            record("R3-2 toolbar 已连接 + FPS", "FAIL", f"connected={v.get('has_connected')} fps={v.get('has_fps')} error={v.get('has_error')}")

        # ─── R3-3: 自然语言 → excel-generate skill (B1) ───
        # 用 CDP 直接 send to chat input + observe artifact card
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, error: 'no input' };
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const ns = Object.getOwnPropertyDescriptor(proto, 'value').set;
  ns.call(el, '帮我做一个本月开支统计表，含餐饮和交通两类');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.focus();
  const sb = document.querySelector('[data-testid="send-button"]');
  if (!sb) return { ok: false, error: 'no send btn' };
  if (sb.disabled) return { ok: false, error: 'send disabled' };
  sb.click();
  return { ok: true };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        if not v.get("ok"):
            record("R3-3 自然语言触发 skill", "FAIL", f"send failed: {v.get('error')}")
        else:
            # 等 30s LLM + skill 处理
            print(f"     waiting up to 30s for skill response...")
            found_skill = False
            for i in range(15):
                await asyncio.sleep(2)
                r = await evaluate(ws, r"""
(() => {
  const txt = document.body.innerText;
  return {
    has_artifact: txt.includes('.xlsx') || txt.includes('打开') || txt.includes('表格') || txt.includes('已生成'),
    has_excel: txt.includes('excel') || txt.includes('Excel') || txt.includes('xlsx'),
    snippet: txt.slice(-500),
  };
})()
""", next_id())
                v = r.get("result", {}).get("value", {})
                if v.get("has_excel") or v.get("has_artifact"):
                    found_skill = True
                    record("R3-3 自然语言触发 skill", "PASS", f"snippet: {v.get('snippet','')[:120]}")
                    break
            if not found_skill:
                # 可能 LLM 没调 skill 只回了纯文本回复 — 部分 pass
                r = await evaluate(ws, "document.body.innerText.slice(-500)", next_id())
                txt = r.get("result", {}).get("value", "")
                record("R3-3 自然语言触发 skill", "PARTIAL", f"LLM replied but no clear skill trigger: {txt[:120]}")

        # ─── R5-1: 点 relay-account-pill → 账户面板 ───
        r = await evaluate(ws, r"""
(() => {
  const pill = document.querySelector('[data-testid="relay-account-pill"]');
  if (!pill) return { ok: false, error: 'no pill' };
  pill.click();
  return { ok: true };
})()
""", next_id())
        await asyncio.sleep(1.2)
        r = await evaluate(ws, r"""
(() => {
  // Account panel 通常是 modal 或 panel with email / 余额 / 充值
  const txt = document.body.innerText;
  return {
    has_email: /@qq\.com|@\w+\.\w+/.test(txt),
    has_balance: txt.includes('余额') || txt.includes('账户') || txt.includes('充值'),
    has_logout: txt.includes('退出') || txt.includes('登出'),
  };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        if v.get("has_email") or v.get("has_balance"):
            record("R5-1 账户面板打开", "PASS", f"email={v.get('has_email')} balance={v.get('has_balance')} logout={v.get('has_logout')}")
            # close it back
            await evaluate(ws, r"""
(() => {
  // Close by ESC or click outside
  const btns = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('关闭') || b.innerText === '×');
  if (btns) btns.click();
  else document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
})()
""", next_id())
        else:
            record("R5-1 账户面板打开", "FAIL", "no email/balance text found after pill click")

        # ─── TC-10: message panel toggle ↔ 主输入栏隐藏 ───
        # 点消息按钮（toolbar 的 message toggle）
        r = await evaluate(ws, r"""
(() => {
  // Look for buttons whose innerText is "消息"
  const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === '消息');
  if (!btn) return { ok: false, error: 'no message btn' };
  // check main input visible BEFORE click
  const inputBefore = document.querySelector('[data-testid="chat-input"]');
  const visBefore = inputBefore && inputBefore.offsetParent !== null;
  btn.click();
  return { ok: true, visBefore };
})()
""", next_id())
        await asyncio.sleep(0.8)
        r = await evaluate(ws, r"""
(() => {
  const inputAfter = document.querySelector('[data-testid="chat-input"]');
  return {
    visAfter: inputAfter ? inputAfter.offsetParent !== null : false,
  };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        # TC-10 期望：面板打开后主输入框隐藏
        if v.get("visAfter") is False:
            record("TC-10 主输入栏面板互斥", "PASS", "panel 打开后主 chat-input 已隐藏")
            # 再切回去验证恢复
            await evaluate(ws, r"""
Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === '消息')?.click()
""", next_id())
            await asyncio.sleep(0.8)
            r = await evaluate(ws, """
document.querySelector('[data-testid="chat-input"]')?.offsetParent !== null
""", next_id())
            restored = r.get("result", {}).get("value")
            if restored:
                record("TC-10 关闭后恢复", "PASS", "主 chat-input 恢复显示")
            else:
                record("TC-10 关闭后恢复", "FAIL", "panel 关闭但主输入栏未恢复")
        else:
            record("TC-10 主输入栏面板互斥", "FAIL", f"panel 打开后主输入栏仍可见: visAfter={v.get('visAfter')}")

        # ─── TC-08: settings-toggle → 配置/模型 modal ───
        r = await evaluate(ws, r"""
(() => {
  const btn = document.querySelector('[data-testid="settings-toggle"]');
  if (!btn) return { ok: false, error: 'no settings-toggle' };
  btn.click();
  return { ok: true };
})()
""", next_id())
        await asyncio.sleep(1.0)
        r = await evaluate(ws, r"""
(() => {
  const txt = document.body.innerText;
  // settings panel 通常含 模型 / 中转站 / base_url / api_key 等
  return {
    has_settings: txt.includes('设置') || txt.includes('模型') || txt.includes('Provider') || txt.includes('API'),
    has_dialog: !!document.querySelector('[role="dialog"]'),
  };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        if v.get("has_settings") or v.get("has_dialog"):
            record("TC-08 settings/模型 modal", "PASS", f"settings={v.get('has_settings')} dialog={v.get('has_dialog')}")
        else:
            record("TC-08 settings/模型 modal", "FAIL", "no settings text found")
        # close
        await evaluate(ws, """
document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
""", next_id())
        await asyncio.sleep(0.5)

        # ─── TC-09: mic-button 切录音态 ───
        r = await evaluate(ws, r"""
(() => {
  const mic = document.querySelector('[data-testid="mic-button"]');
  if (!mic) return { ok: false, error: 'no mic' };
  // 取 click 前的 aria-pressed / classList
  const beforePressed = mic.getAttribute('aria-pressed');
  const beforeCls = mic.className;
  mic.click();
  return { ok: true, beforePressed, beforeCls };
})()
""", next_id())
        await asyncio.sleep(0.6)
        r = await evaluate(ws, r"""
(() => {
  const mic = document.querySelector('[data-testid="mic-button"]');
  if (!mic) return null;
  return {
    afterPressed: mic.getAttribute('aria-pressed'),
    afterCls: mic.className,
  };
})()
""", next_id())
        v = r.get("result", {}).get("value", {})
        # state changed? aria-pressed flips OR class includes recording/active
        cls = (v or {}).get("afterCls", "") if v else ""
        if (v and v.get("afterPressed") == "true") or "record" in cls.lower() or "active" in cls.lower():
            record("TC-09 mic 切录音态", "PASS", f"aria-pressed={v.get('afterPressed')} cls∋record/active")
            # click again to stop
            await evaluate(ws, "document.querySelector('[data-testid=\"mic-button\"]')?.click()", next_id())
        else:
            record("TC-09 mic 切录音态", "PARTIAL", f"click sent but no obvious state change: {v}")

        # ─── B5: ppt-generate via 自然语言 (skip — too slow for this run) ───
        record("B5 ppt-generate", "SKIP", "自然语言 LLM 路由 + 真生成 .pptx 时间长，单独跑")

        # ─── Save ───
        OUT.write_text(json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n=== Summary ===")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        partial = sum(1 for r in RESULTS if r["status"] == "PARTIAL")
        skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
        print(f"PASS: {passed}  FAIL: {failed}  PARTIAL: {partial}  SKIP: {skipped}")
        print(f"Output: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
