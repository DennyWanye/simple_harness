# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""What the orchestration view may see (plan §3.3 ``projection.py``, HA-4).

The input is the SDK facade's ``MissionViewV1`` (snapshot + ``through_seq``); the output
is a whitelisted, bounded projection.  Text a model wrote is always marked
``{"text": …, "source": "model"}`` so the UI can never show it as the system's own words;
internal records (dispatch intents, agent configs, local storage paths, event payloads)
never leave.  A verification layer keeps its own status — ``NOT_REQUIRED`` is never turned
into a pass.  Approvals in a detail come from the snapshot (every state, with who
decided), joined with the action they bind; the pending list for the approval cards comes
from the Approval API.  ``ui_state`` is the P3.1 §3.4 vocabulary (请求已接收／排队／运行／
待验证／待人／UNKNOWN／正式交付), derived here once for the list and the detail.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MISSION_FIELDS = (
    "id",
    "goal",
    "status",
    "stop_reason",
    "created_at",
    "version",
    "budget",
    "allowed_tools",
    "untrusted_sources",
    "ui_state",
)
MODEL_TEXT_LIMIT = 2000
SUMMARY_LIMIT = 300
PARAMS_LIMIT = 600
MODEL_LAYERS = frozenset({"critic_review"})  # layers whose summary a model wrote

UI_STATES = (
    "received",
    "queued",
    "running",
    "verifying",
    "waiting_person",
    "unknown",
    "delivered",
    "failed",
    "cancelled",
)
_RUNNING_ATTEMPTS = frozenset({"CLAIMED", "DISPATCHED", "STARTED", "RUNNING"})
_VERIFYING_ATTEMPTS = frozenset({"SUBMITTED", "VERIFYING"})
_RECEIVED_MISSIONS = frozenset({"CREATED", "PLANNING"})


def model_text(value: Any, limit: int = MODEL_TEXT_LIMIT) -> dict[str, str]:
    return {"text": str(value or "")[:limit], "source": "model"}


def _system_text(value: Any, limit: int = SUMMARY_LIMIT) -> dict[str, str]:
    return {"text": str(value or "")[:limit], "source": "system"}


