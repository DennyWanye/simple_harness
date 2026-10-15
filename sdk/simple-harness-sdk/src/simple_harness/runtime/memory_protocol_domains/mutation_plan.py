# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Memory mutation operations and plans, their validation diagnostics and parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from simple_harness.contracts import JsonValue

from ..disclosure_protocol import (
    DisclosureContext,
    _digest,
    _domain_hash,
    _exact_keys,
    _identifier,
    _object,
    _objects,
    _positive_int,
    _strings,
)
from ..evidence_protocol import (
    EvidenceActorRole,
    EvidenceProvenance,
    EvidenceRef,
    EvidenceSpanRef,
    EvidenceSupportKind,
    _evidence_refs,
    _refs_from_json,
)
from ..information_classification_protocol import InformationAttribute, PrivacyClass
from ..memory_action_protocol import MemoryActionAuthorityRef, MemoryActionIntent, MemoryActionKind
from .common import _bounded_enum_tuple, _bounded_identifiers
from .mutation_payloads import (
    CognitiveLifecycleState,
    ConflictStatus,
    CreatedByOperationTarget,
    EpisodeLifecycleState,
    EpisodeMemoryPayload,
    EpistemicStatus,
    ExistingMemoryTarget,
    MemoryMutationApplyMode,
    MemoryMutationKind,
    MemoryMutationPayload,
    MemoryMutationPlanOutcome,
    MemoryMutationTarget,
    ProcedureLifecycleState,
    ProcedureMemoryPayload,
    ProspectiveLifecycleState,
    ProspectiveMemoryPayload,
    SemanticLifecycleState,
    SemanticMemoryPayload,
    SemanticRelationMemoryPayload,
    ValidTimeInterval,
    VerificationState,
    _mutation_target_from_json,
)
from .types import (
    MEMORY_MUTATION_SCHEMA_VERSION,
    MEMORY_MUTATION_VALIDATION_DIAGNOSTIC_SCHEMA_VERSION,
    LongTermMemoryType,
    MemoryMutationValidationReasonCode,
    SemanticMemoryKind,
    _memory_mutation_schema_version,
    _MemoryMutationAdmissionError,
)

_PRIVACY_ORDER = {
    PrivacyClass.PUBLIC: 0,
    PrivacyClass.PERSONAL: 1,
    PrivacyClass.SENSITIVE: 2,
    PrivacyClass.RESTRICTED: 3,
}


_PAYLOAD_TYPES: dict[LongTermMemoryType, type[MemoryMutationPayload]] = {
    LongTermMemoryType.EPISODE: EpisodeMemoryPayload,
    LongTermMemoryType.PROCEDURE: ProcedureMemoryPayload,
    LongTermMemoryType.PROSPECTIVE: ProspectiveMemoryPayload,
}


_LIFECYCLE_TYPES: dict[LongTermMemoryType, type[CognitiveLifecycleState]] = {
    LongTermMemoryType.EPISODE: EpisodeLifecycleState,
    LongTermMemoryType.SEMANTIC: SemanticLifecycleState,
    LongTermMemoryType.PROCEDURE: ProcedureLifecycleState,
    LongTermMemoryType.PROSPECTIVE: ProspectiveLifecycleState,
}


_CONTEST_DESTRUCTIVE_LIFECYCLES: frozenset[CognitiveLifecycleState] = frozenset(
    {
        EpisodeLifecycleState.SUPERSEDED,
        EpisodeLifecycleState.REJECTED,
        EpisodeLifecycleState.FORGOTTEN,
        SemanticLifecycleState.SUPERSEDED,
        SemanticLifecycleState.REJECTED,
        SemanticLifecycleState.FORGOTTEN,
        ProcedureLifecycleState.INAPPLICABLE,
        ProcedureLifecycleState.SUPERSEDED,
        ProcedureLifecycleState.FORGOTTEN,
        ProspectiveLifecycleState.COMPLETED,
        ProspectiveLifecycleState.CANCELLED,
        ProspectiveLifecycleState.EXPIRED,
        ProspectiveLifecycleState.SUPERSEDED,
        ProspectiveLifecycleState.FORGOTTEN,
    }
)


