"""One new actual-main combination; old six helper controls are not repeated."""
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sys

import httpx
import pytest

from deskpet.quality.corpus_c07 import RECENT_MESSAGES
from deskpet.quality.corpus_c07_phase import RecentMessagesProvider
from deskpet.quality.corpus_scoring import prepare_batch
from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity


@pytest.mark.asyncio
async def test_actual_main_recent_phase_then_separate_scoring_run(tmp_path, monkeypatch):
    # Use this tree's real candidate, never an older hard-coded fixture version.
    identity = build_candidate_identity()
    assert identity.version == version('simple-harness-sdk') == '0.7.10'
    assert sha256(identity.wheel_path.read_bytes()).hexdigest() == identity.wheel_sha256
    assert version('simple-harness-memory-sdk') == '0.6.19'
    sdk_root = Path('/Users/denny/projects/simple-harness-memory-sdk')
    host = Path(__file__).resolve().parents[3]
    root = prepare_batch(corpus_root=sdk_root / 'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk_root / 'scripts',
        case_ids=['C07-06'], output=tmp_path / 'corpus')
    directory = root / 'C07-06'
    original_input = (directory / 'input.json').read_bytes()
    authored = json.loads(original_input)
    assert [(row['role'], row['content']) for row in authored['recent_messages']] == list(RECENT_MESSAGES['C07-06'])
    read_text = Path.read_text
    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', isolated_read)

    from deskpet.memory.wemm_embedder import WeMMEmbedder
    blocked_loads = []
    def forbid_model(self):
        # Committed-turn ingestion may attempt optional embedding and degrade.
        # Block actual model loading; an attempted load is not a loaded model.
        blocked_loads.append(self)
        raise RuntimeError('c07_control_optional_embedding_unavailable')
    monkeypatch.setattr(WeMMEmbedder, '_load_sync', forbid_model)
    local, scoring = [], []
    start = RecentMessagesProvider.start
    async def capture_start(self):
        result = await start(self)
        local.append(self)
        return result
    monkeypatch.setattr(RecentMessagesProvider, 'start', capture_start)
    send = httpx.AsyncClient.send
    async def controlled_send(self, request, *args, **kwargs):
        if request.url.host == '127.0.0.1':
            # Real loopback HTTP -> unchanged ProductProviderAdapter/guard/SDK.
            assert len(local) == 1 and request.url.port == int(local[0].base_url.split(':')[2].split('/')[0])
            return await send(self, request, *args, **kwargs)
        if request.url.host != 'c07-scoring.invalid':
            raise AssertionError('unexpected external network')
        assert len(local) == 1 and local[0]._server is None
        phase = json.loads(read_text(directory / 'setup-recent/phase.json'))
        assert phase['status'] == 'CONFIRMED'
        body = json.loads(request.content)
        expected = [*RECENT_MESSAGES['C07-06'], ('user', authored['current_user_message'])]
        assert [(row['role'], row['content']) for row in body['messages'] if row['role'] != 'system'] == expected
        assert '我过去习惯把文具收进木抽屉。' not in repr(body['messages'])
        scoring.append(body)
        return httpx.Response(200, request=request, json=dict(id='c07-mock-scoring-response',
            choices=[dict(index=0, message=dict(role='assistant', content='fixture current response'), finish_reason='stop')],
            model=body['model'], usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))
    monkeypatch.setattr(httpx.AsyncClient, 'send', controlled_send)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        key, _ = configure_process(directory, host, initialize_only=True)  # no real .env or secret
        code = await run(directory, host, key, 'https://c07-scoring.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(read_text(directory / 'execution.json'))
    assert code == 0, result
    assert result['execution_status'] == 'COMPLETED' and result['cleanup_errors'] == []
    assert all(embedder._model is None for embedder in blocked_loads)
    assert len(scoring) == 1
    assert len(local) == 1 and local[0]._server is None and not local[0]._tasks
    assert (directory / 'input.json').read_bytes() == original_input
    phase = result['setup_phase']
    assert phase['status'] == 'CONFIRMED' and phase['real_model_calls'] == 0
    assert phase['fixture_http_requests'] == phase['sdk_provider_handoffs'] == 1
    assert phase['sdk_run_id'] != result['sdk_run_id']
    assert phase['host_run_id'] != result['host_run_id']
    assert result['provider_statistics_scope'] == 'scoring_run_only_setup_excluded'
    assert result['trace']['trace_status'] == 'COMPLETE'
    assert len(result['trace']['providers']) == 1
    scoring_invocation = result['trace']['providers'][0]
    assert scoring_invocation['invocation_id'] != phase['provider_invocation_id']
    assert scoring_invocation['response_json']['provider_request_id'] == 'c07-mock-scoring-response'
    import main
    assert main._provider_registry.get_chain()[0]['id'] == 'corpus-real-provider'
