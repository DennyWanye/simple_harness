"""Product delivery bindings for canonical Native workflow events."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from types import SimpleNamespace
from typing import Any

from deskpet.execution.contracts import RunEvent, thaw_json
from deskpet.harness.projector import DeliveryDiscarded, SinkRegistration
from deskpet.workflows.delivery import DeliveryDisposition, normalize_v6_delivery_result

BoundReader = Callable[[str], bool | Awaitable[bool]]


def workflow_event_envelope(event: RunEvent) -> dict[str, Any]:
    payload = thaw_json(event.candidate.payload)
    correlation = thaw_json(event.candidate.correlation)
    error = None if event.candidate.error is None else thaw_json(event.candidate.error)
    assert isinstance(payload, dict) and isinstance(correlation, dict)
    assert error is None or isinstance(error, dict)
    card = payload.get("card")
    card = dict(card) if isinstance(card, Mapping) else {}
    return {
        "schema_version": 1,
        "type": "workflow_event",
        "event_id": event.event_id,
        "event_key": event.candidate.event_key,
        "run_id": event.run_id,
        "root_run_id": event.root_run_id,
        "seq": event.durable_seq,
        "event_type": event.candidate.kind,
        "status": event.candidate.status.value,
        "payload": payload,
        "correlation": correlation,
        "error": error,
        "artifact_refs": list(event.candidate.artifact_refs),
        "request_id": correlation.get("request_id") or card.get("request_id"),
        "turn_id": correlation.get("turn_id") or card.get("turn_id"),
        "workflow_name": correlation.get("workflow_name") or card.get("workflow_name"),
        "workflow_version": correlation.get("workflow_version") or card.get("workflow_version"),
        "created_at": event.created_at,
    }


def workflow_sink_registrations(
    handlers: Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[Any]]],
    *,
    bound_readers: Mapping[str, BoundReader] | None = None,
) -> tuple[SinkRegistration, ...]:
    readers = dict(bound_readers or {})

    def bind(channel: str, handler):
        key, bound = str(channel).strip().lower(), readers.get(str(channel).strip().lower())

        async def workflow_target_is_bound(target_id: str) -> bool:
            if bound is None:
                return bool(str(target_id).strip())
            value = bound(target_id)
            return bool(await value) if inspect.isawaitable(value) else bool(value)

        async def deliver_workflow_projection(event: RunEvent, target_id: str) -> None:
            result = await handler(
                workflow_event_envelope(event),
                {
                    "channel": key,
                    "sink_kind": key,
                    "target_id": target_id,
                    "event_id": event.event_id,
                    "manifest_ref": f"execution:{event.event_id}:{key}",
                },
            )
            try:
                normalized = normalize_v6_delivery_result(result)
            except ValueError:
                if isinstance(result, Mapping) and result.get("discarded") is True:
                    raise DeliveryDiscarded("product delivery was fenced")
                return
            if normalized.disposition is DeliveryDisposition.DELIVERED:
                return
            if normalized.disposition is DeliveryDisposition.DISCARDED_FENCED or (
                key == "session_message"
                and event.kind in {"workflow.report", "workflow.artifact_card"}
                and normalized.reason_code == "projection_content_missing"
            ):
                raise DeliveryDiscarded(normalized.reason_code)
            raise RuntimeError(normalized.reason_code)

        return SinkRegistration(
            key,
            "workflow",
            SimpleNamespace(
                is_bound=workflow_target_is_bound,
                deliver=deliver_workflow_projection,
            ),
        )

    return tuple(bind(channel, handler) for channel, handler in sorted(handlers.items()))
