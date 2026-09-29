"""收口第 1～3 项真实模型验收（第三轮）：上一轮评估已通过（opt.86 认根解析证书）→ 准入 →
跑一个表格任务看执行者是否调用已准入技能。只读跟随；15 分钟无变化或总 90 分钟即停。结果写 run2.json。"""
import asyncio, json, time, uuid
import websockets

OUT = "run3.json"
PARALLEL = {"goal": "写一份 NOTES.md，列出三条关于'如何安排周末读书时间'的建议，每条一句话。",
            "success_criteria": ["file:NOTES.md", "NOTES.md 恰好三条建议，每条一句话"]}
TABLE = {"goal": "把下面三种水果的数据整理成 Markdown 表格，保存为 fruits.md：苹果 3 斤 每斤 4.5 元；香蕉 2 斤 每斤 3 元；橙子 5 斤 每斤 6 元。如果技能目录里有合适的技能，先按它的规范来。",
         "success_criteria": ["file:fruits.md", "fruits.md 是一张 Markdown 表格，含三种水果的数量与单价"]}


async def call(ws, type_, payload, timeout=180):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


def row_of(catalogue):
    return next(r for r in catalogue["skills"] if r["name"] == "md-table-style")


async def follow(ws, ids, note, start, limit=5400):
    last, last_change = {}, time.time()
    while True:
        states = {}
        for mid in ids:
            m = ((await call(ws, "mission_get", {"mission_id": mid})).get("data") or {}).get("mission") or {}
            states[mid] = (m.get("status"), m.get("ui_state"))
        if states != last:
            last, last_change = states, time.time(); note("missions", states)
        if all(s[0] in ("COMPLETED", "FAILED", "CANCELLED") for s in states.values()):
            return states
        if time.time() - last_change > 900 or time.time() - start > limit:
            note("watchdog", "stalled_or_timeout"); return states
        await asyncio.sleep(15)


async def main():
    out = {"steps": []}
    start = time.time()
    def note(k, v):
        out["steps"].append({"t": round(time.time() - start), k: v}); print(k, json.dumps(v, ensure_ascii=False)[:700], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=skills-real-3", max_size=None) as ws:
        row = row_of((await call(ws, "orchestration_skill_catalogue", {}))["data"])
        note("before", {"state": row["state"], "evaluation": row.get("evaluation")})
        if not (row.get("evaluation") or {}).get("passed"):
            json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1); return
        got = await call(ws, "orchestration_skill_admit", {"skill_ref": row["skill_ref"], "command_id": f"real-admit-{int(time.time())}"})
        note("admit", {"ok": got.get("ok"), "error": got.get("error")})
        if got.get("ok"):
            row = row_of(got["data"]["catalogue"]); note("admitted", {"state": row["state"], "pools": {k: [v["state"], v["usable"]] for k, v in row["pools"].items()}})
        tab = await call(ws, "mission_create", {**TABLE, "idempotency_key": f"real-table-{int(time.time())}"})
        tab_mid = (tab.get("data") or {}).get("mission_id"); note("table_created", {"ok": tab.get("ok"), "mission_id": tab_mid, "error": tab.get("error")})
        if tab_mid:
            out["phase2"] = await follow(ws, [tab_mid], note, start)
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
