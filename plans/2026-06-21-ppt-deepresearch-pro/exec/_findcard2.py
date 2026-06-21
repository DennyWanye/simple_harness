import json, urllib.request
from websocket import create_connection
CDP="http://127.0.0.1:9333"
ts=json.loads(urllib.request.urlopen(CDP+"/json",timeout=5).read())
for t in ts:
    if t.get("type")!="page": continue
    url=t.get("url","")
    try:
        ws=create_connection(t["webSocketDebuggerUrl"],timeout=10,suppress_origin=True)
        # 用 ascii 安全的探测:是否有 ppt_outline 卡 / 确认生成按钮 / outline_md
        expr=r"""JSON.stringify({
          hasConfirmBtn: [...document.querySelectorAll('button')].some(b=>(b.innerText||'').includes('确认生成')),
          hasModify: [...document.querySelectorAll('button')].some(b=>(b.innerText||'').includes('修改')),
          cardText: (document.body.innerText.match(/确认生成|历史大纲|让我改改/g)||[]).length,
          msgRoles: (window.__DESKPET_DEBUG__&&0)||'na'
        }).replace(/[^\x00-\x7f]/g,'?')"""
        ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr,"returnByValue":True}}))
        while True:
            m=json.loads(ws.recv())
            if m.get("id")==1:
                v=m["result"]["result"].get("value")
                tag="ROOT" if url.rstrip().endswith(":5173/") else ("CODE" if "code-panel" in url else "MSG")
                print(tag, v); break
        ws.close()
    except Exception as e:
        print(url[:30],"ERR",str(e)[:60])
