"""保证通道收尾与唯一定稿写入接缝（产品同形世界，2026-10-03 迁离 ``_assured_fixture``）。

产品部署上主循环真跑一个用户任务：内容审阅、验收、终审正式通过 → 根结论由产品触发、凭当前终审的
使用证书写下 → 判定记下 → 收尾从"差根结论 / 差判定"走到 READY → 唯一定稿写入（任务完成、收尾行
定稿、完成事件、通知请求）→ 通知只发一次；定稿以后再评估只是回执。另一个任务在跑之前被用户取消：
取消的那次写入同时请求通知，通知只发一次，不会走到定稿写入。替身只有脚本化的模型回复。

旧版里"手把收尾行改成 READY 再调定稿写入"的合同测试不再需要：产品自己推出了 READY。
"""
from _product_seam import EVIDENCE, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.orchestrator.assurance_consumers import CLOSEOUT_EVENT, NOTIFICATION_EVENT, NOTIFIED_EVENT
from agent_orchestrator.orchestrator.assurance_final_writer import (CLOSEOUT_REQUESTED_EVENT, FINALIZED_KIND,
                                                                     finalize_assured_mission)
from agent_orchestrator.orchestrator.assurance_validity import ROOT_RESOLUTION_CONSUMER
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def events(store: Any, mission_id: str, kind: str) -> list[Any]:
    return [e for e in store.iter_events(mission_id) if e.type == kind]


def closeout_row(store: Any, mission_id: str) -> dict[str, Any] | None:
    row = store.connection.execute("SELECT resolution_id,state,row_version,last_receipt_id FROM assurance_closeouts "
                                   "WHERE mission_id=?", (mission_id,)).fetchone()
    return None if row is None else dict(row)


async def drain(tick: Any, limit: int = 20) -> int:
    rounds = 0
    while rounds < limit and await tick.tick():
        rounds += 1
    return rounds


