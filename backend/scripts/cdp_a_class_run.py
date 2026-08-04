# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""A 类用户级真测 — CDP 注入真实输入到真桌宠 WebView2，触发工具调用，
截图 + 轮询产物落盘。Chromium 官方协议注入，非 import/协议层伪造。

用法:
  python cdp_a_class_run.py --panel code-panel --text "帮我生成关于猫咪护理的3页PPT" \
      --expect ppt_create --ext pptx --prefix tc-a1 --wait 180
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import websockets

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SHOT_DIR = Path(r"G:\projects\deskpet\plans\manual-results-2026-06-01\screenshots")
# code 模式产物落项目目录；companion 模式落 artifacts dir。两处都扫。
WATCH_DIRS = [
    Path(r"G:\projects\deskpet\backend\userdata\artifacts"),
    Path(r"G:\projects\test-research-helper"),
    Path(r"G:\projects\小说网站"),
]
_PROD_EXTS = (".pptx", ".xlsx", ".docx", ".pdf", ".png", ".jpg")
_mid = [0]


def find_page(url_frag):
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in d:
        if t.get("type") == "page" and url_frag in t.get("url", ""):
            return t
    return None


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


async def shot(ws, name):
    r = await cdp(ws, "Page.captureScreenshot", {"format": "png"})
    data = r.get("data")
    if not data:
        print(f"  [shot] {name}: 截图失败(空)")
        return None
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    p = SHOT_DIR / f"{name}.png"
    p.write_bytes(base64.b64decode(data))
    print(f"  [shot] {name}.png ({p.stat().st_size} bytes)")
    return str(p)


def list_artifacts():
    out = {}
    for d in WATCH_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in _PROD_EXTS:
                    out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="code-panel")
    ap.add_argument("--text", required=True)
    ap.add_argument("--expect", default="")
    ap.add_argument("--ext", default="")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--wait", type=int, default=180)
    ap.add_argument("--ta-index", type=int, default=0, help="第几个 textarea")
    args = ap.parse_args()

    target = find_page(args.panel)
    if not target:
        print(f"[FAIL] 没找到 panel={args.panel}")
        return 1
    print(f"[CDP] 连 {target['url']}")

    before_files = list_artifacts()
    print(f"[artifacts] 测前已有 {len(before_files)} 个文件")

    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        await cdp(ws, "Runtime.enable")
        await cdp(ws, "Page.enable")

        await shot(ws, f"{args.prefix}-1-before")

        # 找目标 textarea/input
        has = await ev(ws, "document.querySelectorAll('textarea, input.bp-chat-input, input[data-testid=\"chat-input\"]').length")
        print(f"[1] 输入框数量={has}")
        if not has:
            print("[FAIL] 无输入框")
            return 1

        # 注入文本（React controlled — native setter + input event）
        text_js = json.dumps(args.text)
        idx = args.ta_index
        set_ok = await ev(ws, f"""
(() => {{
  const els = document.querySelectorAll('textarea, input.bp-chat-input, input[data-testid="chat-input"]');
  const el = els[{idx}]; if(!el) return 'no-el';
  el.focus();
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, {text_js});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  return el.value;
}})()
""")
        print(f"[2] 注入文本 → textarea.value={set_ok!r}")
        await asyncio.sleep(0.4)
        await shot(ws, f"{args.prefix}-2-typed")

        # 发送：先试 Enter（keyDown+keyUp，非 shift）
        for typ in ("keyDown", "keyUp"):
            await cdp(ws, "Input.dispatchKeyEvent", {
                "type": typ, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.2)
        after_val = await ev(ws, f"""
(() => {{ const els=document.querySelectorAll('textarea, input.bp-chat-input, input[data-testid="chat-input"]');
  return els[{idx}] ? els[{idx}].value : 'gone'; }})()
""")
        sent = (after_val == "" or after_val == "gone")
        print(f"[3] 按 Enter 发送 → textarea 现值={after_val!r} (清空={sent})")
        if not sent:
            # Enter 没发出，可能需要点发送按钮；尝试找按钮
            clicked = await ev(ws, r"""
(() => {
  const btns = Array.from(document.querySelectorAll('button'));
  const send = btns.find(b => /发送|send|↑|➤/i.test((b.textContent||'')+(b.getAttribute('aria-label')||'')));
  if(send){ send.click(); return 'clicked:'+(send.textContent||send.getAttribute('aria-label')); }
  return 'no-send-btn';
})()
""")
            print(f"[3b] Enter 未清空，尝试点发送按钮 → {clicked!r}")
            await asyncio.sleep(1.2)

        # 轮询：等产物落盘 OR DOM 出现 ArtifactCard
        print(f"[4] 轮询最多 {args.wait}s 等结果...")
        t0 = time.time()
        result = {"new_files": [], "artifact_card": False, "tool_seen": False}
        while time.time() - t0 < args.wait:
            now_files = list_artifacts()
            new = [f for f in now_files if f not in before_files]
            if args.ext:
                new = [f for f in new if f.lower().endswith("." + args.ext.lower())]
            if new:
                result["new_files"] = new
                break
            card = await ev(ws, r"""
(() => {
  const c = document.querySelector('[class*="artifact" i], [data-testid*="artifact" i], [class*="ArtifactCard"]');
  return !!c;
})()
""")
            if card:
                result["artifact_card"] = True
            await asyncio.sleep(3)

        await asyncio.sleep(1)
        await shot(ws, f"{args.prefix}-3-result")

        # 最终 DOM 状态
        dom = await ev(ws, r"""
(() => {
  const card = document.querySelector('[class*="artifact" i], [data-testid*="artifact" i]');
  return {
    hasCard: !!card,
    cardText: card ? card.innerText.slice(0,200) : '',
    bodyTail: (document.body.innerText||'').slice(-300),
  };
})()
""")
        print(f"\n[RESULT] new_files={result['new_files']}")
        print(f"[RESULT] DOM hasArtifactCard={dom['hasCard']}")
        if dom["hasCard"]:
            print(f"[RESULT] cardText={dom['cardText']!r}")
        print(f"[RESULT] bodyTail={dom['bodyTail']!r}")

        ok = bool(result["new_files"]) or dom["hasCard"]
        print("\n" + "="*60)
        print(f"[{'PASS' if ok else 'FAIL'}] {args.prefix}: "
              f"产物={len(result['new_files'])}个 / ArtifactCard={dom['hasCard']}")
        print("="*60)
        return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
