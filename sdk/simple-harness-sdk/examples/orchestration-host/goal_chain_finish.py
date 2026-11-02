"""续收口：接上 goal_chain_game.py 跑到 C 完成的那个数据根（脚本在收口前没等 C 的结果被消费）。
重开宿主 → 等所有结果被续办消费（只读汇总）→ 宿主验收器读真实产物登记三条结论（接替先前的 fail 记录）
→ finish 成功、重复 finish 原回执 → 恢复报告 → 关掉重开核对 Goal 与记录。用法：python goal_chain_finish.py <输出目录>"""
import asyncio, json, os, sys, time
from pathlib import Path

from agent_orchestrator.host import (
    AuthorizationDecision, ContinuationDecision, HostSettings, ModelConfig, OrchestrationHost, Principal,
)
from agent_orchestrator.host.sessions import open_session

OUT = Path(sys.argv[1]); ROOT = OUT / "host-data"
LOG, T0 = [], time.time()


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


class ReadOnlyPolicy:
    """收口阶段：剩下的结果只读汇总，不再放控制动作。"""

    async def decide(self, context):
        return ContinuationDecision.enqueue()


class HostAuthorization:
    async def decide(self, request):
        return AuthorizationDecision.allow(revision="policy-1")


async def open_host():
    return await OrchestrationHost.open(
        ROOT, principal=Principal("accept8", "验收人"), model=model(), settings=HostSettings(),
        auto_mode=True, authorization_port=HostAuthorization(),
    )


async def wait_until(fn, seconds, every):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        await asyncio.sleep(every)
    return fn()


def validate(session, host):
    found = {}
    for task in session.tasks():
        mid = task.get("mission_id")
        if not mid:
            continue
        for art in host.snapshot(mid)["snapshot"].get("artifacts", []):
            name = str(art.get("logical_path") or art.get("path") or "").rsplit("/", 1)[-1]
            if name in {"guide.md", "meeting.md", "summary.md"} and name not in found:
                text = host.artifact_read(art["id"]).get("content") or ""
                found[name] = {"mission_id": mid, "artifact": art, "text": text}
    results = {}
    for cid, name in (("guide", "guide.md"), ("meeting", "meeting.md"), ("summary", "summary.md")):
        hit = found.get(name)
        if hit is None:
            results[cid] = ("fail", [], f"{name} 未交付")
            continue
        text = hit["text"]
        ok = 0 < len(text.strip()) <= 1200
        if cid == "summary":
            ok = ok and ("指南" in text or "guide" in text.lower()) and ("例会" in text or "meeting" in text.lower())
        evidence = [{"ref": f"artifact:{hit['mission_id']}:{hit['artifact']['id']}", "content_hash": hit["artifact"]["content_hash"]}]
        results[cid] = ("pass" if ok else "fail", evidence, f"{name} {len(text)} 字")
    return results


