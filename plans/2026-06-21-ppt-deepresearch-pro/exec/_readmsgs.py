import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and "message-pan" in x.get("url","")]
if not t: t=[x for x in ts if x.get("type")=="page" and x.get("url","").rstrip().endswith(":5173/")]
ws=create_connection(t[0]["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{const out=[];document.querySelectorAll('[class*=message],[class*=bubble],[class*=msg],[class*=Row]').forEach(e=>{const t=(e.innerText||'').trim();if(t&&t.length<300)out.push(t)});return JSON.stringify(out.slice(-12));})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1:
        v=m["result"]["result"].get("value")
        try:
            for s in json.loads(v): print("·",s[:160])
        except: print(v[:800])
        break
ws.close()
