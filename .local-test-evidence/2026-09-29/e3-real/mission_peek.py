"""只读查看一个任务：步骤状态 + 最近事件类型。用法：python inspect.py <mission_id> [事件条数]"""
import asyncio, json, sys, uuid
import websockets

MID, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40


async def call(ws, type_, payload):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 120))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"].get("data") or {}


async def main():
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=e3-inspect", max_size=None) as ws:
        d = await call(ws, "mission_get", {"mission_id": MID})
        m = d.get("mission") or {}
        print("mission", m.get("status"), m.get("ui_state"), (m.get("error") or "")[:300])
        for t in d.get("tasks") or []:
            print(" task", t.get("task_id"), t.get("status") or t.get("state"),
                  str(t.get("title") or t.get("goal") or "")[:80], str(t.get("wait_reason") or t.get("reason") or "")[:160])
        seqs, after = [], 0
        while True:
            ev = (await call(ws, "mission_events", {"mission_id": MID, "after_seq": after, "limit": 200})).get("events") or []
            if not ev:
                break
            seqs += ev
            after = max(int(e.get("seq") or 0) for e in ev)
        for e in seqs[-N:]:
            p = e.get("payload") or {}
            print(e.get("seq"), e.get("type"), json.dumps({k: p[k] for k in list(p)[:4]}, ensure_ascii=False)[:220])

asyncio.run(main())
