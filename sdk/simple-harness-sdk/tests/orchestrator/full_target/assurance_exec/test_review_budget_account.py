# SPDX-License-Identifier: Apache-2.0
"""审阅的花费记在哪个账户（HTN 精简 片 B 真机第 4 局，2026-10-01）。

中间目标这种任务自己不带额度——额度都分在它下面的步骤上。它的组合审阅原来要从"中间目标
自己的任务账户"预留，永远预留不到（真机：``BudgetExhausted … remaining 0``），中间目标出不了
结论，后面的步骤永远不开工。现在与根目标的最终审查一样记在任务总账上。

发起审阅时的预留、事后对账读的账户，两处用同一个函数，不会对不上。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_orchestrator.contracts.resolution import ReviewAccount, ReviewPurpose, account_for_purpose
from agent_orchestrator.orchestrator.assurance_review_transport import review_budget_subject

MISSION, TASK = "mission-1", "task-goal"


def _package(purpose: ReviewPurpose):
    return SimpleNamespace(purpose=purpose, account=account_for_purpose(purpose))


@pytest.mark.parametrize("purpose, subject", [
    (ReviewPurpose.COMPOSITION, MISSION),    # a compound holds no tokens of its own
    (ReviewPurpose.MISSION_FINAL, MISSION),
    (ReviewPurpose.METHOD_PLAN, MISSION),
    (ReviewPurpose.TASK_CONTENT, TASK),      # a step pays for its own content review
])
def test_which_account_a_review_is_charged_to(purpose, subject):
    assert review_budget_subject(None, MISSION, _package(purpose), TASK) == subject


def test_the_composition_account_is_still_named_as_the_compounds_in_the_contract():
    """契约里的账户名不变（它说的是"这笔花费属于谁"）；变的只是实际从哪个账户预留。"""
    assert account_for_purpose(ReviewPurpose.COMPOSITION) is ReviewAccount.PARENT_COMPOUND_TASK
