"""收口第 6 项第 6 轮（2026-09-30，SDK opt.91 = 架构方案 C）：用户回答附成资料，执行者能读到文件。

与第 2～5 轮同一道题：两件互不相关的事，其中一件依赖不存在的 data/sales.csv。执行者报缺数据 →
修复请求 → 规划器问人 → 脚本以人的身份回答并 **attach_as_source=True**（回答登记成
sources/answers/<问题id>.md）→ 规划器原方法重试 → 新尝试挂载这份资料 → 报告写出 → 任务完成。
入口与界面相同的控制通道消息；只读跟随；15 分钟无变化或总 120 分钟即停。
"""
import asyncio, json, os, sys, time, uuid
import websockets

OUT = sys.argv[1] if len(sys.argv) > 1 else "run6.json"
TASK = {
    "goal": "完成两件互不相关的事：(1) 把下面的团队值班规则整理成 oncall.md：每周一 10 点交接；值班人手机 24 小时开机；"
            "P1 故障 15 分钟内响应；交接时写清未关闭事项。(2) 读取工作区里的 data/sales.csv，按地区汇总三个月的销售额，"
            "写成 sales_report.md。",
    "success_criteria": [
        "file:oncall.md",
        "oncall.md 完整列出上面 4 条值班规则",
        "file:sales_report.md",
        "sales_report.md 含按地区汇总的表格，数字与 data/sales.csv 一致",
    ],
}
ANSWER = ("工作区里确实没有 data/sales.csv。销售数据如下，这份回答已作为资料附在任务里，请直接读取这份资料，"
          "按它汇总（它就是 data/sales.csv 的内容）：\n\n"
          "month,region,amount\n2026-07,华东,120\n2026-07,华北,90\n2026-08,华东,135\n2026-08,华北,110\n"
          "2026-09,华东,150\n2026-09,华北,95\n")


