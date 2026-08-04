#!/usr/bin/env python
"""Auto-locate + real SendInput send to a DeskPet chat input.

USAGE:
  python _send.py code0 "中文消息"     # test-research-helper code tile (textarea idx 0)
  python _send.py code1 "..."          # 小说网站 tile (idx 1)
  python _send.py companion "..."      # root pet companion window

Locates the textarea + nearest 发送 button FRESH (defeats layout-shift coord drift),
then shells out to deskpet-input.ps1 -Action send for a real OS click+paste+click.
Prints the coords used. Returns 0 on success.
"""
import sys, json, subprocess, urllib.request
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
from websocket import create_connection

CDP = "http://127.0.0.1:9333"
PS1 = r"C:\Users\24378\AppData\Local\Temp\deskpet-input.ps1"

def pick(sub):
    ts = json.loads(urllib.request.urlopen(CDP + "/json", timeout=5).read())
    if sub == "companion":
        for t in ts:
            u = t.get("url", "")
            if t.get("type") == "page" and "#" not in u and "index.html" not in u:
                return t
        for t in ts:
            if t.get("type")=="page" and t.get("url","").endswith(":5173/"): return t
    for t in ts:
        if t.get("type") == "page" and "code-panel" in t.get("url", ""):
            return t
    raise SystemExit("no page")

def ev(t, expr):
    ws = create_connection(t["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True,"awaitPromise":True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id")==1: return m["result"]["result"].get("value")
    finally: ws.close()

target, text = sys.argv[1], sys.argv[2]
# match textarea by placeholder substring (robust vs index drift during re-render)
PH = {"code0":"test-research-helper", "code1":"小说网站", "companion":""}
phmatch = PH.get(target, "test-research-helper")
page = pick("companion" if target=="companion" else "code-panel")
js = f"""(()=>{{
  const dpr=window.devicePixelRatio,sx=window.screenX,sy=window.screenY;
  const sub={json.dumps(phmatch)};
  const tas=[...document.querySelectorAll('textarea')];
  const ta = sub ? (tas.find(t=>(t.placeholder||'').includes(sub))||tas[0]) : tas[0];
  if(!ta) return JSON.stringify({{err:'no textarea for '+sub}});
  const rt=ta.getBoundingClientRect();
  // send button = the 发送 inside the SAME tile container as this textarea
  // (DOM 祖先法，避免 dashboard 双 tile 下按几何选错另一 tile 的发送按钮)。
  let cont=ta; for(let i=0;i<10&&cont.parentElement;i++){{cont=cont.parentElement;
    if(cont.querySelector('textarea')===ta){{const b=[...cont.querySelectorAll('button')].find(x=>x.innerText.trim()==='发送'||x.innerText.includes('停止')||x.getAttribute('aria-label')==='发送'); if(b){{var sameTileBtn=b;}}}}
    // stop climbing once we'd include a SECOND textarea (left the tile)
    if(cont.querySelectorAll('textarea').length>1) break;
  }}
  let best=null;
  if(typeof sameTileBtn!=='undefined'&&sameTileBtn) best=sameTileBtn.getBoundingClientRect();
  if(!best){{ // fallback: nearest 发送 on same row to the right
    const sends=[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='发送'||b.innerText.includes('停止'));
    let bd=1e9; for(const b of sends){{const r=b.getBoundingClientRect(); if(r.left<rt.left)continue; const d=Math.abs(r.top-(rt.top+rt.height/2)); if(d<bd){{bd=d;best=r;}}}}
  }}
  if(!best) return JSON.stringify({{err:'no send btn'}});
  return JSON.stringify({{ph:ta.placeholder, inX:Math.round((sx+rt.left+rt.width/2)*dpr),inY:Math.round((sy+rt.top+rt.height/2)*dpr),
    sendX:Math.round((sx+best.left+best.width/2)*dpr),sendY:Math.round((sy+best.top+best.height/2)*dpr)}});
}})()"""
c = json.loads(ev(page, js))
if "err" in c: raise SystemExit("locate failed: "+c["err"])
print(f"coords in({c['inX']},{c['inY']}) send({c['sendX']},{c['sendY']})")
r = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",PS1,
    "-Action","send","-X",str(c["inX"]),"-Y",str(c["inY"]),"-Text",text,
    "-X2",str(c["sendX"]),"-Y2",str(c["sendY"])], capture_output=True, text=True)
print(r.stdout.strip());
if r.stderr.strip(): print("ERR:",r.stderr.strip())
