"""内容验收只认结果当时交上来的端口认领，重启后也一样（2026-10-03 迁到产品同形世界）。

内容那一步的审阅员调用被扣住（一次很慢的模型调用），在这个窗口里发生外界会发生的事：

* 进程重启：同一个库、同一套连接器重新起编排服务，任务照常走完，只发布一次；
* 磁盘上那条"结果已提交"事件里的端口认领被抹掉（字节损坏，分诊裁决①b1）：验收按名拒绝，绝不从
  当前声明的端口顺序里补一份认领，什么都不验收。

原"准备好的 MIXED 范围数据可读、顺序与终结仍关"并入 ``test_publish_variants.py`` 第一条。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_orchestrator.orchestrator.operation_completion import OperationCompletionError
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import ReviewHeld, publishing, quick_waits  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


#: 重启后，被打断的那次审阅调用要等"服务阻塞上限"（stall_seconds 与 30 秒取小）才判定；测试里缩短它。
LEASE = {"stall_seconds": 3.0}


def test_a_restart_during_the_content_review_still_completes_with_one_publish(tmp_path):
    state: dict[str, str] = {}

    async def first() -> None:
        provider = ReviewHeld("TASK_CONTENT")
        try:
            async with publishing(tmp_path, provider=provider, **LEASE) as case:
                state["mission_id"] = case.mission_id
                await case.run_until(provider.review_entered.is_set)
                [submitted] = case.events("ResultSubmitted")
                assert submitted.payload["completion_port_claims"]
        finally:
            provider.go.set()

    async def second() -> None:
        async with publishing(tmp_path, provider=LayeredScriptedProvider(), reopen=state["mission_id"],
                              **LEASE) as case:
            # 用"跑到条件成立"而不是"跑到空闲"：重启前那次被打断的审阅调用意图会一直停在 SUBMITTED
            # （见报告里的疑似缺陷），主循环因此不空闲。
            await case.run_until(lambda: bool(case.pending_approvals()), timeout=60)
            case.approve(case.pending_approvals()[0])
            await case.run_until(lambda: case.status() in {"COMPLETED", "FAILED", "CANCELLED"}, timeout=60)
            mission = case.mission()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert len(case.published_files()) == 1
            # 每份被验收的结果，它的端口认领都来自它自己那条"结果已提交"事件。
            claims = {event.payload["result_id"]: event.payload["completion_port_claims"]
                      for event in case.events("ResultSubmitted")}
            accepted = [t.accepted_result_id for t in case.store.list_tasks(case.mission_id) if t.accepted_result_id]
            assert accepted and all(claims.get(result_id) for result_id in accepted)

    asyncio.run(first())
    asyncio.run(second())


def test_cold_missing_result_claims_cannot_be_filled_from_current_ports(tmp_path):
    async def run() -> None:
        provider = ReviewHeld("TASK_CONTENT")
        try:
            async with publishing(tmp_path, provider=provider) as case:
                await case.run_until(provider.review_entered.is_set)
                with case.store.transaction():
                    case.store.connection.execute(
                        "UPDATE events SET payload_json=json_remove(payload_json,'$.completion_port_claims') "
                        "WHERE mission_id=? AND type='ResultSubmitted'", (case.mission_id,))
                provider.go.set()
                # 现状：这条按名拒绝会逃出本轮主循环（与迁移裁决 B4 同一类，待后续改为记录并原地处理）。
                with pytest.raises(OperationCompletionError, match="port claims"):
                    await case.run_until(lambda: False, timeout=30)
                assert HtnStore(case.store).list_acceptances(case.mission_id) == ()
                assert all(t.accepted_result_id is None for t in case.store.list_tasks(case.mission_id))
                assert case.actions() == [] and case.published_files() == []
        finally:
            provider.go.set()

    asyncio.run(run())
