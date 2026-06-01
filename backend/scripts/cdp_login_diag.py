# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""诊断 frozen 安装版：登录成功但 backend cloud_configured=false + 模型列表空。
CDP 连 9222 → 捕获 console → reload 触发 relayProviderBridge.apply → 看哪环断。"""
import asyncio, json, sys, urllib.request
import websockets
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
_mid=[0]; _logs=[]

def find_page(frag):
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    for t in d:
        if t.get("type")=="page" and frag in t.get("url",""): return t
    return next((t for t in d if t.get("type")=="page"), None)

async def cdp(ws,m,p=None):
    _mid[0]+=1; mid=_mid[0]
    await ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
    while True:
        r=json.loads(await ws.recv())
        if r.get("method")=="Runtime.consoleAPICalled":
            a=r["params"].get("args",[])
            txt=" ".join(str(x.get("value",x.get("description",""))) for x in a)
            _logs.append((r["params"].get("type",""),txt))
        if r.get("id")==mid: return r.get("result",{})

async def ev(ws,expr):
    r=await cdp(ws,"Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":True})
    return r.get("result",{}).get("value")

async def recv_bg(ws,secs):
    """后台收 console 事件 secs 秒。"""
    try:
        end=secs
        while end>0:
            r=json.loads(await asyncio.wait_for(ws.recv(),timeout=1))
            if r.get("method")=="Runtime.consoleAPICalled":
                a=r["params"].get("args",[])
                txt=" ".join(str(x.get("value",x.get("description",""))) for x in a)
                _logs.append((r["params"].get("type",""),txt))
            end-=0.1
    except asyncio.TimeoutError: pass
    except Exception: pass

async def main():
    tgt=find_page("message-panel") or find_page("")
    print(f"[CDP] 连 {tgt['url']}")
    async with websockets.connect(tgt["webSocketDebuggerUrl"],max_size=None) as ws:
        await cdp(ws,"Runtime.enable")
        await cdp(ws,"Page.enable")
        # reload 触发 restoreSession + relayProviderBridge.apply
        print("[reload] 触发登录恢复 + provider 推送...")
        await cdp(ws,"Page.reload",{})
        await recv_bg(ws,8)
        # 看 backend cloud 是否被更新 + provider 状态
        backend=await ev(ws,r"""
(async()=>{
  try{ const r=await fetch('http://127.0.0.1:8100/health'); const j=await r.json();
    return {cloud_configured:j.cloud_configured, strategy:j.strategy}; }
  catch(e){ return {err:String(e)}; }
})()
""")
        print("\n[backend health via 前端 fetch]:", json.dumps(backend,ensure_ascii=False))
        # console 里 relay/provider/cloud/error 相关
        print("\n[console 日志 (relay/provider/cloud/error/IPC)]:")
        hits=[(t,m) for (t,m) in _logs if any(k in m.lower() for k in ["relay","provider","cloud","updatecloud","ipc","invoke","error","fail","401","tsk_","model"])]
        for t,m in hits[:25]: print(f"  [{t}] {m[:160]}")
        if not hits: print("  (无相关 console — 可能 bridge 没触发/日志被 release 剥离)")
        print(f"\n[console 总行数] {len(_logs)}")
    return 0

if __name__=="__main__":
    sys.exit(asyncio.run(main()))
