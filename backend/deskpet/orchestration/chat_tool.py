# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``mission_start``: the chat Agent starts a background Mission (NEXT-TG-1.0 §9).

The tool calls the very same ``OrchestrationService.create_mission`` the task page
uses, so the Mission gets the same door checks, deployment defaults (budget, context
profile), hierarchical root and strict TaskGraph requirement.  Nothing is decided
here that the task page does not decide: content-only completion requirements are
auto-confirmed by the service as usual, anything that publishes or sends still waits
for the person on the task page.

The idempotency key is derived from the chat Run and the tool call, so a replayed
call returns the Mission it created the first time instead of creating another.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

MISSION_START_TOOL_NAME = "mission_start"
MAX_CRITERIA = 12
MAX_TEXT = 4000

MISSION_START_DESCRIPTION = (
    "Start a background task (Mission) on the task orchestration page. Use it only when the user "
    "asks for work to be handed to a background task (for example '交给后台任务', '开一个编排任务', "
    "'放到任务编排里做'); do not use it for work you can finish in this chat. goal is what the task "
    "must achieve; success_criteria are checkable completion conditions, one per item. The result "
    "gives the mission_id; progress, approvals and results are on the task orchestration page. "
    "Publishing or other external actions still wait for the user's confirmation there. "
    "To have a produced file published into the user's authorized publish directory, add one criterion "
    "'action:file_publish.publish:<file name>' per file (for example 'action:file_publish.publish:README.md'); "
    "the system then requires a step that writes that file and publishes it after the user approves. "
    "A task card appears in this chat; the user confirms and approves there. Use mission_status to read "
    "progress and results."
)

MISSION_START_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "success_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                             "maxItems": MAX_CRITERIA},
    },
    "required": ["goal", "success_criteria"],
    "additionalProperties": False,
}


class MissionStartRefused(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(message)


def _request(arguments: Mapping[str, Any], *, run_id: str, call_id: str) -> dict[str, Any]:
    goal = arguments.get("goal")
    criteria = arguments.get("success_criteria")
    if set(arguments) - {"goal", "success_criteria"}:
        raise MissionStartRefused("invalid_arguments", "只接受 goal 与 success_criteria")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > MAX_TEXT:
        raise MissionStartRefused("invalid_arguments", "goal 必须是非空文字")
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA
            or not all(isinstance(c, str) and c.strip() and len(c) <= MAX_TEXT for c in criteria)):
        raise MissionStartRefused("invalid_arguments", f"success_criteria 需要 1～{MAX_CRITERIA} 条非空文字")
    return {"goal": goal.strip(), "success_criteria": [c.strip() for c in criteria],
            "idempotency_key": f"chat-mission:{run_id}:{call_id}"}


def start_mission(service_getter: Callable[[], Any], arguments: Mapping[str, Any], *,
                  run_id: str, call_id: str) -> dict[str, Any]:
    request = _request(arguments, run_id=run_id, call_id=call_id)
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    status = service.status()
    if not status.get("available"):
        raise MissionStartRefused("orchestration_unavailable",
                                  "任务编排当前不可用：" + str(status.get("reason") or "原因未知"), retryable=True)
    from .service import OrchestrationRequestError

    try:
        receipt = service.create_mission(request)
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    mission_id = str(receipt["mission_id"])
    state = ""
    try:
        detail = service.mission_detail(mission_id)
        state = str((detail.get("mission") or {}).get("status") or "")
    except Exception:  # noqa: BLE001 - the Mission exists; its status is a courtesy
        state = ""
    return {
        "mission_id": mission_id,
        "created": bool(receipt.get("created")),
        "status": state,
        "where": "任务编排页",
        "note": ("已在后台创建任务；进度、需要你确认的完成要求与发布批准都在任务编排页。"
                 if receipt.get("created") else "这次调用对应的任务早已创建，没有重复创建。"),
    }


