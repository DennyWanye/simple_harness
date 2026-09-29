"""收口第 7 项界面真机补点：只读跟随界面上新建的两个任务（notes.md 发布任务、weekly-bullets 评估任务）。

有待批准申请、全部结束或 10 分钟无变化即退出（退出时界面去点批准/准入）。最长 60 分钟。
"""
import asyncio, json, sys, time, uuid
import websockets

OUT = sys.argv[1]
ALL = len(sys.argv) > 2  # 第二个参数：只等全部结束


async def call(ws, type_, payload, timeout=120):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


async def main():
    start, log, last, last_change = time.time(), [], None, time.time()
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=" + uuid.uuid4().hex[:12]
                                  + "-uiwatch", max_size=None) as ws:
        while time.time() - start < 3600:
            data = (await call(ws, "mission_list", {})).get("data") or {}
            missions = data.get("missions") if isinstance(data, dict) else data
            recent = [m for m in (missions or []) if ("notes.md" in str(m.get("goal")) or "weekly-bullets" in str(m.get("goal")))
                      ][:4]
            state = {}
            for m in recent[:2]:
                mid = m.get("mission_id")
                approvals = ((await call(ws, "mission_approval_list", {"mission_id": mid})).get("data") or {}).get("approvals") or []
                pending = [a for a in approvals if str(a.get("status") or "PENDING").upper() in ("PENDING", "OPEN")]
                state[mid] = (m.get("status"), m.get("ui_state"), len(pending), str(m.get("goal"))[:30])
            if state != last:
                last, last_change = state, time.time()
                log.append({"t": round(time.time() - start), "state": state}); print(json.dumps(log[-1], ensure_ascii=False), flush=True)
                json.dump(log, open(OUT, "w"), ensure_ascii=False, indent=1)
            if (not ALL and any(v[2] for v in state.values())) or (state and all(v[0] in ("COMPLETED", "FAILED", "CANCELLED") for v in state.values())):
                print("EXIT", flush=True); return
            if time.time() - last_change > 600:
                print("STALL", flush=True); return
            await asyncio.sleep(20)

asyncio.run(main())
