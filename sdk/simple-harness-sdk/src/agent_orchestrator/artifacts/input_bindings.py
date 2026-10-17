# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""DataRequirement -> BoundInput -> InputManifest, resolved as a pure function (P2.2b).

Plan §24.1 decision 3 and annex TG §4.3 / §10.1-10.3 replace "an Attempt starts
from every accepted artifact of every ancestor" with a declared contract:

``DataRequirement``
    what the planner promised: *this* producer occurrence's *this* output port
    feeds *that* consumer's *that* input port, under a named schema, assurance
    and freshness policy.
``ResolvedInputBinding``
    that promise resolved, before dispatch, to one exact identity — result id,
    acceptance id + support revision, artifact id + content hash.  The annex is
    explicit that ``latest`` may never be left for the Worker to interpret.
``InputManifest``
    the immutable set of those bindings, with a canonical hash that
    ``Acceptance.input_manifest_hash`` can quote.

What this module deliberately does **not** do, so the two modes stay apart until
P2.3b wires them together:

* it never reads a file, a workspace or a content-addressed store — the plan it
  produces says *where* bytes should go, and something else puts them there;
* it never touches :mod:`.versioning`; the former all-ancestors merge kept its
  bytes and its ``ArtifactConflict`` semantics (plan §18.2, §24.1 decision 4);
* it never propagates ORDER.  An ORDER-only predecessor has no DataRequirement,
  so it cannot appear in a manifest however many artifacts it accepted — which
  is the whole point of TG §10.1;
* it never *infers* schema compatibility.  Exact identity, or a registered
  declaration, or a refusal.  No containment proof, no ``Any``.

Every refusal is a :class:`ResolutionProblem` with its own kind, because the
caller has to act differently on each: an ambiguous single-valued port needs a
human choice or a synthesis Task, an unordered set port needs a declaration, an
unusable witness needs a re-check.  A single boolean would hide all three.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..contracts.evidence_state import ValidityWitness, WitnessDecision, WitnessPurpose
from ..contracts.htn import (
    ORDERED_CARDINALITIES,
    BoundInput,
    DataRequirement,
    OccurrenceId,
    PortCardinality,
    PortOrdering,
    PortSpec,
    TaskRef,
    TaskSemanticBindingV1,
)
from ..contracts.models import ContractError, sha256_hex
from ..contracts.semantic_base import TypedRefKind, VersionedRef
from .paths import normalise_workspace_path

# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------


class DisclosureState(StrEnum):
    """TG §4.3: revocation, deletion and purpose limits are checked *now* — an input
    whose source was withdrawn is not readable because it used to be."""

    DISCLOSABLE = "disclosable"
    REVOKED = "revoked"
    DELETED = "deleted"
    PURPOSE_RESTRICTED = "purpose_restricted"


class ResolutionProblemKind(StrEnum):
    """One value per class of refusal.  Never collapsed into a boolean."""

    AMBIGUOUS_SINGLE_PORT = "ambiguous_single_port"
    SET_PORT_UNORDERED = "set_port_unordered"
    SET_PORT_ORDER_INCOMPLETE = "set_port_order_incomplete"
    UNDECLARED_PORT = "undeclared_port"
    FOREIGN_REQUIREMENT = "foreign_requirement"
    UNBOUND_REQUIRED_PORT = "unbound_required_port"
    SCHEMA_MISMATCH = "schema_mismatch"
    CONVERTER_NOT_DEPLOYED = "converter_not_deployed"
    NOT_DISCLOSABLE = "not_disclosable"
    WITNESS_MISSING = "witness_missing"
    WITNESS_NOT_USABLE = "witness_not_usable"
    WITNESS_PURPOSE_MISMATCH = "witness_purpose_mismatch"
    WITNESS_CONSUMER_MISMATCH = "witness_consumer_mismatch"
    WITNESS_SUPPORT_MISMATCH = "witness_support_mismatch"
    WITNESS_EPOCH_UNKNOWN = "witness_epoch_unknown"
    PENDING_PRODUCER = "pending_producer"
    PROVISIONAL_NOT_AUTHORIZED = "provisional_not_authorized"
    REVISION_NOT_AVAILABLE = "revision_not_available"
    TARGET_PATH_CONFLICT = "target_path_conflict"
    TARGET_PATH_INVALID = "target_path_invalid"
    MAP_KEY_MISSING = "map_key_missing"
    MAP_KEY_DUPLICATE = "map_key_duplicate"
    MAP_KEY_MISPLACED = "map_key_misplaced"
    # 数据边交出的非端口文件（交付绑定）也要见证与版本证明
    DELIVERY_WITNESS_MISSING = "delivery_witness_missing"
    DELIVERY_WITNESS_NOT_USABLE = "delivery_witness_not_usable"
    DELIVERY_SOURCE_UNAVAILABLE = "delivery_source_unavailable"
    DELIVERY_VERSION_CONFLICT = "delivery_version_conflict"


@dataclass(frozen=True, slots=True)
class ResolutionProblem:
    """One refusal.

    ``input_ports`` is the full set of ports a problem touches; ``input_port`` is
    the convenience for the common single-port case and is folded into
    ``input_ports`` automatically.  A target-path conflict spans several ports, so
    it fills ``input_ports`` and leaves the singular field empty rather than
    naming one port and hiding the rest.
    """

    kind: ResolutionProblemKind
    detail: str
    input_port: str | None = None
    requirement_ids: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    input_ports: tuple[str, ...] = ()
    #: the producer tasks of ``candidates``, in the same order (delivery conflicts name
    #: the steps so the write-conflict repair request can say who wrote what)
    producers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.input_ports and self.input_port is not None:
            object.__setattr__(self, "input_ports", (self.input_port,))
        else:
            object.__setattr__(self, "input_ports", tuple(self.input_ports))
        object.__setattr__(self, "producers", tuple(self.producers))

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "detail": self.detail,
            "input_port": self.input_port,
            "input_ports": list(self.input_ports),
            "producers": list(self.producers),
            "requirement_ids": list(self.requirement_ids),
            "candidates": list(self.candidates),
        }


class ManifestNotFrozen(RuntimeError):
    """The manifest still holds a symbolic binding, so it has no dispatch identity.

    Implementation annex §4.3: freezing a dispatch intent means every input is an
    exact identity.  A manifest that still says "whatever occ-b eventually
    accepts" has no hash an Acceptance could quote and nothing a Worker could be
    handed, so both are refused rather than approximated.
    """


# --------------------------------------------------------------------------------------
# Resource identity (TG §10.2: at least (namespace/workspace/object-id, normalised path))
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    """Where a byte string lives.  The relative path alone is **not** an identity.

    ``attempt-A/report.md`` and ``attempt-B/report.md`` are two resources: two
    isolated namespaces that happen to agree on a relative name.  Collapsing them
    because the tail matches is exactly the independent-exploration bug TG §10.2
    names.
    """

    namespace: str
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ContractError("resource.namespace must be a non-blank string")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ContractError("resource.path must be a non-blank string")
        object.__setattr__(self, "path", normalise_workspace_path(self.path))
        if not self.path:
            raise ContractError("resource.path normalises to nothing")

    @property
    def is_escaping(self) -> bool:
        """``..`` survives normalisation on purpose, so the caller can refuse it."""

        return ".." in self.path.split("/")

    def collation_key(self, *, case_insensitive: bool) -> tuple[str, str]:
        """The key two identities collide on under a given platform rule."""

        if case_insensitive:
            return (self.namespace.casefold(), self.path.casefold())
        return (self.namespace, self.path)

    def to_json(self) -> dict[str, Any]:
        return {"namespace": self.namespace, "path": self.path}


# --------------------------------------------------------------------------------------
# What the resolver is given
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptedOutput:
    """One accepted contribution at one producer occurrence's output port."""

    producer_occurrence: OccurrenceId
    producer_task_ref: TaskRef
    output_port: str
    producer_result_id: str
    acceptance_id: str
    support_revision: int
    artifact_id: str
    content_hash: str
    schema_ref: VersionedRef
    source_revision: str
    source_identity: ResourceIdentity
    producer_ordinal: int = 0
    order_keys: Mapping[str, str] = field(default_factory=dict)
    disclosure: DisclosureState = DisclosureState.DISCLOSABLE
    disclosure_scope: str = "mission"
    provisional: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.schema_ref, VersionedRef):
            raise ContractError("accepted_output.schema_ref must be a VersionedRef")
        if not isinstance(self.source_identity, ResourceIdentity):
            raise ContractError("accepted_output.source_identity must be a ResourceIdentity")
        object.__setattr__(self, "order_keys", dict(self.order_keys))


