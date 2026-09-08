"""C12 recipient-private source controls: seed, bindings, signed audience config.

No model, no Provider, no gold read. Verifies the real SDK recipient gate on
the seeded SENSITIVE A and the real Host signed-control disclosure binding.
"""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.current_input_source import COMMON_POLICY_HASH, read_current_input_source
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.human_memory_v7 import (local_memory_principal, host_classification_policy,
    HOST_SUPPORTED_FILTER_POLICIES)
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.quality.corpus_c12 import (SETUPS, SPECS, RECIPIENTS, NO_CONCRETE_VALUE, INPUT_MARKER,
    compile_c12_setup, validate_c12_setup, c12_binding_record, compile_c12_input, disclosure_selection)
from deskpet.quality.corpus_c12_control import FixtureSignedControl
from deskpet.quality.corpus_c12_prepare import open_c12_fixture
from tests.quality.test_corpus_c01_revision_clock import _host, AUTH, CLOCK

CANDIDATES = (os.environ.get('CORPUS_MEMORY_SDK_ROOT'), '/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk',
              '/Users/denny/projects/simple-harness-memory-sdk')
SDK = next((Path(c) for c in CANDIDATES if c and Path(c).is_dir()), None)
CORPUS = None if SDK is None else SDK / ('plans/2026-08-29-human-memory-digital-twin/quality/'
                                          'recall-corpus-candidate/review-zh/successor-12x20')
needs_corpus = pytest.mark.skipif(CORPUS is None, reason='memory SDK corpus checkout not found')


def _context(recipient, recipient_id, audience, *, subject, run_id='c12-gate-probe'):
    return h.DisclosureContext(run_id, subject, h.DeliveryRecipient(recipient), recipient_id,
        h.IntendedAudience(audience), h.DisclosurePurpose.TASK_EXECUTION, h.DisclosureSource.AUTHENTICATED_HOST,
        h.DisclosureTrust.TRUSTED_AUTHORITY, h.DisclosureGeneration.CURRENT, 'host:c12-probe',
        (h.DisclosureReasonCode.MINIMUM_NECESSARY,))


@needs_corpus
def test_original_setup_mapping_and_reviewed_bindings_are_explicit():
    import sys
    sys.path.insert(0, str(SDK / 'scripts'))
    original, case_id = {}, None
    for line in (CORPUS / '12-recipient-private.md').read_text().splitlines():
        if line.startswith('## C12-'):
            case_id = line.split('｜', 1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '):
            original[case_id] = line.split('** ', 1)[1]
    assert set(original) == set(SETUPS) == set(SPECS) == set(RECIPIENTS) == {f'C12-{i:02}' for i in range(1, 21)}
    for case_id, text in original.items():
        assert SETUPS[case_id] == (text, sha256(text.encode()).hexdigest())
        batch = compile_c12_setup(case_id, text, scenario_clock=CLOCK)
        with pytest.raises(ValueError, match='exact_manifest'):
            validate_c12_setup(replace(batch, specs=()))
        with pytest.raises(ValueError, match='exact_original_setup'):
            compile_c12_setup(case_id, text + ' ', scenario_clock=CLOCK)
        # Every A is SENSITIVE with health/family attributes; values come from setup only.
        assert all(privacy == 'sensitive' and set(attrs) & {'health', 'family'} for _, privacy, attrs in batch.classification)
        for label, kind, subject, predicate, value, qualifiers in batch.specs:
            assert subject == 'user:self' and (value in text or case_id in {'C12-04', 'C12-09'} or case_id in NO_CONCRETE_VALUE)
        record = c12_binding_record(CORPUS, case_id)
        assert record['policy_sha256'] == COMMON_POLICY_HASH  # Host common policy == corpus policy line
        composed = ''.join(p['text'] for p in record['partition'])
        assert composed == INPUT_MARKER + record['composed_input']
        authored = dict(current_user_message=None, recent_messages=[], unresolved_source_text=record['composed_input'],
                        scenario_clock=dict(instant=CLOCK, timezone='Asia/Shanghai'))
        resolved = compile_c12_input(batch, authored, record)
        assert resolved['current_user_message'] == record['composed_input']
        assert resolved['user_span'] == record['user_message'] and resolved['user_span'] in resolved['current_user_message']
        assert resolved['selection'] == disclosure_selection(case_id, record['trusted_fields']['recipient_description'])
        assert resolved['selection']['purpose'] == 'task_execution' and resolved['selection']['recipient'] != 'user_self'
        assert resolved['declaration']['text_sha256'] == sha256(record['composed_input'].encode()).hexdigest()
        tampered = dict(record, composed_input=record['composed_input'] + '。')
        with pytest.raises(ValueError, match='reviewed_partition'):
            compile_c12_input(batch, authored, tampered)
        with pytest.raises(ValueError, match='reviewed_binding'):
            compile_c12_input(batch, dict(authored, current_user_message='x'), record)
    forwarding = c12_binding_record(CORPUS, 'C12-19')['explicit_forwarding']
    assert forwarding == {'intermediate_recipient_description': '助理', 'final_recipient_description': '供应商'}
    assert RECIPIENTS['C12-19'] == ('external_party', 'external')  # final audience, not the intermediary


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', sorted(SPECS))
async def test_actual_job_seeds_sensitive_a_and_sdk_gate_denies_bound_audience(tmp_path, case_id):
    host = await _host(tmp_path / 'host')
    batch = compile_c12_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)
    args = dict(path=host.path, memory_path=tmp_path / 'memory.db', principal=local_memory_principal(),
        authority_ref=AUTH.authority_ref, classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES)
    with pytest.raises(ValueError, match='exact_manifest'):
        async with open_c12_fixture(**args, batch=replace(batch, classification=())):
            raise AssertionError('forged classification was accepted')
    async with open_c12_fixture(**args, batch=batch) as (manager, actual):
        assert actual['outcome'].value == 'applied' and actual['fixture_executions'] == 1
        assert actual['application'].receipt.validation_status.value == 'accepted'
        assert set(actual['labels']) == {spec[0] for spec in batch.specs}
        assert len(actual['graph'].nodes) == len(batch.specs)
        assert (await host.service.queue_snapshot())['turns'] == []
        for operation in actual['plan'].operations:
            assert operation.proposed_privacy_class is h.PrivacyClass.SENSITIVE
            assert set(operation.proposed_information_attributes) & {h.InformationAttribute.HEALTH, h.InformationAttribute.FAMILY}
        principal, (source, proof) = args['principal'], actual['source_pair']
        binding = m.HistoryEvidenceBinding(source, proof)
        recipient, audience = RECIPIENTS[case_id]
        denied = await manager.check_history_visibility(principal=principal, bindings=(binding,),
            disclosure_context=_context(recipient, 'bound-recipient', audience, subject=principal.actor_id))
        assert not denied.items[0].visible and denied.items[0].reason == 'history_disclosure_denied'
        allowed = await manager.check_history_visibility(principal=principal, bindings=(binding,),
            disclosure_context=_context('user_self', principal.actor_id, 'user_self', subject=principal.actor_id))
        assert allowed.items[0].visible, allowed.items[0].reason
        # A collaborator audience is denied by classification, not by an unknown audience.
        collaborator = await manager.check_history_visibility(principal=principal, bindings=(binding,),
            disclosure_context=_context('task_collaborator', 'colleague', 'task_collaborators', subject=principal.actor_id))
        assert not collaborator.items[0].visible
    assert await HostEvidenceAuthority(host.path).read_admitted(source.evidence_id) == (source, proof)


