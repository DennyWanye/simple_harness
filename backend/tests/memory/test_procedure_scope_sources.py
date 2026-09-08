"""New source-boundary controls; parser proofs do not replace physical-run evidence."""
import aiosqlite
import pytest

from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.memory.primary_message_v3 import read_scope_sources_tx
from tests.execution.test_evidence_reservations import _bound_run, _rows, _tool_fact, RUN, fq


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
    from simple_harness.execution.context_authority import ContextRouteReceipt, TaskScopeRoute
    # This parser fixture has no workspace grant. A genuine no-authority
    # standalone decision exercises the same control producer without inventing
    # a TaskScope binding receipt. Actual-main covers the real CREATE route.
    route = ContextRouteReceipt(receipt_id='parser-control-route', run_id=RUN,
        raw_call_id='actual-route-call', effect_id='actual-route-effect',
        route=TaskScopeRoute.DIRECT_STANDALONE, task_scope_id=None, binding_set_revision=None)
    await ledger.record_route_decision(receipt=route, provider_turn_ordinal=1,
        origin='context_tool', idempotency_key='parser-control-route')
    async with aiosqlite.connect(path) as read_db:
        async with read_db.execute('SELECT decision_id FROM context_route_decisions '
                'WHERE sdk_run_id=? AND effect_id=?', (RUN, 'actual-route-effect')) as cursor:
            decision_id = (await cursor.fetchone())[0]
    await ledger.record_tool_invocation(sdk_run_id=RUN, raw_call_id='actual-route-call',
        effect_id='actual-route-effect', proposal={'route': 'direct_standalone'},
        verdict='accepted', decision_id=decision_id, detail={'route': 'direct_standalone'})
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


@pytest.mark.asyncio
async def test_mid_run_route_control_keeps_its_dispatch_reservation_tool_name(tmp_path):
    """HM-TO-A6 incident R (2026-09-08 native-a6-run7, T6): a Run's *second*
    ``context_route`` stalled the foreground driver.

    The first route call of a Run binds the admission scope, so at dispatch time
    ``ToolAdapter._reserve_evidence`` has no scope and reserves nothing; the
    control ledger's own ``ingest_ledger_fact_tx`` then reserves it with
    ``tool_name IS NULL``.  A later, mid-Run route call — now routinely guided by
    the zero-hit ``task_scope_search`` follow-up — already has a scope, so the
    ordinary Tool path reserves it first *with* ``tool_name='context_route'``.
    Both are the same legitimate control producer.  Shapes here are synthetic
    identifiers only; no evidence text from the incident is reproduced.
    """

    from deskpet.memory.primary_message_v3 import PrimaryScopeSourceError
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    from simple_harness.execution.context_authority import ContextRouteReceipt, TaskScopeRoute

    path, _, _ = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(path)
    ledger = ContextRouteLedgerStore(path, evidence_ingress=ingress)

    async def _route(effect_id, raw_call_id, verdict, *, decision):
        decision_id = None
        if decision:
            await ledger.record_route_decision(
                receipt=ContextRouteReceipt(receipt_id=f"route-{effect_id}", run_id=RUN,
                    raw_call_id=raw_call_id, effect_id=effect_id,
                    route=TaskScopeRoute.DIRECT_STANDALONE, task_scope_id=None,
                    binding_set_revision=None),
                provider_turn_ordinal=1, origin="context_tool", idempotency_key=f"route-{effect_id}")
            async with aiosqlite.connect(path) as read_db:
                async with read_db.execute("SELECT decision_id FROM context_route_decisions "
                        "WHERE sdk_run_id=? AND effect_id=?", (RUN, effect_id)) as cursor:
                    decision_id = (await cursor.fetchone())[0]
        await ledger.record_tool_invocation(sdk_run_id=RUN, raw_call_id=raw_call_id,
            effect_id=effect_id, proposal={"route": "direct_standalone"}, verdict=verdict,
            decision_id=decision_id, detail={"route": "direct_standalone"})

    # 1) Binding route: no dispatch-time reservation, ledger reserves tool_name NULL.
    await _route("route-effect-binding", "raw-call-binding", "accepted", decision=True)
    # 2) Mid-Run route: the ordinary Tool dispatch path reserves it first.
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation",
        source_event_id="effect:route-effect-midrun", tool_name="context_route")
    await _route("route-effect-midrun", "raw-call-midrun", "rejected", decision=False)
    # 3) Negative: a reservation for a *different* Tool may never be relabelled.
    await ingress.reserve(run_id=RUN, task_scope_id=fq.SCOPE, kind="tool_invocation",
        source_event_id="effect:route-effect-foreign", tool_name="write_file")
    await _route("route-effect-foreign", "raw-call-foreign", "accepted", decision=True)

    names = dict(_rows(path, "SELECT source_event_id,tool_name FROM harness_evidence_reservations "
                             "WHERE run_id=?", RUN))
    assert names["effect:route-effect-binding"] is None
    assert names["effect:route-effect-midrun"] == "context_route"
    assert names["effect:route-effect-foreign"] == "write_file"

    def control(effect_id, raw_call_id, ordinal, state="succeeded"):
        return dict(item_ordinal=ordinal, sdk_run_id=RUN, effect_id=effect_id,
            raw_call_id=raw_call_id, internal_call_id=f"internal-{effect_id}",
            tool_name="context_route", state=state)

    binding = control("route-effect-binding", "raw-call-binding", 3)
    # The incident's fact carried the SDK effect state ``failed`` (the route was
    # rejected); control lineage grants no Scope proof either way.
    midrun = control("route-effect-midrun", "raw-call-midrun", 5, state="failed")
    foreign = control("route-effect-foreign", "raw-call-foreign", 7)

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        assert await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN,
            facts=[binding, midrun]) == [
                dict(item_ordinal=3, task_scope_id=None, ingest_receipt=None),
                dict(item_ordinal=5, task_scope_id=None, ingest_receipt=None)]
        with pytest.raises(PrimaryScopeSourceError) as refused:
            await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN, facts=[foreign])
        assert str(refused.value) == "primary_message_scope_source_mismatch"
        assert refused.value.reason_code == "route_control_reservation_tool_name"
        assert refused.value.item_ordinal == 7


