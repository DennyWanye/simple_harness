# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Control-channel protocol of the orchestration view (plan §3.4).

``handle(service, type, payload)`` is a pure dispatcher the ``/ws/control`` loop awaits;
it never raises into the socket loop.  Every request answers
``<type>_response {request_id, ok, data | error_code, error}``.  There is no message that
changes the policy library (HA-7).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

MESSAGE_TYPES = (
    "orchestration_status",
    "mission_create",
    "mission_create_with_sources",
    "mission_operation_completion_approve",
    "mission_planning_answer",
    "mission_planning_authorization",
    "mission_operation_intent_submit",
    "mission_operation_intent_status",
    "mission_source_register",
    "mission_source_supersede",
    "mission_source_revoke",
    "mission_citation_read",
    "mission_list",
    "mission_get",
    "taskgraph.snapshot",
    "taskgraph.why_not_ready",
    "taskgraph.diff",
    "taskgraph.convergence",
    "taskgraph.execution_snapshot",
    "taskgraph.execution_detail",
    "mission_assurance_snapshot",
    "mission_assurance_review",
    "mission_assurance_use_check",
    "mission_events",
    "mission_cancel",
    "mission_approval_list",
    "mission_approval_decide",
    "mission_takeover",
    "mission_comment",
    "mission_artifact_read",
    "mission_diagnostics",
    "mission_support_export",
    "orchestration_policy_status",
    "orchestration_storage_get",
    "agent_runtime_request",
    "agent_skill_evaluation_mission",
    "agent_skill_evaluation_dispatch",
    "agent_skill_request",
    "orchestration_skill_catalogue",
    "orchestration_skill_install_file",
    "orchestration_skill_lifecycle",
)


def _ok(msg_type: str, request_id: Any, data: Any) -> dict[str, Any]:
    return {
        "type": f"{msg_type}_response",
        "payload": {"request_id": request_id, "ok": True, "data": data},
    }


def _error(msg_type: str, request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "type": f"{msg_type}_response",
        "payload": {"request_id": request_id, "ok": False, "error_code": code, "error": message},
    }


def _text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name, "")
    return value if isinstance(value, str) else ""


async def handle(
    service: Any, msg_type: str, payload: Any, *, request_id: Any = None
) -> dict[str, Any]:
    if payload is not None and not isinstance(payload, Mapping):
        # review P2-6: answered, never raised into the shared control socket loop
        return _error(msg_type, request_id, "invalid_request", "payload must be an object")
    body = dict(payload or {})
    # Planning authorization has its own durable request_id. It is not the
    # transport correlation ID and must reach the SDK issuer unchanged.
    if msg_type.startswith("mission_assurance_"):
        # The Assurance DTOs carry ``request_id`` inside the contract body (host-*-request-v1);
        # it doubles as the transport correlation and must reach the SDK unchanged.
        request_id = body.get("request_id", request_id)
    elif msg_type != "mission_planning_authorization":
        request_id = body.pop("request_id", request_id)
    if msg_type == "orchestration_status":
        if service is None:
            return _ok(msg_type, request_id, {"available": False, "state": "absent", "reason": "编排服务未启动"})
        return _ok(msg_type, request_id, service.status())
    status = {} if service is None else service.status()
    # review P2-1: a degraded loop still answers reads, cancel, decisions and takeovers so a
    # person can stop the damage; the service itself refuses new Missions while degraded
    if status.get("state") not in ("available", "degraded"):
        reason = str(status.get("reason") or "编排服务不可用")
        return _error(msg_type, request_id, "orchestration_unavailable", reason)
    action = _ACTIONS.get(msg_type)
    if action is None:
        return _error(msg_type, request_id, "invalid_request", f"unknown message {msg_type!r}")
    try:
        data = action(service, body)
        if hasattr(data, "__await__"):
            data = await data  # type: ignore[misc]
        return _ok(msg_type, request_id, data)
    except Exception as error:  # the socket loop must never see an exception
        from .taskgraph import TaskGraphRequestError
        if isinstance(error, TaskGraphRequestError):
            response = _error(msg_type, request_id, error.code, str(error))
            response["payload"]["taskgraph_error"] = error.wire
            return response
        from .assurance import AssuranceRequestError
        if isinstance(error, AssuranceRequestError):
            response = _error(msg_type, request_id, error.code, str(error))
            response["payload"]["assurance_error"] = error.wire
            return response
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            return _error(msg_type, request_id, code, str(error))
        logger.exception("orchestration handler failed", extra={"msg_type": msg_type})
        return _error(msg_type, request_id, "internal_error", "编排服务处理请求时出错")


