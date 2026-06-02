# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Quick recon of the code-panel webview state before Layer 1A E2E."""
from __future__ import annotations
import asyncio, json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
import websockets


def targets():
    with urllib.request.urlopen("http://localhost:9222/json/list", timeout=2) as r:
        return json.loads(r.read())


async def main():
    t = targets()
    code = next((x for x in t if "#/code-panel" in x["url"]), None)
    if not code:
        print("NO code-panel target"); return
    ws = await websockets.connect(code["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024)
    _id = 0
    async def send(method, params=None):
        nonlocal _id
        _id += 1
        await ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == _id:
                return msg.get("result", {})
    await send("Runtime.enable")
    r = await send("Runtime.evaluate", {"expression": r"""
      (() => {
        const ta = document.querySelector('textarea');
        const btns = Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(Boolean).slice(0,40);
        const body = document.body.textContent || "";
        return {
          url: location.href,
          textareaFound: !!ta,
          textareaPlaceholder: ta ? ta.placeholder : null,
          buttons: btns,
          bodyHead: body.slice(0, 600),
          hasSendBtn: btns.includes('发送'),
        };
      })()
    """, "returnByValue": True})
    print(json.dumps(r.get("result", {}).get("value", {}), ensure_ascii=False, indent=2))
    await ws.close()


asyncio.run(main())
