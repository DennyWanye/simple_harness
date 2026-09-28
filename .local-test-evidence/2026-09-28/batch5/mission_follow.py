"""第六批 E5：强制退出重启后，跟随已有任务到结束；卡住 12 分钟无进展即停。"""
import asyncio, json, sys, time, uuid
import websockets

OUT, MID = sys.argv[1], sys.argv[2]
GOAL = "写一份三条的周会要点清单，保存为 notes.md：每条一句话，内容围绕'下周读书会的准备事项'。"
CRITERIA = ["notes.md 存在，恰好三条要点", "每条要点都与读书会准备有关"]


async def call(ws, type_, payload, timeout=120):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


async def main():
    out = {"mission_id": MID, "timeline": []}
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=batch5-mission", max_size=None) as ws:
        mid = MID
        start, last_change, last = time.time(), time.time(), None
        while True:
            got = await call(ws, "mission_get", {"mission_id": mid})
            m = (got.get("data") or {}).get("mission") or {}
            sig = (m.get("status"), m.get("last_seq") or (got.get("data") or {}).get("last_seq"))
            if sig != last:
                last, last_change = sig, time.time()
                out["timeline"].append({"t": round(time.time() - start), "status": m.get("status"), "ui_state": m.get("ui_state")})
                print(json.dumps(out["timeline"][-1], ensure_ascii=False), flush=True)
            if m.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                out["final"] = got; break
            if time.time() - last_change > 1500:
                out["final"] = got; out["stalled"] = True; print("STALLED", flush=True); break
            if time.time() - start > 2700:
                out["final"] = got; out["timeout"] = True; print("TIMEOUT", flush=True); break
            await asyncio.sleep(20)
        out["mission_id"] = mid
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
