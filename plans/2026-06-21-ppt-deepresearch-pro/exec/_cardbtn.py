import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
page=[t for t in ts if t.get("type")=="page" and "message-pan" in t.get("url","")][0]
ws=create_connection(page["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
expr=r"""(()=>{const dpr=devicePixelRatio,sx=screenX,sy=screenY;
const out={visible:document.visibilityState,btns:[]};
for(const b of document.querySelectorAll('button')){const t=(b.innerText||'').trim();
 if(/确认生成|修改|取消|历史大纲/.test(t)){const r=b.getBoundingClientRect();
   out.btns.push({t:t.replace(/[^\x00-\xff]/g,c=>'\u'+c.charCodeAt(0).toString(16)),x:Math.round((sx+r.left+r.width/2)*dpr),y:Math.round((sy+r.top+r.height/2)*dpr),vis:r.width>0&&r.height>0});}}
// 大纲卡正文前80字(转义)
const card=[...document.querySelectorAll('*')].find(e=>/确认生成/.test(e.innerText||'')&&e.querySelector('button'));
return JSON.stringify(out);})()"""
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
while True:
    m=json.loads(ws.recv())
    if m.get("id")==1: print(m["result"]["result"].get("value")); break
ws.close()
