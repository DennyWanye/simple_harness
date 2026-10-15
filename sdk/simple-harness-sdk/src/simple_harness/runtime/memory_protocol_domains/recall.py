# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Recall contexts, plans and decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from simple_harness.contracts import JsonValue, fingerprint_json

from ..disclosure_protocol import (
    COGNITIVE_MEMORY_SCHEMA_VERSION,
    HUMAN_MEMORY_SCHEMA_VERSION,
    DeliveryRecipient,
    DisclosureContext,
    DisclosureGeneration,
    DisclosurePurpose,
    DisclosureSource,
    DisclosureTrust,
    IntendedAudience,
    _bounded_text,
    _canonical_hash,
    _digest,
    _domain_hash,
    _exact_keys,
    _identifier,
    _object,
    _objects,
    _optional_identifier,
    _positive_int,
    _schema_version,
    _strings,
)
from ..evidence_protocol import EvidenceRef, _evidence_refs, _refs_from_json
from .common import (
    _bounded_enum_tuple,
    _bounded_identifiers,
    _bounded_tuple,
    _optional_timestamp,
    _trusted_current_time,
)
from .types import (
    RECALL_DECISION_SCHEMA_VERSION,
    LongTermMemoryType,
    _cognitive_schema_version,
    _recall_decision_schema_version,
)


class RecallReasonCode(StrEnum):
    NO_RECALL_CONTEXT_SUFFICIENT = "recall_context_sufficient"
    USER_PREFERENCE_DEPENDENCY = "recall_user_preference_dependency"
    PAST_EVENT_DEPENDENCY = "recall_past_event_dependency"
    USER_FACT_DEPENDENCY = "recall_user_fact_dependency"
    PROCEDURE_DEPENDENCY = "recall_procedure_dependency"
    FUTURE_INTENTION_DEPENDENCY = "recall_future_intention_dependency"
    SHORT_HORIZON_DEPENDENCY = "recall_short_horizon_dependency"
    TASK_RESUME_DEPENDENCY = "recall_task_resume_dependency"
    DISCLOSURE_DENIED = "recall_disclosure_denied"
    INVALID_PLAN = "recall_invalid_plan"
    BUDGET_EXHAUSTED = "recall_budget_exhausted"
    NO_ELIGIBLE_MEMORY = "recall_no_eligible_memory"
    NEEDS_USER_CONFIRMATION = "recall_needs_user_confirmation"


_RECALL_DEPENDENCY_REASONS = frozenset(
    {
        RecallReasonCode.USER_PREFERENCE_DEPENDENCY,
        RecallReasonCode.PAST_EVENT_DEPENDENCY,
        RecallReasonCode.USER_FACT_DEPENDENCY,
        RecallReasonCode.PROCEDURE_DEPENDENCY,
        RecallReasonCode.FUTURE_INTENTION_DEPENDENCY,
        RecallReasonCode.SHORT_HORIZON_DEPENDENCY,
        RecallReasonCode.TASK_RESUME_DEPENDENCY,
    }
)
_NO_RECALL_REASONS = frozenset(
    {
        RecallReasonCode.NO_RECALL_CONTEXT_SUFFICIENT,
        RecallReasonCode.BUDGET_EXHAUSTED,
        RecallReasonCode.NO_ELIGIBLE_MEMORY,
    }
)
_REJECTED_REASONS = frozenset(
    {
        RecallReasonCode.DISCLOSURE_DENIED,
        RecallReasonCode.INVALID_PLAN,
    }
)


class RecallDecisionOutcome(StrEnum):
    NO_RECALL = "no_recall"
    RECALL = "recall"
    NEEDS_USER_CONFIRMATION = "needs_user_confirmation"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RecallConfirmationItem:
    """A post-gate conflicting candidate that requires user confirmation."""

    conflict_group_ref: str
    memory_type: LongTermMemoryType
    memory_ref: str

    def __post_init__(self) -> None:
        _identifier(self.conflict_group_ref, "conflict_group_ref")
        object.__setattr__(self, "memory_type", LongTermMemoryType(self.memory_type))
        _identifier(self.memory_ref, "memory_ref")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "conflict_group_ref": self.conflict_group_ref,
            "memory_type": self.memory_type.value,
            "memory_ref": self.memory_ref,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> RecallConfirmationItem:
        _exact_keys(
            value,
            {"conflict_group_ref", "memory_type", "memory_ref"},
            "RecallConfirmationItem",
        )
        return cls(
            conflict_group_ref=_identifier(value["conflict_group_ref"], "conflict_group_ref"),
            memory_type=LongTermMemoryType(value["memory_type"]),  # type: ignore[arg-type]
            memory_ref=_identifier(value["memory_ref"], "memory_ref"),
        )


@dataclass(frozen=True, slots=True)
class RecallBudget:
    max_items: int
    max_bytes: int
    max_tokens: int
    deadline_ms: int

    def __post_init__(self) -> None:
        limits = {
            "max_items": 32,
            "max_bytes": 65_536,
            "max_tokens": 8_192,
            "deadline_ms": 2_000,
        }
        for name, maximum in limits.items():
            value = _positive_int(getattr(self, name), name)
            if value > maximum:
                raise ValueError(f"{name} exceeds protocol maximum {maximum}")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "max_items": self.max_items,
            "max_bytes": self.max_bytes,
            "max_tokens": self.max_tokens,
            "deadline_ms": self.deadline_ms,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> RecallBudget:
        expected = {"max_items", "max_bytes", "max_tokens", "deadline_ms"}
        _exact_keys(value, expected, "RecallBudget")
        return cls(
            _positive_int(value["max_items"], "max_items"),
            _positive_int(value["max_bytes"], "max_bytes"),
            _positive_int(value["max_tokens"], "max_tokens"),
            _positive_int(value["deadline_ms"], "deadline_ms"),
        )


