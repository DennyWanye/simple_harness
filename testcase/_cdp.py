#!/usr/bin/env python
"""CDP harness for windows-mcp E2E (DeskPet, port 9333).

USAGE (locate = 'look with eyes'; real click done by SendInput externally):
  python _cdp.py pages
  python _cdp.py locate <page_substr> <css_selector>     # -> physical (x,y) of element center
  python _cdp.py eval   <page_substr> <js_expr>           # eval JS in page, print JSON result
  python _cdp.py shot   <page_substr> <out_png>           # screenshot via CDP Page.captureScreenshot
  python _cdp.py text   <page_substr>                     # dump chat transcript innerText (last 1500 chars)

page_substr matches against target url (e.g. 'code-panel','message-panel', or '/' root pet window).
Physical coord = (window.screenX + rect.left + rect.width/2) * dpr , same for y.
This is LOCATING ONLY (read element geometry) — the actual click is a real OS SendInput elsewhere.
"""
import sys, json, urllib.request
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from websocket import create_connection  # websocket-client

CDP = "http://127.0.0.1:9333"

def targets():
    return json.loads(urllib.request.urlopen(CDP + "/json", timeout=5).read())

def pick(sub):
    if sub in ("root", "pet"):
        for t in targets():
            u = t.get("url", "")
            if t.get("type") == "page" and "#" not in u and "index.html" not in u:
                return t
    for t in targets():
        if t.get("type") == "page" and sub in t.get("url", ""):
            return t
    # root pet window: url ends with /index.html or bare host
    if sub == "/":
        for t in targets():
            u = t.get("url", "")
            if t.get("type") == "page" and (u.rstrip("/").endswith("index.html") or u.endswith(":5173/")):
                return t
    raise SystemExit(f"no page matching {sub!r}; have: " + ", ".join(t.get('url','') for t in targets()))

def ws_eval(sub, expr, returnByValue=True, awaitPromise=True):
    t = pick(sub)
    ws = create_connection(t["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": returnByValue,
                                       "awaitPromise": awaitPromise}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                return msg
    finally:
        ws.close()

def cmd_locate(sub, sel):
    js = f"""(()=>{{
      const el=document.querySelector({json.dumps(sel)});
      if(!el) return JSON.stringify({{ok:false,err:'not found'}});
      const r=el.getBoundingClientRect();
      const dpr=window.devicePixelRatio;
      return JSON.stringify({{ok:true,
        cssX:r.left+r.width/2, cssY:r.top+r.height/2,
        w:r.width,h:r.height, screenX:window.screenX, screenY:window.screenY, dpr,
        physX:Math.round((window.screenX+r.left+r.width/2)*dpr),
        physY:Math.round((window.screenY+r.top+r.height/2)*dpr),
        text:(el.innerText||el.value||'').slice(0,60)}});
    }})()"""
    m = ws_eval(sub, js)
    print(m["result"]["result"].get("value", json.dumps(m)))

def cmd_eval(sub, expr):
    m = ws_eval(sub, expr)
    r = m.get("result", {}).get("result", {})
    print(r.get("value") if "value" in r else json.dumps(m))

def cmd_text(sub):
    js = "(()=>{const m=document.querySelector('[class*=transcript],[class*=messages],[class*=chat],main')||document.body;return (m.innerText||'').slice(-1500);})()"
    cmd_eval(sub, js)

def cmd_shot(sub, out):
    t = pick(sub)
    ws = create_connection(t["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"})); ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                import base64
                data = base64.b64decode(msg["result"]["data"])
                open(out, "wb").write(data)
                print(f"saved {out} ({len(data)} bytes)")
                return
    finally:
        ws.close()

if __name__ == "__main__":
    c = sys.argv[1]
    if c == "pages":
        for t in targets():
            print(t.get("type"), "|", t.get("url"))
    elif c == "locate": cmd_locate(sys.argv[2], sys.argv[3])
    elif c == "eval":   cmd_eval(sys.argv[2], sys.argv[3])
    elif c == "text":   cmd_text(sys.argv[2])
    elif c == "shot":   cmd_shot(sys.argv[2], sys.argv[3])
