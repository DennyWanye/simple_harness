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
    "mission_source_register",
    "mission_source_supersede",
    "mission_source_revoke",
    "mission_citation_read",
    "mission_list",
    "mission_get",
    "mission_events",
    "mission_cancel",
    "mission_approval_list",
    "mission_approval_decide",
    "mission_takeover",
    "mission_comment",
    "mission_artifact_read",
    "orchestration_policy_status",
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
    except Exception as error:  # noqa: BLE001 - the socket loop must never see an exception
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            return _error(msg_type, request_id, code, str(error))
        logger.exception("orchestration handler failed", extra={"msg_type": msg_type})
        return _error(msg_type, request_id, "internal_error", "编排服务处理请求时出错")


def _create(service: Any, body: Mapping[str, Any]) -> Any:
    return service.create_mission(dict(body))


def _create_with_sources(service: Any, body: Mapping[str, Any]) -> Any:
    return service.create_mission_with_sources(dict(body))


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


_ACTIONS: dict[str, Callable[[Any, Mapping[str, Any]], Any | Awaitable[Any]]] = {
    "mission_create": _create,
    "mission_create_with_sources": _create_with_sources,
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
    "orchestration_policy_status": _policy,
}

__all__ = ("MESSAGE_TYPES", "handle")