@dataclass(frozen=True, slots=True)
class AcceptedOutputsIndex:
    """The accepted outputs the resolver may choose from, plus who has finished.

    ``completed_producers`` is separate from the outputs because "this producer
    accepted nothing at that port" and "this producer has not finished" are
    different answers: the first leaves a required port unbound, the second
    leaves a symbolic binding that a later resolution can complete.
    """

    outputs: tuple[AcceptedOutput, ...] = ()
    completed_producers: frozenset[OccurrenceId] = frozenset()
    authorized_revisions: Mapping[tuple[OccurrenceId, str], str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", tuple(self.outputs))
        object.__setattr__(self, "completed_producers", frozenset(self.completed_producers))
        object.__setattr__(self, "authorized_revisions", dict(self.authorized_revisions))

    def at(self, occurrence: OccurrenceId, output_port: str) -> tuple[AcceptedOutput, ...]:
        """Every accepted output at that port, in a deterministic order."""

        found = [
            item
            for item in self.outputs
            if item.producer_occurrence == occurrence and item.output_port == output_port
        ]
        return tuple(sorted(found, key=lambda item: (item.source_revision, item.artifact_id)))

    def is_complete(self, occurrence: OccurrenceId) -> bool:
        return occurrence in self.completed_producers

    def authorized_revision(self, occurrence: OccurrenceId, output_port: str) -> str | None:
        return self.authorized_revisions.get((occurrence, output_port))


@dataclass(frozen=True, slots=True)
class SchemaCompatibilityRule:
    """A *registered* statement that one produced schema may feed one required one.

    It is directed and it names its authority: the annex forbids claiming the
    system can decide arbitrary JSON Schema containment, so the only yes that
    exists is one a person or a registry wrote down.
    """

    produced: VersionedRef
    required: VersionedRef
    declaration_ref: str
    converter_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaCompatibilityRegistry:
    rules: tuple[SchemaCompatibilityRule, ...] = ()

    def match(
        self, produced: VersionedRef, required: VersionedRef
    ) -> SchemaCompatibilityRule | None:
        for rule in self.rules:
            if rule.produced == produced and rule.required == required:
                return rule
        return None


@dataclass(frozen=True, slots=True)
class ExplicitPortOrder:
    """The declared order of an ``EXPLICIT`` set port.

    ``PortSpec`` carries ``ordering`` and ``order_key`` but no explicit list, so
    the list rides beside the policy until the contract grows a field for it (see
    the P2.2b journal's contract-change request).
    """

    input_port: str
    ordered_requirement_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolutionPolicy:
    """Everything the resolution depends on that is not in the network itself."""

    schema_registry: SchemaCompatibilityRegistry = field(
        default_factory=SchemaCompatibilityRegistry
    )
    explicit_orders: tuple[ExplicitPortOrder, ...] = ()
    #: input ports on which speculative execution has been authorised.
    provisional_ports: frozenset[str] = frozenset()
    require_witness: bool = True
    #: The purposes a witness may carry to license *this* use.  §11.5: permission
    #: to use one conclusion for one purpose is not permission for another, so a
    #: PLAN-purpose witness does not license starting the work.
    witness_purposes: frozenset[WitnessPurpose] = frozenset({WitnessPurpose.START})
    now_ms: int = 0
    #: scope_id -> the epoch that is current now (I19: a cached witness expires).
    scope_epochs: Mapping[str, int] = field(default_factory=dict)
    #: I19 is a *comparison*; not knowing the current epoch is not a pass.  A
    #: caller that genuinely has no epoch service must say so out loud.
    allow_unknown_scope: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_epochs", dict(self.scope_epochs))
        object.__setattr__(self, "provisional_ports", frozenset(self.provisional_ports))
        object.__setattr__(self, "witness_purposes", frozenset(self.witness_purposes))

    def explicit_order(self, input_port: str) -> tuple[str, ...] | None:
        for declared in self.explicit_orders:
            if declared.input_port == input_port:
                return tuple(declared.ordered_requirement_ids)
        return None


# --------------------------------------------------------------------------------------
# What the resolver produces
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedInputBinding:
    """Implementation annex §4.3: one input, frozen to one exact identity.

    The identity half **is** the contract's own :class:`~..contracts.htn.BoundInput`,
    embedded rather than copied field by field: requirement id, result id,
    acceptance id, artifact id, content hash, schema and source revision have
    exactly one home, so a wire record and a resolved binding cannot drift into
    disagreeing about which artifact was bound.  Everything else here is
    *routing and policy* around that identity — who produced it at which port,
    which consumer port it feeds, where it came from, and under which read,
    freshness and disclosure terms.
    """

    binding_id: str
    bound: BoundInput
    producer_task_ref: TaskRef
    producer_occurrence: OccurrenceId
    output_port: str
    support_revision: int
    consumer_task_ref: TaskRef
    input_port: str
    port_ordinal: int
    source_identity: ResourceIdentity
    produced_schema_ref: VersionedRef
    read_policy: str
    freshness_policy: str
    disclosure_scope: str
    #: TG §5.5: the declared key of this binding on a MAP port; ``None`` elsewhere.
    map_key: str | None = None
    requires_reacceptance: bool = False
    provisional: bool = False
    witness_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bound, BoundInput):
            raise ContractError("binding.bound must be a contracts.htn.BoundInput")

    def as_bound_input(self) -> BoundInput:
        """The contract record this binding resolves to, unchanged."""

        return self.bound

    # The identity is read through the embedded BoundInput so there is one source
    # of truth, not two that a later edit could let diverge.
    @property
    def requirement_id(self) -> str:
        return self.bound.requirement_id

    @property
    def producer_result_id(self) -> str:
        return self.bound.producer_result_id

    @property
    def acceptance_id(self) -> str:
        return self.bound.acceptance_id

    @property
    def artifact_id(self) -> str:
        return self.bound.artifact_id

    @property
    def content_hash(self) -> str:
        return self.bound.content_hash

    @property
    def schema_ref(self) -> VersionedRef:
        """The *required* schema the input was bound against."""

        return self.bound.schema_ref

    @property
    def source_revision(self) -> str:
        return self.bound.source_revision

    def to_json(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "bound_input": self.bound.to_json(),
            "producer_task_ref": str(self.producer_task_ref),
            "producer_occurrence": str(self.producer_occurrence),
            "output_port": self.output_port,
            "support_revision": self.support_revision,
            "consumer_task_ref": str(self.consumer_task_ref),
            "input_port": self.input_port,
            "port_ordinal": self.port_ordinal,
            "source_identity": self.source_identity.to_json(),
            "produced_schema_ref": self.produced_schema_ref.to_json(),
            "read_policy": self.read_policy,
            "freshness_policy": self.freshness_policy,
            "disclosure_scope": self.disclosure_scope,
            "map_key": self.map_key,
            "requires_reacceptance": self.requires_reacceptance,
            "provisional": self.provisional,
            "witness_id": self.witness_id,
        }


@dataclass(frozen=True, slots=True)
class SymbolicBinding:
    """A promise kept open because its producer has not finished yet."""

    requirement_id: str
    producer_occurrence: OccurrenceId
    output_port: str
    consumer_task_ref: TaskRef
    input_port: str

    def to_json(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "producer_occurrence": str(self.producer_occurrence),
            "output_port": self.output_port,
            "consumer_task_ref": str(self.consumer_task_ref),
            "input_port": self.input_port,
        }


@dataclass(frozen=True, slots=True)
class DeliveryBinding:
    """One non-port file a DATA-bound producer hands on, frozen to one exact identity
    (2026-10-11 第三批：冻结清单里的接力文件).

    A port binding says "the plan named this document"; a delivery binding says "the
    producer's *acceptance* covered this file too, and the consumer may read it under
    the same licence".  ``acceptance_id`` is the real producer's acceptance — for a
    relayed file that is the acceptance of the step that wrote it, and
    ``carried_from`` names the continuation producer whose own frozen manifest
    proved the relay.  ``witness_id`` is the consumer's START licence over that
    acceptance, exactly as on a port binding.  ``path`` is the place in the
    consumer's namespace (the producer's own path; deliveries are not re-rooted).
    """

    binding_id: str
    producer_task_ref: TaskRef
    acceptance_id: str
    support_revision: int
    artifact_id: str
    path: str
    content_hash: str
    witness_id: str | None = None
    carried_from: TaskRef | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "producer_task_ref": str(self.producer_task_ref),
            "acceptance_id": self.acceptance_id,
            "support_revision": self.support_revision,
            "artifact_id": self.artifact_id,
            "path": self.path,
            "content_hash": self.content_hash,
            "witness_id": self.witness_id,
            "carried_from": None if self.carried_from is None else str(self.carried_from),
        }


def delivery_binding_id(consumer: str, artifact_id: str, acceptance_id: str) -> str:
    """Keyed by consumer + artifact + acceptance: one file reaching one consumer through
    two relay paths is one binding, not two."""

    return (
        "delivery-"
        + sha256_hex(
            {
                "consumer_task_ref": consumer,
                "artifact_id": artifact_id,
                "acceptance_id": acceptance_id,
            }
        )[:16]
    )