# ------------------------------------------------------------------ mission_status
# 2026-09-29：编排的最终使用者是主 Agent——它要能在对话里告诉用户任务走到哪、等谁、结果在哪。
# 只读：确认完成要求、批准发布都必须由人亲手点（对话里的任务卡片或任务编排页），模型不能代批。

MISSION_STATUS_TOOL_NAME = "mission_status"

MISSION_STATUS_DESCRIPTION = (
    "Read the current state of a background task (Mission) started with mission_start: its status, "
    "what it is waiting for (for example the user confirming completion requirements or approving "
    "a publish), how many steps are done, and which files were published. Read-only: you cannot "
    "confirm or approve anything with it — the user does that themselves on the task card in this "
    "chat or on the task orchestration page."
)

MISSION_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"mission_id": {"type": "string"}},
    "required": ["mission_id"],
    "additionalProperties": False,
}

_STATUS_ZH = {"CREATED": "已创建，等确认", "ACTIVE": "进行中", "COMPLETED": "已完成",
              "FAILED": "未完成（已停止）", "CANCELLED": "已取消"}
_DONE_TASKS = frozenset({"COMPLETED", "SUCCEEDED", "ACCEPTED"})


def _approval_line(approval: Mapping[str, Any]) -> str:
    summary = approval.get("summary") if isinstance(approval.get("summary"), Mapping) else {}
    action = approval.get("action") if isinstance(approval.get("action"), Mapping) else {}
    target = str(summary.get("target") or action.get("target") or "")
    if (summary.get("connector") or action.get("connector")) == "file_publish":
        return f"等用户批准发布 {target}"
    return f"等用户处理审批（{approval.get('kind') or '未知'}{'：' + target if target else ''}）"


def mission_status(service_getter: Callable[[], Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    mission_id = arguments.get("mission_id")
    if set(arguments) - {"mission_id"} or not isinstance(mission_id, str) or not mission_id.strip():
        raise MissionStartRefused("invalid_arguments", "只接受 mission_id（mission_start 返回的那个）")
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    from .service import OrchestrationRequestError

    try:
        detail = service.mission_detail(mission_id.strip())
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    mission = detail.get("mission") or {}
    status = str(mission.get("status") or "")
    waiting: list[str] = []
    workspace = detail.get("operation_workspace")
    if isinstance(workspace, Mapping) and workspace.get("state") == "CONFIRMATION_REQUIRED":
        waiting.append("等用户确认完成要求")
    waiting += [_approval_line(a) for a in detail.get("approvals") or () if a.get("state") == "PENDING"]
    waiting += [f"等用户回答规划问题：{q.get('question')}" for q in detail.get("planning_questions") or ()
                if q.get("state") == "PENDING" and q.get("question")]
    work = [t for t in detail.get("tasks") or () if t.get("kind") in (None, "work")]
    published = [{"target": a.get("target"), "published_path": a.get("published_path")}
                 for a in detail.get("actions") or ()
                 if a.get("state") == "SUCCEEDED" and a.get("published_path")]
    return {
        "mission_id": mission_id.strip(),
        "status": status,
        "status_zh": _STATUS_ZH.get(status, status or "未知"),
        "goal": mission.get("goal"),
        "waiting_for": waiting,
        "steps": {"total": len(work), "done": sum(1 for t in work if t.get("status") in _DONE_TASKS)},
        "published": published,
        "stop_reason": mission.get("stop_reason") if status in {"FAILED", "CANCELLED"} else None,
        "note": ("需要用户亲手确认/批准的事项在对话里的任务卡片和任务编排页上；你不能代为确认或批准。"
                 if waiting else "目前不需要用户操作。"),
    }


__all__ = ("MISSION_START_DESCRIPTION", "MISSION_START_SCHEMA", "MISSION_START_TOOL_NAME",
           "MISSION_STATUS_DESCRIPTION", "MISSION_STATUS_SCHEMA", "MISSION_STATUS_TOOL_NAME",
           "MissionStartRefused", "mission_status", "start_mission")
