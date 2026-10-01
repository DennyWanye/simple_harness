# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划器能回的决定种类只有一张表，表里的每一种都真的能执行（HTN 精简 片 C）。

此前是四层叠加的表（H1 → H3 → V14 → H4），每一项带"能解码 / 能受理 / 能执行"三个开关；
规划包先按第一层生成清单，另一处再按第四层覆盖；"声明受阻"和"修复里的升级问人"停在
"只能解码"——规划器要是写了，回复被解码、落库，然后被拒。"卡住了"只有一种说法（问用户），
所以这两种类型连类型一起删，而不是留一个永远被拒的入口。这里钉住：

* 契约里只有一张表，由决定类型和修复种类两个枚举直接得出，不存在"只能解码"的种类；
* 声明受阻、修复里的升级不再是合法的类型，写了按"类型不认识"拒绝；
* 规划包列出的清单、规划授权策略允许的清单、提示词讲到的清单，都是这张表。
"""
from __future__ import annotations

import pytest

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.contracts import planning_decisions as contract
from agent_orchestrator.contracts.planning_decisions import (
    ENABLED_DECISIONS,
    PlanningDecisionType,
    RepairKind,
    exposed_enablement,
    internal_enablement_keys,
)
from agent_orchestrator.runtime.role_templates import PLANNER_HIERARCHICAL


def test_there_is_one_table_and_it_is_the_two_enums() -> None:
    for gone in ("H1_DECISION_ENABLEMENT", "H3_DECISION_ENABLEMENT", "V14_DECISION_ENABLEMENT",
                 "H4_DECISION_ENABLEMENT", "DecisionEnablement"):
        assert not hasattr(contract, gone), gone
    expected = {str(item) for item in PlanningDecisionType if item is not PlanningDecisionType.REPAIR}
    expected |= {f"REPAIR/{item}" for item in RepairKind}
    assert ENABLED_DECISIONS == frozenset(expected)
    types, kinds = exposed_enablement()
    assert types == sorted(str(item) for item in PlanningDecisionType)
    assert kinds == sorted(str(item) for item in RepairKind)
    assert internal_enablement_keys(types, kinds) == ENABLED_DECISIONS


def test_stuck_has_one_spelling() -> None:
    assert "DECLARE_BLOCKED" not in {str(item) for item in PlanningDecisionType}
    assert "ESCALATE" not in {str(item) for item in RepairKind}
    for gone in ("DeclareBlockedDecision", "RepairEscalateDecision"):
        assert not hasattr(contract, gone), gone
    assert str(PlanningDecisionType.REQUEST_HUMAN) in ENABLED_DECISIONS
    # the runtime-blocked repair is an infrastructure fact with a resume condition; it stays
    assert "REPAIR/DECLARE_RUNTIME_BLOCKED" in ENABLED_DECISIONS
    with pytest.raises((ContractError, ValueError)):
        PlanningDecisionType("DECLARE_BLOCKED")
    with pytest.raises((ContractError, ValueError)):
        RepairKind("ESCALATE")


def test_the_authorization_policy_allows_exactly_the_table() -> None:
    from agent_orchestrator.governance.planning_authorization import planning_policy_for_mission

    policy = planning_policy_for_mission(None, "mission-x")
    assert frozenset(policy.allowed_decisions) == ENABLED_DECISIONS


def test_the_prompt_describes_every_kind_in_the_table() -> None:
    types, kinds = exposed_enablement()
    for name in (*types, *kinds):
        assert name in PLANNER_HIERARCHICAL.instructions, name
