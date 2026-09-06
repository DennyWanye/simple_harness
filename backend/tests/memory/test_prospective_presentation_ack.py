"""New A7 transactions over real SDK inbox; fixture Run IDs are unit scope.

No real Provider, UI, or production final-disclosure wiring claimed here.
"""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from deskpet.memory.s5c_store import S5cStore, S5cConflict
from deskpet.memory.prospective_occurrence import (
    ProspectiveOccurrenceCoordinator, CurrentOccurrenceRead, settle_acknowledged_tx, settle_exited_tx,
)
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.prospective_ack import prospective_ack_registration
from deskpet.execution.terminal_identity import PrimaryTerminalIdentity
from tests.memory.test_prospective_consumer_m617 import World, P


async def setup(tmp_path):
    w=World(tmp_path)
    await w.setup();await w.mutate('a7-create');await w.consumer().run_once()
    w.clock[0]=30.
    signals=ProspectiveSignalStore(w.path,P)
    assert await ProspectiveScheduler(store=signals,source=PublicTimeAuthoritySource(
        registrations=S5cStore(w.path,P),signals=signals),memory=w,clock=lambda:w.clock[0]).tick(claim_owner='a7-timer')==1
    return w


def coordinator(w,*,fault=None):
    async def read_current(*,principal,sdk_run_id,requested_keys):
        # Unit authority: explicit fixture Run set; production must resolve the
        # real Run/disclosure context, never accept arbitrary run strings.
        assert principal==P and sdk_run_id in {'run-1','run-2','run-3','run-4'}
        return CurrentOccurrenceRead(S5cStore(w.path,P).owner,sdk_run_id,'d'*64,
            (await w.manager.read_occurrence_inbox(principal=principal)).entries)
    return ProspectiveOccurrenceCoordinator(store=S5cStore(w.path,P,fault_inject=fault),
        read_current=read_current,clock=lambda:w.clock[0])


async def snapshot(w,c,run,ordinal=1):
    prepared=await c.prepare(run)
    ledger=ContextRouteLedgerStore(w.path,clock=lambda:w.clock[0])
    identity=await ledger.record_snapshot_receipt(sdk_run_id=run,provider_turn_ordinal=ordinal,
        prior_context_revision=1,payload_hash='a'*64,expected_request_fingerprint='a'*64,
        source_revisions={'context':1},occurrence_coordinator=c,occurrence_presentation=prepared)
    return prepared,identity


async def facts(c):
    async with c.store._transaction() as db:
        async with db.execute('SELECT phase,COUNT(*) n FROM prospective_occurrences GROUP BY phase') as q:
            counts={r['phase']:r['n'] for r in await q.fetchall()}
        async with db.execute('SELECT COUNT(*) FROM occurrence_presented') as q:
            exited=(await q.fetchone())[0]
    return counts,exited


@pytest.mark.asyncio
async def test_three_runs_overdue_fourth_ack_receipt_terminal(tmp_path):
    w=await setup(tmp_path);c=coordinator(w)
    try:
        for i in range(1,4):
            prepared,identity=await snapshot(w,c,f'run-{i}')
            assert len(prepared.items)==1
            assert prepared.items[0].count==i and prepared.items[0].overdue==(i==3)
            # Same Run later provider turn, no second presented fact.
            replay,_=await snapshot(w,c,f'run-{i}',2)
            assert replay.items[0].count==i
            counts,exit_count=await facts(c)
            assert counts['presented']==i and counts.get('overdue',0)==int(i>=3)
            assert exit_count==0
        prepared,_=await snapshot(w,c,'run-4')
        key=prepared.items[0].entry.occurrence_key
        registration=prospective_ack_registration(coordinator=c,
            context_getter=lambda:SimpleNamespace(run_id=SimpleNamespace(value='run-4')))
        receipt=await registration.handler({'occurrence_key':key},None)
        assert await registration.handler({'occurrence_key':key},None)==receipt
        counts,exit_count=await facts(c)
        assert counts['acknowledged']==1 and 'settled' not in counts and exit_count==1
        assert (await c.prepare('run-4')).items==()
        identity=PrimaryTerminalIdentity('run-4','fixture-terminal','f'*64,'COMPLETED',
            'fixture-host-receipt','e'*64,False,None)
        actual=SimpleNamespace(run_id='run-4',event_id='fixture-terminal',event_hash='f'*64,state='completed')
        async with c.store._transaction() as db:
            await settle_acknowledged_tx(db,principal=P,sdk_run_id='run-4',terminal_identity=identity,actual_sdk_terminal=actual)
        async with c.store._transaction() as db:
            await settle_acknowledged_tx(db,principal=P,sdk_run_id='run-4',terminal_identity=identity,actual_sdk_terminal=actual)
        counts,_=await facts(c)
        assert counts=={'claimed':1,'presented':4,'overdue':1,'acknowledged':1,'settled':1}
        assert len((await w.manager.read_occurrence_inbox(principal=P)).entries)==1
    finally:await w.manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('point',['s5c.ack.before_commit','s5c.ack.after_commit'])
