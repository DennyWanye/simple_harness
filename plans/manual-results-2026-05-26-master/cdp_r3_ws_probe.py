# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""R3-1 终极诊断：用 CDP 拿 webview 内的 ControlChannel 状态，
让它 send "你好" 并收 backend reply。

这绕过 UI input — 直接命令 React app 的 controlChannel 发消息。
"""
import asyncio
import json
import urllib.request

import websockets


def find_main():
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"):
            return t
    return None


async def evaluate(ws, expr, mid, await_p=False):
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
        # 1) 拿 secret + session_id from window (Tauri app 通常 expose to window)
        probe_secret = r"""
(() => {
  // Try common locations
  const candidates = [
    'window.__DESKPET_SECRET__',
    'window.SHARED_SECRET',
    'window.deskpet?.secret',
  ];
  for (const c of candidates) {
    try {
      const v = eval(c);
      if (v) return { found: c, value: String(v).slice(0, 64) };
    } catch (e) {}
  }
  // Else look in WebSocket-like global
  return { found: null, keys: Object.keys(window).filter(k => /secret|deskpet|tauri|ws/i.test(k)).slice(0, 30) };
})()
"""
        r = await evaluate(ws, probe_secret, 1)
        print("Secret probe:")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 2) Inject + try to find existing ControlChannel + send chat through it
        inject_chat = r"""
(async () => {
  // Strategy: find an open WS that goes to /ws/control and send chat through it
  // We can't directly access existing connections easily; instead create our own
  // WS by reading secret from URL/storage/cookies, or from any open WS we can sniff
  // via window.WebSocket monkey patching (too late now).
  //
  // Easier: use Tauri invoke (if it's a Tauri command path)
  if (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke) {
    try {
      const r = await window.__TAURI__.core.invoke('get_shared_secret');
      return { method: 'tauri_invoke', secret: String(r).slice(0, 64) };
    } catch (e) {
      return { method: 'tauri_invoke', error: String(e) };
    }
  }
  return { method: null, error: 'no tauri invoke available' };
})()
"""
        r2 = await evaluate(ws, inject_chat, 2, await_p=True)
        print("\nTauri invoke probe:")
        print(json.dumps(r2, ensure_ascii=False, indent=2))

        secret = None
        if "value" in r2.get("result", {}) and isinstance(r2["result"]["value"], dict):
            secret = r2["result"]["value"].get("secret")

        if not secret:
            print("\nCannot get secret — try local connections debug")
            return

        print(f"\nGot secret: {secret[:8]}...")

        # 3) Direct WS connect with secret + send 你好
        ws_url2 = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id=cdp-r3-probe"
        print(f"Connecting to backend ws: {ws_url2[:80]}...")
        try:
            async with websockets.connect(ws_url2, max_size=4_000_000, open_timeout=10) as wsb:
                # skip startup_status
                try:
                    first = await asyncio.wait_for(wsb.recv(), timeout=3)
                    print(f"first frame: {first[:200]}")
                except Exception as e:
                    print(f"first recv fail: {e}")
                # send chat
                await wsb.send(json.dumps({"type": "chat", "payload": {"text": "你好", "session_id": "cdp-r3-probe"}}))
                # collect frames
                got = []
                final_text = ""
                end = False
                start = asyncio.get_event_loop().time()
                while not end and (asyncio.get_event_loop().time() - start) < 30:
                    try:
                        raw = await asyncio.wait_for(wsb.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    got.append(msg.get("type", "?"))
                    pl = msg.get("payload", {}) or {}
                    if msg.get("type") == "chat_v2_delta":
                        for k in ("content", "text", "delta"):
                            if isinstance(pl.get(k), str):
                                final_text += pl[k]
                                break
                    elif msg.get("type") == "chat_v2_final":
                        final_text = pl.get("text", final_text)
                        end = True
                    elif msg.get("type") in ("chat_v2_error", "error"):
                        print(f"ERROR: {pl}")
                        end = True
                print(f"\nFrame types: {got}")
                print(f"Final text: {final_text[:300]}")
        except Exception as e:
            print(f"WS connect failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
