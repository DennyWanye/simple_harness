"""仓库外正式一局（只装发版轮子，只用公开接口）：补充合同 v1.1 的 INT-01。
主会话登记 Goal（三条可机械核验的条件）→ 用户一句话：先并行做 A、B，做完后再开一个任务 C 把两份合并
→ 模型自己开两个工作会话并各委派一个任务 → 两任务结果回流时宿主策略先 hold（模拟"等宿主放行"）
→ 宿主显式重评并带控制动作 delegate_task 放行（授权端口对 continue_within_goal 说允许）
→ 续办回合在同一 Goal 下自动委派 C，没有新的用户消息 → C 完成回流、只读汇总
→ 宿主验收器（机械规则）读真实产物登记三条 pass（证据 = 产物号 + 内容哈希）
→ 登记前 finish 被拒 acceptance_incomplete；登记后 finish 成功，回执绑记录集 → 关掉重开，Goal 与记录都在。
密钥只从环境变量读（run.py 注入）。用法：python goal_chain_game.py <输出目录>"""
import asyncio, json, os, sys, time
from pathlib import Path

from agent_orchestrator.host import (
    AuthorizationDecision, ContinuationDecision, HostSettings, ModelConfig, OrchestrationHost, Principal,
)
from agent_orchestrator.host.sessions import open_session

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
ROOT = OUT / "host-data"
LOG = []
T0 = time.time()
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}


def log(**row):
    row["t"] = round(time.time() - T0, 1)
    LOG.append(row)
    print(json.dumps(row, ensure_ascii=False, default=str)[:900], flush=True)


def model():
    base = os.environ["DEEPSEEKER_BASEURL"]
    return ModelConfig(
        base_url=base, model=os.environ["DEEPSEEKER_MODEL"], api_key=os.environ["DEEPSEEKER_APIKEY"],
        compatible_hosts=(base.split("//")[1].split("/")[0].lower(),),
        response_model_aliases=("deepseek-ai/DeepSeek-V4.1-Flash", "deepseek-flash", "deepseek-v4-flash"),
    )


class HostPolicy:
    """AgentOS 一侧的续办策略替身：宿主没放行前 hold；放行后带 delegate_task 续办。只看元数据。"""

    def __init__(self):
        self.allowed = False
        self.seen = []

    async def decide(self, context):
        self.seen.append((context.event_id[:12], context.reevaluation_id, context.goal_ref))
        if not self.allowed:
            return ContinuationDecision.hold("await_host_release")
        return ContinuationDecision.enqueue(controls=["delegate_task"])


class HostAuthorization:
    """AgentOS 一侧的授权端口替身：记下每个请求；授权内续办与其余动作都允许（auto 模式）。"""

    def __init__(self):
        self.requests = []

    async def decide(self, request):
        self.requests.append((request.action, request.target_ref, request.goal_ref, request.grant_ref))
        return AuthorizationDecision.allow(revision="policy-1")


POLICY, PORT = HostPolicy(), HostAuthorization()


async def open_host():
    return await OrchestrationHost.open(
        ROOT, principal=Principal("accept8", "验收人"), model=model(), settings=HostSettings(),
        auto_mode=True, authorization_port=PORT,
    )


async def wait_until(fn, seconds, every):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        await asyncio.sleep(every)
    return fn()


def artifacts_of(session, host):
    rows = []
    for task in session.tasks():
        mid = task.get("mission_id")
        if not mid:
            continue
        for art in host.snapshot(mid)["snapshot"].get("artifacts", []):
            rows.append((mid, art, task))
    return rows


def validate(session, host):
    """宿主验收器（机械规则）：按文件名找产物、读正文、核长度与引用；给每条条件一个结论。"""
    found = {}
    for mid, art, task in artifacts_of(session, host):
        name = str(art.get("logical_path", "")).rsplit("/", 1)[-1]
        if name in {"guide.md", "meeting.md", "summary.md"} and name not in found:
            body = host.artifact_read(art["id"])
            text = body.get("content") or body.get("text") or ""
            found[name] = {"mission_id": mid, "artifact": art, "text": text, "task": task}
    results = {}
    for cid, name in (("guide", "guide.md"), ("meeting", "meeting.md"), ("summary", "summary.md")):
        hit = found.get(name)
        if hit is None:
            results[cid] = ("fail", [], f"{name} 未交付")
            continue
        text = hit["text"]
        ok = len(text.strip()) > 0 and len(text) <= 1200
        if cid == "summary":
            ok = ok and ("指南" in text or "guide" in text.lower()) and ("例会" in text or "meeting" in text.lower())
        evidence = [{"ref": f"artifact:{hit['mission_id']}:{hit['artifact']['id']}", "content_hash": hit["artifact"]["content_hash"]}]
        results[cid] = ("pass" if ok else "fail", evidence, f"{name} {len(text)} 字")
    return results


