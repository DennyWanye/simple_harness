"""收口第 1、2 项真实模型验收：设置页同样的控制通道消息——安装技能→开始评估→跟随评估任务→通过后准入。
只读跟随；15 分钟无变化或总 60 分钟即停（看门狗）。结果写 eval_run.json。"""
import asyncio, json, sys, time, uuid
import websockets

BUNDLE = "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/skills/md-table-style.zip"
OUT = "eval_run.json"


async def call(ws, type_, payload, timeout=180):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


def row_of(catalogue):
    return next(r for r in catalogue["skills"] if r["name"] == "md-table-style")


async def main():
    out = {"steps": []}
    def note(k, v):
        out["steps"].append({"t": round(time.time() - start), k: v}); print(k, json.dumps(v, ensure_ascii=False)[:600], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    start = time.time()
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=skills-real", max_size=None) as ws:
        got = await call(ws, "orchestration_skill_install_file", {"path": BUNDLE})
        assert got.get("ok"), got
        row = row_of(got["data"]["catalogue"]); note("installed", {"state": row["state"], "pools": {k: v["state"] for k, v in row["pools"].items()}})
        if row["state"] not in ("ADMITTED",):
            got = await call(ws, "orchestration_skill_evaluate", {"skill_ref": row["skill_ref"], "command_id": f"real-eval-{int(time.time())}"})
            assert got.get("ok"), got
            ev = got["data"]["evaluation"]; note("evaluation_started", ev)
            mid = ev["mission_id"]
            last, last_change = None, time.time()
            while True:
                m = ((await call(ws, "mission_get", {"mission_id": mid})).get("data") or {}).get("mission") or {}
                sig = (m.get("status"), m.get("ui_state"), m.get("last_seq"))
                if sig != last:
                    last, last_change = sig, time.time(); note("mission", {"status": m.get("status"), "ui_state": m.get("ui_state")})
                if m.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                    out["mission_final"] = m; break
                if time.time() - last_change > 900 or time.time() - start > 3600:
                    note("watchdog", "stalled_or_timeout"); out["mission_final"] = m; break
                await asyncio.sleep(20)
            cat = (await call(ws, "orchestration_skill_catalogue", {}))["data"]
            row = row_of(cat); note("after_mission", {"state": row["state"], "evaluation": row.get("evaluation")})
            if (row.get("evaluation") or {}).get("passed"):
                got = await call(ws, "orchestration_skill_admit", {"skill_ref": row["skill_ref"], "command_id": f"real-admit-{int(time.time())}"})
                note("admit", {"ok": got.get("ok"), "error": got.get("error")})
                if got.get("ok"):
                    row = row_of(got["data"]["catalogue"]); note("admitted", {"state": row["state"], "pools": {k: [v["state"], v["usable"]] for k, v in row["pools"].items()}})
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
