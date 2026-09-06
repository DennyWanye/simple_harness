"""Actual setup/preview/revision/selection Runs; controlled HTTP, no gold input."""
import asyncio
from contextlib import ExitStack
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch

import httpx

from deskpet.quality.corpus_c05_transport import TaskSetupHttpProvider
from deskpet.quality.corpus_scoring import prepare_batch, review_packet
from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity


async def execute(directory, host, case_id, mode):
    assert case_id == 'C05-18' and mode in {'revision', 'empty'}
    assert build_candidate_identity().version == version('simple-harness-sdk') == '0.7.10'
    assert version('simple-harness-memory-sdk') == '0.6.19'
    sdk = Path('/Users/denny/projects/simple-harness-memory-sdk')
    root = prepare_batch(corpus_root=sdk / 'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk / 'scripts',
        case_ids=[case_id], output=directory.parent / 'compiled')
    (root / case_id).rename(directory)
    original = (directory / 'input.json').read_bytes()
    script = (directory / 'scheduler.json').read_bytes()
    current = json.loads(original)['current_user_message']
    followup = json.loads(script)['scripted_followup'][0]['user_message']
    read_text = Path.read_text
    local, scoring, blocked = [], [], []
    stages = {}
    old = new = None
    from deskpet.memory.wemm_embedder import WeMMEmbedder

    def no_model(self):
        blocked.append(self)
        raise RuntimeError('c18_control_optional_embedding_unavailable')

    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)

    start, send = TaskSetupHttpProvider.start, httpx.AsyncClient.send
    async def capture_start(self):
        value = await start(self)
        local.append(self)
        return value

    async def controlled_send(client, request, *args, **kwargs):
        nonlocal old, new
        if request.url.host == '127.0.0.1':
            assert any(request.url.port == int(item.base_url.split(':')[2].split('/')[0]) for item in local)
            return await send(client, request, *args, **kwargs)
        assert request.url.host == 'c18-scoring.invalid'
        assert all(item._server is None and not item._tasks for item in local)
        body = json.loads(request.content)
        messages = body['messages']
        users = [row['content'] for row in messages if row['role'] == 'user']
        text = users[-1]
        assert text in {current, followup}
        ordinal = 0 if text == current else 1
        stage = stages.get(ordinal, 0)
        stages[ordinal] = stage + 1
        scoring.append(body)
        if ordinal == 0:
            assert len(local) == 1 and followup not in json.dumps(messages, ensure_ascii=False)
        else:
            assert len(local) == 2
            phase = json.loads(read_text(directory / 'setup-task-revision/phase.json'))
            assert phase['status'] == 'CONFIRMED'
            # Previous scoring conversation survives the non-prefix fixture
            # filter; neither original nor revision setup USER is injected.
            assert current in users and '旧候选预览已返回。' in [r['content'] for r in messages if r['role'] == 'assistant']
            assert not any('[执行原setup预先声明的阶段]' in value for value in users)
        calls = []
        content = '旧候选预览已返回。' if ordinal == 0 else '当前来源回读完成。'

        def tool(call_id):
            result = json.loads(next(row['content'] for row in messages
                if row['role'] == 'tool' and row['tool_call_id'] == call_id))
            return result

        def call(name, args, identity):
            return [dict(id=identity, type='function', function=dict(name=name,
                arguments=json.dumps(args, ensure_ascii=False)))]

        if ordinal == 0 and stage == 0:
            calls = call('task_scope_search', {'query': 'active' if mode == 'revision' else 'c18-no-match-541'}, 'preview')
        elif ordinal == 0:
            assert stage == 1
            result = tool('preview')
            assert result['outcome'] == 'succeeded', result
            candidates = result['value']['candidates']
            if mode == 'empty':
                assert candidates == []
            else:
                assert len(candidates) == 1
                old = candidates[0]
                assert old['scope_disclosure']['disclosure']['fields']['resume'] == '校对'
        elif stage == 0:
            # Real outdated public preview, not a made-up hash. Production
            # must reject it before granting a route, then allow a new search.
            calls = call('context_route', dict(route='resume_existing', task_scope_id=old['task_scope_id'],
                expected_source_hash=old['source_hash']), 'stale-resume')
        elif stage == 1:
            result = tool('stale-resume')
            assert result['outcome'] != 'succeeded' or 'error' in result.get('value', {}), result
            assert 'context_route_receipt' not in result.get('value', {})
            # The exact production stale cause must be observable, not just a
            # different field/permission rejection which happens to be negative.
            assert 'task_scope_source_stale' in json.dumps(result, ensure_ascii=False), result
            calls = call('task_scope_search', {'query': 'active'}, 'current-search')
        elif stage == 2:
            result = tool('current-search')
            assert result['outcome'] == 'succeeded', result
            assert len(result['value']['candidates']) == 1
            new = result['value']['candidates'][0]
            assert new['task_scope_id'] == old['task_scope_id'] and new['source_hash'] != old['source_hash']
            assert new['scope_disclosure']['disclosure']['fields']['resume'] == '待确认图片'
            calls = call('context_route', dict(route='resume_existing', task_scope_id=new['task_scope_id'],
                expected_source_hash=new['source_hash']), 'current-resume')
        else:
            assert stage == 3
            result = tool('current-resume')
            assert result['outcome'] == 'succeeded', result
            assert result['value']['context_route_receipt']['task_scope_id'] == new['task_scope_id']
            assert '待确认图片' in json.dumps(result, ensure_ascii=False)
        message = dict(role='assistant', content=content)
        if calls:
            message['tool_calls'] = calls
        return httpx.Response(200, request=request, json=dict(id=f'c18-scoring-{len(scoring)}', model=body['model'],
            choices=[dict(index=0, message=message, finish_reason='tool_calls' if calls else 'stop')],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))

    stdout, stderr = sys.stdout, sys.stderr
    try:
        with ExitStack() as owners:
            owners.enter_context(patch.object(Path, 'read_text', isolated_read))
            owners.enter_context(patch.object(WeMMEmbedder, '_load_sync', no_model))
            owners.enter_context(patch.object(TaskSetupHttpProvider, 'start', capture_start))
            owners.enter_context(patch.object(httpx.AsyncClient, 'send', controlled_send))
            key, _ = configure_process(directory, host, initialize_only=True)
            code = await run(directory, host, key, 'https://c18-scoring.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(read_text(directory / 'execution.json'))
    assert (code, result.get('execution_status')) == ((0, 'COMPLETED') if mode == 'revision' else (1, 'FOLLOWUP_UNMET')), result
    assert result['cleanup_errors'] == [] and all(item._model is None for item in blocked)
    assert (directory / 'input.json').read_bytes() == original and (directory / 'scheduler.json').read_bytes() == script
    assert all(item._server is None and not item._tasks for item in local)
    runs = result['scoring_runs']
    if mode == 'revision':
        phase = json.loads(read_text(directory / 'setup-task-revision/phase.json'))
        assert len(runs) == 2 and len(scoring) == 6
        assert phase['sdk_run_id'] not in {row['sdk_run_id'] for row in runs}
        assert phase['old_preview_terminal']['run_id'] == runs[0]['sdk_run_id']
        assert phase['original_source_hash'] == old['source_hash'] and phase['source_hash'] == new['source_hash']
        assert len(phase['exact_hidden_setup_turns']) == 2
        assert result['followup_events'][0]['fixture_action_observation']['status'] == 'CONFIRMED'
    else:
        assert len(runs) == 1 and len(scoring) == 2 and len(local) == 1
        assert not (directory / 'setup-task-revision').exists()
    packet = review_packet(directory, code)
    assert packet['observed_handed_off_invocations'] == len(scoring)
    assert packet['quality_thresholds_status'] == 'NOT_EVALUATED_PARTIAL_C05_BATCH'
    (directory / 'control.json').write_text(json.dumps(dict(mode=mode, cleanup_errors=[], scoring_http=len(scoring))))


if __name__ == '__main__':
    asyncio.run(execute(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:]))
