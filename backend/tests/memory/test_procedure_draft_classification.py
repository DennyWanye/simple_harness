"""Prompt routing and source-bound scripted classifications, not model accuracy."""
from copy import deepcopy
from dataclasses import replace

import pytest
from simple_harness import thaw_json
from deskpet.memory import analysis_protocol
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory import analysis_proposal_v5_1 as current
from tests.memory.test_procedure_adoption import compilation


def compile_case(case, proposal=None):
    request = replace(case.request, prompt_version=current.PROMPT_VERSION,
        result_schema_version=current.RESULT_SCHEMA_VERSION, policy_version=current.POLICY_VERSION)
    return current.compile_proposal(case.proposal if proposal is None else proposal,
        request=request, items=case.items, base_revision=1, plan_id='candidate-workflow', now=1)


@pytest.mark.parametrize('text', [
    '我在考虑一个还没决定采用的可复用工作流程：先列清单，再复制到备份目录。请先记下来，暂不执行。',
    '这是我准备试用的工作流程：先列清单，再复制到备份目录，但我还没决定采用。',
])
def test_tentative_user_workflow_stays_draft_with_original_source(text):
    case = compilation(text, intent='uncertain')
    result = compile_case(case)
    assert not result.rejected
    operation = result.plan.operations[0]
    assert operation.lifecycle_state.value == 'draft'
    assert tuple(operation.payload.steps) == ('先列清单', '再复制到备份目录')
    # Dropping the undecided/no-execution context cannot pass the source gate.
    bad = deepcopy(case.proposal)
    bad['operations'][0]['exact_quote'] = '先列清单，再复制到备份目录'
    assert compile_case(case, bad).plan is None


def test_ordinary_task_episode_is_not_automatically_promoted():
    case = compilation('请今天先列清单，再复制到备份目录。', intent='uncertain')
    raw = deepcopy(case.proposal)
    operation = raw['operations'][0]
    operation.pop('procedure')
    operation['memory_type'] = 'episode'
    operation['episode'] = {'title': '本次整理请求', 'actions': ['先列清单', '再复制到备份目录'],
                            'results': []}
    result = compile_case(case, raw)
    assert not result.rejected
    assert all(op.memory_type.value == 'episode' for op in result.plan.operations)


def test_prompt_successor_keeps_old_request_and_wire_exact():
    case = compilation()
    old = replace(case.request, prompt_version=v5.PROMPT_VERSION,
        result_schema_version=v5.RESULT_SCHEMA_VERSION, policy_version=v5.POLICY_VERSION)
    new = replace(old, prompt_version=current.PROMPT_VERSION)
    assert old.request_hash != new.request_hash
    assert analysis_protocol.protocol_for_request(old) is v5
    assert analysis_protocol.protocol_for_request(new) is current
    assert thaw_json(current.proposal_tool_spec().parameters) == thaw_json(v5.proposal_tool_spec().parameters)
    assert 'procedure = a reusable how-to the user follows;' not in current.PROPOSAL_TOOL_DESCRIPTION
    assert 'procedure = a reusable how-to the user follows;' in v5.PROPOSAL_TOOL_DESCRIPTION
    for prompt in (current.ANALYSIS_SYSTEM_INSTRUCTION, current.PROPOSAL_TOOL_DESCRIPTION):
        assert '普通一次性任务' in prompt and '助手自己的执行计划' in prompt
        assert 'DRAFT' in prompt and 'adoption_quote' in prompt


@pytest.mark.asyncio
async def test_persisted_v5_response_recovers_under_v5_1_without_provider(tmp_path, monkeypatch):
    from tests.memory.test_procedure_adoption import (
        test_public_materialization_and_response_only_reopen_keep_persisted_protocol as replay,
    )
    await replay(tmp_path, monkeypatch, version=5, recover=True, recovery_version="5.1")
