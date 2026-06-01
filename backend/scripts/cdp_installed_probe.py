# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""实测安装版桌宠(F:\\deskpet)的登录状态 + provider/模型列表 + code 换模型。
CDP 连安装版 WebView2 (9222, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 开的)。"""
import asyncio, json, sys, urllib.request
import websockets
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
_mid=[0]

def find_page(frag):
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    for t in d:
        if t.get("type")=="page" and frag in t.get("url",""): return t
    for t in d:
        if t.get("type")=="page": return t
    return None

async def cdp(ws,m,p=None):
    _mid[0]+=1; mid=_mid[0]
    await ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
    while True:
        r=json.loads(await ws.recv())
        if r.get("id")==mid: return r.get("result",{})

async def ev(ws,expr):
    r=await cdp(ws,"Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":True})
    return r.get("result",{}).get("value")

async def main():
    # 列所有 page
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    print("[pages]", [t.get("url","")[:60] for t in d if t.get("type")=="page"])
    tgt=find_page("code-panel") or find_page("")
    if not tgt: print("[FAIL] 无 page"); return 1
    print(f"[CDP] 连 {tgt['url']}")
    async with websockets.connect(tgt["webSocketDebuggerUrl"],max_size=None) as ws:
        await cdp(ws,"Runtime.enable")
        # 1. 登录状态 + localStorage token
        login=await ev(ws,r"""
(()=>{
  const ls={}; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i); if(/auth|token|relay|user|account|login|provider/i.test(k)) ls[k]=(localStorage.getItem(k)||'').slice(0,60);}
  const body=document.body?document.body.innerText:'';
  return {
    hasLoginWord: /登录|未登录|login|sign in/i.test(body),
    bodyHead: body.slice(0,200),
    authKeys: ls,
  };
})()
""")
        print("\n[1] 登录线索:")
        print("  body含'登录'字样:", login.get("hasLoginWord"))
        print("  localStorage auth/token/provider keys:", json.dumps(login.get("authKeys",{}),ensure_ascii=False))
        print("  body head:", repr(login.get("bodyHead",""))[:220])
        # 2. provider / 模型 chip / 项目
        ui=await ev(ws,r"""
(()=>{
  const txt=(sel)=>Array.from(document.querySelectorAll(sel)).map(e=>(e.textContent||'').trim()).filter(Boolean);
  // 模型 chip 候选
  const modelish=Array.from(document.querySelectorAll('button,[class*="model" i],[data-testid*="model" i]'))
    .map(e=>({t:(e.textContent||'').trim().slice(0,40),tid:e.getAttribute('data-testid')||'',cls:(e.className||'').toString().slice(0,30)}))
    .filter(x=>/模型|model|默认|✎|provider/i.test(x.t+x.tid+x.cls));
  return {
    projectCount: document.querySelectorAll('[class*="session" i],[class*="project" i]').length,
    modelChips: modelish.slice(0,8),
    selectCount: document.querySelectorAll('select').length,
    selects: txt('select').slice(0,5),
  };
})()
""")
        print("\n[2] UI 元素:")
        print("  模型相关 chip/button:", json.dumps(ui.get("modelChips",[]),ensure_ascii=False))
        print("  select 下拉数:", ui.get("selectCount"), "内容:", json.dumps(ui.get("selects",[]),ensure_ascii=False))
        # 3. 实操点 "默认模型 ✎" chip → dump ChangeModelModal 模型列表
        clicked=await ev(ws,r"""
(()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>/默认模型|✎/.test(x.textContent||''));
 if(!b) return 'no-chip'; b.click(); return 'clicked:'+(b.textContent||'').trim();})()
""")
        await asyncio.sleep(1.2)
        modal=await ev(ws,r"""
(()=>{
  const m=document.querySelector('[class*="modal" i],[role="dialog"],[class*="Modal" i]');
  const selects=Array.from(document.querySelectorAll('select')).map(s=>({n:s.options.length,opts:Array.from(s.options).map(o=>o.text).slice(0,30)}));
  const listItems=Array.from(document.querySelectorAll('[role="option"],[class*="model-item" i],li')).map(e=>(e.textContent||'').trim()).filter(Boolean).slice(0,30);
  return {modalOpen:!!m, modalText:m?m.innerText.slice(0,400):'(无modal)', selects, listItems};
})()
""")
        print("\n[3] 实操点 '默认模型 ✎' chip:")
        print("  点击结果:", clicked)
        print("  modal 打开:", modal.get("modalOpen"))
        print("  modal 文本:", repr(modal.get("modalText",""))[:400])
        print("  下拉选项:", json.dumps(modal.get("selects",[]),ensure_ascii=False)[:500])
        print("  列表项:", json.dumps(modal.get("listItems",[]),ensure_ascii=False)[:300])
    return 0

if __name__=="__main__":
    sys.exit(asyncio.run(main()))
