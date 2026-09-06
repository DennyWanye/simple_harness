"""New source-boundary controls; parser proofs do not replace physical-run evidence."""
import aiosqlite
import pytest

from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.memory.primary_message_v3 import read_scope_sources_tx
from tests.execution.test_evidence_reservations import _bound_run, _tool_fact, RUN, fq


@pytest.mark.asyncio
async def test_scope_source_exact_ingest_receipt_rejects_foreign_run_call_state_owner(tmp_path):
    path, _, _ = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(path)
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT,
        fact=_tool_fact(RUN, "procedure-source-effect"))
    fact = dict(item_ordinal=3, sdk_run_id=RUN, effect_id="procedure-source-effect",
        internal_call_id="call-1", tool_name="write_file", state="succeeded")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        result = await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[fact])
        assert result[0]["task_scope_id"] == fq.SCOPE
        assert result[0]["ingest_receipt"]["source_event_id"] == "effect:procedure-source-effect"
        for change in ({"sdk_run_id": "foreign-run"}, {"internal_call_id": "other-call"}, {"state": "failed"}):
            with pytest.raises(RuntimeError, match="scope_source_mismatch"):
                await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[{**fact, **change}])
        with pytest.raises(RuntimeError, match="scope_source_mismatch"):
            await read_scope_sources_tx(db, subject="foreign-owner", sdk_run_id=RUN, facts=[fact])
        absent = await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN,
            facts=[{**fact, "effect_id": "never-committed"}])
        assert absent == [dict(item_ordinal=3, task_scope_id=None, ingest_receipt=None)]


@pytest.mark.asyncio
async def test_v2_group_hashes_remain_exact_after_explicit_v53_upgrade(tmp_path):
    from tests.memory.test_primary_tool_message_ingestion import host
    from tests.memory.test_primary_short_ingestion import memory
    from deskpet.memory.conversation_registration import PrimaryConversationAuthority
    from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
    from deskpet.memory.human_memory_service import QueueTurnRequest
    from deskpet.memory.procedure_schema import initialize_procedure_state_db
    from deskpet.sdk_adapters.context_route import local_owner_auth
    path, service, primary, runtime, stack, _ = await host(tmp_path)
    manager = None
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "procedure-legacy-source", "Search available tools"))
        assert await runtime._drive_once() and runtime.last_error is None
        authority = PrimaryConversationAuthority(path, subject=local_owner_auth().subject, primary_ref=primary)
        manager = await memory(tmp_path / "memory.db", authority)
        assert await MemoryIngestionOutboxWorker(path, lambda: manager, owner_id="legacy-proof").run_once() == "delivered"
        run_id, = await authority.completed_run_ids()
        before = await authority.registrations_for_run(run_id)
        assert before.terminal_source[0].sanitized_payload["message_source_contract"] == "primary-message-v2"
        await initialize_procedure_state_db(path)
        after = await authority.registrations_for_run(run_id)
        assert after == before and after.references == before.references
    finally:
        if manager is not None:
            await manager.close()
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_real_route_control_ledger_has_no_physical_scope_and_mismatch_rejects(tmp_path):
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    path, _, _ = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(path)
    ledger = ContextRouteLedgerStore(path, evidence_ingress=ingress)
    await ledger.record_tool_invocation(sdk_run_id=RUN, raw_call_id='actual-route-call',
        effect_id='actual-route-effect', proposal={'route': 'continue_active'},
        verdict='accepted', decision_id='actual-route-decision', detail={'route': 'continue_active'})
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT,
        fact=_tool_fact(RUN, 'actual-physical-effect'))
    control = dict(item_ordinal=3, sdk_run_id=RUN, effect_id='actual-route-effect',
        raw_call_id='actual-route-call', internal_call_id='internal-route-call',
        tool_name='context_route', state='succeeded')
    physical = dict(item_ordinal=5, sdk_run_id=RUN, effect_id='actual-physical-effect',
        internal_call_id='call-1', tool_name='write_file', state='succeeded')
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        result = await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[control, physical])
        assert result[0] == dict(item_ordinal=3, task_scope_id=None, ingest_receipt=None)
        assert result[1]['task_scope_id'] == fq.SCOPE
        assert result[1]['ingest_receipt']['source_event_id'] == 'effect:actual-physical-effect'
        for change in ({'raw_call_id': 'foreign-raw'}, {'sdk_run_id': 'foreign-run'}, {'tool_name': 'write_file'}):
            with pytest.raises(RuntimeError, match='scope_source_mismatch'):
                await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[{**control, **change}])
        # Simulate a damaged read response only, retaining real rows/triggers.
        class ChangedCursor:
            def __init__(self, original):
                self.original = original
            async def fetchone(self):
                row = await self.original.fetchone()
                return None if row is None else dict(row, proposal_hash='0' * 64)
        class ChangedLedgerRead:
            async def execute(self, sql, params=()):
                cursor = await db.execute(sql, params)
                return ChangedCursor(cursor) if 'FROM context_route_tool_invocations' in sql else cursor
        with pytest.raises(RuntimeError, match='scope_source_mismatch'):
            await read_scope_sources_tx(ChangedLedgerRead(), subject=fq.SUBJECT, sdk_run_id=RUN, facts=[control])
        assert await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[control, physical]) == result