def _create(service: Any, body: Mapping[str, Any]) -> Any:
    return service.create_mission(dict(body))


def _create_with_sources(service: Any, body: Mapping[str, Any]) -> Any:
    return service.create_mission_with_sources(dict(body))


def _completion_approve(service: Any, body: Mapping[str, Any]) -> Any:
    return service.approve_operation_completion_spec(dict(body))


def _operation_submit(service: Any, body: Mapping[str, Any]) -> Any:
    return service.submit_operation_intent(dict(body))


def _operation_status(service: Any, body: Mapping[str, Any]) -> Any:
    if set(body) != {"intent_id"} or not _text(body, "intent_id"):
        from .service import OrchestrationRequestError
        raise OrchestrationRequestError("invalid_request", "需要 intent_id")
    return service.operation_intent_status(_text(body, "intent_id"))


def _source_register(service: Any, body: Mapping[str, Any]) -> Any:
    return service.source_command("register", dict(body))


def _source_supersede(service: Any, body: Mapping[str, Any]) -> Any:
    return service.source_command("supersede", dict(body))


def _source_revoke(service: Any, body: Mapping[str, Any]) -> Any:
    return service.source_command("revoke", dict(body))


async def _citation(service: Any, body: Mapping[str, Any]) -> Any:
    result = service.citation_read(dict(body))
    # The ignored-userdata fixture gives native clicks a reproducible in-flight
    # window. Delay delivery only; the real receipt-bound read above is unchanged.
    if service.status().get("test_scenario") == "document-ui" and body.get("offset", 0) == 0:
        await asyncio.sleep(1.5)
    return result


def _list(service: Any, body: Mapping[str, Any]) -> Any:
    limit = body.get("limit", 50)
    return {"missions": service.list_missions(limit=limit if isinstance(limit, int) else 50)}


def _get(service: Any, body: Mapping[str, Any]) -> Any:
    return service.mission_detail(_text(body, "mission_id"))


def _events(service: Any, body: Mapping[str, Any]) -> Any:
    return service.events(
        _text(body, "mission_id"),
        after_seq=body.get("after_seq", 0),
        limit=body.get("limit", 50),
    )


def _cancel(service: Any, body: Mapping[str, Any]) -> Any:
    return service.cancel_mission(_text(body, "mission_id"))


def _approvals(service: Any, body: Mapping[str, Any]) -> Any:
    mission_id = body.get("mission_id")
    return {"approvals": service.approvals(mission_id if isinstance(mission_id, str) else None)}


def _decide(service: Any, body: Mapping[str, Any]) -> Any:
    # ``approval_id``, not ``request_id``: the envelope's ``request_id`` pairs a request with
    # its response and would collide with an approval request's own id
    return service.decide(
        _text(body, "approval_id"),
        _text(body, "decision"),
        reason=_text(body, "reason"),
        note=_text(body, "note"),
        ruling=_text(body, "ruling"),
        basis=_text(body, "basis"),
    )


def _takeover(service: Any, body: Mapping[str, Any]) -> Any:
    return service.takeover(
        _text(body, "task_id"), _text(body, "action"), basis=_text(body, "basis"), note=_text(body, "note")
    )


