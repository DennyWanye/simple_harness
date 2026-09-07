"""Actual main revision setup -> approved SDK route -> next physical input.

HTTP responses are deterministic controls, not real model quality evidence.
No source/permission/handler/terminal substitute; no original gold is read.
"""
import json
from pathlib import Path
import sys

import httpx
import pytest

from deskpet.quality.corpus_scoring import prepare_batch
from deskpet.quality.corpus_scoring_session import configure_process, run


def mappings(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from mappings(child)


@pytest.mark.asyncio
async def test_actual_main_revision_route_supplies_new_head_to_next_request(tmp_path, monkeypatch):
    host = Path(__file__).resolve().parents[3]
    sdk = Path('/Users/denny/projects/simple-harness-memory-sdk')
    root = prepare_batch(corpus_root=sdk/'plans/2026-08-29-human-memory-digital-twin/quality/'
        'recall-corpus-candidate/review-zh/successor-12x20', compiler_root=sdk/'scripts',
        case_ids=['C01-06'], output=tmp_path/'corpus')
    directory = root/'C01-06'
    input_bytes = (directory/'input.json').read_bytes()
    authored = json.loads(input_bytes)
    read_text = Path.read_text
    def isolated_read(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'case.json', 'original-documents.json'}
        assert str(path) != '/Users/denny/projects/simple_harness/.env'
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', isolated_read)
    from deskpet.memory.wemm_embedder import WeMMEmbedder
    blocked_loads = []
    def block_model(self):
        blocked_loads.append(self)
        raise RuntimeError('c01_route_control_optional_embedding_unavailable')
    monkeypatch.setattr(WeMMEmbedder, '_load_sync', block_model)
    sent, returned_fragments = [], []
    async def controlled_send(self, request, *args, **kwargs):
        assert request.url.host == 'c01-route.invalid' and request.method == 'POST'
        body = json.loads(request.content)
        sent.append(body)
        assert len(sent) <= 2, 'unexpected third provider invocation'
        if len(sent) == 1:
            non_system = [(m['role'], m['content']) for m in body['messages'] if m['role'] != 'system']
            assert non_system == [('user', authored['current_user_message'])]
            schema = next(t['function']['parameters'] for t in body['tools']
                if t['function']['name'] == 'context_route')
            assert schema['properties']['reuse_workspace_of']['type'] == ['string', 'null']
            message = dict(role='assistant', content=None, tool_calls=[dict(id='c01-route-call',
                type='function', function=dict(name='context_route', arguments=json.dumps(dict(
                    route='memory_standalone', query='preferred_name', memory_types=['semantic'],
                    include_short_horizon=False, reuse_workspace_of=None, expected_source_hash=None))))])
            finish = 'tool_calls'
        else:
            for message in body['messages']:
                if message['role'] == 'tool':
                    for obj in mappings(json.loads(message['content'])):
                        if 'fragments' in obj:
                            returned_fragments.extend(obj['fragments'])
            assert len(returned_fragments) == 1, 'actual route did not supply one qualified result'
            message = dict(role='assistant', content='fixture route completed')
            finish = 'stop'
        return httpx.Response(200, request=request, json=dict(id=f'c01-local-{len(sent)}',
            model=body['model'], choices=[dict(index=0, message=message, finish_reason=finish)],
            usage=dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)))
    monkeypatch.setattr(httpx.AsyncClient, 'send', controlled_send)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        key, _ = configure_process(directory, host, initialize_only=True)
        code = await run(directory, host, key, 'https://c01-route.invalid/v1')
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    outcome = json.loads(read_text(directory/'execution.json'))
    assert code == 0, outcome
    assert outcome['execution_status'] == 'COMPLETED' and outcome['cleanup_errors'] == []
    assert all(instance._model is None for instance in blocked_loads)
    assert len(sent) == 2 and (directory/'input.json').read_bytes() == input_bytes
    assert len(outcome['route_audit']) == 1
    route = outcome['route_audit'][0]
    assert route['verdict'] == 'accepted'
    fragment, = route['detail']['typed_carrier']['fragments']
    a, b = outcome['setup_receipt']['labels']['A'], outcome['setup_receipt']['labels']['B']
    assert a['memory_id'] == b['memory_id'] and (a['revision'], b['revision']) == (2, 1)
    assert (fragment['source_ref'], fragment['source_revision']) == (a['memory_id'], 2)
    actual, = returned_fragments
    assert actual['ref'] == fragment['recall_binding']['item_id']
    assert actual['payload'] == fragment['public_payload']
    assert actual['payload_hash'] == fragment['public_payload_hash']
    assert actual['payload']['object_value'] == '小周'
    providers = outcome['trace']['providers']
    assert len(providers) == 2 and all(p['state'] == 'succeeded' for p in providers)
    assert outcome['trace']['trace_status'] == 'COMPLETE'