@dataclass(frozen=True, slots=True)
class InputManifest:
    """The immutable inputs of one dispatch.

    Bindings are stored in canonical order — by input port, then by the port's
    declared ordinal — so the hash depends on what was resolved and on the
    *declared* set-port order, never on the order the requirements happened to
    arrive in.  ``deliveries`` are the non-port files the same producers hand on,
    one chosen version per path (2026-10-11 第三批); they are part of the hash, so
    "what this Attempt was built on" is the whole manifest and nothing outside it.
    """

    consumer_task_ref: TaskRef
    bindings: tuple[ResolvedInputBinding, ...] = ()
    pending: tuple[SymbolicBinding, ...] = ()
    deliveries: tuple[DeliveryBinding, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(
                self.bindings,
                key=lambda item: (item.input_port, item.port_ordinal, item.binding_id),
            )
        )
        if len({item.binding_id for item in ordered}) != len(ordered):
            raise ContractError("manifest must not repeat a binding_id")
        object.__setattr__(self, "bindings", ordered)
        object.__setattr__(
            self,
            "pending",
            tuple(sorted(self.pending, key=lambda item: (item.input_port, item.requirement_id))),
        )
        delivered = tuple(sorted(self.deliveries, key=lambda item: (item.path, item.binding_id)))
        if any(not isinstance(item, DeliveryBinding) for item in delivered):
            raise ContractError("manifest.deliveries must be DeliveryBinding records")
        if len({item.binding_id for item in delivered}) != len(delivered):
            raise ContractError("manifest must not repeat a delivery binding_id")
        if len({item.path for item in delivered}) != len(delivered):
            raise ContractError("manifest must hold one delivered version per path")
        object.__setattr__(self, "deliveries", delivered)

    @property
    def is_frozen(self) -> bool:
        return not self.pending

    @property
    def has_provisional(self) -> bool:
        return any(item.provisional for item in self.bindings)

    @property
    def provisional_ports(self) -> frozenset[str]:
        return frozenset(item.input_port for item in self.bindings if item.provisional)

    def for_port(self, input_port: str) -> tuple[ResolvedInputBinding, ...]:
        return tuple(item for item in self.bindings if item.input_port == input_port)

    def required_ports_satisfied(self, consumer: TaskSemanticBindingV1) -> bool:
        """TG §4.3: a provisional binding may run, but never *claims* the DATA is met."""

        for spec in consumer.input_ports:
            if not spec.required:
                continue
            firm = [item for item in self.for_port(spec.port_key) if not item.provisional]
            if not firm:
                return False
        return True

    def _hash_payload(self) -> dict[str, Any]:
        return {
            "consumer_task_ref": str(self.consumer_task_ref),
            "bindings": [item.to_json() for item in self.bindings],
            "deliveries": [item.to_json() for item in self.deliveries],
        }

    def manifest_hash(self) -> str:
        if not self.is_frozen:
            raise ManifestNotFrozen(
                f"{len(self.pending)} input(s) are still symbolic; "
                "a dispatch identity needs every input resolved (implementation annex §4.3)"
            )
        return sha256_hex(self._hash_payload())

    def to_json(self) -> dict[str, Any]:
        return {
            "consumer_task_ref": str(self.consumer_task_ref),
            "bindings": [item.to_json() for item in self.bindings],
            "deliveries": [item.to_json() for item in self.deliveries],
            "pending": [item.to_json() for item in self.pending],
            "manifest_hash": self.manifest_hash() if self.is_frozen else None,
        }


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """``manifest`` or ``problems`` — and, for a pending producer, both."""

    manifest: InputManifest | None
    problems: tuple[ResolutionProblem, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def kinds(self) -> frozenset[ResolutionProblemKind]:
        return frozenset(problem.kind for problem in self.problems)

    def of_kind(self, kind: ResolutionProblemKind) -> tuple[ResolutionProblem, ...]:
        return tuple(problem for problem in self.problems if problem.kind is kind)


# --------------------------------------------------------------------------------------
# resolve_declared_inputs
# --------------------------------------------------------------------------------------


def _binding_id(requirement: DataRequirement, candidate: AcceptedOutput, consumer: str) -> str:
    return (
        "binding-"
        + sha256_hex(
            {
                "requirement_id": requirement.requirement_id,
                "consumer_task_ref": consumer,
                "input_port": requirement.input_port,
                "artifact_id": candidate.artifact_id,
                "content_hash": candidate.content_hash,
                "source_revision": candidate.source_revision,
            }
        )[:16]
    )


def _select_by_revision(
    requirement: DataRequirement,
    candidates: Sequence[AcceptedOutput],
    accepted: AcceptedOutputsIndex,
    policy: ResolutionPolicy,
) -> tuple[tuple[AcceptedOutput, ...], str | None, ResolutionProblem | None]:
    """Narrow the candidates to the revision authorised now: every DATA edge follows
    the acceptance that currently counts (TaskGraph 补全第五批：没有"钉住旧版本"）。"""

    authorized = accepted.authorized_revision(
        requirement.producer_occurrence, requirement.output_port
    )
    target = authorized
    if target is None and candidates:
        # 读不到授权版本：报"来源不可用"，不在候选里任选、不取最新
        return (
            (),
            None,
            ResolutionProblem(
                kind=ResolutionProblemKind.REVISION_NOT_AVAILABLE,
                detail=(
                    f"the authorised revision of {requirement.producer_occurrence}."
                    f"{requirement.output_port} cannot be read"
                ),
                input_port=requirement.input_port,
                requirement_ids=(requirement.requirement_id,),
            ),
        )
    if target is None:
        # Nothing pins this input yet: every accepted revision is still a candidate,
        # and an ambiguity here is reported rather than resolved by recency.
        return tuple(candidates), None, None
    matching = tuple(item for item in candidates if item.source_revision == target)
    if not matching:
        return (
            (),
            target,
            ResolutionProblem(
                kind=ResolutionProblemKind.REVISION_NOT_AVAILABLE,
                detail=(
                    f"revision {target!r} of {requirement.producer_occurrence}."
                    f"{requirement.output_port} is not among the accepted outputs"
                ),
                input_port=requirement.input_port,
                requirement_ids=(requirement.requirement_id,),
            ),
        )
    return matching, target, None


def _check_schema(
    requirement: DataRequirement,
    port_spec: PortSpec,
    candidate: AcceptedOutput,
    policy: ResolutionPolicy,
) -> ResolutionProblem | None:
    """Exact identity, or a registered declaration that needs no conversion.

    TG §5.5: a declaration that needs a converter has to produce a new artifact and
    have it accepted; no conversion path is deployed, so such a declaration is
    refused by name rather than binding the original bytes under the new label.
    """

    required = requirement.schema_ref
    if required != port_spec.schema_ref:
        return ResolutionProblem(
            kind=ResolutionProblemKind.SCHEMA_MISMATCH,
            detail=(
                f"requirement {requirement.requirement_id!r} declares schema "
                f"{required.id!r} v{required.version} but port {port_spec.port_key!r} "
                f"declares {port_spec.schema_ref.id!r} v{port_spec.schema_ref.version}"
            ),
            input_port=requirement.input_port,
            requirement_ids=(requirement.requirement_id,),
        )
    if candidate.schema_ref == required:
        return None
    rule = policy.schema_registry.match(candidate.schema_ref, required)
    if rule is not None and rule.converter_ref is None:
        return None
    if rule is not None:
        return ResolutionProblem(
            kind=ResolutionProblemKind.CONVERTER_NOT_DEPLOYED,
            detail=(
                f"declaration {rule.declaration_ref!r} maps {candidate.schema_ref.id!r} "
                f"v{candidate.schema_ref.version} onto {required.id!r} v{required.version} "
                f"through converter {rule.converter_ref!r}, and no conversion path is deployed; "
                f"the bytes of {candidate.artifact_id} are not bound under the new schema"
            ),
            input_port=requirement.input_port,
            requirement_ids=(requirement.requirement_id,),
            candidates=(candidate.artifact_id,),
        )
    return ResolutionProblem(
        kind=ResolutionProblemKind.SCHEMA_MISMATCH,
        detail=(
            f"{candidate.artifact_id} carries schema {candidate.schema_ref.id!r} "
            f"v{candidate.schema_ref.version} and no registered declaration maps it onto "
            f"{required.id!r} v{required.version}; containment is never inferred"
        ),
        input_port=requirement.input_port,
        requirement_ids=(requirement.requirement_id,),
        candidates=(candidate.artifact_id,),
    )


def _check_witness(
    requirement: DataRequirement,
    candidate: AcceptedOutput,
    consumer_task_ref: TaskRef,
    witnesses: Mapping[str, ValidityWitness],
    policy: ResolutionPolicy,
) -> tuple[str | None, ResolutionProblem | None]:
    """Is *this* witness permission for *this* consumer to use *this* support, now?

    §11.5 / AER §8.1: a witness is permission to use one conclusion, once, for one
    purpose — so four things have to agree before it licenses a binding, and each
    disagreement is its own refusal:

    ``purpose``
        a PLAN-purpose witness does not authorise starting work.
    ``consumer_ref``
        a witness issued to another task is that task's permission, not this one's.
    ``support_revision``
        a witness taken over revision 1 of the support says nothing about
        revision 99; reusing it would be quoting an old reading of new evidence.
    ``decision`` + freshness
        USABLE, at the current scope epoch, before the deadline (invariant I19).
    """

    def refuse(kind: ResolutionProblemKind, detail: str) -> tuple[None, ResolutionProblem]:
        return None, ResolutionProblem(
            kind=kind,
            detail=detail,
            input_port=requirement.input_port,
            requirement_ids=(requirement.requirement_id,),
            candidates=(candidate.artifact_id,),
        )

    verdict = witness_verdict(
        witnesses.get(candidate.acceptance_id),
        acceptance_id=candidate.acceptance_id,
        artifact_id=candidate.artifact_id,
        support_revision=candidate.support_revision,
        consumer_task_ref=consumer_task_ref,
        policy=policy,
    )
    if verdict is None:
        return None, None
    witness_id, problem = verdict
    if problem is not None:
        return refuse(*problem)
    return witness_id, None


def witness_verdict(
    witness: ValidityWitness | None,
    *,
    acceptance_id: str,
    artifact_id: str,
    support_revision: int,
    consumer_task_ref: TaskRef,
    policy: ResolutionPolicy,
) -> tuple[str | None, tuple[ResolutionProblemKind, str] | None] | None:
    """The one reading of "does this witness license this consumer over this acceptance".

    Port bindings and delivery bindings (2026-10-11 第三批) ask the same question and
    must get the same answer, so the five checks live here once.  ``None`` means the
    policy asks for no witness at all; otherwise ``(witness_id, None)`` licenses and
    ``(None, (kind, detail))`` refuses.
    """

    if witness is None:
        if not policy.require_witness:
            return None
        return None, (
            ResolutionProblemKind.WITNESS_MISSING,
            f"no validity witness covers acceptance {acceptance_id}",
        )
    if witness.purpose not in policy.witness_purposes:
        return None, (
            ResolutionProblemKind.WITNESS_PURPOSE_MISMATCH,
            (
                f"witness {witness.witness_id} was issued for {witness.purpose!s}; binding an "
                f"input needs one of {sorted(str(p) for p in policy.witness_purposes)}"
            ),
        )
    if witness.consumer_ref.kind is not TypedRefKind.TASK or witness.consumer_ref.id != str(
        consumer_task_ref
    ):
        return None, (
            ResolutionProblemKind.WITNESS_CONSUMER_MISMATCH,
            (
                f"witness {witness.witness_id} was issued to "
                f"{witness.consumer_ref.kind!s} {witness.consumer_ref.id!r}, "
                f"not to task {str(consumer_task_ref)!r}"
            ),
        )
    if witness.support_revision != support_revision:
        return None, (
            ResolutionProblemKind.WITNESS_SUPPORT_MISMATCH,
            (
                f"witness {witness.witness_id} was taken over support revision "
                f"{witness.support_revision} but {artifact_id} carries "
                f"{support_revision}"
            ),
        )
    if witness.decision is not WitnessDecision.USABLE:
        return None, (
            ResolutionProblemKind.WITNESS_NOT_USABLE,
            f"witness {witness.witness_id} decided {witness.decision!s}",
        )
    if witness.scope_id not in policy.scope_epochs:
        if not policy.allow_unknown_scope:
            return None, (
                ResolutionProblemKind.WITNESS_EPOCH_UNKNOWN,
                (
                    f"the current epoch of scope {witness.scope_id!r} is unknown, so witness "
                    f"{witness.witness_id} cannot be compared against it (I19); set "
                    "allow_unknown_scope to accept that risk explicitly"
                ),
            )
        current_epoch = witness.scope_epoch
    else:
        current_epoch = policy.scope_epochs[witness.scope_id]
    if not witness.is_fresh_for(now_ms=policy.now_ms, current_scope_epoch=current_epoch):
        return None, (
            ResolutionProblemKind.WITNESS_NOT_USABLE,
            (
                f"witness {witness.witness_id} is stale: it was taken at scope epoch "
                f"{witness.scope_epoch} (now {current_epoch}) and expires at "
                f"{witness.not_after_ms}"
            ),
        )
    return witness.witness_id, None


def _check_map_key(requirement: DataRequirement, port_spec: PortSpec) -> ResolutionProblem | None:
    """A MAP port needs a declared key on every input; no other port takes one."""

    if port_spec.cardinality is PortCardinality.MAP and requirement.map_key is None:
        return ResolutionProblem(
            kind=ResolutionProblemKind.MAP_KEY_MISSING,
            detail=(
                f"requirement {requirement.requirement_id!r} feeds map port "
                f"{port_spec.port_key!r} without declaring a key"
            ),
            input_port=requirement.input_port,
            requirement_ids=(requirement.requirement_id,),
        )
    if port_spec.cardinality is not PortCardinality.MAP and requirement.map_key is not None:
        return ResolutionProblem(
            kind=ResolutionProblemKind.MAP_KEY_MISPLACED,
            detail=(
                f"requirement {requirement.requirement_id!r} declares key "
                f"{requirement.map_key!r} but port {port_spec.port_key!r} is "
                f"{port_spec.cardinality!s}, not a map"
            ),
            input_port=requirement.input_port,
            requirement_ids=(requirement.requirement_id,),
        )
    return None


def _order_by_map_key(
    port_spec: PortSpec,
    entries: Sequence[tuple[DataRequirement, AcceptedOutput, str | None]],
) -> tuple[tuple[int, ...], ResolutionProblem | None]:
    """Ordinal = rank of the declared key; one key with two live candidates is refused."""

    keys = [str(entry[0].map_key) for entry in entries]
    if len(set(keys)) != len(keys):
        shared = sorted({key for key in keys if keys.count(key) > 1})
        return (), ResolutionProblem(
            kind=ResolutionProblemKind.MAP_KEY_DUPLICATE,
            detail=(
                f"map port {port_spec.port_key!r} has more than one live candidate under "
                f"key(s) {shared}"
            ),
            input_port=port_spec.port_key,
            requirement_ids=tuple(sorted({entry[0].requirement_id for entry in entries})),
            candidates=tuple(sorted(entry[1].artifact_id for entry in entries)),
        )
    ranked = sorted(keys)
    return tuple(ranked.index(key) for key in keys), None


def _order_entries(
    port_spec: PortSpec,
    entries: Sequence[tuple[DataRequirement, AcceptedOutput, str | None]],
    policy: ResolutionPolicy,
) -> tuple[tuple[int, ...], ResolutionProblem | None]:
    """Give each entry of a set port its ordinal, or say why it cannot be ordered."""

    requirement_ids = tuple(entry[0].requirement_id for entry in entries)
    if port_spec.ordering is None:
        return (), ResolutionProblem(
            kind=ResolutionProblemKind.SET_PORT_UNORDERED,
            detail=(
                f"set port {port_spec.port_key!r} has {len(entries)} binding(s) and no declared "
                "ordering; a set port may not fall back to whichever producer finished most "
                "recently"
            ),
            input_port=port_spec.port_key,
            requirement_ids=requirement_ids,
        )
    if port_spec.ordering is PortOrdering.BY_PRODUCER_ORDINAL:
        keys: list[Any] = [entry[1].producer_ordinal for entry in entries]
        label = "producer ordinal"
    elif port_spec.ordering is PortOrdering.BY_KEY:
        key_name = port_spec.order_key or ""
        raw = [entry[1].order_keys.get(key_name) for entry in entries]
        if any(value is None for value in raw):
            return (), ResolutionProblem(
                kind=ResolutionProblemKind.SET_PORT_ORDER_INCOMPLETE,
                detail=(
                    f"set port {port_spec.port_key!r} orders by key {key_name!r} but "
                    f"{sum(1 for value in raw if value is None)} candidate(s) do not carry it"
                ),
                input_port=port_spec.port_key,
                requirement_ids=requirement_ids,
            )
        keys = list(raw)
        label = f"order key {key_name!r}"
    else:
        declared = policy.explicit_order(port_spec.port_key)
        if declared is None:
            return (), ResolutionProblem(
                kind=ResolutionProblemKind.SET_PORT_UNORDERED,
                detail=(
                    f"set port {port_spec.port_key!r} declares an EXPLICIT order but no order "
                    "was supplied"
                ),
                input_port=port_spec.port_key,
                requirement_ids=requirement_ids,
            )
        if sorted(declared) != sorted(requirement_ids) or len(set(declared)) != len(declared):
            return (), ResolutionProblem(
                kind=ResolutionProblemKind.SET_PORT_ORDER_INCOMPLETE,
                detail=(
                    f"the EXPLICIT order of {port_spec.port_key!r} is {list(declared)} but the "
                    f"bindings are {sorted(requirement_ids)}; every binding needs exactly one "
                    "position"
                ),
                input_port=port_spec.port_key,
                requirement_ids=requirement_ids,
            )
        keys = [declared.index(entry[0].requirement_id) for entry in entries]
        label = "explicit order"
    if len(set(keys)) != len(keys):
        return (), ResolutionProblem(
            kind=ResolutionProblemKind.SET_PORT_ORDER_INCOMPLETE,
            detail=(
                f"set port {port_spec.port_key!r} orders by {label} and two bindings share one "
                "position, so the order is not determined"
            ),
            input_port=port_spec.port_key,
            requirement_ids=requirement_ids,
        )
    ranking = {key: rank for rank, key in enumerate(sorted(keys))}
    return tuple(ranking[key] for key in keys), None


def resolve_declared_inputs(
    consumer: TaskSemanticBindingV1,
    requirements: Sequence[DataRequirement],
    accepted: AcceptedOutputsIndex,
    *,
    witnesses: Mapping[str, ValidityWitness],
    policy: ResolutionPolicy,
    consumer_occurrence: OccurrenceId,
) -> ResolutionResult:
    """Resolve the declared DATA contract of one consumer into an InputManifest.

    ``consumer_occurrence`` is required, not optional.  ``consumer`` is identified
    by TaskRef while a :class:`~..contracts.htn.DataRequirement` names an
    occurrence, and without the occurrence there is nothing to compare a
    requirement's ``consumer_occurrence`` against — a requirement aimed at a
    different consumer would be absorbed silently as one of this task's inputs.

    Only the declared requirements are consulted.  An ORDER-only predecessor has
    none, so it contributes nothing however many artifacts it accepted (TG §10.1,
    §10.3); the all-ancestors sweep that used to live in :mod:`.versioning` is gone.
    """

    ports = {spec.port_key: spec for spec in consumer.input_ports}
    problems: list[ResolutionProblem] = []
    pending: list[SymbolicBinding] = []
    # input_port -> (requirement, candidate, witness_id)
    per_port: dict[str, list[tuple[DataRequirement, AcceptedOutput, str | None]]]
    per_port = {}

    # TG §5.5: a MAP port takes each input under the key its requirement declares;
    # two requirements declaring one key is a refusal, never a pick.
    declared_keys: dict[tuple[str, str], list[str]] = {}
    for requirement in requirements:
        spec = ports.get(requirement.input_port)
        if (
            requirement.consumer_occurrence == consumer_occurrence
            and spec is not None
            and spec.cardinality is PortCardinality.MAP
            and requirement.map_key is not None
        ):
            declared_keys.setdefault((requirement.input_port, requirement.map_key), []).append(
                requirement.requirement_id
            )
    duplicate_keys = {key: sorted(ids) for key, ids in declared_keys.items() if len(ids) > 1}
    for (input_port, map_key), requirement_ids in sorted(duplicate_keys.items()):
        problems.append(
            ResolutionProblem(
                kind=ResolutionProblemKind.MAP_KEY_DUPLICATE,
                detail=(
                    f"map port {input_port!r} has {len(requirement_ids)} inputs declared under "
                    f"key {map_key!r}"
                ),
                input_port=input_port,
                requirement_ids=tuple(requirement_ids),
            )
        )

    for requirement in sorted(requirements, key=lambda item: item.requirement_id):
        if requirement.consumer_occurrence != consumer_occurrence:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.FOREIGN_REQUIREMENT,
                    detail=(
                        f"requirement {requirement.requirement_id!r} feeds "
                        f"{requirement.consumer_occurrence}, not {consumer_occurrence}"
                    ),
                    input_port=requirement.input_port,
                    requirement_ids=(requirement.requirement_id,),
                )
            )
            continue
        port_spec = ports.get(requirement.input_port)
        if port_spec is None:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.UNDECLARED_PORT,
                    detail=(
                        f"requirement {requirement.requirement_id!r} targets input port "
                        f"{requirement.input_port!r}, which {consumer.task_id} does not declare"
                    ),
                    input_port=requirement.input_port,
                    requirement_ids=(requirement.requirement_id,),
                )
            )
            continue
        key_problem = _check_map_key(requirement, port_spec)
        if key_problem is not None:
            problems.append(key_problem)
            continue
        if (requirement.input_port, requirement.map_key or "") in duplicate_keys:
            continue  # already refused above, once per key
        if not accepted.is_complete(requirement.producer_occurrence):
            pending.append(
                SymbolicBinding(
                    requirement_id=requirement.requirement_id,
                    producer_occurrence=requirement.producer_occurrence,
                    output_port=requirement.output_port,
                    consumer_task_ref=consumer.task_id,
                    input_port=requirement.input_port,
                )
            )
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.PENDING_PRODUCER,
                    detail=(
                        f"{requirement.producer_occurrence} has not finished; requirement "
                        f"{requirement.requirement_id!r} stays symbolic and the manifest cannot "
                        "be frozen"
                    ),
                    input_port=requirement.input_port,
                    requirement_ids=(requirement.requirement_id,),
                )
            )
            continue
        candidates = accepted.at(requirement.producer_occurrence, requirement.output_port)
        narrowed, _target, revision_problem = _select_by_revision(
            requirement, candidates, accepted, policy
        )
        if revision_problem is not None:
            problems.append(revision_problem)
            continue
        for candidate in narrowed:
            if candidate.disclosure is not DisclosureState.DISCLOSABLE:
                problems.append(
                    ResolutionProblem(
                        kind=ResolutionProblemKind.NOT_DISCLOSABLE,
                        detail=(
                            f"{candidate.artifact_id} is {candidate.disclosure!s}; an earlier "
                            "binding does not carry a permission forward"
                        ),
                        input_port=requirement.input_port,
                        requirement_ids=(requirement.requirement_id,),
                        candidates=(candidate.artifact_id,),
                    )
                )
                continue
            schema_problem = _check_schema(requirement, port_spec, candidate, policy)
            if schema_problem is not None:
                problems.append(schema_problem)
                continue
            if candidate.provisional and requirement.input_port not in policy.provisional_ports:
                problems.append(
                    ResolutionProblem(
                        kind=ResolutionProblemKind.PROVISIONAL_NOT_AUTHORIZED,
                        detail=(
                            f"{candidate.artifact_id} is provisional and port "
                            f"{requirement.input_port!r} is not in the authorised speculation scope"
                        ),
                        input_port=requirement.input_port,
                        requirement_ids=(requirement.requirement_id,),
                        candidates=(candidate.artifact_id,),
                    )
                )
                continue
            witness_id, witness_problem = _check_witness(
                requirement, candidate, consumer.task_id, witnesses, policy
            )
            if witness_problem is not None:
                problems.append(witness_problem)
                continue
            per_port.setdefault(requirement.input_port, []).append(
                (requirement, candidate, witness_id)
            )

    bindings: list[ResolvedInputBinding] = []
    for port_key in sorted(per_port):
        port_spec = ports[port_key]
        entries = per_port[port_key]
        if port_spec.cardinality is PortCardinality.SINGLE and len(entries) > 1:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.AMBIGUOUS_SINGLE_PORT,
                    detail=(
                        f"single-valued port {port_key!r} has {len(entries)} live candidates; "
                        "choose one explicitly or introduce a synthesis task"
                    ),
                    input_port=port_key,
                    requirement_ids=tuple(sorted({entry[0].requirement_id for entry in entries})),
                    candidates=tuple(sorted(entry[1].artifact_id for entry in entries)),
                )
            )
            continue
        if port_spec.cardinality in ORDERED_CARDINALITIES:
            ordinals, order_problem = _order_entries(port_spec, entries, policy)
            if order_problem is not None:
                problems.append(order_problem)
                continue
        elif port_spec.cardinality is PortCardinality.MAP:
            ordinals, key_problem = _order_by_map_key(port_spec, entries)
            if key_problem is not None:
                problems.append(key_problem)
                continue
        else:
            ordinals = (0,) * len(entries)
        for ordinal, (requirement, candidate, witness_id) in zip(ordinals, entries, strict=True):
            bindings.append(
                ResolvedInputBinding(
                    binding_id=_binding_id(requirement, candidate, str(consumer.task_id)),
                    bound=BoundInput(
                        requirement_id=requirement.requirement_id,
                        producer_result_id=candidate.producer_result_id,
                        acceptance_id=candidate.acceptance_id,
                        artifact_id=candidate.artifact_id,
                        content_hash=candidate.content_hash,
                        schema_ref=requirement.schema_ref,
                        source_revision=candidate.source_revision,
                    ),
                    producer_task_ref=candidate.producer_task_ref,
                    producer_occurrence=candidate.producer_occurrence,
                    output_port=candidate.output_port,
                    support_revision=candidate.support_revision,
                    consumer_task_ref=consumer.task_id,
                    input_port=requirement.input_port,
                    port_ordinal=ordinal,
                    source_identity=candidate.source_identity,
                    produced_schema_ref=candidate.schema_ref,
                    read_policy=requirement.assurance_policy_ref,
                    freshness_policy=requirement.freshness_policy_ref,
                    disclosure_scope=candidate.disclosure_scope,
                    map_key=requirement.map_key,
                    # Every binding follows the authorised revision, so each one is an
                    # input this consumer has not been accepted against yet.
                    requires_reacceptance=True,
                    provisional=candidate.provisional,
                    witness_id=witness_id,
                )
            )

    bound_ports = {item.input_port for item in bindings}
    pending_ports = {item.input_port for item in pending}
    failed_ports = {problem.input_port for problem in problems}
    for spec in consumer.input_ports:
        if not spec.required or spec.port_key in bound_ports or spec.port_key in pending_ports:
            continue
        if spec.port_key in failed_ports:
            continue  # already explained by a more specific refusal
        problems.append(
            ResolutionProblem(
                kind=ResolutionProblemKind.UNBOUND_REQUIRED_PORT,
                detail=(
                    f"required input port {spec.port_key!r} of {consumer.task_id} has no "
                    "accepted binding"
                ),
                input_port=spec.port_key,
            )
        )

    ordered_problems = tuple(
        sorted(problems, key=lambda item: (str(item.kind), item.input_port or "", item.detail))
    )
    blocking = [
        problem
        for problem in ordered_problems
        if problem.kind is not ResolutionProblemKind.PENDING_PRODUCER
    ]
    if blocking:
        return ResolutionResult(manifest=None, problems=ordered_problems)
    manifest = InputManifest(
        consumer_task_ref=consumer.task_id,
        bindings=tuple(bindings),
        pending=tuple(pending),
    )
    return ResolutionResult(manifest=manifest, problems=ordered_problems)


