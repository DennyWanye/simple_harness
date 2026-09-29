"""架构方案 B 真实模型验证（2026-09-30，SDK opt.92）：任务跑到一半换资料版本 → 重新规划 → 用新数据完成。

建任务时带一份资料 sources/sales.csv（华东 100 / 华北 50）；等报告那步开始执行（ui_state=running）
就换版本（华东 999 / 华北 50，需一次批准）。期望：系统对正在跑的尝试发"证据失效"修复请求 →
规划器处理（原方法重试或按验收失败重做）→ 新尝试挂载新版 → 报告里是 999 → 任务完成；
修复请求被系统或计划改动消费，不再欠修复。入口与界面相同的控制通道消息；只读跟随。
"""
import asyncio, hashlib, json, os, sys, time, uuid
import websockets

OUT = sys.argv[1] if len(sys.argv) > 1 else "run1.json"
V1 = "region,amount\n华东,100\n华北,50\n"
V2 = "region,amount\n华东,999\n华北,50\n"
PATH = "sources/sales.csv"
MISSION = {
    "goal": "读取任务资料 sources/sales.csv（两列：region,amount），按地区汇总销售额，写成 sales_report.md，"
            "表格里的数字必须与资料一致；只写这一个文件。",
    "success_criteria": ["file:sales_report.md", "sales_report.md 含按地区汇总的表格，数字与 sources/sales.csv 一致"],
}


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def call(ws, type_, payload, timeout=180):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


def as_list(v):
    return v if isinstance(v, list) else []


def confirm_body(w):
    criteria = as_list(w.get("criteria"))
    content = [c["id"] for c in criteria]
    ref = w["requirements_ref"]
    return {"mission_id": w["mission_id"], "expected_requirements_ref": ref,
            "proposal": {"schema_version": 1, "mission_id": w["mission_id"],
                         "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
                         "mode": "CONTENT_ONLY", "content_criterion_ids": content, "effects": []},
            "command_id": f"b-confirm-{uuid.uuid4().hex[:8]}"}


async def main():
    out = {"mission": MISSION, "steps": []}
    start = time.time()

    def note(kind, value):
        out["steps"].append({"t": round(time.time() - start), kind: value})
        print(kind, json.dumps(value, ensure_ascii=False)[:700], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=breal-"
                                  + uuid.uuid4().hex[:10], max_size=None) as ws:
        created = await call(ws, "mission_create_with_sources", {
            "mission": {**MISSION, "idempotency_key": f"b-real-{uuid.uuid4().hex[:10]}"},
            "sources": [{"path": PATH, "content": V1, "kind": "text"}]})
        mid = (created.get("data") or {}).get("mission_id")
        note("created", {"ok": created.get("ok"), "mission_id": mid, "error": created.get("error"),
                         "data": {k: v for k, v in (created.get("data") or {}).items() if k != "mission_id"}})
        if not mid:
            return
        out["mission_id"] = mid
        confirmed, superseded, decided, seen_seq = False, False, set(), 0
        last, last_change = None, time.time()
        while True:
            detail = (await call(ws, "mission_get", {"mission_id": mid})).get("data") or {}
            mission = detail.get("mission") or {}
            w = detail.get("operation_workspace") or {}
            if not confirmed and w.get("editable") is True:
                got = await call(ws, "mission_operation_completion_approve", confirm_body(w))
                confirmed = bool(got.get("ok"))
                note("confirm", {"ok": got.get("ok"), "error": got.get("error")})
            if confirmed and not superseded and mission.get("ui_state") == "running":
                got = await call(ws, "mission_source_supersede", {
                    "mission_id": mid, "path": PATH, "content": V2, "kind": "text",
                    "idempotency_key": f"b-sup-{uuid.uuid4().hex[:8]}", "expected_version_hash": h(V1)})
                superseded = bool(got.get("ok"))
                note("supersede", {"ok": got.get("ok"), "error": got.get("error"), "data": got.get("data")})
            for q in as_list(detail.get("planning_questions")):
                if str(q.get("state")) != "PENDING" or q.get("decision_id") in decided:
                    continue
                decided.add(q.get("decision_id"))
                options = as_list(q.get("options"))
                answer = str(options[0].get("key")) if options else "资料已经换成新版本，请按 sources/sales.csv 的当前内容重做报告。"
                got = await call(ws, "mission_planning_answer", {"decision_id": q.get("decision_id"), "answer": answer,
                                                                 "expected_version": q.get("version"), "nonce": uuid.uuid4().hex})
                note("planning_answer", {"question": str(q.get("question"))[:300], "answer": answer, "ok": got.get("ok"), "error": got.get("error")})
            approvals = ((await call(ws, "mission_approval_list", {"mission_id": mid})).get("data") or {}).get("approvals") or []
            for item in approvals:
                aid = item.get("request_id") or item.get("approval_id") or item.get("id")
                if not aid or aid in decided or str(item.get("status") or "PENDING").upper() not in ("PENDING", "OPEN"):
                    continue
                decided.add(aid)
                got = await call(ws, "mission_approval_decide", {"approval_id": aid, "decision": "approve"})
                note("approve", {"approval_id": aid, "kind": item.get("kind") or item.get("action"), "ok": got.get("ok"), "error": got.get("error")})
            events = ((await call(ws, "mission_events", {"mission_id": mid, "after_seq": seen_seq, "limit": 300}))
                      .get("data") or {}).get("events") or []
            for event in events:
                seen_seq = max(seen_seq, int(event.get("seq") or event.get("sequence") or 0))
                kind = str(event.get("type") or event.get("event_type") or "")
                if any(k in kind for k in ("Source", "Repair", "PlanRevision", "PlanningDecision", "PlanningHuman", "PlanningRetry",
                                           "AttemptStarted", "VerificationFailed", "ResultRejected", "TaskCompleted", "AcceptanceCommitted",
                                           "RootReview")):
                    payload = event.get("payload") or {}
                    slim = {k: payload.get(k) for k in ("decision_type", "source_key", "reason", "recut_reasons", "path") if payload.get(k)}
                    note("event", {"seq": seen_seq, "type": kind, **slim})
            state = (mission.get("status"), mission.get("ui_state"), w.get("state"))
            if state != last:
                last, last_change = state, time.time()
                note("mission", state)
            if mission.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            if time.time() - last_change > 900 or time.time() - start > 5400:
                note("watchdog", "stalled_or_timeout")
                break
            await asyncio.sleep(4 if not superseded else 12)
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
