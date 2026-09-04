"""「响应不可用」必须与「模型主动判定无可记」区分开。

实测故障（.local-test-evidence/real-ui-channel/20260904T131633）：
analysis 响应 `output_tokens=2048` 正好顶满 `max_output_tokens=2048` → 模型发不出
完整的 `memory_analysis_proposal` 工具调用 → `proposal_from_response()` 返 None →
旧代码把它压成 `analysis_model_declined`（本该表示"模型看过内容、主动认为无可记"）。
结果：attempt 记 `succeeded`、batch 记 `applied`、记忆零物化、无死信无重试无告警，
事后**完全无法**与"确实无可记"区分——该轮因此被误判为 INCONCLUSIVE 而非缺陷。

本用例调用**生产函数** `compile_proposal`，不复刻其控制流。
"""

from __future__ import annotations

import pytest

from deskpet.memory.analysis_proposal import compile_proposal


class _Req:
    ordered_evidence_refs: tuple = ()
    idempotency_key = "k"
    request_hash = "h"
    job_id = "j"
    run_id = "r"


def _compile(proposal):
    return compile_proposal(
        proposal, request=_Req(), items=(), base_revision=1, plan_id="p", now=0.0
    )


def _reason(compiled) -> str:
    return str(compiled.structured_result.get("closure_reason"))


def test_unparseable_response_is_not_reported_as_a_model_decline() -> None:
    """None = 拿不到合规 proposal（截断/不可解析），不是模型的判断。"""
    compiled = _compile(None)
    assert compiled.outcome == "no_mutation"
    assert _reason(compiled) == "analysis_response_unusable"
    assert _reason(compiled) != "analysis_model_declined"


@pytest.mark.parametrize("bad", ["", "not-a-mapping", 42, [], ()], ids=str)
def test_any_non_mapping_proposal_takes_the_unusable_branch(bad) -> None:
    assert _reason(_compile(bad)) == "analysis_response_unusable"


def test_genuine_model_decline_keeps_its_own_reason() -> None:
    """模型给了合规 proposal 且自述无可记 —— 这条是合法收敛，理由码必须不同。"""
    compiled = _compile({"outcome": "no_mutation", "closure_reason": "nothing_worth_storing"})
    assert compiled.outcome == "no_mutation"
    assert _reason(compiled) == "nothing_worth_storing"


def test_empty_operations_defaults_to_model_no_change_not_unusable() -> None:
    """给了 proposal 但操作为空 —— 仍是模型的判断，不该混进"响应不可用"。"""
    compiled = _compile({"outcome": "mutate", "operations": []})
    assert _reason(compiled) == "model_no_change"


def test_the_two_causes_are_distinguishable_from_the_result_alone() -> None:
    """账本只留 structured_result；两种成因必须仅凭它就能分开。"""
    unusable = _reason(_compile(None))
    declined = _reason(_compile({"outcome": "no_mutation", "closure_reason": "x"}))
    empty = _reason(_compile({"outcome": "mutate", "operations": []}))
    assert len({unusable, declined, empty}) == 3
