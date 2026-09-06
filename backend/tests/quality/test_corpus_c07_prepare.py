"""Prepared, NOT_RUN C07 controls. Deterministic transport is not model quality."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
import simple_harness as h
import simple_harness_memory as m

from deskpet.quality.corpus_c07 import SETUPS, compile_c07_setup, validate_c07_setup
from deskpet.quality.corpus_c07_prepare import prepare_c07_seed, prepare_c07_recent_group
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.human_memory_v7 import (
    local_memory_principal, host_classification_policy as classification_policy,
    HOST_SUPPORTED_FILTER_POLICIES as FILTERS,
)
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.memory.test_primary_read_api import setup, result

CLOCK = '2026-09-06T10:00:00+08:00'
ORIGINAL = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/'
    'quality/recall-corpus-candidate/review-zh/successor-12x20/07-no-match.md')


def batch(case_id):
    return compile_c07_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)


def test_all_twenty_original_setups_and_input_boundary():
    # Compare ONLY original setup fields, not gold or generated answers.
    originals, current = {}, None
    for line in ORIGINAL.read_text().splitlines():
        if line.startswith('## C07-'):
            current = line.split('｜', 1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '):
            originals[current] = line.split('** ', 1)[1]
    assert set(originals) == set(SETUPS) == {f'C07-{n:02}' for n in range(1, 21)}
    for case_id, text in originals.items():
        assert SETUPS[case_id] == (text, sha256(text.encode()).hexdigest())
        compiled = batch(case_id)
        assert compiled.distractor.source_text and compiled.distractor.source_text != text
        validate_c07_setup(compiled)
        with pytest.raises(ValueError, match='source_changed'):
            compile_c07_setup(case_id, {'setup': text, 'gold': 'forbidden'}, scenario_clock=CLOCK)
        with pytest.raises(ValueError, match='manifest_differs'):
            validate_c07_setup(replace(compiled, distractor=replace(compiled.distractor, value='tampered')))
    assert batch('C07-11').distractor.kind == 'procedure'
    assert len(batch('C07-11').distractor.steps) == 5
    assert batch('C07-06').recent_messages == (
        ('user', '红盒放钥匙，蓝盒放卡片。'), ('assistant', '收到，钥匙在红盒，卡片在蓝盒。'))
    assert batch('C07-14').recent_messages == (
        ('user', '书架第二层留空。'), ('assistant', '收到，第二层留空。'))


async def seed_at(host, memory_path, compiled):
    delivery = SetupFixtureDeliveryAuthority()
    principal = local_memory_principal()
    manager = await m.build_human_memory_v7(memory_path,
        classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(host.path), analysis_delivery_authority=delivery,
        clock=lambda: compiled.scenario_time)
    try:
        actual = await prepare_c07_seed(path=host.path, manager=manager, principal=principal,
            authority_ref=local_owner_auth().authority_ref, batch=compiled, delivery_authority=delivery)
        assert actual['application'].receipt.validation_status is h.AnalysisValidationStatus.ACCEPTED
        source = actual['source_pair'][0]
        expected_refs = (h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
        assert actual['request'].ordered_evidence_refs == actual['plan'].evidence_refs == expected_refs
        assert actual['fixture_executions'] == 1
        assert (await actual['runner'].run_once()).value == 'idle'
        return actual
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id,kind', [('C07-01', 'semantic'), ('C07-03', 'episode'), ('C07-11', 'procedure')])
async def test_real_nonempty_job_source_and_cold_public_readback(tmp_path, monkeypatch, case_id, kind):
    import tests.memory.test_primary_read_api as read_fixture
    monkeypatch.setattr(read_fixture, 'AUTH', local_owner_auth())
    host = await setup(tmp_path / 'host')
    compiled = batch(case_id)
    actual = await seed_at(host, tmp_path / 'memory.db', compiled)
    source, proof = actual['source_pair']
    assert await HostEvidenceAuthority(host.path).read_admitted(source.evidence_id) == (source, proof)
    assert source.sanitized_payload['text'] == compiled.distractor.source_text
    assert actual['plan'].operations[0].evidence_spans[0].evidence_id == source.evidence_id
    assert actual['labels']['distractor'].memory_type == kind
    # No fake history needed to get a genuinely nonempty cognitive library.
    assert await PrimaryConversationAuthority(host.path, subject=local_owner_auth().subject).completed_run_ids() == ()
    assert result(await host.send('primary.messages.page', {'primary_ref': host.primary}, key='no-seed-history'))['items'] == []
    manager = await m.build_human_memory_v7(tmp_path / 'memory.db',
        classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(host.path), clock=lambda: compiled.scenario_time)
    try:
        graph = await manager.get_twin_graph_view(principal=local_memory_principal())
        assert len(graph.nodes) == 1 and not graph.edges
        assert graph.nodes[0] == actual['labels']['distractor']
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ['C07-06', 'C07-14'])
async def test_authored_group_reaches_next_actual_context_without_distractor_leak(tmp_path, monkeypatch, case_id):
    from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
    from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
    from deskpet.quality.corpus_runtime import execute_scoring_turn
    import tests.memory.test_primary_read_api as read_fixture
    import tests.execution.test_primary_foreground_runtime as runtime_fixture
    monkeypatch.setattr(read_fixture, 'AUTH', local_owner_auth())
    host = await setup(tmp_path / 'host')
    compiled = batch(case_id)
    await seed_at(host, tmp_path / 'visibility-memory.db', compiled)
    class AuthoredTransport(runtime_fixture.Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            # Original assistant message from recent_messages, not query gold.
            text = compiled.recent_messages[1][1] if len(self.requests) == 1 else 'fixture scoring response'
            return runtime_fixture.ProviderResponse(request.request_id,
                runtime_fixture.Message(runtime_fixture.MessageRole.ASSISTANT,
                    (runtime_fixture.ContentBlock('output_text', {'text': text}),)),
                model='model', usage=runtime_fixture.ProviderUsage(10, 10, 20), opaque_continuation_ref='fixture-opaque')
    provider = AuthoredTransport()
    memory = HumanMemoryV7Runtime(tmp_path / 'visibility-memory.db',
        evidence_authority=HostEvidenceAuthority(host.path), clock=lambda: compiled.scenario_time)
    runtime, stack, _ = await runtime_fixture.build(tmp_path, host.path, provider, visibility_memory=memory)
    worker = MemoryIngestionOutboxWorker(host.path, memory.manager, owner_id='c07-recent-user')
    rows = [dict(ordinal=i + 1, role=role, content=text) for i, (role, text) in enumerate(compiled.recent_messages)]
    try:
        # Malformed role/order must fail before any actual SDK request.
        with pytest.raises(ValueError, match='original_recent_messages_differ'):
            await prepare_c07_recent_group(path=host.path, subject=local_owner_auth().subject,
                batch=compiled, recent_messages=[dict(rows[0], role='assistant'), rows[1]],
                service=host.factory.bind(local_owner_auth()), runtime=runtime, ingestion_worker=worker)
        assert provider.requests == []
        recent = await prepare_c07_recent_group(path=host.path, subject=local_owner_auth().subject,
            batch=compiled, recent_messages=rows, service=host.factory.bind(local_owner_auth()),
            runtime=runtime, ingestion_worker=worker)
        assert len(recent.completed_group.registrations) == 2
        current = '独立当前输入，不合并历史消息。'
        await execute_scoring_turn(service=host.factory.bind(local_owner_auth()), runtime=runtime,
            scoring_path=host.path, subject=local_owner_auth().subject, text=current,
            delivery_key='c07-next-physical-context', ingestion_worker=worker)
        assert len(provider.requests) == 2
        # This is the actual outgoing request assembled from the committed group,
        # not a mock return from PrimaryHistoryStore or an AX/text fixture.
        messages = provider.requests[-1].messages
        sent = [(message.role.value, str(message.content)) for message in messages]
        for role, text in compiled.recent_messages:
            assert any(actual_role == role and text in content for actual_role, content in sent), sent
        assert compiled.distractor.source_text not in repr(messages)
        assert current in repr(messages)
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()
