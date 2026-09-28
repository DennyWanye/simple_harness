"""第五批真机：通过真实控制通道（开发模式）操作统一技能目录：总览→从本地安装→重复安装→退役→再总览。"""
import asyncio, json, sys, uuid
import websockets

OUT, BUNDLE = sys.argv[1], sys.argv[2]


async def call(ws, type_, payload, timeout=120):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


async def main():
    out = {}
    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=batch5-probe", max_size=None) as ws:
        out["status"] = (await call(ws, "orchestration_status", {}))["data"].get("native_plane")
        out["before"] = await call(ws, "orchestration_skill_catalogue", {})
        out["install"] = await call(ws, "orchestration_skill_install_file", {"path": BUNDLE})
        out["install_again"] = await call(ws, "orchestration_skill_install_file", {"path": BUNDLE})
        skills = out["install"]["data"]["catalogue"]["skills"] if out["install"].get("ok") else []
        if skills:
            ref = skills[0]["skill_ref"]
            out["suspend_refused"] = await call(ws, "orchestration_skill_lifecycle", {"skill_ref": ref, "action": "SUSPEND", "command_id": "probe-s1"})
            out["retire"] = await call(ws, "orchestration_skill_lifecycle", {"skill_ref": ref, "action": "RETIRE", "command_id": "probe-r1"})
        try:
            out["closed_verb"] = await call(ws, "agent_runtime_request", {"verb": "agent_skills_list"}, timeout=8)
        except TimeoutError:
            out["closed_verb"] = "no_response (not routed on the control channel)"
        out["after"] = await call(ws, "orchestration_skill_catalogue", {})
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