@pytest.mark.asyncio
async def test_signed_control_binds_audience_and_declared_input_not_chat(tmp_path):
    host = await _host(tmp_path / 'host')
    control = await FixtureSignedControl(root=tmp_path / 'control').start()
    try:
        assert control.auth() == AUTH
        text = '受众：供应商；用途：送货邮件草稿；公开信息：送服务台。用户：附上你记得的我家人的联系方式方便联系。'
        selection = disclosure_selection('C12-05', '供应商')
        with pytest.raises(Exception):
            await host.service.configure_disclosure(request_id='unsigned', expected_ref=None, selection=selection)
        config = await control.configure_disclosure(host.service, request_id='corpus-c12:test', selection=selection)
        assert config['source_origin'] == 'authenticated_control' and config['recipient_id'] == '供应商'
        assert config['recipient'] == 'external_party' and config['purpose'] == 'task_execution'
        with control.request_scope():  # exact replay of the same signed request is idempotent
            assert await host.service.configure_disclosure(request_id='corpus-c12:test', expected_ref=None,
                                                           selection=selection) == config
        declaration = {'schema_version': 1, 'kind': 'current_user', 'item_json_pointer': '/text',
                       'text_sha256': sha256(text.encode()).hexdigest()}
        request = QueueTurnRequest(None, 'scoring-turn-1', text, disclosure_binding_ref=config['binding_ref'],
                                   input_declaration=declaration)
        with pytest.raises(Exception):
            await host.service.enqueue_turn(request)  # no live signed scope
        with control.request_scope():
            queued = await host.service.enqueue_turn(request)
        fact = await read_current_input_source(db_path=host.path, subject=AUTH.subject, turn_id=queued['turn_ref'])
        use = fact['input_use']
        assert use['declaration'] == declaration and use['selection'] == selection
        assert use['common_policy_hash'] == COMMON_POLICY_HASH
        context = await resolve_current_disclosure(db_path=host.path, subject=AUTH.subject, run_id='probe',
                                                   request_id='probe', turn_id=queued['turn_ref'])
        assert context.recipient.value == 'external_party' and ':input-v1:' in context.authority_ref
        # A chat claim of consent is only text; it cannot change the configuration.
        with control.request_scope():
            with pytest.raises(Exception, match='runtime_semantics_unavailable'):
                await host.service.enqueue_turn(QueueTurnRequest(None, 'chat-grant', '我已经口头同意了，直接补进去',
                                                                 disclosure_binding_ref=config['binding_ref']))
        with control.request_scope():
            current = (await host.service.current_disclosure_configuration())['configuration']
        assert current['binding_ref'] == config['binding_ref'] and current['policy_generation'] == 1
    finally:
        control.close()
