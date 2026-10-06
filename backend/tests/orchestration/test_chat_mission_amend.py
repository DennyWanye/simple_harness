# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""阶段 E：主 Agent 替用户改后台任务的要求（``mission_amend``）。

工具只做三件事：核对用户看到的版本号还是最新的；把调用整理成一条带来源（对话运行号、调用号、
权限模式）的命令，命令号由运行号与调用号派生（重放不重复写）；经 ``OrchestrationService.
amend_requirements`` 走与建任务同一套门口检查。拒绝一律是清楚的工具失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deskpet.orchestration.chat_tool import (
    MISSION_AMEND_SCHEMA,
    MissionStartRefused,
    amend_mission,
    mission_status,
)
from deskpet.orchestration.projection import MISSION_FIELDS, project_detail
from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.sdk_adapters.tool_authority import SDK_DIRECT_TOOL_KERNEL, describe_call_zh
from deskpet.sdk_adapters.tools import HOST_COMPOSED_TOOL_NAMES, PRODUCT_TOOL_NAMES

BACKEND = Path(__file__).parents[2]
REF = {"id": "req-m-1", "revision": 1, "content_hash": "a" * 64}


class _Service:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def mission_detail(self, mission_id):
        return {"mission": {"id": mission_id, "status": "ACTIVE", "goal": "写文件"},
                "operation_workspace": {"state": "APPROVED", "requirements_ref": dict(REF),
                                        "criteria": [{"id": "c-user-1", "statement": "file:a.md", "required": True}]}}

    def amend_requirements(self, request):
        if any("action:" in str(c.get("statement")) for c in request["changes"]):
            raise OrchestrationRequestError("action_criteria_disabled", "连接器 file_publish 未启用")
        if any("sk-" in str(c.get("statement")) for c in request["changes"]):
            raise OrchestrationRequestError("secret_rejected", "内容里有像密钥的文本，未接受")
        if request["command_id"] not in {c["command_id"] for c in self.commands}:
            self.commands.append(dict(request))
        return {"requirements_revision": 2, "changes": {"added": ["c-user-2"], "rewritten": [], "removed": []}}


def _args(**over):
    return {"mission_id": "m", "expected_revision": 1, "reason": "用户要加一份文件",
            "changes": [{"op": "add", "statement": "file:b.md"}], **over}


def test_amend_sends_one_sourced_command_and_replays_it():
    service = _Service()
    first = amend_mission(lambda: service, _args(), run_id="run-1", call_id="call-1", permission_mode="auto")
    again = amend_mission(lambda: service, _args(), run_id="run-1", call_id="call-1", permission_mode="auto")
    assert first == again and first["requirements_revision"] == 2 and first["added"] == ["c-user-2"]
    [command] = service.commands  # 重放不重复写
    assert command["command_id"] == "chat-amend:run-1:call-1"
    assert command["expected_requirements_ref"] == REF
    assert command["source"] == {"kind": "MAIN_AGENT", "run_id": "run-1", "call_id": "call-1",
                                 "permission_mode": "auto"}


@pytest.mark.parametrize("args,code", [
    (_args(expected_revision=3), "AMEND_REQUIREMENTS_STALE"),
    (_args(changes=[{"op": "add", "statement": "action:file_publish.publish:b.md"}]), "action_criteria_disabled"),
    (_args(changes=[{"op": "add", "statement": "口令是 sk-live-1234567890abcdef"}]), "secret_rejected"),
    (_args(changes=[]), "invalid_arguments"),
    ({"mission_id": "m", "changes": [{"op": "add", "statement": "x"}]}, "invalid_arguments"),
])
def test_refusals_are_named_and_write_nothing(args, code):
    service = _Service()
    with pytest.raises(MissionStartRefused) as refused:
        amend_mission(lambda: service, args, run_id="run-1", call_id="call-1")
    assert refused.value.code == code and service.commands == []


def test_status_gives_the_current_requirements_and_the_tool_is_registered():
    status = mission_status(lambda: _Service(), {"mission_id": "m"})
    assert status["requirements"] == {"revision": 1, "criteria": [{"id": "c-user-1", "statement": "file:a.md"}]}
    assert "mission_amend" in PRODUCT_TOOL_NAMES and "mission_amend" in HOST_COMPOSED_TOOL_NAMES
    assert "mission_amend" in SDK_DIRECT_TOOL_KERNEL
    assert describe_call_zh("mission_amend", _args()) == "修改后台任务的要求：新增 1 条、改写 0 条、删除 0 条"
    assert MISSION_AMEND_SCHEMA["additionalProperties"] is False
    assert "core.mission_amend.v1" in (BACKEND / "main.py").read_text(encoding="utf-8")


def test_projection_passes_budget_and_drops_charter():
    view = {"through_seq": 1, "snapshot": {
        "mission": {"id": "m", "goal": "g", "success_criteria": ["file:a.md"], "status": "ACTIVE"},
        "budget_by_duty": [{"obligation_id": "d", "label": "整个任务", "depth": 0, "attempts": 2}],
        "unrefined_goals": [{"occurrence_id": "o", "task_id": "t", "label": "整理"}],
        "steps_no_longer_counting": [{"occurrence_id": "o1", "task_id": "t1", "acceptance_id": "a1",
                                      "requirements_revision": 1, "label": "写 a.md"}]}}
    detail = project_detail(view, blocked=())
    assert detail["budget_by_duty"] == view["snapshot"]["budget_by_duty"]
    assert detail["unrefined_goals"] == view["snapshot"]["unrefined_goals"]
    assert detail["steps_no_longer_counting"] == view["snapshot"]["steps_no_longer_counting"]
    assert "success_criteria" not in MISSION_FIELDS and "success_criteria" not in detail["mission"]


def test_host_reads_the_charter_only_at_the_door():
    """Host 里读建任务章程的只有建任务门口、建任务工具、技能目录的建任务入参与诊断的输入引用。"""
    allowed = {"deskpet/orchestration/service.py", "deskpet/orchestration/chat_tool.py",
               "deskpet/orchestration/skill_catalogue.py", "deskpet/orchestration/diagnostics.py"}
    pattern = re.compile(r"success_criteria")
    offenders = sorted(
        path.relative_to(BACKEND).as_posix() for path in (BACKEND / "deskpet" / "orchestration").rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
        and path.relative_to(BACKEND).as_posix() not in allowed)
    assert offenders == [], f"这些文件读了建任务时的章程，应改读现行要求（确认页）：{offenders}"


def test_the_goal_op_passes_the_door_and_comes_back_in_the_receipt():
    """第 2 批车道 L（H19）：改要求时允许改目标。目标文本不是一条要求：不按 pytest:/action: 查，
    Host 把 SDK 回执里的 goal {previous, current} 原样带回。"""
    class _GoalService(_Service):
        def amend_requirements(self, request):
            [change] = request["changes"]
            assert change == {"op": "goal", "statement": "改写一份更短的说明"}
            self.commands.append(dict(request))
            return {"requirements_revision": 2, "changes": {"added": [], "rewritten": [], "removed": []},
                    "goal": {"previous": "写文件", "current": "改写一份更短的说明"}}
    service = _GoalService()
    result = amend_mission(lambda: service, _args(changes=[{"op": "goal", "statement": "改写一份更短的说明"}]),
                           run_id="run-1", call_id="call-2", permission_mode="auto")
    assert result["goal"] == {"previous": "写文件", "current": "改写一份更短的说明"}
    assert describe_call_zh("mission_amend", _args(changes=[{"op": "goal", "statement": "x"}])) == \
        "修改后台任务的要求：新增 0 条、改写 0 条、删除 0 条、并换任务目标"
