import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
for t in ts:
    if t.get("type")!="page": continue
    url=t.get("url","")
    try:
        ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
        ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":
          "JSON.stringify({outline: !!document.body.innerText.match(/确认生成|大纲|让我改改|历史大纲/), btns:[...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(x=>x).slice(0,20)})",
          "returnByValue":True}}))
        while True:
            m=json.loads(ws.recv())
            if m.get("id")==1: print(url[:45],"=>",m["result"]["result"].get("value")); break
        ws.close()
    except Exception as e:
        print(url[:45],"ERR",e)
