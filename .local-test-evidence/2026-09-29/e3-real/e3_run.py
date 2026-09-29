"""收口第 6 项（计划 §12.1 E3 一次结构修复）真实模型验收。

触发路径：任务只让写 draft/report.md，但完成要求里有"发布 weekly.md"。内容步骤通过后，系统发现
没有能发布成 weekly.md 的来源文件，按"发布找不到来源"请规划器补一个写出它的步骤 → 规划器提案 →
第 2 版计划 → 新步骤写出 weekly.md → 系统准备发布申请 → 批准 → 任务完成。

入口与界面相同的控制通道消息：mission_create / mission_get / mission_operation_completion_approve
（按界面默认映射：普通要求算内容交付，发布要求配一个效果，完成标准选"哈希"那项）/
mission_approval_list / mission_approval_decide / mission_events。只读跟随；15 分钟无变化或总 120 分钟即停。
"""
import asyncio, json, os, sys, time, uuid
import websockets

OUT = sys.argv[1] if len(sys.argv) > 1 else "e3_run.json"
TASK = {
    "goal": "把下面这周的工作记录整理成一份周报正文，保存为 draft/report.md（只写这一个文件）。"
            "定稿后要把它发布为 weekly.md。工作记录：周一修复登录超时；周二评审支付模块设计；"
            "周三上线搜索分页；周四排查内存泄漏；周五编写下周计划。",
    "success_criteria": [
        "file:draft/report.md",
        "draft/report.md 至少三节：本周完成、问题与风险、下周计划",
        "action:file_publish.publish:weekly.md",
    ],
}


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
    hash_ms = next((m for m in milestones if "哈希" in str(m.get("label") or "")), milestones[0] if len(milestones) == 1 else None)
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

    async with websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=e3-real-"
                                  + uuid.uuid4().hex[:8], max_size=None) as ws:
        mid = os.environ.get("E3_MISSION")  # 接管已有任务（例如重启后继续跟随）
        if mid:
            note("attached", {"mission_id": mid})
        else:
            created = await call(ws, "mission_create", {**TASK, "idempotency_key": f"e3-{uuid.uuid4().hex[:10]}"})
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
            if not confirmed and w.get("editable") is True and as_list(w.get("milestones")):
                body = confirm_body(w)
                got = await call(ws, "mission_operation_completion_approve", body)
                confirmed = bool(got.get("ok"))
                note("confirm", {"ok": got.get("ok"), "error": got.get("error"),
                                 "content": body["proposal"]["content_criterion_ids"],
                                 "effects": [e["criterion_ids"] for e in body["proposal"]["effects"]]})
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
                if any(key in kind for key in ("Repair", "PlanRevision", "Planning", "SystemOperation", "Publish", "Operation")):
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
