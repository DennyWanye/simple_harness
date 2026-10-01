# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划器提示词只有一份当前版本，说的是现在的事（HTN 精简 片 A 第 10 项）。

v8–v13 是六层叠加，留着"你不需要也不能自己合成方法""单候选由系统本地处理"这些已经不
成立的话。v14 是完整的一份。这里钉住三件事：

* 当前规划包只配这一份提示词，新任务绑定的就是它；
* 提示词里的每个示例都能通过决定解码器，新做法示例能通过做法解码器——示例写错了，模型
  照抄就是一轮格式错；
* 它列出的决定种类与启用表一致：启用的都讲到了，没启用的（声明受阻、修复里的升级）不
  再出现。
"""
from __future__ import annotations

import json

from agent_orchestrator.contracts.planning_decisions import (
    H4_DECISION_ENABLEMENT,
    PlanningDecisionEnvelopeV1,
    exposed_enablement,
)
from agent_orchestrator.orchestrator.planning_protocol_binding import PLANNING_PROTOCOL_BINDING
from agent_orchestrator.planning.htn.registry import MethodProposal
from agent_orchestrator.runtime.role_templates import (
    PLANNER_HIERARCHICAL_V14,
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
    hierarchical_planner_versions,
)

PROMPT = PLANNER_HIERARCHICAL_V14.instructions


def _examples() -> list[dict]:
    return [json.loads(line) for line in PROMPT.splitlines() if line.startswith("{")]


def test_the_current_package_is_paired_with_exactly_this_prompt() -> None:
    assert PLANNING_DECISION_PROMPT_VERSION == PLANNER_HIERARCHICAL_V14.prompt_version
    assert hierarchical_planner_versions(PLANNING_DECISION_PACKAGE_VERSION) == frozenset(
        {PLANNER_HIERARCHICAL_V14.prompt_version})
    assert PLANNING_PROTOCOL_BINDING == {
        "package_version": PLANNING_DECISION_PACKAGE_VERSION,
        "prompt_version": PLANNER_HIERARCHICAL_V14.prompt_version,
    }


def test_every_example_in_the_prompt_decodes() -> None:
    examples = _examples()
    assert [item["decision_type"] for item in examples] == [
        "REFINE", "PROPOSE_METHOD", "REPAIR", "REQUEST_HUMAN"]
    for document in examples:
        PlanningDecisionEnvelopeV1.from_json(document)
    proposal = MethodProposal.from_json(examples[1]["payload"]["method_proposal"])
    assert [step.local_id for step in proposal.method.steps] == ["collect", "deliver"]
    # every criterion the example links is carried by a step the example declares
    assert {link.child_step for link in proposal.method.composition.criterion_links} == {
        "collect", "deliver"}


def test_the_prompt_offers_what_is_enabled_and_nothing_that_is_not() -> None:
    decision_types, repair_kinds = exposed_enablement(H4_DECISION_ENABLEMENT)
    for name in (*decision_types, *repair_kinds):
        assert name in PROMPT, f"enabled but not described: {name}"
    # "stuck" has one spelling: ask the user
    assert "DECLARE_BLOCKED" not in decision_types and "ESCALATE" not in repair_kinds
    assert "DECLARE_BLOCKED" not in PROMPT and "ESCALATE" not in PROMPT
    assert "REQUEST_HUMAN" in decision_types


def test_the_prompt_no_longer_says_the_program_chooses_or_synthesises() -> None:
    for stale in ("不需要也不能自己合成方法", "单候选由系统本地处理", "MODEL_REFINE",
                  "方法合成轮", "method_synthesizer"):
        assert stale not in PROMPT
    # the Planner chooses among candidates and may propose a method itself
    assert "没有替你选" in PROMPT and "PROPOSE_METHOD" in PROMPT
    # a proposed method is adopted only after its independent review passed
    assert "review.outcome=PASSED" in PROMPT and "METHOD_NOT_AUTHORIZED" in PROMPT
