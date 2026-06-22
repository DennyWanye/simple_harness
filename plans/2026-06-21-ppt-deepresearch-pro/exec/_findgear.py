import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and x.get("url","").rstrip().endswith(":5173/")][0]
ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{const dpr=devicePixelRatio,sx=screenX,sy=screenY;const out=[];
 document.querySelectorAll('button,[role=button],[title],[aria-label]').forEach(e=>{
  const lab=(e.getAttribute('title')||e.getAttribute('aria-label')||e.innerText||'').trim();
  const r=e.getBoundingClientRect(); if(r.width<8||r.height<8)return;
  if(/设置|settings|齿轮|gear|偏好/i.test(lab)) out.push({lab:lab.slice(0,20),x:Math.round((sx+r.left+r.width/2)*dpr),y:Math.round((sy+r.top+r.height/2)*dpr)});
 });return JSON.stringify(out);})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1: print(m["result"]["result"].get("value")); break
ws.close()
