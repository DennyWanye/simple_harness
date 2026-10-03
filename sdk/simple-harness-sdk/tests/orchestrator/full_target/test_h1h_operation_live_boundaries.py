# ruff: noqa: E501
"""Live H1-H operation boundaries backed by the formal T0/T1 producer.

This file deliberately does not manufacture authoritative negative evidence.  The
supported O07 branches below cover a real matching success receipt and a mismatching
receipt only.

HTN 补齐阶段 A′：O07 改为代表用例 3 的变体（``step07/helpers_step07.operation_world``）：产品部署上
建任务、人确认完成映射、执行者写文件、系统物化发布动作（真实 T0 链接）、人批准、真实的
``FilePublishConnector`` 发布、读回核对、任务完成；成功回执被操作快照判为"已生效"。随后在磁盘上
改坏动作行里回执的字节（裁决①b1），读侧按 ``success_without_matching_receipt`` 拒绝，既不判"已生效"
也不猜成"未生效"。

偏离：删 ``test_o09_action_and_exact_link_rollback_together_then_same_command_replays_once``——它在
旧叶子世界里 monkeypatch 存储写入函数造故障；同一性质（真实 T0 物化时链接写入失败，动作 / 链接 /
事件一起回滚，同一命令重放只成一份）由门禁节点
``test_h1h_operation_current_gates.py::test_o09_real_t0_materialization_rolls_back_link_fault_and_replays_once``
覆盖（``h1h_stage_runner.py`` 的 O09 映射，属对外操作族）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_STEP07 = _HERE.parent / "step07"
if str(_STEP07) not in sys.path:
    sys.path.insert(0, str(_STEP07))

from helpers_step07 import operation_world, run_until, until_pending  # noqa: E402

from agent_orchestrator.runtime.planning_operations import (  # noqa: E402
    OperationEffect,
    SourceUnavailable,
    StoreOperationReader,
    build_operation_snapshot,
)
from agent_orchestrator.storage.planning_admission_store import (  # noqa: E402
    PlanningAdmissionStore,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_o07_real_t0_t1_success_receipt_is_applied_and_wrong_receipt_is_refused(tmp_path) -> None:
    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-o07") as world:
            action = await until_pending(world)
            key = action["action_key"]
            link = PlanningAdmissionStore(world.store).get_operation_action_link_for_action(key)
            assert link is not None  # the system's own T0 link
            request = [a for a in world.control.approvals(world.mission_id) if a.get("state") == "PENDING"][0]
            world.control.decide(request["request_id"], "approve")
            await run_until(world.product, lambda: str(world.mission.status.value) in {"COMPLETED", "FAILED"})
            assert str(world.mission.status.value) == "COMPLETED", world.mission.final_report
            executed = world.store.get_action(key)
            assert executed["state"] == "SUCCEEDED" and executed["handoffs"] == 1
            assert executed["receipt"]["params_hash"] == executed["params_hash"]

            snapshot = build_operation_snapshot(world.mission_id, reader=StoreOperationReader(world.store))
            assert snapshot.effects == ((link["operation_id"], OperationEffect.APPLIED),)

            # The receipt identity is altered on disk while the state stays successful.  A
            # successful status without an exact connector/operation/target/params/idempotency
            # receipt is unavailable evidence, never APPLIED and never a guessed negative proof.
            with world.store.transaction() as connection:
                connection.execute(
                    "UPDATE actions SET json = json_set(json, '$.receipt.params_hash', ?) WHERE action_key = ?",
                    ("0" * 64, key))
            with pytest.raises(SourceUnavailable, match="success_without_matching_receipt"):
                build_operation_snapshot(world.mission_id, reader=StoreOperationReader(world.store))

    asyncio.run(case())