async def main() -> None:
    quick()
    report: dict[str, Any] = {
        "status": "PASS",
        "scope": "product deployment, real main loop: root resolution licensed by the current MISSION_FINAL use "
                 "certificate -> judgment -> closeout NOT_READY -> READY -> unique final writer (COMPLETED, FINALIZED "
                 "row, MissionCompleted, notification request) -> NOTIFY once; a user cancel requests NOTIFY in the "
                 "same write and never reaches the final writer. Scripted model replies only; not a real model, "
                 "Host or UI."}
    with TemporaryDirectory(prefix="assurance-final-writer-") as temp:
        async with product_world(Path(temp).resolve() / "root", LayeredScriptedProvider()) as world:
            store, commit = world.store, world.loop.commit
            installed = world.deployment.assurance
            closeout = installed.consumers["CLOSEOUT"]
            assert closeout.finalizer is not None and closeout.finalizer.func is finalize_assured_mission

            # 1. 用户取消：取消那次写入就请求通知；通知只发一次；不走定稿写入。
            cancelled_id = world.create({"goal": "写 A.md", "success_criteria": ["file:A.md"],
                                         "idempotency_key": "cancel-1"})["mission_id"]
            world.control.cancel(cancelled_id)
            cancelled = store.get_mission(cancelled_id)
            assert str(cancelled.status.value) == "CANCELLED", cancelled.status
            [final] = events(store, cancelled_id, "MissionCancelled")
            requests = [e.payload for e in events(store, cancelled_id, NOTIFICATION_EVENT)]
            assert requests == [{"final_event_id": final.id, "state_version": cancelled.version,
                                 "final_event_type": "MissionCancelled"}], requests
            await world.drain(timeout=15)
            await drain(installed.tick)
            sent = [n for n in world.notices if n.get("mission_id") == cancelled_id]
            assert sent == [{"mission_id": cancelled_id, "event_id": final.id, "state_version": cancelled.version}], sent
            assert closeout_row(store, cancelled_id) is None and not events(store, cancelled_id, CLOSEOUT_REQUESTED_EVENT)
            report["cancel"] = {"notification_requests": requests, "sent": len(sent), "closeout_row": None}

            # 2. 一个任务跑完：根结论、判定、收尾、唯一定稿写入、通知。
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "final-1"})["mission_id"]
            completed = await world.run_until_settled(mission_id, timeout=60)
            assert str(completed.status.value) == "COMPLETED", completed.final_report
            assert completed.stop_reason == "verification_passed", completed.stop_reason
            [resolved] = [e.payload for e in events(store, mission_id, "GoalResolutionCommitted")
                          if e.payload.get("is_mission_root")]
            licences = [dict(r) for r in store.connection.execute(
                "SELECT certificate_id,consumer_id,purpose FROM assurance_use_certificates WHERE mission_id=? "
                "AND consumer_kind=?", (mission_id, ROOT_RESOLUTION_CONSUMER))]
            assert licences and resolved["witness_id"] == licences[0]["certificate_id"], (resolved, licences)
            assert licences[0]["purpose"] == "ACCEPT"
            assert not events(store, mission_id, "RootGoalResolutionRefused")
            closeouts = [e.payload for e in events(store, mission_id, CLOSEOUT_EVENT)]
            states = [c["state"] for c in closeouts]
            reasons = sorted({r for c in closeouts if c["state"] == "NOT_READY" for r in c["reasons"]})
            assert "READY" in states and states.index("READY") < states.index("FINALIZED"), states
            assert events(store, mission_id, "MissionSuccessJudged") and events(store, mission_id, CLOSEOUT_REQUESTED_EVENT)
            row = closeout_row(store, mission_id)
            assert row["state"] == "FINALIZED" and row["resolution_id"] == licences[0]["consumer_id"], row
            [done] = events(store, mission_id, "MissionCompleted")
            [finalized] = events(store, mission_id, FINALIZED_KIND)
            assert finalized.payload["final_event_id"] == done.id
            receipt = store.get_receipt("assurance-finalized:" + mission_id)
            assert receipt and receipt["state_version"] == completed.version, receipt
            assert row["last_receipt_id"] == "assurance-finalized:" + mission_id
            closeout_record = completed.final_report["assurance_closeout"]
            assert closeout_record["report_ref"]["pin"]["id"] == row["last_receipt_id"]
            assert closeout_record["requirements_ref"]["revision"] == 1 and closeout_record["state"] == "FINALIZED"
            assert completed.final_report["assurance_judgment"]["met"] is True
            requests = [e.payload for e in events(store, mission_id, NOTIFICATION_EVENT)]
            assert requests == [{"final_event_id": done.id, "state_version": completed.version,
                                 "final_event_type": "MissionCompleted"}], requests
            rounds = await drain(installed.tick)
            sent = [n for n in world.notices if n.get("mission_id") == mission_id]
            assert sent == [{"mission_id": mission_id, "event_id": done.id, "state_version": completed.version}], sent
            assert len(events(store, mission_id, NOTIFIED_EVENT)) == 1
            assert not commit.assured_closeout_pending(mission_id)
            # 定稿以后：再交一次 READY 评估（比如重启后重放）按名拒绝，什么都不写。
            before = store.connection.total_changes
            stale = {"mission_id": mission_id, "state": "READY", "mission_version": completed.version, "reasons": [],
                     "root_resolution_ref": {"id": row["resolution_id"], "revision": 0, "content_hash": "0" * 64}}
            with store.transaction():
                try:
                    finalize_assured_mission(commit, mission_id, stale)
                except AssuranceError as error:
                    assert error.code == "CLOSEOUT_NOT_READY", error.code
                else:
                    raise AssertionError("final writer ran twice")
            assert store.connection.total_changes == before
            assert len(events(store, mission_id, "MissionCompleted")) == 1 and closeout_row(store, mission_id) == row
            report["root_resolution"] = {"witness_is_mission_final_use": True, "licence": licences[0]}
            report["closeout"] = {"states": states, "not_ready_reasons": reasons, "row": row}
            report["final_writer"] = {"mission_status": "COMPLETED", "stop_reason": completed.stop_reason,
                                      "receipt": receipt, "notification_requests": requests, "sent": len(sent),
                                      "post_final_rounds": rounds, "refusal_after_finalized": "CLOSEOUT_NOT_READY"}
    report["source_sha256"] = source_sha256([
        "orchestrator/assurance_final_writer.py", "orchestrator/assurance_consumers.py",
        "orchestrator/assurance_assembly.py", "orchestrator/assurance_validity.py", "orchestrator/resolution_commits.py",
        "orchestrator/hierarchical_dispatch.py", "orchestrator/commit_service.py"])
    write_report("final-writer-seam", report)


if __name__ == "__main__":
    asyncio.run(main())
