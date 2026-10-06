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
MAX_SOURCES = 8
MAX_SOURCE_TEXT = 200_000

MISSION_START_DESCRIPTION = (
    "Start a background task (Mission) on the task orchestration page. Use it only when the user "
    "asks for work to be handed to a background task (for example '交给后台任务', '开一个编排任务', "
    "'放到任务编排里做'); do not use it for work you can finish in this chat. goal is what the task "
    "must achieve; success_criteria are checkable completion conditions, one per item. An item is "
    "plain text, or 'file:<relative path>' when that file must be delivered: after 'file:' write the "
    "path only (for example 'file:report.md') and say what the file must contain in goal or in a "
    "separate plain-text item, never inside the 'file:' item. sources (optional) attaches reference "
    "material the task must work from: each is {path, content} — a file name such as "
    "'requirements.md' and the full text; pass it when the user gives or points to material the "
    "task should read, and use mission_source_update later if that material changes. When the "
    "deliverables must follow the material, say so by name in goal and success_criteria (for example "
    "'prices and dates must match requirements.md') and do not copy concrete values from the material "
    "into them — copied values would contradict the material once it is replaced. The result "
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
        "sources": {"type": "array", "maxItems": MAX_SOURCES, "items": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False}},
    },
    "required": ["goal", "success_criteria"],
    "additionalProperties": False,
}


class MissionStartRefused(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code, self.retryable = code, retryable
        super().__init__(message)


def _source_path(value: Any) -> str:
    """资料路径与任务页同一写法：统一放在 ``sources/`` 下。"""
    if not isinstance(value, str) or not value.strip().strip("/"):
        raise MissionStartRefused("invalid_arguments", "资料的 path 必须是文件名，例如 requirements.md")
    path = value.strip().lstrip("/")
    return path if path.startswith("sources/") else "sources/" + path


def _source_kind(path: str) -> str:
    return "markdown" if path.lower().endswith(".md") else "text"


def _source_content(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_SOURCE_TEXT:
        raise MissionStartRefused("invalid_arguments", f"资料的 content 必须是非空文字（最多 {MAX_SOURCE_TEXT} 字）")
    return value


def _request(arguments: Mapping[str, Any], *, run_id: str, call_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    goal = arguments.get("goal")
    criteria = arguments.get("success_criteria")
    sources = arguments.get("sources", [])
    if set(arguments) - {"goal", "success_criteria", "sources"}:
        raise MissionStartRefused("invalid_arguments", "只接受 goal、success_criteria 与 sources")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > MAX_TEXT:
        raise MissionStartRefused("invalid_arguments", "goal 必须是非空文字")
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA
            or not all(isinstance(c, str) and c.strip() and len(c) <= MAX_TEXT for c in criteria)):
        raise MissionStartRefused("invalid_arguments", f"success_criteria 需要 1～{MAX_CRITERIA} 条非空文字")
    if (not isinstance(sources, list) or len(sources) > MAX_SOURCES
            or not all(isinstance(s, Mapping) and set(s) == {"path", "content"} for s in sources)):
        raise MissionStartRefused("invalid_arguments", f"sources 最多 {MAX_SOURCES} 份，每份是 path 与 content")
    attached = [{"path": _source_path(s["path"]), "content": _source_content(s["content"]),
                 "kind": _source_kind(str(s["path"]))} for s in sources]
    if len({s["path"] for s in attached}) != len(attached):
        raise MissionStartRefused("invalid_arguments", "sources 里的 path 不能重复")
    return ({"goal": goal.strip(), "success_criteria": [c.strip() for c in criteria],
             "idempotency_key": f"chat-mission:{run_id}:{call_id}"}, attached)


def start_mission(service_getter: Callable[[], Any], arguments: Mapping[str, Any], *,
                  run_id: str, call_id: str) -> dict[str, Any]:
    request, sources = _request(arguments, run_id=run_id, call_id=call_id)
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    status = service.status()
    if not status.get("available"):
        raise MissionStartRefused("orchestration_unavailable",
                                  "任务编排当前不可用：" + str(status.get("reason") or "原因未知"), retryable=True)
    from .service import OrchestrationRequestError

    try:
        receipt = (service.create_mission_with_sources({"mission": request, "sources": sources})
                   if sources else service.create_mission(request))
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
        "sources": [s["path"] for s in sources],
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
    "a publish), how many steps are done, which files were published, its current requirements "
    "(revision number and each entry's id — read them before mission_amend) and its reference "
    "material (each path with its current version_hash — read it before mission_source_update). "
    "If you do not know the mission_id, call mission_list first. Read-only: you cannot "
    "confirm or approve anything with it — the user does that themselves on the task card in this "
    "chat or on the task orchestration page."
)

MISSION_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"mission_id": {"type": "string"}},
    "required": ["mission_id"],
    "additionalProperties": False,
}

