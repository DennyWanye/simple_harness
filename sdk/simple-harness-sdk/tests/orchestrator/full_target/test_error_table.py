# SPDX-License-Identifier: Apache-2.0
"""错误码表（TaskGraph 原计划 §12；HTN 补齐阶段 A 第 4 条）：枚举全集、未知码拒绝、先后顺序。"""
from __future__ import annotations

import pytest

from agent_orchestrator.contracts.error_table import (
    PLANNING_ERRORS,
    SHARING_PLANNER_CODE,
    TASKGRAPH_ERRORS,
    ErrorCategory,
    SharingRefusalCode,
    SharingRefused,
    TaskGraphBoundaryCode,
    classify,
    ordered,
)
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionRejectionCode as P
from agent_orchestrator.planning.decision_feedback import feedback_from_decision


def test_every_cross_boundary_code_is_registered_exactly_once():
    """**Mutation**: drop any row from either table → red."""
    assert set(PLANNING_ERRORS) == set(P)
    assert set(TASKGRAPH_ERRORS) == set(TaskGraphBoundaryCode)
    assert not {c.value for c in P} & {c.value for c in TaskGraphBoundaryCode}
    for code in (*P, *TaskGraphBoundaryCode):
        assert classify(code) is classify(code.value)


@pytest.mark.parametrize("code", ["NO_SUCH_CODE", "", None, 7])
def test_an_unregistered_code_is_refused(code):
    with pytest.raises(ContractError, match="ERROR_CODE_UNREGISTERED"):
        classify(code)


def test_codes_are_reported_in_category_order_stable_within_a_category():
    """**Mutation**: return the discovery order from ``ordered`` → red."""
    found = [P.BUDGET_INSUFFICIENT, P.DATA_UNBOUND, P.PARAMETER_INVALID, P.ORDER_CYCLE,
             P.REQUEST_BINDING_STALE, P.DATA_UNBOUND, P.UNKNOWN_FIELD]
    assert ordered(found) == (P.UNKNOWN_FIELD, P.REQUEST_BINDING_STALE, P.PARAMETER_INVALID,
                              P.DATA_UNBOUND, P.ORDER_CYCLE, P.BUDGET_INSUFFICIENT)
    assert [classify(c).category for c in ordered(found)] == sorted(classify(c).category for c in ordered(found))
    assert list(ErrorCategory) == sorted(ErrorCategory)


def test_a_stored_refusal_with_an_unknown_code_is_refused_not_relabelled():
    """**Mutation**: map an unknown stored code back to ``MALFORMED_DECISION`` → red."""
    budgets = {"same_request_format_retries_remaining": 0, "planning_rounds_remaining": 1,
               "root_review_repairs_remaining": 1, "repeated_failure_before_escalation_remaining": None}
    row = {"decision_id": "pd-1", "status": "REJECTED", "rejection_codes": ["REQUEST_COMPENSATION_GONE"],
           "detail": {}}
    with pytest.raises(ContractError, match="ERROR_CODE_UNREGISTERED"):
        feedback_from_decision(row, budgets=budgets)
    row["rejection_codes"] = ["BUDGET_INSUFFICIENT", "UNKNOWN_FIELD"]
    feedback = feedback_from_decision(row, budgets=budgets)
    assert feedback.rejection_codes == (P.UNKNOWN_FIELD, P.BUDGET_INSUFFICIENT)


# ---- 第 1 批 T01：共享核对的拒绝按类型码退回规划器 ----


