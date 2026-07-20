"""Product-neutral execution event projection and durable delivery worker.

This module is add-only WI-3 readiness.  It is intentionally absent from the
production bootstrap until the atomic owner cutover: legacy workflow/session
projectors remain the sole production path meanwhile.
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliveryRecord,
    DeliverySpec,
    ExecutionError,
    JsonValue,
    RunEvent,
    thaw_json,
)
from deskpet.execution.ports import ExecutionUnitOfWork, SinkKey
from deskpet.harness.tool_executor import ToolOutcome


class ProjectionContractError(ExecutionError):
    pass


class ProjectionSink(Protocol):
    async def is_bound(self, target_id: str) -> bool: ...

    async def project(self, event: RunEvent, target_id: str) -> None: ...


class SessionMessageStore(Protocol):
    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        **kwargs: object,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class SinkRegistration:
    sink_kind: str
    sink_instance: str
    sink: ProjectionSink

    def __post_init__(self) -> None:
        if not self.sink_kind.strip() or not self.sink_instance.strip():
            raise ValueError("sink registration requires kind and instance")

    @property
    def key(self) -> SinkKey:
        return self.sink_kind, self.sink_instance


@dataclass(frozen=True, slots=True)
class EventMergeCursor:
    """Venue-owned reducer cursor for independent durable/live order domains."""

    run_id: str
    durable_seq: int = 0
    durable_event_id: str | None = None
    live_epoch: str | None = None
    live_seq: int = 0

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if self.durable_seq < 0 or self.live_seq < 0:
            raise ValueError("merge sequences must be non-negative")
        if self.live_epoch is None and self.live_seq != 0:
            raise ValueError("live_seq requires an active stream epoch")

    def activate_live_epoch(self, stream_epoch: str) -> "EventMergeCursor":
        epoch = str(stream_epoch).strip()
        if not epoch:
            raise ValueError("stream_epoch must be non-empty")
        return replace(self, live_epoch=epoch, live_seq=0)

    def accept(self, event: RunEvent) -> tuple["EventMergeCursor", bool]:
        if event.run_id != self.run_id:
            raise ProjectionContractError(
                "cross_run_event",
                f"cursor for {self.run_id} cannot consume {event.run_id}",
            )
        if event.durable:
            assert event.durable_seq is not None
            if event.durable_seq <= self.durable_seq:
                return self, False
            return (
                replace(
                    self,
                    durable_seq=event.durable_seq,
                    durable_event_id=event.event_id,
                ),
                True,
            )
        assert event.live_cursor is not None
        if event.live_cursor.stream_epoch != self.live_epoch:
            return self, False
        if event.live_cursor.live_seq <= self.live_seq:
            return self, False
        return replace(self, live_seq=event.live_cursor.live_seq), True


def standard_delivery_specs(
    *,
    session_id: str,
    session_sink_instance: str,
    ws_sink_instance: str,
    ws_target_id: str,
    tts_sink_instance: str,
    tts_target_id: str,
) -> tuple[DeliverySpec, DeliverySpec, DeliverySpec]:
    """Create the fixed WI-3 sink policy set for one public Run event."""

    return (
        DeliverySpec(
            sink_kind="session_db",
            sink_instance=session_sink_instance,
            target_id=session_id,
            policy=DeliveryPolicy.DURABLE_REQUIRED,
        ),
        DeliverySpec(
            sink_kind="ws",
            sink_instance=ws_sink_instance,
            target_id=ws_target_id,
            policy=DeliveryPolicy.RETRY_WHILE_BOUND,
        ),
        DeliverySpec(
            sink_kind="tts",
            sink_instance=tts_sink_instance,
            target_id=tts_target_id,
            policy=DeliveryPolicy.BEST_EFFORT,
        ),
    )


def run_event_envelope(event: RunEvent) -> dict[str, JsonValue]:
    """Serialize one typed event without re-inferring success from payload."""

    live_cursor: JsonValue = None
    if event.live_cursor is not None:
        live_cursor = {
            "schema_version": event.live_cursor.schema_version,
            "stream_epoch": event.live_cursor.stream_epoch,
            "live_seq": event.live_cursor.live_seq,
        }
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "root_run_id": event.root_run_id,
        "session_id": event.session_id,
        "durable_seq": event.durable_seq,
        "live_cursor": live_cursor,
        "kind": event.kind,
        "status": event.status.value,
        "driver_kind": event.driver_kind,
        "correlation": thaw_json(event.correlation),
        "payload": thaw_json(event.candidate.payload),
        "error": None if event.error is None else thaw_json(event.error),
        "artifact_refs": list(event.artifact_refs),
        "created_at": event.created_at,
    }


def tool_outcome_payload(outcome: ToolOutcome) -> dict[str, JsonValue]:
    """Preserve the typed ToolOutcome status for RunEvent payloads."""

    return {
        "call_id": outcome.call_id,
        "effect_id": outcome.effect_id,
        "status": outcome.status.value,
        "value": thaw_json(outcome.value),
        "error": None if outcome.error is None else thaw_json(outcome.error),
        "receipt_ref": outcome.receipt_ref,
        "artifact_refs": list(outcome.artifact_refs),
        "retryable": outcome.retryable,
        "reconciliation": (
            None
            if outcome.reconciliation is None
            else thaw_json(outcome.reconciliation)
        ),
    }


class SessionDBProjectionSink:
    """Stable-event-id adapter over SessionDB's existing idempotent message row."""

    def __init__(self, session_db: SessionMessageStore) -> None:
        self._session_db = session_db

    async def is_bound(self, target_id: str) -> bool:
        return bool(target_id)

    async def project(self, event: RunEvent, target_id: str) -> None:
        if target_id != event.session_id:
            raise ProjectionContractError(
                "cross_session_projection",
                "SessionDB delivery target differs from RunEvent session",
            )
        payload = thaw_json(event.candidate.payload)
        message = payload.get("session_message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ProjectionContractError(
                "missing_session_message",
                "session_db delivery requires payload.session_message",
            )
        role = str(message.get("role") or "").strip()
        content = message.get("content")
        if not role or not isinstance(content, str):
            raise ProjectionContractError(
                "invalid_session_message",
                "session_message requires role and string content",
            )
        await self._session_db.append_message(
            target_id,
            role,
            content,
            workflow_event_id=event.event_id,
            projection_kind=cast(str | None, message.get("projection_kind")),
            context_visibility=cast(str | None, message.get("context_visibility")),
            skip_embed=bool(message.get("skip_embed", False)),
        )


