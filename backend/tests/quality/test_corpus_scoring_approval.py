"""One actual main/SDK control: exact allow to terminal, unrelated route stays open.

Only the remote Provider delegate is deterministic. No authorization/handler,
main factory, SDK receipt, or terminal is substituted. Not a model quality test.
"""
import json
import socket
import sys
from pathlib import Path

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

from deskpet.quality.corpus_scoring import prepare_batch
from deskpet.quality import corpus_runtime
from deskpet.quality.corpus_approval import CorpusApprovalBlocked
from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider


@pytest.mark.asyncio
async def test_main_exact_memory_allow_terminal_and_unrelated_route_blocked(tmp_path, monkeypatch):
    corpus = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20')
    host = Path(__file__).resolve().parents[3]
    root = prepare_batch(corpus_root=corpus,
        compiler_root=Path('/Users/denny/projects/simple-harness-memory-sdk/scripts'),
        case_ids=['C01-10'], output=tmp_path/'corpus')
    directory = root/'C01-10'
    original_read = Path.read_text
    def no_oracle(path, *args, **kwargs):
        assert path.name not in {'oracle.json', 'original-documents.json', 'case.json', '.env'}
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', no_oracle)
    def no_network(*args, **kwargs):
        raise AssertionError('network forbidden in local approval control')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    requests = []
    async def local_response(self, request, *, cancel):
        requests.append(request)
        n = len(requests)
        assert n <= 3, 'no repeat after blocked decision'
        calls = ()
        if n in (1, 3):
            args = ({'route':'memory_standalone', 'query':'待办排序约定', 'memory_types':['semantic']}
                if n == 1 else {'route':'create_new', 'title':'not authorized by benchmark'})
            calls = (ProviderToolCall(CallId(f'approval-control-{n}'), 'context_route', args),)
        return ProviderResponse(request.request_id,
            Message(MessageRole.ASSISTANT, '本地权限接线控制完成' if n == 2 else '查询'),
            tool_calls=calls, model='gpt-5.5', usage=ProviderUsage(10,10,20))
    monkeypatch.setattr(_ProductOpenAICompatibleProvider, 'invoke', local_response)
    original_execute = corpus_runtime.execute_scoring_turn
    observed = {}
    async def two_branches(**kwargs):
        first = await original_execute(**kwargs)
        approval = kwargs['approval_driver']
        assert len(requests) == 2
        assert len(approval.attempted) == 1
        allowed_id = next(iter(approval.attempted))
        import main
        # Completed public terminal and the real decision both survive before
        # beginning an independent unapproved request in the same actual stack.
        first_state = await kwargs['service'].read_primary_state(request_id='positive-terminal')
        assert first_state['current_run'] is None
        changed = dict(kwargs, text='创建另一个任务。', delivery_key='unapproved-control')
        with pytest.raises(CorpusApprovalBlocked, match='corpus_approval_not_authorized'):
            await original_execute(**changed)
        state = await kwargs['service'].read_primary_state(request_id='blocked-control')
        active = state['current_run']
        assert main._sdk_ingress.query(active['sdk_run_ref']).state.value == 'waiting'
        pending = main._sdk_ingress.list_open_authorizations(run_id=active['sdk_run_ref'])
        assert len(pending) == 1
        record = main._sdk_ingress.read_authorization_decision(
            run_id=active['sdk_run_ref'], decision_id=pending[0].decision_id)
        assert record.state.value == 'open'
        assert record.request['arguments']['route'] == 'create_new'
        assert pending[0].decision_id not in approval.attempted
        assert approval.attempted == {allowed_id} and len(requests) == 3
        # A second visit remains BLOCKED and never delivers another response.
        queued = next(t for t in (await kwargs['service'].queue_snapshot())['turns']
            if t['delivery_key'] == 'unapproved-control')
        with pytest.raises(CorpusApprovalBlocked):
            await approval(service=kwargs['service'], queued=queued)
        assert len(requests) == 3
        observed['blocked'] = True
        return first
    monkeypatch.setattr(corpus_runtime, 'execute_scoring_turn', two_branches)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        key, endpoint = configure_process(directory, host, initialize_only=True)
        code = await run(directory, host, key, endpoint)
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    result = json.loads(original_read(directory/'execution.json'))
    assert code == 0, (result.get('execution_status'), result.get('error_type'), result.get('cleanup_errors'))
    assert result['execution_status'] == 'COMPLETED' and result['cleanup_errors'] == []
    assert result['trace']['terminal_status'] == 'TERMINAL'
    assert result['trace']['provider_observation_complete'] is True
    assert len(result['trace']['providers']) == 2 and observed['blocked']
    facts = [json.loads(original_read(p)) for p in sorted(directory.glob('approval-*.json'))]
    assert [f['status'] for f in facts] == ['RESPONSE_ATTEMPTED','ALLOWED','BLOCKED','BLOCKED']
    assert facts[1]['response']['outcome'] == 'allowed'
