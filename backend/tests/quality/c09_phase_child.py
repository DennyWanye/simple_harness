"""Actual main no-recall dispatcher with pre-existing superseded history.

The HTTP response is controlled; it establishes source isolation, not quality.
"""
import asyncio
from contextlib import ExitStack
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch

import httpx

from deskpet.quality.corpus_scoring import prepare_batch, review_packet
from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity


async def execute(root, host, case_id):
    assert build_candidate_identity().version == version('simple-harness-sdk') == '0.7.10'
    assert version('simple-harness-memory-sdk') == '0.6.19'
    sdk = Path('/Users/denny/projects/simple-harness-memory-sdk')
    prepare_batch(corpus_root=sdk/'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk/'scripts',
        case_ids=[case_id], output=root)
    directory = root / case_id
    original = (directory / 'input.json').read_bytes()
    current = json.loads(original)['current_user_message']
    setup = json.loads((directory / 'setup.json').read_text())['setup_source_text']
    read_text = Path.read_text
    scoring, blocked = [], []
    from deskpet.memory.wemm_embedder import WeMMEmbedder

    def no_model(self):
        blocked.append(self)
        raise RuntimeError('c09_control_optional_embedding_unavailable')

    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)

    async def controlled_send(client, request, *args, **kwargs):
        assert request.url.host == 'c09-dispatch.invalid' and request.method == 'POST'
        body = json.loads(request.content)
        assert [(row['role'], row['content']) for row in body['messages'] if row['role'] != 'system'] == [('user', current)]
        assert setup not in json.dumps(body['messages'], ensure_ascii=False)
        scoring.append(body)
        assert len(scoring) == 1
        return httpx.Response(200, request=request, json=dict(id='c09-controlled-current',
            model=body['model'], choices=[dict(index=0, message=dict(role='assistant',
                content='本地来源隔离控制完成。'), finish_reason='stop')],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))

    stdout, stderr = sys.stdout, sys.stderr
    try:
        with ExitStack() as owners:
            owners.enter_context(patch.object(Path, 'read_text', isolated_read))
            owners.enter_context(patch.object(WeMMEmbedder, '_load_sync', no_model))
            owners.enter_context(patch.object(httpx.AsyncClient, 'send', controlled_send))
            key, _ = configure_process(directory, host, initialize_only=True)
            code = await run(directory, host, key, 'https://c09-dispatch.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(read_text(directory / 'execution.json'))
    assert (code, result.get('execution_status')) == (0, 'COMPLETED'), (
        f"exit={code} status={result.get('execution_status')} stage={result.get('stage')} "
        f"error={result.get('error_type')}; see {directory / 'execution.json'}")
    assert result['cleanup_errors'] == [] and len(scoring) == 1
    assert all(model._model is None for model in blocked)
    assert (directory / 'input.json').read_bytes() == original
    seed = result['setup_receipt']
    assert seed['outcome'] == 'applied' and seed['fixture_executions'] == 1
    old, new = seed['old_receipt'], seed['new_receipt']
    before = {op['operation_id']: op for op in old['operations']}
    after = {op['operation_id']: op for op in new['operations']}
    expected = 1 if case_id == 'C09-02' else 2
    assert len(before) == len(after) == expected
    for i in range(expected):
        prior, successor = before[f'old-{i}'], after[f'successor-{i}']
        assert prior['memory_id'] == successor['memory_id']
        assert (prior['revision'], successor['revision']) == (1, 2)
        assert seed['labels'][f'old-{i}'] == prior
        assert seed['labels'][f'successor-{i}'] == successor
    assert all(op['kind'] == ('supersede' if case_id == 'C09-02' else 'revise')
        for op in seed['plan']['operations'])
    assert result['route_audit'] == []
    trace = result['trace']
    assert trace['trace_status'] == 'COMPLETE' and len(trace['providers']) == 1
    assert trace['providers'][0]['response_json']['provider_request_id'] == 'c09-controlled-current'
    packet = review_packet(directory, code)  # Parent-side only after worker cleanup.
    assert packet['observed_handed_off_invocations'] == 1
    assert packet['predicted_types'] == []
    assert packet['quality_thresholds_status'] == 'NOT_EVALUATED_PARTIAL_C09_BATCH'
    assert packet['oracle_verdict'] == 'PENDING_POST_TERMINAL_REVIEW'


if __name__ == '__main__':
    asyncio.run(execute(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]))
