# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Memory mutation targets, lifecycle states and typed payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from simple_harness.contracts import (
    FrozenJsonValue,
    JsonValue,
    canonical_json,
    fingerprint_json,
    freeze_json,
    thaw_json,
)

from ..disclosure_protocol import (
    _bounded_text,
    _digest,
    _exact_keys,
    _identifier,
    _object,
    _optional_identifier,
    _positive_int,
)
from .common import _bounded_tuple, _optional_timestamp
from .types import (
    LongTermMemoryType,
    MemoryMutationValidationReasonCode,
    SemanticMemoryKind,
    SemanticRelationKind,
    _MemoryMutationAdmissionError,
)


class MemoryMutationKind(StrEnum):
    CREATE = "create"
    REVISE = "revise"
    SUPERSEDE = "supersede"
    CONTEST = "contest"
    SUPPRESS = "suppress"
    NO_MUTATION = "no_mutation"


class MemoryMutationPlanOutcome(StrEnum):
    MUTATE = "mutate"
    NO_MUTATION = "no_mutation"


class MemoryMutationApplyMode(StrEnum):
    """The protocol deliberately has no partial or best-effort apply mode."""

    STRICT_ATOMIC = "strict_atomic"


@dataclass(frozen=True, slots=True)
class ExistingMemoryTarget:
    memory_id: str
    revision: int

    def __post_init__(self) -> None:
        _identifier(self.memory_id, "memory_id")
        _positive_int(self.revision, "revision")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "target_kind": "existing_memory",
            "memory_id": self.memory_id,
            "revision": self.revision,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ExistingMemoryTarget:
        _exact_keys(value, {"target_kind", "memory_id", "revision"}, "ExistingMemoryTarget")
        if value["target_kind"] != "existing_memory":
            raise ValueError("ExistingMemoryTarget discriminator differs")
        return cls(
            _identifier(value["memory_id"], "memory_id"),
            _positive_int(value["revision"], "revision"),
        )


@dataclass(frozen=True, slots=True)
class CreatedByOperationTarget:
    operation_id: str

    def __post_init__(self) -> None:
        _identifier(self.operation_id, "operation_id")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "target_kind": "created_by_operation",
            "operation_id": self.operation_id,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> CreatedByOperationTarget:
        _exact_keys(value, {"target_kind", "operation_id"}, "CreatedByOperationTarget")
        if value["target_kind"] != "created_by_operation":
            raise ValueError("CreatedByOperationTarget discriminator differs")
        return cls(_identifier(value["operation_id"], "operation_id"))


MemoryMutationTarget = ExistingMemoryTarget | CreatedByOperationTarget


def _mutation_target_from_json(value: object) -> MemoryMutationTarget | None:
    if value is None:
        return None
    target = _object(value, "target")
    kind = target.get("target_kind")
    if kind == "existing_memory":
        return ExistingMemoryTarget.from_json(target)
    if kind == "created_by_operation":
        return CreatedByOperationTarget.from_json(target)
    raise ValueError("target has an unknown discriminator")


class EpisodeLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    AMENDED = "amended"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    FORGOTTEN = "forgotten"


class SemanticLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    FORGOTTEN = "forgotten"


class ProcedureLifecycleState(StrEnum):
    DRAFT = "draft"
    ELIGIBLE_FOR_ACTIVATION = "eligible_for_activation"
    ACTIVE = "active"
    REINFORCED = "reinforced"
    REVISED = "revised"
    INAPPLICABLE = "inapplicable"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class ProspectiveLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    PENDING = "pending"
    TRIGGERED = "triggered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


CognitiveLifecycleState = (
    EpisodeLifecycleState
    | SemanticLifecycleState
    | ProcedureLifecycleState
    | ProspectiveLifecycleState
)


class EpistemicStatus(StrEnum):
    EXPLICIT_USER = "explicit_user"
    VERIFIED_EXTERNAL = "verified_external"
    OBSERVED_BEHAVIOR = "observed_behavior"
    LLM_INFERENCE = "llm_inference"
    UNKNOWN = "unknown"


class ConflictStatus(StrEnum):
    UNCONTESTED = "uncontested"
    CONTESTED = "contested"
    RESOLVED = "resolved"


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    SOURCE_BOUND = "source_bound"
    USER_CONFIRMED = "user_confirmed"
    SOURCE_VERIFIED = "source_verified"
    REPEATED_OBSERVATION = "repeated_observation"


class ProcedureRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    IRREVERSIBLE = "irreversible"


