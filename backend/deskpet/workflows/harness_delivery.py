"""Product delivery bindings for canonical Native workflow events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from deskpet.execution.contracts import RunEvent, thaw_json

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