# --------------------------------------------------------------------------------------
# resolve_deliveries (冻结清单里的接力文件)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveredFile:
    """One file a DATA-bound producer's acceptance covers, as the dispatch read it.

    ``carried_from`` is set when the file reached the consumer through a continuation
    producer's own frozen manifest (one level: that manifest already holds what *it*
    received); ``producer_task_ref`` / ``acceptance_id`` are always the real writer's.
    """

    producer_task_ref: TaskRef
    acceptance_id: str
    support_revision: int
    artifact_id: str
    path: str
    content_hash: str
    carried_from: TaskRef | None = None

    @property
    def version_key(self) -> tuple[str, str, str]:
        return (str(self.producer_task_ref), self.path, self.content_hash)


@dataclass(frozen=True, slots=True)
class DeliverySources:
    """Everything the delivery resolution reads, gathered by the dispatch in one pass.

    ``files`` are the candidates (direct and carried); ``received`` maps an acceptance
    id to the ``(producer task, path, hash)`` set that acceptance's Attempt was frozen
    with — the only proof the system accepts that one version is the next of another
    (TaskGraph §2 第 5 条: no "first come", no "later in the order wins").
    ``unavailable`` names acceptances whose files could not be read, so the refusal
    says which source and why instead of delivering less in silence.
    """

    files: tuple[DeliveredFile, ...] = ()
    received: Mapping[str, frozenset[tuple[str, str, str]]] = field(default_factory=dict)
    unavailable: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(
            self, "received", {key: frozenset(value) for key, value in dict(self.received).items()}
        )
        object.__setattr__(self, "unavailable", tuple(self.unavailable))


