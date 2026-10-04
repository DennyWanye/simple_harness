# SPDX-License-Identifier: Apache-2.0
"""C03 plan transaction windows; reserve and physical-turn windows remain separate.

The child process runs the seed Mission on the product deployment and exits at one point
(``crash_seed.py``); the parent reopens the same evidence root with the product's own
startup assembly and a provider that answers nothing (recovery calls no model)."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import sqlite3

import pytest

from production_fixture import product_loop, root_of
from agent_orchestrator.artifacts.store import read_nofollow
from agent_orchestrator.storage.store import Store
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

#: Model calls before any Worker: the Planner proposes, the reviewer reviews the method,
#: the Planner adopts it.
PLANNING_CALLS = 3


def _crash(tmp_path, mode):
    child = subprocess.run([sys.executable, str(Path(__file__).with_name('crash_seed.py')),
        str(tmp_path), mode], capture_output=True, text=True, timeout=60, check=False)
    (tmp_path / 'child.log').write_text(child.stdout + child.stderr)
    return child


def _physical_calls(root: Path):
    calls = []
    for database in sorted(root.glob('execution*.db')):
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_invocations'").fetchone():
                calls += [(database.name, *row) for row in db.execute(
                    'SELECT invocation_id,state,handoff_attempt FROM provider_invocations ORDER BY invocation_id')]
    return calls


@pytest.mark.parametrize('mode,exit_code,committed', (
    ('inside_commit', 81, 0), ('after_commit', 82, 1),
))
def test_process_exit_reuses_original_reply_and_commit_identity(tmp_path, mode, exit_code, committed):
    child = _crash(tmp_path, mode)
    assert child.returncode == exit_code, (child.returncode, child.stdout, child.stderr)
    source = json.loads((tmp_path / 'recovery-source.json').read_text())
    store = Store.open(root_of(tmp_path) / 'orchestrator.db')
    try:
        for table in ('plan_revisions', 'taskgraph_revision_records'):
            assert store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == committed
        assert store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
    finally:
        store.close()

    async def recover():
        provider = RoleScriptedProvider({})
        async with product_loop(tmp_path, provider) as product:
            loop = product.loop
            mission = loop.store.get_mission(source['mission_id'])
            intent = loop.store.get_intent(source['intent_id'])
            assert mission is not None and intent is not None
            dispatch = loop._dispatch_for(mission.id)
            raw = read_nofollow(loop.assembled.workspaces.artifact_store.path_for(source['raw_artifact_ref']))
            assert hashlib.sha256(raw).hexdigest() == source['raw_output_hash'] == source['raw_artifact_ref']
            before = loop.store.connection.total_changes
            await loop._collect_plan_decision(intent, None, mission, raw.decode('utf-8'), dispatch)
            if committed:
                assert loop.store.connection.total_changes == before
            record = TaskGraphStore(loop.store).read_revision(mission.id, 1).record
            assert record.command_id == 'plan:' + source['intent_id']
            assert loop.store.connection.execute('SELECT COUNT(*) FROM plan_revisions').fetchone()[0] == 1
            assert loop.store.connection.execute('SELECT COUNT(*) FROM taskgraph_revision_records').fetchone()[0] == 1
            assert loop.store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
            assert provider.calls == 0
    asyncio.run(recover())


@pytest.mark.parametrize('mode,exit_code,attempt_count', (
    ('after_reserve', 83, 0), ('after_executor', 84, 1),
))
def test_process_exit_at_attempt_boundary_preserves_original_accounting(tmp_path, mode, exit_code, attempt_count):
    child = _crash(tmp_path, mode)
    assert child.returncode == exit_code, (child.returncode, child.stdout, child.stderr)
    source = json.loads((tmp_path / 'recovery-source.json').read_text())
    root = root_of(tmp_path)
    before_calls = _physical_calls(root)
    store = Store.open(root / 'orchestrator.db')
    try:
        for table in ('attempts', 'taskgraph_attempt_inputs'):
            assert store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == attempt_count
        assert store.connection.execute(
            "SELECT COUNT(*) FROM dispatch_intents WHERE kind='attempt'").fetchone()[0] == attempt_count
        # §10.1: the dispatch transaction that binds an Attempt writes exactly one
        # TaskGraphDispatchBound naming it (HTN 补齐阶段 A).
        assert [tuple(r) for r in store.connection.execute(
            "SELECT e.attempt_id FROM events e JOIN taskgraph_attempt_inputs i ON i.attempt_id=e.attempt_id "
            "WHERE e.type='TaskGraphDispatchBound'")] == [tuple(r) for r in store.connection.execute(
            "SELECT attempt_id FROM taskgraph_attempt_inputs")]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE type='TaskGraphDispatchBound'").fetchone()[0] == attempt_count
        assert store.connection.execute('SELECT COUNT(*) FROM taskgraph_revision_records').fetchone()[0] == 1
        # The seed plan is the Planner's own choice (proposed, independently reviewed, adopted),
        # so its model calls were made before any Worker.
        assert all(state == 'succeeded' and handoffs == 1 for _, _, state, handoffs in before_calls)
        if mode == 'after_reserve':
            assert len(before_calls) == PLANNING_CALLS
            # The Attempt's reservation was written in the transaction the exit cut short;
            # what is on disk is what was committed before it.
            reservations = [r[0] for r in store.connection.execute(
                'SELECT reservation_id FROM budget_reservations ORDER BY reservation_id')]
            assert [r[0] for r in store.connection.execute(
                'SELECT account_id FROM budget_accounts ORDER BY account_id')] == source['before_worker']['account_ids']
            assert reservations == source['before_worker']['reservation_ids']
            assert set(source['before_worker']['reservation_ids_in_transaction']) - set(reservations)
        else:
            # The planning calls, then one tool request and the final result envelope.
            assert len(before_calls) == PLANNING_CALLS + 2
    finally:
        store.close()
    if mode == 'after_reserve':
        return

    async def recover():
        provider = RoleScriptedProvider({})
        async with product_loop(tmp_path, provider) as product:
            loop = product.loop
            intent = loop.store.get_intent(source['worker_intent_id'])
            assert intent is not None and intent.state == 'SUBMITTED'
            assert await loop._collect(intent)
            assert loop.store.get_intent(intent.intent_id).state == 'SETTLED'
            # The original recovery pass imports durable physical usage after collection.
            await loop.recover()
            assert loop.commit.ledger.reservation(source['worker_attempt_id'])['state'] == 'SETTLED'
            assert provider.calls == 0 and _physical_calls(root) == before_calls
            assert loop.store.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 1
            assert loop.store.connection.execute('SELECT COUNT(*) FROM taskgraph_revision_records').fetchone()[0] == 1
            # 阶段 G（K04）：恢复后导入的是执行侧原回执——每条用量在整行事件里只写一次（"未知→
            # 已知"的覆盖除外），v3 对这个任务一致，两库对照一致。
            _assert_usage_imported_once_and_replayable(loop.store, source['mission_id'], root)
    asyncio.run(recover())


def _assert_usage_imported_once_and_replayable(store, mission_id, root):
    from agent_orchestrator.observability.business_replay import (
        CONSISTENT, verify_execution_ledgers, verify_mission)

    writes, last = {}, {}
    for (payload,) in store.connection.execute(
            "SELECT payload_json FROM events WHERE type='RowsWritten' ORDER BY seq"):
        for item in json.loads(payload)['changed']:
            if item['table'] != 'imported_usage':
                continue
            ref = item['key']['usage_ref']
            overwrite = (last.get(ref) or {}).get('unknown') == 1 and (item['row'] or {}).get('unknown') == 0
            writes[ref] = writes.get(ref, 0) + (0 if overwrite else 1)
            last[ref] = item['row']
    refs = [r[0] for r in store.connection.execute('SELECT usage_ref FROM imported_usage')]
    assert refs and sorted(writes) == sorted(refs) and set(writes.values()) == {1}, writes
    report = verify_mission(store, mission_id)
    assert report['status'] == CONSISTENT, [(t, i) for t, i in report['tables'].items() if i['status'] != CONSISTENT]
    ledgers = verify_execution_ledgers(store, sorted(root.glob('execution*.db')))
    assert ledgers['status'] == CONSISTENT and ledgers['calls'] >= len(refs), ledgers
