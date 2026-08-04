import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
t=[x for x in ts if x.get("type")=="page" and "message-pan" in x.get("url","")][0]
ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
def ev(e):
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":e,"returnByValue":True}}))
    while True:
        m=json.loads(ws.recv())
        if m.get("id")==1: return m["result"]["result"].get("value")
# 找"确认生成"按钮 scrollIntoView,返回其屏幕坐标
expr=r"""(()=>{
 const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').includes('确认生成'));
 if(!b) return 'NO_BTN';
 b.scrollIntoView({block:'center'});
 const r=b.getBoundingClientRect();
 return JSON.stringify({x:Math.round((screenX+r.left+r.width/2)*devicePixelRatio),y:Math.round((screenY+r.top+r.height/2)*devicePixelRatio),w:Math.round(r.width),top:Math.round(r.top)});
})()"""
print(ev(expr))
ws.close()