def _payload_memory_type(payload: MemoryMutationPayload) -> LongTermMemoryType:
    if isinstance(payload, EpisodeMemoryPayload):
        return LongTermMemoryType.EPISODE
    if isinstance(payload, (SemanticMemoryPayload, SemanticRelationMemoryPayload)):
        return LongTermMemoryType.SEMANTIC
    if isinstance(payload, ProcedureMemoryPayload):
        return LongTermMemoryType.PROCEDURE
    if isinstance(payload, ProspectiveMemoryPayload):
        return LongTermMemoryType.PROSPECTIVE
    raise TypeError("payload must use a registered cognitive payload")


def _payload_from_json(value: object) -> MemoryMutationPayload:
    payload = _object(value, "payload")
    raw_type = payload.get("memory_type")
    try:
        memory_type = LongTermMemoryType(raw_type)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("payload has an unknown memory_type discriminator") from exc
    if memory_type is LongTermMemoryType.SEMANTIC:
        semantic_kind = payload.get("semantic_kind")
        if semantic_kind == SemanticMemoryKind.CLAIM.value:
            return SemanticMemoryPayload.from_json(payload)
        if semantic_kind == SemanticMemoryKind.RELATION.value:
            return SemanticRelationMemoryPayload.from_json(payload)
        raise ValueError("semantic payload has an unknown semantic_kind discriminator")
    return _PAYLOAD_TYPES[memory_type].from_json(payload)  # type: ignore[attr-defined,return-value]


def _relation_created_endpoint_ids(
    operation: MemoryMutationOperation,
) -> tuple[str, ...]:
    payload = operation.payload
    if not isinstance(payload, SemanticRelationMemoryPayload):
        return ()
    return tuple(
        endpoint.operation_id
        for endpoint in (payload.source_endpoint, payload.target_endpoint)
        if isinstance(endpoint, CreatedByOperationTarget)
    )


def _validate_epistemic_evidence_matrix(
    epistemic: EpistemicStatus,
    verification: VerificationState,
    spans: tuple[EvidenceSpanRef, ...],
) -> None:
    authenticated_user = any(
        span.support_kind
        in {
            EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
            EvidenceSupportKind.EXPLICIT_USER_CORRECTION,
        }
        and span.actor_role is EvidenceActorRole.USER
        and span.provenance is EvidenceProvenance.AUTHENTICATED_USER
        for span in spans
    )
    external_typed = any(
        span.support_kind is EvidenceSupportKind.TYPED_OBSERVATION
        and span.typed_observation is not None
        and span.actor_role is EvidenceActorRole.EXTERNAL
        and span.provenance is EvidenceProvenance.EXTERNAL_SOURCE
        for span in spans
    )
    observed_behavior = any(
        (
            span.support_kind is EvidenceSupportKind.TYPED_OBSERVATION
            and span.typed_observation is not None
            and span.actor_role is EvidenceActorRole.TOOL
            and span.provenance is EvidenceProvenance.TRUSTED_TOOL
        )
        or (
            span.support_kind is EvidenceSupportKind.RUNTIME_EVENT
            and span.actor_role is EvidenceActorRole.RUNTIME
            and span.provenance is EvidenceProvenance.HOST_RUNTIME
        )
        for span in spans
    )
    model_inference = any(
        span.support_kind is EvidenceSupportKind.MODEL_INFERENCE
        and span.actor_role is EvidenceActorRole.ASSISTANT
        and span.provenance is EvidenceProvenance.MODEL_OUTPUT
        for span in spans
    )
    trusted_typed = any(
        span.support_kind is EvidenceSupportKind.TYPED_OBSERVATION
        and span.typed_observation is not None
        and (span.actor_role, span.provenance)
        in {
            (EvidenceActorRole.TOOL, EvidenceProvenance.TRUSTED_TOOL),
            (EvidenceActorRole.EXTERNAL, EvidenceProvenance.EXTERNAL_SOURCE),
        }
        for span in spans
    )

    if epistemic is EpistemicStatus.EXPLICIT_USER and not authenticated_user:
        raise ValueError("explicit_user requires authenticated user evidence")
    if epistemic is EpistemicStatus.VERIFIED_EXTERNAL:
        if not external_typed:
            raise ValueError("verified_external requires external typed authority")
        if verification is not VerificationState.SOURCE_VERIFIED:
            raise ValueError("verified_external requires source_verified state")
    if epistemic is EpistemicStatus.OBSERVED_BEHAVIOR and not observed_behavior:
        raise ValueError("observed_behavior requires trusted Tool or Runtime evidence")
    if epistemic is EpistemicStatus.LLM_INFERENCE and not model_inference:
        raise ValueError("llm_inference requires model inference evidence")
    if (
        verification
        in {
            VerificationState.SOURCE_VERIFIED,
            VerificationState.REPEATED_OBSERVATION,
        }
        and not trusted_typed
    ):
        raise ValueError("verified states require trusted typed observation evidence")


