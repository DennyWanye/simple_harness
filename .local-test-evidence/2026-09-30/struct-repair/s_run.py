"""收口第 6 项补验：真实模型下"结构修复 = 出第 2 版计划"（2026-09-30，SDK opt.95）。

设计：严格引用（文档研究领域）任务，带一份资料 sources/policy.md，两步：先写 summary.md（逐条引用资料），
再据 summary.md 写 faq.md。第一步验收通过（AcceptanceCommitted）后，把资料换成新版本（改一条规则）。
这时"已通过的结果引用了旧版资料"→ 系统发证据失效修复请求（方案 B）→ 期望规划器为已完成的那一步
提出后继步骤（PROPOSE_SUCCESSOR）或其它结构修复 → 第 2 版计划（PlanRevisionCommitted 第二次）→ 用新版完成。
入口与界面相同的控制通道消息；只读跟随；15 分钟无变化或总 120 分钟即停。
"""
import asyncio, hashlib, json, sys, time, uuid
import websockets

OUT = sys.argv[1] if len(sys.argv) > 1 else "run1.json"
PATH = "sources/policy.md"
V1 = ("# 值班规则（第 1 版）\n\n1. 每周一 10:00 交接值班。\n2. 值班人手机 24 小时开机。\n"
      "3. P1 故障 15 分钟内响应。\n")
V2 = ("# 值班规则（第 2 版）\n\n1. 每周一 09:00 交接值班。\n2. 值班人手机 24 小时开机。\n"
      "3. P1 故障 10 分钟内响应。\n4. 交接时写清未关闭事项。\n")
MISSION = {
    "goal": "分两步完成：第一步，根据资料 sources/policy.md 写出 summary.md，逐条概括全部值班规则，每条都引用资料原文；"
            "第二步，根据 summary.md 写出 faq.md，给出三个问答，答案必须与 summary.md 一致。只写这两个文件。",
    "success_criteria": [
        "file:summary.md",
        "summary.md 逐条概括 sources/policy.md 的全部规则并引用原文",
        "file:faq.md",
        "faq.md 有三个问答，答案与 summary.md 一致",
    ],
    "domain": "doc-research-v1",
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
    ref = w["requirements_ref"]
    return {"mission_id": w["mission_id"], "expected_requirements_ref": ref,
            "proposal": {"schema_version": 1, "mission_id": w["mission_id"],
                         "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
                         "mode": "CONTENT_ONLY", "content_criterion_ids": [c["id"] for c in as_list(w.get("criteria"))],
                         "effects": []},
            "command_id": f"s-confirm-{uuid.uuid4().hex[:8]}"}


async def main():
    out = {"mission": MISSION, "steps": []}
    start = time.time()

    def note(kind, value):
        out["steps"].append({"t": round(time.time() - start), kind: value})
        print(kind, json.dumps(value, ensure_ascii=False)[:600], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=sreal-"
                                  + uuid.uuid4().hex[:10], max_size=None) as ws:
        created = await call(ws, "mission_create_with_sources", {
            "mission": {**MISSION, "idempotency_key": f"s-real-{uuid.uuid4().hex[:10]}"},
            "sources": [{"path": PATH, "content": V1, "kind": "markdown"}]})
        mid = (created.get("data") or {}).get("mission_id")
        note("created", {"ok": created.get("ok"), "mission_id": mid, "error": created.get("error")})
        if not mid:
            return
        out["mission_id"] = mid
        confirmed, superseded, decided, seen_seq = False, False, set(), 0
        accepted_tasks, revisions = [], 0
        last, last_change = None, time.time()
        while True:
            detail = (await call(ws, "mission_get", {"mission_id": mid})).get("data") or {}
            mission = detail.get("mission") or {}
            w = detail.get("operation_workspace") or {}
            if not confirmed and w.get("editable") is True:
                got = await call(ws, "mission_operation_completion_approve", confirm_body(w))
                confirmed = bool(got.get("ok"))
                note("confirm", {"ok": got.get("ok"), "error": got.get("error")})
            # 接口每页上限 200；翻到最新为止（第 1 局用了 300，每次都被拒，脚本一条事件都没收到）。
            events = []
            while True:
                page = await call(ws, "mission_events", {"mission_id": mid, "after_seq": seen_seq, "limit": 200})
                if not page.get("ok"):
                    note("events_error", page.get("error"))
                    break
                data = page.get("data") or {}
                batch = data.get("events") or []
                events.extend(batch)
                if batch:
                    seen_seq = max(seen_seq, max(int(e.get("seq") or 0) for e in batch))
                if not data.get("has_more"):
                    break
            for event in events:
                kind = str(event.get("type") or event.get("event_type") or "")
                payload = event.get("payload") or {}
                if kind == "AcceptanceCommitted":
                    accepted_tasks.append(event.get("task_id"))
                if kind == "PlanRevisionCommitted":
                    revisions += 1
                if any(k in kind for k in ("Source", "Repair", "PlanRevision", "PlanningDecision", "PlanningHuman", "PlanningRetry",
                                           "PlanningWait", "AttemptStarted", "VerificationFailed", "AcceptanceCommitted",
                                           "TaskCompleted", "RootReview")):
                    slim = {k: payload.get(k) for k in ("decision_type", "status", "source_key", "recut_reasons", "repair_kind") if payload.get(k)}
                    note("event", {"seq": seen_seq, "type": kind, "task": event.get("task_id"), **slim})
            if confirmed and not superseded and accepted_tasks:
                got = await call(ws, "mission_source_supersede", {
                    "mission_id": mid, "path": PATH, "content": V2, "kind": "markdown",
                    "idempotency_key": f"s-sup-{uuid.uuid4().hex[:8]}", "expected_version_hash": h(V1)})
                superseded = bool(got.get("ok"))
                note("supersede", {"after_accepted": accepted_tasks[:1], "ok": got.get("ok"), "error": got.get("error")})
            for q in as_list(detail.get("planning_questions")):
                if str(q.get("state")) != "PENDING" or q.get("decision_id") in decided:
                    continue
                decided.add(q.get("decision_id"))
                options = as_list(q.get("options"))
                answer = str(options[0].get("key")) if options else "资料已换成第 2 版，请按 sources/policy.md 当前内容重做受影响的部分。"
                got = await call(ws, "mission_planning_answer", {"decision_id": q.get("decision_id"), "answer": answer,
                                                                 "expected_version": q.get("version"), "nonce": uuid.uuid4().hex})
                note("planning_answer", {"question": str(q.get("question"))[:300], "answer": answer, "ok": got.get("ok")})
            approvals = ((await call(ws, "mission_approval_list", {"mission_id": mid})).get("data") or {}).get("approvals") or []
            for item in approvals:
                aid = item.get("request_id") or item.get("approval_id") or item.get("id")
                if not aid or aid in decided or str(item.get("status") or "PENDING").upper() not in ("PENDING", "OPEN"):
                    continue
                decided.add(aid)
                got = await call(ws, "mission_approval_decide", {"approval_id": aid, "decision": "approve"})
                note("approve", {"approval_id": aid, "kind": item.get("kind") or item.get("action"), "ok": got.get("ok"), "error": got.get("error")})
            state = (mission.get("status"), mission.get("ui_state"), w.get("state"))
            if state != last:
                last, last_change = state, time.time()
                note("mission", state)
            if mission.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            if time.time() - last_change > 900 or time.time() - start > 7200:
                note("watchdog", "stalled_or_timeout")
                break
            await asyncio.sleep(8)
        out["plan_revisions"] = revisions
        note("summary", {"plan_revisions": revisions, "status": mission.get("status")})
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
