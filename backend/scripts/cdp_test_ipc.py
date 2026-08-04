# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""决定性实测：CDP 在 pet 主窗手动触发 update_cloud_config IPC，
测 Rust IPC 通道是否工作(区分 IPC坏 vs login事件没触发apply)。
无害：用已知 chinzy base_url + 不传 key(保留当前)。"""
import asyncio, json, sys, urllib.request
import websockets
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
_mid=[0]

def pet_main():
    d=json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list").read())
    pages=[t for t in d if t.get("type")=="page"]
    for t in pages:
        if t.get("url","").rstrip("/").endswith("tauri.localhost"): return t
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
    return r.get("result",{})

async def main():
    tgt=pet_main()
    print(f"[CDP] 连 {tgt['url']}")
    async with websockets.connect(tgt["webSocketDebuggerUrl"],max_size=None) as ws:
        await cdp(ws,"Runtime.enable")
        # 探测 Tauri invoke 入口
        probe=await ev(ws,r"""
(()=>{
  const k=Object.keys(window).filter(x=>/tauri/i.test(x));
  const hasInternals=!!(window.__TAURI_INTERNALS__&&window.__TAURI_INTERNALS__.invoke);
  const hasCore=!!(window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke);
  return {tauriKeys:k, hasInternals, hasCore};
})()
""")
        print("[tauri 入口]:", json.dumps(probe.get("result",{}).get("value",{}),ensure_ascii=False))
        # 手动调 update_cloud_config (无害: chinzy base_url + 不传key)
        r=await ev(ws,r"""
(async()=>{
  const inv=(window.__TAURI_INTERNALS__&&window.__TAURI_INTERNALS__.invoke)||(window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke);
  if(!inv) return {err:'no invoke fn'};
  try{
    const res=await inv('update_cloud_config',{update:{base_url:'https://chinzy.com/v1',model:'gpt-5.5'}});
    return {ipc_ok:true, result:res};
  }catch(e){ return {ipc_ok:false, err:String(e)}; }
})()
""")
        out=r.get("result",{}).get("value",{}) or r.get("exceptionDetails")
        print("\n[手动 update_cloud_config IPC 结果]:")
        print(" ", json.dumps(out,ensure_ascii=False)[:400])
    # 调用后看 backend cloud 是否变化
    await asyncio.sleep(1)
    try:
        h=json.loads(urllib.request.urlopen("http://127.0.0.1:8100/health").read())
        print("\n[调用后 backend cloud_configured]:", h.get("cloud_configured"))
    except Exception as e:
        print("[health]", e)
    return 0

if __name__=="__main__":
    sys.exit(asyncio.run(main()))
