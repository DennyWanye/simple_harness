# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §9: the chat Agent starts a background Mission through the task page's own door.

``mission_start`` calls ``OrchestrationService.create_mission`` (same door checks,
budget defaults, strict TaskGraph requirement); a replayed call keeps its
Run/call-derived idempotency key and so creates one Mission only; an unavailable
service or a refused request is a clear tool failure, never a silent success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.orchestration.chat_tool import MissionStartRefused, start_mission
from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.sdk_adapters.tool_authority import SDK_DIRECT_TOOL_KERNEL, describe_call_zh
from deskpet.sdk_adapters.tools import HOST_COMPOSED_TOOL_NAMES, PRODUCT_TOOL_NAMES

MAIN = Path(__file__).parents[2] / "main.py"


class _Service:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.missions: dict[str, dict] = {}

    def status(self):
        return {"available": self.available, "reason": None if self.available else "没有模型"}

    def create_mission(self, request):
        if "发布" in request["goal"] and len(request["success_criteria"]) > 5:
            raise OrchestrationRequestError("action_criteria_disabled", "连接器 file_publish 未启用")
        key = request["idempotency_key"]
        created = key not in self.missions
        self.missions.setdefault(key, {"mission_id": "mission-" + str(len(self.missions) + 1), **request})
        return {"mission_id": self.missions[key]["mission_id"], "created": created, "spec_hash": "h"}

    def mission_detail(self, mission_id):
        return {"mission": {"id": mission_id, "status": "PLANNING"}}


def test_start_goes_through_create_mission_once_per_call():
    service = _Service()
    args = {"goal": "写一份 ZIP.md", "success_criteria": ["ZIP.md 有三句话"]}
    first = start_mission(lambda: service, args, run_id="run-1", call_id="call-1")
    again = start_mission(lambda: service, args, run_id="run-1", call_id="call-1")
    other = start_mission(lambda: service, args, run_id="run-1", call_id="call-2")
    assert first["created"] is True and first["status"] == "PLANNING"
    assert again == {**first, "created": False, "note": again["note"]} and "没有重复创建" in again["note"]
    assert other["mission_id"] != first["mission_id"]
    [request, *_] = service.missions.values()
    assert request["idempotency_key"] == "chat-mission:run-1:call-1"
    assert set(request) == {"mission_id", "goal", "success_criteria", "idempotency_key"}  # no budget invented here


@pytest.mark.parametrize("args,code", [
    ({"goal": "", "success_criteria": ["x"]}, "invalid_arguments"),
    ({"goal": "g", "success_criteria": []}, "invalid_arguments"),
    ({"goal": "g", "success_criteria": ["x"], "budget": {"max_tokens": 1}}, "invalid_arguments"),
    ({"goal": "发布", "success_criteria": ["a", "b", "c", "d", "e", "f"]}, "action_criteria_disabled"),
])
def test_refusals_are_tool_failures(args, code):
    with pytest.raises(MissionStartRefused) as refused:
        start_mission(lambda: _Service(), args, run_id="r", call_id="c")
    assert refused.value.code == code


def test_unavailable_orchestration_is_retryable_not_silent():
    for getter in (lambda: None, lambda: _Service(available=False)):
        with pytest.raises(MissionStartRefused) as refused:
            start_mission(getter, {"goal": "g", "success_criteria": ["x"]}, run_id="r", call_id="c")
        assert refused.value.code == "orchestration_unavailable" and refused.value.retryable


def test_the_tool_is_registered_visible_and_described():
    assert "mission_start" in PRODUCT_TOOL_NAMES and "mission_start" in HOST_COMPOSED_TOOL_NAMES
    assert "mission_start" in SDK_DIRECT_TOOL_KERNEL
    assert describe_call_zh("mission_start", {"goal": "写 ZIP.md"}) == "在任务编排里新建后台任务「写 ZIP.md」"
    assert 'name=MISSION_START_TOOL_NAME' in MAIN.read_text(encoding="utf-8")


def test_mission_status_reads_progress_and_what_waits_for_the_person():
    """2026-09-29：主 Agent 能在对话里说清任务走到哪、等谁；确认与批准只能由人点，工具只读。"""
    from deskpet.orchestration.chat_tool import mission_status

    class _Detail(_Service):
        def mission_detail(self, mission_id):
            return {
                "mission": {"id": mission_id, "status": "ACTIVE", "goal": "写词频模块并发布"},
                "operation_workspace": {"state": "CONFIRMED"},
                "approvals": [{"state": "PENDING", "kind": "action", "summary": {
                    "connector": "file_publish", "operation": "publish", "target": "README.md"}}],
                "tasks": [{"status": "COMPLETED", "kind": "work"}, {"status": "RUNNING", "kind": "work"}],
                "actions": [{"state": "SUCCEEDED", "target": "wordfreq.py",
                             "published_path": "/pub/wordfreq.342d.v1.py"}],
            }

    status = mission_status(lambda: _Detail(), {"mission_id": "mission-1"})
    assert status["status_zh"] == "进行中"
    assert status["waiting_for"] == ["等用户批准发布 README.md"]
    assert status["steps"] == {"total": 2, "done": 1}
    assert status["published"] == [{"target": "wordfreq.py", "published_path": "/pub/wordfreq.342d.v1.py"}]
    assert "不能代为确认或批准" in status["note"]
    with pytest.raises(MissionStartRefused):
        mission_status(lambda: _Detail(), {"mission_id": "m", "approve": True})


def test_mission_status_is_registered_read_only_and_described():
    assert "mission_status" in PRODUCT_TOOL_NAMES and "mission_status" in HOST_COMPOSED_TOOL_NAMES
    assert "mission_status" in SDK_DIRECT_TOOL_KERNEL
    assert describe_call_zh("mission_status", {"mission_id": "m"}) == "查看后台任务的进度"
    assert 'name=MISSION_STATUS_TOOL_NAME' in MAIN.read_text(encoding="utf-8")
    from deskpet.orchestration.chat_tool import MISSION_STATUS_SCHEMA
    assert set(MISSION_STATUS_SCHEMA["properties"]) == {"mission_id"}  # 没有任何能改状态的参数
