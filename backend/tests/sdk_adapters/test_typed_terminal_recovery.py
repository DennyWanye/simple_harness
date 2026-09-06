"""Only new pending/sink boundaries; uses the fixed installed H075 target."""
import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
import simple_harness as h
import simple_harness_memory as m

from tests.sdk_adapters.test_typed_context_use_primary import wired_runtime


@pytest.mark.parametrize('point',['before_sink','after_sink','response_reserved'])
def test_actual_process_loss_and_no_recall_sink_recovery(tmp_path, point):
    target=Path(h.__file__).resolve().parents[1]
    child=Path(__file__).with_name('typed_terminal_crash_child.py')
    def run(mode):
        result=subprocess.run([sys.executable,'-I','-B',str(child),str(tmp_path),str(target),mode,point],
            capture_output=True,text=True,timeout=30)
        (tmp_path/(mode+'.log')).write_text(result.stdout+result.stderr)
        return result
    first=run('seed')
    assert first.returncode==73, first.stderr
    crash=json.loads((tmp_path/'crash.json').read_text())
    assert len((tmp_path/'physical.jsonl').read_text().splitlines())==1
    assert len(crash['sink'])==(0 if point=='before_sink' else 1)
    resumed=run('resume')
    assert resumed.returncode==0, resumed.stderr
    after=json.loads((tmp_path/'resumed.json').read_text())
    for key in ('run_id','request_id','provider_attempt_id','grant_hash'):
        assert after[key]==crash[key]
    assert len(after['sink'])==1
    if crash['sink']: assert after['sink']==crash['sink']
    assert len((tmp_path/'physical.jsonl').read_text().splitlines())==1


async def seed_public_pending(path):
    # Reuse only Host-owned DTO construction, NOT its old private-backend seeder.
    from tests.sdk_adapters import test_no_recall_gate as seed
    from deskpet.memory.human_memory_v7 import local_memory_principal,local_memory_scope
    envelope,receipt=seed._admitted(); span=seed._span(envelope,receipt)
    authority=seed._SeedAuthority(envelope,receipt,span)
    manager=await m.MemoryManager.build_human_memory_v7(path,evidence_authority=authority,
        memory_action_authority=authority,prospective_signal_authority=authority)
    principal=local_memory_principal(); scope=local_memory_scope()
    try:
        await manager.register_principal_owner(principal=principal)
        await manager.ingest_committed_evidence(envelope,receipt)
        plan=h.MemoryMutationPlan('pending-plan','run-1','turn-1',principal.actor_id,1,
            h.MemoryMutationPlanOutcome.MUTATE,(seed._create_prospective_operation(span),),
            seed._disclosure(),(h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),),'pending-create')
        applied=await manager.apply_memory_mutation_plan(principal=principal,scope=scope,plan=plan)
        assert applied.outcome is h.MemoryMutationApplyOutcome.COMMITTED
        view=await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
        memory_id=view.operations[0].memory_id
        page=await manager.read_outbox(principal=principal)
        registration=next(r for r in page.entries if r.topic=='memory.prospective.registration.requested')
        for kind,old,new,key in (
            (h.ProspectiveSignalKind.REGISTRATION_ACCEPTED,h.ProspectiveLifecycleState.PENDING,h.ProspectiveLifecycleState.PENDING,'accepted'),
            (h.ProspectiveSignalKind.TIME_DUE,h.ProspectiveLifecycleState.PENDING,h.ProspectiveLifecycleState.TRIGGERED,'due')):
            ref=seed._grant(authority,memory_id=memory_id,revision=1,kind=kind,transition_from=old,
                transition_to=new,observed_at=seed.NOW-20,signal_id=key,
                outbox_id=registration.outbox_id if key=='accepted' else None,
                outbox_hash=registration.payload_hash if key=='accepted' else None)
            await manager.apply_prospective_signal(principal=principal,scope=scope,reference=ref)
        inbox=await manager.read_occurrence_inbox(principal=principal)
        assert any(row.outcome=='matched' for row in inbox.entries)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_actual_pending_refuses_empty_terminal_without_hiding_provider_success(tmp_path, monkeypatch):
    from deskpet.memory.schema import dispatch_startup_epoch
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory,QueueTurnRequest
    from deskpet.memory.runtime_composition import compose_human_memory_runtime
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from tests.execution.test_primary_foreground_runtime import Provider
    state=tmp_path/'state.db'
    startup=await dispatch_startup_epoch(state,approved_fresh_lane=True)
    service=HumanMemoryHostServiceFactory(state,startup).bind(local_owner_auth())
    await service.open_primary()
    await seed_public_pending(tmp_path/'memory.db')
    memory=compose_human_memory_runtime(state,tmp_path/'memory.db',adapter_factory=lambda _:None)
    provider=Provider()
    runtime,stack,queue,authority=await wired_runtime(tmp_path,state,memory,provider,monkeypatch)
    try:
        assert await memory.pending_occurrences(())
        # Same production sink reconcile as main. This may refuse only AFTER the
        # actual successful provider response; that physical fact must remain true.
        authority._terminal_sink._reconcile=memory.pending_occurrences
        await service.enqueue_turn(QueueTurnRequest(None,'pending-ordinary','Hello.'))
        assert await asyncio.wait_for(runtime._drive_once(),20)
        assert len(provider.requests)==1
        with closing(sqlite3.connect(state)) as db:
            assert db.execute("SELECT count(*) FROM context_route_decisions WHERE origin='no_recall'").fetchone()==(0,)
            assert db.execute('SELECT terminal_state FROM foreground_terminal_receipts').fetchall()==[('FAILED',)]
            run_id=db.execute('SELECT sdk_run_id FROM foreground_run_sdk_bindings').fetchone()[0]
        view=stack.read_provider_context_use(run_id,provider.requests[0].request_id.value)
        assert view.invocation_state=='succeeded' and view.requests==() and view.receipts==()
        assert await memory.pending_occurrences(())
    finally:
        await runtime.close(); await stack.close(); await memory.close()
