# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐阶段 A：删"老任务迁入执行图"（CAPTURED_BASELINE）。

执行图在第一份计划之前绑定（建任务时）；已有计划的任务不再迁入，启用命令直接拒绝。库层两个
守卫触发器（迁移 34）不再接受迁入来源，每条执行图历史都必须指向一次真实的 APPLIED 计划准入。
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from production_fixture import enabled_world
from agent_orchestrator.storage.store import StoreError


def _count(store, table, mission_id):
    return store.connection.execute(f"SELECT COUNT(*) FROM {table} WHERE mission_id=?", (mission_id,)).fetchone()[0]


def test_enabling_a_mission_that_already_has_a_plan_is_refused(tmp_path):
    """**Mutation**: drop the ``TASKGRAPH_MISSION_ALREADY_PLANNED`` check → red (the
    command falls through to another refusal, or a second baseline would be captured)."""

    async def case():
        async with enabled_world(tmp_path, key="tg-already-planned", hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.store, world.mission.id
            # A new enable command (not the creation's own, whose receipt it would read back).
            with pytest.raises(StoreError, match="TASKGRAPH_MISSION_ALREADY_PLANNED"):
                world.graph.policy.enable_taskgraph_contract(mission, "enable-again:" + mission)
            assert store.get_receipt("enable-again:" + mission) is None
            assert _count(store, "taskgraph_policy_bindings", mission) == 1
            assert _count(store, "taskgraph_revision_records", mission) == 1

    asyncio.run(case())


def test_the_store_refuses_a_captured_baseline_history_row(tmp_path):
    """**Mutation**: drop the source-kind clause from migration 34's
    ``tg_revision_source_guard`` → red (the old CHECK still admits the value)."""

    async def case():
        async with enabled_world(tmp_path, key="tg-no-baseline", hold_worker=True) as world:
            await world.commit_seed()
            row = world.store.connection.execute(
                "SELECT * FROM taskgraph_revision_records WHERE mission_id=? AND revision=1",
                (world.mission.id,)).fetchone()
            assert row["source_kind"] == "SEED_COMMIT"
            values = dict(row)
            values.update(revision=2, source_kind="CAPTURED_BASELINE", admission_check_id=None,
                          command_id="baseline:forged")
            columns = ",".join(values)
            with pytest.raises(sqlite3.IntegrityError, match="TG_REVISION_SOURCE_KIND_REMOVED"):
                world.store.connection.execute(
                    f"INSERT INTO taskgraph_revision_records ({columns}) VALUES ({','.join('?' * len(values))})",
                    tuple(values.values()))

    asyncio.run(case())
