"""Actual main/HTTP/public decisions. Controlled responses are not model quality.

The fixed search uses public status words, never a setup label or gold ID. It
selects the first actual disclosed candidate on the final authored turn even if
that is semantically wrong. This tests dispatch/authority, not correct selection.
"""
import asyncio
from contextlib import ExitStack
from hashlib import sha256
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
    identity = build_candidate_identity()
    assert identity.version == version('simple-harness-sdk') == '0.7.10'
    assert version('simple-harness-memory-sdk') == '0.6.19'
    assert sha256(identity.wheel_path.read_bytes()).hexdigest() == identity.wheel_sha256
    sdk_root = Path('/Users/denny/projects/simple-harness-memory-sdk')
    root = prepare_batch(corpus_root=sdk_root / 'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk_root / 'scripts',
        case_ids=[case_id], output=directory.parent / 'compiled')
    (root / case_id).rename(directory)
    authored_bytes = (directory / 'input.json').read_bytes()
    schedule_bytes = (directory / 'scheduler.json').read_bytes()
    authored = json.loads(authored_bytes)
    schedule = json.loads(schedule_bytes)['scripted_followup']
    texts = [authored['current_user_message'], *(row['user_message'] for row in schedule)]
    read_text = Path.read_text
    local, requests, selections, actual_candidates, blocked_models = [], [], [], [], []
    stages = {}
    source_sha = None
    from deskpet.memory.wemm_embedder import WeMMEmbedder

    def forbid_model(self):
        blocked_models.append(self)
        raise RuntimeError('c05_control_optional_embedding_unavailable')

    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)

    start, send = TaskSetupHttpProvider.start, httpx.AsyncClient.send
    async def capture_start(self):
        value = await start(self)
        local.append(self)
        return value

    async def controlled_send(self, request, *args, **kwargs):
        nonlocal source_sha
        if request.url.host == '127.0.0.1':
            assert len(local) == 1
            assert request.url.port == int(local[0].base_url.split(':')[2].split('/')[0])
            return await send(self, request, *args, **kwargs)
        assert request.url.host == 'c05-scoring.invalid', 'unexpected external HTTP'
        assert local[0]._server is None and not local[0]._tasks
        phase = json.loads(read_text(directory / 'setup-tasks/phase.json'))
        assert phase['status'] == 'CONFIRMED'
        body = json.loads(request.content)
        messages = body['messages']
        current = [row['content'] for row in messages if row['role'] == 'user'][-1]
        assert current in texts
        ordinal = texts.index(current)
        if not requests:
            assert [(row['role'], row['content']) for row in messages if row['role'] != 'system'] == [('user', texts[0])]
        requests.append(dict(ordinal=ordinal, body=body))
        # Future authored messages must not be injected early into any message.
        for future in texts[ordinal + 1:]:
            assert future not in json.dumps(messages, ensure_ascii=False)
        stage = stages.get(ordinal, 0)
        stages[ordinal] = stage + 1
        calls = []
        content = '受控响应。'
        if ordinal == 0 and stage == 0:
            name, args, call_id = 'task_scope_search', {'query': 'active paused' if mode == 'visible'
                else 'c05-absent-9bb731d8'}, 'preview-call'
            calls = [dict(id=call_id, type='function', function=dict(name=name, arguments=json.dumps(args)))]
        elif ordinal == 0:
            assert stage == 1
            tool = next(row for row in messages if row['role'] == 'tool' and row['tool_call_id'] == 'preview-call')
            result = json.loads(tool['content'])
            assert result['outcome'] == 'succeeded', result
            actual_candidates.extend(result['value']['candidates'])
            if mode == 'visible':
                assert actual_candidates
                assert all(item['scope_disclosure']['disclosure']['fields'] for item in actual_candidates)
                # Inspect only actual public output; no fixture labels/IDs.
                source_sha = actual_candidates[0]['source_hash']
            else:
                assert actual_candidates == []
        elif ordinal < len(texts) - 1:
            assert stage == 0  # C05-20 f1 must remain preview-only.
        elif stage == 0:
            selected = actual_candidates[0]
            assert selected['task_scope_id'] in json.dumps(messages, ensure_ascii=False)
            selections.append(dict(task_scope_id=selected['task_scope_id'], source_hash=source_sha))
            args = dict(route='resume_existing', task_scope_id=selected['task_scope_id'], expected_source_hash=source_sha)
            calls = [dict(id='resume-call', type='function', function=dict(name='context_route',
                arguments=json.dumps(args)))]
        else:
            assert stage == 1
            tool = next(row for row in messages if row['role'] == 'tool' and row['tool_call_id'] == 'resume-call')
            result = json.loads(tool['content'])
            assert result['outcome'] == 'succeeded', result
            assert result['value']['context_route_receipt']['task_scope_id'] == selections[-1]['task_scope_id']
            assert result['value']['context_route_receipt']['route'] == 'resume_existing'
        offered = {row['function']['name'] for row in body['tools']}
        assert all(call['function']['name'] in offered for call in calls)
        message = dict(role='assistant', content=content)
        if calls:
            message['tool_calls'] = calls
        return httpx.Response(200, request=request, json=dict(id=f'c05-scoring-{len(requests)}',
            model=body['model'], choices=[dict(index=0, message=message, finish_reason='tool_calls' if calls else 'stop')],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))

    stdout, stderr = sys.stdout, sys.stderr
    try:
        with ExitStack() as owners:
            owners.enter_context(patch.object(Path, 'read_text', isolated_read))
            owners.enter_context(patch.object(WeMMEmbedder, '_load_sync', forbid_model))
            owners.enter_context(patch.object(TaskSetupHttpProvider, 'start', capture_start))
            owners.enter_context(patch.object(httpx.AsyncClient, 'send', controlled_send))
            key, _ = configure_process(directory, host, initialize_only=True)
            code = await run(directory, host, key, 'https://c05-scoring.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(read_text(directory / 'execution.json'))
    expected_code, expected_status = ((0, 'COMPLETED') if mode == 'visible' else (1, 'FOLLOWUP_UNMET'))
    assert (code, result.get('execution_status')) == (expected_code, expected_status), (
        f"C05 child exit={code} status={result.get('execution_status')} "
        f"stage={result.get('stage')} error={result.get('error_type')}; "
        f"see {directory / 'execution.json'} and original setup trace")
    assert result['cleanup_errors'] == []
    assert all(model._model is None for model in blocked_models)
    assert len(local) == 1 and local[0]._server is None and not local[0]._tasks
    assert (directory / 'input.json').read_bytes() == authored_bytes
    assert (directory / 'scheduler.json').read_bytes() == schedule_bytes
    phase = result['setup_phase']
    assert phase['status'] == 'CONFIRMED' and phase['setup_archive_count'] == 2
    setup_runs = {row['sdk_run_id'] for row in phase['runs']}
    scoring_runs = result['scoring_runs']
    assert setup_runs.isdisjoint(row['sdk_run_id'] for row in scoring_runs)
    assert len({row['sdk_run_id'] for row in scoring_runs}) == len(scoring_runs)
    assert all(row['trace']['trace_status'] == 'COMPLETE' for row in scoring_runs)
    assert all(event['no_formal_scope_authority'] for event in result['followup_events'])
    assert sum(len(row['trace']['providers']) for row in scoring_runs) == len(requests)
    # Controlled frontend responses are not a gold-selected result. All final
    # model semantics still need the original post-execution review.
    if mode == 'visible':
        assert code == 0 and result['execution_status'] == 'COMPLETED', result
        assert len(scoring_runs) == len(texts) and len(selections) == 1
        assert result['scripted_followups_executed'] == len(schedule)
        assert all(event['status'] == 'SATISFIED' for event in result['followup_events'])
    else:
        assert code == 1 and result['execution_status'] == 'FOLLOWUP_UNMET', result
        assert len(scoring_runs) == 1 and len(requests) == 2 and selections == []
        assert result['unmet_followup'] == 'f1'
    packet = review_packet(directory, code)  # Only after worker dispatch/cleanup.
    assert packet['observed_handed_off_invocations'] == len(requests)
    assert packet['quality_thresholds_status'] == 'NOT_EVALUATED_PARTIAL_C05_BATCH'
    (directory / 'control.json').write_text(json.dumps(dict(mode=mode,
        cleanup_errors=result['cleanup_errors'], scoring_http_requests=len(requests),
        fixture_http_requests=local[0].attempts, model_loaded=False), ensure_ascii=False))


if __name__ == '__main__':
    directory, host = map(Path, sys.argv[1:3])
    asyncio.run(execute(directory, host, *sys.argv[3:]))
