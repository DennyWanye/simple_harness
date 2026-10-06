# SPDX-License-Identifier: Apache-2.0
"""第 2 批 K01：审阅员拿到黑板读工具（用户 2026-10-02："审阅员通过黑板自己去取"；10-06 定）。

审阅员原来只有证据找/读两件工具，知识只以"相关条目"写进审查包。现在它还拿到执行者那一套
``knowledge_list`` / ``knowledge_read``（同一个读器，按审阅所在任务的范围、只列当前有效的条目；
审阅员本身是只读身份），审阅员提示词说明可以自己查黑板、查到的不是证据。审查包里的相关条目
照旧保留——那是系统摆事实，与审阅员自取不是同一件事。

* 工具常量：审阅员的四件工具 = 两件证据工具 + 两件知识工具；绑定只收这四件，别的拒绝；
* 提示词：说明两件知识工具与"不是证据、不能写进 evidence_ids"；提示词版本升到 v2；
* 产品路径：内容审阅里审阅员调 ``knowledge_list`` 能拿到目录，审阅照常给结论、任务完成；
  审阅意图的 tool_names 带知识工具、prompt_version 是新版本；审查包仍带 related_entries。

**改坏检验**：网关对审阅绑定仍去取工作区（去掉"审阅绑定不碰工作区"）→ 第三条变红
（知识工具被拒，审阅员拿不到目录）。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.assurance.review_input import REVIEW_INSTRUCTIONS, REVIEW_INSTRUCTIONS_VERSION
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.runtime.tool_gateway import ASSURANCE_EVIDENCE_TOOLS, ASSURANCE_REVIEWER_TOOLS
from agent_orchestrator.verification.reviewer_evidence_tools import ASSURANCE_PROTOCOL, ReviewerEvidenceTools

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


def test_the_reviewer_tool_set_is_the_evidence_tools_plus_the_blackboard_readers() -> None:
    assert ASSURANCE_REVIEWER_TOOLS == (*ASSURANCE_EVIDENCE_TOOLS, *KNOWLEDGE_TOOLS)
    assert REVIEW_INSTRUCTIONS_VERSION == "assurance-review-instructions-v2"
    for name in KNOWLEDGE_TOOLS:
        assert name in REVIEW_INSTRUCTIONS, name
    # 查到的知识不是证据：不能写进 evidence_ids；审查包里的相关条目仍然在提示词里
    knowledge_paragraph = REVIEW_INSTRUCTIONS[REVIEW_INSTRUCTIONS.index("knowledge_list"):]
    assert "不是证据" in knowledge_paragraph and "evidence_ids" in knowledge_paragraph
    assert "package.related_entries" in REVIEW_INSTRUCTIONS


def _tools(bound: list[Any]) -> ReviewerEvidenceTools:
    row = {"mission_id": "m1"}
    connection = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(fetchone=lambda: row))
    gateway = SimpleNamespace(bind=lambda agent_id, binding: bound.append((agent_id, binding)))
    runtime = SimpleNamespace(
        orchestrator=SimpleNamespace(assembled=SimpleNamespace(gateway=gateway)),
        consumer=SimpleNamespace(store=SimpleNamespace(connection=connection)))
    return ReviewerEvidenceTools(runtime)


def test_the_binding_takes_the_four_reviewer_tools_and_refuses_anything_else() -> None:
    bound: list[Any] = []
    config = {"assurance_protocol": ASSURANCE_PROTOCOL, "review_key": "rk-1", "attempt_id": "",
              "agent_config": {"tool_names": list(ASSURANCE_REVIEWER_TOOLS)}}
    _tools(bound).bind("agent-1", config)
    [(agent_id, binding)] = bound
    assert agent_id == "agent-1" and binding.allowed_tools == ASSURANCE_REVIEWER_TOOLS
    assert binding.review_key == "rk-1" and binding.mission_id == "m1" and binding.view == "verify"
    assert binding.writable is False
    with pytest.raises(ContractError, match="read-only"):
        _tools([]).bind("agent-2", {**config, "agent_config": {"tool_names": [*ASSURANCE_REVIEWER_TOOLS, "workspace_read_file"]}})


# ------------------------------------------------------------------------ product path
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    review_input,
    review_reply,
    worker_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _tool_results(request: Any) -> list[str]:
    return [str(message.content) for message in request.messages if "tool" in str(message.role).lower()]


def test_a_content_reviewer_can_read_the_blackboard_on_the_native_pool_and_still_concludes(tmp_path):
    seen: list[str] = []
    packages: list[dict[str, Any]] = []

    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) != "TASK_CONTENT":
            return review_reply(data)
        packages.append(data["package"])
        results = _tool_results(request)
        if not results:
            return ("knowledge_list", {})
        seen.append(results[-1])
        return review_reply(data)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(worker=worker_reply, reviewer=reviewer)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "kn-reviewer"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert mission.status.value == "COMPLETED", mission.final_report
            assert seen, "the reviewer never got a knowledge_list result"
            body = json.loads(seen[0])
            assert body.get("outcome") == "succeeded", seen[0]
            value = body["value"]
            assert "items" in value and "sha256" in value and "layer=verified" in value["notice"]
            # 审查包里的相关条目照旧保留（这一局没有矛盾结论、没有引用知识，所以是空的，渲染时不列）：
            # 系统摆事实与审阅员自取是两件事
            store = world.store
            from agent_orchestrator.contracts.resolution import ReviewPurpose
            from agent_orchestrator.storage.htn_store import HtnStore
            stored = HtnStore(store).list_review_packages(mission_id, purpose=ReviewPurpose.TASK_CONTENT)
            assert packages and stored and all(isinstance(pkg.related_entries, tuple) for pkg in stored)
            # 审阅意图：工具名带知识工具，提示词版本是新版本
            reviews = [intent for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED")
                       if intent.mission_id == mission_id and intent.config.get("assurance_protocol") == ASSURANCE_PROTOCOL]
            assert reviews
            for intent in reviews:
                assert tuple(intent.config["agent_config"]["tool_names"]) == ASSURANCE_REVIEWER_TOOLS
                assert intent.config["prompt_version"] == REVIEW_INSTRUCTIONS_VERSION

    asyncio.run(case())
