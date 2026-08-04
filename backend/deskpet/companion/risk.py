"""Deterministic, non-executing risk classification for growth candidates.

This module consumes frozen manifest facts only.  It never imports, loads, or
executes candidate code.  Anything not proven safe is classified into a path
that requires an exact evaluation authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .store import canonical_hash

CandidateKind = Literal[
    "instruction",
    "personal_workflow",
    "code",
    "hook",
    "local_runtime",
    "unknown",
]
RiskLevel = Literal["low", "medium", "high", "unknown"]
PermitMode = Literal["safe_auto", "safe_static", "user_authorized"]

_DANGEROUS_EFFECTS = {
    "external",
    "external_send",
    "irreversible",
    "payment",
    "destructive",
    "credential",
    "privacy",
    "unknown",
}
_KNOWN_EFFECTS = _DANGEROUS_EFFECTS | {
    "read_only",
    "idempotent_write",
    "draft_only",
    "reversible_local",
}
_EXECUTABLE_KINDS = {"code", "hook", "local_runtime"}


@dataclass(frozen=True, slots=True)
class EffectFactV1:
    tool_ref: str
    effect_kind: str
    idempotent: bool

    def __post_init__(self) -> None:
        if not self.tool_ref or not self.effect_kind:
            raise ValueError("tool_ref and effect_kind are required")

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_ref": self.tool_ref,
            "effect_kind": self.effect_kind,
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True, slots=True)
class CandidateRiskInputV1:
    candidate_id: str
    package_hash: str
    candidate_kind: CandidateKind
    declared_tool_refs: tuple[str, ...] = ()
    referenced_tool_refs: tuple[str, ...] = ()
    tool_facts: tuple[EffectFactV1, ...] = ()
    source_tool_refs: tuple[str, ...] = ()
    permissions_added: tuple[str, ...] = ()
    topology_expanded: bool = False
    enforce_source_nonexpansion: bool = False
    workflow_nodes_known: bool = True
    workflow_dataflow_safe: bool = True

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.package_hash:
            raise ValueError("candidate identity is required")
        if self.candidate_kind not in {
            "instruction",
            "personal_workflow",
            "code",
            "hook",
            "local_runtime",
            "unknown",
        }:
            raise ValueError("unknown candidate kind")
        for name in (
            "declared_tool_refs",
            "referenced_tool_refs",
            "source_tool_refs",
            "permissions_added",
        ):
            values = tuple(sorted(set(getattr(self, name))))
            if any(not item for item in values):
                raise ValueError(f"{name} cannot contain empty values")
            object.__setattr__(self, name, values)
        facts = tuple(sorted(self.tool_facts, key=lambda item: item.tool_ref))
        if len({item.tool_ref for item in facts}) != len(facts):
            raise ValueError("tool facts cannot contain duplicate refs")
        object.__setattr__(self, "tool_facts", facts)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "package_hash": self.package_hash,
            "candidate_kind": self.candidate_kind,
            "declared_tool_refs": list(self.declared_tool_refs),
            "referenced_tool_refs": list(self.referenced_tool_refs),
            "tool_facts": [item.to_dict() for item in self.tool_facts],
            "source_tool_refs": list(self.source_tool_refs),
            "permissions_added": list(self.permissions_added),
            "topology_expanded": self.topology_expanded,
            "enforce_source_nonexpansion": self.enforce_source_nonexpansion,
            "workflow_nodes_known": self.workflow_nodes_known,
            "workflow_dataflow_safe": self.workflow_dataflow_safe,
        }


@dataclass(frozen=True, slots=True)
class EffectTopologyDiffV1:
    permissions_added: tuple[str, ...] = ()
    effects_added: tuple[str, ...] = ()
    sequence_changed: bool = False
    targets_changed: bool = False
    dataflow_changed: bool = False
    topology_expanded: bool = False
    unknown_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("permissions_added", "effects_added", "unknown_effects"):
            object.__setattr__(
                self, name, tuple(sorted(set(getattr(self, name))))
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "permissions_added": list(self.permissions_added),
            "effects_added": list(self.effects_added),
            "sequence_changed": self.sequence_changed,
            "targets_changed": self.targets_changed,
            "dataflow_changed": self.dataflow_changed,
            "topology_expanded": self.topology_expanded,
            "unknown_effects": list(self.unknown_effects),
        }


@dataclass(frozen=True, slots=True)
class StaticRiskPreflightResultV1:
    candidate_id: str
    package_hash: str
    candidate_kind: CandidateKind
    safe_auto_eligible: bool
    direct_os_effects_unverifiable: bool
    high_risk_signals: tuple[str, ...]
    unknown_signals: tuple[str, ...]
    facts_hash: str
    preflight_hash: str


@dataclass(frozen=True, slots=True)
class RiskAssessmentV1:
    candidate_id: str
    package_hash: str
    risk: RiskLevel
    permit_mode: PermitMode
    reasons: tuple[str, ...]
    preflight_hash: str
    topology_diff_hash: str
    risk_hash: str


class StaticRiskPreflight:
    """Classify frozen manifest facts without touching candidate bytes."""

    def inspect(
        self, candidate: CandidateRiskInputV1
    ) -> StaticRiskPreflightResultV1:
        high: set[str] = set()
        unknown: set[str] = set()
        if candidate.candidate_kind in _EXECUTABLE_KINDS:
            high.add("executable_candidate")
        elif candidate.candidate_kind == "unknown":
            unknown.add("unknown_candidate_kind")

        declared = set(candidate.declared_tool_refs)
        referenced = set(candidate.referenced_tool_refs)
        facts = {item.tool_ref: item for item in candidate.tool_facts}
        if referenced - declared:
            unknown.add("undeclared_tool_reference")
        if declared - facts.keys():
            unknown.add("tool_manifest_fact_missing")
        for tool_ref in declared:
            fact = facts.get(tool_ref)
            if fact is None:
                continue
            if fact.effect_kind not in _KNOWN_EFFECTS:
                unknown.add("unknown_tool_effect")
            elif fact.effect_kind in _DANGEROUS_EFFECTS:
                high.add("irreversible_or_external_effect")
            elif fact.effect_kind != "read_only" or not fact.idempotent:
                high.add("non_readonly_or_non_idempotent_tool")

        if candidate.permissions_added:
            high.add("permission_expansion")
        if candidate.topology_expanded:
            high.add("tool_topology_expansion")
        if candidate.enforce_source_nonexpansion and (
            declared - set(candidate.source_tool_refs)
        ):
            high.add("tool_topology_expansion")
        if candidate.candidate_kind == "personal_workflow" and (
            not candidate.workflow_nodes_known
            or not candidate.workflow_dataflow_safe
        ):
            unknown.add("unknown_workflow_node_or_dataflow")

        safe_kind = candidate.candidate_kind in {
            "instruction",
            "personal_workflow",
        }
        safe_auto = safe_kind and not high and not unknown
        facts_hash = canonical_hash(candidate.to_dict())
        payload = {
            "schema_version": 1,
            "candidate_id": candidate.candidate_id,
            "package_hash": candidate.package_hash,
            "candidate_kind": candidate.candidate_kind,
            "safe_auto_eligible": safe_auto,
            "direct_os_effects_unverifiable": (
                candidate.candidate_kind in _EXECUTABLE_KINDS
            ),
            "high_risk_signals": sorted(high),
            "unknown_signals": sorted(unknown),
            "facts_hash": facts_hash,
        }
        return StaticRiskPreflightResultV1(
            candidate_id=candidate.candidate_id,
            package_hash=candidate.package_hash,
            candidate_kind=candidate.candidate_kind,
            safe_auto_eligible=safe_auto,
            direct_os_effects_unverifiable=(
                candidate.candidate_kind in _EXECUTABLE_KINDS
            ),
            high_risk_signals=tuple(sorted(high)),
            unknown_signals=tuple(sorted(unknown)),
            facts_hash=facts_hash,
            preflight_hash=canonical_hash(payload),
        )


class CapabilityRiskPolicy:
    """Apply deterministic topology policy; unsafe signals override auto paths."""

    def assess(
        self,
        preflight: StaticRiskPreflightResultV1,
        topology_diff: EffectTopologyDiffV1,
    ) -> RiskAssessmentV1:
        high = set(preflight.high_risk_signals)
        unknown = set(preflight.unknown_signals)
        added_effects = set(topology_diff.effects_added)
        if topology_diff.unknown_effects or any(
            item not in _KNOWN_EFFECTS for item in added_effects
        ):
            unknown.add("unknown_effect_topology")
        if added_effects & _DANGEROUS_EFFECTS:
            high.add("irreversible_or_external_effect")
        if topology_diff.permissions_added:
            high.add("permission_expansion")

        medium = any(
            (
                topology_diff.sequence_changed,
                topology_diff.targets_changed,
                topology_diff.dataflow_changed,
                topology_diff.topology_expanded,
                bool(added_effects),
            )
        )
        if high:
            risk: RiskLevel = "high"
            reasons = tuple(sorted(high | unknown))
        elif unknown:
            risk = "unknown"
            reasons = tuple(sorted(unknown))
        elif preflight.safe_auto_eligible and not medium:
            risk = "low"
            reasons = ("manifest_proven_readonly",)
        else:
            risk = "medium"
            reasons = ("effect_topology_changed",)
        diff_hash = canonical_hash(topology_diff.to_dict())
        permit_mode: PermitMode
        if risk == "low":
            permit_mode = "safe_auto"
        elif (
            preflight.candidate_kind == "instruction"
            and risk in {"medium", "high"}
        ):
            # Instruction bytes are inspected by a frozen zero-tool runner.
            # Their activation risk remains non-low; this mode authorizes only
            # static evaluation and can never authorize activation.
            permit_mode = "safe_static"
        else:
            permit_mode = "user_authorized"
        payload = {
            "schema_version": 1,
            "candidate_id": preflight.candidate_id,
            "package_hash": preflight.package_hash,
            "risk": risk,
            "permit_mode": permit_mode,
            "reasons": list(reasons),
            "preflight_hash": preflight.preflight_hash,
            "topology_diff_hash": diff_hash,
        }
        return RiskAssessmentV1(
            candidate_id=preflight.candidate_id,
            package_hash=preflight.package_hash,
            risk=risk,
            permit_mode=permit_mode,
            reasons=reasons,
            preflight_hash=preflight.preflight_hash,
            topology_diff_hash=diff_hash,
            risk_hash=canonical_hash(payload),
        )


__all__ = [
    "CapabilityRiskPolicy",
    "CandidateRiskInputV1",
    "EffectFactV1",
    "EffectTopologyDiffV1",
    "RiskAssessmentV1",
    "StaticRiskPreflight",
    "StaticRiskPreflightResultV1",
]
