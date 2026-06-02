# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Layer 1B auto-confirm verifier — send a task similar to a previously
approved one; expect NO plan-confirm buttons (auto-confirmed) and the tile
goes inflight (ReAct runs) without any click.

Usage: python cdp_layer1b_autocheck.py "<message>"
"""
from __future__ import annotations
import asyncio, base64, json, sys, urllib.request
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
import websockets

EV = Path("G:/projects/deskpet/plans/2026-06-02-superpowers-code-workflow/evidence")
TILE = "test-research-helper"


def code_target():
    with urllib.request.urlopen("http://localhost:9222/json/list", timeout=2) as r:
        return next((x for x in json.loads(r.read()) if "#/code-panel" in x["url"]), None)


async def main(message):
    tgt = code_target()
    async with await websockets.connect(tgt["webSocketDebuggerUrl"], max_size=30 * 1024 * 1024) as ws:
        i = 0
        async def ev(e):
            nonlocal i; i += 1
            await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate", "params": {"expression": e, "returnByValue": True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i: return m["result"]["result"].get("value")
        async def shot(n):
            nonlocal i; i += 1
            await ws.send(json.dumps({"id": i, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i: (EV / n).write_bytes(base64.b64decode(m["result"]["data"])); return
        # type + send
        await ev(r"""(()=>{const ta=Array.from(document.querySelectorAll('textarea')).find(t=>(t.placeholder||'').includes('%TILE%')); ta.focus(); const s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set; s.call(ta,%MSG%); ta.dispatchEvent(new Event('input',{bubbles:true})); const b=Array.from(ta.parentElement.querySelectorAll('button')).find(x=>x.textContent.trim()==='发送'); b.click();})()""".replace("%TILE%", TILE).replace("%MSG%", json.dumps(message, ensure_ascii=False)))
        print("sent:", message)
        # watch 25s: buttons should NEVER appear; tile should go inflight (停止 button)
        buttons_appeared = False
        inflight_seen = False
        for _ in range(25):
            await asyncio.sleep(1.0)
            st = await ev(r"""(()=>{const ta=Array.from(document.querySelectorAll('textarea')).find(t=>(t.placeholder||'').includes('test-research-helper')); const stop=ta&&Array.from(ta.parentElement.querySelectorAll('button')).some(x=>x.textContent.includes('停止')); return {go:!!document.querySelector('[data-testid="plan-confirm-go"]'), inflight:!!stop};})()""")
            if st["go"]: buttons_appeared = True
            if st["inflight"]: inflight_seen = True
            if buttons_appeared: break
        await shot("layer1b-auto-after.png")
        print(f"buttons_appeared={buttons_appeared} inflight_seen={inflight_seen}")
        # PASS = auto-confirmed: NO buttons + tile went inflight (ReAct running)
        ok = (not buttons_appeared) and inflight_seen
        print("AUTO-CONFIRM PASS" if ok else "FAIL (expected no buttons + inflight)")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
