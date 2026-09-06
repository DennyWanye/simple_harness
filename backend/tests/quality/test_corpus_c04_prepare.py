"""First C04 setup controls only; no Provider/model/quality execution."""
from datetime import datetime, timezone

import pytest
import simple_harness_memory as m
from deskpet.quality.corpus_c04 import SETUPS, SCENARIO_CLOCKS, compile_c04_setup, temporal_payload
from deskpet.quality.corpus_c04_prepare import open_c04_fixture
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from tests.memory.test_primary_read_api import setup, AUTH
from tests.memory.test_primary_visibility import classification_policy, FILTERS


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', [key for key in SETUPS if key not in {'C04-12', 'C04-17'}])
async def test_c04_actual_public_time_setup(tmp_path, monkeypatch, case_id):
    host = await setup(tmp_path / 'host')
    batch = compile_c04_setup(case_id, SETUPS[case_id][0], scenario_clock=SCENARIO_CLOCKS[case_id])
    principal = m.MemoryPrincipal('host', 'household', AUTH.subject, 'corpus-fixture')
    signals = []
    async def forbidden_signal(self, **kwargs):
        signals.append(kwargs)
        raise AssertionError('setup must not claim an external event or time signal')
    monkeypatch.setattr(m.MemoryManager, 'apply_prospective_signal', forbidden_signal)
    async with open_c04_fixture(path=host.path, memory_path=tmp_path / 'memory.db',
            principal=principal, authority_ref=AUTH.authority_ref, batch=batch,
            classification_policy=classification_policy(), supported_filter_policies=FILTERS) as (manager, actual):
        assert actual['fixture_executions'] == 1
        assert actual['application'].receipt.validation_status.value == 'accepted'
        assert actual['source_pair'][0].sanitized_payload['text'] == SETUPS[case_id][0]
        assert actual['source_pair'][1].admitted_at == batch.ingestion_time
        assert actual['ingestion_receipt'].accepted_at == batch.ingestion_time
        assert actual['application'].receipt.committed_at == batch.ingestion_time
        assert actual['clock']() == batch.scenario_time
        assert actual['graph'].generated_at == batch.scenario_time
        assert len(actual['labels']) == len(batch.specs)
        assert await PrimaryConversationAuthority(host.path, subject=AUTH.subject).completed_run_ids() == ()
        assert signals == []
        payloads = {op.operation_id: op.payload for op in actual['plan'].operations}
        if case_id in {'C04-10', 'C04-11'}:
            assert payloads['P'].trigger.to_json()['trigger_kind'] == 'event'
            assert payloads['P'].trigger.event_authority_ref.startswith('corpus:unobserved-event:')
            assert not actual['event_publisher_bound']
            assert next(op for op in actual['plan'].operations if op.operation_id == 'P').lifecycle_state.value == 'pending'
        if case_id == 'C04-07':
            assert payloads['E'].occurred_start == datetime(2026, 8, 20, 4, tzinfo=timezone.utc).timestamp()
            assert batch.ingestion_time == datetime(2026, 9, 5, 4, tzinfo=timezone.utc).timestamp()
            assert payloads['E'].occurred_start < batch.ingestion_time < batch.scenario_time
        if case_id == 'C04-15':
            assert payloads['E'].occurred_start == datetime(2026, 9, 30, 8, tzinfo=timezone.utc).timestamp()
            assert batch.ingestion_time == datetime(2026, 9, 30, 9, tzinfo=timezone.utc).timestamp()
            assert batch.scenario_time == datetime(2026, 9, 30, 10, tzinfo=timezone.utc).timestamp()
        if case_id == 'C04-06':
            assert payloads['P'].trigger.timezone == 'Europe/London'
            assert payloads['P'].trigger.to_json()['trigger_at'] == datetime(2026, 9, 8, 8, tzinfo=timezone.utc).timestamp()
        if case_id == 'C04-20':
            assert payloads['P'].trigger.to_json()['timezone'] == 'Asia/Shanghai'
            assert '2026-09-07T16:30' not in actual['source_pair'][0].sanitized_payload['text']
        for spec in batch.specs:
            if spec[5] not in {'minute', 'event'}:
                assert '非原文事实或评分答案' in str(temporal_payload(batch, spec).to_json())
