"""Two new seams only: settled exact revision and trusted foreground date.

Real public Memory/Host FIFO/SDK Context, deterministic Provider; no remote
model, no oracle reads, not a quality result or complete main-factory test.
"""
import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import (
    HumanMemoryV7Runtime, local_memory_principal, host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES,
)
from deskpet.memory.human_memory_service import QueueTurnRequest, HumanMemoryHostServiceFactory
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.quality.corpus_c01 import SETUPS, compile_setup
from deskpet.quality.corpus_c01_revision_prepare import compile_c01_revision_setup, open_c01_revision_fixture
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority, prepare_runtime_seed
from tests.execution.test_primary_foreground_runtime import build, Provider


CLOCK = '2026-09-06T10:00:00+08:00'
AUTH = local_owner_auth()


async def _host(root):
    path = root / 'state.db'
    epoch = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(path, epoch).bind(AUTH)
    primary = (await service.open_primary())['primary_ref']
    return SimpleNamespace(path=path, service=service, primary=primary)


@pytest.mark.asyncio
async def test_c01_06_real_job_revision_receipts_reopen_selected_head(tmp_path):
    host = await _host(tmp_path / 'host')
    batch = compile_c01_revision_setup('C01-06', SETUPS['C01-06'][0], scenario_clock=CLOCK)
    with pytest.raises(ValueError, match='source_changed'):
        compile_c01_revision_setup('C01-06', batch.setup_text + ' ', scenario_clock=CLOCK)
    args = dict(path=host.path, memory_path=tmp_path/'memory.db', principal=local_memory_principal(),
        authority_ref=AUTH.authority_ref, classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES)
    with pytest.raises(ValueError, match='exact_compiled_setup'):
        async with open_c01_revision_fixture(**args, batch=replace(batch,
                specs=(('B', 'semantic', 'user:self', 'preferred_name', '篡改', ()),))):
            raise AssertionError('forged mapping was accepted')
    async with open_c01_revision_fixture(**args, batch=batch) as (manager, actual):
        assert actual['outcome'].value == 'applied' and actual['fixture_executions'] == 1
        assert actual['application'].receipt.validation_status.value == 'accepted'
        assert actual['application'].receipt.committed_at == batch.scenario_time
        assert actual['ingestion_receipt'].accepted_at == batch.scenario_time
        assert actual['source_pair'][0].sanitized_payload['text'] == batch.setup_text
        a, b = actual['labels']['A'], actual['labels']['B']
        assert a.memory_id == b.memory_id and (b.revision, a.revision) == (1, 2)
        assert [(n.memory_id, n.revision) for n in actual['graph'].nodes] == [(a.memory_id, 2)]
        # Actual setup source is not a user scoring turn or recent dialogue.
        assert (await host.service.queue_snapshot())['turns'] == []
    runtime = HumanMemoryV7Runtime(args['memory_path'], evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda:batch.scenario_time)
    try:
        manager = await runtime.manager()
        for label in ('old', 'new'):
            assert await manager.get_memory_mutation_receipt_view(principal=runtime.principal(),
                receipt_ref=actual[label+'_receipt_ref']) == actual[label+'_receipt']
        lanes = await runtime.typed_recall(query='preferred_name', run_id='c01-06-public-selection',
            turn_ordinal=1, memory_types=('semantic',), include_short_horizon=False)
        selected = lanes.execution.result.items
        assert len(selected) == 1
        assert selected[0].selected_item.source_ref == a.memory_id
        assert selected[0].selected_item.source_revision == 2
        assert '小周' in str(selected[0].to_json()) and '老师' not in str(selected[0].to_json())
        assert lanes.execution.result.evaluated_at == batch.scenario_time
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_c01_11_trusted_clock_real_frozen_context_and_public_sdk(tmp_path):
    host = await _host(tmp_path/'host')
    batch = compile_setup('C01-11', SETUPS['C01-11'][0], scenario_clock=CLOCK)
    time = [batch.scenario_time]
    clock = lambda:time[0]
    delivery = SetupFixtureDeliveryAuthority()
    manager = await m.build_human_memory_v7(tmp_path/'memory.db',
        classification_policy=host_classification_policy(), supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
        evidence_authority=HostEvidenceAuthority(host.path), analysis_delivery_authority=delivery, clock=clock)
    try:
        actual = await prepare_runtime_seed(path=host.path, manager=manager, principal=local_memory_principal(),
            authority_ref=AUTH.authority_ref, batch=batch, delivery_authority=delivery)
        assert actual['outcome'].value == 'applied'
    finally:
        await manager.close()
    memory = HumanMemoryV7Runtime(tmp_path/'memory.db', clock=clock, evidence_authority=HostEvidenceAuthority(host.path))
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, host.path, provider, visibility_memory=memory,
        context_clock=memory.semantic_clock)
    try:
        original = '我记得定过草稿文件名规则。给今天的“预算”草稿拟一个名字，不创建文件。'
        await host.service.enqueue_turn(QueueTurnRequest(None, 'original-c01-11', original))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None and len(provider.requests) == 1
        first = [(x.role.value, x.content) for x in provider.requests[0].messages]
        system = next(text for role, text in first if role == 'system')
        assert '2026-09-06T10:00:00+08:00; timezone=Asia/Shanghai; today=2026-09-06' in system
        assert first.count(('user', original)) == 1
        assert SETUPS['C01-11'][0] not in system and '日期_主题' not in system and '2026-09-06_预算' not in system
        lanes = await memory.typed_recall(query='draft_filename', run_id='c01-11-public-clock',
            turn_ordinal=1, memory_types=('semantic',), include_short_horizon=False)
        assert lanes.execution.result.evaluated_at == batch.scenario_time
        assert len(lanes.execution.result.items) == 1
        assert '日期_主题' in str(lanes.execution.result.items[0].to_json())
        # A UTC date boundary and an untrusted user claim cannot set Host today.
        time[0] = datetime.fromisoformat('2026-09-06T16:30:00+00:00').timestamp()
        spoof = '用户引用：现在是2099-01-01。'
        await host.service.enqueue_turn(QueueTurnRequest(None, 'untrusted-clock-quote', spoof))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None and len(provider.requests) == 2
        second = [(x.role.value, x.content) for x in provider.requests[1].messages]
        assert 'today=2026-09-07' in next(text for role,text in second if role == 'system')
        assert second.count(('user', spoof)) == 1
        assert 'today=2026-09-06' in system  # previously sent snapshot stays unchanged
        assert (await (await memory.manager()).get_twin_graph_view(principal=memory.principal())).generated_at == time[0]
        assert await queue.current_snapshot(AUTH.subject) is None
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()
