"""Actual main C12 session: bound non-self audience -> policy persona -> SDK gate.

HTTP responses are deterministic controls, not model quality evidence. The
real main factory, Host disclosure configuration, declared USER item, SDK
typed recall gate and terminal are exercised; no gold is read.
"""
import json
import os
from pathlib import Path
import sys

import httpx
import pytest

from deskpet.memory.current_input_source import COMMON_POLICY_TEXT
from deskpet.quality.corpus_scoring import prepare_batch, review_packet
from deskpet.quality.corpus_scoring_session import configure_process, run
from tests.quality.test_corpus_c01_main_route import mappings

CANDIDATES = (os.environ.get('CORPUS_MEMORY_SDK_ROOT'), '/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk',
              '/Users/denny/projects/simple-harness-memory-sdk')
SDK = next((Path(c) for c in CANDIDATES if c and Path(c).is_dir()), None)


@pytest.mark.skipif(SDK is None, reason='memory SDK corpus checkout not found')
@pytest.mark.asyncio
async def test_actual_main_c12_bound_audience_denies_sensitive_recall(tmp_path, monkeypatch):
    host = Path(__file__).resolve().parents[3]
    evidence = host / '.local-test-evidence' / 'pytest-c12-main-route' / tmp_path.name
    root = prepare_batch(corpus_root=SDK / 'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=SDK / 'scripts',
        case_ids=['C12-05'], output=evidence)
    directory = root / 'C12-05'
    authored = json.loads((directory / 'input.json').read_text())
    binding = json.loads((directory / 'trusted-binding.json').read_text())
    read_text = Path.read_text
    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', isolated_read)
    from deskpet.memory.wemm_embedder import WeMMEmbedder
    class FixtureModel:
        """Deterministic 2048-d unit vectors; no local model weights are loaded."""
        def get_embedding_dimension(self):
            return 2048
        def encode(self, texts, normalize_embeddings=True):
            import hashlib, math
            vectors = []
            for text in texts:
                seed = hashlib.sha256(text.encode()).digest()
                raw = [((seed[i % 32] * 31 + i) % 97) - 48.0 for i in range(2048)]
                norm = math.sqrt(sum(v * v for v in raw)) or 1.0
                vectors.append([v / norm for v in raw])
            return vectors
    loaded = []
    def fixture_load(self):
        with self._physical_lock:
            self._model = FixtureModel()
            loaded.append(self)
            self._set_state('ready')
    monkeypatch.setattr(WeMMEmbedder, '_load_sync', fixture_load)
    sent, fragments = [], []
    async def controlled_send(self, request, *args, **kwargs):
        assert request.url.host == 'c12-route.invalid' and request.method == 'POST'
        body = json.loads(request.content)
        sent.append(body)
        assert len(sent) <= 2, 'unexpected third provider invocation'
        if len(sent) == 1:
            system = [m['content'] for m in body['messages'] if m['role'] == 'system']
            assert len(system) == 1 and COMMON_POLICY_TEXT in system[0]
            assert 'user_self' not in system[0]  # no per-case allow/deny hint beyond the common policy
            non_system = [(m['role'], m['content']) for m in body['messages'] if m['role'] != 'system']
            assert non_system == [('user', authored['unresolved_source_text'])]
            assert binding['user_message'] in non_system[0][1]
            message = dict(role='assistant', content=None, tool_calls=[dict(id='c12-route-call',
                type='function', function=dict(name='context_route', arguments=json.dumps(dict(
                    route='memory_standalone', query='家人联络号码 family_contact_number', memory_types=['semantic'],
                    include_short_horizon=False, reuse_workspace_of=None, expected_source_hash=None))))])
            finish = 'tool_calls'
        else:
            for message in body['messages']:
                if message['role'] == 'tool':
                    for obj in mappings(json.loads(message['content'])):
                        if 'fragments' in obj:
                            fragments.extend(obj['fragments'])
            message = dict(role='assistant', content='送货邮件草稿：请送至服务台。该受众不使用家庭联系方式。')
            finish = 'stop'
        return httpx.Response(200, request=request, json=dict(id=f'c12-local-{len(sent)}',
            model=body['model'], choices=[dict(index=0, message=message, finish_reason=finish)],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))
    monkeypatch.setattr(httpx.AsyncClient, 'send', controlled_send)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        key, _ = configure_process(directory, host, initialize_only=True)
        code = await run(directory, host, key, 'https://c12-route.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    outcome = json.loads(read_text(directory / 'execution.json'))
    assert code == 0, outcome
    assert outcome['execution_status'] == 'COMPLETED' and outcome['cleanup_errors'] == []
    assert len(sent) == 2 and loaded
    disclosure = outcome['disclosure_binding']
    assert disclosure['configuration']['recipient'] == 'external_party'
    assert disclosure['configuration']['recipient_id'] == '供应商'
    assert disclosure['configuration']['source_origin'] == 'authenticated_control'
    assert disclosure['selection']['purpose'] == 'task_execution'
    assert outcome['current_input_fact']['input_use']['declaration']['kind'] == 'current_user'
    assert set(outcome['setup_receipt']['labels']) == {'A'}
    assert outcome['setup_receipt']['classification'][0][1] == 'sensitive'
    # The bound non-self audience: the real SDK gate returns no sensitive A fragment.
    assert fragments == [], fragments
    for text in (json.dumps(sent[1], ensure_ascii=False),):
        assert '测试分机216' not in text
    providers = outcome['trace']['providers']
    assert len(providers) == 2 and all(p['state'] == 'succeeded' for p in providers)
    assert outcome['trace']['trace_status'] == 'COMPLETE'
    monkeypatch.setattr(Path, 'read_text', read_text)  # parent-side review opens the oracle only after exit
    packet = review_packet(directory, code)
    assert packet['oracle_verdict'] == 'PENDING_POST_TERMINAL_REVIEW'
    assert packet['quality_thresholds_status'] == 'NOT_EVALUATED_PARTIAL_C12_BATCH'
    assert packet['recipient_binding']['configuration']['recipient'] == 'external_party'
