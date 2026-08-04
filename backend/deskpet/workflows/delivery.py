"""Typed runtime-only outcomes for durable workflow delivery attempts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class DeliveryDisposition(str, Enum):
    DELIVERED = "delivered"
    DISCARDED_FENCED = "discarded_fenced"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True, slots=True)
class DeliveryAttemptResultV1:
    """Frozen v6 handler result; never persisted as a second state owner."""

    disposition: DeliveryDisposition
    reason_code: str
    artifact_projection: Mapping[str, Any] | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        code = str(self.reason_code).strip()
        if not code or len(code) > 80:
            raise ValueError("delivery reason_code must contain 1..80 characters")
        if self.schema_version != 1:
            raise ValueError("delivery result schema_version must be 1")
        object.__setattr__(self, "reason_code", code)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "disposition": self.disposition.value,
            "reason_code": self.reason_code,
            "artifact_projection": (
                dict(self.artifact_projection)
                if self.artifact_projection is not None
                else None
            ),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "DeliveryAttemptResultV1":
        if set(value) != {
            "schema_version",
            "disposition",
            "reason_code",
            "artifact_projection",
        }:
            raise ValueError("delivery result keys do not match DeliveryAttemptResultV1")
        if value.get("schema_version") != 1:
            raise ValueError("delivery result schema_version must be 1")
        raw_projection = value.get("artifact_projection")
        if raw_projection is not None and not isinstance(raw_projection, Mapping):
            raise ValueError("artifact_projection must be an object or null")
        return cls(
            disposition=DeliveryDisposition(str(value.get("disposition"))),
            reason_code=str(value.get("reason_code") or ""),
            artifact_projection=(dict(raw_projection) if raw_projection is not None else None),
        )

    @classmethod
    def delivered(
        cls,
        reason_code: str = "projection_persisted",
        *,
        artifact_projection: Mapping[str, Any] | None = None,
    ) -> "DeliveryAttemptResultV1":
        return cls(DeliveryDisposition.DELIVERED, reason_code, artifact_projection)

    @classmethod
    def discarded_fenced(cls, reason_code: str) -> "DeliveryAttemptResultV1":
        return cls(DeliveryDisposition.DISCARDED_FENCED, reason_code)

    @classmethod
    def retryable_failure(cls, reason_code: str) -> "DeliveryAttemptResultV1":
        return cls(DeliveryDisposition.RETRYABLE_FAILURE, reason_code)


def normalize_v6_delivery_result(value: object) -> DeliveryAttemptResultV1:
    if isinstance(value, DeliveryAttemptResultV1):
        return value
    if isinstance(value, Mapping):
        return DeliveryAttemptResultV1.from_json(value)
    raise ValueError("v6 delivery handler returned an invalid result")


async def broadcast_websocket_best_effort(
    envelope: Mapping[str, Any],
    connections: Iterable[Any],
    *,
    timeout_s: float = 1.0,
) -> DeliveryAttemptResultV1:
    """Broadcast one projection with a typed, retry-safe delivery outcome."""

    seen: set[int] = set()
    try:
        for target_ws in connections:
            marker = id(target_ws)
            if marker in seen:
                continue
            seen.add(marker)
            await asyncio.wait_for(
                target_ws.send_json(dict(envelope)), timeout=float(timeout_s)
            )
    except TimeoutError:
        return DeliveryAttemptResultV1.retryable_failure("websocket_send_timeout")
    except Exception:
        return DeliveryAttemptResultV1.retryable_failure("websocket_send_failed")
    return DeliveryAttemptResultV1.delivered("websocket_best_effort")


__all__ = [
    "DeliveryAttemptResultV1",
    "DeliveryDisposition",
    "normalize_v6_delivery_result",
    "broadcast_websocket_best_effort",
]
