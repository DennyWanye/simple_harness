# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""A 类真测侦察：连真桌宠 WebView2 (CDP 9222)，列出所有 page，
找主对话输入框（textarea/input/contenteditable）+ 判断登录态。"""
import asyncio
import json
import sys
import urllib.request

import websockets

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_mid = [0]


def list_targets():
    return json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())


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


async def main():
    targets = list_targets()
    pages = [t for t in targets if t.get("type") == "page"]
    print(f"[CDP] {len(pages)} page(s):")
    for p in pages:
        print(f"  - title={p.get('title','')!r} url={p.get('url','')[:90]}")

    for p in pages:
        url = p.get("url", "")
        print(f"\n{'='*70}\n[PAGE] {url[:90]}")
        try:
            async with websockets.connect(p["webSocketDebuggerUrl"], max_size=None) as ws:
                await cdp(ws, "Runtime.enable")
                info = await ev(ws, r"""
(() => {
  const inputs = Array.from(document.querySelectorAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"]'));
  const desc = inputs.map(el => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName, type: el.getAttribute('type'),
      placeholder: el.getAttribute('placeholder') || el.getAttribute('aria-label') || '',
      testid: el.getAttribute('data-testid') || '',
      cls: (el.className||'').toString().slice(0,40),
      x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
      w: Math.round(r.width), h: Math.round(r.height), visible: r.width>0&&r.height>0,
    };
  });
  return {
    title: document.title,
    bodyText: (document.body ? document.body.innerText : '').slice(0,160),
    inputCount: inputs.length,
    inputs: desc,
    // 登录态线索
    hasLogin: /登录|login|sign in|账号|密码|onboarding/i.test(document.body ? document.body.innerText : ''),
  };
})()
""")
                print(f"  title={info['title']!r}")
                print(f"  bodyText[:160]={info['bodyText']!r}")
                print(f"  hasLoginHints={info['hasLogin']}")
                print(f"  输入框 {info['inputCount']} 个:")
                for d in info["inputs"]:
                    print(f"    {d['tag']}/{d['type']} testid={d['testid']!r} ph={d['placeholder']!r} "
                          f"cls={d['cls']!r} center=({d['x']},{d['y']}) {d['w']}x{d['h']} vis={d['visible']}")
        except Exception as e:
            print(f"  [ERR] {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