_STATUS_ZH = {"CREATED": "已创建，等确认", "PLANNING": "规划中", "ACTIVE": "进行中", "COMPLETED": "已完成",
              "FAILED": "未完成（已停止）", "CANCELLED": "已取消"}
_DONE_TASKS = frozenset({"COMPLETED", "SUCCEEDED", "ACCEPTED"})


def _approval_line(approval: Mapping[str, Any]) -> str:
    summary = approval.get("summary") if isinstance(approval.get("summary"), Mapping) else {}
    action = approval.get("action") if isinstance(approval.get("action"), Mapping) else {}
    target = str(summary.get("target") or action.get("target") or "")
    if (summary.get("connector") or action.get("connector")) == "file_publish":
        return f"等用户批准发布 {target}"
    if approval.get("kind") == "source_change":
        change = approval.get("source_change") if isinstance(approval.get("source_change"), Mapping) else {}
        return f"等用户批准资料变更 {change.get('path') or ''}"
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
    isolated = _isolation(detail.get("recovery_isolated"))
    return {
        "mission_id": mission_id.strip(),
        "status": status,
        "status_zh": _STATUS_ZH.get(status, status or "未知"),
        "goal": mission.get("goal"),
        # 现行要求（改要求前先读这里：版本号与每条的编号）
        "requirements": (None if not isinstance(workspace, Mapping) else {
            "revision": (workspace.get("requirements_ref") or {}).get("revision"),
            "criteria": [{"id": c.get("id"), "statement": c.get("statement")}
                         for c in workspace.get("criteria") or ()]}),
        # 现行资料（换资料前先读这里：路径与当前版本号）
        "sources": [{"path": s.get("path"), "version_hash": s.get("version_hash")}
                    for s in detail.get("sources") or ()
                    if s.get("revoked") is not True and not s.get("superseded_by")],
        "waiting_for": waiting,
        "steps": {"total": len(work), "done": sum(1 for t in work if t.get("status") in _DONE_TASKS)},
        "published": published,
        "stop_reason": mission.get("stop_reason") if status in {"FAILED", "CANCELLED"} else None,
        # 重启核对没通过、被隔离（不再推进）：带对不上的表名与一句给用户的话；没隔离为 None
        "recovery_isolated": isolated,
        "note": isolated["note"] if isolated else (
            "需要用户亲手确认/批准的事项在对话里的任务卡片和任务编排页上；你不能代为确认或批准。"
            if waiting else "目前不需要用户操作。"),
    }


def _isolation(value: Any) -> dict[str, Any] | None:
    """服务给的 ``recovery_isolated``（``{"tables": [...]}`` 或 None）→ 主对话回执：加上同一句话。"""
    if not isinstance(value, Mapping):
        return None
    from .service import RECOVERY_ISOLATED_NOTE

    return {"tables": list(value.get("tables") or ()), "note": RECOVERY_ISOLATED_NOTE}


# ------------------------------------------------------------------ mission_amend
# 阶段 E：用户中途改要求。和建任务同一条规矩——主 Agent 把用户的话整理成"增 / 改 / 删哪几条"，
# 人的把关在"确认完成要求"那一步（手动模式人点；自动模式纯内容要求由系统代确认，带 action: 的等人点）。

MISSION_AMEND_TOOL_NAME = "mission_amend"

