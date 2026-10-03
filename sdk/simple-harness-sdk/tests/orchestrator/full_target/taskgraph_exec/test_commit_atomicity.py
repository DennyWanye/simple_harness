# SPDX-License-Identifier: Apache-2.0
"""Abort actual SQLite writes after the original planning authority checks."""
import asyncio

import pytest

from production_fixture import enabled_world
from agent_orchestrator.storage.store import StoreConflict
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.artifacts.store import read_nofollow

PLAN_TABLES = ('plan_revisions', 'taskgraph_revision_records', 'taskgraph_member_pins',
               'taskgraph_method_pins', 'taskgraph_followups', 'attempts')


def _adopting_reply(world):
    """The Planner request whose reply (adopting the reviewed method) is in, not yet collected."""
    for intent in world.store.list_intents('SUBMITTED'):
        if intent.mission_id == world.mission.id and intent.kind == 'plan' and ':planner:' in intent.subject_id \
                and intent.subject_id != f'{world.mission.id}:planner:1':
            return intent
    return None


def _rows(store, mission, table):
    return store.connection.execute(f'SELECT COUNT(*) FROM {table} WHERE mission_id=?', (mission,)).fetchone()[0]


@pytest.mark.parametrize('point,table,predicate', (
    ('applied', 'planning_admission_checks', "NEW.phase='APPLIED'"),
    ('record', 'taskgraph_revision_records', '1'),
    ('followup', 'taskgraph_followups', '1'),
))
def test_commit_write_failure_leaves_no_plan_tasks_budget_or_graph_receipt(tmp_path, point, table, predicate):
    async def case():
        async with enabled_world(tmp_path, key='tg-atomic-' + point) as world:
            store, mission = world.store, world.mission.id
            # Real rounds up to the moment the Planner's adopting reply waits for collection
            # (the method it proposed passed its independent review in between).
            intent = await world.until(lambda: _adopting_reply(world))
            assert all(_rows(store, mission, name) == 0 for name in PLAN_TABLES)
            tasks = {task.id for task in store.list_tasks(mission)}
            account_ids = {r[0] for r in store.connection.execute(
                'SELECT account_id FROM budget_accounts WHERE mission_id=?', (mission,))}
            existing_intents = {r[0] for r in store.connection.execute(
                'SELECT intent_id FROM dispatch_intents WHERE mission_id=?', (mission,))}
            calls = world.provider.calls
            # A trigger is fault injection into the actual transaction, not a
            # substitute compiler/Store/Commit return value. Remove only this
            # test-owned trigger after the attempt; source constraints remain.
            store.connection.execute(
                f"CREATE TRIGGER tg_test_abort BEFORE INSERT ON {table} WHEN {predicate} "
                "BEGIN SELECT RAISE(ABORT,'TG_TEST_ATOMIC_WRITE'); END")
            try:
                # 阶段 B 裁决第 9 类：写失败不再冲出主循环；这一个任务的这一轮被接住、记一条
                # "任务一轮故障"，下一轮原地再来。
                async with asyncio.timeout(20):
                    while not any(event.type == 'MissionRoundFault' and 'TG_TEST_ATOMIC_WRITE'
                                  in event.payload['summary'] for event in store.iter_events(mission)):
                        await world.step()
            finally:
                store.connection.execute('DROP TRIGGER tg_test_abort')
            assert {task.id for task in store.list_tasks(mission)} == tasks
            for name in PLAN_TABLES:
                assert _rows(store, mission, name) == 0, name
            assert {r[0] for r in store.connection.execute(
                'SELECT account_id FROM budget_accounts WHERE mission_id=?', (mission,))} == account_ids
            assert {r[0] for r in store.connection.execute(
                'SELECT intent_id FROM dispatch_intents WHERE mission_id=?', (mission,))} == existing_intents
            assert store.connection.execute(
                "SELECT COUNT(*) FROM planning_admission_checks WHERE request_id=? AND phase='APPLIED'",
                (intent.intent_id,)).fetchone()[0] == 0
            assert not any(event.type in {'PlanRevisionCommitted', 'TaskGraphRevisionRecorded'}
                           for event in store.iter_events(mission))
            assert world.provider.calls == calls
    asyncio.run(case())


def test_committed_reply_replay_after_revoke_is_readonly_and_changed_bytes_conflict(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-committed-replay', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.store, world.mission.id
            decision = world.committed_decision()
            assert decision['status'] == 'COMMITTED'
            raw = read_nofollow(world.loop.assembled.workspaces.artifact_store.path_for(
                decision['raw_artifact_ref'])).decode('utf-8')
            request = PlanningDecisionStore(store).get_planning_request_for_intent(world.intent.intent_id)
            grant = world.grant(request.request_id)
            world.authorization.revoke(grant.grant_id, expected_revision=grant.revision,
                command_id='revoke-after-commit', reason='Verify historical receipt replay')
            before = (store.connection.total_changes, world.provider.calls)
            current = store.get_mission(mission)
            await world.loop._collect_plan_decision(world.intent, None, current, raw, world.dispatch)
            assert (store.connection.total_changes, world.provider.calls) == before
            with pytest.raises(StoreConflict):
                await world.loop._collect_plan_decision(world.intent, None, current, raw + '\n', world.dispatch)
            assert (store.connection.total_changes, world.provider.calls) == before
    asyncio.run(case())
