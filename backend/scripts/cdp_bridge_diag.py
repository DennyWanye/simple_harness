# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""连 pet 主窗(RelayEdition 所在)，监听 console 看 relayProviderBridge →
updateCloudConfig 是否失败 + dump bridge 失败 banner。不 reload(保登录)。"""
import asyncio, json, sys, urllib.request
import websockets
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
_mid=[0]; _logs=[]

def pet_main_page():
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    pages=[t for t in d if t.get("type")=="page"]
    # pet 主窗: url 以 / 结尾且无 #/panel
    for t in pages:
        u=t.get("url","")
        if u.rstrip("/").endswith("tauri.localhost") or u.endswith("tauri.localhost/") or (u.endswith("/index.html") and "#" not in u):
            return t
    for t in pages:
        if "#" not in t.get("url",""): return t
    return pages[0] if pages else None

async def cdp(ws,m,p=None):
    _mid[0]+=1; mid=_mid[0]
    await ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
    while True:
        r=json.loads(await ws.recv())
        if r.get("id")==mid: return r.get("result",{})

async def ev(ws,expr):
    r=await cdp(ws,"Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":True})
    return r.get("result",{}).get("value")

async def listen(ws,secs):
    t=secs
    while t>0:
        try:
            r=json.loads(await asyncio.wait_for(ws.recv(),timeout=1))
            if r.get("method")=="Runtime.consoleAPICalled":
                a=r["params"].get("args",[])
                txt=" ".join(str(x.get("value",x.get("description",""))) for x in a)
                _logs.append((r["params"].get("type",""),txt))
        except asyncio.TimeoutError: pass
        except Exception: break
        t-=1

async def main():
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    print("[pages]",[t.get("url","") for t in d if t.get("type")=="page"])
    tgt=pet_main_page()
    if not tgt: print("[FAIL] 无 pet 主窗"); return 1
    print(f"[CDP] 连 pet 主窗 {tgt['url']}")
    async with websockets.connect(tgt["webSocketDebuggerUrl"],max_size=None) as ws:
        await cdp(ws,"Runtime.enable")
        # 登录状态 + bridge banner
        st=await ev(ws,r"""
(()=>{
  const body=document.body?document.body.innerText:'';
  return {
    loggedIn: /退出登录|账户余额|@qq|@/.test(body) || /prepaid|钱包余额/.test(body),
    bridgeBanner: /模型配置失败|点此重试|配置失败|重试/.test(body),
    bodySnippet: body.slice(0,150),
  };
})()
""")
        print("\n[登录态]:", st.get("loggedIn"), "| bridge失败banner:", st.get("bridgeBanner"))
        print("[body]:", repr(st.get("bodySnippet",""))[:160])
        print("\n[监听 console 10s — 捕获 bridge 自动重试/updateCloudConfig 错误]...")
        await listen(ws,10)
        rel=[(t,m) for (t,m) in _logs if any(k in m.lower() for k in ["relay","provider","cloud","updatecloud","bridge","invoke","ipc","error","fail","401","model","secret"])]
        print(f"[console 相关 {len(rel)}/{len(_logs)} 行]:")
        for t,m in rel[:30]: print(f"  [{t}] {m[:170]}")
        if not rel: print("  (无相关 — bridge 可能已 ok 或 console 被 release 剥离)")
    return 0

if __name__=="__main__":
    sys.exit(asyncio.run(main()))
