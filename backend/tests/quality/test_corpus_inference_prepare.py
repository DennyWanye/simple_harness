"""New prepare + separate-process recovery controls, not old setup reruns."""
import asyncio
import json
from pathlib import Path
import sys

import pytest
import simple_harness as h
import simple_harness_memory as m
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderUsage
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.quality.corpus_c02 import SETUPS as C02
from deskpet.quality.corpus_c03 import SETUPS as C03
from deskpet.quality.corpus_c03_inference import EXPECTED_OUTPUT
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.task_scope.protocol import canonical_hash
from tests.execution.test_primary_create_new_runtime import fixture
import tests.execution.test_primary_foreground_runtime as runtime_fixture


async def child(config, path):
    path.write_text(json.dumps(config, ensure_ascii=False))
    backend = str(Path(__file__).resolve().parents[2])
    installed = str(Path(m.__file__).resolve().parent.parent)
    code = ('import sys,runpy;sys.path[:0]=' + repr([backend, installed])
        + ';runpy.run_module("tests.quality.inference_prepare_worker",run_name="__main__")')
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', '-c', code, str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 45)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    path.with_suffix('.log').write_bytes(stdout)
    return process.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id,fault', [('C02-19', 'after_finalize'), ('C03-20', 'before_finalize')])
async def test_prepare_cross_process_original_proof_and_no_reextraction(tmp_path, case_id, fault):
    auth = local_owner_auth()
    text = (C02 if case_id == 'C02-19' else C03)[case_id][0]
    response = '我推测你偏好云端；尚未得到确认。' if case_id == 'C02-19' else EXPECTED_OUTPUT
    class Provider(runtime_fixture.Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, response),
                model='model', usage=ProviderUsage(10, 10, 20))
    source, _, service, _, _ = await fixture(tmp_path / 'source')
    await service.enqueue_turn(QueueTurnRequest(None, 'prepare-source', text))
    provider = Provider()
    runtime, stack, _ = await runtime_fixture.build(tmp_path / 'source', source, provider)
    try:
        await runtime.after_enqueue(subject=auth.subject)
        await runtime.drain()
        assert runtime.last_error is None and len(provider.requests) == 1
        memory = runtime.history_memory
        manager = await memory.manager()
        await manager.register_principal_owner(memory.principal(), m.MemoryScope.personal(auth.subject))
        assert await MemoryIngestionOutboxWorker(source, memory.manager,
            owner_id='prepare-source-ingestion').run_once() == 'delivered'
        runs = await PrimaryConversationAuthority(source, subject=auth.subject).completed_run_ids()
        assert len(runs) == 1
        group = await PrimaryConversationAuthority(source, subject=auth.subject).registrations_for_run(runs[0])
    finally:
        await runtime.close()
        await stack.close()
    scoring, _, _, _, _ = await fixture(tmp_path / 'scoring')
    checkpoint = tmp_path / 'recovery.json'
    config = dict(case_id=case_id, setup_text=text, scenario_time=1788660000.0,
        source_path=str(source), scoring_path=str(scoring), memory_path=str(tmp_path / 'memory.db'),
        proof_path=str(checkpoint), subject=auth.subject, host_run_id=runs[0],
        now=1788660000.0, fault=fault, output=str(tmp_path / 'first.json'))
    assert await child(config, tmp_path / 'first-config.json') == (83 if fault == 'after_finalize' else 84)
    first = json.loads((tmp_path / 'first.json').read_text())
    saved = json.loads(checkpoint.read_text())
    assert len(saved['body']['candidates']) == 1
    assert first['receipt']['validation_status'] == 'accepted'
    # A different interpreter recovers only from original DBs + JSON candidate.
    config.update(now=1788660211.0, fault='none', output=str(tmp_path / 'second.json'))
    assert await child(config, tmp_path / 'second-config.json') == 0
    second = json.loads((tmp_path / 'second.json').read_text())
    assert second['confirmed'] and second['analysis_jobs'] == 'applied'
    assert second['executions'] == 1  # First result/application reused, second job only.
    assert len(second['application_receipts']) == 2
    receipts = [h.MemoryAnalysisReceipt.from_json(r) for r in second['application_receipts']]
    assert all(r.validation_status is h.AnalysisValidationStatus.ACCEPTED for r in receipts)
    assert h.MemoryAnalysisReceipt.from_json(first['receipt']) in receipts
    assert len({r['run_id'] for r in second['requests']}) == 2
    user = next(r for r in second['requests'] if r['run_id'] == group.registrations[0].envelope.run_id)
    lineage = group.user_analysis_lineage
    assert (user['provider_id'], user['model_id'], user['model_config_hash']) == (
        lineage.provider_id, lineage.model_id, lineage.model_config_hash)
    assert len(second['graph']['nodes']) == (2 if case_id == 'C02-19' else 3)
    assert second['graph']['edges'] == []
    config.update(output=str(tmp_path / 'third.json'))
    assert await child(config, tmp_path / 'third-config.json') == 0
    third = json.loads((tmp_path / 'third.json').read_text())
    assert third['executions'] == 0 and third['outcomes'] == []
    assert third['graph'] == second['graph'] and third['receipt'] == second['receipt']
    assert len(provider.requests) == 1
    if case_id == 'C02-19':
        # Recompute the public file checksum after swapping to another actual
        # job's application: checksum correctness must not grant acceptance.
        value = json.loads(checkpoint.read_text())
        candidates = value['body']['candidates']
        candidates[0]['application'] = candidates[1]['application']
        value['body_hash'] = canonical_hash(value['body'])
        checkpoint.write_text(json.dumps(value))
        config.update(output=str(tmp_path / 'forged.json'))
        assert await child(config, tmp_path / 'forged-config.json') != 0
        assert not (tmp_path / 'forged.json').exists()
        assert 'inference_fixture_prior_application_not_accepted' in (tmp_path / 'forged-config.log').read_text()
