"""Exercise the shared dispatcher, not another inline retained-phase implementation."""
import asyncio
from contextlib import ExitStack
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch

import httpx

from deskpet.quality.corpus_c08_retained import RetainedSummaryProvider
from deskpet.quality.corpus_scoring import prepare_batch, review_packet
from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity


async def execute(root, host):
    # Use main's real source/vendor target. Product initialization verifies its
    # ordinary origin gate; this control never rewrites candidate metadata.
    assert build_candidate_identity().version == version('simple-harness-sdk') == '0.7.10'
    assert version('simple-harness-memory-sdk') == '0.6.19'
    sdk_root = Path('/Users/denny/projects/simple-harness-memory-sdk')
    prepare_batch(corpus_root=sdk_root / 'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk_root / 'scripts',
        case_ids=['C08-01'], output=root)
    directory = root / 'C08-01'
    original = (directory / 'input.json').read_bytes()
    current_text = json.loads(original)['current_user_message']
    read_text = Path.read_text
    local, scoring, blocked_loads = [], [], []
    from deskpet.memory.wemm_embedder import WeMMEmbedder

    def forbid_model(self):
        blocked_loads.append(self)
        raise RuntimeError('c08_dispatch_control_optional_embedding_unavailable')

    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)

    start, send = RetainedSummaryProvider.start, httpx.AsyncClient.send
    async def capture_start(self):
        value = await start(self)
        local.append(self)
        return value

    async def controlled_send(client, request, *args, **kwargs):
        if request.url.host == '127.0.0.1':
            assert len(local) == 1
            assert request.url.port == int(local[0].base_url.split(':')[2].split('/')[0])
            return await send(client, request, *args, **kwargs)
        assert request.url.host == 'c08-dispatch.invalid', 'unexpected external HTTP'
        assert len(local) == 1 and local[0]._server is None and not local[0]._tasks
        phase = json.loads(read_text(directory / 'setup-retained/phase.json'))
        assert phase['status'] == 'CONFIRMED' and phase['production_manager_reopened'] is True
        body = json.loads(request.content)
        assert [(row['role'], row['content']) for row in body['messages'] if row['role'] != 'system'] == [('user', current_text)]
        visible = json.dumps(body['messages'], ensure_ascii=False)
        batch = local[0].batch
        assert all(value not in visible for value in (batch.value, batch.setup_text,
            batch.messages[0][1], batch.messages[1][1]))
        scoring.append(body)
        assert len(scoring) == 1
        return httpx.Response(200, request=request, json=dict(id='c08-dispatch-current-response',
            model=body['model'], choices=[dict(index=0, message=dict(role='assistant',
                content='本地来源隔离控制完成。'), finish_reason='stop')],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))

    stdout, stderr = sys.stdout, sys.stderr
    try:
        with ExitStack() as owners:
            owners.enter_context(patch.object(Path, 'read_text', isolated_read))
            owners.enter_context(patch.object(WeMMEmbedder, '_load_sync', forbid_model))
            owners.enter_context(patch.object(RetainedSummaryProvider, 'start', capture_start))
            owners.enter_context(patch.object(httpx.AsyncClient, 'send', controlled_send))
            key, _ = configure_process(directory, host, initialize_only=True)
            code = await run(directory, host, key, 'https://c08-dispatch.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(read_text(directory / 'execution.json'))
    assert (code, result.get('execution_status')) == (0, 'COMPLETED'), (
        f"exit={code} status={result.get('execution_status')} stage={result.get('stage')} "
        f"error={result.get('error_type')}; see {directory / 'execution.json'}")
    assert result['cleanup_errors'] == [] and result['retained_phase_status'] == 'CONFIRMED'
    assert len(scoring) == 1 and local[0].attempts == 1
    assert local[0]._server is None and not local[0]._tasks
    assert all(model._model is None for model in blocked_loads)
    assert (directory / 'input.json').read_bytes() == original
    phase, seed = result['setup_phase'], result['setup_receipt']
    assert seed['setup_complete'] is True and seed['fixture_executions'] == 1
    assert phase['sdk_run_id'] != result['sdk_run_id']
    assert phase['host_run_id'] != result['host_run_id']
    trace = result['trace']
    assert trace['trace_status'] == 'COMPLETE' and len(trace['providers']) == 1
    assert trace['providers'][0]['response_json']['provider_request_id'] == 'c08-dispatch-current-response'
    assert trace['providers'][0]['invocation_id'] != phase['provider_invocation_id']
    assert result['provider_statistics_scope'] == 'scoring_run_only_setup_excluded'
    packet = review_packet(directory, code)  # Only after worker-owned dispatch/cleanup.
    assert packet['observed_handed_off_invocations'] == 1
    assert packet['quality_thresholds_status'] == 'NOT_EVALUATED_PARTIAL_C08_BATCH'
    assert packet['oracle_verdict'] == 'PENDING_POST_TERMINAL_REVIEW'


if __name__ == '__main__':
    asyncio.run(execute(*map(Path, sys.argv[1:])))