def test_every_sharing_refusal_code_maps_to_exactly_one_planner_code():
    """**Mutation**: drop a row from ``SHARING_PLANNER_CODE`` → red."""
    assert set(SHARING_PLANNER_CODE) == set(SharingRefusalCode)
    coverage = {SharingRefusalCode.TASKGRAPH_INDEPENDENT_WORK_STILL_REQUIRED,
                SharingRefusalCode.TASKGRAPH_RETAINED_PRODUCER_DEMAND_MISSING}
    # 第 1 批评估：刚失效 / 未决属 §12 的"请求过期 / 需收敛"，不是规划器答错，不扣次数
    stale = {SharingRefusalCode.TASKGRAPH_SHARE_ACTIVE_START_NOT_CURRENT}
    unsettled = {SharingRefusalCode.TASKGRAPH_SHARE_ACTIVE_ORDER_UNMET,
                 SharingRefusalCode.TASKGRAPH_SHARE_ACTIVE_ORDER_UNSETTLED}
    from agent_orchestrator.contracts.error_table import refusal_charges_planner
    for code in SharingRefusalCode:
        expected = (P.COVERAGE_GAP if code in coverage else P.REQUEST_BINDING_STALE if code in stale
                    else P.RUNNING_WORK_NOT_RECONCILED if code in unsettled else P.REUSE_NOT_ALLOWED)
        assert SHARING_PLANNER_CODE[code] is expected, code
        assert refusal_charges_planner([str(expected)]) is (code not in stale), code
        refused = SharingRefused(code)
        assert refused.code is code and refused.planner_code is expected
        assert str(refused) == code.value  # 现有用例按文字匹配码
        assert isinstance(refused, ContractError)
    # 库故障码不在表里：来源与旧图不一致不是规划器的错
    assert "TASKGRAPH_INDEPENDENT_DEMAND_SOURCE_INVALID" not in SharingRefusalCode.__members__


def test_sharing_refused_only_accepts_registered_codes():
    with pytest.raises(ContractError, match="ERROR_CODE_UNREGISTERED"):
        SharingRefused("TASKGRAPH_SHARED_PRODUCER_SOURCE_CHANGED")  # type: ignore[arg-type]


def test_every_sharing_raise_site_uses_the_typed_refusal():
    """源码扫描：共享核对两个文件里再没有以 ``TASKGRAPH_SHARED_``/``SHARE_ACTIVE_``/``REUSE_``/
    ``ACCEPTED_PRODUCER_``/``INDEPENDENT_WORK``/``RETAINED_PRODUCER`` 开头的普通 ``ContractError``。"""
    import re
    from pathlib import Path

    import agent_orchestrator.graph.taskgraph_sharing as sharing
    import agent_orchestrator.graph.convergence as convergence

    for module in (sharing, convergence):
        text = Path(module.__file__).read_text(encoding="utf-8")
        plain = re.findall(r'raise ContractError\("(TASKGRAPH_[A-Z_]+)"\)', text)
        assert not any(code in SharingRefusalCode.__members__ for code in plain), plain
        typed = re.findall(r"raise SharingRefused\(SharingRefusalCode\.([A-Z_]+)\)", text)
        assert all(code in SharingRefusalCode.__members__ for code in typed), typed
    assert "TASKGRAPH_INDEPENDENT_DEMAND_SOURCE_INVALID" in Path(sharing.__file__).read_text(encoding="utf-8")


def test_the_main_loop_judges_a_sharing_refusal_by_type_not_by_text():
    """**Mutation**: ``event_handler`` 那段改回 ``str(error).startswith("TASKGRAPH_SHARED_")`` → red。"""
    from pathlib import Path

    import agent_orchestrator.orchestrator.event_handler as handler

    refused = SharingRefused(SharingRefusalCode.TASKGRAPH_SHARED_PRODUCER_BINDING_CHANGED)
    assert handler._sharing_refusal_codes(refused) == [str(P.REUSE_NOT_ALLOWED)]
    gap = SharingRefused(SharingRefusalCode.TASKGRAPH_INDEPENDENT_WORK_STILL_REQUIRED)
    assert handler._sharing_refusal_codes(gap) == [str(P.COVERAGE_GAP)]
    # 同样的文字、普通类型：不是规划器被拒
    assert handler._sharing_refusal_codes(ContractError("TASKGRAPH_SHARED_PRODUCER_BINDING_CHANGED")) is None
    assert handler._sharing_refusal_codes(RuntimeError("TASKGRAPH_REUSE_ACCEPTANCE_NOT_CURRENT")) is None
    text = Path(handler.__file__).read_text(encoding="utf-8")
    assert "except SharingRefused as error:" in text
    assert 'startswith(("TASKGRAPH_SHARED_"' not in text