@pytest.mark.asyncio
async def test_scope_source_clauses_carry_stable_payload_free_reason_codes(tmp_path):
    """Every refusal names its own clause, so a stall is diagnosable from audit.

    Reason codes are Host field names plus the transcript ordinal — never
    envelope bytes, tool arguments or results.
    """

    from deskpet.execution.foreground_runtime import ForegroundRuntimeExecutionAuthority
    from deskpet.memory.primary_message_v3 import PrimaryScopeSourceError

    path, _, _ = await _bound_run(tmp_path)
    ingress = ExecutionEvidenceIngress(path)
    await ingress.commit_fact(task_scope_id=fq.SCOPE, subject=fq.SUBJECT,
        fact=_tool_fact(RUN, "clause-effect"))
    fact = dict(item_ordinal=9, sdk_run_id=RUN, effect_id="clause-effect",
        internal_call_id="call-1", tool_name="write_file", state="succeeded")
    expected = {"sdk_run_id": ("foreign-run", "fact_sdk_run_id"),
                "internal_call_id": ("other-call", "public_call_id"),
                "tool_name": ("read_file", "public_tool_name"),
                "state": ("failed", "public_effect_state")}
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        for key, (value, reason) in expected.items():
            with pytest.raises(PrimaryScopeSourceError) as refused:
                await read_scope_sources_tx(db, subject=fq.SUBJECT, sdk_run_id=RUN,
                    facts=[{**fact, key: value}])
            assert refused.value.reason_code == reason
            assert refused.value.item_ordinal == 9
            assert str(refused.value) == "primary_message_scope_source_mismatch"
        with pytest.raises(PrimaryScopeSourceError) as owner:
            await read_scope_sources_tx(db, subject="foreign-owner", sdk_run_id=RUN, facts=[fact])
        assert owner.value.reason_code == "task_scope_owner_subject"

    # The driver's failed/stalled audit lines carry the clause, not just the code.
    carried = ForegroundRuntimeExecutionAuthority._reason_audit_fields(owner.value)
    assert carried == {"error_reason_code": "task_scope_owner_subject",
                       "error_reason_ordinal": 9}
    assert ForegroundRuntimeExecutionAuthority._reason_audit_fields(RuntimeError("plain")) == {}