class ProspectiveTriggerKind(StrEnum):
    TIME = "time"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class ValidTimeInterval:
    valid_from: float | None
    valid_until: float | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.valid_from, "valid_from"),
            (self.valid_until, "valid_until"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0
            ):
                raise ValueError(f"{name} must be a non-negative timestamp or null")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_from > self.valid_until
        ):
            raise ValueError("valid time interval is inverted")

    def to_json(self) -> dict[str, JsonValue]:
        return {"valid_from": self.valid_from, "valid_until": self.valid_until}

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ValidTimeInterval:
        _exact_keys(value, {"valid_from", "valid_until"}, "ValidTimeInterval")
        parsed: list[float | None] = []
        for name in ("valid_from", "valid_until"):
            item = value[name]
            if item is None:
                parsed.append(None)
            elif isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError(f"{name} must be numeric or null")
            else:
                parsed.append(float(item))
        return cls(parsed[0], parsed[1])


@dataclass(frozen=True, slots=True)
class EpisodeMemoryPayload:
    title: str
    participants: tuple[str, ...]
    goals: tuple[str, ...]
    actions: tuple[str, ...]
    results: tuple[str, ...]
    impacts: tuple[str, ...]
    occurred_start: float
    occurred_end: float | None
    thread_ref: str | None

    def __post_init__(self) -> None:
        _bounded_text(self.title, "title", max_bytes=4096)
        object.__setattr__(
            self, "participants", _bounded_tuple(self.participants, "participants", required=True)
        )
        for name in ("goals", "actions", "results", "impacts"):
            object.__setattr__(self, name, _bounded_tuple(getattr(self, name), name))
        interval = ValidTimeInterval(float(self.occurred_start), self.occurred_end)
        if interval.valid_from is None:
            raise ValueError("occurred_start is required")
        object.__setattr__(self, "occurred_start", interval.valid_from)
        object.__setattr__(self, "occurred_end", interval.valid_until)
        object.__setattr__(self, "thread_ref", _optional_identifier(self.thread_ref, "thread_ref"))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "memory_type": LongTermMemoryType.EPISODE.value,
            "title": self.title,
            "participants": list(self.participants),
            "goals": list(self.goals),
            "actions": list(self.actions),
            "results": list(self.results),
            "impacts": list(self.impacts),
            "occurred_start": self.occurred_start,
            "occurred_end": self.occurred_end,
            "thread_ref": self.thread_ref,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> EpisodeMemoryPayload:
        _exact_keys(
            value,
            {
                "memory_type",
                "title",
                "participants",
                "goals",
                "actions",
                "results",
                "impacts",
                "occurred_start",
                "occurred_end",
                "thread_ref",
            },
            "EpisodeMemoryPayload",
        )
        if value["memory_type"] != LongTermMemoryType.EPISODE.value:
            raise ValueError("EpisodeMemoryPayload discriminator differs")
        start = value["occurred_start"]
        end = value["occurred_end"]
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise TypeError("occurred_start must be numeric")
        if end is not None and (isinstance(end, bool) or not isinstance(end, (int, float))):
            raise TypeError("occurred_end must be numeric or null")
        return cls(
            _bounded_text(value["title"], "title", max_bytes=4096),
            _bounded_tuple(value["participants"], "participants", required=True),
            _bounded_tuple(value["goals"], "goals"),
            _bounded_tuple(value["actions"], "actions"),
            _bounded_tuple(value["results"], "results"),
            _bounded_tuple(value["impacts"], "impacts"),
            float(start),
            None if end is None else float(end),
            _optional_identifier(value["thread_ref"], "thread_ref"),
        )


@dataclass(frozen=True, slots=True)
class SemanticMemoryPayload:
    subject_entity: str
    predicate: str
    object_value: JsonValue
    qualifiers: tuple[str, ...]
    semantic_kind: SemanticMemoryKind = field(init=False, default=SemanticMemoryKind.CLAIM)
    object_value_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _bounded_text(self.subject_entity, "subject_entity", max_bytes=2048)
        _bounded_text(self.predicate, "predicate", max_bytes=512)
        frozen = freeze_json(self.object_value)
        thawed = thaw_json(frozen)
        encoded_hash = fingerprint_json(thawed)
        if len(canonical_json(thawed).encode("utf-8")) > 16_384:
            raise ValueError("object_value exceeds the byte limit")
        object.__setattr__(self, "object_value", frozen)
        object.__setattr__(self, "object_value_hash", encoded_hash)
        object.__setattr__(self, "qualifiers", _bounded_tuple(self.qualifiers, "qualifiers"))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "memory_type": LongTermMemoryType.SEMANTIC.value,
            "semantic_kind": self.semantic_kind.value,
            "subject_entity": self.subject_entity,
            "predicate": self.predicate,
            "object_value": thaw_json(cast(FrozenJsonValue, self.object_value)),
            "object_value_hash": self.object_value_hash,
            "qualifiers": list(self.qualifiers),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> SemanticMemoryPayload:
        _exact_keys(
            value,
            {
                "memory_type",
                "semantic_kind",
                "subject_entity",
                "predicate",
                "object_value",
                "object_value_hash",
                "qualifiers",
            },
            "SemanticMemoryPayload",
        )
        if value["memory_type"] != LongTermMemoryType.SEMANTIC.value:
            raise ValueError("SemanticMemoryPayload discriminator differs")
        if value["semantic_kind"] != SemanticMemoryKind.CLAIM.value:
            raise ValueError("SemanticMemoryPayload semantic_kind differs")
        expected_hash = _digest(value["object_value_hash"], "object_value_hash")
        payload = cls(
            _bounded_text(value["subject_entity"], "subject_entity", max_bytes=2048),
            _bounded_text(value["predicate"], "predicate", max_bytes=512),
            cast(JsonValue, value["object_value"]),
            _bounded_tuple(value["qualifiers"], "qualifiers"),
        )
        if payload.object_value_hash != expected_hash:
            raise ValueError("object_value_hash does not bind object_value")
        return payload