class WebSocketProjectionSink:
    def __init__(
        self,
        send: Callable[[str, Mapping[str, JsonValue]], Awaitable[None]],
        is_bound: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._send = send
        self._is_bound = is_bound

    async def is_bound(self, target_id: str) -> bool:
        return await self._is_bound(target_id)

    async def project(self, event: RunEvent, target_id: str) -> None:
        await self._send(target_id, run_event_envelope(event))


class TTSProjectionSink:
    def __init__(
        self,
        speak: Callable[[str, str], Awaitable[None]],
        is_bound: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._speak = speak
        self._is_bound = is_bound

    async def is_bound(self, target_id: str) -> bool:
        return await self._is_bound(target_id)

    async def project(self, event: RunEvent, target_id: str) -> None:
        payload = thaw_json(event.candidate.payload)
        cue = payload.get("tts_cue") if isinstance(payload, dict) else None
        text = cue.get("text") if isinstance(cue, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProjectionContractError(
                "missing_tts_cue", "tts delivery requires payload.tts_cue.text"
            )
        await self._speak(target_id, text)


class ExecutionProjector:
    """Dispatch durable claims and direct live events to registered sinks."""

    def __init__(
        self,
        event_store: ExecutionUnitOfWork,
        registrations: Sequence[SinkRegistration],
    ) -> None:
        self._event_store = event_store
        self._registrations = tuple(registrations)
        keys = tuple(item.key for item in self._registrations)
        if len(dict.fromkeys(keys)) != len(keys):
            raise ValueError("projection sink registrations must be unique")

    @property
    def sink_keys(self) -> tuple[SinkKey, ...]:
        return tuple(item.key for item in self._registrations)

    def _registration(self, key: SinkKey) -> SinkRegistration:
        for item in self._registrations:
            if item.key == key:
                return item
        raise ProjectionContractError(
            "sink_not_registered", f"projection sink is not registered: {key!r}"
        )

    async def dispatch(self, delivery: DeliveryRecord) -> bool:
        registration = self._registration(
            (delivery.sink_kind, delivery.sink_instance)
        )
        if delivery.policy in {
            DeliveryPolicy.RETRY_WHILE_BOUND,
            DeliveryPolicy.BEST_EFFORT,
        } and not await registration.sink.is_bound(delivery.target_id):
            return False
        event = await self._event_store.get_event(delivery.event_id)
        await registration.sink.project(event, delivery.target_id)
        return True

    async def hydrate(
        self, cursor: EventMergeCursor
    ) -> tuple[EventMergeCursor, tuple[RunEvent, ...]]:
        accepted: list[RunEvent] = []
        current = cursor
        for event in await self._event_store.list_events(
            cursor.run_id, after_durable_seq=cursor.durable_seq
        ):
            current, visible = current.accept(event)
            if visible:
                accepted.append(event)
        return current, tuple(accepted)

    async def project_live(
        self,
        event: RunEvent,
        *,
        cursor: EventMergeCursor,
        sink_kind: str,
        sink_instance: str,
        target_id: str,
    ) -> tuple[EventMergeCursor, bool]:
        if event.durable:
            raise ProjectionContractError(
                "durable_live_bypass",
                "durable events must use persisted delivery rows",
            )
        updated, visible = cursor.accept(event)
        if not visible:
            return updated, False
        registration = self._registration((sink_kind, sink_instance))
        if not await registration.sink.is_bound(target_id):
            return updated, False
        await registration.sink.project(event, target_id)
        return updated, True


class DeliveryWorker:
    """One-step durable dispatcher; process lifetime is owned by later bootstrap."""

    def __init__(
        self,
        store: ExecutionUnitOfWork,
        projector: ExecutionProjector,
        *,
        clock: Callable[[], float] = time.time,
        claim_ttl_seconds: float = 30.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
    ) -> None:
        timing = (claim_ttl_seconds, retry_base_seconds, retry_max_seconds)
        if any(not math.isfinite(float(value)) or value <= 0 for value in timing):
            raise ValueError("delivery timing values must be positive")
        self._store = store
        self._projector = projector
        self._clock = clock
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)

    def _retry_at(self, attempts: int) -> float:
        delay = min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** min(30, max(0, attempts - 1))),
        )
        return float(self._clock()) + delay

    async def run_once(self) -> bool:
        claim = await self._store.claim_delivery(
            sink_keys=self._projector.sink_keys,
            claim_ttl_seconds=self._claim_ttl_seconds,
        )
        if claim is None:
            return False
        try:
            delivered = await self._projector.dispatch(claim)
        except Exception as exc:
            discard = claim.policy is DeliveryPolicy.BEST_EFFORT
            await self._store.release_delivery(
                claim.delivery_id,
                expected_version=claim.delivery_version,
                error=f"{type(exc).__name__}: {exc}",
                retry_at=None if discard else self._retry_at(claim.attempts),
                discard=discard,
            )
        else:
            if delivered:
                await self._store.complete_delivery(
                    claim.delivery_id,
                    expected_version=claim.delivery_version,
                )
            else:
                await self._store.release_delivery(
                    claim.delivery_id,
                    expected_version=claim.delivery_version,
                    error="projection sink is no longer bound",
                    retry_at=None,
                    discard=True,
                )
        return True

    async def drain(self, *, max_deliveries: int = 100) -> int:
        if max_deliveries < 1:
            raise ValueError("max_deliveries must be positive")
        processed = 0
        while processed < max_deliveries and await self.run_once():
            processed += 1
        return processed


__all__ = [
    "DeliveryWorker",
    "EventMergeCursor",
    "ExecutionProjector",
    "ProjectionContractError",
    "ProjectionSink",
    "SessionDBProjectionSink",
    "SinkRegistration",
    "TTSProjectionSink",
    "WebSocketProjectionSink",
    "run_event_envelope",
    "standard_delivery_specs",
    "tool_outcome_payload",
]
