# SPDX-License-Identifier: Apache-2.0
"""A01/C01 entry assertions; not the entire scenario acceptance matrix.

建任务时就绑定执行图（用户 2026-10-03 定）：没有"签授权后再启用"这一步，建任务的同一事务
里已有绑定与启用回执；第一份计划照旧经原派发与收集提交成执行图第 1 版。"""
import asyncio

from production_fixture import enabled_world
from agent_orchestrator.orchestrator.taskgraph_policy import enable_command_id
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore


def test_the_creation_binds_the_graph_and_the_seed_plan_is_its_first_revision(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-production-seed', hold_worker=True) as world:
            store, mission = world.store, world.mission.id
            # Bound when the Mission was created: before any loop round, no plan yet.
            assert store.connection.execute(
                'SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?', (mission,)).fetchone()[0] == 1
            receipt = store.get_receipt(enable_command_id(mission))
            assert receipt is not None and receipt['mission_id'] == mission
            types = [event.type for event in store.list_events(mission)]
            assert types.index('MissionCreated') < types.index('TaskGraphContractEnabled')
            # The binding is a trusted deployment action on the principal's behalf, never a click.
            [enabled] = [e for e in store.list_events(mission) if e.type == 'TaskGraphContractEnabled']
            assert enabled.actor_type == 'system' and enabled.actor_id == world.principal.principal_id
            assert enabled.payload['enabled_by'] == 'HOST_DELEGATED'
            assert 'MissionPlanning' not in types
            assert store.connection.execute(
                'SELECT COUNT(*) FROM plan_revisions WHERE mission_id=?', (mission,)).fetchone()[0] == 0

            await world.commit_seed()
            historical = TaskGraphStore(store).read_revision(mission, 1)
            assert historical.record.source_kind == 'SEED_COMMIT'
            before = (store.last_event_seq(mission), store.connection.execute(
                'SELECT COUNT(*) FROM attempts WHERE mission_id=?', (mission,)).fetchone()[0])
            snapshot = world.graph.reads.snapshot(mission)
            repeated = world.graph.reads.snapshot(mission)
            assert snapshot == repeated
            assert snapshot['read_token']['plan_revision'] == 1
            # Reading writes no event and creates no Attempt.
            assert (store.last_event_seq(mission), store.connection.execute(
                'SELECT COUNT(*) FROM attempts WHERE mission_id=?', (mission,)).fetchone()[0]) == before
            assert store.connection.execute(
                'SELECT COUNT(*) FROM taskgraph_revision_records WHERE mission_id=?', (mission,)).fetchone()[0] == 1
    asyncio.run(case())