@dataclass(frozen=True, slots=True)
class MemoryMutationOperation:
    operation_id: str
    kind: MemoryMutationKind
    memory_type: LongTermMemoryType
    payload: MemoryMutationPayload | None
    target: MemoryMutationTarget | None
    depends_on_operation_ids: tuple[str, ...]
    lifecycle_state: CognitiveLifecycleState
    epistemic_status: EpistemicStatus
    conflict_status: ConflictStatus
    verification_state: VerificationState
    valid_time_interval: ValidTimeInterval
    proposed_privacy_class: PrivacyClass
    proposed_information_attributes: tuple[InformationAttribute, ...]
    evidence_spans: tuple[EvidenceSpanRef, ...]
    reason_code: str
    action_authority_ref: MemoryActionAuthorityRef | None = None
    operation_intent_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _identifier(self.operation_id, "operation_id")
        kind = MemoryMutationKind(self.kind)
        if kind is MemoryMutationKind.NO_MUTATION:
            raise ValueError("no_mutation is a plan outcome, not an operation")
        memory_type = LongTermMemoryType(self.memory_type)
        if self.payload is not None and _payload_memory_type(self.payload) is not memory_type:
            raise ValueError("payload discriminator differs from memory_type")
        if kind is MemoryMutationKind.CREATE:
            if self.target is not None or self.payload is None:
                raise ValueError("create requires payload and a null target")
        else:
            if not isinstance(self.target, (ExistingMemoryTarget, CreatedByOperationTarget)):
                raise ValueError("existing-memory mutation requires a strict target")
            if kind is not MemoryMutationKind.SUPPRESS and self.payload is None:
                raise ValueError("non-suppress mutation requires a typed payload")
        dependencies = _bounded_identifiers(
            self.depends_on_operation_ids, "depends_on_operation_ids"
        )
        dependencies = tuple(sorted(dependencies))
        if self.operation_id in dependencies:
            raise ValueError("operation cannot depend on self")
        if isinstance(self.target, CreatedByOperationTarget):
            if self.target.operation_id == self.operation_id:
                raise ValueError("operation cannot target itself")
            if self.target.operation_id not in dependencies:
                raise ValueError("created_by_operation target must be an explicit dependency")
        lifecycle_type = _LIFECYCLE_TYPES[memory_type]
        if not isinstance(self.lifecycle_state, lifecycle_type):
            raise ValueError(f"lifecycle_state is invalid for {memory_type.value} memory")
        lifecycle = self.lifecycle_state
        epistemic = EpistemicStatus(self.epistemic_status)
        conflict = ConflictStatus(self.conflict_status)
        verification = VerificationState(self.verification_state)
        if kind is MemoryMutationKind.CONTEST:
            if conflict is not ConflictStatus.CONTESTED:
                raise ValueError("contest requires contested conflict_status")
            if lifecycle in _CONTEST_DESTRUCTIVE_LIFECYCLES:
                raise ValueError("contest cannot propose a destructive terminal lifecycle")
        if not isinstance(self.valid_time_interval, ValidTimeInterval):
            raise TypeError("valid_time_interval must use ValidTimeInterval")
        privacy = PrivacyClass(self.proposed_privacy_class)
        attributes = cast(
            tuple[InformationAttribute, ...],
            _bounded_enum_tuple(
                self.proposed_information_attributes,
                "proposed_information_attributes",
                InformationAttribute,
                max_items=32,
            ),
        )
        spans = tuple(self.evidence_spans)
        if (
            not spans
            or len(spans) > 64
            or not all(isinstance(item, EvidenceSpanRef) for item in spans)
            or len({item.span_id for item in spans}) != len(spans)
        ):
            raise _MemoryMutationAdmissionError(
                MemoryMutationValidationReasonCode.EVIDENCE_REF_REQUIRED,
                "evidence_spans must contain unique bounded EvidenceSpanRef values",
            )
        _validate_epistemic_evidence_matrix(epistemic, verification, spans)
        _identifier(self.reason_code, "reason_code")
        protected_action = kind in {
            MemoryMutationKind.REVISE,
            MemoryMutationKind.SUPERSEDE,
            MemoryMutationKind.SUPPRESS,
        }
        if protected_action and not isinstance(self.target, ExistingMemoryTarget):
            raise ValueError(
                "existing-memory action authority requires an exact ExistingMemoryTarget"
            )
        if self.action_authority_ref is not None:
            if type(self.action_authority_ref) is not MemoryActionAuthorityRef:
                raise TypeError("action_authority_ref must use MemoryActionAuthorityRef")
            if not protected_action:
                raise ValueError("action_authority_ref is only valid for revise/supersede/suppress")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "memory_type", memory_type)
        object.__setattr__(self, "depends_on_operation_ids", dependencies)
        object.__setattr__(self, "lifecycle_state", lifecycle)
        object.__setattr__(self, "epistemic_status", epistemic)
        object.__setattr__(self, "conflict_status", conflict)
        object.__setattr__(self, "verification_state", verification)
        object.__setattr__(self, "proposed_privacy_class", privacy)
        object.__setattr__(self, "proposed_information_attributes", attributes)
        object.__setattr__(self, "evidence_spans", spans)
        object.__setattr__(
            self,
            "operation_intent_hash",
            _domain_hash(
                "simple-harness/memory-mutation-operation-intent/v4",
                self._intent_json(),
            ),
        )

    def _intent_json(self) -> dict[str, JsonValue]:
        """Canonical mutation intent, deliberately excluding Host authority ref."""

        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "memory_type": self.memory_type.value,
            "payload": None if self.payload is None else self.payload.to_json(),
            "target": None if self.target is None else self.target.to_json(),
            "depends_on_operation_ids": list(self.depends_on_operation_ids),
            "lifecycle_state": self.lifecycle_state.value,
            "epistemic_status": self.epistemic_status.value,
            "conflict_status": self.conflict_status.value,
            "verification_state": self.verification_state.value,
            "valid_time_interval": self.valid_time_interval.to_json(),
            "proposed_privacy_class": self.proposed_privacy_class.value,
            "proposed_information_attributes": [
                item.value for item in self.proposed_information_attributes
            ],
            "evidence_spans": [item.to_json() for item in self.evidence_spans],
            "reason_code": self.reason_code,
        }

    def effective_privacy_class(self, *trusted_floors: PrivacyClass) -> PrivacyClass:
        """Join the model proposal with every trusted policy/evidence/record floor."""

        floors = tuple(PrivacyClass(item) for item in trusted_floors)
        if not floors:
            raise ValueError("effective privacy requires at least one trusted floor")
        return max(
            (self.proposed_privacy_class, *floors),
            key=lambda item: _PRIVACY_ORDER[item],
        )

    def effective_information_attributes(
        self, *trusted_attribute_sets: tuple[InformationAttribute, ...]
    ) -> tuple[InformationAttribute, ...]:
        """Union proposal attributes with all trusted policy/evidence/record labels."""

        trusted = {
            InformationAttribute(item) for values in trusted_attribute_sets for item in values
        }
        return tuple(
            sorted(
                set(self.proposed_information_attributes) | trusted,
                key=lambda item: item.value,
            )
        )

    def to_json(self) -> dict[str, JsonValue]:
        value = self._intent_json()
        value["operation_intent_hash"] = self.operation_intent_hash
        value["action_authority_ref"] = (
            None if self.action_authority_ref is None else self.action_authority_ref.to_json()
        )
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationOperation:
        expected = {
            "operation_id",
            "kind",
            "memory_type",
            "payload",
            "target",
            "depends_on_operation_ids",
            "lifecycle_state",
            "epistemic_status",
            "conflict_status",
            "verification_state",
            "valid_time_interval",
            "proposed_privacy_class",
            "proposed_information_attributes",
            "evidence_spans",
            "reason_code",
            "operation_intent_hash",
            "action_authority_ref",
        }
        _exact_keys(value, expected, "MemoryMutationOperation")
        memory_type = LongTermMemoryType(value["memory_type"])  # type: ignore[arg-type]
        lifecycle_type = _LIFECYCLE_TYPES[memory_type]
        payload_value = value["payload"]
        action_ref_value = value["action_authority_ref"]
        operation = cls(
            operation_id=_identifier(value["operation_id"], "operation_id"),
            kind=MemoryMutationKind(value["kind"]),  # type: ignore[arg-type]
            memory_type=memory_type,
            payload=None if payload_value is None else _payload_from_json(payload_value),
            target=_mutation_target_from_json(value["target"]),
            depends_on_operation_ids=_strings(
                value["depends_on_operation_ids"], "depends_on_operation_ids"
            ),
            lifecycle_state=lifecycle_type(value["lifecycle_state"]),  # type: ignore[arg-type]
            epistemic_status=EpistemicStatus(value["epistemic_status"]),  # type: ignore[arg-type]
            conflict_status=ConflictStatus(value["conflict_status"]),  # type: ignore[arg-type]
            verification_state=VerificationState(value["verification_state"]),  # type: ignore[arg-type]
            valid_time_interval=ValidTimeInterval.from_json(
                _object(value["valid_time_interval"], "valid_time_interval")
            ),
            proposed_privacy_class=PrivacyClass(value["proposed_privacy_class"]),  # type: ignore[arg-type]
            proposed_information_attributes=tuple(
                InformationAttribute(item)
                for item in _strings(
                    value["proposed_information_attributes"],
                    "proposed_information_attributes",
                )
            ),
            evidence_spans=tuple(
                EvidenceSpanRef.from_json(item)
                for item in _objects(value["evidence_spans"], "evidence_spans")
            ),
            reason_code=_identifier(value["reason_code"], "reason_code"),
            action_authority_ref=(
                None
                if action_ref_value is None
                else MemoryActionAuthorityRef.from_json(
                    _object(action_ref_value, "action_authority_ref")
                )
            ),
        )
        if operation.operation_intent_hash != _digest(
            value["operation_intent_hash"], "operation_intent_hash"
        ):
            raise ValueError("operation_intent_hash does not bind operation intent")
        return operation