def resolve_deliveries(
    manifest: InputManifest,
    sources: DeliverySources,
    *,
    target_rules: TargetRules,
    witnesses: Mapping[str, ValidityWitness],
    policy: ResolutionPolicy,
) -> ResolutionResult:
    """Add the delivery bindings to a frozen port manifest, or say why not.

    This is the *only* algorithm that decides which non-port files a consumer starts
    from and which version of each: dispatch, the handover re-check, recovery, sharing
    and the write-conflict scan all read the manifest this produces.

    Rules, all order and none judgment:

    * a path the plan binds as a port document is that document; another content at
      the same place is a version conflict unless the port producer's acceptance was
      frozen with it (it received that version and changed it on purpose);
    * elsewhere, of several contents at one path only the one whose producer's
      acceptance was frozen with every other is taken; otherwise a conflict;
    * every delivered file carries the consumer's licence over the real producer's
      acceptance — the same five checks a port binding passes; a file whose licence
      is missing or not usable is refused by name, not dropped.

    Identical content at one path is one file, however many producers agree on it.
    """

    if not manifest.is_frozen:
        raise ManifestNotFrozen("deliveries are resolved onto a frozen port manifest")
    consumer = str(manifest.consumer_task_ref)
    problems: list[ResolutionProblem] = []
    for acceptance_id, detail in sources.unavailable:
        problems.append(
            ResolutionProblem(
                kind=ResolutionProblemKind.DELIVERY_SOURCE_UNAVAILABLE,
                detail=f"the files accepted under {acceptance_id} cannot be read: {detail}",
            )
        )
    port_plan = materialise_plan(
        InputManifest(consumer_task_ref=manifest.consumer_task_ref, bindings=manifest.bindings),
        target_rules,
    )
    if port_plan.problems:
        return ResolutionResult(
            manifest=None,
            problems=tuple(
                sorted(
                    (*problems, *port_plan.problems), key=lambda item: (str(item.kind), item.detail)
                )
            ),
        )
    bindings_by_id = {binding.binding_id: binding for binding in manifest.bindings}
    ports: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    for entry in port_plan.entries:
        members = [bindings_by_id[item] for item in entry.binding_ids if item in bindings_by_id]
        ports[entry.target.path] = (
            entry.content_hash,
            tuple(sorted({binding.acceptance_id for binding in members})),
            tuple(sorted({binding.artifact_id for binding in members})),
        )

    def received_by(acceptance_ids: Sequence[str]) -> frozenset[tuple[str, str, str]]:
        found: set[tuple[str, str, str]] = set()
        for acceptance_id in acceptance_ids:
            found |= sources.received.get(acceptance_id, frozenset())
        return frozenset(found)

    # direct files before carried ones, then by producer and artifact: the first file
    # of a content is the one recorded, so the result never depends on read order
    ordered = sorted(
        sources.files,
        key=lambda item: (
            item.carried_from is not None,
            str(item.producer_task_ref),
            item.artifact_id,
            item.acceptance_id,
        ),
    )
    by_path: dict[str, dict[str, DeliveredFile]] = {}
    for item in ordered:
        by_path.setdefault(item.path, {}).setdefault(item.content_hash, item)

    deliveries: list[DeliveryBinding] = []
    for path in sorted(by_path):
        versions = by_path[path]
        port = ports.get(path)
        if port is not None:
            port_hash, port_acceptances, port_artifacts = port
            known = received_by(port_acceptances)
            rivals = [
                item
                for item in versions.values()
                if item.content_hash != port_hash and item.version_key not in known
            ]
            if rivals:
                problems.append(
                    ResolutionProblem(
                        kind=ResolutionProblemKind.DELIVERY_VERSION_CONFLICT,
                        detail=(
                            f"the plan binds {path!r} as a port document and "
                            f"{len(rivals)} other content(s) reach the same place; nothing "
                            "proves which version this step takes"
                        ),
                        candidates=(*port_artifacts, *(item.artifact_id for item in rivals)),
                        producers=(
                            *(
                                str(bindings_by_id[b].producer_task_ref)
                                for b in sorted(bindings_by_id)
                                if bindings_by_id[b].artifact_id in port_artifacts
                            ),
                            *(str(item.producer_task_ref) for item in rivals),
                        ),
                    )
                )
            continue  # the port document is the file at this place
        chosen: DeliveredFile | None
        if len(versions) == 1:
            chosen = next(iter(versions.values()))
        else:
            chosen = None
            for candidate in versions.values():
                others = [item.version_key for item in versions.values() if item is not candidate]
                if all(
                    key in sources.received.get(candidate.acceptance_id, frozenset())
                    for key in others
                ):
                    chosen = candidate
                    break
            if chosen is None:
                items = list(versions.values())
                problems.append(
                    ResolutionProblem(
                        kind=ResolutionProblemKind.DELIVERY_VERSION_CONFLICT,
                        detail=(
                            f"{len(items)} different contents of {path!r} reach this step and no "
                            "producer's frozen inputs prove one is the next version of the others"
                        ),
                        candidates=tuple(item.artifact_id for item in items),
                        producers=tuple(str(item.producer_task_ref) for item in items),
                    )
                )
                continue
        verdict = witness_verdict(
            witnesses.get(chosen.acceptance_id),
            acceptance_id=chosen.acceptance_id,
            artifact_id=chosen.artifact_id,
            support_revision=chosen.support_revision,
            consumer_task_ref=manifest.consumer_task_ref,
            policy=policy,
        )
        witness_id: str | None = None
        if verdict is not None:
            witness_id, problem = verdict
            if problem is not None:
                kind, detail = problem
                problems.append(
                    ResolutionProblem(
                        kind=(
                            ResolutionProblemKind.DELIVERY_WITNESS_MISSING
                            if kind is ResolutionProblemKind.WITNESS_MISSING
                            else ResolutionProblemKind.DELIVERY_WITNESS_NOT_USABLE
                        ),
                        detail=(
                            f"delivered file {path!r} (artifact {chosen.artifact_id}, accepted "
                            f"under {chosen.acceptance_id} of {chosen.producer_task_ref!s}"
                            + (
                                f", relayed by {chosen.carried_from!s}"
                                if chosen.carried_from
                                else ""
                            )
                            + f"): {detail}"
                        ),
                        candidates=(chosen.artifact_id,),
                        producers=(str(chosen.producer_task_ref),),
                    )
                )
                continue
        deliveries.append(
            DeliveryBinding(
                binding_id=delivery_binding_id(consumer, chosen.artifact_id, chosen.acceptance_id),
                producer_task_ref=chosen.producer_task_ref,
                acceptance_id=chosen.acceptance_id,
                support_revision=int(chosen.support_revision),
                artifact_id=chosen.artifact_id,
                path=chosen.path,
                content_hash=chosen.content_hash,
                witness_id=witness_id,
                carried_from=chosen.carried_from,
            )
        )
    ordered_problems = tuple(sorted(problems, key=lambda item: (str(item.kind), item.detail)))
    if ordered_problems:
        return ResolutionResult(manifest=None, problems=ordered_problems)
    return ResolutionResult(
        manifest=InputManifest(
            consumer_task_ref=manifest.consumer_task_ref,
            bindings=manifest.bindings,
            pending=manifest.pending,
            deliveries=tuple(deliveries),
        )
    )


