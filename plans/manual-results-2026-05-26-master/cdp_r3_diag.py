"""R3-1 诊断：看 chat-input 的 React state、check input value, look for messages in DOM."""
import asyncio
import json
import urllib.request

import websockets

CDP = "127.0.0.1:9222"


def find_main():
    data = json.loads(urllib.request.urlopen(f"http://{CDP}/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"):
            return t
    return None


async def evaluate(ws, expr, mid=1, await_p=False):
    await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


async def main():
    target = find_main()
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connecting: {target['url']}")
    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        # Now also enable Network domain so we can see WS messages
        await ws.send(json.dumps({"id": 1000, "method": "Runtime.enable"}))
        await ws.recv()

        # 1) Look at current chat-input value + entire visible body text
        diag = r"""
(() => {
  const out = {};
  const ci = document.querySelector('[data-testid="chat-input"]');
  out.chat_input_value = ci ? ci.value : null;
  out.chat_input_placeholder = ci ? ci.placeholder : null;
  const sb = document.querySelector('[data-testid="send-button"]');
  out.send_button_disabled = sb ? sb.disabled : null;

  // Look for any visible message containers
  const candidates = Array.from(document.querySelectorAll('div,p,span'));
  out.matches = [];
  for (const el of candidates) {
    if (el.offsetParent === null) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length < 2 || t.length > 500) continue;
    if (t.includes('你好') || t.includes('陪') || t.includes('聊') || t.includes('哦')
        || t.includes('我') || t.includes('能') || t.includes('帮')) {
      // skip placeholders + button labels
      if (t === '和桌宠说点什么...' || t === '发送' || t === '消息') continue;
      out.matches.push({ text: t.slice(0, 200), tag: el.tagName });
      if (out.matches.length >= 10) break;
    }
  }

  // Dump everything visible to spot dialog-bar / bubble
  const all = Array.from(document.querySelectorAll('[class*="bubble"], [class*="dialog"], [class*="message"], [class*="reply"], [class*="bar"]'));
  out.styled = all.slice(0, 10).map(el => ({
    tag: el.tagName,
    cls: el.className,
    text: (el.innerText || '').slice(0, 100),
    visible: el.offsetParent !== null,
  }));

  // Window state - any React error boundary
  out.error_overlays = Array.from(document.querySelectorAll('[role="alert"], [class*="error"]'))
    .filter(el => el.offsetParent !== null)
    .map(el => ({ text: (el.innerText || '').slice(0, 200) }));

  return out;
})()
"""
        r = await evaluate(ws, diag, 2)
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 2) Try send again — make sure value still set
        send_again = r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, err: 'no input' };
  if (!el.value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const ns = Object.getOwnPropertyDescriptor(proto, 'value').set;
    ns.call(el, '你好');
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const sb = document.querySelector('[data-testid="send-button"]');
  if (!sb) return { ok: false, err: 'no send btn' };
  if (sb.disabled) return { ok: false, err: 'send disabled', value: el.value };
  sb.click();
  return { ok: true, value_was: el.value, value_after: el.value };
})()
"""
        r2 = await evaluate(ws, send_again, 3)
        print("\nSend again:")
        print(json.dumps(r2, ensure_ascii=False, indent=2))

        # 3) Wait 8s, then dump entire body innerText for evidence
        await asyncio.sleep(8)
        dump = """
document.body.innerText.slice(0, 2000)
"""
        r3 = await evaluate(ws, dump, 4)
        print("\nBody text (first 2000 chars):")
        print(r3.get("result", {}).get("value", "")[:2000])


if __name__ == "__main__":
    asyncio.run(main())
