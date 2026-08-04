import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
for t in ts:
    if t.get("type")!="page" or "message-pan" not in t.get("url",""): continue
    ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
    expr="JSON.stringify([...document.querySelectorAll('button')].map(b=>{var r=b.getBoundingClientRect();return [(b.innerText||'').trim().replace(/[^\u0000-\u00ff]/g,'#'),Math.round((screenX+r.left+r.width/2)*devicePixelRatio),Math.round((screenY+r.top+r.height/2)*devicePixelRatio),Math.round(r.width)];}))"
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==1: print(m["result"]["result"].get("value")); break
    ws.close()
