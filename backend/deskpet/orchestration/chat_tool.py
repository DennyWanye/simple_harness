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
    "Publishing or other external actions still wait for the user's confirmation there."
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


__all__ = ("MISSION_START_DESCRIPTION", "MISSION_START_SCHEMA", "MISSION_START_TOOL_NAME",
           "MissionStartRefused", "start_mission")
