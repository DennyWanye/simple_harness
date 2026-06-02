# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""plan-confirm 硬门真机 E2E — 验证 code 任务先出 plan 等确认再跑。

GO 路径: 派明确任务 → 弹 plan 卡 + [执行]/[取消] 按钮 + 暂停(不跑工具)
        → 点[执行] → ReAct 才开始。
CANCEL 路径: 同上 → 点[取消] → 不跑,emit cancelled。

Usage: python cdp_plan_gate_test.py go|cancel "<message>"
"""
from __future__ import annotations
import asyncio, base64, json, sys, urllib.request
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
import websockets

REPO = Path(__file__).resolve().parents[1]
EV = REPO / "plans" / "2026-06-02-superpowers-code-workflow" / "evidence"
EV.mkdir(parents=True, exist_ok=True)
TILE = "test-research-helper"


def code_target():
    with urllib.request.urlopen("http://localhost:9222/json/list", timeout=2) as r:
        return next((x for x in json.loads(r.read()) if "#/code-panel" in x["url"]), None)


class Cdp:
    def __init__(self, ws_url): self.ws_url = ws_url; self._id = 0; self._ws = None
    async def __aenter__(self):
        self._ws = await websockets.connect(self.ws_url, max_size=30 * 1024 * 1024)
        await self.send("Page.enable"); await self.send("Runtime.enable"); return self
    async def __aexit__(self, *_):
        if self._ws: await self._ws.close()
    async def send(self, method, params=None):
        self._id += 1; mid = self._id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(await self._ws.recv())
            if m.get("id") == mid:
                if "error" in m: raise RuntimeError(f"{method} -> {m['error']}")
                return m.get("result", {})
    async def ev(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if r.get("exceptionDetails"): raise RuntimeError(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")
    async def shot(self, name):
        r = await self.send("Page.captureScreenshot", {"format": "png"})
        (EV / name).write_bytes(base64.b64decode(r["data"]))


async def run(decision, message):
    tgt = code_target()
    if not tgt: print("NO code-panel"); return 3
    async with Cdp(tgt["webSocketDebuggerUrl"]) as c:
        # type + send
        typed = await c.ev(r"""
          (() => {
            const ta = Array.from(document.querySelectorAll('textarea'))
              .find(t => (t.placeholder||'').includes('%TILE%'));
            if (!ta) return {ok:false};
            ta.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
            setter.call(ta, %MSG%); ta.dispatchEvent(new Event('input',{bubbles:true}));
            const btn = Array.from(ta.parentElement.querySelectorAll('button')).find(b=>b.textContent.trim()==='发送');
            if (btn) btn.click();
            return {ok:true};
          })()
        """.replace("%TILE%", TILE).replace("%MSG%", json.dumps(message, ensure_ascii=False)))
        print("typed+sent:", typed)

        # poll up to 90s for the plan-confirm buttons to appear
        gate_seen = False
        for _ in range(90):
            await asyncio.sleep(1.0)
            st = await c.ev(r"""(() => ({
              goBtn: !!document.querySelector('[data-testid="plan-confirm-go"]'),
              cancelBtn: !!document.querySelector('[data-testid="plan-confirm-cancel"]'),
              planCard: (document.body.textContent||'').includes('📋 计划'),
            }))()""")
            if st["goBtn"] and st["cancelBtn"]:
                gate_seen = True; break
        await c.shot(f"plan-gate-{decision}-awaiting.png")
        print("gate buttons appeared:", gate_seen, st)
        if not gate_seen:
            print("FAIL: plan-confirm buttons never appeared"); return 1

        # click the chosen decision button
        clicked = await c.ev(r"""
          (() => {
            const b = document.querySelector('[data-testid="plan-confirm-%DEC%"]');
            if (!b) return {ok:false}; b.click(); return {ok:true};
          })()
        """.replace("%DEC%", "go" if decision == "go" else "cancel"))
        print(f"clicked [{decision}]:", clicked)
        await asyncio.sleep(2.0)
        await c.shot(f"plan-gate-{decision}-after-click.png")
        # buttons should be gone after click
        gone = await c.ev(r"""(() => !document.querySelector('[data-testid="plan-confirm-go"]'))()""")
        print("buttons gone after click:", gone)
        return 0 if (gate_seen and clicked.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1], sys.argv[2])))
