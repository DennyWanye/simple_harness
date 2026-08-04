"""Deterministic evidence clustering and strict growth proposal contracts.

This module deliberately has no model, SQLite, or capability-manager access.
The model can fill the proposal-shaped fields, while the host supplies and
verifies owner identity, evidence, and binding fences before a proposal is
persisted or built.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import CandidateMode, GrowthEvent, JsonValue, OwnerRef

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?$")
_PROPOSAL_SCHEMA = "structured-growth-proposal-v1"
_CONTEXT_SCHEMA = "independent-evidence-context-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_required")
    return unicodedata.normalize("NFC", value.strip())


def _safe_id(value: object, name: str) -> str:
    text = _required_text(value, name)
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{name}_invalid")
    return text


def _digest(value: object, name: str) -> str:
    text = _required_text(value, name)
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{name}_invalid")
    return text


def _json_object(value: object, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name}_object_required")
    try:
        cloned = json.loads(_canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}_invalid_json") from exc
    if not isinstance(cloned, dict):
        raise ValueError(f"{name}_object_required")
    return MappingProxyType(cloned)


def normalize_intent(value: str) -> str:
    """Normalize an already host-extracted intent for context comparison.

    This is intentionally lexical, not a semantic classifier.  A model may
    help the host extract the intent, but repeating model prose cannot create
    another independent evidence context.
    """

    text = unicodedata.normalize("NFKC", _required_text(value, "intent"))
    return " ".join(text.casefold().split())


@dataclass(frozen=True, slots=True)
class GrowthEvidenceV1:
    owner: OwnerRef
    event_id: str
    capability_id: str
    root_run_id: str
    intent: str
    occurred_at: datetime
    source_kind: str
    source_ref: str
    retry_of_event_id: str | None = None
    previous_run_id: str | None = None
    current_run_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "capability_id",
            "root_run_id",
            "source_kind",
            "source_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "intent", normalize_intent(self.intent))
        occurred = self.occurred_at
        if (
            not isinstance(occurred, datetime)
            or occurred.tzinfo is None
            or occurred.utcoffset() is None
        ):
            raise ValueError("occurred_at_timezone_required")
        object.__setattr__(self, "occurred_at", occurred.astimezone(UTC))
        if self.schema_version != 1:
            raise ValueError("growth_evidence_schema_unsupported")
        if bool(self.previous_run_id) != bool(self.current_run_id):
            raise ValueError("retry_run_links_incomplete")

    @classmethod
    def from_growth_event(
        cls,
        event: GrowthEvent,
        *,
        occurred_at: datetime,
        intent: str | None = None,
        capability_id: str | None = None,
    ) -> "GrowthEvidenceV1":
        payload = dict(event.payload)
        facts = payload.get("facts")
        fact_map = facts if isinstance(facts, Mapping) else payload
        extracted = intent
        if extracted is None:
            for key in ("normalized_intent", "intent"):
                value = fact_map.get(key)
                if isinstance(value, str) and value.strip():
                    extracted = value
                    break
        extracted = extracted or event.context_key
        capability = capability_id
        if capability is None:
            for key in ("capability_id", "pack_id", "target_id"):
                value = fact_map.get(key)
                if isinstance(value, str) and value.strip():
                    capability = value
                    break
        capability = capability or "unscoped"
        retry_links = payload.get("retry_links")
        links = retry_links if isinstance(retry_links, Mapping) else {}
        return cls(
            owner=event.owner,
            event_id=event.event_id,
            capability_id=capability,
            root_run_id=event.root_run_id,
            intent=extracted,
            occurred_at=occurred_at,
            source_kind=event.source_kind,
            source_ref=event.source_ref,
            retry_of_event_id=event.retry_of,
            previous_run_id=(
                str(links["previous_run_id"])
                if links.get("previous_run_id")
                else None
            ),
            current_run_id=(
                str(links["current_run_id"])
                if links.get("current_run_id")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class IndependentEvidenceContextV1:
    context_id: str
    owner: OwnerRef
    capability_id: str
    normalized_intent: str
    event_ids: tuple[str, ...]
    root_run_ids: tuple[str, ...]
    first_observed_at: datetime
    last_observed_at: datetime
    schema_version: int = 1

    @property
    def evidence_count(self) -> int:
        return len(self.event_ids)


class EvidenceClusterer:
    """Collapse retries and repeated prose into independent user contexts."""

    def __init__(self, *, time_window: timedelta = timedelta(hours=24)) -> None:
        if time_window.total_seconds() <= 0:
            raise ValueError("evidence_time_window_invalid")
        self._window_seconds = int(time_window.total_seconds())

    def cluster(
        self, evidence: Sequence[GrowthEvidenceV1]
    ) -> tuple[IndependentEvidenceContextV1, ...]:
        items = tuple(evidence)
        if len({item.event_id for item in items}) != len(items):
            raise ValueError("duplicate_evidence_event_id")
        if not items:
            return ()

        parent = list(range(len(items)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            a, b = find(left), find(right)
            if a != b:
                parent[max(a, b)] = min(a, b)

        by_event_id = {item.event_id: index for index, item in enumerate(items)}
        by_run_id: dict[tuple[OwnerRef, str, str], list[int]] = {}
        by_base: dict[tuple[OwnerRef, str, str, str, int], int] = {}

        for index, item in enumerate(items):
            bucket = int(item.occurred_at.timestamp()) // self._window_seconds
            base = (
                item.owner,
                item.capability_id,
                item.root_run_id,
                item.intent,
                bucket,
            )
            previous = by_base.get(base)
            if previous is not None:
                union(index, previous)
            else:
                by_base[base] = index
            for run_id in (item.root_run_id, item.current_run_id):
                if run_id:
                    by_run_id.setdefault(
                        (item.owner, item.capability_id, run_id), []
                    ).append(index)

        # Only explicit host-issued retry references may cross a root or time
        # window.  Text similarity is never used as retry evidence.
        for index, item in enumerate(items):
            if item.retry_of_event_id in by_event_id:
                target = by_event_id[item.retry_of_event_id]
                if (
                    items[target].owner == item.owner
                    and items[target].capability_id == item.capability_id
                ):
                    union(index, target)
            if item.previous_run_id:
                for target in by_run_id.get(
                    (item.owner, item.capability_id, item.previous_run_id), ()
                ):
                    union(index, target)

        groups: dict[int, list[GrowthEvidenceV1]] = {}
        for index, item in enumerate(items):
            groups.setdefault(find(index), []).append(item)

        result: list[IndependentEvidenceContextV1] = []
        for group in groups.values():
            ordered = sorted(group, key=lambda item: (item.occurred_at, item.event_id))
            event_ids = tuple(sorted(item.event_id for item in ordered))
            root_ids = tuple(sorted({item.root_run_id for item in ordered}))
            intents = tuple(sorted({item.intent for item in ordered}))
            # Explicit retry links may span minor intent wording changes.  The
            # canonical intent remains deterministic and auditable.
            canonical_intent = intents[0]
            context_id = _hash(
                {
                    "schema": _CONTEXT_SCHEMA,
                    "owner": {
                        "profile_id": ordered[0].owner.profile_id,
                        "profile_generation": ordered[0].owner.profile_generation,
                    },
                    "capability_id": ordered[0].capability_id,
                    "event_ids": list(event_ids),
                    "root_run_ids": list(root_ids),
                    "normalized_intents": list(intents),
                }
            )
            result.append(
                IndependentEvidenceContextV1(
                    context_id=context_id,
                    owner=ordered[0].owner,
                    capability_id=ordered[0].capability_id,
                    normalized_intent=canonical_intent,
                    event_ids=event_ids,
                    root_run_ids=root_ids,
                    first_observed_at=ordered[0].occurred_at,
                    last_observed_at=ordered[-1].occurred_at,
                )
            )
        return tuple(sorted(result, key=lambda item: item.context_id))

    def independent_context_count(
        self, evidence: Sequence[GrowthEvidenceV1]
    ) -> int:
        return len(self.cluster(evidence))


def evidence_set_hash(evidence_event_ids: Sequence[str]) -> str:
    event_ids = tuple(sorted({_required_text(item, "evidence_event_id") for item in evidence_event_ids}))
    return _hash({"schema": "growth-evidence-set-v1", "event_ids": list(event_ids)})


class GrowthTargetKind(StrEnum):
    SKILL = "skill"
    WORKFLOW = "workflow"


class GrowthProposalDecision(StrEnum):
    CANDIDATE = "candidate"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class GrowthTargetIdentityV1:
    kind: GrowthTargetKind | str
    target_id: str
    stable_name: str
    pack_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", GrowthTargetKind(self.kind))
        for name in ("target_id", "stable_name", "pack_id"):
            object.__setattr__(self, name, _safe_id(getattr(self, name), name))

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "target_id": self.target_id,
            "stable_name": self.stable_name,
            "pack_id": self.pack_id,
        }


@dataclass(frozen=True, slots=True)
class CandidateBindingFenceV1:
    owner_key: str
    scope: str
    scope_key: str
    pack_id: str
    expected_absent: bool
    binding_generation: int
    version: str | None = None
    manifest_hash: str | None = None

    def __post_init__(self) -> None:
        for name in ("owner_key", "scope", "scope_key", "pack_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if not isinstance(self.expected_absent, bool):
            raise ValueError("expected_absent_bool_required")
        if (
            not isinstance(self.binding_generation, int)
            or isinstance(self.binding_generation, bool)
            or self.binding_generation < 0
        ):
            raise ValueError("binding_generation_invalid")
        if self.expected_absent:
            if (
                self.binding_generation != 0
                or self.version is not None
                or self.manifest_hash is not None
            ):
                raise ValueError("absent_fence_must_not_name_binding")
        else:
            object.__setattr__(self, "version", _required_text(self.version, "version"))
            object.__setattr__(
                self,
                "manifest_hash",
                _digest(self.manifest_hash, "manifest_hash"),
            )
            if self.binding_generation < 1:
                raise ValueError("present_fence_generation_required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "owner_key": self.owner_key,
            "scope": self.scope,
            "scope_key": self.scope_key,
            "pack_id": self.pack_id,
            "expected_absent": self.expected_absent,
            "binding_generation": self.binding_generation,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True, slots=True)
class StructuredGrowthProposalV1:
    """Closed schema accepted from a reflector or explicit-build adapter."""

    decision: GrowthProposalDecision | str
    evidence_event_ids: tuple[str, ...]
    evidence_set_hash: str
    target: GrowthTargetIdentityV1 | None = None
    candidate_mode: CandidateMode | str | None = None
    source_fence: CandidateBindingFenceV1 | None = None
    target_fence: CandidateBindingFenceV1 | None = None
    hypothesis: str | None = None
    structured_diff: Mapping[str, JsonValue] = field(default_factory=dict)
    expected_improvement: str | None = None
    risk_hints: tuple[str, ...] = ()
    evaluation_plan: tuple[Mapping[str, JsonValue], ...] = ()
    abstain_reason: str | None = None
    schema_id: str = _PROPOSAL_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", GrowthProposalDecision(self.decision))
        if self.schema_id != _PROPOSAL_SCHEMA:
            raise ValueError("growth_proposal_schema_unsupported")
        evidence_ids = tuple(
            sorted({_required_text(item, "evidence_event_id") for item in self.evidence_event_ids})
        )
        object.__setattr__(self, "evidence_event_ids", evidence_ids)
        object.__setattr__(
            self, "evidence_set_hash", _digest(self.evidence_set_hash, "evidence_set_hash")
        )
        object.__setattr__(
            self, "structured_diff", _json_object(self.structured_diff, "structured_diff")
        )
        object.__setattr__(
            self,
            "risk_hints",
            tuple(_required_text(item, "risk_hint") for item in self.risk_hints),
        )
        object.__setattr__(
            self,
            "evaluation_plan",
            tuple(_json_object(item, "evaluation_case") for item in self.evaluation_plan),
        )

        if self.decision is GrowthProposalDecision.ABSTAIN:
            if not self.abstain_reason:
                raise ValueError("abstain_reason_required")
            mutation_fields = (
                self.target,
                self.candidate_mode,
                self.source_fence,
                self.target_fence,
                self.hypothesis,
                self.expected_improvement,
            )
            if any(value is not None for value in mutation_fields):
                raise ValueError("abstain_cannot_carry_mutation")
            if self.structured_diff or self.risk_hints or self.evaluation_plan:
                raise ValueError("abstain_cannot_carry_build_plan")
            return

        if not evidence_ids:
            raise ValueError("candidate_proposal_requires_evidence")
        if self.target is None or self.target_fence is None:
            raise ValueError("candidate_proposal_target_required")
        mode = CandidateMode(self.candidate_mode)
        object.__setattr__(self, "candidate_mode", mode)
        for name in ("hypothesis", "expected_improvement"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if not self.structured_diff:
            raise ValueError("structured_diff_required")
        if not self.evaluation_plan:
            raise ValueError("evaluation_plan_required")
        if self.abstain_reason is not None:
            raise ValueError("candidate_cannot_carry_abstain_reason")
        self._validate_fences(mode)

    def _validate_fences(self, mode: CandidateMode) -> None:
        assert self.target is not None and self.target_fence is not None
        target = self.target_fence
        if target.pack_id != self.target.pack_id:
            raise ValueError("proposal_target_pack_mismatch")
        if mode is CandidateMode.GENESIS:
            if self.source_fence is not None or not target.expected_absent:
                raise ValueError("genesis_fence_invalid")
            return
        if self.source_fence is None:
            raise ValueError("source_fence_required")
        source = self.source_fence
        if source.expected_absent or source.pack_id != target.pack_id:
            raise ValueError("source_fence_invalid")
        if mode is CandidateMode.UPDATE:
            if target.expected_absent:
                raise ValueError("update_target_must_exist")
            if (
                source.owner_key,
                source.scope,
                source.scope_key,
                source.binding_generation,
            ) != (
                target.owner_key,
                target.scope,
                target.scope_key,
                target.binding_generation,
            ):
                raise ValueError("update_source_target_fence_mismatch")
            return
        if (
            source.owner_key != "builtin"
            or source.scope != "builtin"
            or not target.expected_absent
            or target.scope != "user"
            or not target.owner_key.startswith("companion:")
        ):
            raise ValueError("builtin_override_fence_invalid")

    @property
    def proposal_hash(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_id": self.schema_id,
            "decision": self.decision.value,
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_set_hash": self.evidence_set_hash,
            "target": None if self.target is None else self.target.to_dict(),
            "candidate_mode": (
                None
                if self.candidate_mode is None
                else CandidateMode(self.candidate_mode).value
            ),
            "source_fence": (
                None if self.source_fence is None else self.source_fence.to_dict()
            ),
            "target_fence": (
                None if self.target_fence is None else self.target_fence.to_dict()
            ),
            "hypothesis": self.hypothesis,
            "structured_diff": dict(self.structured_diff),
            "expected_improvement": self.expected_improvement,
            "risk_hints": list(self.risk_hints),
            "evaluation_plan": [dict(item) for item in self.evaluation_plan],
            "abstain_reason": self.abstain_reason,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "StructuredGrowthProposalV1":
        required = {
            "schema_id",
            "decision",
            "evidence_event_ids",
            "evidence_set_hash",
            "target",
            "candidate_mode",
            "source_fence",
            "target_fence",
            "hypothesis",
            "structured_diff",
            "expected_improvement",
            "risk_hints",
            "evaluation_plan",
            "abstain_reason",
        }
        if set(raw) != required:
            raise ValueError("growth_proposal_fields_invalid")

        def target(value: object) -> GrowthTargetIdentityV1 | None:
            if value is None:
                return None
            if not isinstance(value, Mapping) or set(value) != {
                "kind",
                "target_id",
                "stable_name",
                "pack_id",
            }:
                raise ValueError("growth_target_fields_invalid")
            return GrowthTargetIdentityV1(**dict(value))

        def fence(value: object) -> CandidateBindingFenceV1 | None:
            if value is None:
                return None
            if not isinstance(value, Mapping) or set(value) != {
                "owner_key",
                "scope",
                "scope_key",
                "pack_id",
                "expected_absent",
                "binding_generation",
                "version",
                "manifest_hash",
            }:
                raise ValueError("binding_fence_fields_invalid")
            return CandidateBindingFenceV1(**dict(value))

        evidence_ids = raw["evidence_event_ids"]
        risk_hints = raw["risk_hints"]
        evaluation_plan = raw["evaluation_plan"]
        if (
            not isinstance(evidence_ids, list)
            or not isinstance(risk_hints, list)
            or not isinstance(evaluation_plan, list)
        ):
            raise ValueError("growth_proposal_array_invalid")
        if not all(isinstance(item, str) for item in evidence_ids):
            raise ValueError("evidence_event_ids_invalid")
        if not all(isinstance(item, str) for item in risk_hints):
            raise ValueError("risk_hints_invalid")
        if not all(isinstance(item, Mapping) for item in evaluation_plan):
            raise ValueError("evaluation_plan_invalid")
        diff = raw["structured_diff"]
        if not isinstance(diff, Mapping):
            raise ValueError("structured_diff_invalid")
        return cls(
            schema_id=str(raw["schema_id"]),
            decision=str(raw["decision"]),
            evidence_event_ids=tuple(str(item) for item in evidence_ids),
            evidence_set_hash=str(raw["evidence_set_hash"]),
            target=target(raw["target"]),
            candidate_mode=(
                None if raw["candidate_mode"] is None else str(raw["candidate_mode"])
            ),
            source_fence=fence(raw["source_fence"]),
            target_fence=fence(raw["target_fence"]),
            hypothesis=None if raw["hypothesis"] is None else str(raw["hypothesis"]),
            structured_diff=dict(diff),
            expected_improvement=(
                None
                if raw["expected_improvement"] is None
                else str(raw["expected_improvement"])
            ),
            risk_hints=tuple(str(item) for item in risk_hints),
            evaluation_plan=tuple(dict(item) for item in evaluation_plan),
            abstain_reason=(
                None if raw["abstain_reason"] is None else str(raw["abstain_reason"])
            ),
        )

    @classmethod
    def abstain(
        cls,
        *,
        evidence_event_ids: Sequence[str],
        evidence_set_hash: str,
        reason: str,
    ) -> "StructuredGrowthProposalV1":
        return cls(
            decision=GrowthProposalDecision.ABSTAIN,
            evidence_event_ids=tuple(evidence_event_ids),
            evidence_set_hash=evidence_set_hash,
            abstain_reason=_required_text(reason, "abstain_reason"),
        )


class GrowthReflector:
    """Host-side validation around a zero-tool reflector result.

    The actual background model run lives behind the existing Harness.  This
    class only accepts its closed-schema result and applies deterministic
    evidence sufficiency rules; it has no tool or Builder handle.
    """

    def __init__(
        self,
        *,
        clusterer: EvidenceClusterer | None = None,
        minimum_independent_contexts: int = 2,
    ) -> None:
        if minimum_independent_contexts < 1:
            raise ValueError("minimum_independent_contexts_invalid")
        self._clusterer = clusterer or EvidenceClusterer()
        self._minimum_contexts = minimum_independent_contexts

    def review(
        self,
        model_output: Mapping[str, object] | StructuredGrowthProposalV1,
        *,
        evidence: Sequence[GrowthEvidenceV1],
        explicit_user_signal: bool = False,
    ) -> StructuredGrowthProposalV1:
        proposal = (
            model_output
            if isinstance(model_output, StructuredGrowthProposalV1)
            else StructuredGrowthProposalV1.from_mapping(model_output)
        )
        event_ids = tuple(sorted(item.event_id for item in evidence))
        expected_hash = evidence_set_hash(event_ids)
        if proposal.evidence_event_ids != event_ids:
            raise ValueError("proposal_evidence_set_mismatch")
        if proposal.evidence_set_hash != expected_hash:
            raise ValueError("proposal_evidence_hash_mismatch")
        if proposal.decision is GrowthProposalDecision.ABSTAIN:
            return proposal
        required = 1 if explicit_user_signal else self._minimum_contexts
        if self._clusterer.independent_context_count(evidence) < required:
            return StructuredGrowthProposalV1.abstain(
                evidence_event_ids=event_ids,
                evidence_set_hash=expected_hash,
                reason="insufficient_independent_evidence",
            )
        return proposal


__all__ = [
    "CandidateBindingFenceV1",
    "EvidenceClusterer",
    "GrowthEvidenceV1",
    "GrowthProposalDecision",
    "GrowthReflector",
    "GrowthTargetIdentityV1",
    "GrowthTargetKind",
    "IndependentEvidenceContextV1",
    "StructuredGrowthProposalV1",
    "evidence_set_hash",
    "normalize_intent",
]
