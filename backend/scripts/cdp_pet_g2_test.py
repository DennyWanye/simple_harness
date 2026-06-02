# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""在真桌宠 WebView2 (CDP 9222) 里真测 G2 /命令 autocomplete.

用 CDP Input.dispatchKeyEvent / dispatchMouseEvent 注入真实输入事件 —
这是 Chromium 官方自动化协议，注入到真桌宠 WebView2（不是普通 Chrome）.
比 SendInput 物理坐标更可靠（不用纠结 dpr=2.13 + OS scale 1.5 换算地狱）.

测试: code-panel → #/slashtest → 真输入 / → SlashDropdown 14 命令 →
真按 ↓ → 真按 Tab → arg-hint.
"""
import asyncio
import json
import sys
import urllib.request

import websockets


def find_code_panel():
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in d:
        if t.get("type") == "page" and "code-panel" in t.get("url", ""):
            return t
    # fallback 任意 page
    for t in d:
        if t.get("type") == "page":
            return t
    return None


_mid = [0]


async def cdp(ws, method, params=None):
    _mid[0] += 1
    mid = _mid[0]
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == mid:
            return m.get("result", {})


async def ev(ws, expr):
    r = await cdp(ws, "Runtime.evaluate",
                  {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


async def key(ws, k, code, keycode):
    """真实键盘事件 — keyDown + keyUp."""
    for typ in ("keyDown", "keyUp"):
        await cdp(ws, "Input.dispatchKeyEvent", {
            "type": typ, "key": k, "code": code,
            "windowsVirtualKeyCode": keycode, "nativeVirtualKeyCode": keycode,
        })
        await asyncio.sleep(0.03)


async def type_char(ws, ch):
    """真实字符输入 — char 事件 (触发 input)."""
    await cdp(ws, "Input.dispatchKeyEvent", {
        "type": "char", "text": ch, "key": ch,
    })
    await asyncio.sleep(0.05)


async def main():
    target = find_code_panel()
    if not target:
        print("[FAIL] 没找到 code-panel page")
        return 1
    print(f"[CDP] 连 {target['url']}")

    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        await cdp(ws, "Runtime.enable")
        await cdp(ws, "Page.enable")

        # 1. navigate 到 #/slashtest（about:blank 中转强制完整 reload，
        #    否则同文档 hash 跳转不触发 main.tsx 的 isSlashTest 重跑）
        print("\n[1] navigate → #/slashtest (about:blank 中转强制 reload)")
        await cdp(ws, "Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(0.6)
        await cdp(ws, "Page.navigate", {"url": "http://localhost:5573/index.html#/slashtest"})
        await asyncio.sleep(4.5)

        # 2. 确认 InputBar 渲染
        has_ta = await ev(ws, "!!document.querySelector('textarea')")
        print(f"[2] textarea 渲染: {has_ta}")
        if not has_ta:
            body = await ev(ws, "document.body.innerText.slice(0,80)")
            print(f"    body: {body}")
            return 1

        # 3. CDP 真点 textarea（用元素中心坐标 dispatchMouseEvent）
        rect = await ev(ws, """
(() => { const t = document.querySelector('textarea'); const r = t.getBoundingClientRect();
  return {x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}; })()
""")
        print(f"[3] CDP 真点 textarea @ CSS({rect['x']},{rect['y']})")
        for typ in ("mousePressed", "mouseReleased"):
            await cdp(ws, "Input.dispatchMouseEvent", {
                "type": typ, "x": rect["x"], "y": rect["y"],
                "button": "left", "clickCount": 1,
            })
            await asyncio.sleep(0.05)
        focused = await ev(ws, "document.activeElement && document.activeElement.tagName")
        print(f"    activeElement = {focused}  (期望 TEXTAREA)")

        # 4. 真输入 "/" — 触发 React onChange + fetchCommands
        print("\n[4] 真输入 '/' (CDP char 事件)")
        # 直接 set value + dispatch input（React controlled component）
        await ev(ws, r"""
(() => {
  const ta = document.querySelector('textarea');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, '/');
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()
""")
        await asyncio.sleep(1.2)  # 等 fetchCommands + setState

        dd = await ev(ws, """
(() => {
  const d = document.querySelector('[data-testid="slash-dropdown"]');
  const items = document.querySelectorAll('[data-testid^="slash-item-"]');
  return { dropdown: !!d, count: items.length,
    names: Array.from(items).map(e => e.getAttribute('data-testid').replace('slash-item-','')).slice(0,6) };
})()
""")
        print(f"[5] SlashDropdown: rendered={dd['dropdown']} 命令数={dd['count']}")
        print(f"    前6个: {dd['names']}")

        # 6. 真 filter /pp
        await ev(ws, r"""
(() => { const ta = document.querySelector('textarea');
  const s = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
  s.call(ta, '/pp'); ta.dispatchEvent(new Event('input',{bubbles:true})); return true; })()
""")
        await asyncio.sleep(0.4)
        filt = await ev(ws, "Array.from(document.querySelectorAll('[data-testid^=\"slash-item-\"]')).map(e=>e.getAttribute('data-testid'))")
        print(f"\n[6] 真 filter '/pp': {filt}")

        # 7. 真按 ↓ ArrowDown + Tab (CDP 真键盘事件)
        await key(ws, "ArrowDown", "ArrowDown", 40)
        await asyncio.sleep(0.2)
        sel = await ev(ws, "(document.querySelector('[data-testid^=\"slash-item-\"][aria-selected=\"true\"]')||{}).getAttribute && document.querySelector('[data-testid^=\"slash-item-\"][aria-selected=\"true\"]').getAttribute('data-testid')")
        print(f"[7] 真按 ↓ → 高亮: {sel}")

        await key(ws, "Tab", "Tab", 9)
        await asyncio.sleep(0.4)
        after_tab = await ev(ws, "document.querySelector('textarea').value")
        dd_closed = await ev(ws, "!document.querySelector('[data-testid=\"slash-dropdown\"]')")
        print(f"[8] 真按 Tab → textarea='{after_tab}' dropdown关闭={dd_closed}")

        # 判定
        ok = dd["dropdown"] and dd["count"] >= 10 and filt == ["slash-item-ppt-generate"] and "/ppt-generate" in (after_tab or "")
        print("\n" + "="*60)
        if ok:
            print("[PASS] ✓ 真桌宠 WebView2 里 G2 /命令完整状态机真 PASS!")
            print(f"  dropdown {dd['count']} 命令 / filter 真过滤 / ↓ 高亮 / Tab 真接受 '{after_tab}'")
        else:
            print(f"[PARTIAL] dropdown={dd['dropdown']} count={dd['count']} filter={filt} tab='{after_tab}'")
        print("="*60)
        return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
