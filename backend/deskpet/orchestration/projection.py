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
    "success_criteria",
    "status",
    "stop_reason",
    "created_at",
    "version",
    "budget",
    "allowed_tools",
    "graph_version",
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


def ui_state(
    status: Any,
    *,
    attempt_statuses: Iterable[Any] = (),
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
    if mission in _RECEIVED_MISSIONS and not attempts:
        return "received"
    return "queued"


def _mission(raw: Mapping[str, Any], graph_version: int, state: str) -> dict[str, Any]:
    report = dict(raw.get("final_report") or {})
    return {
        "id": raw.get("id"),
        "goal": _short(raw.get("goal"), MODEL_TEXT_LIMIT),  # a person's words
        "success_criteria": list(raw.get("success_criteria") or ()),
        "status": raw.get("status"),
        "stop_reason": raw.get("stop_reason"),
        "created_at": raw.get("created_at"),
        "version": raw.get("version"),
        "budget": dict(raw.get("budget") or {}),
        "allowed_tools": list(raw.get("allowed_tools") or ()),
        "graph_version": graph_version,
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
    summary = raw.get("summary")
    return {
        "layer": raw.get("layer"),
        "status": raw.get("status"),  # PASS / FAIL / ERROR / NOT_REQUIRED / … as recorded
        "summary": model_text(summary, SUMMARY_LIMIT) if name in MODEL_LAYERS else _system_text(summary),
        "undeployed": bool(detail.get("undeployed")),
    }


def _result(raw: Mapping[str, Any]) -> dict[str, Any]:
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
    }


def _comments(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"principal_id": c.get("principal_id"), "text": _short(c.get("text"), 600)}
        for c in raw.get("comments") or ()
        if isinstance(c, Mapping)
    ]


def _approval_summary(value: Any) -> dict[str, str]:
    """A review request carries the verification layers; an action request a sentence.
    Either may hold model-written text, so the whole summary is marked as the model's."""

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
    attempts = [_attempt(a) for a in snapshot.get("attempts") or ()]
    raw_actions = [dict(a) for a in snapshot.get("actions") or ()]
    latest_actions: dict[str, Mapping[str, Any]] = {}
    for action in raw_actions:  # the latest version of each action key
        key = str(action.get("action_key"))
        if key not in latest_actions or int(action.get("version") or 0) >= int(latest_actions[key].get("version") or 0):
            latest_actions[key] = action
    policy = dict(snapshot.get("mission_policy") or {})
    approvals = [_snapshot_approval(a, latest_actions) for a in snapshot.get("approvals") or ()]
    waiting_on = [dict(w) for w in snapshot.get("waiting_on") or ()]
    raw_mission = dict(snapshot.get("mission") or {})
    state = ui_state(
        raw_mission.get("status"),
        attempt_statuses=(a["status"] for a in attempts),
        waiting=bool(waiting_on) or any(a.get("state") == "PENDING" for a in approvals),
        blocked=bool(blocked),
    )
    return {
        "mission": _mission(raw_mission, int(view.get("graph_version") or 0), state),
        "tasks": [_task(t) for t in snapshot.get("tasks") or ()],
        "attempts": attempts,
        "results": [_result(r) for r in snapshot.get("results") or ()],
        "artifacts": [_artifact(a) for a in snapshot.get("artifacts") or ()],
        "actions": [_action(a) for a in latest_actions.values()],
        "approvals": approvals,
        "waiting_on": waiting_on,
        "blocked": [dict(b) for b in blocked],
        "graph_changes": len(snapshot.get("graph_changes") or ()),
        "conflicts": [
            {
                "conflict_id": c.get("conflict_id"),
                "state": c.get("state"),
                "key": c.get("key"),
                "deferred_reason": c.get("deferred_reason"),
            }
            for c in snapshot.get("conflicts") or ()
        ],
        "mission_policy": {"version_id": policy.get("version_id"), "source": policy.get("source")},
        "usage": {
            "attempts": len(attempts),
            "reserved_tokens": sum(int(a.get("reserved_tokens") or 0) for a in attempts),
            "amount_micros": None,  # no DeepSeek price table is injected: unpriced, never 0
            "priced": False,
        },
        "event_count": snapshot.get("event_count"),
        "through_seq": view.get("through_seq"),
    }


__all__ = (
    "MISSION_FIELDS",
    "UI_STATES",
    "model_text",
    "project_approval",
    "project_detail",
    "project_event",
    "project_events",
    "ui_state",
)
