# SPDX-License-Identifier: Apache-2.0
"""推后第 2 批 T09：执行图策略行只经存储层读写（原计划 §6.2 第 307 行、§6.3 第 318～326 行）。

四个接口 ``policy / insert_policy / list_member_pins / list_method_pins`` 在 ``TaskGraphStore``；
策略表只有存储层写；读策略只有 ``policy()`` 一处核对启用回执。"""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from production_fixture import enabled_world
from agent_orchestrator.orchestrator.taskgraph_policy import enable_command_id
from agent_orchestrator.storage.store import StoreConflict, StoreError
from agent_orchestrator.storage.taskgraph_store import PolicyBinding, TaskGraphStore

_SRC = Path(__file__).resolve().parents[4] / "src" / "agent_orchestrator"


def test_the_four_interfaces_read_the_bound_policy_and_the_pins(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key="t09-read", hold_worker=True) as world:
            store, mission = world.store, world.mission.id
            graph = TaskGraphStore(store)
            assert graph.policy("no-such-mission") is None, "没有绑定 = Missing，不是异常"
            binding = graph.policy(mission)
            assert isinstance(binding, PolicyBinding)
            receipt = store.get_receipt(enable_command_id(mission))
            assert binding.mission_id == mission
            assert binding.enabling_command_id == enable_command_id(mission)
            assert dict(binding.receipt) == dict(receipt)
            assert binding.policy.content_hash == receipt["policy_hash"]

            await world.commit_seed()
            record = graph.read_revision(mission, 1).record
            members = graph.list_member_pins(mission, 1)
            methods = graph.list_method_pins(mission, 1)
            assert members and members == tuple(sorted(record.pins.member_pins, key=lambda p: p.occurrence_id))
            assert methods == tuple(sorted(record.pins.method_pins, key=lambda p: p.instance_id))
            assert graph.list_member_pins(mission, 99) == ()
    asyncio.run(case())


def test_insert_policy_joins_the_outer_transaction_and_never_replaces_a_different_binding(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key="t09-insert", hold_worker=True) as world:
            store, mission = world.store, world.mission.id
            graph = TaskGraphStore(store)
            binding = graph.policy(mission)
            with pytest.raises(StoreError, match="TASKGRAPH_POLICY_TRANSACTION_REQUIRED"):
                graph.insert_policy(binding, binding.receipt)
            with store.transaction():
                assert graph.insert_policy(binding, binding.receipt) == binding, "同键同内容：返回原行"
            changed = dataclasses.replace(binding, created_at=binding.created_at + 1.0)
            with pytest.raises(StoreConflict, match="TASKGRAPH_POLICY_ALREADY_BOUND"):
                with store.transaction():
                    graph.insert_policy(changed, binding.receipt)
            other_receipt = {**binding.receipt, "intent_hash": "0" * 64}
            with pytest.raises(StoreError, match="TASKGRAPH_POLICY_RECEIPT_MISMATCH"):
                with store.transaction():
                    graph.insert_policy(binding, other_receipt)
            assert graph.policy(mission) == binding
    asyncio.run(case())


def test_a_tampered_enabling_receipt_is_refused_by_name(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key="t09-tamper", hold_worker=True) as world:
            store, mission = world.store, world.mission.id
            row = store.connection.execute(
                "SELECT receipt_json FROM commit_receipts WHERE commit_id=?", (enable_command_id(mission),)).fetchone()
            tampered = row[0].replace('"schema_version":1', '"schema_version":2')
            assert tampered != row[0]
            # 模拟库被改坏：先拆掉回执表的只读触发器（测试专用）
            with store.transaction() as db:
                db.execute("DROP TRIGGER commit_receipts_immutable_update")
                db.execute("UPDATE commit_receipts SET receipt_json=? WHERE commit_id=?",
                           (tampered, enable_command_id(mission)))
            with pytest.raises(StoreError, match="TASKGRAPH_POLICY_RECEIPT_CORRUPT"):
                TaskGraphStore(store).policy(mission)
    asyncio.run(case())


def test_no_module_outside_the_store_writes_or_reads_one_policy_row_by_sql():
    """单一路径：策略行的写、单个任务的策略读、按版本读钉，只在存储层。

    剩下三处读（列出全部已绑定任务、数已绑定任务、查步骤是否在任何版本当过成员）原计划接口
    清单里没有对应接口，本条不扩，记录第五节留给主会话。"""
    allowed_reads = {
        "orchestrator/taskgraph_notifications.py": "SELECT mission_id FROM taskgraph_policy_bindings ORDER BY",
        "orchestrator/recovery_coordinator.py": "FROM taskgraph_policy_bindings b JOIN missions",
        "orchestrator/commit_service.py": "SELECT 1 FROM taskgraph_member_pins WHERE mission_id=? AND task_id=?",
    }
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        if relative.startswith("storage/"):
            continue
        text = path.read_text(encoding="utf-8")
        for table in ("taskgraph_policy_bindings", "taskgraph_member_pins", "taskgraph_method_pins"):
            for line in text.splitlines():
                if table in line and not line.lstrip().startswith("#"):
                    allowed = allowed_reads.get(relative)
                    if allowed is None or allowed not in line:
                        offenders.append(f"{relative}: {line.strip()}")
    assert offenders == []