def _short(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    return str(value or "")[:limit]


def _bounded(value: Any, limit: int = PARAMS_LIMIT) -> Any:
    """A structured value kept as it is when small; otherwise a marked, cut preview."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "preview": encoded[:limit]}


def _pick(raw: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {key: raw[key] for key in fields if key in raw} if isinstance(raw, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, (list, tuple)) else []


def _source_approval(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("kind") == "source_change":
        return {"source_change": _pick(raw.get("binding"), (
            "operation", "path", "expected_version_hash", "version_hash", "old_revision", "kind", "reason",
        ))}
    return {}


def _sources(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every registered source version of a Mission and its lifecycle (read model only).

    2026-10-02 (strict citation option A): the document panel is gone; any Mission that
    carries reference material shows its sources and their register/supersede/revoke
    state here.
    """
    return [_pick(s, ("path", "version_hash", "kind", "trust", "registered_at", "superseded_by",
                      "revoked", "revision")) for s in _rows(snapshot.get("sources"))]


def ui_state(
    status: Any,
    *,
    attempt_statuses: Iterable[Any] = (),
    task_statuses: Iterable[Any] = (),
    waiting: bool = False,
    blocked: bool = False,
) -> str:
    """P3.1 §3.4: the state word the UI shows — never taken from a model saying "done"."""

    mission = str(status or "")
    if mission == "COMPLETED":
        return "delivered"
    if mission == "FAILED":
        return "failed"
    if mission == "CANCELLED":
        return "cancelled"
    if blocked:
        return "unknown"
    if waiting:
        return "waiting_person"
    attempts = {str(s) for s in attempt_statuses}
    if attempts & _RUNNING_ATTEMPTS:
        return "running"
    if attempts & _VERIFYING_ATTEMPTS:
        return "verifying"
    tasks = {str(s) for s in task_statuses}
    if mission == "ACTIVE" and tasks == {"COMPLETED"}:
        return "verifying"  # Task PASS still awaits the Mission's final judgment.
    if mission in _RECEIVED_MISSIONS and not attempts:
        return "received"
    return "queued"


def _mission(raw: Mapping[str, Any], state: str) -> dict[str, Any]:
    report = dict(raw.get("final_report") or {})
    return {
        "id": raw.get("id"),
        "goal": _short(raw.get("goal"), MODEL_TEXT_LIMIT),  # a person's words
        "status": raw.get("status"),
        "stop_reason": raw.get("stop_reason"),
        "created_at": raw.get("created_at"),
        "version": raw.get("version"),
        "budget": dict(raw.get("budget") or {}),
        "allowed_tools": list(raw.get("allowed_tools") or ()),
        "untrusted_sources": list(report.get("untrusted_sources") or ()),
        "ui_state": state,
    }


def _task(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "goal": model_text(raw.get("goal"), 600),  # a Planner wrote it
        "status": raw.get("status"),
        "kind": raw.get("kind"),
        "dependency_ids": list(raw.get("dependency_ids") or ()),
        "verification_policy": list(raw.get("verification_policy") or ()),
        "attempt_count": raw.get("attempt_count"),
        "failure_reason": raw.get("failure_reason"),
        "paused": bool(raw.get("paused")),
    }


def _attempt(raw: Mapping[str, Any]) -> dict[str, Any]:
    reserved = dict(raw.get("budget_reserved") or {})
    return {
        "id": raw.get("id"),
        "task_id": raw.get("task_id"),
        "status": raw.get("status"),
        "role": raw.get("role"),
        "model": raw.get("model"),
        "ordinal": raw.get("ordinal"),
        "created_at": raw.get("created_at"),
        "reserved_tokens": reserved.get("max_tokens"),
        "failure": None if raw.get("failure") is None else _short(raw.get("failure")),
    }


def _layer(raw: Mapping[str, Any]) -> dict[str, Any]:
    detail = raw.get("detail") if isinstance(raw.get("detail"), Mapping) else {}
    name = str(raw.get("layer") or "")
    summary = raw.get("summary", detail.get("summary"))
    return {
        "layer": raw.get("layer"),
        "status": raw.get("status"),  # PASS / FAIL / ERROR / NOT_REQUIRED / … as recorded
        "summary": model_text(summary, SUMMARY_LIMIT) if name in MODEL_LAYERS else _system_text(summary),
        "undeployed": bool(detail.get("undeployed")),
    }


def _final_rejection(raw: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """Live acceptance failures are separate from the frozen verification layers."""
    envelope = raw.get("envelope") or {}
    if (raw.get("verification_state") != "DONE" or raw.get("verdict") != "FAIL"
            or not envelope.get("attempt_id") or attempt.get("id") != envelope.get("attempt_id")
            or attempt.get("task_id") != envelope.get("task_id")
            or attempt.get("mission_id") != envelope.get("mission_id")):
        return None
    failure = attempt.get("failure")
    if not isinstance(failure, Mapping) or failure.get("reason") != "verification_failed":
        return None
    for item in _rows(failure.get("failures")):
        detail = item.get("detail")
        if item.get("status") not in {"FAIL", "ERROR"} or not isinstance(detail, Mapping):
            continue
        reason = detail.get("reason")
        if not isinstance(reason, str) or reason not in {
            "stale_source", "source_unavailable", "used_knowledge_stale",
        }:
            continue
        return {
            "reason": reason,
            "source_issues": [
                {key: value[:SUMMARY_LIMIT] for key in ("code", "reason", "path", "version")
                 if isinstance((value := issue.get(key)), str)}
                for issue in _rows(detail.get("source_current_issues"))[:20]
            ],
        }
    return None


def _result(raw: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    envelope = dict(raw.get("envelope") or {})
    return {
        "result_id": envelope.get("id"),
        "task_id": envelope.get("task_id"),
        "attempt_id": envelope.get("attempt_id"),
        "outcome": envelope.get("outcome"),
        "summary": model_text(envelope.get("summary")),
        "claims": [model_text((c or {}).get("content")) for c in envelope.get("claims") or ()][:20],
        "verdict": raw.get("verdict"),
        "verification_state": raw.get("verification_state"),
        "final_rejection": _final_rejection(raw, attempt),
        "verification_layers": [_layer(v) for v in raw.get("verifications") or ()],
    }


def _artifact(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {  # never ``storage_uri``: artifacts are read by id through the facade
        "id": raw.get("id"),
        "task_id": raw.get("task_id"),
        "attempt_id": raw.get("attempt_id"),
        "path": raw.get("path"),  # workspace-relative, as the Worker named it
        "content_hash": raw.get("content_hash"),
        "size_bytes": raw.get("size_bytes"),
        "verification_status": raw.get("verification_status"),
    }


def _action(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_key": raw.get("action_key"),
        "version": raw.get("version"),
        "state": raw.get("state"),
        "connector": raw.get("connector"),
        "operation": raw.get("operation"),
        "target": _short(raw.get("target"), PARAMS_LIMIT),
        "params_hash": raw.get("params_hash"),
        "reason": model_text(raw.get("reason")),
        # P3.2 (P32-15): what actually happened in the world, so the view can tell
        # "generated, not published yet" from "published" and show the bytes' identity
        **_action_receipt(raw.get("receipt")),
    }


def _action_receipt(raw: Any) -> dict[str, Any]:
    """The published file and the hash that was read back, or nothing yet."""

    if not isinstance(raw, Mapping):
        return {"published_path": None, "published_hash": None}
    after = raw.get("after") if isinstance(raw.get("after"), Mapping) else {}
    return {
        "published_path": _short(after.get("path"), PARAMS_LIMIT),
        "published_hash": after.get("content_hash"),
    }


def _comments(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"principal_id": c.get("principal_id"), "text": _short(c.get("text"), 600)}
        for c in raw.get("comments") or ()
        if isinstance(c, Mapping)
    ]


def _approval_summary(value: Any) -> dict[str, Any]:
    """A review request carries the verification layers; an action request a sentence.
    Either may hold model-written text, so the whole summary is marked as the model's.

    2026-09-29 真机：动作审批的摘要是结构化的（连接器/操作/目标/参数/理由），整个转成一段
    文字后卡片只能显示原始字典。保留要显示的字段；理由按记录的来源标注（系统按已批准效果
    写的是 system，其余一律当模型写的）。"""

    if isinstance(value, Mapping) and value.get("connector") and value.get("operation"):
        params = value.get("params") if isinstance(value.get("params"), Mapping) else {}
        previous = value.get("previous_attempt") if isinstance(value.get("previous_attempt"), Mapping) else None
        return {
            "connector": _short(value.get("connector"), 80),
            "operation": _short(value.get("operation"), 80),
            "target": _short(value.get("target"), PARAMS_LIMIT),
            "params": {"artifact_path": _short(params.get("artifact_path"), PARAMS_LIMIT)},
            "reason": _short(value.get("reason"), 600),
            "reason_source": "system" if value.get("reason_source") == "system" else "model",
            # 2026-10-03 真机：系统重交的卡片要写明上次为何没生效（阶段 B 裁决第 1、3 类）；结局与原因
            # 都是系统登记的固定码，不是模型写的话。
            **({"previous_attempt": {"attempt": previous.get("attempt"),
                                     "outcome": _short(previous.get("outcome"), 80),
                                     "reason": _short(previous.get("reason"), 300)}} if previous else {}),
        }
    if isinstance(value, Mapping) and isinstance(value.get("layers"), Sequence):
        parts = []
        for layer in value["layers"]:
            if isinstance(layer, Mapping):
                parts.append(f"{layer.get('layer')}: {layer.get('status')} — {_short(layer.get('summary'), 160)}")
        return model_text("；".join(parts), 600)
    return model_text(value, 600)


def project_approval(raw: Mapping[str, Any]) -> dict[str, Any]:
    """An item of the Approval API list (pending cards), the model-written text marked."""

    action = dict(raw.get("action") or {})
    reason = dict(action.get("reason") or {})
    return {
        "request_id": raw.get("request_id"),
        "kind": raw.get("kind"),
        "mission_id": raw.get("mission_id"),
        "task_id": raw.get("task_id"),
        "state": raw.get("state"),
        "level": raw.get("level"),
        "required_count": raw.get("required_count"),
        "grant_count": raw.get("grant_count"),
        "expires_at": raw.get("expires_at"),
        "topic": raw.get("topic"),
        "options": list(raw.get("options") or ()),
        "summary": _approval_summary(raw.get("summary")),
        "created_at": raw.get("created_at"),
        "action": None
        if not action
        else {
            "connector": action.get("connector"),
            "operation": action.get("operation"),
            "target": _short(action.get("target"), PARAMS_LIMIT),
            "params": _bounded(action.get("params")),
            "params_hash": action.get("params_hash"),
            "state": action.get("state"),
            "reason": model_text(reason.get("text")),
        },
        "comments": _comments(raw),
        **_source_approval(raw),
    }


def _snapshot_approval(raw: Mapping[str, Any], actions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    bound = actions.get(str(raw.get("subject_key"))) if raw.get("kind") == "action" else None
    return {
        "request_id": raw.get("request_id"),
        "kind": raw.get("kind"),
        "task_id": raw.get("task_id"),
        "state": raw.get("state"),
        "level": raw.get("level"),
        "topic": raw.get("topic"),
        "options": list(raw.get("options") or ()),
        "summary": _approval_summary(raw.get("summary")),
        "created_at": raw.get("created_at"),
        "closed_at": raw.get("closed_at"),
        "granted_by": list(raw.get("granted_by") or ()),
        "rejected_by": raw.get("rejected_by"),
        "decided_by": raw.get("decided_by"),
        "action": None if bound is None else _action(bound),
        "comments": _comments(raw),
        **_source_approval(raw),
    }


def project_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    """One timeline row: ids, type and time only — the payload never leaves, except the
    words of a person's comment (short)."""

    payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    event = {
        "seq": raw.get("seq"),
        "type": raw.get("type"),
        "created_at": raw.get("created_at"),
        "task_id": raw.get("task_id"),
        "attempt_id": raw.get("attempt_id"),
        "actor_type": raw.get("actor_type"),
    }
    if raw.get("type") == "HumanCommentAdded":
        event["summary"] = _short(payload.get("text"), SUMMARY_LIMIT)
    return event


def project_events(page: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": page.get("mission_id"),
        "events": [project_event(e) for e in page.get("events") or () if isinstance(e, Mapping)],
        "has_more": bool(page.get("has_more")),
        "through_seq": page.get("through_seq"),
    }


def project_detail(view: Mapping[str, Any], *, blocked: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    snapshot = dict(view.get("snapshot") or {})
    attempts_by_id = {a.get("id"): a for a in _rows(snapshot.get("attempts")) if a.get("id")}
    attempts = [_attempt(a) for a in snapshot.get("attempts") or ()]
    raw_actions = [dict(a) for a in snapshot.get("actions") or ()]
    latest_actions: dict[str, Mapping[str, Any]] = {}
    for action in raw_actions:  # the latest version of each action key
        key = str(action.get("action_key"))
        if key not in latest_actions or int(action.get("version") or 0) >= int(latest_actions[key].get("version") or 0):
            latest_actions[key] = action
    policy = dict(snapshot.get("mission_policy") or {})
    budget_usage = snapshot.get("budget_usage")
    ledger = budget_usage if isinstance(budget_usage, Mapping) else {}
    approvals = [_snapshot_approval(a, latest_actions) for a in snapshot.get("approvals") or ()]
    waiting_on = [dict(w) for w in snapshot.get("waiting_on") or ()]
    questions = [{"decision_id": row.get("decision_id"), "version": row.get("version"),
                  "state": row.get("state"),
                  "question": (row.get("request") or {}).get("payload", {}).get("question"),
                  "options": (row.get("request") or {}).get("payload", {}).get("options", []),
                  "blocking": (row.get("request") or {}).get("payload", {}).get("blocking", False),
                  "answer": (row.get("answer") or {}).get("answer")}
                 for row in _rows(snapshot.get("planning_questions"))]
    planning_authorizations = _rows(snapshot.get("planning_authorization_requests"))
    raw_mission = dict(snapshot.get("mission") or {})
    state = ui_state(
        raw_mission.get("status"),
        attempt_statuses=(a["status"] for a in attempts),
        task_statuses=(t.get("status") for t in snapshot.get("tasks") or ()),
        waiting=bool(waiting_on) or bool(planning_authorizations) or any(a.get("state") == "PENDING" for a in approvals) or any(q["state"] == "PENDING" and q["blocking"] for q in questions),
        blocked=bool(blocked),
    )
    return {
        "mission": _mission(raw_mission, state),
        "tasks": [_task(t) for t in snapshot.get("tasks") or ()],
        "attempts": attempts,
        "results": [_result(r, attempts_by_id.get((r.get("envelope") or {}).get("attempt_id"), {}))
                    for r in snapshot.get("results") or ()],
        "artifacts": [_artifact(a) for a in snapshot.get("artifacts") or ()],
        "actions": [_action(a) for a in latest_actions.values()],
        "approvals": approvals,
        "planning_questions": questions,
        "planning_authorization_requests": planning_authorizations,
        "operation_workspace": snapshot.get("operation_workspace"),
        # 阶段 E：预算去向（逐义务，含下级与被换掉的做法）与还没细化的目标——SDK 读时推出，原样透传
        "budget_by_duty": [dict(row) for row in snapshot.get("budget_by_duty") or ()],
        "unrefined_goals": [dict(row) for row in snapshot.get("unrefined_goals") or ()],
        "steps_no_longer_counting": [dict(row) for row in snapshot.get("steps_no_longer_counting") or ()],
        "waiting_on": waiting_on,
        "blocked": [dict(b) for b in blocked],
        "disputes": [dict(d) for d in snapshot.get("disputes") or ()],
        "mission_policy": {"version_id": policy.get("version_id"), "source": policy.get("source")},
        "usage": {
            "attempts": len(attempts),
            "reserved_tokens": ledger.get("reserved_tokens"),
            "settled_tokens": ledger.get("settled_tokens"),
            "ledger_version": ledger.get("version"),
        },
        "event_count": snapshot.get("event_count"),
        "through_seq": view.get("through_seq"),
        **({"sources": _sources(snapshot)} if _rows(snapshot.get("sources")) else {}),
    }


# ---------------------------------------------------------------------------
# 对外公开合同的投影（推后第 2 批 U03；Assurance §13.1 L350/L354、F13；HTN §17.2 L856）
#
# 下面这些读动词的回复和错误回执，出门前按 SDK 随包发布的公开合同核一遍；核过的才出门，
# 而且出门的是新拷贝，不是 SDK 交来的那个对象。不合合同 → ``ProtocolError``，``handlers``
# 回具名协议错 ``protocol_error``，不带数据、不带原回执，原因只进 Host 日志。
#
# 核法按合同族各用 SDK 指定的那一条：
# - Assurance：``agent_orchestrator.assurance.contracts`` 的 host-*-v1 Schema 与其核对器。
# - 执行图：边界 codec（第 2 批 T06 定的"执行图合同在生产代码里只走 codec"；codec 与
#   ``graph/schemas`` 里的 Schema 一致由 SDK 用例守住）。
# 前端 ``tauri-app/src/ws/orchestrationContracts.ts`` 直接 import 同一批 Schema 文件，表与这里同名。
#
# 推后第 3 批 U09（HTN §17.2）补上界面画得最多的四个读动词：执行图主画面与回合详情（执行图合同族，
# 走 codec），任务列表与任务详情（Host 任务投影，合同 ``host-mission-*-v1`` 放在 SDK 的 Host DTO 合同处，
# 走同一个 ``host-`` 核对器）。
# ---------------------------------------------------------------------------

PROTOCOL_ERROR = "protocol_error"
PROTOCOL_ERROR_TEXT = "收到的数据格式不对，没有显示，请稍后重新读取。"

#: 动词 → Assurance 回复合同名（错误回执一律 host-error-v1）
ASSURANCE_REPLIES = {
    "mission_assurance_snapshot": "host-response-v1",
    "mission_assurance_review": "host-review-response-v1",
    "mission_assurance_use_check": "host-use-response-v1",
}
ASSURANCE_ERROR = "host-error-v1"
#: 动词 → 执行图回复合同名（错误回执一律 taskgraph-error-v1）
TASKGRAPH_REPLIES = {
    "taskgraph.snapshot": "taskgraph-view-v1",
    "taskgraph.why_not_ready": "taskgraph-explanation-v1",
    "taskgraph.diff": "taskgraph-diff-v1",
    "taskgraph.convergence": "taskgraph-convergence-view-v2",
    "taskgraph.execution_snapshot": "taskgraph-execution-view-v1",
    "taskgraph.execution_detail": "taskgraph-execution-detail-v1",
}
TASKGRAPH_ERROR = "taskgraph-error-v1"
#: 动词 → Host 任务投影的合同名（推后第 3 批 U09）
MISSION_REPLIES = {
    "mission_list": "host-mission-list-v1",
    "mission_get": "host-mission-detail-v1",
}
CONTRACT_VERBS = frozenset(ASSURANCE_REPLIES) | frozenset(TASKGRAPH_REPLIES) | frozenset(MISSION_REPLIES)
#: 回执种类 → (回复里的字段名, 合同名)
ERROR_WIRES = {"assurance": ("assurance_error", ASSURANCE_ERROR), "taskgraph": ("taskgraph_error", TASKGRAPH_ERROR)}


class ProtocolError(Exception):
    """SDK 交来的东西不合公开合同。``code`` 让 ``handlers`` 按具名错误回给界面。"""

    code = PROTOCOL_ERROR

    def __init__(self, verb: str, contract: str, reason: str) -> None:
        super().__init__(PROTOCOL_ERROR_TEXT)
        self.verb = verb
        self.contract = contract
        self.reason = reason


def _taskgraph_codec(contract: str) -> Any:
    from agent_orchestrator.graph.notification_contracts import TaskGraphErrorV1
    from agent_orchestrator.graph.structural_diff import TaskGraphDiffV1
    from agent_orchestrator.graph.view_contracts import (
        TaskGraphConvergenceViewV2,
        TaskGraphExecutionDetailV1,
        TaskGraphExecutionViewV1,
        TaskGraphExplanationV1,
        TaskGraphViewV1,
    )

    return {
        "taskgraph-execution-view-v1": TaskGraphExecutionViewV1,
        "taskgraph-execution-detail-v1": TaskGraphExecutionDetailV1,
        "taskgraph-view-v1": TaskGraphViewV1,
        "taskgraph-explanation-v1": TaskGraphExplanationV1,
        "taskgraph-diff-v1": TaskGraphDiffV1,
        "taskgraph-convergence-view-v2": TaskGraphConvergenceViewV2,
        "taskgraph-error-v1": TaskGraphErrorV1,
    }[contract]


def _through_contract(verb: str, contract: str, body: Any) -> dict[str, Any]:
    if contract.startswith("host-"):
        from agent_orchestrator.assurance.contracts import ContractViolation, validate

        try:
            validate(contract, body)
            # 合同已逐字段核过（additionalProperties: false），这里只断开与 SDK 对象的引用
            return json.loads(json.dumps(body, ensure_ascii=False))
        except (ContractViolation, TypeError, ValueError) as error:
            raise ProtocolError(verb, contract, str(error)) from error
    from agent_orchestrator.contracts.models import ContractError

    try:
        return _taskgraph_codec(contract).from_json(body).to_json()
    except (ContractError, TypeError, ValueError, KeyError) as error:
        raise ProtocolError(verb, contract, str(error)) from error


def project_reply(verb: str, body: Any) -> dict[str, Any]:
    """有公开合同的读动词：回复核过合同后出一份新拷贝；不合 → ``ProtocolError``。"""

    contract = ASSURANCE_REPLIES.get(verb) or MISSION_REPLIES.get(verb) or TASKGRAPH_REPLIES[verb]
    return _through_contract(verb, contract, body)


def project_error(verb: str, contract: str, wire: Any) -> dict[str, Any]:
    """错误回执（``assurance_error`` 核 host-error-v1，``taskgraph_error`` 核 taskgraph-error-v1）。

    不分动词：只要 Host 往外挂这两种回执，就先核合同。"""

    return _through_contract(verb, contract, wire)


__all__ = (
    "CONTRACT_VERBS",
    "ERROR_WIRES",
    "MISSION_FIELDS",
    "MISSION_REPLIES",
    "PROTOCOL_ERROR",
    "PROTOCOL_ERROR_TEXT",
    "ProtocolError",
    "UI_STATES",
    "model_text",
    "project_approval",
    "project_detail",
    "project_event",
    "project_error",
    "project_events",
    "project_reply",
    "ui_state",
)