async def test_ack_fault_reopen_exact_receipt(tmp_path,point):
    w=await setup(tmp_path)
    fired=False
    def fault(value):
        nonlocal fired
        if value==point and not fired:
            fired=True;raise ConnectionError('lost ACK')
    c=coordinator(w,fault=fault)
    try:
        prepared,_=await snapshot(w,c,'run-1');key=prepared.items[0].entry.occurrence_key
        with pytest.raises(ConnectionError):await c.ack(sdk_run_id='run-1',occurrence_key=key)
        counts,exited=await facts(c)
        assert counts.get('acknowledged',0)==int(point.endswith('after_commit'))
        assert exited==int(point.endswith('after_commit'))
        c=coordinator(w)
        result=await c.ack(sdk_run_id='run-1',occurrence_key=key)
        assert await c.ack(sdk_run_id='run-1',occurrence_key=key)==result
        before=await facts(c)
        with pytest.raises(S5cConflict,match='not_presented_in_run'):
            await c.ack(sdk_run_id='run-2',occurrence_key=key)
        with pytest.raises(S5cConflict):await c.ack(sdk_run_id='run-1',occurrence_key='0'*64)
        assert await facts(c)==before
    finally:await w.manager.close()


@pytest.mark.asyncio
async def test_actual_guard_missing_handoff_keeps_rejected_classification():
    from simple_harness import RequestId
    from simple_harness.providers import ProviderRequest
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers.errors import ProviderRequestRejectedError
    from deskpet.sdk_adapters.prospective_request_guard import ProspectiveRequestGuard, ProspectiveRequestRejected
    guard=ProspectiveRequestGuard(sdk_run_id='actual-request-run',
        coordinator=SimpleNamespace(store=SimpleNamespace(principal=P)),
        read_provider_context_use=lambda run,request:None)
    request=ProviderRequest(RequestId('physical-attempt'),(Message(role=MessageRole.USER,content='hello'),))
    with pytest.raises(ProspectiveRequestRejected) as denied:
        await guard(request)
    assert isinstance(denied.value,ProviderRequestRejectedError)


@pytest.mark.asyncio
async def test_snapshot_rollback_and_full_group_codec(tmp_path):
    from deskpet.memory.prospective_occurrence import presentation_payload, presentation_from_payload, decode_snapshot
    from copy import deepcopy
    w=await setup(tmp_path);c=coordinator(w)
    try:
        prepared=await c.prepare('run-1')
        payload=presentation_payload(prepared)
        assert presentation_from_payload(payload)==prepared
        malformed=[]
        extra=deepcopy(payload);extra['unexpected']=1;malformed.append(extra)
        extra=deepcopy(payload);extra['items'][0]['extra']=1;malformed.append(extra)
        duplicate=deepcopy(payload);duplicate['items'].append(deepcopy(duplicate['items'][0]));malformed.append(duplicate)
        wrongindex=deepcopy(payload);wrongindex['items'][0]['index']=1;malformed.append(wrongindex)
        for changed in malformed:
            with pytest.raises(S5cConflict):presentation_from_payload(changed)
        class FailingIngress:
            async def ingest_ledger_fact_tx(self,*args,**kwargs):
                raise ConnectionError('snapshot before commit')
        ledger=ContextRouteLedgerStore(w.path,clock=lambda:w.clock[0],evidence_ingress=FailingIngress())
        with pytest.raises(ConnectionError,match='snapshot before commit'):
            await ledger.record_snapshot_receipt(sdk_run_id='run-1',provider_turn_ordinal=1,
                prior_context_revision=1,payload_hash='a'*64,expected_request_fingerprint='a'*64,
                source_revisions={'context':1},occurrence_coordinator=c,occurrence_presentation=prepared)
        assert await facts(c)==({},0)
        async with c.store._transaction() as db:
            assert (await (await db.execute('SELECT COUNT(*) FROM run_context_snapshot_receipts')).fetchone())[0]==0
        _,identity=await snapshot(w,c,'run-1')
        restored=await c.restore_snapshot(sdk_run_id='run-1',provider_turn_ordinal=1,prior_context_revision=1,snapshot_id=identity[0])
        assert restored==prepared
        async with c.store._transaction() as db:
            row=dict(await (await db.execute('SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?',(identity[0],))).fetchone())
        revisions,group=decode_snapshot(row)
        assert revisions=={'context':1} and group==prepared
        corrupted=dict(row,receipt_hash='0'*64)
        with pytest.raises(S5cConflict,match='receipt_corrupt'):decode_snapshot(corrupted)
    finally:await w.manager.close()
