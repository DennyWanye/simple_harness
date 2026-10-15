# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Context fragments and context assembly decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from simple_harness.contracts import JsonValue

from ..disclosure_protocol import (
    HUMAN_MEMORY_SCHEMA_VERSION,
    DisclosureContext,
    _canonical_hash,
    _digest,
    _exact_keys,
    _identifier,
    _object,
    _positive_int,
    _schema_version,
    _strings,
)
from ..evidence_protocol import EvidenceRef, _evidence_refs, _refs_from_json
from .types import ContextFragmentType


@dataclass(frozen=True, slots=True)
class LegacyContextFragmentV1:
    fragment_id: str
    run_id: str
    subject: str
    fragment_type: ContextFragmentType
    source_ref: str
    source_revision: int
    content_hash: str
    token_estimate: int
    byte_estimate: int
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    schema_version: int = HUMAN_MEMORY_SCHEMA_VERSION
    fragment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != HUMAN_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported ContextFragment schema_version")
        for value, name in (
            (self.fragment_id, "fragment_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
        ):
            _identifier(value, name)
        object.__setattr__(self, "fragment_type", ContextFragmentType(self.fragment_type))
        _identifier(self.source_ref, "source_ref", max_length=1024)
        _positive_int(self.source_revision, "source_revision")
        _digest(self.content_hash, "content_hash")
        for estimate_value, estimate_name in (
            (self.token_estimate, "token_estimate"),
            (self.byte_estimate, "byte_estimate"),
        ):
            if (
                isinstance(estimate_value, bool)
                or not isinstance(estimate_value, int)
                or estimate_value < 0
            ):
                raise ValueError(f"{estimate_name} must be a non-negative integer")
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        refs = _evidence_refs(self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "fragment_hash", _canonical_hash(self.to_json()))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "fragment_id": self.fragment_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "fragment_type": self.fragment_type.value,
            "source_ref": self.source_ref,
            "source_revision": self.source_revision,
            "content_hash": self.content_hash,
            "token_estimate": self.token_estimate,
            "byte_estimate": self.byte_estimate,
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> LegacyContextFragmentV1:
        _exact_keys(
            value,
            {
                "schema_version",
                "fragment_id",
                "run_id",
                "subject",
                "fragment_type",
                "source_ref",
                "source_revision",
                "content_hash",
                "token_estimate",
                "byte_estimate",
                "disclosure_context",
                "evidence_refs",
            },
            "ContextFragment",
        )
        estimates: dict[str, int] = {}
        for name in ("token_estimate", "byte_estimate"):
            estimate = value[name]
            if isinstance(estimate, bool) or not isinstance(estimate, int) or estimate < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            estimates[name] = estimate
        return cls(
            fragment_id=_identifier(value["fragment_id"], "fragment_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            fragment_type=ContextFragmentType(value["fragment_type"]),  # type: ignore[arg-type]
            source_ref=_identifier(value["source_ref"], "source_ref", max_length=1024),
            source_revision=_positive_int(value["source_revision"], "source_revision"),
            content_hash=_digest(value["content_hash"], "content_hash"),
            token_estimate=estimates["token_estimate"],
            byte_estimate=estimates["byte_estimate"],
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            schema_version=_schema_version(value["schema_version"], "ContextFragment"),
        )


@dataclass(frozen=True, slots=True)
class ContextAssemblyBudget:
    max_total_tokens: int
    max_total_bytes: int
    generation_reserve_tokens: int
    safety_reserve_tokens: int

    def __post_init__(self) -> None:
        for name in (
            "max_total_tokens",
            "max_total_bytes",
            "generation_reserve_tokens",
            "safety_reserve_tokens",
        ):
            _positive_int(getattr(self, name), name)
        if self.generation_reserve_tokens + self.safety_reserve_tokens >= self.max_total_tokens:
            raise ValueError("generation and safety reserves must leave an input token budget")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "max_total_tokens": self.max_total_tokens,
            "max_total_bytes": self.max_total_bytes,
            "generation_reserve_tokens": self.generation_reserve_tokens,
            "safety_reserve_tokens": self.safety_reserve_tokens,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> ContextAssemblyBudget:
        _exact_keys(
            value,
            {
                "max_total_tokens",
                "max_total_bytes",
                "generation_reserve_tokens",
                "safety_reserve_tokens",
            },
            "ContextAssemblyBudget",
        )
        return cls(
            _positive_int(value["max_total_tokens"], "max_total_tokens"),
            _positive_int(value["max_total_bytes"], "max_total_bytes"),
            _positive_int(value["generation_reserve_tokens"], "generation_reserve_tokens"),
            _positive_int(value["safety_reserve_tokens"], "safety_reserve_tokens"),
        )


class ContextAssemblyReasonCode(StrEnum):
    INCLUDED = "context_fragment_included"
    DUPLICATE_OMITTED = "context_fragment_duplicate_omitted"
    TOKEN_BUDGET_OMITTED = "context_fragment_token_budget_omitted"
    BYTE_BUDGET_OMITTED = "context_fragment_byte_budget_omitted"
    DISCLOSURE_DENIED = "context_fragment_disclosure_denied"
    STALE_SOURCE_OMITTED = "context_fragment_stale_source_omitted"
    SUPPRESSED = "context_fragment_suppressed"


@dataclass(frozen=True, slots=True)
class LegacyContextAssemblyDecisionV1:
    decision_id: str
    run_id: str
    subject: str
    selected_fragment_refs: tuple[str, ...]
    omitted_fragment_refs: tuple[str, ...]
    snapshot_refs: tuple[str, ...]
    budget: ContextAssemblyBudget
    selected_token_estimate: int
    selected_byte_estimate: int
    disclosure_context: DisclosureContext
    evidence_refs: tuple[EvidenceRef, ...]
    reason_codes: tuple[ContextAssemblyReasonCode, ...]
    idempotency_key: str
    schema_version: int = HUMAN_MEMORY_SCHEMA_VERSION
    decision_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != HUMAN_MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported ContextAssemblyDecision schema_version")
        for value, name in (
            (self.decision_id, "decision_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _identifier(value, name)
        selected = tuple(
            _identifier(item, "selected_fragment_ref") for item in self.selected_fragment_refs
        )
        omitted = tuple(
            _identifier(item, "omitted_fragment_ref") for item in self.omitted_fragment_refs
        )
        snapshots = tuple(
            _identifier(item, "snapshot_ref", max_length=1024) for item in self.snapshot_refs
        )
        if len(set(selected)) != len(selected) or len(set(omitted)) != len(omitted):
            raise ValueError("fragment refs must be unique within each outcome")
        if set(selected) & set(omitted):
            raise ValueError("a fragment cannot be both selected and omitted")
        if not snapshots or len(set(snapshots)) != len(snapshots):
            raise ValueError("snapshot_refs must be non-empty and unique")
        if not isinstance(self.budget, ContextAssemblyBudget):
            raise TypeError("budget must use ContextAssemblyBudget")
        for estimate_value, estimate_name in (
            (self.selected_token_estimate, "selected_token_estimate"),
            (self.selected_byte_estimate, "selected_byte_estimate"),
        ):
            if (
                isinstance(estimate_value, bool)
                or not isinstance(estimate_value, int)
                or estimate_value < 0
            ):
                raise ValueError(f"{estimate_name} must be a non-negative integer")
        available_tokens = (
            self.budget.max_total_tokens
            - self.budget.generation_reserve_tokens
            - self.budget.safety_reserve_tokens
        )
        if self.selected_token_estimate > available_tokens:
            raise ValueError("selected_token_estimate exceeds the input token budget")
        if self.selected_byte_estimate > self.budget.max_total_bytes:
            raise ValueError("selected_byte_estimate exceeds max_total_bytes")
        if not isinstance(self.disclosure_context, DisclosureContext):
            raise TypeError("disclosure_context must use DisclosureContext")
        if self.disclosure_context.run_id != self.run_id:
            raise ValueError("disclosure_context run_id differs")
        evidence_refs = _evidence_refs(self.evidence_refs)
        reasons = tuple(ContextAssemblyReasonCode(reason) for reason in self.reason_codes)
        if not reasons or len(set(reasons)) != len(reasons):
            raise ValueError("reason_codes must be non-empty and unique")
        object.__setattr__(self, "selected_fragment_refs", selected)
        object.__setattr__(self, "omitted_fragment_refs", omitted)
        object.__setattr__(self, "snapshot_refs", snapshots)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "decision_hash", _canonical_hash(self.to_json()))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "selected_fragment_refs": list(self.selected_fragment_refs),
            "omitted_fragment_refs": list(self.omitted_fragment_refs),
            "snapshot_refs": list(self.snapshot_refs),
            "budget": self.budget.to_json(),
            "selected_token_estimate": self.selected_token_estimate,
            "selected_byte_estimate": self.selected_byte_estimate,
            "disclosure_context": self.disclosure_context.to_json(),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "reason_codes": [reason.value for reason in self.reason_codes],
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> LegacyContextAssemblyDecisionV1:
        _exact_keys(
            value,
            {
                "schema_version",
                "decision_id",
                "run_id",
                "subject",
                "selected_fragment_refs",
                "omitted_fragment_refs",
                "snapshot_refs",
                "budget",
                "selected_token_estimate",
                "selected_byte_estimate",
                "disclosure_context",
                "evidence_refs",
                "reason_codes",
                "idempotency_key",
            },
            "ContextAssemblyDecision",
        )
        estimates: dict[str, int] = {}
        for name in ("selected_token_estimate", "selected_byte_estimate"):
            estimate = value[name]
            if isinstance(estimate, bool) or not isinstance(estimate, int) or estimate < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            estimates[name] = estimate
        return cls(
            decision_id=_identifier(value["decision_id"], "decision_id"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            selected_fragment_refs=_strings(
                value["selected_fragment_refs"], "selected_fragment_refs"
            ),
            omitted_fragment_refs=_strings(value["omitted_fragment_refs"], "omitted_fragment_refs"),
            snapshot_refs=_strings(value["snapshot_refs"], "snapshot_refs"),
            budget=ContextAssemblyBudget.from_json(_object(value["budget"], "budget")),
            selected_token_estimate=estimates["selected_token_estimate"],
            selected_byte_estimate=estimates["selected_byte_estimate"],
            disclosure_context=DisclosureContext.from_json(
                _object(value["disclosure_context"], "disclosure_context")
            ),
            evidence_refs=_refs_from_json(value["evidence_refs"]),
            reason_codes=tuple(
                ContextAssemblyReasonCode(item)
                for item in _strings(value["reason_codes"], "reason_codes")
            ),
            idempotency_key=_identifier(value["idempotency_key"], "idempotency_key"),
            schema_version=_schema_version(value["schema_version"], "ContextAssemblyDecision"),
        )