# --------------------------------------------------------------------------------------
# materialise_plan
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TargetRules:
    """How a binding's source identity becomes a place in the consumer's namespace.

    ``preserve_source_namespace`` is the switch TG §10.2 asks for: keep the two
    attempts apart instead of letting one relative name collapse them.
    ``case_insensitive`` models a platform whose file system folds case, so a
    collision that only a mac or a Windows host would see is reported on every
    host rather than discovered in production.
    """

    namespace: str
    port_prefixes: Mapping[str, str] = field(default_factory=dict)
    preserve_source_namespace: bool = False
    case_insensitive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ContractError("target_rules.namespace must be a non-blank string")
        object.__setattr__(self, "port_prefixes", dict(self.port_prefixes))

    def target_for(self, binding: ResolvedInputBinding) -> ResourceIdentity:
        parts: list[str] = []
        prefix = self.port_prefixes.get(binding.input_port)
        if prefix:
            parts.append(prefix)
        if self.preserve_source_namespace:
            parts.append(binding.source_identity.namespace)
        parts.append(binding.source_identity.path)
        return ResourceIdentity(namespace=self.namespace, path="/".join(parts))


@dataclass(frozen=True, slots=True)
class MaterialisationEntry:
    """One place, one content hash, and every binding that asked for it."""

    target: ResourceIdentity
    content_hash: str
    artifact_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    input_ports: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target.to_json(),
            "content_hash": self.content_hash,
            "artifact_ids": list(self.artifact_ids),
            "binding_ids": list(self.binding_ids),
            "input_ports": list(self.input_ports),
        }