@dataclass(frozen=True, slots=True)
class LegacyRecallContextV1:
    run_id: str
    subject: str
    turn_id: str
    query: str
    active_task_scope_id: str | None
    available_memory_types: tuple[LongTermMemoryType, ...]
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    budget: RecallBudget
    schema_version: int = HUMAN_MEMORY_SCHEMA_VERSION
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != HUMAN_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported RecallContext schema_version")
        for value, name in (
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.turn_id, "turn_id"),
        ):
            _identifier(value, name)
        _bounded_text(self.query, "query", max_bytes=16_384)
        _optional_identifier(self.active_task_scope_id, "active_task_scope_id")
        types = tuple(LongTermMemoryType(item) for item in self.available_memory_types)
        if not types or len(set(types)) != len(types):
            raise ValueError("available_memory_types must be non-empty and unique")
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        refs = _evidence_refs(self.evidence_refs)
        if not isinstance(self.budget, RecallBudget):
            raise TypeError("budget must use RecallBudget")
        object.__setattr__(self, "available_memory_types", types)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "context_hash", _canonical_hash(self.to_json()))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "subject": self.subject,
            "turn_id": self.turn_id,
            "query": self.query,
            "active_task_scope_id": self.active_task_scope_id,
            "available_memory_types": [item.value for item in self.available_memory_types],
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "budget": self.budget.to_json(),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> LegacyRecallContextV1:
        _exact_keys(
            value,
            {
                "schema_version",
                "run_id",
                "subject",
                "turn_id",
                "query",
                "active_task_scope_id",
                "available_memory_types",
                "disclosure_context",
                "evidence_refs",
                "budget",
            },
            "RecallContext",
        )
        return cls(
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            turn_id=_identifier(value["turn_id"], "turn_id"),
            query=_bounded_text(value["query"], "query", max_bytes=16_384),
            active_task_scope_id=_optional_identifier(
                value["active_task_scope_id"], "active_task_scope_id"
            ),
            available_memory_types=tuple(
                LongTermMemoryType(item)
                for item in _strings(value["available_memory_types"], "available_memory_types")
            ),
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            budget=RecallBudget.from_json(_object(value["budget"], "budget")),
            schema_version=_schema_version(value["schema_version"], "RecallContext"),
        )


