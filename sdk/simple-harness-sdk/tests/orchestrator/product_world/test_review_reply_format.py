# SPDX-License-Identifier: Apache-2.0
"""审阅员回复的格式口径（用户 2026-10-02 定；HTN 补齐阶段 C 第一步）。

容忍两样不改变审阅员意思的东西：整个回复外面包一层代码围栏；多写一个值为空的字段。
其余照旧拒收并在同一次尝试里让它改：JSON 前后多写文字、有值的多余字段、超长。

**改坏检验**：解码里去掉剥围栏 → 第一条变红（审阅员被问第二次）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input, review_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _run(tmp_path, reviewer, purpose: str = "TASK_CONTENT") -> tuple[str, list[Any], int]:
    async def case():
        calls = {"content": 0}

        def counted(request: Any) -> Any:
            data = review_input(request)
            if data is not None and str((data.get("package") or {}).get("purpose")) == purpose:
                calls["content"] += 1
                return reviewer(data, calls["content"])
            return None if data is None else review_reply(data)

        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=counted)) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "reply-format"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            return str(mission.status.value), list(world.store.list_events(mission_id)), calls["content"]

    return asyncio.run(case())


def test_fenced_reply_and_empty_extra_field_are_accepted(tmp_path):
    def reviewer(data: Any, call: int) -> str:
        body = json.loads(review_reply(data))
        body["notes"] = ""
        return "```json\n" + json.dumps(body, ensure_ascii=False) + "\n```"

    status, events, calls = _run(tmp_path, reviewer)
    assert status == "COMPLETED" and calls == 1
    assert not [event for event in events if event.type == "AssuranceReviewFormatRejected"]


def test_text_around_json_still_rejected(tmp_path):
    def reviewer(data: Any, call: int) -> str:
        return ("下面是我的结论：\n" if call == 1 else "") + review_reply(data)

    status, events, calls = _run(tmp_path, reviewer)
    assert status == "COMPLETED" and calls == 2  # the second call is the format repair


@pytest.mark.parametrize(("purpose", "field"), [("TASK_CONTENT", "methods"), ("METHOD_PLAN", "summary")])
def test_a_section_the_package_does_not_have_is_a_scope_error(tmp_path, purpose, field):
    """回复第 4 版的两节只对有对应一节的审查包：步骤内容审阅写了做法表态、做法审阅写了摘要核对，
    都按"范围错误"在同一次尝试里让它改（阶段 C3）。"""

    def reviewer(data: Any, call: int) -> str:
        body = json.loads(review_reply(data))
        if call == 1:
            body[field] = ([{"method_ref": "m@1", "reusable": False, "purpose": "", "at_fault": False, "reason": "r"}]
                           if field == "methods" else {"faithful": True, "reason": "r"})
        return json.dumps(body, ensure_ascii=False)

    status, events, calls = _run(tmp_path, reviewer, purpose)
    assert status == "COMPLETED" and calls == 2
    rejected = [event for event in events if event.type == "AssuranceReviewInterpretationRejected"]
    expected = "METHOD_SCOPE" if field == "methods" else "SUMMARY_SCOPE"
    assert rejected and expected in json.dumps([event.payload for event in rejected])