MISSION_AMEND_DESCRIPTION = (
    "Amend the requirements of a running background task (Mission): add, rewrite or remove "
    "requirement entries. Use it only when the user explicitly asks to change what a background task "
    "must deliver. First call mission_status to read the current requirements (their revision number "
    "and each entry's id), then pass expected_revision and the changes: "
    "{op:'add', statement}, {op:'rewrite', criterion_id, statement}, {op:'remove', criterion_id} or "
    "{op:'goal', statement} (replace the task's goal text with the new statement). "
    "A statement is plain text, or 'file:<relative path>' when a file must be delivered: after 'file:' "
    "write the path only (for example 'file:pr-template.md') and put what the file must contain in a "
    "separate plain-text statement, never inside the 'file:' one. "
    "A task that is already finishing cannot be amended. After the amendment the task re-plans for the new requirements; "
    "steps already done under the old requirements may be redone. Requirements with 'action:' still "
    "wait for the user's confirmation on the task card."
)

MISSION_AMEND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mission_id": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "changes": {"type": "array", "minItems": 1, "maxItems": MAX_CRITERIA, "items": {
            "type": "object",
            "properties": {"op": {"type": "string", "enum": ["add", "rewrite", "remove", "goal"]},
                           "criterion_id": {"type": "string"}, "statement": {"type": "string"}},
            "required": ["op"], "additionalProperties": False}},
        "reason": {"type": "string"},
    },
    "required": ["mission_id", "expected_revision", "changes", "reason"],
    "additionalProperties": False,
}


def amend_mission(service_getter: Callable[[], Any], arguments: Mapping[str, Any], *,
                  run_id: str, call_id: str, permission_mode: str = "") -> dict[str, Any]:
    fields = {"mission_id", "expected_revision", "changes", "reason"}
    mission_id, expected = arguments.get("mission_id"), arguments.get("expected_revision")
    changes, reason = arguments.get("changes"), arguments.get("reason")
    if (set(arguments) != fields or not isinstance(mission_id, str) or not mission_id.strip()
            or type(expected) is not int or not isinstance(reason, str) or len(reason) > MAX_TEXT
            or not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CRITERIA
            or not all(isinstance(c, Mapping) and len(str(c.get("statement") or "")) <= MAX_TEXT for c in changes)):
        raise MissionStartRefused(
            "invalid_arguments", "需要 mission_id、expected_revision（整数）、changes（1～12 条）、reason")
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    from .service import OrchestrationRequestError

    try:
        detail = service.mission_detail(mission_id.strip())
        workspace = detail.get("operation_workspace")
        reference = dict((workspace or {}).get("requirements_ref") or {}) if isinstance(workspace, Mapping) else {}
        if int(reference.get("revision") or 0) != expected:
            raise MissionStartRefused(
                "AMEND_REQUIREMENTS_STALE",
                f"要求现在是第 {reference.get('revision')} 版，不是第 {expected} 版；先用 mission_status 读最新的再改")
        receipt = service.amend_requirements({
            "mission_id": mission_id.strip(), "command_id": f"chat-amend:{run_id}:{call_id}",
            "expected_requirements_ref": {k: reference[k] for k in ("id", "revision", "content_hash")},
            "changes": [dict(item) for item in changes], "reason": reason.strip(),
            "source": {"kind": "MAIN_AGENT", "run_id": run_id, "call_id": call_id,
                       "permission_mode": permission_mode}})
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    changed = receipt.get("changes") or {}
    return {
        "mission_id": mission_id.strip(),
        "requirements_revision": receipt.get("requirements_revision"),
        "added": list(changed.get("added") or ()), "rewritten": list(changed.get("rewritten") or ()),
        "removed": list(changed.get("removed") or ()),
        "goal": dict(receipt.get("goal") or {}) or None,
        "where": "任务编排页",
        "note": "要求已改为新一版；任务会按新要求重新规划。带 action: 的要求需要用户在任务卡片上确认。",
    }


# ------------------------------------------------------------------ mission_list
# 2026-10-05 用户决定：任务不一定是主 Agent 自己建的（任务页也能建），它要能查到有哪些任务。

MISSION_LIST_TOOL_NAME = "mission_list"

MISSION_LIST_DESCRIPTION = (
    "List the user's recent background tasks (Missions), newest first: mission_id, goal, status and "
    "how many approvals are waiting for the user. Use it when the user refers to a background task "
    "whose mission_id you do not have (for example one they created on the task orchestration page), "
    "then use mission_status with the id. Read-only."
)

