"""仓库外正式一局（只装发版轮子，只用公开接口）：
主会话 → 模型自己开两个工作会话并各委派一个任务 → 任务跑完结果回流 → 自动汇总 → 用户追问能看到结论
→ 关掉重开，关系/任务/收件箱都还在，恢复报告只读事实。
密钥只从环境变量读（run.py 注入）。用法：python main_tree_game.py <输出目录>"""
import asyncio, json, os, sys, time
from pathlib import Path

from agent_orchestrator.host import HostSettings, ModelConfig, OrchestrationHost, Principal

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
ROOT = OUT / "host-data"
LOG = []
T0 = time.time()


def log(**row):
    row["t"] = round(time.time() - T0, 1)
    LOG.append(row)
    print(json.dumps(row, ensure_ascii=False)[:700], flush=True)


def model():
    base = os.environ["DEEPSEEKER_BASEURL"]
    return ModelConfig(
        base_url=base, model=os.environ["DEEPSEEKER_MODEL"], api_key=os.environ["DEEPSEEKER_APIKEY"],
        compatible_hosts=(base.split("//")[1].split("/")[0].lower(),),
        response_model_aliases=("deepseek-ai/DeepSeek-V4.1-Flash", "deepseek-flash", "deepseek-v4-flash"),
    )


async def open_host():
    return await OrchestrationHost.open(
        ROOT, principal=Principal("accept7", "验收人"), model=model(), settings=HostSettings(), auto_mode=True,
    )


async def wait_until(fn, seconds, every):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        await asyncio.sleep(every)
    return fn()


async def main():
    host = await open_host()
    session = await host.open_session("main")
    verdict = {}
    try:
        caps = session.capabilities()
        log(step="capabilities", caps=caps.get("capabilities"), unsupported=caps.get("unsupported"))
        reply = await session.send(
            "请帮我并行做两件独立的小工作，各开一个工作会话交给后台：A. 写一份不超过 200 字的《给新同事的第一天指南》，存成 guide.md；"
            "B. 写一份不超过 200 字的《团队每周例会流程》，存成 meeting.md。两件都交出去就告诉我一声。", key="u1")
        log(step="u1", state=reply.get("state"), tools=[c["tool"] for c in reply.get("tool_calls") or []],
            answer=(reply.get("text") or "")[:300])
        log(step="children", children=[c["child_key"] for c in session.children()],
            tasks=[(t.get("child_key"), t["status"]) for t in session.tasks()])
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}
        await wait_until(lambda: len(session.tasks()) >= 2 and all(t["status"] in terminal for t in session.tasks()), 20 * 60, 10)
        log(step="tasks_final", tasks=[(t.get("child_key"), t["status"]) for t in session.tasks()])
        await wait_until(lambda: session.continuations() and all(
            c["state"] in {"applied", "hold", "deny", "waiting"} for c in session.continuations()), 4 * 60, 3)
        log(step="continuations", rows=[(c["event_id"][:14], c["state"], c.get("reason")) for c in session.continuations()],
            queued=[(q["kind"], q["state"]) for q in session.queued_inputs()],
            inbox=[(n["source"], n["status"], n["applied_turn_id"] is not None) for n in session.inbox()])
        follow = await session.send("刚才两份文档的结论分别是什么？一句话各说一下。", key="u2")
        log(step="u2", state=follow.get("state"), tools=[c["tool"] for c in follow.get("tool_calls") or []],
            answer=(follow.get("text") or "")[:400])
        report = session.recovery_report()
        log(step="recovery_report", report=report)
        before = {
            "children": sorted(c["child_key"] for c in session.children()),
            "tasks": sorted((t.get("child_key"), t["status"]) for t in session.tasks()),
            "inbox": len(session.inbox()),
            "history": len(session.history(limit=50)),
        }
        verdict.update(
            model_created_two_children=len(session.children()) >= 2,
            both_tasks_completed=len(session.tasks()) >= 2 and all(t["status"] == "COMPLETED" for t in session.tasks()),
            summary_applied=any(c["state"] == "applied" for c in session.continuations()),
            followup_committed=follow.get("state") == "committed",
            followup_has_conclusions=follow.get("state") == "committed" and len(follow.get("text") or "") > 20,
        )
    finally:
        await session.close(); await host.close()
    host = await open_host()
    session = await host.open_session("main")
    try:
        after = {
            "children": sorted(c["child_key"] for c in session.children()),
            "tasks": sorted((t.get("child_key"), t["status"]) for t in session.tasks()),
            "inbox": len(session.inbox()),
            "history": len(session.history(limit=50)),
        }
        log(step="reopen", before=before, after=after, owner_generation=host.owner_generation)
        verdict["reopen_preserves_tree"] = before == after
        same = await session.send("刚才两份文档的结论分别是什么？一句话各说一下。", key="u2")
        verdict["same_key_replays_without_model"] = same.get("text") == follow.get("text")
        log(step="verdict", **verdict)
    finally:
        await session.close(); await host.close()
    (OUT / "main-tree-game.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=1))
    sys.exit(0 if all(verdict.values()) else 1)


asyncio.run(main())