async def call(ws, type_, payload, timeout=180):
    rid = str(uuid.uuid4())
    await ws.send(json.dumps({"type": type_, "request_id": rid, "payload": payload}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == type_ + "_response" and (msg.get("payload") or {}).get("request_id") == rid:
            return msg["payload"]


def as_list(value):
    return value if isinstance(value, list) else []


def confirm_body(w):
    criteria = as_list(w.get("criteria"))
    is_action = lambda c: str(c.get("statement") or c.get("text") or "").startswith("action:") or c.get("kind") == "action"
    content = [c["id"] for c in criteria if not is_action(c)]
    milestones, obligations = as_list(w.get("milestones")), as_list(w.get("obligations"))
    hash_ms = next((m for m in milestones if "哈希" in str(m.get("label") or "")), milestones[0] if len(milestones) == 1 else None) or {}
    effects = []
    for c in criteria:
        if is_action(c):
            key = f"effect-{uuid.uuid4().hex[:8]}"
            effects.append({"effect_key": key, "source_slot_key": key,
                            "obligation_id": obligations[0]["id"] if len(obligations) == 1 else "",
                            "criterion_ids": [c["id"]], "required_milestone": hash_ms["id"],
                            "milestone_policy_ref": hash_ms["milestone_policy_ref"],
                            "evidence_policy_ref": hash_ms["evidence_policy_ref"]})
    ref = w["requirements_ref"]
    return {"mission_id": w["mission_id"], "expected_requirements_ref": ref,
            "proposal": {"schema_version": 1, "mission_id": w["mission_id"],
                         "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
                         "mode": "REQUIRED_EFFECTS" if effects else "CONTENT_ONLY",
                         "content_criterion_ids": content, "effects": effects},
            "command_id": f"e3-confirm-{uuid.uuid4().hex[:8]}"}


async def main():
    out = {"task": TASK, "steps": []}
    start = time.time()

    def note(kind, value):
        out["steps"].append({"t": round(time.time() - start), kind: value})
        print(kind, json.dumps(value, ensure_ascii=False)[:800], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=e3r6-"
                                  + uuid.uuid4().hex[:10], max_size=None) as ws:
        mid = os.environ.get("E3_MISSION")
        if mid:
            note("attached", {"mission_id": mid})
        else:
            created = await call(ws, "mission_create", {**TASK, "idempotency_key": f"e3r6-{uuid.uuid4().hex[:10]}"})
            mid = (created.get("data") or {}).get("mission_id")
            note("created", {"ok": created.get("ok"), "mission_id": mid, "error": created.get("error")})
        if not mid:
            return
        out["mission_id"] = mid
        confirmed, decided, seen_seq = bool(os.environ.get("E3_MISSION")), set(), 0
        last, last_change = None, time.time()
        while True:
            detail = (await call(ws, "mission_get", {"mission_id": mid})).get("data") or {}
            mission = detail.get("mission") or {}
            w = detail.get("operation_workspace") or {}
            if not confirmed and w.get("editable") is True:
                body = confirm_body(w)
                got = await call(ws, "mission_operation_completion_approve", body)
                confirmed = bool(got.get("ok"))
                note("confirm", {"ok": got.get("ok"), "error": got.get("error"),
                                 "content": body["proposal"]["content_criterion_ids"],
                                 "effects": [e["criterion_ids"] for e in body["proposal"]["effects"]]})
            for q in as_list(detail.get("planning_questions")):
                if str(q.get("state")) != "PENDING" or q.get("decision_id") in decided:
                    continue
                decided.add(q.get("decision_id"))
                options = as_list(q.get("options"))
                pick = next((o for o in options if any(k in str(o.get("label") or "") for k in ("生成", "新增", "补", "创建"))),
                            options[0] if options else None)
                answer = str(pick.get("key")) if pick else ANSWER
                payload = {"decision_id": q.get("decision_id"), "answer": answer,
                           "expected_version": q.get("version"), "nonce": uuid.uuid4().hex}
                if not pick:
                    payload["attach_as_source"] = True  # 架构方案 C：回答同时登记成资料
                got = await call(ws, "mission_planning_answer", payload)
                note("planning_answer", {"question": str(q.get("question"))[:300], "options": options, "answer": answer[:120],
                                         "attach": not pick, "ok": got.get("ok"), "error": got.get("error"),
                                         "source": (got.get("data") or {}).get("source")})
            for r in as_list(detail.get("planning_authorization_requests")):
                key = json.dumps(r, sort_keys=True, ensure_ascii=False)[:200]
                if key not in decided:
                    decided.add(key)
                    note("planning_authorization_request", r)
            approvals = ((await call(ws, "mission_approval_list", {"mission_id": mid})).get("data") or {}).get("approvals") or []
            for item in approvals:
                aid = item.get("request_id") or item.get("approval_id") or item.get("id")
                if not aid or aid in decided or str(item.get("status") or "PENDING").upper() not in ("PENDING", "OPEN"):
                    continue
                decided.add(aid)
                got = await call(ws, "mission_approval_decide", {"approval_id": aid, "decision": "approve"})
                note("approve", {"approval_id": aid, "kind": item.get("kind") or item.get("action"),
                                 "ok": got.get("ok"), "error": got.get("error")})
            events = ((await call(ws, "mission_events", {"mission_id": mid, "after_seq": seen_seq, "limit": 200}))
                      .get("data") or {}).get("events") or []
            for event in events:
                seen_seq = max(seen_seq, int(event.get("seq") or event.get("sequence") or 0))
                kind = str(event.get("type") or event.get("event_type") or "")
                if any(key in kind for key in ("Repair", "PlanRevision", "Planning", "Blocked", "TaskCompleted", "TaskCancelled",
                                               "Source", "Invalidated", "AttemptStarted", "ResultAccepted")):
                    note("event", {"seq": seen_seq, "type": kind})
            state = (mission.get("status"), mission.get("ui_state"), w.get("state"))
            if state != last:
                last, last_change = state, time.time()
                note("mission", state)
            if mission.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            if time.time() - last_change > 900 or time.time() - start > 7200:
                note("watchdog", "stalled_or_timeout")
                break
            await asyncio.sleep(15)
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