SemanticRelationEndpoint = ExistingMemoryTarget | CreatedByOperationTarget


@dataclass(frozen=True, slots=True)
class SemanticRelationMemoryPayload:
    relation_kind: SemanticRelationKind
    source_endpoint: SemanticRelationEndpoint
    target_endpoint: SemanticRelationEndpoint
    semantic_kind: SemanticMemoryKind = field(init=False, default=SemanticMemoryKind.RELATION)

    def __post_init__(self) -> None:
        try:
            relation_kind = SemanticRelationKind(self.relation_kind)
        except (TypeError, ValueError) as exc:
            raise _MemoryMutationAdmissionError(
                MemoryMutationValidationReasonCode.INVALID_RELATION_KIND,
                "semantic relation kind is unsupported",
            ) from exc
        object.__setattr__(self, "relation_kind", relation_kind)
        for endpoint, name in (
            (self.source_endpoint, "source_endpoint"),
            (self.target_endpoint, "target_endpoint"),
        ):
            if not isinstance(endpoint, (ExistingMemoryTarget, CreatedByOperationTarget)):
                raise TypeError(f"{name} must use an exact semantic relation endpoint")
        same_existing_memory = (
            isinstance(self.source_endpoint, ExistingMemoryTarget)
            and isinstance(self.target_endpoint, ExistingMemoryTarget)
            and self.source_endpoint.memory_id == self.target_endpoint.memory_id
        )
        if self.source_endpoint == self.target_endpoint or same_existing_memory:
            raise _MemoryMutationAdmissionError(
                MemoryMutationValidationReasonCode.RELATION_SELF_LOOP,
                "semantic relation cannot be a self relation",
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "memory_type": LongTermMemoryType.SEMANTIC.value,
            "semantic_kind": self.semantic_kind.value,
            "relation_kind": self.relation_kind.value,
            "source_endpoint": self.source_endpoint.to_json(),
            "target_endpoint": self.target_endpoint.to_json(),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> SemanticRelationMemoryPayload:
        _exact_keys(
            value,
            {
                "memory_type",
                "semantic_kind",
                "relation_kind",
                "source_endpoint",
                "target_endpoint",
            },
            "SemanticRelationMemoryPayload",
        )
        if value["memory_type"] != LongTermMemoryType.SEMANTIC.value:
            raise ValueError("SemanticRelationMemoryPayload discriminator differs")
        if value["semantic_kind"] != SemanticMemoryKind.RELATION.value:
            raise ValueError("SemanticRelationMemoryPayload semantic_kind differs")
        source = _mutation_target_from_json(value["source_endpoint"])
        target = _mutation_target_from_json(value["target_endpoint"])
        if source is None or target is None:
            raise ValueError("semantic relation endpoints are required")
        return cls(
            relation_kind=cast(SemanticRelationKind, value["relation_kind"]),
            source_endpoint=source,
            target_endpoint=target,
        )


@dataclass(frozen=True, slots=True)
class ProcedureMemoryPayload:
    name: str
    applicability: tuple[str, ...]
    steps: tuple[str, ...]
    proposed_risk_level: ProcedureRiskLevel

    def __post_init__(self) -> None:
        _bounded_text(self.name, "name", max_bytes=4096)
        object.__setattr__(
            self,
            "applicability",
            _bounded_tuple(self.applicability, "applicability", required=True),
        )
        object.__setattr__(self, "steps", _bounded_tuple(self.steps, "steps", required=True))
        object.__setattr__(
            self, "proposed_risk_level", ProcedureRiskLevel(self.proposed_risk_level)
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "memory_type": LongTermMemoryType.PROCEDURE.value,
            "name": self.name,
            "applicability": list(self.applicability),
            "steps": list(self.steps),
            "proposed_risk_level": self.proposed_risk_level.value,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ProcedureMemoryPayload:
        _exact_keys(
            value,
            {"memory_type", "name", "applicability", "steps", "proposed_risk_level"},
            "ProcedureMemoryPayload",
        )
        if value["memory_type"] != LongTermMemoryType.PROCEDURE.value:
            raise ValueError("ProcedureMemoryPayload discriminator differs")
        return cls(
            _bounded_text(value["name"], "name", max_bytes=4096),
            _bounded_tuple(value["applicability"], "applicability", required=True),
            _bounded_tuple(value["steps"], "steps", required=True),
            ProcedureRiskLevel(value["proposed_risk_level"]),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class ProspectiveTimeTrigger:
    trigger_at: float
    timezone: str

    def __post_init__(self) -> None:
        trigger_at = _optional_timestamp(self.trigger_at, "trigger_at")
        if trigger_at is None:
            raise ValueError("trigger_at is required")
        object.__setattr__(self, "trigger_at", trigger_at)
        _identifier(self.timezone, "timezone", max_length=128)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "trigger_kind": ProspectiveTriggerKind.TIME.value,
            "trigger_at": self.trigger_at,
            "timezone": self.timezone,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ProspectiveTimeTrigger:
        _exact_keys(value, {"trigger_kind", "trigger_at", "timezone"}, "ProspectiveTimeTrigger")
        if value["trigger_kind"] != ProspectiveTriggerKind.TIME.value:
            raise ValueError("ProspectiveTimeTrigger discriminator differs")
        return cls(
            cast(float, _optional_timestamp(value["trigger_at"], "trigger_at")),
            _identifier(value["timezone"], "timezone", max_length=128),
        )


@dataclass(frozen=True, slots=True)
class ProspectiveEventTrigger:
    event_authority_ref: str
    condition: str
    condition_hash: str

    def __post_init__(self) -> None:
        _identifier(self.event_authority_ref, "event_authority_ref", max_length=1024)
        _bounded_text(self.condition, "condition", max_bytes=4096)
        _digest(self.condition_hash, "condition_hash")
        if fingerprint_json(self.condition) != self.condition_hash:
            raise ValueError("condition_hash does not bind condition")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "trigger_kind": ProspectiveTriggerKind.EVENT.value,
            "event_authority_ref": self.event_authority_ref,
            "condition": self.condition,
            "condition_hash": self.condition_hash,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ProspectiveEventTrigger:
        _exact_keys(
            value,
            {"trigger_kind", "event_authority_ref", "condition", "condition_hash"},
            "ProspectiveEventTrigger",
        )
        if value["trigger_kind"] != ProspectiveTriggerKind.EVENT.value:
            raise ValueError("ProspectiveEventTrigger discriminator differs")
        return cls(
            _identifier(value["event_authority_ref"], "event_authority_ref", max_length=1024),
            _bounded_text(value["condition"], "condition", max_bytes=4096),
            _digest(value["condition_hash"], "condition_hash"),
        )


ProspectiveTrigger = ProspectiveTimeTrigger | ProspectiveEventTrigger


def _prospective_trigger_from_json(value: object) -> ProspectiveTrigger:
    trigger = _object(value, "trigger")
    kind = trigger.get("trigger_kind")
    if kind == ProspectiveTriggerKind.TIME.value:
        return ProspectiveTimeTrigger.from_json(trigger)
    if kind == ProspectiveTriggerKind.EVENT.value:
        return ProspectiveEventTrigger.from_json(trigger)
    raise ValueError("trigger has an unknown discriminator")


@dataclass(frozen=True, slots=True)
class ProspectiveMemoryPayload:
    action: str
    trigger: ProspectiveTrigger

    def __post_init__(self) -> None:
        _bounded_text(self.action, "action", max_bytes=8192)
        if not isinstance(self.trigger, (ProspectiveTimeTrigger, ProspectiveEventTrigger)):
            raise TypeError("trigger must use a strict time or event trigger")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "memory_type": LongTermMemoryType.PROSPECTIVE.value,
            "action": self.action,
            "trigger": self.trigger.to_json(),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ProspectiveMemoryPayload:
        _exact_keys(
            value,
            {"memory_type", "action", "trigger"},
            "ProspectiveMemoryPayload",
        )
        if value["memory_type"] != LongTermMemoryType.PROSPECTIVE.value:
            raise ValueError("ProspectiveMemoryPayload discriminator differs")
        return cls(
            _bounded_text(value["action"], "action", max_bytes=8192),
            _prospective_trigger_from_json(value["trigger"]),
        )


MemoryMutationPayload = (
    EpisodeMemoryPayload
    | SemanticMemoryPayload
    | SemanticRelationMemoryPayload
    | ProcedureMemoryPayload
    | ProspectiveMemoryPayload
)