async def main():
    verdict = {}
    host = await open_host()
    session = await open_session(host, "main", continuation_policy=ReadOnlyPolicy())
    try:
        log(step="reopen_for_finish", tasks=[(t.get("child_key"), t["status"]) for t in session.tasks()],
            inbox=[(n["event_id"][:12], n["applied_turn_id"] is not None) for n in session.inbox()],
            report=session.recovery_report())
        ok = await wait_until(lambda: all(n["applied_turn_id"] is not None for n in session.inbox()), 60, 5)
        if not ok:
            # 模型把 C 委派在主会话（没指定工作会话）：主会话自己委派的结果按设计由用户下一句消费，不走自动汇总
            for attempt in range(1, 4):
                if attempt > 1:
                    await asyncio.sleep(60)
                reply = await session.send("三份都做完了吗？用一句话说 summary.md 的内容。", key=f"u2-{attempt}")
                log(step="u2", attempt=attempt, state=reply.get("state"), answer=(reply.get("text") or "")[:300],
                    error=(reply.get("error") or {}).get("error_code"))
                if reply.get("state") == "committed":
                    break
            ok = all(n["applied_turn_id"] is not None for n in session.inbox())
        log(step="all_results_applied", ok=ok, continuations=[(c["event_id"][:12], c["state"], c.get("controls")) for c in session.continuations()],
            inbox=[(n["event_id"][:12], n["applied_turn_id"] is not None) for n in session.inbox()],
            tasks=len(session.tasks()), history=len(session.history(limit=50)))
        verdict["all_results_consumed"] = ok
        verdict["no_extra_task_after_c"] = len(session.tasks()) == 3
        goal = session.goal("onboarding-pack")
        log(step="goal_before", state=goal["state"], blockers=goal["acceptance_blockers"],
            records=[(r["criterion_id"], r["verdict"], r["current"]) for r in session.acceptance_records("onboarding-pack")])
        # 结构事实收口后、当前记录若还是 fail：finish 该被验收记录拦住（记录已是 pass 时跳过这一步）
        current = [r for r in session.acceptance_records("onboarding-pack") if r["current"]]
        if goal["state"] == "active" and any(r["verdict"] != "pass" for r in current):
            try:
                session.finish_goal("onboarding-pack", "succeeded", command_id="finish-with-fails", expected_version=1, basis="看过了")
                verdict["finish_blocked_by_fail_records"] = False
            except Exception as error:  # noqa: BLE001
                code = getattr(error, "code", "")
                log(step="finish_with_fails", code=code, blockers=session.goal("onboarding-pack")["acceptance_blockers"])
                verdict["finish_blocked_by_fail_records"] = code == "acceptance_incomplete"
        checks = validate(session, host)
        already_done = session.goal("onboarding-pack")["state"] in {"succeeded", "failed", "cancelled"}
        for cid, (v, evidence, note) in ([] if already_done else checks.items()):
            rec = session.record_acceptance(
                "onboarding-pack",
                {"criterion_id": cid, "verdict": v, "validator_id": "ext-accept8-mechanical", "validator_version": "2",
                 "method": "mechanical:artifact_exists+length+keywords", "evidence": evidence, "note": note},
                command_id=f"accept2-{cid}", expected_version=1,
            )
            log(step="record", cid=cid, verdict=v, note=note, record=rec["record_id"][:22], evidence=[e["ref"][:60] for e in evidence])
        rows = session.acceptance_records("onboarding-pack")
        log(step="records", rows=[(r["criterion_id"], r["verdict"], r["current"], (r.get("superseded_by") or "")[:14]) for r in rows])
        verdict["all_criteria_pass"] = all(v[0] == "pass" for v in checks.values())
        verdict["old_fail_records_superseded"] = all(
            (r["current"] is False and r["superseded_by"]) for r in rows if r["verdict"] == "fail")
        done = session.finish_goal("onboarding-pack", "succeeded", command_id="finish-2", expected_version=1, basis="三条条件由机械验收器核过")
        log(step="finish", done=done)
        verdict["finish_succeeded_with_records"] = done["state"] == "succeeded" and len(done.get("accepted_records", {}).get("record_ids", [])) == 3
        verdict["finish_replays"] = session.finish_goal("onboarding-pack", "succeeded", command_id="finish-2", expected_version=1, basis="三条条件由机械验收器核过") == done
        log(step="recovery_report", report=session.recovery_report())
        before = {"tasks": sorted((t.get("child_key") or "", t["status"]) for t in session.tasks()),
                  "goal": session.goal("onboarding-pack")["state"], "records": len(rows),
                  "accepted": sorted(done["accepted_records"]["record_ids"])}
    finally:
        await session.close(); await host.close()
    host = await open_host()
    session = await open_session(host, "main", continuation_policy=ReadOnlyPolicy())
    try:
        goal = session.goal("onboarding-pack")
        after = {"tasks": sorted((t.get("child_key") or "", t["status"]) for t in session.tasks()), "goal": goal["state"],
                 "records": len(session.acceptance_records("onboarding-pack")), "accepted": sorted(goal["accepted_records"]["record_ids"])}
        log(step="reopen", before=before, after=after)
        verdict["reopen_preserves_goal_and_records"] = before == after
        log(step="verdict", **verdict)
    finally:
        await session.close(); await host.close()
    (OUT / "goal-chain-finish.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=1, default=str))
    sys.exit(0 if all(verdict.values()) else 1)


asyncio.run(main())
