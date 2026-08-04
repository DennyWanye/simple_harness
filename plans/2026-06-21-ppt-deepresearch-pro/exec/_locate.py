import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
page=[t for t in ts if t.get("type")=="page" and t.get("url","").rstrip().endswith(":5173/")][0]
ws=create_connection(page["webSocketDebuggerUrl"],timeout=15,suppress_origin=True)
def ev(expr):
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==1: return m["result"]["result"].get("value")
js=r"""(()=>{
 const dpr=window.devicePixelRatio,sx=window.screenX,sy=window.screenY;
 const out={dpr,sx,sy,inputs:[],buttons:[]};
 for(const el of document.querySelectorAll('textarea,input,[contenteditable=""],[contenteditable="true"]')){
   const r=el.getBoundingClientRect(); if(r.width<5||r.height<5)continue;
   out.inputs.push({tag:el.tagName,ce:el.getAttribute('contenteditable'),ph:el.getAttribute('placeholder')||el.getAttribute('data-placeholder')||'',
     x:Math.round((sx+r.left+r.width/2)*dpr),y:Math.round((sy+r.top+r.height/2)*dpr),w:Math.round(r.width),h:Math.round(r.height)});
 }
 for(const b of document.querySelectorAll('button')){
   const r=b.getBoundingClientRect(); if(r.width<5)continue; const t=(b.innerText||'').trim();
   if(t) out.buttons.push({t,x:Math.round((sx+r.left+r.width/2)*dpr),y:Math.round((sy+r.top+r.height/2)*dpr)});
 }
 return JSON.stringify(out);
})()"""
print(ev(js))
ws.close()
