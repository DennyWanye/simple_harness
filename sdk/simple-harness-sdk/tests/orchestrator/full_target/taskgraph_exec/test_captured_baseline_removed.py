# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐阶段 A：删"老任务迁入执行图"（CAPTURED_BASELINE）。

执行图在第一份计划之前绑定；已有计划的任务不再迁入，启用命令直接拒绝。库层两个守卫触发器
（迁移 34）不再接受迁入来源，每条执行图历史都必须指向一次真实的 APPLIED 计划准入。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_taskgraph_required import _drive, _world  # noqa: E402
from test_h1i_production_entry import _config, _open_planner_round, _refine_reply  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.taskgraph_requirement import enable_command_id  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.store import StoreError  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of  # noqa: E402


async def _plan_once(loop, mission, dispatch, principal, key, *, enable=None):
    intent = await _open_planner_round(loop, mission, dispatch, ordinal=1)
    PlanningAuthorizationApi(loop.commit, tenant_id=mission.tenant_id, principal=principal).issue(
        mission.id, command_id=f"grant:{key}", request_id=intent.intent_id)
    if enable is not None:
        enable()
    async with asyncio.timeout(20):
        while (current := await _drive(loop, intent)).state != "SETTLED":
            assert current.state not in {"FAILED", "CANCELLED"}, current.state


def test_enabling_a_mission_that_already_has_a_plan_is_refused(tmp_path):
    """**Mutation**: drop the ``TASKGRAPH_MISSION_ALREADY_PLANNED`` check → red (a
    binding is written for a Mission whose history has no seed revision record)."""

    async def case():
        provider = RoleScriptedProvider({"planner": [lambda request: _refine_reply(package_of(request))]})
        async with Orchestrator(_config(tmp_path), provider) as loop:
            mission, dispatch, principal, graph = _world(loop, tmp_path, "tg-already-planned", required=False)
            await _plan_once(loop, mission, dispatch, principal, "tg-already-planned")
            assert HtnStore(loop.store).active_plan_revision(mission.id) is not None
            with pytest.raises(StoreError, match="TASKGRAPH_MISSION_ALREADY_PLANNED"):
                graph.policy.enable_taskgraph_contract(mission.id, enable_command_id(mission.id))
            assert loop.store.connection.execute(
                "SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?", (mission.id,)).fetchone()[0] == 0
            assert loop.store.connection.execute(
                "SELECT COUNT(*) FROM taskgraph_revision_records WHERE mission_id=?", (mission.id,)).fetchone()[0] == 0

    asyncio.run(case())


def test_the_store_refuses_a_captured_baseline_history_row(tmp_path):
    """**Mutation**: drop the source-kind clause from migration 34's
    ``tg_revision_source_guard`` → red (the old CHECK still admits the value)."""

    async def case():
        provider = RoleScriptedProvider({"planner": [lambda request: _refine_reply(package_of(request))]})
        async with Orchestrator(_config(tmp_path), provider) as loop:
            mission, dispatch, principal, graph = _world(loop, tmp_path, "tg-no-baseline")
            await _plan_once(loop, mission, dispatch, principal, "tg-no-baseline",
                             enable=lambda: graph.policy.enable_taskgraph_contract(
                                 mission.id, enable_command_id(mission.id)))
            row = loop.store.connection.execute(
                "SELECT * FROM taskgraph_revision_records WHERE mission_id=? AND revision=1",
                (mission.id,)).fetchone()
            assert row["source_kind"] == "SEED_COMMIT"
            values = dict(row)
            values.update(revision=2, source_kind="CAPTURED_BASELINE", admission_check_id=None,
                          command_id="baseline:forged")
            columns = ",".join(values)
            with pytest.raises(sqlite3.IntegrityError, match="TG_REVISION_SOURCE_KIND_REMOVED"):
                loop.store.connection.execute(
                    f"INSERT INTO taskgraph_revision_records ({columns}) VALUES ({','.join('?' * len(values))})",
                    tuple(values.values()))

    asyncio.run(case())
