"""New timer path against actual SQLite SDK; no model or private SDK writes."""
import pytest
from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import HOST_PUBLIC_TURN_FILTER_POLICY
from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore, TimerConflict
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_store import HostProspectiveSignalAuthority, S5cStore
from deskpet.memory.s5c_timer_schema import initialize_s5c_timer_state_db
from tests.memory.test_s5c_consumer_sdk import fixture
from tests.memory.test_s5c_store import P


@pytest.mark.asyncio
async def test_real_due_lost_ack_expired_reopen_single_inbox(tmp_path):
    clock=[20.0]
    path,memory,_=await fixture(tmp_path,clock)
    await initialize_s5c_timer_state_db(path)
    registrations=S5cStore(path,P)
    await ProspectiveRegistrationConsumer(registrations,memory,
        PublicRegistrationAuthoritySource(store=registrations,memory=memory,clock=lambda:clock[0])).run_once()
    await memory.close()
    signals=ProspectiveSignalStore(path,P)
    registration_authority=HostProspectiveSignalAuthority(path,P)
    class Authority:
        async def resolve_prospective_signal_authority(self,ref):
            if ref.authority_id.startswith('host:time-authority:'):
                return await signals.resolve_prospective_signal_authority(ref)
            return await registration_authority.resolve_prospective_signal_authority(ref)
    async def open_memory():
        backend=SQLiteHumanMemoryBackend(tmp_path/'memory.db',now=lambda:clock[0],
            supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
            evidence_authority=HostEvidenceAuthority(path),prospective_signal_authority=Authority())
        await backend.initialize()
        return backend
    memory=await open_memory()
    calls=[]
    class LostAck:
        async def apply_prospective_signal(self,**kwargs):
            calls.append(kwargs['reference'])
            result=await memory.apply_prospective_signal(**kwargs)
            if len(calls)==1:raise TimeoutError('committed then lost ACK')
            return result
    try:
        source=PublicTimeAuthoritySource(registrations=registrations,signals=signals,lifetime_seconds=10)
        assert await source.prepare_due(now=29,limit=10)==()
        clock[0]=30
        prepared=(await source.prepare_due(now=30,limit=10))[0]
        scheduler=ProspectiveScheduler(store=signals,source=source,memory=LostAck(),clock=lambda:clock[0],lease_seconds=2)
        with pytest.raises(TimeoutError,match='committed then lost ACK'):
            await scheduler.tick(claim_owner='first')
        inbox=await memory.read_occurrence_inbox(principal=P)
        assert len(inbox.entries)==1
        key=inbox.entries[0].occurrence_key
        await memory.close();clock[0]=50;memory=await open_memory()
        signals=ProspectiveSignalStore(path,P)
        assert await signals.get_prepared(prepared.authority.intent.signal_id)==prepared
        source=PublicTimeAuthoritySource(registrations=S5cStore(path,P),signals=signals,lifetime_seconds=10)
        scheduler=ProspectiveScheduler(store=signals,source=source,memory=LostAck(),clock=lambda:clock[0],lease_seconds=2)
        assert await scheduler.tick(claim_owner='restart')==1
        assert calls[0]==calls[1]
        inbox=await memory.read_occurrence_inbox(principal=P)
        assert [e.occurrence_key for e in inbox.entries]==[key]
        assert await scheduler.tick(claim_owner='again')==0
        assert len(calls)==2
    finally:
        await memory.close()
