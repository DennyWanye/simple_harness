"""JSON-safe snapshots for one immutable Context OS tool scope.

The snapshot contains capability metadata and schemas, never handlers or live
services.  Recovery reconstructs the immutable value and then asks the live
registry to validate policy, visibility, schema, and environment eligibility.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolEffectClassification,
    ToolEligibilityContext,
    ToolSelectionDecision,
    canonical_hash,
)


_SKILL_SCOPE_WIDENING_CONTROLS = frozenset(
    {
        "skill_invoke",
        "workflow_spawn",
        "decision",
        "capability_activate",
        "capability_install",
        "capability_update",
        "capability_build",
        "capability_repair",
        "capability_rollback",
        "capability_uninstall",
    }
)


def exact_tool_ref_payload(ref: ToolCapabilityRef) -> dict[str, Any]:
    """Canonical security-relevant payload for one frozen Tool ref."""

    return {
        "capability_id": ref.capability_id,
        "name": ref.name,
        "toolset": ref.toolset,
        "source": ref.source,
        "schema_hash": ref.schema_hash,
        "spec_version": ref.spec_version,
        "permission_policy_version": ref.permission_policy_version,
        "permission_category": ref.permission_category,
        "dangerous": ref.dangerous,
    }


def exact_tool_ref_hash(ref: ToolCapabilityRef) -> str:
    """Hash one exact frozen Tool ref, excluding display-only description."""

    return canonical_hash(exact_tool_ref_payload(ref))


@dataclass(frozen=True, slots=True)
class PreparedSkillToolIntersectionV1:
    """Effective Skill tools derived only by narrowing a base ToolSet."""

    active_scope_ids: tuple[str, ...]
    active_scope_hashes: tuple[str, ...]
    allowed_tool_names: tuple[str, ...]
    effective_tool_names: tuple[str, ...]
    effective_tool_refs: tuple[ToolCapabilityRef, ...]
    effective_tool_fact_payloads: tuple[Mapping[str, Any], ...]
    effective_tool_ref_hashes: tuple[str, ...]
    effective_tool_refs_hash: str
    prepared_tool_set: PreparedToolSet


def intersect_prepared_skill_tools(
    prepared: PreparedToolSet,
    scopes: tuple[Any, ...],
    *,
    exact_tool_facts: tuple[Mapping[str, Any], ...] | None = None,
) -> PreparedSkillToolIntersectionV1:
    """Intersect active Skill scopes with one immutable base ToolSet.

    Skill frontmatter is never a source of new capabilities. Unknown names are
    therefore harmlessly absent from the result. Scope-widening control tools
    fail closed because a low-risk instruction scope may not activate them.
    """

    if not scopes:
        raise ValueError("at least one active skill scope is required")
    allowed: set[str] = set()
    scope_ids: list[str] = []
    scope_hashes: list[str] = []
    for scope in scopes:
        scope_hash = str(getattr(scope, "scope_hash", "") or "")
        if len(scope_hash) != 64:
            raise ValueError("invalid skill scope hash")
        scope_id = str(
            getattr(scope, "scope_id", "")
            or f"skill-scope:{scope_hash}"
        )
        names = {
            str(item)
            for item in (getattr(scope, "allowed_tools", ()) or ())
            if str(item)
        }
        forbidden = names & _SKILL_SCOPE_WIDENING_CONTROLS
        if forbidden:
            raise ValueError(
                f"skill scope contains widening controls: {sorted(forbidden)!r}"
            )
        scope_ids.append(scope_id)
        scope_hashes.append(scope_hash)
        allowed.update(names)

    current = {
        cap.ref.name: cap for cap in (*prepared.direct, *prepared.activated)
    }
    effective_names = tuple(sorted(allowed & current.keys()))
    effective_set = set(effective_names)
    effective_refs = tuple(
        current[name].ref for name in effective_names
    )
    fact_payloads: tuple[Mapping[str, Any], ...]
    if exact_tool_facts is None:
        fact_payloads = tuple(
            exact_tool_ref_payload(ref) for ref in effective_refs
        )
    else:
        facts_by_name: dict[str, Mapping[str, Any]] = {}
        for raw in exact_tool_facts:
            fact = dict(raw)
            name = str(fact.get("name") or "")
            if not name or name in facts_by_name:
                raise ValueError("captured exact Tool facts are malformed")
            facts_by_name[name] = fact
        missing = effective_set - facts_by_name.keys()
        if missing:
            raise ValueError(
                f"captured exact Tool facts are missing: {sorted(missing)!r}"
            )
        verified: list[Mapping[str, Any]] = []
        for ref in effective_refs:
            fact = facts_by_name[ref.name]
            if str(fact.get("schema_hash") or "") != ref.schema_hash:
                raise ValueError(
                    f"captured exact Tool schema drifted: {ref.name}"
                )
            required = {
                "name",
                "stable_handler_id",
                "tool_spec_fingerprint",
                "schema_hash",
                "execution_build_identity",
                "dispatch_adapter_id",
                "dispatch_adapter_version",
                "dispatch_adapter_fingerprint",
                "effect_policy",
                "idempotency",
            }
            if set(fact) != required:
                raise ValueError(
                    f"captured exact Tool fact fields drifted: {ref.name}"
                )
            verified.append(fact)
        fact_payloads = tuple(verified)
    ref_hashes = tuple(
        canonical_hash(dict(payload)) for payload in fact_payloads
    )
    classifications = tuple(
        item
        for item in prepared.effect_classifications
        if item.name in effective_set
    )
    narrowed = PreparedToolSet.create(
        scope_id=prepared.scope_id,
        revision=prepared.revision,
        registry_revision=prepared.registry_revision,
        direct=tuple(
            cap for cap in prepared.direct if cap.ref.name in effective_set
        ),
        deferred=tuple(
            ref for ref in prepared.deferred if ref.name in effective_set
        ),
        activated=tuple(
            cap for cap in prepared.activated if cap.ref.name in effective_set
        ),
        denied_names=prepared.denied_names,
        policy_fingerprint=prepared.policy_fingerprint,
        decisions=prepared.decisions,
        confirm_only_names=tuple(
            name for name in prepared.confirm_only_names if name in effective_set
        ),
        effect_policy_version=prepared.effect_policy_version,
        effect_policy_hash=prepared.effect_policy_hash,
        effect_classifications=classifications,
    )
    effective_hash = canonical_hash(
        {
            "schema": "prepared_skill_tool_intersection/v1",
            "base_scope_id": prepared.scope_id,
            "base_schema_fingerprint": prepared.schema_fingerprint,
            "active_scope_ids": sorted(scope_ids),
            "active_scope_hashes": sorted(scope_hashes),
            "effective_tool_ref_hashes": list(ref_hashes),
        }
    )
    return PreparedSkillToolIntersectionV1(
        active_scope_ids=tuple(sorted(scope_ids)),
        active_scope_hashes=tuple(sorted(scope_hashes)),
        allowed_tool_names=tuple(sorted(allowed)),
        effective_tool_names=effective_names,
        effective_tool_refs=effective_refs,
        effective_tool_fact_payloads=fact_payloads,
        effective_tool_ref_hashes=ref_hashes,
        effective_tool_refs_hash=effective_hash,
        prepared_tool_set=narrowed,
    )


def verify_prepared_skill_tool_intersection(
    prepared: PreparedToolSet,
    scopes: tuple[Any, ...],
    *,
    active_scope_ids: tuple[str, ...],
    effective_tool_refs_hash: str,
    exact_tool_facts: tuple[Mapping[str, Any], ...] | None = None,
) -> PreparedSkillToolIntersectionV1:
    """Recompute the boundary intersection and fail closed on any drift."""

    computed = intersect_prepared_skill_tools(
        prepared,
        scopes,
        exact_tool_facts=exact_tool_facts,
    )
    if tuple(sorted(active_scope_ids)) != computed.active_scope_ids:
        raise ValueError("active skill scope ids mismatch")
    if effective_tool_refs_hash != computed.effective_tool_refs_hash:
        raise ValueError("effective skill tool refs hash mismatch")
    return computed


def _ref_payload(ref: ToolCapabilityRef) -> dict[str, Any]:
    return {
        "capability_id": ref.capability_id,
        "name": ref.name,
        "toolset": ref.toolset,
        "source": ref.source,
        "description": ref.description,
        "schema_hash": ref.schema_hash,
        "spec_version": ref.spec_version,
        "permission_policy_version": ref.permission_policy_version,
        "permission_category": ref.permission_category,
        "dangerous": ref.dangerous,
    }


def _load_ref(raw: Mapping[str, Any]) -> ToolCapabilityRef:
    return ToolCapabilityRef(
        capability_id=str(raw["capability_id"]),
        name=str(raw["name"]),
        toolset=str(raw["toolset"]),
        source=str(raw["source"]),
        description=str(raw.get("description") or ""),
        schema_hash=str(raw["schema_hash"]),
        spec_version=str(raw.get("spec_version") or "v1"),
        permission_policy_version=str(
            raw.get("permission_policy_version") or "v1"
        ),
        permission_category=str(raw.get("permission_category") or "read_file"),
        dangerous=bool(raw.get("dangerous", False)),
    )


def _capability_payload(capability: PreparedToolCapability) -> dict[str, Any]:
    return {
        "ref": _ref_payload(capability.ref),
        "schema": capability.schema_copy(),
    }


def _load_capability(raw: Mapping[str, Any]) -> PreparedToolCapability:
    ref = raw.get("ref")
    schema = raw.get("schema")
    if not isinstance(ref, Mapping) or not isinstance(schema, Mapping):
        raise ValueError("prepared capability snapshot is malformed")
    return PreparedToolCapability(_load_ref(ref), dict(schema))


def dump_prepared_tool_set(prepared: PreparedToolSet) -> dict[str, Any]:
    return {
        "scope_id": prepared.scope_id,
        "revision": prepared.revision,
        "registry_revision": prepared.registry_revision,
        "direct": [_capability_payload(item) for item in prepared.direct],
        "deferred": [_ref_payload(item) for item in prepared.deferred],
        "activated": [_capability_payload(item) for item in prepared.activated],
        "denied_names": list(prepared.denied_names),
        "policy_fingerprint": prepared.policy_fingerprint,
        "schema_fingerprint": prepared.schema_fingerprint,
        "confirm_only_names": list(prepared.confirm_only_names),
        "effect_policy_version": prepared.effect_policy_version,
        "effect_policy_hash": prepared.effect_policy_hash,
        "effect_classifications": [
            item.fingerprint_payload()
            for item in prepared.effect_classifications
        ],
        "decisions": [
            {
                "name": item.name,
                "disposition": item.disposition,
                "reason": item.reason,
            }
            for item in prepared.decisions
        ],
    }


def load_prepared_tool_set(raw: Mapping[str, Any]) -> PreparedToolSet:
    direct = raw.get("direct", ())
    deferred = raw.get("deferred", ())
    activated = raw.get("activated", ())
    decisions = raw.get("decisions", ())
    effect_classifications = raw.get("effect_classifications", ())
    confirm_only_names = raw.get("confirm_only_names", ())
    if any(
        isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple))
        for value in (
            direct,
            deferred,
            activated,
            decisions,
            effect_classifications,
            confirm_only_names,
        )
    ):
        raise ValueError("prepared tool-set snapshot sequences are malformed")
    prepared = PreparedToolSet.create(
        scope_id=str(raw["scope_id"]),
        revision=int(raw["revision"]),
        registry_revision=int(raw["registry_revision"]),
        direct=tuple(_load_capability(item) for item in direct),
        deferred=tuple(_load_ref(item) for item in deferred),
        activated=tuple(_load_capability(item) for item in activated),
        denied_names=tuple(str(item) for item in raw.get("denied_names", ())),
        policy_fingerprint=str(raw["policy_fingerprint"]),
        confirm_only_names=tuple(str(item) for item in confirm_only_names),
        effect_policy_version=str(raw.get("effect_policy_version") or ""),
        effect_policy_hash=str(raw.get("effect_policy_hash") or ""),
        effect_classifications=tuple(
            ToolEffectClassification(
                name=str(item["name"]),
                schema_hash=str(item.get("schema_hash") or ""),
                stable_handler_id=str(item.get("stable_handler_id") or ""),
                effect_class=str(item["effect_class"]),
                idempotency=str(item["idempotency"]),
                target_normalizer_version=str(
                    item["target_normalizer_version"]
                ),
                disposition=str(item["disposition"]),
            )
            for item in effect_classifications
        ),
        decisions=tuple(
            ToolSelectionDecision(
                str(item["name"]),
                str(item["disposition"]),
                str(item["reason"]),
            )
            for item in decisions
        ),
    )
    expected = str(raw.get("schema_fingerprint") or "")
    if not expected or prepared.schema_fingerprint != expected:
        raise ValueError("prepared tool-set schema fingerprint mismatch")
    return prepared


def dump_eligibility(value: ToolEligibilityContext) -> dict[str, str]:
    return {
        "session_id": value.session_id,
        "request_id": value.request_id,
        "task_type": value.task_type,
        "mode": value.mode,
    }


def load_eligibility(raw: Mapping[str, Any]) -> ToolEligibilityContext:
    return ToolEligibilityContext(
        session_id=str(raw["session_id"]),
        request_id=str(raw["request_id"]),
        task_type=str(raw["task_type"]),
        mode=str(raw.get("mode") or "companion"),
    )


def dump_context_os_snapshot(
    prepared: PreparedToolSet, eligibility: ToolEligibilityContext
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "eligibility": dump_eligibility(eligibility),
        "tool_set": dump_prepared_tool_set(prepared),
    }


def load_context_os_snapshot(
    raw: Mapping[str, Any],
) -> tuple[PreparedToolSet, ToolEligibilityContext]:
    if int(raw.get("schema_version", 0)) != 1:
        raise ValueError("unsupported Context OS snapshot version")
    tool_set = raw.get("tool_set")
    eligibility = raw.get("eligibility")
    if not isinstance(tool_set, Mapping) or not isinstance(eligibility, Mapping):
        raise ValueError("Context OS snapshot is incomplete")
    return load_prepared_tool_set(tool_set), load_eligibility(eligibility)


__all__ = [
    "PreparedSkillToolIntersectionV1",
    "dump_context_os_snapshot",
    "dump_eligibility",
    "dump_prepared_tool_set",
    "exact_tool_ref_hash",
    "exact_tool_ref_payload",
    "intersect_prepared_skill_tools",
    "load_context_os_snapshot",
    "load_eligibility",
    "load_prepared_tool_set",
    "verify_prepared_skill_tool_intersection",
]