async def main():
    host = await open_host()
    session = await open_session(host, "main", continuation_policy=POLICY)
    verdict = {}
    try:
        caps = session.capabilities()
        log(step="capabilities", caps=caps.get("capabilities"), protocol=caps.get("protocol_version"),
            config=caps.get("session_config_version"))
        verdict["capabilities_name_three"] = {"continuation_controls", "continuation_reevaluation", "goal_acceptance_records"} <= set(caps["capabilities"])
        goal = session.put_goal(
            {
                "goal_id": "onboarding-pack",
                "objective": "交付新同事入职三件文档：指南、例会流程、合并摘要",
                "completion_criteria": [
                    {"criterion_id": "guide", "text": "guide.md 已交付，非空且不超过 1200 字"},
                    {"criterion_id": "meeting", "text": "meeting.md 已交付，非空且不超过 1200 字"},
                    {"criterion_id": "summary", "text": "summary.md 已交付，引用了指南与例会两份内容"},
                ],
                "stop_conditions": ["宿主要求停止", "连续自动续办超过 6 轮"],
                "budget": {"max_model_calls": 200},
                "grant_ref": "grant-onboarding",
                "policy_revision": "policy-1",
            },
            command_id="goal-1", expected_version=0,
        )
        log(step="goal", goal=goal)
        # 登记前就想收口：被验收记录拦住（结构事实此时也没收口，但先核结构会报 active_work；这里只是记录现状）
        text_u1 = (
            "请帮我做一个入职资料包，分三步：先并行开两个工作会话各交一个后台任务——"
            "A. 写一份不超过 200 字的《给新同事的第一天指南》，存成 guide.md；"
            "B. 写一份不超过 200 字的《团队每周例会流程》，存成 meeting.md。"
            "等 A、B 两份都完成并回到你这里之后，再开第三个任务 C：把两份内容合并成一份不超过 300 字的 summary.md"
            "（要同时提到指南和例会）。A、B 交出去就先告诉我一声。")
        # 线路故障（中继 5xx / 524）不算这局的失败：换一个幂等键原地重发，最多三次，次数记在日志里
        for attempt in range(1, 6):
            if attempt > 1:
                await asyncio.sleep(60)
            reply = await session.send(text_u1, key=f"u1-{attempt}")
            error = (reply.get("error") or {}).get("error_code") if reply.get("state") != "committed" else None
            log(step="u1", attempt=attempt, state=reply.get("state"), error=error,
                tools=[c["tool"] for c in reply.get("tool_calls") or []], answer=(reply.get("text") or "")[:300])
            if reply.get("state") == "committed" or error not in {"provider_server_error", "provider_unavailable"}:
                break
        verdict["model_created_two_children"] = len(session.children()) >= 2
        await wait_until(lambda: len(session.tasks()) >= 2 and all(t["status"] in TERMINAL for t in session.tasks()), 25 * 60, 10)
        log(step="ab_final", tasks=[(t.get("child_key"), t["status"]) for t in session.tasks()])
        verdict["ab_completed"] = len(session.tasks()) >= 2 and all(t["status"] == "COMPLETED" for t in session.tasks())
        # 结果回流 → 策略 hold
        await wait_until(lambda: len(session.continuations()) >= 2 and all(c["state"] == "hold" for c in session.continuations()), 3 * 60, 2)
        log(step="held", rows=[(c["event_id"][:12], c["state"], c.get("reason")) for c in session.continuations()])
        verdict["results_held_by_policy"] = len(session.continuations()) >= 2 and all(c["state"] == "hold" for c in session.continuations())
        # 宿主放行：显式重评，带控制动作
        POLICY.allowed = True
        receipt = session.reevaluate_continuation(command_id="release-1", policy_revision="policy-2", expected_goal_version=1)
        log(step="reevaluate", receipt=receipt)
        verdict["reevaluation_accepted"] = receipt["status"] == "accepted" and len(receipt["event_ids"]) >= 2
        # 续办回合应在同一 Goal 下自动委派 C（没有新的用户消息）
        await wait_until(lambda: len(session.tasks()) >= 3, 6 * 60, 3)
        cont = [q for q in session.queued_inputs() if q["kind"] == "result_event"]
        log(step="continuation_inputs", rows=[(q["input_id"][:12], q["state"], q.get("controls"), q.get("controls_withheld")) for q in cont],
            continuations=[(c["event_id"][:12], c["state"], c.get("controls"), [d["to"] for d in c.get("decisions", [])]) for c in session.continuations()],
            authorizations=[(a, t) for a, t, _, _ in PORT.requests if a == "continue_within_goal"])
        turn_ids = {q["turn_id"] for q in cont}
        third = [t for t in session.tasks() if t["turn_id"] in turn_ids]
        verdict["c_delegated_by_authorized_continuation"] = len(third) == 1 and third[0]["goal_ref"] == goal["goal_ref"]
        verdict["port_asked_for_continue_within_goal"] = any(a == "continue_within_goal" for a, *_ in PORT.requests)
        log(step="c_task", third=[(t.get("child_key"), t["status"], t["turn_id"][:12], t["goal_ref"]) for t in third],
            history=len(session.history(limit=50)))
        await wait_until(lambda: len(session.tasks()) >= 3 and all(t["status"] in TERMINAL for t in session.tasks()), 25 * 60, 10)
        log(step="c_final", tasks=[(t.get("child_key"), t["status"]) for t in session.tasks()])
        verdict["c_completed"] = len(third) == 1 and session.task(third[0]["delegation_id"])["status"] == "COMPLETED"
        await wait_until(lambda: all(c["state"] in {"applied", "waiting", "cancelled"} for c in session.continuations()), 6 * 60, 3)
        log(step="continuations_final", rows=[(c["event_id"][:12], c["state"], c.get("reason"), c.get("controls")) for c in session.continuations()],
            tasks=len(session.tasks()), history=len(session.history(limit=50)))
        verdict["no_extra_task_after_c"] = len(session.tasks()) == 3
        # 收口：先不登记 → 被拦；登记三条 → 成功
        try:
            session.finish_goal("onboarding-pack", "succeeded", command_id="finish-early", expected_version=1, basis="看过了")
            verdict["finish_blocked_without_records"] = False
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "code", "")
            log(step="finish_early", code=code, blockers=session.goal("onboarding-pack").get("acceptance_blockers"), waiting=session.goal("onboarding-pack").get("waiting_reasons"))
            verdict["finish_blocked_without_records"] = code in {"acceptance_incomplete", "active_work"}
        checks = validate(session, host)
        for cid, (verdict_value, evidence, note) in checks.items():
            rec = session.record_acceptance(
                "onboarding-pack",
                {"criterion_id": cid, "verdict": verdict_value, "validator_id": "ext-accept8-mechanical", "validator_version": "1",
                 "method": "mechanical:artifact_exists+length+keywords", "evidence": evidence, "note": note},
                command_id=f"accept-{cid}", expected_version=1,
            )
            log(step="record", cid=cid, verdict=verdict_value, note=note, record=rec["record_id"][:20])
        verdict["all_criteria_pass"] = all(v[0] == "pass" for v in checks.values())
        done = session.finish_goal("onboarding-pack", "succeeded", command_id="finish-1", expected_version=1, basis="三条条件由机械验收器核过")
        log(step="finish", done=done)
        verdict["finish_succeeded_with_records"] = done["state"] == "succeeded" and len(done.get("accepted_records", {}).get("record_ids", [])) == 3
        verdict["finish_replays"] = session.finish_goal("onboarding-pack", "succeeded", command_id="finish-1", expected_version=1, basis="三条条件由机械验收器核过") == done
        log(step="recovery_report", report=session.recovery_report())
        before = {"children": sorted(c["child_key"] for c in session.children()), "tasks": sorted((t.get("child_key"), t["status"]) for t in session.tasks()),
                  "goal": session.goal("onboarding-pack")["state"], "records": len(session.acceptance_records("onboarding-pack"))}
    finally:
        await session.close(); await host.close()
    host = await open_host()
    session = await open_session(host, "main", continuation_policy=POLICY)
    try:
        after = {"children": sorted(c["child_key"] for c in session.children()), "tasks": sorted((t.get("child_key"), t["status"]) for t in session.tasks()),
                 "goal": session.goal("onboarding-pack")["state"], "records": len(session.acceptance_records("onboarding-pack"))}
        log(step="reopen", before=before, after=after)
        verdict["reopen_preserves_goal_and_records"] = before == after
        log(step="verdict", **verdict)
    finally:
        await session.close(); await host.close()
    (OUT / "goal-chain-game.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=1, default=str))
    sys.exit(0 if all(verdict.values()) else 1)


asyncio.run(main())