# ---- 第 1 批 T03：只读接口对外发出的码全部登记并经分类 ----


def test_read_api_codes_are_registered_and_categorised():
    expected = {
        "BOUND_REACHED": ErrorCategory.BUDGET,
        "INVALID_CURSOR": ErrorCategory.IDENTITY_PROTOCOL,
        "INVALID_REQUEST": ErrorCategory.IDENTITY_PROTOCOL,
        "INVALID_REVISION": ErrorCategory.IDENTITY_PROTOCOL,
        "NOT_ENABLED": ErrorCategory.IDENTITY_PROTOCOL,
        "NOT_FOUND": ErrorCategory.SOURCE_UNAVAILABLE,
        "REVISION_NOT_FOUND": ErrorCategory.SOURCE_UNAVAILABLE,
        "SNAPSHOT_CHANGED": ErrorCategory.REQUEST_STALE,
        "SOURCE_CHANGED": ErrorCategory.REQUEST_STALE,
    }
    for code, category in expected.items():
        assert code in TaskGraphBoundaryCode.__members__
        assert classify(code).category is category, code


def test_every_code_the_read_api_emits_is_registered():
    """源码扫描：``api/taskgraph.py`` 里每个 ``_fail("…")`` 的码都在 ``TaskGraphBoundaryCode`` 里。"""
    import re
    from pathlib import Path

    import agent_orchestrator.api.taskgraph as api

    text = Path(api.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'_fail\("([A-Z_]+)"', text))
    assert emitted and emitted <= set(TaskGraphBoundaryCode.__members__), emitted - set(TaskGraphBoundaryCode.__members__)


def test_read_api_fail_refuses_an_unregistered_code():
    """**Mutation**: ``_fail`` 不先 ``classify`` → red。"""
    from agent_orchestrator.api.taskgraph import TaskGraphReadError, _fail

    with pytest.raises(ContractError, match="ERROR_CODE_UNREGISTERED"):
        _fail("NO_SUCH_READ_CODE", "x")
    with pytest.raises(TaskGraphReadError) as caught:
        _fail("NOT_FOUND", "Mission was not found")
    assert caught.value.code == "NOT_FOUND" and caught.value.error.retry_kind == "NONE"


def test_only_the_codecs_unreadable_codes_allow_one_free_same_request_reask():
    """2026-10-09 四项修复第 2 条：解码器读不懂回复的 7 个码标 free_reask，同一请求重问一次不算答错；
    准入/提交被拒的码都不标。

    **Mutation**: clear ``free_reask`` on any of the seven, or set it on a content code → red."""
    from agent_orchestrator.contracts.error_table import refusal_free_reask

    free = {code for code, entry in PLANNING_ERRORS.items() if entry.free_reask}
    assert free == {P.DECISION_BLOCK_MISSING, P.MULTIPLE_DECISIONS, P.MIXED_PROTOCOL_BLOCKS,
                    P.MALFORMED_DECISION, P.UNKNOWN_FIELD, P.MODEL_SET_SYSTEM_FIELD, P.DECISION_TYPE_UNKNOWN}
    assert refusal_free_reask(["MALFORMED_DECISION"]) and refusal_free_reask([P.DECISION_BLOCK_MISSING])
    assert not refusal_free_reask([]) and not refusal_free_reask(["PARAMETER_INVALID"])
    assert not refusal_free_reask(["MALFORMED_DECISION", "COVERAGE_GAP"]) and not refusal_free_reask(["NOPE"])
