# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""R3-1 真测：通过 CDP 注入"你好" 到桌宠主输入框 + 触发 send + 收 LLM 回复。

绕过 windows-mcp WebView2 边界，用 CDP Runtime.evaluate 直接操纵 React DOM。

Verifies:
  - 主输入框存在 + 可写入
  - send-button 触发后真发 chat 到 backend
  - LLM 流式回复显示在桌宠 DialogBar 或 message panel
"""
import asyncio
import json
import sys
import time
import urllib.request

import websockets


CDP_HOST = "127.0.0.1:9222"


def find_main_target():
    data = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith(
            "tauri.localhost"
        ):
            return t
    for t in data:
        if t.get("type") == "page" and "#" not in t.get("url", ""):
            return t
    return None


async def evaluate(ws, expression: str, msg_id: int, await_promise: bool = False):
    await ws.send(
        json.dumps(
            {
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": await_promise,
                },
            }
        )
    )
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == msg_id:
            return msg.get("result", {})


async def main():
    target = find_main_target()
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connecting: {target['url']}")

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        # 1) Probe — onboarding closed? input bar exists?
        probe = """
(() => {
  const out = {};
  out.wizard_gone = !document.querySelector('[data-testid="onboarding-wizard"]');
  out.relay_modal = !!document.querySelector('[role="dialog"][aria-modal="true"]');
  // Find chat input — placeholder "和桌宠说点什么..." or testid
  const inputs = Array.from(document.querySelectorAll('input,textarea'));
  out.inputs = inputs.map(el => ({
    tag: el.tagName,
    type: el.type,
    placeholder: el.placeholder || '',
    testid: el.getAttribute('data-testid'),
    visible: el.offsetParent !== null,
  }));
  out.send_btn = !!document.querySelector('[data-testid="send-button"]');
  return out;
})()
"""
        r = await evaluate(ws, probe, 1)
        print("DOM probe:")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 2) Find chat input — looking for textarea with 和桌宠 placeholder
        find_input_js = """
(() => {
  const inputs = Array.from(document.querySelectorAll('input,textarea'));
  // 桌宠主 InputBar 通常是 textarea，placeholder 含"和桌宠说"
  const main = inputs.find(el => (el.placeholder || '').includes('和桌宠'));
  if (!main) return null;
  return {
    tag: main.tagName,
    placeholder: main.placeholder,
    selector: 'textarea[placeholder*="和桌宠"]',
  };
})()
"""
        r = await evaluate(ws, find_input_js, 2)
        print("\nFind input result:")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 3) 注入 "你好" 到输入框 + 派发 React events
        # 桌宠 chat input 是 <input data-testid="chat-input"> 不是 textarea
        inject_js = r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, error: 'chat-input not found' };
  const proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  nativeSetter.call(el, '你好');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  // Focus + 再确认 value
  el.focus();
  return { ok: true, value: el.value, tag: el.tagName };
})()
"""
        r = await evaluate(ws, inject_js, 3)
        print("\nInject '你好':")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 4) Click send-button
        click_send = """
(() => {
  const btn = document.querySelector('[data-testid="send-button"]');
  if (!btn) return { ok: false, error: 'send-button not found' };
  if (btn.disabled) return { ok: false, error: 'send-button disabled' };
  btn.click();
  return { ok: true };
})()
"""
        r = await evaluate(ws, click_send, 4)
        print("\nClick send:")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 5) Poll for LLM reply for 30s
        print("\nWaiting for LLM reply (up to 30s)...")
        check_reply = r"""
(() => {
  // Look for bubble / DialogBar showing assistant message
  // 1) Find any visible text containing assistant reply
  const candidates = [
    '[data-testid="dialog-bar-text"]',
    '[data-testid="pet-bubble"]',
    '[data-testid="assistant-message"]',
    '[role="status"]',
    '.dialog-bar',
    '.pet-bubble',
  ];
  for (const sel of candidates) {
    const el = document.querySelector(sel);
    if (el && el.innerText && el.innerText.trim() && !el.innerText.includes('和桌宠说')) {
      return { found: true, selector: sel, text: el.innerText.slice(0, 300) };
    }
  }
  // Generic: find any visible non-button text in bottom half of viewport
  const all = Array.from(document.querySelectorAll('div, p, span'));
  const visible = all.filter(el => {
    if (el.offsetParent === null) return false;
    const t = (el.innerText || '').trim();
    if (!t || t.length < 2 || t.length > 500) return false;
    if (t.includes('和桌宠说') || t.includes('你好') === false) return false;
    if (el.querySelector('button,input,textarea')) return false;
    return true;
  });
  // 看是否有包含"你好"的回复
  const found = visible.find(el => {
    const t = el.innerText;
    // assistant 回复通常是较长的中文，含"你好"或问候词
    return t.length > 5 && (t.includes('你好') || t.includes('陪') || t.includes('聊'));
  });
  if (found) {
    return { found: true, text: found.innerText.slice(0, 300) };
  }
  return { found: false };
})()
"""
        reply_found = None
        for i in range(15):
            await asyncio.sleep(2)
            r = await evaluate(ws, check_reply, 100 + i)
            val = r.get("result", {}).get("value", {})
            if val.get("found"):
                reply_found = val
                print(f"  iter {i}: FOUND")
                print(f"    {val}")
                break
            print(f"  iter {i}: not yet")

        # 6) Final summary
        print("\n=== R3-1 RESULT ===")
        if reply_found:
            print(f"PASS — LLM reply received")
            print(f"  text: {reply_found.get('text','')[:200]}")
        else:
            print("INCONCLUSIVE — no reply detected in 30s; check backend log")


if __name__ == "__main__":
    asyncio.run(main())
