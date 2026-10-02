# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Domain profiles (P3.3 plan v3 D1, Phase3 §5.3).

A profile says what a Mission of this kind may look like: which evidence it may cite,
which criteria grammar it may use, which verification layers run, which prompt versions
replace the built-in ones, and the completion rules its results are graded by.

The deployment owns these constants; a Mission freezes one at creation and every later
read uses the frozen snapshot.  There is exactly one current profile per domain
(删旧平面模式第三刀第 4、5 步, 2026-10-02): no historical versions, no document domain,
no conflict/synthesis templates.  A snapshot written under another schema is refused,
never migrated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ..contracts.models import SYSTEM_DEFAULT_POLICY, VERIFICATION_LAYERS

#: 2: the profile lost ``planner_floor``, ``conflict_template``,
#: ``synthesis_default_policy`` and ``adapters`` (删旧平面模式第三刀第 4 步).
DOMAIN_SCHEMA_VERSION = 2

CODE_DOMAIN = "code-v1"
APPWORLD_DOMAIN = "appworld-v1"
DRONE_SIM_DOMAIN = "drone-sim-v1"

# how a criterion is spelled; "free" is a free-text criterion judged by the Critic
CRITERION_KINDS = ("pytest", "file", "action", "free")
EVIDENCE_KINDS = ("pytest", "file", "artifact", "tool-run", "knowledge", "source")


def criterion_kind(criterion: str) -> str:
    head, sep, _ = criterion.partition(":")
    return head if sep and head in CRITERION_KINDS else "free"


_TUPLE_FIELDS = (
    "allowed_input_kinds",
    "allowed_artifact_kinds",
    "allowed_evidence_kinds",
    "criterion_kinds",
    "runs_layers",
    "default_policy",
    "source_roots",
)
_MAPPING_FIELDS = ("completion_rules", "role_templates", "context_wording")


@dataclass(frozen=True, slots=True)
class DomainProfileV1:
    id: str
    version: str
    allowed_input_kinds: tuple[str, ...]
    allowed_artifact_kinds: tuple[str, ...]
    allowed_evidence_kinds: tuple[str, ...]
    criterion_kinds: tuple[str, ...]
    # the layers this domain runs at all; the deployment's own ``deployed_layers`` still
    # filters them
    runs_layers: tuple[str, ...]
    default_policy: tuple[str, ...]
    # the adapter whose non-empty verdict counts as "an external check ran" (plan D1)
    external_check: str
    source_roots: tuple[str, ...] = ()
    completion_rules: Mapping[str, Any] = field(default_factory=dict)
    role_templates: Mapping[str, str] = field(default_factory=dict)
    context_wording: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _MAPPING_FIELDS:
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DomainProfileV1:
        """Read the stored profile, never reconstruct it from the live registry."""
        if value.get("schema") != DOMAIN_SCHEMA_VERSION:
            raise ValueError("unsupported frozen domain schema")
        data = dict(value)
        data.pop("schema")
        expected = {
            "id", "version", "external_check", *_TUPLE_FIELDS, *_MAPPING_FIELDS,
        }
        if set(data) != expected:
            raise ValueError(
                f"frozen domain fields differ: missing {sorted(expected - set(data))}, "
                f"unknown {sorted(set(data) - expected)}"
            )
        for key in _TUPLE_FIELDS:
            data[key] = tuple(data[key])
        return cls(**data)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": DOMAIN_SCHEMA_VERSION,
            "id": self.id,
            "version": self.version,
            "allowed_input_kinds": list(self.allowed_input_kinds),
            "allowed_artifact_kinds": list(self.allowed_artifact_kinds),
            "allowed_evidence_kinds": list(self.allowed_evidence_kinds),
            "criterion_kinds": list(self.criterion_kinds),
            "runs_layers": list(self.runs_layers),
            "default_policy": list(self.default_policy),
            "external_check": self.external_check,
            "source_roots": list(self.source_roots),
            "completion_rules": dict(self.completion_rules),
            "role_templates": dict(self.role_templates),
            "context_wording": dict(self.context_wording),
        }


#: The general task (code domain).  User decision 2026-09-26: it may carry reference
#: material under ``sources/``, frozen per Attempt, mounted read-only and citable as
#: ``source`` evidence.  Strict citation (the removed document domain) became a
#: requirement the reviewer judges (user decision 2026-10-02, option A).
CODE_PROFILE = DomainProfileV1(
    id=CODE_DOMAIN,
    version="6",
    allowed_input_kinds=("text/*", "application/octet-stream"),
    allowed_artifact_kinds=("text/*",),
    allowed_evidence_kinds=("pytest", "file", "artifact", "tool-run", "knowledge", "source"),
    criterion_kinds=("pytest", "file", "action", "free"),
    runs_layers=VERIFICATION_LAYERS,
    default_policy=SYSTEM_DEFAULT_POLICY,
    external_check="code_test",
    source_roots=("sources/",),
    completion_rules={"result_envelope_contract": "candidate-json-v1"},
)

