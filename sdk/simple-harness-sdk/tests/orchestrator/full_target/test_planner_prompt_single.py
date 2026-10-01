# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划器提示词只有一份当前版本，说的是现在的事（HTN 精简 片 A 第 10 项）。

它是完整的一份，不从旧版本拼接（旧版本留着"你不需要也不能自己合成方法""单候选由系统
本地处理"这些已经不成立的话，片 C 已全部删除）。这里钉住三件事：

* 当前规划包只配这一份提示词，新任务绑定的就是它；
* 提示词里的每个示例都能通过决定解码器，新做法示例能通过做法解码器——示例写错了，模型
  照抄就是一轮格式错；
* 它列出的决定种类与启用表一致：表里的都讲到了；声明受阻、修复里的升级这两种类型已
  删除，不再出现。
"""
from __future__ import annotations

import json

from agent_orchestrator.contracts.planning_decisions import (
    PlanningDecisionEnvelopeV1,
    exposed_enablement,
)
from agent_orchestrator.orchestrator.planning_protocol_binding import PLANNING_PROTOCOL_BINDING
from agent_orchestrator.planning.htn.registry import MethodProposal
from agent_orchestrator.runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
    hierarchical_planner_pairing_is_valid,
)

PROMPT = PLANNER_HIERARCHICAL.instructions


def _examples() -> list[dict]:
    return [json.loads(line) for line in PROMPT.splitlines() if line.startswith("{")]


def test_the_current_package_is_paired_with_exactly_this_prompt() -> None:
    assert PLANNING_DECISION_PROMPT_VERSION == PLANNER_HIERARCHICAL.prompt_version
    assert hierarchical_planner_pairing_is_valid(
        PLANNER_HIERARCHICAL.prompt_version, PLANNING_DECISION_PACKAGE_VERSION)
    assert PLANNING_PROTOCOL_BINDING == {
        "package_version": PLANNING_DECISION_PACKAGE_VERSION,
        "prompt_version": PLANNER_HIERARCHICAL.prompt_version,
    }


def test_every_example_in_the_prompt_decodes() -> None:
    examples = _examples()
    assert [item["decision_type"] for item in examples] == [
        "REFINE", "PROPOSE_METHOD", "PROPOSE_METHOD", "REPAIR", "REQUEST_HUMAN"]
    for document in examples:
        PlanningDecisionEnvelopeV1.from_json(document)
    proposal = MethodProposal.from_json(examples[1]["payload"]["method_proposal"])
    assert [step.local_id for step in proposal.method.steps] == ["collect", "deliver"]
    # every criterion the example links is carried by a step the example declares
    assert {link.child_step for link in proposal.method.composition.criterion_links} == {
        "collect", "deliver"}


def test_the_prompt_offers_what_is_enabled_and_nothing_that_is_not() -> None:
    decision_types, repair_kinds = exposed_enablement()
    for name in (*decision_types, *repair_kinds):
        assert name in PROMPT, f"enabled but not described: {name}"
    # "stuck" has one spelling: ask the user
    assert "DECLARE_BLOCKED" not in PROMPT and "ESCALATE" not in PROMPT
    assert "REQUEST_HUMAN" in decision_types


def test_the_prompt_no_longer_says_the_program_chooses_or_synthesises() -> None:
    for stale in ("不需要也不能自己合成方法", "单候选由系统本地处理", "MODEL_REFINE",
                  "方法合成轮", "方法合成器"):
        assert stale not in PROMPT
    # the Planner chooses among candidates and may propose a method itself
    assert "没有替你选" in PROMPT and "PROPOSE_METHOD" in PROMPT
    # a proposed method is adopted only after its independent review passed
    assert "review.outcome=PASSED" in PROMPT and "METHOD_NOT_AUTHORIZED" in PROMPT


def test_the_prompt_explains_sub_goals_as_facts_and_order_rules_only() -> None:
    """片 B：提示词讲清"目标还没有做法"的请求、子目标类型从哪里抄、交给子目标的要求保持
    原编号、中间目标的做法恰好覆盖分到的要求——都是事实与秩序；要不要拆出中间目标由规划器判断。"""
    for needed in ("trigger_source 为 GOAL_UNREFINED", "subgoal_types", "SUBGOAL_COVERAGE", "open_goals"):
        assert needed in PROMPT, needed
    assert "是否需要中间目标由你判断" in PROMPT
    # the sub-goal example: a compound step, its requirement handed down under the same id
    examples = _examples()
    assert [item["decision_type"] for item in examples] == [
        "REFINE", "PROPOSE_METHOD", "PROPOSE_METHOD", "REPAIR", "REQUEST_HUMAN"]
    nested = MethodProposal.from_json(examples[2]["payload"]["method_proposal"]).method
    compound = [step.local_id for step in nested.steps if str(step.form) == "compound"]
    assert compound == ["organise"]
    handed = [(link.parent_criterion_id, link.child_criterion_id)
              for link in nested.composition.criterion_links if link.child_step == "organise"]
    assert handed and all(parent == child for parent, child in handed)
    # the later step takes the sub-goal's result through a port, not through ordering alone
    from agent_orchestrator.contracts.htn import OutputValue
    from agent_orchestrator.planning.htn.registry import iter_values
    consumer = next(step for step in nested.steps if step.local_id == "summarise")
    read = [node for argument in consumer.arguments.values() for node in iter_values(argument)
            if isinstance(node, OutputValue)]
    assert [(node.step, node.port) for node in read] == [("organise", "result")]
    assert "只写先后顺序拿不到文件" in PROMPT and "finalizer_step 必须写" in PROMPT
