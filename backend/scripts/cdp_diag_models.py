# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""诊断 backend code_models_list：直连 ws/control 发 code_models_list，
看 provider_registry chain 是否空(决定 code 模型列表为何空)。
dev 模式 WS auth 开放,纯诊断 backend 逻辑(非 UI 证据)。"""
import asyncio, json, sys
import websockets
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

async def main():
    uri="ws://127.0.0.1:8100/ws/control?session_id=diag-models"
    try:
        async with websockets.connect(uri, max_size=None) as ws:
            await ws.send(json.dumps({"type":"code_models_list"}))
            for _ in range(20):
                try:
                    raw=await asyncio.wait_for(ws.recv(), timeout=12)
                except asyncio.TimeoutError:
                    print("[超时] 12s 未收到 code_models_list_response"); return 1
                m=json.loads(raw)
                if m.get("type")=="code_models_list_response":
                    p=m.get("payload",{})
                    models=p.get("models",[])
                    print(f"[code_models_list_response]")
                    print(f"  source: {p.get('source')!r}  (none=registry空 / live=拉到 / config=fallback)")
                    print(f"  base_url: {p.get('base_url')!r}")
                    print(f"  模型数: {len(models)}")
                    print(f"  前10: {[x.get('id') for x in models[:10]]}")
                    return 0
                # 跳过其它消息
            print("[未收到 response]"); return 1
    except Exception as e:
        print(f"[ws 连接失败] {type(e).__name__}: {e}"); return 1

if __name__=="__main__":
    sys.exit(asyncio.run(main()))
