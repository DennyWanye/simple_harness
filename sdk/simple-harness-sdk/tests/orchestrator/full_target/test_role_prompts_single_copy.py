# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""分层角色的提示词各只登记一份，方法合成器角色不存在（HTN 精简 片 C）。

片 A 把"现有做法都不合适时提一个新做法"交给了规划器，方法合成器这个角色从那时起就没有
运行路径了；它的九个提示词版本、重问反馈、角色清单里的名字都是死代码。规划器、分层执行者、
根审阅员的历史版本同理：开发期不保留历史版本，也不为它们钉哈希。这里钉住：

* 登记表里没有方法合成器角色，源码里不再出现它的名字，旧模块不存在；
* 规划器、分层执行者、根审阅员各只登记一份当前提示词，模块不再导出带版本号的历史名字；
* 规划包版本与提示词版本只有当前这一对是合法配对；
* 规划器提出的做法经解码后直接交给注册协议，不再绕一圈文本。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from agent_orchestrator.runtime import role_templates as templates

SRC = Path(templates.__file__).resolve().parents[2]


def test_there_is_no_method_synthesizer_role() -> None:
    assert "method_synthesizer" not in templates.TEMPLATE_VERSIONS
    assert [name for name in dir(templates) if name.startswith("METHOD_SYNTHESIZER")] == []
    assert importlib.util.find_spec("agent_orchestrator.planning.htn.synthesis") is None
    named = sorted(
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
        if "method_synthesizer" in path.read_text(encoding="utf-8")
        or "method-synthesizer" in path.read_text(encoding="utf-8"))
    assert named == [], named


def test_each_hierarchical_role_registers_exactly_one_prompt() -> None:
    planner = {version for version in templates.TEMPLATE_VERSIONS["planner"]
               if version.startswith("planner-hierarchical-")}
    assert planner == {templates.PLANNER_HIERARCHICAL.prompt_version}
    assert templates.PLANNING_DECISION_PROMPT_VERSION == templates.PLANNER_HIERARCHICAL.prompt_version
    worker = {version for version in templates.TEMPLATE_VERSIONS["worker"]
              if version.startswith("worker-hierarchical-")}
    assert worker == {templates.WORKER_HIERARCHICAL.prompt_version}
    assert set(templates.TEMPLATE_VERSIONS["root_reviewer"]) == {
        templates.ROOT_REVIEWER.prompt_version}


def test_no_historical_prompt_names_are_left() -> None:
    stale = [name for name in dir(templates) if re.fullmatch(
        r"(PLANNER_HIERARCHICAL|WORKER_HIERARCHICAL|ROOT_REVIEWER)_V\d+(_VERSION)?", name)]
    assert stale == []
    for gone in ("HIERARCHICAL_PLANNER_VERSIONS", "HIERARCHICAL_PLANNER_VERSIONS_BY_PACKAGE",
                 "hierarchical_planner_versions"):
        assert not hasattr(templates, gone), gone
    assert set(templates.__all__) <= set(dir(templates))


def test_only_the_current_package_and_prompt_pair() -> None:
    prompt = templates.PLANNING_DECISION_PROMPT_VERSION
    package = templates.PLANNING_DECISION_PACKAGE_VERSION
    valid = templates.hierarchical_planner_pairing_is_valid
    assert valid(prompt, package)
    assert not valid("planner-hierarchical-v13", 8)
    assert not valid(prompt, package - 1)
    assert not valid("planner-hierarchical-v13", package)


def test_the_hierarchical_workers_of_other_domains_still_register() -> None:
    """领域自己的分层执行者（AppWorld、无人机模拟）不在这次收口范围里，照常登记。"""
    assert templates.HIERARCHICAL_WORKER_VERSIONS == frozenset({
        templates.WORKER_HIERARCHICAL.prompt_version,
        "worker-appworld-hierarchical-v1", "worker-drone-sim-hierarchical-v1"})


def test_a_proposed_method_is_admitted_from_the_decoded_proposal() -> None:
    """受理入口接的是已解码的提议；状态与作者由注册协议裁决，不在入口改写。"""
    from agent_orchestrator.planning.htn import method_proposals

    assert not hasattr(method_proposals, "MethodSynthesizer")
    assert not hasattr(method_proposals, "SynthesisRequest")
    with pytest.raises(Exception, match="MethodProposal"):
        method_proposals.admit_proposal("<method_proposal>{}</method_proposal>",  # type: ignore[arg-type]
                                        registry=None, policy=None)  # type: ignore[arg-type]


def test_nothing_is_appended_to_a_planning_request_as_a_second_prompt() -> None:
    """规划请求末尾曾经挂着一段英文"协议提醒"——讲旧协议字段、第 7 版包、第 10 版提示词，
    等于第二份提示词。删掉；其中仍然成立的两条写法在唯一的那份提示词里。"""
    handler = (SRC / "agent_orchestrator" / "orchestrator" / "event_handler.py").read_text(encoding="utf-8")
    assert "PROTOCOL REMINDER" not in handler
    prompt = templates.PLANNER_HIERARCHICAL.instructions
    assert "attempt_review_ref.id" in prompt and "不是整个引用对象" in prompt
    assert "resumable_if（数组，取 evidence_updated、human_resolved、plan_revision_changed）" in prompt