@dataclass(frozen=True, slots=True)
class LegacyRecallPlanV1:
    plan_id: str
    run_id: str
    subject: str
    query: str
    requested_memory_types: tuple[LongTermMemoryType, ...]
    include_short_horizon: bool
    task_scope_ids: tuple[str, ...]
    entity_constraints: tuple[str, ...]
    earliest_occurred_at: float | None
    latest_occurred_at: float | None
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    budget: RecallBudget
    idempotency_key: str
    reason_codes: tuple[RecallReasonCode, ...]
    schema_version: int = HUMAN_MEMORY_SCHEMA_VERSION
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != HUMAN_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported RecallPlan schema_version")
        for value, name in (
            (self.plan_id, "plan_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _identifier(value, name)
        _bounded_text(self.query, "query", max_bytes=16_384)
        memory_types = tuple(LongTermMemoryType(item) for item in self.requested_memory_types)
        if len(set(memory_types)) != len(memory_types):
            raise ValueError("requested_memory_types must be unique")
        if not isinstance(self.include_short_horizon, bool):
            raise TypeError("include_short_horizon must be a boolean")
        if not memory_types and not self.include_short_horizon:
            raise ValueError("RecallPlan must request a long-term type or short-horizon recall")
        task_scope_ids = tuple(_identifier(item, "task_scope_id") for item in self.task_scope_ids)
        entities = tuple(
            _bounded_text(item, "entity_constraint", max_bytes=512)
            for item in self.entity_constraints
        )
        if len(set(task_scope_ids)) != len(task_scope_ids):
            raise ValueError("task_scope_ids must be unique")
        if len(set(entities)) != len(entities):
            raise ValueError("entity_constraints must be unique")
        for time_value, time_name in (
            (self.earliest_occurred_at, "earliest_occurred_at"),
            (self.latest_occurred_at, "latest_occurred_at"),
        ):
            if time_value is not None and (
                isinstance(time_value, bool)
                or not isinstance(time_value, (int, float))
                or float(time_value) < 0
            ):
                raise ValueError(f"{time_name} must be a non-negative timestamp or null")
        if (
            self.earliest_occurred_at is not None
            and self.latest_occurred_at is not None
            and self.earliest_occurred_at > self.latest_occurred_at
        ):
            raise ValueError("recall time range is inverted")
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        refs = _evidence_refs(self.evidence_refs)
        if not refs:
            raise ValueError("RecallPlan requires evidence_refs")
        if not isinstance(self.budget, RecallBudget):
            raise TypeError("budget must use RecallBudget")
        reasons = tuple(RecallReasonCode(reason) for reason in self.reason_codes)
        if not reasons or len(set(reasons)) != len(reasons):
            raise ValueError("reason_codes must be non-empty and unique")
        object.__setattr__(self, "requested_memory_types", memory_types)
        object.__setattr__(self, "task_scope_ids", task_scope_ids)
        object.__setattr__(self, "entity_constraints", entities)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "plan_hash", _canonical_hash(self.to_json()))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "query": self.query,
            "requested_memory_types": [item.value for item in self.requested_memory_types],
            "include_short_horizon": self.include_short_horizon,
            "task_scope_ids": list(self.task_scope_ids),
            "entity_constraints": list(self.entity_constraints),
            "earliest_occurred_at": self.earliest_occurred_at,
            "latest_occurred_at": self.latest_occurred_at,
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "budget": self.budget.to_json(),
            "idempotency_key": self.idempotency_key,
            "reason_codes": [reason.value for reason in self.reason_codes],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> LegacyRecallPlanV1:
        _exact_keys(
            value,
            {
                "schema_version",
                "plan_id",
                "run_id",
                "subject",
                "query",
                "requested_memory_types",
                "include_short_horizon",
                "task_scope_ids",
                "entity_constraints",
                "earliest_occurred_at",
                "latest_occurred_at",
                "disclosure_context",
                "evidence_refs",
                "budget",
                "idempotency_key",
                "reason_codes",
            },
            "RecallPlan",
        )
        include_short_horizon = value["include_short_horizon"]
        if not isinstance(include_short_horizon, bool):
            raise TypeError("include_short_horizon must be a boolean")
        timestamps: dict[str, float | None] = {}
        for name in ("earliest_occurred_at", "latest_occurred_at"):
            timestamp = value[name]
            if timestamp is None:
                timestamps[name] = None
            elif isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise TypeError(f"{name} must be numeric or null")
            else:
                timestamps[name] = float(timestamp)
        return cls(
            plan_id=_identifier(value["plan_id"], "plan_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            query=_bounded_text(value["query"], "query", max_bytes=16_384),
            requested_memory_types=tuple(
                LongTermMemoryType(item)
                for item in _strings(value["requested_memory_types"], "requested_memory_types")
            ),
            include_short_horizon=include_short_horizon,
            task_scope_ids=_strings(value["task_scope_ids"], "task_scope_ids"),
            entity_constraints=_strings(value["entity_constraints"], "entity_constraints"),
            earliest_occurred_at=timestamps["earliest_occurred_at"],
            latest_occurred_at=timestamps["latest_occurred_at"],
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            budget=RecallBudget.from_json(_object(value["budget"], "budget")),
            idempotency_key=_identifier(value["idempotency_key"], "idempotency_key"),
            reason_codes=tuple(
                RecallReasonCode(item) for item in _strings(value["reason_codes"], "reason_codes")
            ),
            schema_version=_schema_version(value["schema_version"], "RecallPlan"),
        )


class RecallSelectorDomain(StrEnum):
    MEMORY_TYPE = "memory_type"
    TASK_SCOPE = "task_scope"
    ENTITY = "entity"
    TIME = "time"
    EVENT = "event"
    ENVIRONMENT = "environment"
    TASK_PHASE = "task_phase"
    SHORT_HORIZON = "short_horizon"


class RecallRetrievalMode(StrEnum):
    EXACT = "exact"
    VECTOR = "vector"
    FULL_TEXT = "full_text"
    GRAPH = "graph"
    TEMPORAL = "temporal"


class RecallCandidateCountStage(StrEnum):
    AFTER_ALL_ELIGIBILITY_GATES = "after_all_eligibility_gates"


def _validate_recall_selector_consistency(
    domains: tuple[RecallSelectorDomain, ...],
    *,
    memory_types: tuple[LongTermMemoryType, ...],
    short_horizon: bool,
    task_scope_ids: tuple[str, ...],
    entity_constraints: tuple[str, ...],
    earliest_occurred_at: float | None,
    latest_occurred_at: float | None,
    event_constraint_refs: tuple[str, ...],
    environment_constraint_refs: tuple[str, ...],
    task_phase_authority_refs: tuple[str, ...],
    name: str,
) -> None:
    """Require every selector domain to have exactly one corresponding constraint."""

    constraints = {
        RecallSelectorDomain.MEMORY_TYPE: bool(memory_types),
        RecallSelectorDomain.TASK_SCOPE: bool(task_scope_ids),
        RecallSelectorDomain.ENTITY: bool(entity_constraints),
        RecallSelectorDomain.TIME: (
            earliest_occurred_at is not None or latest_occurred_at is not None
        ),
        RecallSelectorDomain.EVENT: bool(event_constraint_refs),
        RecallSelectorDomain.ENVIRONMENT: bool(environment_constraint_refs),
        RecallSelectorDomain.TASK_PHASE: bool(task_phase_authority_refs),
        RecallSelectorDomain.SHORT_HORIZON: short_horizon,
    }
    domain_set = set(domains)
    for domain, present in constraints.items():
        if (domain in domain_set) != present:
            raise ValueError(f"{name} selector domain {domain.value} and constraint differ")


def _validate_recall_disclosure(context: DisclosureContext) -> None:
    """Fail closed for any non-current, external, unknown, or untrusted authority."""

    if context.trust is not DisclosureTrust.TRUSTED_AUTHORITY:
        raise ValueError("recall requires trusted disclosure authority")
    if context.source not in {
        DisclosureSource.AUTHENTICATED_HOST,
        DisclosureSource.AUTHENTICATED_UI,
        DisclosureSource.AUDIT_ACCESS_DECISION,
    }:
        raise ValueError("recall rejects unknown or untrusted disclosure source")
    if context.generation is not DisclosureGeneration.CURRENT:
        raise ValueError("recall rejects stale, conflicted, or unknown disclosure")
    if context.purpose is DisclosurePurpose.UNKNOWN:
        raise ValueError("recall rejects unknown disclosure purpose")
    if context.recipient in {
        DeliveryRecipient.EXTERNAL_PARTY,
        DeliveryRecipient.PUBLIC,
        DeliveryRecipient.UNKNOWN,
    }:
        raise ValueError("recall rejects external or unknown recipient")
    if context.intended_audience in {
        IntendedAudience.EXTERNAL,
        IntendedAudience.PUBLIC,
        IntendedAudience.UNKNOWN,
    }:
        raise ValueError("recall rejects external or unknown audience")


def _bounded_digests(
    value: object,
    name: str,
    *,
    max_items: int = 128,
) -> tuple[str, ...]:
    """Validate a bounded Host-owned set of canonical SHA-256 authorities."""

    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} must be an array")
    result = tuple(_digest(item, f"{name} item") for item in value)
    if len(result) > max_items:
        raise ValueError(f"{name} exceeds the item limit")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must be unique")
    return result


def _validate_time_window(earliest: float | None, latest: float | None, *, name: str) -> None:
    if earliest is not None and latest is not None and earliest > latest:
        raise ValueError(f"{name} time range is inverted")


@dataclass(frozen=True, slots=True)
class RecallContext:
    """Exact Host authority from which a model may only narrow recall."""

    run_id: str
    subject: str
    turn_id: str
    context_revision: int
    expires_at: float
    query: str
    active_task_scope_id: str | None
    available_memory_types: tuple[LongTermMemoryType, ...]
    short_horizon_allowed: bool
    allowed_selector_domains: tuple[RecallSelectorDomain, ...]
    allowed_retrieval_modes: tuple[RecallRetrievalMode, ...]
    allowed_task_scope_ids: tuple[str, ...]
    allowed_entity_constraints: tuple[str, ...]
    earliest_occurred_at: float | None
    latest_occurred_at: float | None
    event_constraint_refs: tuple[str, ...]
    environment_constraint_refs: tuple[str, ...]
    task_phase_authority_refs: tuple[str, ...]
    procedure_applicability_fingerprints: tuple[str, ...]
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    budget: RecallBudget
    schema_version: int = COGNITIVE_MEMORY_SCHEMA_VERSION
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != COGNITIVE_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported RecallContext schema_version")
        for value, name in (
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.turn_id, "turn_id"),
        ):
            _identifier(value, name)
        _positive_int(self.context_revision, "context_revision")
        expires_at = _optional_timestamp(self.expires_at, "expires_at")
        if expires_at is None:
            raise ValueError("expires_at is required")
        _bounded_text(self.query, "query", max_bytes=16_384)
        active_scope = _optional_identifier(self.active_task_scope_id, "active_task_scope_id")
        types = cast(
            tuple[LongTermMemoryType, ...],
            _bounded_enum_tuple(
                self.available_memory_types,
                "available_memory_types",
                LongTermMemoryType,
                required=True,
            ),
        )
        if not isinstance(self.short_horizon_allowed, bool):
            raise TypeError("short_horizon_allowed must be a boolean")
        domains = cast(
            tuple[RecallSelectorDomain, ...],
            _bounded_enum_tuple(
                self.allowed_selector_domains,
                "allowed_selector_domains",
                RecallSelectorDomain,
                required=True,
            ),
        )
        modes = cast(
            tuple[RecallRetrievalMode, ...],
            _bounded_enum_tuple(
                self.allowed_retrieval_modes,
                "allowed_retrieval_modes",
                RecallRetrievalMode,
                required=True,
            ),
        )
        scopes = _bounded_identifiers(self.allowed_task_scope_ids, "allowed_task_scope_ids")
        entities = _bounded_tuple(
            self.allowed_entity_constraints,
            "allowed_entity_constraints",
            max_items=128,
            max_item_bytes=512,
        )
        if active_scope is not None and active_scope not in scopes:
            raise ValueError("active_task_scope_id must be in allowed_task_scope_ids")
        if self.short_horizon_allowed != (RecallSelectorDomain.SHORT_HORIZON in domains):
            raise ValueError("short_horizon_allowed and SHORT_HORIZON selector authority differ")
        earliest = _optional_timestamp(self.earliest_occurred_at, "earliest_occurred_at")
        latest = _optional_timestamp(self.latest_occurred_at, "latest_occurred_at")
        _validate_time_window(earliest, latest, name="RecallContext")
        events = _bounded_identifiers(self.event_constraint_refs, "event_constraint_refs")
        environments = _bounded_identifiers(
            self.environment_constraint_refs, "environment_constraint_refs"
        )
        phases = _bounded_identifiers(self.task_phase_authority_refs, "task_phase_authority_refs")
        procedure_applicability = _bounded_digests(
            self.procedure_applicability_fingerprints,
            "procedure_applicability_fingerprints",
        )
        _validate_recall_selector_consistency(
            domains,
            memory_types=types,
            short_horizon=self.short_horizon_allowed,
            task_scope_ids=scopes,
            entity_constraints=entities,
            earliest_occurred_at=earliest,
            latest_occurred_at=latest,
            event_constraint_refs=events,
            environment_constraint_refs=environments,
            task_phase_authority_refs=phases,
            name="RecallContext",
        )
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        evidence_refs = _evidence_refs(self.evidence_refs)
        if not evidence_refs:
            raise ValueError("RecallContext requires evidence_refs")
        if not isinstance(self.budget, RecallBudget):
            raise TypeError("budget must use RecallBudget")
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "active_task_scope_id", active_scope)
        object.__setattr__(self, "available_memory_types", types)
        object.__setattr__(self, "allowed_selector_domains", domains)
        object.__setattr__(self, "allowed_retrieval_modes", modes)
        object.__setattr__(self, "allowed_task_scope_ids", scopes)
        object.__setattr__(self, "allowed_entity_constraints", entities)
        object.__setattr__(self, "earliest_occurred_at", earliest)
        object.__setattr__(self, "latest_occurred_at", latest)
        object.__setattr__(self, "event_constraint_refs", events)
        object.__setattr__(self, "environment_constraint_refs", environments)
        object.__setattr__(self, "task_phase_authority_refs", phases)
        object.__setattr__(
            self,
            "procedure_applicability_fingerprints",
            procedure_applicability,
        )
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(
            self,
            "context_hash",
            _domain_hash("simple-harness/recall-context/v2", self.to_json()),
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "subject": self.subject,
            "turn_id": self.turn_id,
            "context_revision": self.context_revision,
            "expires_at": self.expires_at,
            "query": self.query,
            "active_task_scope_id": self.active_task_scope_id,
            "available_memory_types": [item.value for item in self.available_memory_types],
            "short_horizon_allowed": self.short_horizon_allowed,
            "allowed_selector_domains": [item.value for item in self.allowed_selector_domains],
            "allowed_retrieval_modes": [item.value for item in self.allowed_retrieval_modes],
            "allowed_task_scope_ids": list(self.allowed_task_scope_ids),
            "allowed_entity_constraints": list(self.allowed_entity_constraints),
            "earliest_occurred_at": self.earliest_occurred_at,
            "latest_occurred_at": self.latest_occurred_at,
            "event_constraint_refs": list(self.event_constraint_refs),
            "environment_constraint_refs": list(self.environment_constraint_refs),
            "task_phase_authority_refs": list(self.task_phase_authority_refs),
            "procedure_applicability_fingerprints": list(self.procedure_applicability_fingerprints),
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [item.to_json() for item in self.evidence_refs],
            "budget": self.budget.to_json(),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> RecallContext:
        expected = {
            "schema_version",
            "run_id",
            "subject",
            "turn_id",
            "context_revision",
            "expires_at",
            "query",
            "active_task_scope_id",
            "available_memory_types",
            "short_horizon_allowed",
            "allowed_selector_domains",
            "allowed_retrieval_modes",
            "allowed_task_scope_ids",
            "allowed_entity_constraints",
            "earliest_occurred_at",
            "latest_occurred_at",
            "event_constraint_refs",
            "environment_constraint_refs",
            "task_phase_authority_refs",
            "procedure_applicability_fingerprints",
            "disclosure_context",
            "evidence_refs",
            "budget",
        }
        _exact_keys(value, expected, "RecallContext")
        short_horizon = value["short_horizon_allowed"]
        if not isinstance(short_horizon, bool):
            raise TypeError("short_horizon_allowed must be a boolean")
        return cls(
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            turn_id=_identifier(value["turn_id"], "turn_id"),
            context_revision=_positive_int(value["context_revision"], "context_revision"),
            expires_at=cast(float, _optional_timestamp(value["expires_at"], "expires_at")),
            query=_bounded_text(value["query"], "query", max_bytes=16_384),
            active_task_scope_id=_optional_identifier(
                value["active_task_scope_id"], "active_task_scope_id"
            ),
            available_memory_types=tuple(
                LongTermMemoryType(item)
                for item in _strings(value["available_memory_types"], "available_memory_types")
            ),
            short_horizon_allowed=short_horizon,
            allowed_selector_domains=tuple(
                RecallSelectorDomain(item)
                for item in _strings(value["allowed_selector_domains"], "allowed_selector_domains")
            ),
            allowed_retrieval_modes=tuple(
                RecallRetrievalMode(item)
                for item in _strings(value["allowed_retrieval_modes"], "allowed_retrieval_modes")
            ),
            allowed_task_scope_ids=_strings(
                value["allowed_task_scope_ids"], "allowed_task_scope_ids"
            ),
            allowed_entity_constraints=_strings(
                value["allowed_entity_constraints"], "allowed_entity_constraints"
            ),
            earliest_occurred_at=_optional_timestamp(
                value["earliest_occurred_at"], "earliest_occurred_at"
            ),
            latest_occurred_at=_optional_timestamp(
                value["latest_occurred_at"], "latest_occurred_at"
            ),
            event_constraint_refs=_strings(value["event_constraint_refs"], "event_constraint_refs"),
            environment_constraint_refs=_strings(
                value["environment_constraint_refs"], "environment_constraint_refs"
            ),
            task_phase_authority_refs=_strings(
                value["task_phase_authority_refs"], "task_phase_authority_refs"
            ),
            procedure_applicability_fingerprints=_strings(
                value["procedure_applicability_fingerprints"],
                "procedure_applicability_fingerprints",
            ),
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            budget=RecallBudget.from_json(_object(value["budget"], "budget")),
            schema_version=_cognitive_schema_version(value["schema_version"], "RecallContext"),
        )


@dataclass(frozen=True, slots=True)
class RecallPlan:
    plan_id: str
    run_id: str
    subject: str
    context_hash: str
    context_revision: int
    query: str
    requested_memory_types: tuple[LongTermMemoryType, ...]
    include_short_horizon: bool
    selector_domains: tuple[RecallSelectorDomain, ...]
    retrieval_modes: tuple[RecallRetrievalMode, ...]
    task_scope_ids: tuple[str, ...]
    entity_constraints: tuple[str, ...]
    earliest_occurred_at: float | None
    latest_occurred_at: float | None
    event_constraint_refs: tuple[str, ...]
    environment_constraint_refs: tuple[str, ...]
    task_phase_authority_refs: tuple[str, ...]
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    budget: RecallBudget
    idempotency_key: str
    reason_codes: tuple[RecallReasonCode, ...]
    schema_version: int = COGNITIVE_MEMORY_SCHEMA_VERSION
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != COGNITIVE_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported RecallPlan schema_version")
        for value, name in (
            (self.plan_id, "plan_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _identifier(value, name)
        _digest(self.context_hash, "context_hash")
        _positive_int(self.context_revision, "context_revision")
        _bounded_text(self.query, "query", max_bytes=16_384)
        memory_types = cast(
            tuple[LongTermMemoryType, ...],
            _bounded_enum_tuple(
                self.requested_memory_types,
                "requested_memory_types",
                LongTermMemoryType,
            ),
        )
        if not isinstance(self.include_short_horizon, bool):
            raise TypeError("include_short_horizon must be a boolean")
        if not memory_types and not self.include_short_horizon:
            raise ValueError("RecallPlan must request memory or short-horizon recall")
        domains = cast(
            tuple[RecallSelectorDomain, ...],
            _bounded_enum_tuple(
                self.selector_domains,
                "selector_domains",
                RecallSelectorDomain,
                required=True,
            ),
        )
        modes = cast(
            tuple[RecallRetrievalMode, ...],
            _bounded_enum_tuple(
                self.retrieval_modes,
                "retrieval_modes",
                RecallRetrievalMode,
                required=True,
            ),
        )
        if self.include_short_horizon and RecallSelectorDomain.SHORT_HORIZON not in domains:
            raise ValueError("short-horizon recall requires its selector domain")
        scopes = _bounded_identifiers(self.task_scope_ids, "task_scope_ids")
        entities = _bounded_tuple(
            self.entity_constraints,
            "entity_constraints",
            max_items=128,
            max_item_bytes=512,
        )
        earliest = _optional_timestamp(self.earliest_occurred_at, "earliest_occurred_at")
        latest = _optional_timestamp(self.latest_occurred_at, "latest_occurred_at")
        _validate_time_window(earliest, latest, name="RecallPlan")
        events = _bounded_identifiers(self.event_constraint_refs, "event_constraint_refs")
        environments = _bounded_identifiers(
            self.environment_constraint_refs, "environment_constraint_refs"
        )
        phases = _bounded_identifiers(self.task_phase_authority_refs, "task_phase_authority_refs")
        _validate_recall_selector_consistency(
            domains,
            memory_types=memory_types,
            short_horizon=self.include_short_horizon,
            task_scope_ids=scopes,
            entity_constraints=entities,
            earliest_occurred_at=earliest,
            latest_occurred_at=latest,
            event_constraint_refs=events,
            environment_constraint_refs=environments,
            task_phase_authority_refs=phases,
            name="RecallPlan",
        )
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        evidence = _evidence_refs(self.evidence_refs)
        if not evidence:
            raise ValueError("RecallPlan requires evidence_refs")
        if not isinstance(self.budget, RecallBudget):
            raise TypeError("budget must use RecallBudget")
        reasons = cast(
            tuple[RecallReasonCode, ...],
            _bounded_enum_tuple(
                self.reason_codes,
                "reason_codes",
                RecallReasonCode,
                required=True,
            ),
        )
        object.__setattr__(self, "requested_memory_types", memory_types)
        object.__setattr__(self, "selector_domains", domains)
        object.__setattr__(self, "retrieval_modes", modes)
        object.__setattr__(self, "task_scope_ids", scopes)
        object.__setattr__(self, "entity_constraints", entities)
        object.__setattr__(self, "earliest_occurred_at", earliest)
        object.__setattr__(self, "latest_occurred_at", latest)
        object.__setattr__(self, "event_constraint_refs", events)
        object.__setattr__(self, "environment_constraint_refs", environments)
        object.__setattr__(self, "task_phase_authority_refs", phases)
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self, "plan_hash", _domain_hash("simple-harness/recall-plan/v2", self.to_json())
        )

    def validate_narrowing(self, context: RecallContext, *, current_time: float) -> None:
        """Fail closed unless every model-controlled selector narrows Host authority."""

        if not isinstance(context, RecallContext):
            raise TypeError("context must use RecallContext")
        trusted_now = _trusted_current_time(current_time)
        if context.expires_at <= trusted_now:
            raise ValueError("RecallContext is expired at trusted current_time")
        if self.run_id != context.run_id or self.subject != context.subject:
            raise ValueError("RecallPlan run/subject differs from RecallContext")
        if self.context_hash != context.context_hash:
            raise ValueError("RecallPlan context_hash differs")
        if self.context_revision != context.context_revision:
            raise ValueError("RecallPlan context_revision differs")
        if self.query != context.query:
            raise ValueError("RecallPlan query differs from RecallContext")
        subset_checks: tuple[tuple[set[object], set[object], str], ...] = (
            (set(self.requested_memory_types), set(context.available_memory_types), "memory types"),
            (set(self.selector_domains), set(context.allowed_selector_domains), "selector domains"),
            (set(self.retrieval_modes), set(context.allowed_retrieval_modes), "retrieval modes"),
            (set(self.task_scope_ids), set(context.allowed_task_scope_ids), "task scopes"),
            (set(self.entity_constraints), set(context.allowed_entity_constraints), "entities"),
            (
                set(self.event_constraint_refs),
                set(context.event_constraint_refs),
                "event constraints",
            ),
            (
                set(self.environment_constraint_refs),
                set(context.environment_constraint_refs),
                "environment constraints",
            ),
            (
                set(self.task_phase_authority_refs),
                set(context.task_phase_authority_refs),
                "task phase constraints",
            ),
        )
        for requested, allowed, name in subset_checks:
            if not requested <= allowed:
                raise ValueError(f"RecallPlan expands {name}")
        mandatory_domains = set(context.allowed_selector_domains) - {
            RecallSelectorDomain.SHORT_HORIZON,
            # Available long-term types are a maximum capability. A valid
            # short-horizon-only plan must be able to narrow them to none.
            RecallSelectorDomain.MEMORY_TYPE,
        }
        if not mandatory_domains <= set(self.selector_domains):
            raise ValueError("RecallPlan removes a mandatory Host selector domain")
        if self.include_short_horizon and not context.short_horizon_allowed:
            raise ValueError("RecallPlan expands short-horizon authority")
        if context.earliest_occurred_at is not None and (
            self.earliest_occurred_at is None
            or self.earliest_occurred_at < context.earliest_occurred_at
        ):
            raise ValueError("RecallPlan expands earliest time")
        if context.latest_occurred_at is not None and (
            self.latest_occurred_at is None or self.latest_occurred_at > context.latest_occurred_at
        ):
            raise ValueError("RecallPlan expands latest time")
        for name in ("max_items", "max_bytes", "max_tokens", "deadline_ms"):
            if getattr(self.budget, name) > getattr(context.budget, name):
                raise ValueError(f"RecallPlan budget expands {name}")
        if self.disclosure_context.to_json() != context.disclosure_context.to_json():
            raise ValueError("RecallPlan disclosure_context must exactly bind Host authority")
        allowed_evidence = {fingerprint_json(item.to_json()) for item in context.evidence_refs}
        if (
            not {fingerprint_json(item.to_json()) for item in self.evidence_refs}
            <= allowed_evidence
        ):
            raise ValueError("RecallPlan evidence_refs expand Host evidence")

    def unsupported_capabilities(self) -> tuple[str, ...]:
        """Return every V1-unsupported capability in deterministic order."""

        ordered: tuple[tuple[object, str], ...] = (
            (RecallSelectorDomain.EVENT, "selector:event"),
            (RecallSelectorDomain.ENVIRONMENT, "selector:environment"),
            (RecallSelectorDomain.TASK_PHASE, "selector:task_phase"),
            (RecallRetrievalMode.EXACT, "retrieval:exact"),
            (RecallRetrievalMode.TEMPORAL, "retrieval:temporal"),
            (RecallRetrievalMode.GRAPH, "retrieval:graph"),
        )
        present = set(self.selector_domains) | set(self.retrieval_modes)
        return tuple(label for capability, label in ordered if capability in present)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "context_hash": self.context_hash,
            "context_revision": self.context_revision,
            "query": self.query,
            "requested_memory_types": [item.value for item in self.requested_memory_types],
            "include_short_horizon": self.include_short_horizon,
            "selector_domains": [item.value for item in self.selector_domains],
            "retrieval_modes": [item.value for item in self.retrieval_modes],
            "task_scope_ids": list(self.task_scope_ids),
            "entity_constraints": list(self.entity_constraints),
            "earliest_occurred_at": self.earliest_occurred_at,
            "latest_occurred_at": self.latest_occurred_at,
            "event_constraint_refs": list(self.event_constraint_refs),
            "environment_constraint_refs": list(self.environment_constraint_refs),
            "task_phase_authority_refs": list(self.task_phase_authority_refs),
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [item.to_json() for item in self.evidence_refs],
            "budget": self.budget.to_json(),
            "idempotency_key": self.idempotency_key,
            "reason_codes": [item.value for item in self.reason_codes],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> RecallPlan:
        expected = {
            "schema_version",
            "plan_id",
            "run_id",
            "subject",
            "context_hash",
            "context_revision",
            "query",
            "requested_memory_types",
            "include_short_horizon",
            "selector_domains",
            "retrieval_modes",
            "task_scope_ids",
            "entity_constraints",
            "earliest_occurred_at",
            "latest_occurred_at",
            "event_constraint_refs",
            "environment_constraint_refs",
            "task_phase_authority_refs",
            "disclosure_context",
            "evidence_refs",
            "budget",
            "idempotency_key",
            "reason_codes",
        }
        _exact_keys(value, expected, "RecallPlan")
        include_short_horizon = value["include_short_horizon"]
        if not isinstance(include_short_horizon, bool):
            raise TypeError("include_short_horizon must be a boolean")
        return cls(
            plan_id=_identifier(value["plan_id"], "plan_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            context_hash=_digest(value["context_hash"], "context_hash"),
            context_revision=_positive_int(value["context_revision"], "context_revision"),
            query=_bounded_text(value["query"], "query", max_bytes=16_384),
            requested_memory_types=tuple(
                LongTermMemoryType(item)
                for item in _strings(value["requested_memory_types"], "requested_memory_types")
            ),
            include_short_horizon=include_short_horizon,
            selector_domains=tuple(
                RecallSelectorDomain(item)
                for item in _strings(value["selector_domains"], "selector_domains")
            ),
            retrieval_modes=tuple(
                RecallRetrievalMode(item)
                for item in _strings(value["retrieval_modes"], "retrieval_modes")
            ),
            task_scope_ids=_strings(value["task_scope_ids"], "task_scope_ids"),
            entity_constraints=_strings(value["entity_constraints"], "entity_constraints"),
            earliest_occurred_at=_optional_timestamp(
                value["earliest_occurred_at"], "earliest_occurred_at"
            ),
            latest_occurred_at=_optional_timestamp(
                value["latest_occurred_at"], "latest_occurred_at"
            ),
            event_constraint_refs=_strings(value["event_constraint_refs"], "event_constraint_refs"),
            environment_constraint_refs=_strings(
                value["environment_constraint_refs"], "environment_constraint_refs"
            ),
            task_phase_authority_refs=_strings(
                value["task_phase_authority_refs"], "task_phase_authority_refs"
            ),
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            budget=RecallBudget.from_json(_object(value["budget"], "budget")),
            idempotency_key=_identifier(value["idempotency_key"], "idempotency_key"),
            reason_codes=tuple(
                RecallReasonCode(item) for item in _strings(value["reason_codes"], "reason_codes")
            ),
            schema_version=_cognitive_schema_version(value["schema_version"], "RecallPlan"),
        )


@dataclass(frozen=True, slots=True)
class LegacyRecallDecisionV3:
    decision_id: str
    run_id: str
    subject: str
    context_hash: str
    context_revision: int
    plan_id: str
    plan_hash: str
    outcome: RecallDecisionOutcome
    selected_memory_types: tuple[LongTermMemoryType, ...]
    selected_memory_refs: tuple[str, ...]
    confirmation_items: tuple[RecallConfirmationItem, ...]
    filtered_candidate_count: int
    candidate_count_stage: RecallCandidateCountStage
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    reason_codes: tuple[RecallReasonCode, ...]
    decided_at: float
    schema_version: int = RECALL_DECISION_SCHEMA_VERSION
    decision_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != RECALL_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported RecallDecision schema_version")
        for value, name in (
            (self.decision_id, "decision_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.plan_id, "plan_id"),
        ):
            _identifier(value, name)
        _digest(self.context_hash, "context_hash")
        _positive_int(self.context_revision, "context_revision")
        _digest(self.plan_hash, "plan_hash")
        object.__setattr__(self, "outcome", RecallDecisionOutcome(self.outcome))
        types = cast(
            tuple[LongTermMemoryType, ...],
            _bounded_enum_tuple(
                self.selected_memory_types,
                "selected_memory_types",
                LongTermMemoryType,
            ),
        )
        selected = _bounded_identifiers(self.selected_memory_refs, "selected_memory_refs")
        if not isinstance(self.confirmation_items, (tuple, list)) or not all(
            isinstance(item, RecallConfirmationItem) for item in self.confirmation_items
        ):
            raise TypeError("confirmation_items must contain RecallConfirmationItem values")
        confirmation = tuple(
            sorted(
                self.confirmation_items,
                key=lambda item: (
                    item.conflict_group_ref,
                    item.memory_type.value,
                    item.memory_ref,
                ),
            )
        )
        if len(confirmation) > 128:
            raise ValueError("confirmation_items exceeds the item limit")
        confirmation_refs = tuple(item.memory_ref for item in confirmation)
        if len(set(confirmation_refs)) != len(confirmation_refs):
            raise ValueError("confirmation memory_ref values must be globally unique")
        group_counts: dict[str, int] = {}
        for item in confirmation:
            group_counts[item.conflict_group_ref] = group_counts.get(item.conflict_group_ref, 0) + 1
        if any(count < 2 for count in group_counts.values()):
            raise ValueError("each confirmation conflict group requires at least two items")
        if (
            isinstance(self.filtered_candidate_count, bool)
            or not isinstance(self.filtered_candidate_count, int)
            or self.filtered_candidate_count < 0
        ):
            raise ValueError("filtered_candidate_count must be a non-negative integer")
        stage = RecallCandidateCountStage(self.candidate_count_stage)
        if stage is not RecallCandidateCountStage.AFTER_ALL_ELIGIBILITY_GATES:
            raise ValueError("candidate count must be after all eligibility gates")
        if len(selected) + len(confirmation) > self.filtered_candidate_count:
            raise ValueError("decision memories exceed post-gate candidate count")
        if self.outcome is not RecallDecisionOutcome.RECALL and (types or selected):
            raise ValueError("non-recall outcome cannot select memories")
        if self.outcome is RecallDecisionOutcome.RECALL:
            if not selected:
                raise ValueError("recall outcome requires selected memories")
            if not types:
                raise ValueError("recall outcome requires selected memory types")
            if len(types) > len(selected):
                raise ValueError("selected memory types exceed selected memory references")
            if confirmation:
                raise ValueError("recall outcome cannot require confirmation")
        elif self.outcome is RecallDecisionOutcome.NEEDS_USER_CONFIRMATION:
            if not confirmation:
                raise ValueError("needs-user-confirmation outcome requires confirmation items")
        elif confirmation:
            raise ValueError("outcome cannot include confirmation items")
        if (
            self.outcome
            in {
                RecallDecisionOutcome.NO_RECALL,
                RecallDecisionOutcome.REJECTED,
            }
            and self.filtered_candidate_count != 0
        ):
            raise ValueError("no-recall and rejected outcomes cannot disclose candidate counts")
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        if self.outcome in {
            RecallDecisionOutcome.RECALL,
            RecallDecisionOutcome.NEEDS_USER_CONFIRMATION,
        }:
            _validate_recall_disclosure(self.disclosure_context)
        evidence = _evidence_refs(self.evidence_refs)
        if not evidence:
            raise ValueError("RecallDecision requires evidence_refs")
        reasons = cast(
            tuple[RecallReasonCode, ...],
            _bounded_enum_tuple(
                self.reason_codes,
                "reason_codes",
                RecallReasonCode,
                required=True,
            ),
        )
        reason_set = set(reasons)
        if self.outcome is RecallDecisionOutcome.RECALL:
            if not reason_set <= _RECALL_DEPENDENCY_REASONS:
                raise ValueError("recall outcome requires only dependency reasons")
        elif self.outcome is RecallDecisionOutcome.NEEDS_USER_CONFIRMATION:
            allowed = _RECALL_DEPENDENCY_REASONS | {RecallReasonCode.NEEDS_USER_CONFIRMATION}
            if RecallReasonCode.NEEDS_USER_CONFIRMATION not in reason_set:
                raise ValueError("needs-user-confirmation outcome requires confirmation reason")
            if not reason_set <= allowed:
                raise ValueError("needs-user-confirmation outcome has an incompatible reason")
        elif self.outcome is RecallDecisionOutcome.NO_RECALL:
            if not reason_set <= _NO_RECALL_REASONS:
                raise ValueError("no-recall outcome has an incompatible reason")
        elif not reason_set <= _REJECTED_REASONS:
            raise ValueError("rejected outcome has an incompatible reason")
        decided_at = _optional_timestamp(self.decided_at, "decided_at")
        if decided_at is None:
            raise ValueError("decided_at is required")
        object.__setattr__(self, "selected_memory_types", types)
        object.__setattr__(self, "selected_memory_refs", selected)
        object.__setattr__(self, "confirmation_items", confirmation)
        object.__setattr__(self, "candidate_count_stage", stage)
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "decided_at", decided_at)
        object.__setattr__(
            self,
            "decision_hash",
            _domain_hash("simple-harness/recall-decision/v3", self.to_json()),
        )

    def validate_bindings(
        self,
        context: RecallContext,
        plan: RecallPlan,
        *,
        current_time: float,
    ) -> None:
        plan.validate_narrowing(context, current_time=current_time)
        if self.run_id != context.run_id or self.subject != context.subject:
            raise ValueError("RecallDecision run/subject differs")
        if self.context_hash != context.context_hash:
            raise ValueError("RecallDecision context_hash differs")
        if self.context_revision != context.context_revision:
            raise ValueError("RecallDecision context_revision differs")
        if self.plan_id != plan.plan_id or self.plan_hash != plan.plan_hash:
            raise ValueError("RecallDecision plan binding differs")
        if not set(self.selected_memory_types) <= set(plan.requested_memory_types):
            raise ValueError("RecallDecision selects an unrequested memory type")
        if not {item.memory_type for item in self.confirmation_items} <= set(
            plan.requested_memory_types
        ):
            raise ValueError("RecallDecision confirms an unrequested memory type")
        if set(self.selected_memory_refs) & {item.memory_ref for item in self.confirmation_items}:
            raise ValueError("RecallDecision selected and confirmation refs overlap")
        if self.disclosure_context.to_json() != plan.disclosure_context.to_json():
            raise ValueError("RecallDecision disclosure differs from RecallPlan")
        plan_evidence = {fingerprint_json(item.to_json()) for item in plan.evidence_refs}
        context_evidence = {fingerprint_json(item.to_json()) for item in context.evidence_refs}
        decision_evidence = {fingerprint_json(item.to_json()) for item in self.evidence_refs}
        if decision_evidence != plan_evidence or not decision_evidence <= context_evidence:
            raise ValueError("RecallDecision evidence_refs do not bind Plan/Context")
        if self.outcome in {
            RecallDecisionOutcome.RECALL,
            RecallDecisionOutcome.NEEDS_USER_CONFIRMATION,
        }:
            _validate_recall_disclosure(self.disclosure_context)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "context_hash": self.context_hash,
            "context_revision": self.context_revision,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "outcome": self.outcome.value,
            "selected_memory_types": [item.value for item in self.selected_memory_types],
            "selected_memory_refs": list(self.selected_memory_refs),
            "confirmation_items": [item.to_json() for item in self.confirmation_items],
            "filtered_candidate_count": self.filtered_candidate_count,
            "candidate_count_stage": self.candidate_count_stage.value,
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [item.to_json() for item in self.evidence_refs],
            "reason_codes": [item.value for item in self.reason_codes],
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> LegacyRecallDecisionV3:
        schema_version: int | None = None
        if "schema_version" in value:
            schema_version = _recall_decision_schema_version(
                value["schema_version"], "RecallDecision"
            )
        expected = {
            "schema_version",
            "decision_id",
            "run_id",
            "subject",
            "context_hash",
            "context_revision",
            "plan_id",
            "plan_hash",
            "outcome",
            "selected_memory_types",
            "selected_memory_refs",
            "confirmation_items",
            "filtered_candidate_count",
            "candidate_count_stage",
            "disclosure_context",
            "evidence_refs",
            "reason_codes",
            "decided_at",
        }
        _exact_keys(value, expected, "RecallDecision")
        count = value["filtered_candidate_count"]
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("filtered_candidate_count must be an integer")
        return cls(
            decision_id=_identifier(value["decision_id"], "decision_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            context_hash=_digest(value["context_hash"], "context_hash"),
            context_revision=_positive_int(value["context_revision"], "context_revision"),
            plan_id=_identifier(value["plan_id"], "plan_id"),
            plan_hash=_digest(value["plan_hash"], "plan_hash"),
            outcome=RecallDecisionOutcome(value["outcome"]),  # type: ignore[arg-type]
            selected_memory_types=tuple(
                LongTermMemoryType(item)
                for item in _strings(value["selected_memory_types"], "selected_memory_types")
            ),
            selected_memory_refs=_strings(value["selected_memory_refs"], "selected_memory_refs"),
            confirmation_items=tuple(
                RecallConfirmationItem.from_json(item)
                for item in _objects(value["confirmation_items"], "confirmation_items")
            ),
            filtered_candidate_count=count,
            candidate_count_stage=RecallCandidateCountStage(value["candidate_count_stage"]),  # type: ignore[arg-type]
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            reason_codes=tuple(
                RecallReasonCode(item) for item in _strings(value["reason_codes"], "reason_codes")
            ),
            decided_at=cast(float, _optional_timestamp(value["decided_at"], "decided_at")),
            schema_version=cast(int, schema_version),
        )
