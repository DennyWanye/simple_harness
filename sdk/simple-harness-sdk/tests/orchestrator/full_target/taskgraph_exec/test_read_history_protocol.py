# SPDX-License-Identifier: Apache-2.0
"""Production-source history/authority assertions; each covers named subcases.

The Worker is held (a slow model) so nothing but the seed plan has happened when these read."""
import asyncio
import sqlite3

import pytest

from production_fixture import enabled_world
from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.api.taskgraph import TaskGraphReadError
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.observability.taskgraph_replay import replay_taskgraph
from agent_orchestrator.orchestrator.taskgraph_policy import enable_command_id
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore


def test_historical_structure_is_nonexecutable_and_offline_projection_has_no_work(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-history-read', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            before = (store.last_event_seq(mission), world.provider.calls)
            history = TaskGraphStore(store).read_revision(mission, 1)
            assert history.executable is False
            snapshot = world.graph.reads.snapshot(mission, revision=1)
            assert snapshot['view_mode'] == 'HISTORICAL_STRUCTURE'
            assert snapshot['planning_frontier'] == snapshot['execution_frontier'] == []
            assert all(node['phase'] == 'HISTORY_ONLY' for node in snapshot['nodes'])
            target = tmp_path / 'offline-projection.db'
            report = replay_taskgraph(store, mission_id=mission, through_revision=1, target_path=target)
            assert report.runtime_status == 'RUNTIME_RESUME_NOT_AUTHORIZED'
            with sqlite3.connect(target) as db:
                assert db.execute('SELECT manifest_hash FROM revisions').fetchone()[0] == history.record.manifest_hash
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert not {'attempts', 'dispatch_intents', 'actions'} & tables
            assert (store.last_event_seq(mission), world.provider.calls) == before
    asyncio.run(case())


def test_seed_certificate_uses_original_applied_check_and_receipt(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-original-applied', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            record = TaskGraphStore(store).read_revision(mission, 1).record
            assert record.admission_check_id
            applied = store.connection.execute(
                'SELECT phase FROM planning_admission_checks WHERE check_id=?',
                (record.admission_check_id,)).fetchone()
            assert applied[0] == 'APPLIED'
            receipt = HtnStore(store).get_commit_receipt(record.command_id)
            assert receipt is not None and receipt.mission_id == mission
            assert record.parent_revision is None
            assert world.graph.reads.diff(mission, from_revision=1, to_revision=1)['changes'] == []
    asyncio.run(case())


def test_authenticated_read_cannot_cross_tenant_and_has_no_side_effects(tmp_path):
    """A deployment serves one tenant: another tenant cannot put a Mission into it (the
    assured creation refuses the tenant inside the creation transaction, nothing is left
    behind), and a read for a Mission this tenant does not own is NOT_FOUND and writes nothing."""
    async def case():
        async with enabled_world(tmp_path, key='tg-tenant-read', hold_worker=True) as world:
            await world.commit_seed()
            store = world.store
            missions = {mission.id for mission in store.list_missions()}
            events = store.connection.execute('SELECT MAX(seq) FROM events').fetchone()[0]
            foreign = MissionControlV1(world.loop, tenant_id='other-tenant', principal=Principal('other-user'))
            with pytest.raises(FacadeError, match='ASSURANCE_CREATION_PROTOCOL_MISMATCH'):
                foreign.create({'goal': 'Foreign Mission', 'success_criteria': ['kept isolated'],
                                'idempotency_key': 'foreign-tg-read'})
            assert {mission.id for mission in store.list_missions()} == missions
            assert store.connection.execute('SELECT MAX(seq) FROM events').fetchone()[0] == events
            changes = store.connection.total_changes
            with pytest.raises(TaskGraphReadError) as refused:
                world.graph.reads.snapshot('mission-of-another-tenant')
            assert refused.value.code == 'NOT_FOUND'
            assert store.connection.total_changes == changes
    asyncio.run(case())


def test_repeated_enable_reads_the_creation_receipt_without_renewing_permission(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-enable-replay', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            before = (store.last_event_seq(mission), world.provider.calls)
            # The enable the deployment issued when it created the Mission, run again.
            command_id = enable_command_id(mission)
            receipt = world.graph.policy.enable_taskgraph_contract(mission, command_id)
            assert receipt == store.get_receipt(command_id)
            assert receipt['mission_id'] == mission
            # Reading the original result writes nothing and asks no model.
            assert (store.last_event_seq(mission), world.provider.calls) == before
    asyncio.run(case())


def test_first_epoch_invalidation_and_notification_share_rollback_boundary(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-epoch-source', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            htn = HtnStore(store)
            scope = 'fresh-scope'
            before = store.last_event_seq(mission)
            class Rollback(Exception):
                pass
            with pytest.raises(Rollback):
                with store.transaction():
                    assert htn.bump_epoch(mission, scope, bumped_by='test-issuer') == 1
                    raise Rollback()
            assert htn.epoch(mission, scope) == 0
            assert store.last_event_seq(mission) == before
            assert htn.bump_epoch(mission, scope, bumped_by='test-issuer') == 1
            events = store.list_events(mission, after_seq=before)
            # The assured lane notes the same change in its own evidence clock.
            assert {event.type for event in events} <= {'TaskGraphSourceChanged', 'AssuranceEvidenceChanged'}
            [changed] = [event for event in events if event.type == 'TaskGraphSourceChanged']
            source = changed.payload['source_ref']
            assert (source['kind'], source['id'], source['revision']) == ('validity_epoch', scope, 1)
    asyncio.run(case())