def _comment(service: Any, body: Mapping[str, Any]) -> Any:
    return service.comment(_text(body, "target_id"), _text(body, "text"))


def _artifact(service: Any, body: Mapping[str, Any]) -> Any:
    return service.artifact_read(_text(body, "artifact_id"))


def _policy(service: Any, body: Mapping[str, Any]) -> Any:
    return service.policy_status()


def _diagnostics(service: Any, body: Mapping[str, Any]) -> Any:
    return service.mission_diagnostics(dict(body))


def _support_export(service: Any, body: Mapping[str, Any]) -> Any:
    return service.mission_diagnostics(dict(body), export=True)


_ACTIONS: dict[str, Callable[[Any, Mapping[str, Any]], Any | Awaitable[Any]]] = {
    "taskgraph.snapshot": lambda service, body: service.taskgraph_read("snapshot", body),
    "taskgraph.why_not_ready": lambda service, body: service.taskgraph_read("why_not_ready", body),
    "taskgraph.diff": lambda service, body: service.taskgraph_read("diff", body),
    "taskgraph.convergence": lambda service, body: service.taskgraph_read("convergence", body),
    # NEXT-TG-1.0 §8：执行过程（尝试/审阅/修补/规划/操作）走 SDK 正式接口，Host 不再直读 SDK 表
    "taskgraph.execution_snapshot": lambda service, body: service.taskgraph_read("execution_snapshot", body),
    "taskgraph.execution_detail": lambda service, body: service.taskgraph_read("execution_detail", body),
    "mission_assurance_snapshot": lambda service, body: service.assurance_read("snapshot", body),
    "mission_assurance_review": lambda service, body: service.assurance_read("review", body),
    "mission_assurance_use_check": lambda service, body: service.assurance_read("use_check", body),
    "mission_create": _create,
    "mission_create_with_sources": _create_with_sources,
    "mission_operation_completion_approve": _completion_approve,
    "mission_planning_authorization": lambda service, body: service.planning_authorization(dict(body)),
    "mission_planning_answer": lambda service, body: service.answer_planning_question(dict(body)),
    "mission_operation_intent_submit": _operation_submit,
    "mission_operation_intent_status": _operation_status,
    "mission_source_register": _source_register,
    "mission_source_supersede": _source_supersede,
    "mission_source_revoke": _source_revoke,
    "mission_citation_read": _citation,
    "mission_list": _list,
    "mission_get": _get,
    "mission_events": _events,
    "mission_cancel": _cancel,
    "mission_approval_list": _approvals,
    "mission_approval_decide": _decide,
    "mission_takeover": _takeover,
    "mission_comment": _comment,
    "mission_artifact_read": _artifact,
    "mission_diagnostics": _diagnostics,
    "mission_support_export": _support_export,
    "orchestration_policy_status": _policy,
    # 2026-09-25 条目 7: disk usage of task/session data for the Settings page reminder
    "orchestration_storage_get": lambda service, body: service.storage_usage(body),
    # ARP-EXEC-1.1.1 (RP-E3): the SDK runtime plane behind one control message, plus the
    # two Host-side links of a Skill evaluation to its original Assurance Mission.
    "agent_runtime_request": lambda service, body: service.runtime_plane(body),
    "agent_skill_evaluation_mission": lambda service, body: service.skill_evaluation_mission(body),
    "agent_skill_evaluation_dispatch": lambda service, body: service.skill_evaluation_dispatch(body),
    # NEXT-TG-1.0 §11: the shared Skill catalogue (one authority for every native pool)
    "agent_skill_request": lambda service, body: service.skill_request(body),
    "orchestration_skill_catalogue": lambda service, body: service.skill_catalogue(body),
    "orchestration_skill_install_file": lambda service, body: service.skill_install_file(body),
    "orchestration_skill_lifecycle": lambda service, body: service.skill_lifecycle(body),
}

__all__ = ("MESSAGE_TYPES", "handle")
