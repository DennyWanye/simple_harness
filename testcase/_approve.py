#!/usr/bin/env python
"""Poll for the DeskPet permission popup (on companion/root window) and
DOUBLE-CLICK the chosen button FAST (request times out ~30-60s).

USAGE:
  python _approve.py once     # click 允许一次 (allow once)
  python _approve.py always   # click 本会话始终允许
  python _approve.py deny      # click 拒绝
  python _approve.py color     # just report popup accent color + text (no click)

Returns coords clicked. Double-clicks because companion is a background window
(1st SendInput click focuses it, 2nd activates the button).
"""
import sys, json, time, subprocess, urllib.request
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
from websocket import create_connection

CDP="http://127.0.0.1:9333"; PS1=r"C:\Users\24378\AppData\Local\Temp\deskpet-input.ps1"
LABEL={"once":"允许一次","always":"本会话始终允许","deny":"拒绝"}

def root():
    ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
    for t in ts:
        u=t.get("url","")
        if t.get("type")=="page" and "#" not in u and "index.html" not in u: return t

def ev(expr):
    t=root(); ws=create_connection(t["webSocketDebuggerUrl"],timeout=15,suppress_origin=True)
    try:
        ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
        while True:
            m=json.loads(ws.recv())
            if m.get("id")==1: return m["result"]["result"].get("value")
    finally: ws.close()

def find(label):
    return ev(f"""(()=>{{const dpr=window.devicePixelRatio,sx=window.screenX,sy=window.screenY;
      const b=[...document.querySelectorAll('button')].find(e=>/{label}/.test(e.innerText||''));
      if(!b)return ''; const r=b.getBoundingClientRect();
      return Math.round((sx+r.left+r.width/2)*dpr)+','+Math.round((sy+r.top+r.height/2)*dpr);}})()""")

def click(x,y):
    subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",PS1,"-Action","click","-X",str(x),"-Y",str(y)],capture_output=True)

cmd=sys.argv[1]
if cmd=="color":
    print(ev("""(()=>{let acc=null;for(const d of document.querySelectorAll('*')){const s=getComputedStyle(d);if(/3px|4px|5px/.test(s.borderTopWidth)&&s.borderTopStyle==='solid'){const c=s.borderTopColor;if(c&&!/rgba?\\(0, 0, 0/.test(c)){acc=c;break;}}}
      const b=[...document.querySelectorAll('button')].find(e=>/允许一次/.test(e.innerText||''));let box=b;for(let i=0;i<8&&box;i++)box=box.parentElement;
      return JSON.stringify({accent:acc,text:(box?box.innerText:'').replace(/\\n+/g,' | ').slice(0,260)});})()"""))
    sys.exit(0)
def shot(path):
    t=root(); ws=create_connection(t["webSocketDebuggerUrl"],timeout=20,suppress_origin=True)
    try:
        ws.send(json.dumps({"id":1,"method":"Page.enable"})); ws.recv()
        ws.send(json.dumps({"id":2,"method":"Page.captureScreenshot","params":{"format":"png"}}))
        while True:
            m=json.loads(ws.recv())
            if m.get("id")==2:
                import base64; open(path,"wb").write(base64.b64decode(m["result"]["data"])); return
    finally: ws.close()

label=LABEL[cmd]
shotpath=sys.argv[2] if len(sys.argv)>2 else None
for i in range(95):  # ~190s: gpt-5.5 多步规划后工具调用可能很晚才弹窗
    c=find(label)
    if c and "," in c:
        x,y=c.split(","); x,y=int(x),int(y)
        if shotpath:
            try: shot(shotpath); print(f"shot {shotpath}")
            except Exception as e: print("shot fail",e)
        print(f"popup found, {label}@({x},{y}) — double-click")
        click(x,y); time.sleep(0.8); click(x,y)
        sys.exit(0)
    time.sleep(2)
print("popup never appeared")
sys.exit(1)
