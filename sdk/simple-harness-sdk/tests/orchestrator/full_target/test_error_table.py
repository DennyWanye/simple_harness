# SPDX-License-Identifier: Apache-2.0
"""错误码表（TaskGraph 原计划 §12；HTN 补齐阶段 A 第 4 条）：枚举全集、未知码拒绝、先后顺序。"""
from __future__ import annotations

import pytest

from agent_orchestrator.contracts.error_table import (
    PLANNING_ERRORS,
    TASKGRAPH_ERRORS,
    ErrorCategory,
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