MISSION_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
    "additionalProperties": False,
}


def mission_list(service_getter: Callable[[], Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    limit = arguments.get("limit", 10)
    if set(arguments) - {"limit"} or type(limit) is not int or not 1 <= limit <= 50:
        raise MissionStartRefused("invalid_arguments", "只接受 limit（1～50 的整数，可不填）")
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    from .service import OrchestrationRequestError

    try:
        rows = service.list_missions(limit=limit)
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    return {"missions": [
        {"mission_id": row.get("mission_id"), "goal": row.get("goal"), "status": row.get("status"),
         "status_zh": _STATUS_ZH.get(str(row.get("status")), str(row.get("status") or "未知")),
         "created_at": row.get("created_at"), "pending_approvals": row.get("pending_approvals"),
         "recovery_isolated": _isolation(row.get("recovery_isolated"))}
        for row in rows],
        "note": "按创建时间从新到旧；用 mission_status 看某一个任务的进度、要求与资料。"}


# ------------------------------------------------------------------ mission_source_update
# 2026-10-05 用户决定：任务跑到一半资料出了新版本，用户在对话里说一声就行。主 Agent 只是把新正文
# 交上去；变更照旧生成一条等人批准的申请（任务卡片 / 任务编排页），批准后系统让用到旧版的步骤失效重做。

MISSION_SOURCE_UPDATE_TOOL_NAME = "mission_source_update"

MISSION_SOURCE_UPDATE_DESCRIPTION = (
    "Replace one piece of a background task's reference material with a new version. Use it only "
    "when the user says material a running task works from has changed and gives the new text. "
    "First call mission_status to read the task's sources (each path and its current version_hash), "
    "then pass that path, expected_version_hash and the full new content. The change does not take "
    "effect by itself: it becomes a request the user approves on the task card or the task "
    "orchestration page; after approval the task re-plans, and steps done from the old version may be "
    "redone. You cannot approve it."
)

MISSION_SOURCE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mission_id": {"type": "string"},
        "path": {"type": "string"},
        "expected_version_hash": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["mission_id", "path", "expected_version_hash", "content"],
    "additionalProperties": False,
}


def update_mission_source(service_getter: Callable[[], Any], arguments: Mapping[str, Any], *,
                          run_id: str, call_id: str) -> dict[str, Any]:
    mission_id, expected = arguments.get("mission_id"), arguments.get("expected_version_hash")
    if (set(arguments) != {"mission_id", "path", "expected_version_hash", "content"}
            or not isinstance(mission_id, str) or not mission_id.strip()
            or not isinstance(expected, str) or not expected.strip()):
        raise MissionStartRefused(
            "invalid_arguments", "需要 mission_id、path、expected_version_hash（mission_status 给出的）与 content")
    path, content = _source_path(arguments.get("path")), _source_content(arguments.get("content"))
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    from .service import OrchestrationRequestError

    try:
        detail = service.mission_detail(mission_id.strip())
        current = [s for s in detail.get("sources") or ()
                   if s.get("path") == path and s.get("revoked") is not True and not s.get("superseded_by")]
        if not current:
            raise MissionStartRefused("SOURCE_UNKNOWN", f"这个任务没有现行资料 {path}；先用 mission_status 看它有哪些资料")
        if current[0].get("version_hash") != expected.strip():
            raise MissionStartRefused(
                "SOURCE_VERSION_STALE", f"{path} 的现行版本已不是你读到的那一版；先用 mission_status 读最新的再换")
        receipt = service.source_command("supersede", {
            "mission_id": mission_id.strip(), "path": path, "content": content,
            "kind": str(current[0].get("kind") or _source_kind(path)),
            "expected_version_hash": expected.strip(),
            "idempotency_key": f"chat-source:{run_id}:{call_id}"})
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    return {
        "mission_id": mission_id.strip(), "path": path,
        "new_version_hash": receipt.get("version_hash"), "state": receipt.get("state"),
        "approval_request_id": receipt.get("request_id"),
        "where": "任务编排页",
        "note": "资料变更已提交，等用户在任务卡片或任务编排页批准；批准后任务会按新版资料重新规划。你不能代为批准。",
    }


# ------------------------------------------------------------------ method_library
# 阶段 C3：全库做法（以前的任务交付成功、审阅员判为可复用的做法，给以后的任务当先例）。
# 主 Agent 可以替用户列出来、按用户的话退役一条；确认规矩与建任务、改要求相同。

METHOD_LIBRARY_TOOL_NAME = "method_library"

METHOD_LIBRARY_DESCRIPTION = (
    "The library of reusable methods: ways of breaking a goal into steps that earlier background "
    "tasks delivered with and the reviewer judged reusable; later tasks' planners see them as "
    "precedents. action='list' shows every entry (id, one line of purpose, state, how many tasks "
    "blamed it). action='retire' takes one entry out of the list so new tasks no longer see it — "
    "use it only when the user explicitly asks to retire that method; pass entry_id and the user's "
    "reason. Tasks already running are not affected. Read-only otherwise: entries are added only by "
    "the system when a task is delivered."
)

METHOD_LIBRARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "retire"]},
        "entry_id": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def method_library(service_getter: Callable[[], Any], arguments: Mapping[str, Any], *,
                   run_id: str, call_id: str) -> dict[str, Any]:
    action = arguments.get("action")
    entry_id, reason = arguments.get("entry_id"), arguments.get("reason")
    if (action not in {"list", "retire"} or not set(arguments) <= {"action", "entry_id", "reason"}
            or (action == "list" and set(arguments) != {"action"})
            or (action == "retire" and (not isinstance(entry_id, str) or not entry_id.strip()
                                        or not isinstance(reason, str) or not reason.strip()
                                        or len(reason) > MAX_TEXT))):
        raise MissionStartRefused(
            "invalid_arguments", "action 为 list（不带别的参数）或 retire（带 entry_id 与 reason）")
    service = service_getter()
    if service is None:
        raise MissionStartRefused("orchestration_unavailable", "任务编排服务没有启动", retryable=True)
    from .service import OrchestrationRequestError

    try:
        if action == "list":
            entries = service.method_library()["entries"]
            return {"entries": [
                {"entry_id": item["entry_id"], "purpose": item["purpose"], "goal_type": item["goal_type_id"],
                 "state": item["state"], "source_mission_id": item["source_mission_id"],
                 "blamed_by_tasks": len(item["blamed_by_missions"]),
                 "retired_by": item["retired_by"], "retired_reason": item["retired_reason"]}
                for item in entries],
                "note": "LISTED 的条目会列给新任务的规划器当先例；RETIRED 的不再列出。"}
        receipt = service.retire_library_entry({
            "entry_id": entry_id.strip(), "command_id": f"chat-method-retire:{run_id}:{call_id}",
            "reason": reason.strip()})
    except OrchestrationRequestError as error:
        raise MissionStartRefused(str(error.code), str(error)) from error
    return {"entry_id": receipt["entry_id"], "purpose": receipt["purpose"], "state": "RETIRED",
            "note": "这条全库做法已退役，之后不再列给新任务；已经在跑的任务不受影响。"}


__all__ = ("MISSION_LIST_DESCRIPTION", "MISSION_LIST_SCHEMA", "MISSION_LIST_TOOL_NAME", "mission_list",
           "MISSION_SOURCE_UPDATE_DESCRIPTION", "MISSION_SOURCE_UPDATE_SCHEMA",
           "MISSION_SOURCE_UPDATE_TOOL_NAME", "update_mission_source",
           "MISSION_AMEND_DESCRIPTION", "MISSION_AMEND_SCHEMA", "MISSION_AMEND_TOOL_NAME", "amend_mission",
           "METHOD_LIBRARY_DESCRIPTION", "METHOD_LIBRARY_SCHEMA", "METHOD_LIBRARY_TOOL_NAME", "method_library",
           "MISSION_START_DESCRIPTION", "MISSION_START_SCHEMA", "MISSION_START_TOOL_NAME",
           "MISSION_STATUS_DESCRIPTION", "MISSION_STATUS_SCHEMA", "MISSION_STATUS_TOOL_NAME",
           "MissionStartRefused", "mission_status", "start_mission")