APPWORLD_PROFILE = DomainProfileV1(
    id=APPWORLD_DOMAIN,
    version="5",
    allowed_input_kinds=("text/*", "application/json"),
    allowed_artifact_kinds=("text/*",),
    allowed_evidence_kinds=("file", "artifact", "tool-run", "knowledge"),
    criterion_kinds=("file", "free"),
    runs_layers=("format_check", "rule_check", "critic_review", "human_review"),
    default_policy=("format_check", "rule_check", "critic_review"),
    external_check="appworld-saved-world-after-stop",
    completion_rules={"handler": "appworld-v1"},
)

DRONE_SIM_PROFILE = DomainProfileV1(
    id=DRONE_SIM_DOMAIN,
    version="2",
    allowed_input_kinds=("text/*", "application/octet-stream"),
    allowed_artifact_kinds=("text/*",),
    allowed_evidence_kinds=("pytest", "file", "artifact", "tool-run", "knowledge"),
    criterion_kinds=("pytest", "file", "action", "free"),
    runs_layers=("format_check", "rule_check", "critic_review", "human_review"),
    default_policy=("format_check", "rule_check", "critic_review"),
    external_check="drone-sim-durable-ledger-v1",
    completion_rules={"handler": "drone-sim-v1", "simulation_only": True},
)

#: P2.3d / defect D1: the *hierarchical* Worker prompt each domain's Missions get.
#:
#: Kept beside the profiles rather than inside one.  ``DomainProfileV1.role_templates``
#: is read as "role name → prompt version" — ``template_for_domain`` looks a role up in
#: it, and callers iterate it expecting every key to name a role — so a
#: ``worker_hierarchical`` entry there is a key that breaks both readings.  It is also
#: not a *frozen* fact about a Mission in the way the profile is: the hierarchical mode
#: had no working AppWorld path before this slice, so there is no replay to preserve and
#: nothing here needs a profile version bump.
#:
#: A domain with no entry falls back to the code-domain ``WORKER_HIERARCHICAL``; an
#: entry naming a version this build does not register is refused
#: (:func:`~..runtime.role_templates.hierarchical_worker_for_domain`).
HIERARCHICAL_WORKER_TEMPLATES: Mapping[str, str] = MappingProxyType({
    DRONE_SIM_DOMAIN: "worker-drone-sim-hierarchical-v1",
    APPWORLD_DOMAIN: "worker-appworld-hierarchical-v1",
})

DOMAINS: Mapping[str, DomainProfileV1] = MappingProxyType({
    CODE_DOMAIN: CODE_PROFILE, APPWORLD_DOMAIN: APPWORLD_PROFILE,
    DRONE_SIM_DOMAIN: DRONE_SIM_PROFILE,
})


def resolve_domain(domain_id: str | None) -> DomainProfileV1:
    """The current profile for ``domain_id``; ``None`` is the general task (code domain)."""

    if domain_id is None:
        return CODE_PROFILE
    return DOMAINS[domain_id]


def check_against_domain(
    domain: DomainProfileV1,
    *,
    key: str,
    success_criteria: Sequence[str],
    verification_policy: Sequence[str] | None = None,
    evidence_kinds: Sequence[str] = (),
) -> list[str]:
    """Problems with one Task's criteria, layers and evidence kinds under ``domain``."""

    problems: list[str] = []
    for criterion in success_criteria:
        kind = criterion_kind(criterion)
        if kind not in domain.criterion_kinds:
            problems.append(
                f"{key}: criterion {criterion!r} uses {kind!r}, which the {domain.id} domain "
                f"does not allow (allowed: {sorted(domain.criterion_kinds)})"
            )
    policy = tuple(verification_policy or ())
    unknown = [layer for layer in policy if layer not in VERIFICATION_LAYERS]
    if unknown:
        problems.append(f"{key}: unknown verification layers {sorted(unknown)}")
    # a layer the domain does not run is not "extra checking": nothing here can run it,
    # so the Task would be built and then fail forever.  Refuse it at the gate instead.
    replaced = [layer for layer in policy if layer in VERIFICATION_LAYERS]
    replaced = [layer for layer in replaced if layer not in domain.runs_layers]
    if replaced:
        problems.append(
            f"{key}: verification policy names {sorted(replaced)}, which the {domain.id} "
            "domain does not run"
        )
    for kind in evidence_kinds:
        if kind not in domain.allowed_evidence_kinds:
            problems.append(
                f"{key}: evidence of kind {kind!r} is not allowed in the {domain.id} domain "
                f"(allowed: {sorted(domain.allowed_evidence_kinds)})"
            )
    return problems


__all__ = (
    "APPWORLD_DOMAIN",
    "APPWORLD_PROFILE",
    "CODE_DOMAIN",
    "CODE_PROFILE",
    "CRITERION_KINDS",
    "DOMAIN_SCHEMA_VERSION",
    "DOMAINS",
    "DRONE_SIM_DOMAIN",
    "DRONE_SIM_PROFILE",
    "EVIDENCE_KINDS",
    "HIERARCHICAL_WORKER_TEMPLATES",
    "DomainProfileV1",
    "check_against_domain",
    "criterion_kind",
    "resolve_domain",
)
