# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""走完 onboarding wizard via CDP Runtime.evaluate.

绕过 windows-mcp input injection 限制 — 直接通过 WebView2 的
remote debugging port (9222) 注入 JavaScript 操作 DOM。

Usage: python cdp_onboarding.py
"""
import asyncio
import json
import sys
import urllib.request

import websockets


CDP_HOST = "127.0.0.1:9222"


def find_main_target():
    """Pick the pet main webview (url ends with index.html, no hash)."""
    data = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith(
            "tauri.localhost"
        ):
            return t
    # Fallback: anything without # in url
    for t in data:
        if t.get("type") == "page" and "#" not in t.get("url", ""):
            return t
    return None


async def evaluate(ws, expression: str, await_promise: bool = False):
    """Send Runtime.evaluate and return its result."""
    msg_id = 1  # we send one at a time
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
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            return msg.get("result", {})


async def main():
    target = find_main_target()
    if target is None:
        print("ERROR: no main pet target found", file=sys.stderr)
        sys.exit(1)
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connecting to pet main: {target['url']}")
    print(f"  ws: {ws_url}")

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        # 1) Inspect DOM — what onboarding-related elements exist?
        probe = """
(() => {
  const out = {};
  out.has_skip = !!document.querySelector('[data-testid="onboarding-skip-btn"]');
  out.has_next = !!document.querySelector('[data-testid="onboarding-next-btn"]');
  out.has_back = !!document.querySelector('[data-testid="onboarding-back-btn"]');
  out.has_wizard = !!document.querySelector('[data-testid="onboarding-wizard"]');
  out.step_welcome = !!document.querySelector('[data-testid="onboarding-step-welcome"]');
  out.step_connect = !!document.querySelector('[data-testid="onboarding-step-connectModel"]');
  out.step_ready = !!document.querySelector('[data-testid="onboarding-step-ready"]');
  out.relay_modal = !!document.querySelector('[role="dialog"][aria-modal="true"]');
  // Visible top-level buttons
  const btns = Array.from(document.querySelectorAll('button')).slice(0, 20);
  out.buttons = btns.map(b => ({ text: (b.innerText || '').trim().slice(0, 30), id: b.id, testid: b.getAttribute('data-testid') }));
  return out;
})()
"""
        r = await evaluate(ws, probe)
        print("DOM probe result:")
        print(json.dumps(r, ensure_ascii=False, indent=2))

        # 2) If skip button exists, click it (fastest path)
        if r.get("result", {}).get("value", {}).get("has_skip"):
            print("\n→ Clicking onboarding-skip-btn")
            r2 = await evaluate(ws, "document.querySelector('[data-testid=\"onboarding-skip-btn\"]').click(); 'clicked-skip'")
            print(json.dumps(r2, ensure_ascii=False))
            await asyncio.sleep(1.5)

        # 3) Re-probe to see if wizard gone
        r3 = await evaluate(ws, probe)
        print("\nAfter skip click:")
        print(json.dumps(r3, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