@dataclass(frozen=True, slots=True)
class MemoryMutationPlan:
    plan_id: str
    run_id: str
    turn_id: str
    subject: str
    base_revision: int
    outcome: MemoryMutationPlanOutcome
    operations: tuple[MemoryMutationOperation, ...]
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    idempotency_key: str
    apply_mode: MemoryMutationApplyMode = MemoryMutationApplyMode.STRICT_ATOMIC
    schema_version: int = MEMORY_MUTATION_SCHEMA_VERSION
    plan_intent_hash: str = field(init=False)
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MUTATION_SCHEMA_VERSION:
            raise ValueError("unsupported MemoryMutationPlan schema_version")
        for value, name in (
            (self.plan_id, "plan_id"),
            (self.run_id, "run_id"),
            (self.turn_id, "turn_id"),
            (self.subject, "subject"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _identifier(value, name)
        apply_mode = MemoryMutationApplyMode(self.apply_mode)
        if apply_mode is not MemoryMutationApplyMode.STRICT_ATOMIC:
            raise ValueError("MemoryMutationPlan requires strict_atomic apply_mode")
        _positive_int(self.base_revision, "base_revision")
        outcome = MemoryMutationPlanOutcome(self.outcome)
        operations = tuple(self.operations)
        if not all(isinstance(item, MemoryMutationOperation) for item in operations):
            raise ValueError("operations must contain MemoryMutationOperation values")
        if len(operations) > 128:
            raise ValueError("operations exceeds the item limit")
        if len({item.operation_id for item in operations}) != len(operations):
            raise _MemoryMutationAdmissionError(
                MemoryMutationValidationReasonCode.DUPLICATE_OPERATION,
                "operation_id values must be unique",
            )
        if outcome is MemoryMutationPlanOutcome.NO_MUTATION and operations:
            raise ValueError("no_mutation outcome requires empty operations")
        if outcome is MemoryMutationPlanOutcome.MUTATE and not operations:
            raise ValueError("mutate outcome requires operations")
        operation_ids = {item.operation_id for item in operations}
        operation_positions = {
            operation.operation_id: index for index, operation in enumerate(operations)
        }
        for operation_index, operation in enumerate(operations):
            dependencies = set(operation.depends_on_operation_ids)
            if isinstance(operation.target, CreatedByOperationTarget):
                dependencies.add(operation.target.operation_id)
            relation_endpoint_ids = set(_relation_created_endpoint_ids(operation))
            if not relation_endpoint_ids.issubset(dependencies):
                raise _MemoryMutationAdmissionError(
                    MemoryMutationValidationReasonCode.RELATION_ENDPOINT_DEPENDENCY_REQUIRED,
                    "created semantic relation endpoints require explicit dependencies",
                )
            dependencies.update(relation_endpoint_ids)
            unknown = dependencies - operation_ids
            if unknown:
                raise _MemoryMutationAdmissionError(
                    MemoryMutationValidationReasonCode.INVALID_DEPENDENCY_ORDER,
                    "operation has unknown dependencies",
                )
            if any(
                operation_positions[endpoint_id] >= operation_index
                for endpoint_id in relation_endpoint_ids
            ):
                raise _MemoryMutationAdmissionError(
                    MemoryMutationValidationReasonCode.INVALID_DEPENDENCY_ORDER,
                    "created semantic relation endpoint must precede the relation",
                )
        by_id = {item.operation_id: item for item in operations}
        for operation in operations:
            if isinstance(operation.target, CreatedByOperationTarget):
                producer = by_id[operation.target.operation_id]
                if producer.kind is not MemoryMutationKind.CREATE:
                    raise ValueError("created_by_operation producer must be a create operation")
                if producer.memory_type is not operation.memory_type:
                    raise ValueError("created_by_operation producer must have the same memory_type")
            if isinstance(operation.payload, SemanticRelationMemoryPayload):
                relation = operation.payload
                for endpoint, role in (
                    (relation.source_endpoint, "source"),
                    (relation.target_endpoint, "target"),
                ):
                    if not isinstance(endpoint, CreatedByOperationTarget):
                        continue
                    producer = by_id[endpoint.operation_id]
                    if producer.kind is not MemoryMutationKind.CREATE:
                        raise _MemoryMutationAdmissionError(
                            MemoryMutationValidationReasonCode.RELATION_ENDPOINT_CREATE_REQUIRED,
                            "semantic relation endpoint producer must be a create operation",
                        )
                    if role == "source":
                        if isinstance(producer.payload, SemanticRelationMemoryPayload):
                            raise _MemoryMutationAdmissionError(
                                MemoryMutationValidationReasonCode.RELATION_ENDPOINT_MUST_BE_CLAIM,
                                "semantic relation cannot be a relation endpoint",
                            )
                        if not isinstance(producer.payload, SemanticMemoryPayload):
                            raise _MemoryMutationAdmissionError(
                                MemoryMutationValidationReasonCode.INVALID_RELATION_SOURCE_TYPE,
                                "applies_to source must be a Semantic claim",
                            )
                    else:
                        if isinstance(producer.payload, SemanticRelationMemoryPayload):
                            raise _MemoryMutationAdmissionError(
                                MemoryMutationValidationReasonCode.INVALID_RELATION_TARGET_TYPE,
                                "applies_to target must be Procedure or Prospective",
                            )
                        if producer.memory_type not in {
                            LongTermMemoryType.PROCEDURE,
                            LongTermMemoryType.PROSPECTIVE,
                        }:
                            raise _MemoryMutationAdmissionError(
                                MemoryMutationValidationReasonCode.INVALID_RELATION_TARGET_TYPE,
                                "applies_to target must be Procedure or Prospective",
                            )
        operations = self._stable_topological_operations(operations)
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        evidence_refs = _evidence_refs(self.evidence_refs)
        if not evidence_refs:
            raise _MemoryMutationAdmissionError(
                MemoryMutationValidationReasonCode.EVIDENCE_REF_REQUIRED,
                "MemoryMutationPlan requires evidence_refs",
            )
        evidence_coverage = {(item.evidence_id, item.content_hash) for item in evidence_refs}
        for operation in operations:
            for span in operation.evidence_spans:
                if (span.evidence_id, span.envelope_hash) not in evidence_coverage:
                    raise _MemoryMutationAdmissionError(
                        MemoryMutationValidationReasonCode.EVIDENCE_REF_REQUIRED,
                        "MemoryMutationPlan evidence_refs do not cover every evidence span",
                    )
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "apply_mode", apply_mode)
        object.__setattr__(
            self,
            "plan_intent_hash",
            _domain_hash(
                "simple-harness/memory-mutation-plan-intent/v5",
                self._intent_json(),
            ),
        )
        object.__setattr__(
            self,
            "plan_hash",
            _domain_hash("simple-harness/memory-mutation-plan/v5", self.to_json()),
        )

    def action_intent(self, operation_id: str) -> MemoryActionIntent:
        """Build the exact non-circular Host authorization commitment."""

        _identifier(operation_id, "operation_id")
        indexed_operation = next(
            (
                (index, item)
                for index, item in enumerate(self.operations, start=1)
                if item.operation_id == operation_id
            ),
            None,
        )
        if indexed_operation is None:
            raise KeyError("memory_action_operation_not_found")
        canonical_operation_index, operation = indexed_operation
        if operation.kind not in {
            MemoryMutationKind.REVISE,
            MemoryMutationKind.SUPERSEDE,
            MemoryMutationKind.SUPPRESS,
        }:
            raise ValueError("operation does not require MemoryActionAuthority")
        if not isinstance(operation.target, ExistingMemoryTarget):
            raise ValueError("memory action requires an exact ExistingMemoryTarget")
        return MemoryActionIntent(
            subject=self.subject,
            action=MemoryActionKind(operation.kind.value),
            target_memory_id=operation.target.memory_id,
            target_revision=operation.target.revision,
            evidence_refs=self.evidence_refs,
            evidence_span_hashes=tuple(span.span_hash for span in operation.evidence_spans),
            run_id=self.run_id,
            turn_id=self.turn_id,
            plan_id=self.plan_id,
            plan_intent_hash=self.plan_intent_hash,
            operation_id=operation.operation_id,
            canonical_operation_index=canonical_operation_index,
            operation_intent_hash=operation.operation_intent_hash,
        )

    @staticmethod
    def _stable_topological_operations(
        operations: tuple[MemoryMutationOperation, ...],
    ) -> tuple[MemoryMutationOperation, ...]:
        by_id = {item.operation_id: item for item in operations}
        remaining = {}
        for item in operations:
            dependencies = set(item.depends_on_operation_ids)
            if isinstance(item.target, CreatedByOperationTarget):
                dependencies.add(item.target.operation_id)
            dependencies.update(_relation_created_endpoint_ids(item))
            remaining[item.operation_id] = dependencies
        ordered: list[MemoryMutationOperation] = []
        while remaining:
            ready = sorted(operation_id for operation_id, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError("operation dependency graph contains a cycle")
            for operation_id in ready:
                ordered.append(by_id[operation_id])
                remaining.pop(operation_id)
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(ordered)

    def topological_operations(self) -> tuple[MemoryMutationOperation, ...]:
        """Stable apply order; wire array order has no authority."""

        return self._stable_topological_operations(self.operations)

    def _intent_json(self) -> dict[str, JsonValue]:
        """Canonical whole-plan commitment excluding every action authority ref."""

        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "subject": self.subject,
            "base_revision": self.base_revision,
            "outcome": self.outcome.value,
            "operations": [
                {
                    **operation._intent_json(),
                    "operation_intent_hash": operation.operation_intent_hash,
                }
                for operation in self.operations
            ],
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "idempotency_key": self.idempotency_key,
            "apply_mode": self.apply_mode.value,
        }

    def to_json(self) -> dict[str, JsonValue]:
        value = self._intent_json()
        value["operations"] = [operation.to_json() for operation in self.operations]
        value["plan_intent_hash"] = self.plan_intent_hash
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationPlan:
        _exact_keys(
            value,
            {
                "schema_version",
                "plan_id",
                "run_id",
                "turn_id",
                "subject",
                "base_revision",
                "outcome",
                "operations",
                "disclosure_context",
                "evidence_refs",
                "idempotency_key",
                "apply_mode",
                "plan_intent_hash",
            },
            "MemoryMutationPlan",
        )
        plan = cls(
            plan_id=_identifier(value["plan_id"], "plan_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            turn_id=_identifier(value["turn_id"], "turn_id"),
            subject=_identifier(value["subject"], "subject"),
            base_revision=_positive_int(value["base_revision"], "base_revision"),
            outcome=MemoryMutationPlanOutcome(value["outcome"]),  # type: ignore[arg-type]
            operations=tuple(
                MemoryMutationOperation.from_json(item)
                for item in _objects(value["operations"], "operations")
            ),
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            idempotency_key=_identifier(value["idempotency_key"], "idempotency_key"),
            apply_mode=MemoryMutationApplyMode(value["apply_mode"]),  # type: ignore[arg-type]
            schema_version=_memory_mutation_schema_version(
                value["schema_version"], "MemoryMutationPlan"
            ),
        )
        if plan.plan_intent_hash != _digest(value["plan_intent_hash"], "plan_intent_hash"):
            raise ValueError("plan_intent_hash does not bind mutation plan intent")
        return plan


_MEMORY_MUTATION_VALIDATION_MESSAGES = {
    MemoryMutationValidationReasonCode.INVALID_WIRE: ("Memory mutation plan wire is invalid."),
    MemoryMutationValidationReasonCode.UNSUPPORTED_SCHEMA_VERSION: (
        "Memory mutation plan schema version is unsupported."
    ),
    MemoryMutationValidationReasonCode.DUPLICATE_OPERATION: (
        "Memory mutation plan contains a duplicate operation."
    ),
    MemoryMutationValidationReasonCode.RELATION_ENDPOINT_DEPENDENCY_REQUIRED: (
        "Semantic relation endpoint dependencies are required."
    ),
    MemoryMutationValidationReasonCode.INVALID_DEPENDENCY_ORDER: (
        "Semantic relation dependency order is invalid."
    ),
    MemoryMutationValidationReasonCode.RELATION_ENDPOINT_CREATE_REQUIRED: (
        "Semantic relation endpoint producer is not a create operation."
    ),
    MemoryMutationValidationReasonCode.INVALID_RELATION_KIND: (
        "Semantic relation kind is unsupported."
    ),
    MemoryMutationValidationReasonCode.INVALID_RELATION_SOURCE_TYPE: (
        "Semantic relation source type is invalid."
    ),
    MemoryMutationValidationReasonCode.INVALID_RELATION_TARGET_TYPE: (
        "Semantic relation target type is invalid."
    ),
    MemoryMutationValidationReasonCode.RELATION_SELF_LOOP: (
        "Semantic relation cannot reference itself."
    ),
    MemoryMutationValidationReasonCode.RELATION_ENDPOINT_MUST_BE_CLAIM: (
        "Semantic relation source endpoint must be a claim."
    ),
    MemoryMutationValidationReasonCode.EVIDENCE_REF_REQUIRED: (
        "Memory mutation evidence is missing or incomplete."
    ),
}


@dataclass(frozen=True, slots=True)
class MemoryMutationValidationDiagnostic:
    reason_code: MemoryMutationValidationReasonCode
    schema_version: int = MEMORY_MUTATION_VALIDATION_DIAGNOSTIC_SCHEMA_VERSION
    message: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MUTATION_VALIDATION_DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported MemoryMutationValidationDiagnostic schema_version")
        reason_code = MemoryMutationValidationReasonCode(self.reason_code)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(
            self,
            "message",
            _MEMORY_MUTATION_VALIDATION_MESSAGES[reason_code],
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "reason_code": self.reason_code.value,
            "message": self.message,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationValidationDiagnostic:
        _exact_keys(
            value,
            {"schema_version", "reason_code", "message"},
            "MemoryMutationValidationDiagnostic",
        )
        schema_version = value["schema_version"]
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise TypeError("MemoryMutationValidationDiagnostic schema_version must be an integer")
        diagnostic = cls(
            reason_code=MemoryMutationValidationReasonCode(value["reason_code"]),  # type: ignore[arg-type]
            schema_version=schema_version,
        )
        if value["message"] != diagnostic.message:
            raise ValueError("MemoryMutationValidationDiagnostic message differs from reason_code")
        return diagnostic


def parse_memory_mutation_plan(
    value: object,
) -> MemoryMutationPlan | MemoryMutationValidationDiagnostic:
    """Parse untrusted model wire without reflecting its contents in diagnostics."""

    if not isinstance(value, Mapping):
        return MemoryMutationValidationDiagnostic(
            MemoryMutationValidationReasonCode.INVALID_WIRE,
        )
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        return MemoryMutationValidationDiagnostic(
            MemoryMutationValidationReasonCode.INVALID_WIRE,
        )
    if schema_version != MEMORY_MUTATION_SCHEMA_VERSION:
        return MemoryMutationValidationDiagnostic(
            MemoryMutationValidationReasonCode.UNSUPPORTED_SCHEMA_VERSION,
        )
    try:
        return MemoryMutationPlan.from_json(value)
    except _MemoryMutationAdmissionError as exc:
        return MemoryMutationValidationDiagnostic(exc.reason_code)
    except (KeyError, TypeError, ValueError):
        return MemoryMutationValidationDiagnostic(
            MemoryMutationValidationReasonCode.INVALID_WIRE,
        )
