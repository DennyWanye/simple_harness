import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and "message-pan" in x.get("url","")][0]
ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{const dpr=devicePixelRatio,sx=screenX,sy=screenY;
 const ta=document.querySelector('textarea')||document.querySelector('input[type=text]')||document.querySelector('[contenteditable]');
 let send=null;for(const b of document.querySelectorAll('button')){if((b.innerText||'').includes('发送')){send=b;break;}}
 const r=ta?ta.getBoundingClientRect():null, s=send?send.getBoundingClientRect():null;
 return JSON.stringify({inX:r?Math.round((sx+r.left+r.width/2)*dpr):0,inY:r?Math.round((sy+r.top+r.height/2)*dpr):0,
   sendX:s?Math.round((sx+s.left+s.width/2)*dpr):0,sendY:s?Math.round((sy+s.top+s.height/2)*dpr):0});})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1: print(m["result"]["result"].get("value")); break
ws.close()
