#!/usr/bin/env python
"""Hover-mount + atomic click for auto-hiding pet toolbar buttons.

The pet toolbar (root window) mounts on hover and auto-hides after ~1-2s,
so locate-then-click over two bash roundtrips always loses the race.
This script does the whole chain atomically:

  1. SetProcessDpiAwareness(2) -> physical pixels.
  2. Real cursor jiggle over the pet body (WM_MOUSEMOVE, not teleport-once)
     to mount the toolbar.
  3. Poll CDP (root page) every 150ms for the aria-label button.
  4. The instant it mounts: move cursor there + SendInput LEFTDOWN/UP.

USAGE: python _hover_click_toolbar.py "进入 Code 模式" [hover_x hover_y]
"""
import ctypes
import json
import sys
import time
import urllib.request

from websocket import create_connection

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32

CDP = "http://127.0.0.1:9333"

ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def send_click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    inputs = (INPUT * 2)()
    inputs[0].type = 0  # INPUT_MOUSE
    inputs[0].mi.dwFlags = 0x0002  # LEFTDOWN
    inputs[1].type = 0
    inputs[1].mi.dwFlags = 0x0004  # LEFTUP
    user32.SendInput(2, inputs, ctypes.sizeof(INPUT))


def root_page():
    ts = json.loads(urllib.request.urlopen(CDP + "/json", timeout=5).read())
    for t in ts:
        u = t.get("url", "")
        if t.get("type") == "page" and "#" not in u and "index.html" not in u:
            return t
    for t in ts:
        if t.get("type") == "page" and t.get("url", "").endswith(":5173/"):
            return t
    raise SystemExit("no root page")


def locate(ws, label_sub: str):
    expr = (
        "(()=>{const b=[...document.querySelectorAll('[aria-label]')]"
        f".find(x=>(x.getAttribute('aria-label')||'').includes({json.dumps(label_sub)}));"
        "if(!b) return null; const r=b.getBoundingClientRect(),d=window.devicePixelRatio;"
        "return JSON.stringify({x:Math.round((window.screenX+r.x+r.width/2)*d),"
        "y:Math.round((window.screenY+r.y+r.height/2)*d)});})()"
    )
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            v = m["result"]["result"].get("value")
            return json.loads(v) if v else None


def main() -> None:
    label = sys.argv[1]
    hx = int(sys.argv[2]) if len(sys.argv) > 3 else 3193
    hy = int(sys.argv[3]) if len(sys.argv) > 3 else 811

    page = root_page()
    ws = create_connection(page["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
    try:
        deadline = time.time() + 8.0
        i = 0
        while time.time() < deadline:
            # real jiggle over pet body -> WM_MOUSEMOVE stream mounts toolbar
            user32.SetCursorPos(hx + (i % 3) * 8 - 8, hy + (i % 2) * 8)
            i += 1
            time.sleep(0.15)
            pos = locate(ws, label)
            if pos:
                send_click(pos["x"], pos["y"])
                print(f"CLICKED {label} at ({pos['x']},{pos['y']})")
                return
        print("TIMEOUT: toolbar never mounted")
        sys.exit(1)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
