"""执行图在建任务时直接绑定（用户 2026-10-03 定，HTN 补齐阶段 A′）。

建任务的同一事务里写下执行图绑定：没有"已要求、还没绑定"的等待期，也不需要先有规划授权
（规划授权照样卡住每一次计划提交）。重复建同一个任务不会再绑一次。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ._layered_lane import layered_service, notes_mission, quick_runtime


@pytest.fixture(autouse=True)
def _quick_runtime(monkeypatch):
    quick_runtime(monkeypatch)


@pytest.mark.asyncio
async def test_a_new_mission_is_bound_in_its_creation_transaction(orchestration_root, principal):
    """**Mutation**: drop the binding from ``UserMissionDeployment._complete`` → red."""
    service = layered_service(orchestration_root, principal)
    await asyncio.wait_for(service.start(), 30)
    try:
        store = service._orchestrator.store
        created = service.create_mission(notes_mission("bound-at-creation"))
        mission_id = created["mission_id"]
        # no loop round has run and no planning grant exists yet
        assert store.connection.execute(
            "SELECT COUNT(*) FROM planning_lane_grants WHERE mission_id=?", (mission_id,)).fetchone()[0] == 0
        [(kernel, policy_json)] = store.connection.execute(
            "SELECT kernel_version, policy_json FROM taskgraph_policy_bindings WHERE mission_id=?",
            (mission_id,)).fetchall()
        assert kernel == "taskgraph-exec-v2"
        assert "planning_delegation_ref" not in json.loads(policy_json)
        replay = service.create_mission(notes_mission("bound-at-creation"))
        assert replay["created"] is False and replay["mission_id"] == mission_id
        assert store.connection.execute(
            "SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?", (mission_id,)).fetchone()[0] == 1
    finally:
        await service.close()