@dataclass(frozen=True, slots=True)
class MaterialisationPlan:
    """Where each input *should* go.  Nothing is written by producing one."""

    consumer_task_ref: TaskRef
    entries: tuple[MaterialisationEntry, ...] = ()
    problems: tuple[ResolutionProblem, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def plan_hash(self) -> str:
        return sha256_hex(
            {
                "consumer_task_ref": str(self.consumer_task_ref),
                "entries": [entry.to_json() for entry in self.entries],
            }
        )


def materialise_plan(manifest: InputManifest, target_rules: TargetRules) -> MaterialisationPlan:
    """Map an immutable manifest onto target places, refusing to merge a collision.

    Two bindings whose *content hashes differ* aimed at one target place is a
    ``TARGET_PATH_CONFLICT``: TG §10.2 requires an explicit selection, conversion
    or synthesis that produces a new artifact and is verified again.  The same
    hash at one place is not a conflict — it is one file two bindings agree on.
    """

    if not manifest.is_frozen:
        raise ManifestNotFrozen(
            "a materialisation plan needs every input resolved to an exact identity"
        )
    problems: list[ResolutionProblem] = []
    # (target, content_hash, artifact_id, binding_id, input_port, requirement_id, producer);
    # a delivery binding has no port and no requirement.
    Member = tuple[ResourceIdentity, str, str, str, str | None, str | None, str]
    grouped: dict[tuple[str, str], list[Member]] = {}
    for binding in manifest.bindings:
        target = target_rules.target_for(binding)
        if target.is_escaping:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.TARGET_PATH_INVALID,
                    detail=(
                        f"binding {binding.binding_id} would be placed at {target.path!r}, "
                        "which leaves the consumer namespace"
                    ),
                    input_port=binding.input_port,
                    requirement_ids=(binding.requirement_id,),
                    candidates=(binding.artifact_id,),
                )
            )
            continue
        key = target.collation_key(case_insensitive=target_rules.case_insensitive)
        grouped.setdefault(key, []).append(
            (
                target,
                binding.content_hash,
                binding.artifact_id,
                binding.binding_id,
                binding.input_port,
                binding.requirement_id,
                str(binding.producer_task_ref),
            )
        )
    for delivery in manifest.deliveries:
        target = ResourceIdentity(namespace=target_rules.namespace, path=delivery.path)
        if target.is_escaping:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.TARGET_PATH_INVALID,
                    detail=(
                        f"delivery {delivery.binding_id} would be placed at {target.path!r}, "
                        "which leaves the consumer namespace"
                    ),
                    candidates=(delivery.artifact_id,),
                    producers=(str(delivery.producer_task_ref),),
                )
            )
            continue
        key = target.collation_key(case_insensitive=target_rules.case_insensitive)
        grouped.setdefault(key, []).append(
            (
                target,
                delivery.content_hash,
                delivery.artifact_id,
                delivery.binding_id,
                None,
                None,
                str(delivery.producer_task_ref),
            )
        )

    entries: list[MaterialisationEntry] = []
    for key in sorted(grouped):
        members = grouped[key]
        hashes = {member[1] for member in members}
        if len(hashes) > 1:
            problems.append(
                ResolutionProblem(
                    kind=ResolutionProblemKind.TARGET_PATH_CONFLICT,
                    detail=(
                        f"{len(hashes)} different contents would occupy {key[1]!r} in "
                        f"{target_rules.namespace!r}; choose, convert or synthesise one new "
                        "artifact and verify it again"
                    ),
                    input_ports=tuple(sorted({m[4] for m in members if m[4] is not None})),
                    requirement_ids=tuple(sorted({m[5] for m in members if m[5] is not None})),
                    candidates=tuple(m[2] for m in sorted(members, key=lambda m: m[2])),
                    producers=tuple(m[6] for m in sorted(members, key=lambda m: m[2])),
                )
            )
            continue
        # F5 / TG §10.2: under a case-folding platform rule the *canonical* spelling
        # is the folded one, not whichever binding happened to be visited first.
        # Picking arbitrarily would make the plan depend on iteration order.
        target = (
            ResourceIdentity(namespace=members[0][0].namespace, path=members[0][0].path.casefold())
            if target_rules.case_insensitive
            else members[0][0]
        )
        entries.append(
            MaterialisationEntry(
                target=target,
                content_hash=members[0][1],
                artifact_ids=tuple(sorted({m[2] for m in members})),
                binding_ids=tuple(sorted(m[3] for m in members)),
                input_ports=tuple(sorted({m[4] for m in members if m[4] is not None})),
            )
        )
    return MaterialisationPlan(
        consumer_task_ref=manifest.consumer_task_ref,
        entries=tuple(entries),
        problems=tuple(sorted(problems, key=lambda item: (str(item.kind), item.detail))),
    )


# --------------------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Explanation:
    """A refusal a person can act on: what happened, and what would fix it."""

    kind: ResolutionProblemKind
    summary: str
    remedy: str
    input_port: str | None = None
    requirement_ids: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    input_ports: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "summary": self.summary,
            "remedy": self.remedy,
            "input_port": self.input_port,
            "input_ports": list(self.input_ports),
            "requirement_ids": list(self.requirement_ids),
            "candidates": list(self.candidates),
        }


