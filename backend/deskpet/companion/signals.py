"""Durable, replay-safe ingress contracts for Companion growth evidence.

The three sources intentionally converge on one ``GrowthEvent`` protocol:

* a committed user message or explicit UI command;
* an authoritative effect/terminal settlement;
* a trusted user decision.

The production composition root binds these ports after the durable authority
cutover. SessionDB remains the message fact source and transport outbox;
CompanionStore remains the only growth authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    RunEvent,
    fingerprint_json,
    thaw_json,
)

from .contracts import (
    CompanionConflictError,
    CompanionOwnerError,
    GrowthEvent,
    JsonValue,
    OwnerRef,
)
from .identity_gate import FrozenOwnerIdentity, IdentityReadyGate

logger = logging.getLogger(__name__)


class GrowthSignalKind(StrEnum):
    MESSAGE_INGRESS = "message_ingress"
    EXECUTION_OUTCOME = "execution_outcome"
    USER_DECISION = "user_decision"


_RECURSIVE_PURPOSES = frozenset({"reflection", "evaluation"})
_SENSITIVE_KEY_PARTS = (
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "device_key",
    "password",
    "private_key",
    "raw_args",
    "secret",
)


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name}_required")
    return text


def _assert_safe_payload(value: object, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list) or isinstance(value, tuple):
        for index, item in enumerate(value):
            _assert_safe_payload(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"growth_payload_key_invalid:{path}")
            key = raw_key.casefold()
            if any(part in key for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"growth_payload_sensitive_key:{path}.{raw_key}")
            _assert_safe_payload(item, path=f"{path}.{raw_key}")
        return
    raise ValueError(f"growth_payload_value_invalid:{path}:{type(value).__name__}")


@dataclass(frozen=True, slots=True)
class GrowthSignalV1:
    """One host-issued, content-addressed source envelope."""

    owner: OwnerRef
    kind: GrowthSignalKind | str
    source_ref: str
    context_key: str
    root_run_id: str
    reason_code: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    retry_of_event_id: str | None = None
    previous_request_id: str | None = None
    previous_run_id: str | None = None
    current_request_id: str | None = None
    current_run_id: str | None = None
    origin: str = "product"
    purpose: str = "user_turn"
    delegated_objective_outcome: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", GrowthSignalKind(self.kind))
        for name in ("source_ref", "context_key", "root_run_id", "reason_code"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.schema_version != 1:
            raise ValueError("growth_signal_schema_unsupported")
        normalized = dict(self.payload)
        _assert_safe_payload(normalized)
        object.__setattr__(self, "payload", normalized)
        if self.origin == "companion" and self.purpose in _RECURSIVE_PURPOSES:
            raise ValueError("growth_signal_recursive_internal_run")
        if (
            self.origin == "companion"
            and self.purpose == "delegated_task"
            and not self.delegated_objective_outcome
        ):
            raise ValueError("growth_signal_delegated_process_not_objective")
        retry_fields = (
            self.previous_request_id,
            self.previous_run_id,
            self.current_request_id,
            self.current_run_id,
        )
        if any(item is not None for item in retry_fields) and not all(
            str(item or "").strip() for item in retry_fields
        ):
            raise ValueError("growth_signal_retry_links_incomplete")

    @property
    def event_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "owner": {
                "profile_id": self.owner.profile_id,
                "profile_generation": self.owner.profile_generation,
            },
            "kind": self.kind.value,
            "source_ref": self.source_ref,
        }
        return "growth_" + fingerprint_json(payload)[:32]

    def to_growth_event(self) -> GrowthEvent:
        retry_links: dict[str, JsonValue] = {}
        if self.previous_request_id is not None:
            retry_links = {
                "previous_request_id": self.previous_request_id,
                "previous_run_id": self.previous_run_id,
                "current_request_id": self.current_request_id,
                "current_run_id": self.current_run_id,
            }
        payload: dict[str, JsonValue] = {
            "signal_kind": self.kind.value,
            "origin": self.origin,
            "purpose": self.purpose,
            "facts": dict(self.payload),
            "retry_links": retry_links,
        }
        event_hash = fingerprint_json(
            {
                "schema_version": self.schema_version,
                "event_id": self.event_id,
                "owner": {
                    "profile_id": self.owner.profile_id,
                    "profile_generation": self.owner.profile_generation,
                },
                "source_ref": self.source_ref,
                "context_key": self.context_key,
                "root_run_id": self.root_run_id,
                "reason_code": self.reason_code,
                "retry_of_event_id": self.retry_of_event_id,
                "payload": payload,
            }
        )
        return GrowthEvent(
            owner=self.owner,
            event_id=self.event_id,
            source_kind=self.kind.value,
            source_ref=self.source_ref,
            context_key=self.context_key,
            root_run_id=self.root_run_id,
            retry_of=self.retry_of_event_id,
            reason_code=self.reason_code,
            payload=payload,
            event_hash=event_hash,
        )


@dataclass(frozen=True, slots=True)
class GrowthDeliveryTarget:
    owner: OwnerRef

    @property
    def target_id(self) -> str:
        return (
            f"companion-profile:{self.owner.profile_id}:"
            f"{self.owner.profile_generation}"
        )

    @classmethod
    def parse(cls, value: str) -> "GrowthDeliveryTarget":
        prefix = "companion-profile:"
        if not value.startswith(prefix):
            raise ValueError("growth_delivery_target_invalid")
        remainder = value[len(prefix) :]
        profile_id, separator, raw_generation = remainder.rpartition(":")
        if not separator or not profile_id:
            raise ValueError("growth_delivery_target_invalid")
        try:
            generation = int(raw_generation)
        except ValueError as exc:
            raise ValueError("growth_delivery_target_invalid") from exc
        return cls(OwnerRef(profile_id, generation))


class GrowthEventStore(Protocol):
    def record_growth_event(self, event: GrowthEvent) -> Mapping[str, Any]: ...


class GrowthIngressDispatcher:
    """At-least-once source dispatcher; the Store owns replay/conflict truth."""

    def __init__(self, store: GrowthEventStore) -> None:
        self._store = store

    def deliver(self, signal: GrowthSignalV1) -> Mapping[str, Any]:
        return self._store.record_growth_event(signal.to_growth_event())


class CompanionIngressOutboxDispatcher:
    """Project committed SessionDB message intents into CompanionStore."""

    def __init__(
        self,
        *,
        session_db: Any,
        store: Any,
        runtime: Any,
        reflection_target_catalog: Sequence[Mapping[str, str]] = (),
        claim_owner: str | None = None,
        lease_seconds: float = 30.0,
    ) -> None:
        self._session_db = session_db
        self._store = store
        self._runtime = runtime
        self._reflection_target_catalog = tuple(
            {
                "target_kind": str(item["target_kind"]),
                "stable_name": str(item["stable_name"]),
                "pack_id": str(item["pack_id"]),
            }
            for item in reflection_target_catalog
        )
        self._claim_owner = (
            claim_owner or f"companion-ingress:{os.getpid()}:{id(self):x}"
        )
        self._lease_seconds = float(lease_seconds)
        self._dispatcher = GrowthIngressDispatcher(store)

    async def settle_semantic_intent(
        self,
        *,
        session_id: str,
        message_id: int,
        request_id: str,
        turn_id: str,
        owner: Any,
        growth_signal_kind: str,
    ) -> Mapping[str, Any]:
        """Persist one model decision, then synchronously project its outbox."""

        settled = await self._session_db.settle_companion_ingress_semantic_intent(
            session_id=session_id,
            message_id=message_id,
            trusted_owner=owner,
            request_id=request_id,
            turn_id=turn_id,
            growth_signal_kind=growth_signal_kind,
        )
        delivered = await self.drain_available(
            profile_id=str(settled["profile_id"]),
            profile_generation=int(settled["profile_generation"]),
        )
        logger.info(
            "companion_ingress_semantic_settled "
            "session_id=%s message_id=%s growth_signal_kind=%s delivered=%s",
            session_id,
            message_id,
            growth_signal_kind,
            delivered,
        )
        return settled

    async def drain_one(
        self,
        *,
        profile_id: str,
        profile_generation: int,
    ) -> Mapping[str, Any] | None:
        row = await self._session_db.claim_companion_ingress_outbox(
            self._claim_owner,
            self._lease_seconds,
            profile_id=profile_id,
            profile_generation=profile_generation,
        )
        if row is None:
            return None
        outbox_id = str(row["outbox_id"])
        attempt = int(row["attempt"])
        try:
            envelope = row.get("event_envelope")
            if not isinstance(envelope, Mapping):
                envelope = json.loads(str(row["event_envelope_json"]))
            message_id = int(envelope["message_id"])
            session_id = str(envelope["session_id"])
            content = await self._session_db.read_companion_ingress_message_content(
                session_id=session_id,
                message_id=message_id,
            )
            payload_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if payload_hash != str(envelope["payload_hash"]):
                raise ValueError("companion_ingress_message_hash_mismatch")
            owner = OwnerRef(
                str(envelope["profile_id"]),
                int(envelope["profile_generation"]),
            )
            priority = str(row.get("priority") or "normal")
            semantic_growth_intent = str(
                envelope.get("semantic_growth_intent") or "none"
            )
            reason_code = {
                "explicit_correction": "explicit_user_correction",
                "explicit_capability_request": (
                    "explicit_user_capability_request"
                ),
            }.get(semantic_growth_intent, "committed_user_message")
            retry_request = _optional_text(envelope.get("retry_of_request_id"))
            retry_run = _optional_text(envelope.get("retry_of_run_id"))
            has_retry_link = retry_request is not None and retry_run is not None
            signal = GrowthSignalV1(
                owner=owner,
                kind=GrowthSignalKind.MESSAGE_INGRESS,
                source_ref=str(envelope["source_ref"]),
                context_key=f"session:{session_id}",
                root_run_id=str(envelope["run_id"]),
                reason_code=reason_code,
                previous_request_id=(retry_request if has_retry_link else None),
                previous_run_id=(retry_run if has_retry_link else None),
                current_request_id=(
                    str(envelope["request_id"])
                    if has_retry_link
                    else None
                ),
                current_run_id=(
                    str(envelope["run_id"])
                    if has_retry_link
                    else None
                ),
                payload={
                    "session_id": session_id,
                    "message_id": message_id,
                    "request_id": str(envelope["request_id"]),
                    "turn_id": str(envelope["turn_id"]),
                    "payload_hash": payload_hash,
                    "message_text": content,
                    "priority": priority,
                    "semantic_growth_intent": semantic_growth_intent,
                },
            )
            event = signal.to_growth_event()
            delivered = self._dispatcher.deliver(signal)
            if semantic_growth_intent in {
                "explicit_correction",
                "explicit_capability_request",
            }:
                self._enqueue_reflection(owner, signal, content)
            settled = await self._session_db.settle_companion_ingress_outbox(
                outbox_id,
                attempt,
                delivered_hash=event.event_hash,
            )
            self._runtime.wake()
            return {
                "event": delivered,
                "outbox": settled,
                "event_id": event.event_id,
                "event_hash": event.event_hash,
            }
        except Exception as exc:
            await self._session_db.settle_companion_ingress_outbox(
                outbox_id,
                attempt,
                error=f"{type(exc).__name__}:{exc}",
                retry_at=time.time() + 1.0,
            )
            raise

    async def drain_available(
        self,
        *,
        profile_id: str,
        profile_generation: int,
        limit: int = 64,
    ) -> int:
        delivered = 0
        for _ in range(max(1, int(limit))):
            row = await self.drain_one(
                profile_id=profile_id,
                profile_generation=profile_generation,
            )
            if row is None:
                break
            delivered += 1
        return delivered

    def _enqueue_reflection(
        self,
        owner: OwnerRef,
        signal: GrowthSignalV1,
        content: str,
    ) -> None:
        event = signal.to_growth_event()
        self._store.enqueue_job(
            owner,
            job_id=f"reflection:{event.event_id}",
            kind="reflection",
            dedupe_key=f"growth-intent:{event.event_id}",
            payload={
                "purpose": "reflection",
                "owner_key": (
                    f"companion:{owner.profile_id}:"
                    f"{owner.profile_generation}"
                ),
                "evidence_ids": [event.event_id],
                "capture_growth": False,
                "requires_idle": True,
                "legacy_text": (
                    "你正在执行零工具的 Companion 成长反思。仅根据下面这条用户"
                    "显式纠正，判断它是否要求长期修改一个既有 Skill 或 Workflow。"
                    "只输出 JSON，不要调用工具；字段必须是 "
                    '{"decision":"propose|abstain","target_kind":"skill|workflow|none",'
                    '"stable_name":"string|null","hypothesis":"string|null",'
                    '"requested_change":"string|null","risk_hints":["string"]}。'
                    f"\n用户纠正：{content}"
                ),
                "text": self._reflection_prompt(content),
                "request_payload": {
                    "growth_signal_ref": event.event_id,
                    "growth_signal_hash": event.event_hash,
                },
            },
            budget_reserved_tokens=4_000,
            budget_reserved_ms=120_000,
            reason_code=f"{signal.reason_code}_reflection_queued",
        )

    def _reflection_prompt(self, content: str) -> str:
        return (
            "你正在执行零工具的 Companion 成长反思。仅根据下面这条"
            "用户显式纠正，判断它是否要求长期修改一个现有 Skill 或 Workflow。"
            "只能从 host 提供的冻结目标目录中选择 exact stable_name。"
            "只输出 JSON，不要调用工具；字段必须是："
            '{"decision":"propose|abstain","target_kind":"skill|workflow|none",'
            '"stable_name":"string|null","hypothesis":"string|null",'
            '"requested_change":"string|null","risk_hints":["string"],'
            '"expected_improvement":"string|null",'
            '"evaluation_plan":[{"case_id":"string","assertion":"string"}]}。'
            "\n冻结目标目录："
            + json.dumps(
                self._reflection_target_catalog,
                ensure_ascii=False,
            )
            + f"\n用户纠正：{content}"
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class GrowthTerminalDeliveryContributor:
    """Freeze the profile inbox at Run start, never at terminal time."""

    sink_kind = "companion_growth"
    sink_instance = "growth-events-v1"

    def __init__(self, gate: IdentityReadyGate) -> None:
        self._gate = gate

    @staticmethod
    def _captures_growth(request: object) -> bool:
        payload = getattr(request, "payload", None)
        if not isinstance(payload, Mapping):
            return True
        background = payload.get("companion_background")
        if not isinstance(background, Mapping):
            return True
        purpose = str(background.get("purpose") or "")
        capture = bool(background.get("capture_growth", False))
        if purpose in _RECURSIVE_PURPOSES:
            return False
        return purpose == "delegated_task" and capture

    def requires_durable(self, request: object, _host: object) -> bool:
        if not self._captures_growth(request):
            return False
        self._gate.freeze()
        return True

    def freeze_deliveries(
        self, request: object, _host: object
    ) -> tuple[DeliverySpec, ...]:
        if not self._captures_growth(request):
            return ()
        frozen = self._gate.freeze()
        return (
            DeliverySpec(
                self.sink_kind,
                self.sink_instance,
                GrowthDeliveryTarget(frozen.owner).target_id,
                DeliveryPolicy.DURABLE_REQUIRED,
            ),
        )


class GrowthTerminalDeliverySink:
    """Convert only the authoritative root terminal event into evidence."""

    def __init__(self, store: GrowthEventStore) -> None:
        self._store = store

    async def is_bound(self, target_id: str) -> bool:
        target = GrowthDeliveryTarget.parse(target_id)
        read = getattr(self._store, "read", None)
        if not callable(read):
            return True
        with read() as db:
            row = db.execute(
                """SELECT status FROM profiles
                   WHERE profile_id=? AND generation=?""",
                (target.owner.profile_id, target.owner.profile_generation),
            ).fetchone()
            return row is not None and str(row["status"]) == "active"

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        target = GrowthDeliveryTarget.parse(target_id)
        if event.run_id != event.root_run_id:
            raise ValueError("growth_delivery_child_terminal_forbidden")
        error_class = None
        if event.candidate.error:
            error_class = str(event.candidate.error.get("class") or "unknown")
        signal = GrowthSignalV1(
            owner=target.owner,
            kind=GrowthSignalKind.EXECUTION_OUTCOME,
            source_ref=f"terminal:{event.event_id}",
            context_key=f"run:{event.root_run_id}",
            root_run_id=event.root_run_id,
            reason_code="root_terminal_outcome",
            payload={
                "event_id": event.event_id,
                "event_kind": event.kind,
                "status": event.status.value,
                "driver_kind": event.candidate.driver_kind,
                "error_class": error_class,
                "artifact_refs": list(event.candidate.artifact_refs),
                "result_payload_hash": fingerprint_json(
                    thaw_json(event.candidate.payload)
                ),
            },
        )
        try:
            self._store.record_growth_event(signal.to_growth_event())
        except CompanionOwnerError:
            # Projector maps this stable code to a tombstone/discard receipt.
            from deskpet.harness.projector import DeliveryDiscarded

            raise DeliveryDiscarded("owner_deleted") from None


def execution_outcome_signal(
    *,
    owner: OwnerRef,
    source_ref: str,
    context_key: str,
    root_run_id: str,
    status: OutcomeStatus | str,
    receipt_ref: str | None,
    evidence_verified: bool,
    capability_audit_ref: str | None = None,
    error_class: str | None = None,
) -> GrowthSignalV1:
    """Build a non-sensitive tool/effect settlement signal."""

    return GrowthSignalV1(
        owner=owner,
        kind=GrowthSignalKind.EXECUTION_OUTCOME,
        source_ref=source_ref,
        context_key=context_key,
        root_run_id=root_run_id,
        reason_code="effect_settled",
        payload={
            "status": OutcomeStatus(status).value,
            "receipt_ref": receipt_ref,
            "evidence_verified": bool(evidence_verified),
            "capability_audit_ref": capability_audit_ref,
            "error_class": error_class,
        },
    )


def frozen_owner(signal: GrowthSignalV1) -> FrozenOwnerIdentity:
    """Small helper used by tests and host-only adapters."""

    return FrozenOwnerIdentity(
        owner=signal.owner,
        owner_key=f"companion:{signal.owner.profile_id}:{signal.owner.profile_generation}",
        binding_epoch=1,
    )


__all__ = [
    "GrowthDeliveryTarget",
    "GrowthIngressDispatcher",
    "CompanionIngressOutboxDispatcher",
    "GrowthSignalKind",
    "GrowthSignalV1",
    "GrowthTerminalDeliveryContributor",
    "GrowthTerminalDeliverySink",
    "execution_outcome_signal",
]
