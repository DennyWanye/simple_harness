import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and x.get("url","").rstrip().endswith(":5173/")][0]
ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{
 const out=[];
 document.querySelectorAll('input[type=number]').forEach(inp=>{
   // 向上找最近含可见文字的容器,取其文字当 label
   let c=inp, lab='';
   for(let i=0;i<6&&c.parentElement;i++){c=c.parentElement;const tx=(c.innerText||'').trim();if(tx&&tx.length<80){lab=tx;break;}}
   out.push({value:inp.value,min:inp.min,max:inp.max,label:lab.replace(/\n/g,' ').slice(0,50)});
 });
 return JSON.stringify(out);})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1: print(m["result"]["result"].get("value")); break
ws.close()