#: The remedy is deliberately a *decision to make*, never a default to apply: no
#: entry here tells the caller to prefer a producer by position or by recency.
_REMEDIES: Mapping[ResolutionProblemKind, str] = {
    ResolutionProblemKind.AMBIGUOUS_SINGLE_PORT: (
        "Name one candidate in the plan, or add a synthesis task that combines them and is "
        "accepted on its own."
    ),
    ResolutionProblemKind.SET_PORT_UNORDERED: (
        "Declare the port's ordering (by producer ordinal, by key, or an explicit sequence)."
    ),
    ResolutionProblemKind.SET_PORT_ORDER_INCOMPLETE: (
        "Give every binding of the port exactly one position under the declared ordering."
    ),
    ResolutionProblemKind.UNDECLARED_PORT: (
        "Declare the input port on the consumer's semantic binding, or retarget the requirement."
    ),
    ResolutionProblemKind.FOREIGN_REQUIREMENT: (
        "Resolve this requirement against the consumer it actually names."
    ),
    ResolutionProblemKind.UNBOUND_REQUIRED_PORT: (
        "Add a data requirement for the port, mark the port optional, or plan a producer for it."
    ),
    ResolutionProblemKind.SCHEMA_MISMATCH: (
        "Register an explicit compatibility declaration, insert a conversion task, or align "
        "the schemas."
    ),
    ResolutionProblemKind.CONVERTER_NOT_DEPLOYED: (
        "Register a declaration that needs no conversion, plan a conversion task whose output "
        "is accepted on its own, or align the schemas."
    ),
    ResolutionProblemKind.NOT_DISCLOSABLE: (
        "Obtain a current permission for the source, or plan the work around a source that may "
        "be read."
    ),
    ResolutionProblemKind.WITNESS_MISSING: (
        "Produce a validity witness for the acceptance before the input is bound."
    ),
    ResolutionProblemKind.WITNESS_NOT_USABLE: (
        "Re-evaluate the witness against the current scope epoch and deadline."
    ),
    ResolutionProblemKind.WITNESS_PURPOSE_MISMATCH: (
        "Obtain a witness issued for this use, or widen the accepted purposes deliberately."
    ),
    ResolutionProblemKind.WITNESS_CONSUMER_MISMATCH: (
        "Obtain a witness issued to this consumer; another task's permission is not this one's."
    ),
    ResolutionProblemKind.WITNESS_SUPPORT_MISMATCH: (
        "Re-take the witness over the support revision the artifact actually carries."
    ),
    ResolutionProblemKind.WITNESS_EPOCH_UNKNOWN: (
        "Supply the current epoch of the witness's scope, or set allow_unknown_scope to accept "
        "an uncompared witness on purpose."
    ),
    ResolutionProblemKind.PENDING_PRODUCER: (
        "Wait for the producer to be accepted, then resolve again; the symbolic binding is kept."
    ),
    ResolutionProblemKind.PROVISIONAL_NOT_AUTHORIZED: (
        "Authorise speculation on this port explicitly, or wait for a firm accepted output."
    ),
    ResolutionProblemKind.REVISION_NOT_AVAILABLE: (
        "Re-pin the input to a revision that is still accepted, or restore the named revision."
    ),
    ResolutionProblemKind.TARGET_PATH_CONFLICT: (
        "Choose, convert or synthesise one artifact for the place and verify it again, or keep "
        "the sources in separate namespaces."
    ),
    ResolutionProblemKind.TARGET_PATH_INVALID: (
        "Give the binding a place inside the consumer namespace."
    ),
    ResolutionProblemKind.MAP_KEY_MISSING: (
        "Declare the key this input is bound under on its data requirement."
    ),
    ResolutionProblemKind.MAP_KEY_DUPLICATE: (
        "Give each input of the map port its own key, or combine the inputs in a synthesis "
        "task that is accepted on its own."
    ),
    ResolutionProblemKind.MAP_KEY_MISPLACED: (
        "Remove the key, or declare the consumer port as a map."
    ),
    ResolutionProblemKind.DELIVERY_WITNESS_MISSING: (
        "wait for the next reading: the consumer's START witness over the real producer's "
        "acceptance is issued when the acceptance is read; a file is never delivered unlicensed"
    ),
    ResolutionProblemKind.DELIVERY_WITNESS_NOT_USABLE: (
        "the licence over that acceptance is blocked or stale (revoked acceptance, moved scope "
        "epoch); the producer's acceptance must be current again or the plan must stop reading it"
    ),
    ResolutionProblemKind.DELIVERY_SOURCE_UNAVAILABLE: (
        "the acceptance, its artifacts or its frozen inputs cannot be read as recorded; this is "
        "a library fault to repair, not a reason to deliver less"
    ),
    ResolutionProblemKind.DELIVERY_VERSION_CONFLICT: (
        "the Planner must say which step takes which version (reorder, re-wire the edge, or "
        "redo one producer); the system does not pick by recency or by order"
    ),
}

_SUMMARIES: Mapping[ResolutionProblemKind, str] = {
    ResolutionProblemKind.AMBIGUOUS_SINGLE_PORT: "a single-valued port has more than one candidate",
    ResolutionProblemKind.SET_PORT_UNORDERED: "a set-valued port has no declared ordering",
    ResolutionProblemKind.SET_PORT_ORDER_INCOMPLETE: (
        "the declared ordering does not rank every binding"
    ),
    ResolutionProblemKind.UNDECLARED_PORT: (
        "a requirement targets a port the consumer does not declare"
    ),
    ResolutionProblemKind.FOREIGN_REQUIREMENT: "a requirement belongs to another consumer",
    ResolutionProblemKind.UNBOUND_REQUIRED_PORT: "a required input port has no binding",
    ResolutionProblemKind.SCHEMA_MISMATCH: "the produced schema is not the required schema",
    ResolutionProblemKind.CONVERTER_NOT_DEPLOYED: (
        "the declared compatibility needs a converter that is not deployed"
    ),
    ResolutionProblemKind.NOT_DISCLOSABLE: "the source may not be read right now",
    ResolutionProblemKind.WITNESS_MISSING: "no validity witness covers the acceptance",
    ResolutionProblemKind.WITNESS_NOT_USABLE: "the validity witness does not permit this use",
    ResolutionProblemKind.WITNESS_PURPOSE_MISMATCH: (
        "the witness was issued for a different purpose"
    ),
    ResolutionProblemKind.WITNESS_CONSUMER_MISMATCH: (
        "the witness was issued to a different consumer"
    ),
    ResolutionProblemKind.WITNESS_SUPPORT_MISMATCH: (
        "the witness covers a different support revision"
    ),
    ResolutionProblemKind.WITNESS_EPOCH_UNKNOWN: (
        "the current epoch of the witness's scope is unknown"
    ),
    ResolutionProblemKind.PENDING_PRODUCER: "the producer has not finished",
    ResolutionProblemKind.PROVISIONAL_NOT_AUTHORIZED: "speculation is not authorised on this port",
    ResolutionProblemKind.REVISION_NOT_AVAILABLE: "the named source revision is not accepted",
    ResolutionProblemKind.TARGET_PATH_CONFLICT: "two different contents claim one place",
    ResolutionProblemKind.TARGET_PATH_INVALID: "the computed place is outside the namespace",
    ResolutionProblemKind.MAP_KEY_MISSING: "an input of a map port declares no key",
    ResolutionProblemKind.MAP_KEY_DUPLICATE: "two inputs of a map port share one key",
    ResolutionProblemKind.MAP_KEY_MISPLACED: "a key is declared for a port that is not a map",
    ResolutionProblemKind.DELIVERY_WITNESS_MISSING: "a delivered file has no licence yet",
    ResolutionProblemKind.DELIVERY_WITNESS_NOT_USABLE: "a delivered file's licence is not usable",
    ResolutionProblemKind.DELIVERY_SOURCE_UNAVAILABLE: "a producer's accepted files cannot be read",
    ResolutionProblemKind.DELIVERY_VERSION_CONFLICT: "several versions of one delivered path, none proven latest",
}


def explain(problems: Sequence[ResolutionProblem]) -> tuple[Explanation, ...]:
    """One structured reason per problem, in the order they were reported."""

    return tuple(
        Explanation(
            kind=problem.kind,
            summary=f"{_SUMMARIES[problem.kind]}: {problem.detail}",
            remedy=_REMEDIES[problem.kind],
            input_port=problem.input_port,
            input_ports=problem.input_ports,
            requirement_ids=problem.requirement_ids,
            candidates=problem.candidates,
        )
        for problem in problems
    )


__all__ = (
    "AcceptedOutput",
    "AcceptedOutputsIndex",
    "DeliveredFile",
    "DeliveryBinding",
    "DeliverySources",
    "delivery_binding_id",
    "resolve_deliveries",
    "witness_verdict",
    "DisclosureState",
    "Explanation",
    "ExplicitPortOrder",
    "InputManifest",
    "ManifestNotFrozen",
    "MaterialisationEntry",
    "MaterialisationPlan",
    "ResolutionPolicy",
    "ResolutionProblem",
    "ResolutionProblemKind",
    "ResolutionResult",
    "ResolvedInputBinding",
    "ResourceIdentity",
    "SchemaCompatibilityRegistry",
    "SchemaCompatibilityRule",
    "SymbolicBinding",
    "TargetRules",
    "explain",
    "materialise_plan",
    "resolve_declared_inputs",
)
