# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Layer 1A real-machine E2E — drives the REAL code-panel UI via CDP.

Tests the rewritten Code persona (意图门 + 澄清 + 计划 + 验证) on the
test-research-helper tile. Full stack: React InputBar → codePanelWS →
backend → AgentLoop(new persona) → relay LLM → rendered DOM.

Per TC: type into the real textarea + click the real 发送 button +
poll until the tile returns to idle + screenshot before/after + capture
the assistant reply tail from the rendered DOM. Tool-call evidence is
read separately from the backend log (authoritative).

Usage: python cdp_layer1a_test.py "<TC-id>" "<message>"
"""
from __future__ import annotations
import asyncio, base64, json, sys, urllib.request
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
import websockets

REPO = Path(__file__).resolve().parents[1]
EV = REPO / "plans" / "2026-06-02-superpowers-code-workflow" / "evidence"
EV.mkdir(parents=True, exist_ok=True)
TILE = "test-research-helper"   # placeholder substring to locate the tile


def code_target():
    with urllib.request.urlopen("http://localhost:9222/json/list", timeout=2) as r:
        t = json.loads(r.read())
    return next((x for x in t if "#/code-panel" in x["url"]), None)


class Cdp:
    def __init__(self, ws_url):
        self.ws_url = ws_url; self._id = 0; self._ws = None
    async def __aenter__(self):
        self._ws = await websockets.connect(self.ws_url, max_size=30 * 1024 * 1024)
        await self.send("Page.enable"); await self.send("Runtime.enable")
        return self
    async def __aexit__(self, *_):
        if self._ws: await self._ws.close()
    async def send(self, method, params=None):
        self._id += 1; mid = self._id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})
    async def ev(self, expr, await_promise=False):
        r = await self.send("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": await_promise})
        if r.get("exceptionDetails"):
            raise RuntimeError(f"eval failed: {r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")
    async def shot(self, name):
        r = await self.send("Page.captureScreenshot", {"format": "png"})
        p = EV / name; p.write_bytes(base64.b64decode(r["data"])); return p


# JS: locate the tile (by textarea placeholder), return its message-area text.
_TILE_TEXT = r"""
  (() => {
    const ta = Array.from(document.querySelectorAll('textarea'))
      .find(t => (t.placeholder||'').includes('%TILE%'));
    if (!ta) return {ok:false, reason:'no textarea for tile'};
    // climb to a container that holds both the textarea and the session header
    let box = ta;
    for (let i=0;i<8 && box.parentElement;i++){ box = box.parentElement;
      if ((box.textContent||'').includes('%TILE%') && (box.textContent||'').length>120) break; }
    // inflight is authoritative via the 停止 button (StatusPill text races).
    const btn = ta.parentElement &&
      Array.from(ta.parentElement.querySelectorAll('button'))
        .find(b => b.textContent.includes('停止') || b.textContent.trim()==='发送');
    const inflight = !!(btn && btn.textContent.includes('停止'));
    const t = box.textContent||'';
    const status = inflight ? 'inflight'
      : (t.includes('✗ 错误') ? 'error' : 'idle');
    return {ok:true, status, inflight, text: t, len: t.length};
  })()
""".replace("%TILE%", TILE)


async def run(tc_id, message):
    tgt = code_target()
    if not tgt: print("NO code-panel"); return 3
    async with Cdp(tgt["webSocketDebuggerUrl"]) as c:
        before = await c.ev(_TILE_TEXT)
        if not before.get("ok"):
            print(f"RECON FAIL: {before}"); return 3
        await c.shot(f"{tc_id}-before.png")
        base_len = before["len"]

        # type into the real textarea (React-compatible) + click real 发送
        typed = await c.ev(r"""
          (() => {
            const ta = Array.from(document.querySelectorAll('textarea'))
              .find(t => (t.placeholder||'').includes('%TILE%'));
            if (!ta) return {ok:false};
            ta.focus();
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(ta, %MSG%);
            ta.dispatchEvent(new Event('input', {bubbles:true}));
            return {ok:true, val: ta.value};
          })()
        """.replace("%TILE%", TILE).replace("%MSG%", json.dumps(message, ensure_ascii=False)))
        if not typed.get("ok"): print(f"TYPE FAIL: {typed}"); return 3
        await asyncio.sleep(0.2)
        clicked = await c.ev(r"""
          (() => {
            const ta = Array.from(document.querySelectorAll('textarea'))
              .find(t => (t.placeholder||'').includes('%TILE%'));
            const btn = ta && ta.parentElement &&
              Array.from(ta.parentElement.querySelectorAll('button'))
                .find(b => b.textContent.trim()==='发送' || b.textContent.includes('停止'));
            if (!btn) return {ok:false};
            btn.click(); return {ok:true, label: btn.textContent.trim()};
          })()
        """.replace("%TILE%", TILE))
        if not clicked.get("ok"): print(f"SEND FAIL: {clicked}"); return 3

        # observe inflight (button=停止), then poll until back to 发送 (max 150s)
        inflight_seen = False
        final = before; reply_landed = False
        for i in range(150):
            await asyncio.sleep(1.0)
            final = await c.ev(_TILE_TEXT)
            if final.get("inflight"):
                inflight_seen = True
                if i == 0 or i == 2:
                    await c.shot(f"{tc_id}-inflight.png")
            # done = no longer inflight AND content grew past the user echo
            if inflight_seen and not final.get("inflight") and final["len"] > base_len + 10:
                reply_landed = True; break
            # fallback: if never saw inflight but content grew a lot, also accept
            if not inflight_seen and i > 4 and final["len"] > base_len + 60:
                reply_landed = True; break
        await c.shot(f"{tc_id}-after.png")

        # extract reply = text AFTER the last echo of the sent message
        full = final["text"]
        idx = full.rfind(message[:24])
        tail = full[idx:] if idx >= 0 else full[max(0, base_len - 40):]
        out = {
            "tc": tc_id, "message": message,
            "typed_ok": typed["ok"], "send_clicked": clicked["ok"],
            "inflight_seen": inflight_seen, "reply_landed": reply_landed,
            "final_status": final["status"],
            "reply_tail": tail[-1800:],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        (EV / f"{tc_id}-reply.txt").write_text(tail, encoding="utf-8")
    return 0 if reply_landed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1], sys.argv[2])))
