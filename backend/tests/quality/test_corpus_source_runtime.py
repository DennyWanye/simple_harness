"""Actual Host/SDK source Run is not scoring history; deterministic transport only."""
import pytest
from deskpet.quality.corpus_source import import_setup_conversation_sources
from deskpet.quality.corpus_runtime import execute_scoring_turn
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from tests.memory.test_primary_read_api import setup,result,AUTH
import tests.execution.test_primary_foreground_runtime as runtime_fixture


@pytest.mark.asyncio
async def test_source_run_import_interruption_replay_and_scoring_request_isolation(tmp_path,monkeypatch):
    import simple_harness_memory as m
    from deskpet.sdk_adapters.context_route import local_owner_auth
    import tests.memory.test_primary_read_api as read_fixture
    auth=local_owner_auth()
    monkeypatch.setattr(read_fixture,'AUTH',auth)
    source=await setup(tmp_path/'source')
    scoring=await setup(tmp_path/'scoring')
    source_text='独立setup源：只在源Run出现的CORPUS_SOURCE_ONLY_673。'
    result(await source.send('queue.enqueue',{'text':source_text},key='synthetic-source'))
    source_provider=runtime_fixture.Provider()
    runtime,stack,_=await runtime_fixture.build(tmp_path/'source',source.path,source_provider)
    try:
        await runtime.after_enqueue(subject=auth.subject)
        await runtime.drain()
        assert runtime.last_error is None and len(source_provider.requests)==1
        ids=await PrimaryConversationAuthority(source.path,subject=auth.subject).completed_run_ids()
        assert len(ids)==1
    finally:
        await runtime.close()
        await stack.close()
    args=dict(source_path=source.path,scoring_path=scoring.path,subject=auth.subject,host_run_id=ids[0])
    with pytest.raises(ValueError,match='corpus_source_scoring_store_must_differ'):
        await import_setup_conversation_sources(**dict(args,scoring_path=source.path))
    append=HumanMemoryProgramStore.append_evidence
    calls=0
    async def interrupted(self,envelope,receipt):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('fixture_import_interruption')
        return await append(self,envelope,receipt)
    with monkeypatch.context() as patch:
        patch.setattr(HumanMemoryProgramStore,'append_evidence',interrupted)
        with pytest.raises(RuntimeError,match='fixture_import_interruption'):
            await import_setup_conversation_sources(**args)
    assert calls==2
    assert result(await scoring.send('primary.messages.page',{'primary_ref':scoring.primary},key='partial'))['items']==[]
    group=await import_setup_conversation_sources(**args)
    assert await import_setup_conversation_sources(**args)==group
    reader=HostEvidenceAuthority(scoring.path)
    for registration in group.registrations:
        assert await reader.read_admitted(registration.envelope.evidence_id)==(
            registration.envelope,registration.admission_receipt)
    user=group.registrations[0]
    principal=m.MemoryPrincipal('host','household',auth.subject,'corpus-fixture')
    assert await HostHistorySourceAuthority(scoring.path).resolve_history_source(
        principal=principal,envelope=user.envelope,receipt=user.admission_receipt) is None
    assert await PrimaryConversationAuthority(scoring.path,subject=auth.subject).completed_run_ids()==()
    assert result(await scoring.send('primary.messages.page',{'primary_ref':scoring.primary},key='imported'))['items']==[]
    query='评分独立问题：请简单打个招呼。'
    scoring_provider=runtime_fixture.Provider()
    runtime,stack,_=await runtime_fixture.build(tmp_path/'scoring',scoring.path,scoring_provider)
    try:
        executed=await execute_scoring_turn(service=scoring.factory.bind(auth),runtime=runtime,
            scoring_path=scoring.path,subject=auth.subject,text=query,delivery_key='score-query')
        assert executed.completed_group.terminal_source[0].sanitized_payload['terminal_state']=='COMPLETED'
        assert runtime.last_error is None and len(scoring_provider.requests)==1
        outgoing=repr(scoring_provider.requests[0].messages)
        assert query in outgoing
        assert 'CORPUS_SOURCE_ONLY_673' not in outgoing
        assert 'Actual response 1' not in outgoing
    finally:
        await runtime.close()
        await stack.close()
