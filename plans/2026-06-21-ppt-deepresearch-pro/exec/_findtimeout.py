import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and x.get("url","").rstrip().endswith(":5173/")][0]
ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{
 const els=[...document.querySelectorAll('*')].filter(e=>/对话超时|超时\(分钟\)|对话超时\(分钟\)/.test(e.textContent||'')&&e.children.length<3);
 if(!els.length) return JSON.stringify({found:false});
 const el=els[0]; el.scrollIntoView({block:'center'});
 // 找附近的 number input 当前值
 let inp=el.closest('div')?.querySelector('input[type=number]')||document.querySelector('input[type=number]');
 const dpr=devicePixelRatio,sx=screenX,sy=screenY,r=el.getBoundingClientRect();
 return JSON.stringify({found:true,label:(el.textContent||'').trim().slice(0,40),inputValue:inp?inp.value:null,
   y:Math.round((sy+r.top)*dpr)});})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1: print(m["result"]["result"].get("value")); break
ws.close()
