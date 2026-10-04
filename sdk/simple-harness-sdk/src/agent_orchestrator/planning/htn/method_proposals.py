# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""A method the Planner proposes: the system's side of it (§7.3 source 4, §18.5 C8).

§7.3 lists four sources a new method may come from, and the Planner writing one for
a goal that has no usable method is one of them.  It is not a special source: it goes
through the *same* six-step admission protocol as a human-written one, and it stops at
``TRIAL_ADMITTED`` like everything else.

This module is the half of that which is not a model call:

:func:`build_context`
    the **typed context** the Planner is given for one open goal — the goal signature
    it must satisfy, the criteria it has to cover, the operators this deployment
    actually registers (with whether their capability is healthy), the applicability
    reports of the methods that exist and do not fit, and the list of fields the model
    may not write.  A context assembled from the catalogue cannot widen the model's
    options the way prose could.
:func:`admit_proposal`
    hand the decoded proposal to :meth:`~.registry.MethodRegistry.admit`.  Nothing is
    normalised on the way in: §7.3 and §6.3 put the registry status *outside* the
    definition, so a payload that spells ``ADMITTED`` is refused with
    ``MODEL_CLAIMED_STATUS`` and the refusal is recorded — quietly rewriting it to
    ``DRAFT`` would hide the attempt.

There is no promotion here, by construction: past ``TRIAL_ADMITTED`` lies the offline
multi-instance evaluation, and succeeding once is not a promotion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...contracts.htn import (
    GoalSignature,
    MethodRegistryStatus,
    MissionRef,
    RegistryAuthor,
    TaskForm,
    TaskSemanticBindingV1,
)
from ...contracts.models import ContractError
from ...contracts.semantic_base import VersionedRef

from .applicability import ApplicabilityReport, CapabilitySnapshot
from .registry import (
    AdmissionPolicy,
    AdmissionReceipt,
    AdmissionVerdict,
    MethodCandidate,
    MethodProposal,
    MethodRegistry,
    TaskTypeCatalog,
)

#: The statuses a proposed method may legitimately reach.  Anything else coming back
#: from the registry is a bug in the registry, not a stronger method, and
#: :func:`admit_proposal` refuses to return it.
TERMINAL_STATUSES: frozenset[MethodRegistryStatus] = frozenset(
    {MethodRegistryStatus.TRIAL_ADMITTED, MethodRegistryStatus.REJECTED}
)

#: §18.5 / §7.3: what a model may *never* write into a proposal, because each of
#: these is the system's own answer to "may this happen at all".  They are refused
#: at the boundary rather than overwritten, because silently replacing a claimed
#: ``registry_status`` with the real one would let a model probe the gate for free and
#: would leave no record that it tried.
SYSTEM_BOUND_FIELDS = frozenset(
    {
        "mission_id",
        "principal",
        "principal_id",
        "scope",
        "scope_id",
        "budget_account",
        "registry_status",
        "opened_by",
        "authorization_ref",
        "grant_ref",
        "provenance",
        "authored_by",
    }
)

#: Sub-trees of :meth:`MethodProposalContext.to_json` that hold *domain values* rather
#: than structural claims: a method parameter that happens to be called ``scope`` is a
#: value, and refusing it would make the contract depend on a domain's vocabulary.
VALUE_CONTAINERS: frozenset[str] = frozenset({"goal_parameters"})

#: Who a proposal arriving on this path is attributed to.  Not a parameter: the
#: definition reached here from a model, and presenting it as anything else would hand
#: a model-authored definition the registration rights of the registry service
#: (§6.3, §7.3).
PROPOSAL_AUTHOR = RegistryAuthor.MODEL

#: The maximum number of operator offers a context carries.  A context is a budget
#: as well as a description; a deployment with a thousand task types does not get a
#: thousand-entry prompt.
DEFAULT_MAX_OPERATORS = 64

#: How many steps a *proposed* method may declare.  The admission policy's own
#: ``max_steps`` defaults to 64, which is the human-authored bound; a model-written
#: ten-step method does not fit a Mission's attempt budget across a repair.  Eight is
#: the TaskGraph attachment's method-width cap.
MAX_PROPOSED_METHOD_STEPS = 8

#: The field names the codec requires, by object, as the context states them
#: to the model (``method_shape``).  They are spelled here rather than read off
#: ``MethodContract.from_json`` because that function keeps its list inline; the
#: suite pins the two against each other (drop any name → the codec names it).
METHOD_SHAPE: Mapping[str, tuple[str, ...]] = {
    "method": (
        "schema_version",
        "method_id",
        "method_version",
        "goal_type_ref",
        "parameter_schema_ref",
        "output_schema_ref",
        "applicable_when",
        "exploration_assumptions",
        "steps",
        "ordering",
        "required_capabilities",
        "expected_effects",
        "composition",
        "basis_refs",
    ),
    "step": (
        "local_id",
        "task_type_ref",
        "form",
        "arguments",
        "required_capabilities",
        "obligation_relation",
    ),
    "composition": (
        "criterion_links",
        "outputs",
        "finalizer_step",
        "independent_review_required",
    ),
    "criterion_link": (
        "parent_criterion_id",
        "child_step",
        "child_criterion_id",
        "evidence_requirement",
    ),
    "versioned_ref": ("id", "version", "content_hash"),
}


def rejection_problems(receipt: AdmissionReceipt) -> tuple[str, ...]:
    """The receipt's problems as the round records them: ``CODE: detail``, verbatim."""

    return tuple(f"{item.code!s}: {item.detail}" for item in receipt.problems)


def authority_claims(payload: object, *, path: str = "") -> tuple[str, ...]:
    """Every system-bound field name that appears as a *structural* key in ``payload``.

    Used by the suite and by :meth:`MethodProposalContext.__post_init__`: a
    context that carried ``registry_status`` or ``manager_epoch`` would be telling
    the model those are its to fill in, which is the bypass §18.5 closes.
    """

    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = str(key)
            if name in SYSTEM_BOUND_FIELDS:
                found.append(f"{path}{name}" if path else name)
            if name in VALUE_CONTAINERS:
                continue
            found.extend(authority_claims(value, path=f"{path}{name}."))
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            found.extend(authority_claims(item, path=f"{path}[{index}]."))
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True, slots=True)
class OperatorOffer:
    """One task type this deployment registers, as the Planner may see it.

    ``available`` is the deployment fact §7.3 step 2 turns into "clearly not
    executable": an operator whose capability is unhealthy is still *offered*, with
    its unavailability stated, because a method built on it is refusable for a
    reason the model can act on — rather than silently absent from a context, which
    reads as "no such operator exists".
    """

    task_type_id: str
    version: int
    content_hash: str
    form: str
    statement: str
    input_ports: tuple[str, ...] = ()
    output_ports: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    unavailable_capabilities: tuple[str, ...] = ()
    coverage_criteria: tuple[str, ...] = ()
    side_effect_kind: str = ""
    domain: str | None = None

    @property
    def available(self) -> bool:
        return not self.unavailable_capabilities

    def to_json(self) -> dict[str, Any]:
        return {
            "task_type_ref": {
                "id": self.task_type_id,
                "version": self.version,
                "content_hash": self.content_hash,
            },
            "form": self.form,
            "statement": self.statement,
            "input_ports": list(self.input_ports),
            "output_ports": list(self.output_ports),
            "required_capabilities": list(self.required_capabilities),
            "unavailable_capabilities": list(self.unavailable_capabilities),
            "coverage_criteria": list(self.coverage_criteria),
            "side_effect_kind": self.side_effect_kind,
            "domain": self.domain,
            "available": self.available,
        }


@dataclass(frozen=True, slots=True)
class ApplicabilityNote:
    """Why one already-registered method did not fit this goal.

    Carried into the request so the model is told what was already tried and how it
    failed.  The four axes stay apart for the same reason
    :class:`~.applicability.ApplicabilityReport` keeps them apart: a missing
    capability, a false precondition, a conflict and a type error call for four
    different candidates.
    """

    method_id: str
    method_version: int
    content_hash: str
    status: str
    truth: str
    unmet_capabilities: tuple[str, ...] = ()
    needs_evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    type_errors: tuple[str, ...] = ()

    @classmethod
    def of(cls, candidate: MethodCandidate, report: ApplicabilityReport) -> ApplicabilityNote:
        return cls(
            method_id=candidate.method_ref.method_id,
            method_version=int(candidate.method_ref.version),
            content_hash=candidate.method_ref.content_hash,
            status=str(report.status),
            truth=str(report.truth),
            unmet_capabilities=tuple(report.unmet_capabilities),
            needs_evidence=tuple(report.needs_evidence),
            conflicts=tuple(report.conflicts),
            type_errors=tuple(report.type_errors),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "method_ref": {
                "id": self.method_id,
                "version": self.method_version,
                "content_hash": self.content_hash,
            },
            "status": self.status,
            "truth": self.truth,
            "unmet_capabilities": list(self.unmet_capabilities),
            "needs_evidence": list(self.needs_evidence),
            "conflicts": list(self.conflicts),
            "type_errors": list(self.type_errors),
        }


@dataclass(frozen=True, slots=True)
class MethodProposalContext:
    """The typed context the Planner writes one goal's method from (§18.5 C8).

    It is data, not a prompt: :meth:`to_json` is what the planning package carries,
    and nothing in it is a field the system binds — :func:`authority_claims` is
    asserted empty at construction, so a context can never hand the model the
    vocabulary of its own authority.
    """

    goal_task_id: str
    obligation_id: str
    goal_signature: Mapping[str, Any]
    required_criteria: tuple[str, ...] = ()
    goal_parameters: Mapping[str, Any] = field(default_factory=dict)
    operators: tuple[OperatorOffer, ...] = ()
    rejected_methods: tuple[ApplicabilityNote, ...] = ()
    unavailable_capabilities: tuple[str, ...] = ()
    #: The goal type's own ``{id, version, content_hash}`` from the catalogue —
    #: ``method.goal_type_ref`` is copied from it.  ``None`` when the deployment does
    #: not declare the type (the empty-library case).
    goal_type_ref: Mapping[str, Any] | None = None
    #: Each criterion the method has to cover, with the sentence a step must make
    #: producible.  Carried beside ``required_criteria`` (ids only) so a criterion is
    #: visible as text, not just as an identifier.
    criterion_evidence: tuple[Mapping[str, str], ...] = ()
    #: What the model may not write, stated *in* the context.  §18.5 refuses such a
    #: field at the boundary rather than overwriting it, and a model that was never
    #: told cannot avoid it.
    forbidden_fields: tuple[str, ...] = ()
    # A collision-free suggestion, never an admission or a registry status.
    new_method_identity: tuple[str, int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "forbidden_fields",
            tuple(sorted(self.forbidden_fields or SYSTEM_BOUND_FIELDS)),
        )
        object.__setattr__(
            self,
            "criterion_evidence",
            tuple(
                {
                    "id": str(item.get("id", "")),
                    "evidence_requirement": str(item.get("evidence_requirement", "")),
                }
                for item in self.criterion_evidence
            ),
        )
        if self.goal_type_ref is not None:
            object.__setattr__(self, "goal_type_ref", dict(self.goal_type_ref))
        claims = authority_claims(self.to_json())
        if claims:
            raise ContractError(
                f"a method proposal context may not carry the system-bound fields {list(claims)}; "
                "a model proposes the shape of the work, never its authority (§18.5)"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "goal_task_ref": self.goal_task_id,
            "duty_ref": self.obligation_id,
            "goal_signature": dict(self.goal_signature),
            "required_criteria": list(self.required_criteria),
            "goal_parameters": dict(self.goal_parameters),
            "operators": [item.to_json() for item in self.operators],
            "rejected_methods": [item.to_json() for item in self.rejected_methods],
            "unavailable_capabilities": list(self.unavailable_capabilities),
            "goal_type_ref": None if self.goal_type_ref is None else dict(self.goal_type_ref),
            "method_shape": {key: list(value) for key, value in METHOD_SHAPE.items()},
            "criterion_evidence": [dict(item) for item in self.criterion_evidence],
            "forbidden_fields": list(self.forbidden_fields),
            **({"new_method_identity": {
                "method_id": self.new_method_identity[0],
                "method_version": self.new_method_identity[1],
                "notice": "Use this fresh method_id and method_version for the new method proposal. "
                          "They avoid shared-library collisions and confer no admission or authority.",
            }} if self.new_method_identity is not None else {}),
        }


def goal_type_ref(catalog: TaskTypeCatalog, signature: GoalSignature) -> VersionedRef | None:
    """The task type ref the registry keys its candidates by, from the catalogue.

    A :class:`~...contracts.htn.GoalSignature` carries no content hash of its own, and
    the registry matches a method's ``goal_type_ref`` on the full
    ``(id, version, content_hash)`` triple — so the hash has to come from the
    declaration that owns it rather than be derived here.  A goal type this deployment
    does not declare returns ``None``: there is then nothing to retrieve candidates or
    suggestions for, which is exactly the "empty library" case.
    """

    for spec in catalog.task_types():
        if spec.goal_signature.signature_id == signature.signature_id and int(
            spec.goal_signature.version
        ) == int(signature.version):
            return spec.task_type_ref
    return None


def _offers(
    catalog: TaskTypeCatalog, capabilities: CapabilitySnapshot, *, domain: str | None,
    max_operators: int,
) -> tuple[OperatorOffer, ...]:
    offers: list[OperatorOffer] = []
    # A task type published at a new version is offered at that version only.  The
    # older row stays in the catalogue — a method that already names it still resolves
    # — but a model shown both would be invited to build on the one whose ports were
    # the defect.  "Latest" is read among the rows that *would* be offered (primitive,
    # in this domain), so a compound or foreign-domain row at a higher version cannot
    # hide the primitive one a method may actually use.
    population = [
        spec
        for spec in catalog.task_types()
        if spec.form is TaskForm.PRIMITIVE
        and (domain is None or spec.domain is None or spec.domain == domain)
    ]
    latest: dict[str, int] = {}
    for spec in population:
        key = spec.task_type_ref.id
        latest[key] = max(latest.get(key, 0), int(spec.task_type_ref.version))
    for spec in sorted(
        population,
        key=lambda item: (item.task_type_ref.id, int(item.task_type_ref.version)),
    ):
        if int(spec.task_type_ref.version) < latest[spec.task_type_ref.id]:
            continue
        ref = spec.task_type_ref
        offers.append(
            OperatorOffer(
                task_type_id=ref.id,
                version=int(ref.version),
                content_hash=ref.content_hash,
                form=str(spec.form),
                statement=spec.goal_signature.statement,
                input_ports=tuple(port.port_key for port in spec.input_ports),
                output_ports=tuple(port.port_key for port in spec.output_ports),
                required_capabilities=tuple(spec.required_capabilities),
                unavailable_capabilities=capabilities.missing_from(
                    tuple(spec.required_capabilities)
                ),
                coverage_criteria=tuple(spec.goal_signature.coverage_criteria),
                side_effect_kind=str(spec.side_effect_kind),
                domain=spec.domain,
            )
        )
        if len(offers) >= max_operators:
            break
    return tuple(offers)


def _notes(
    registry: MethodRegistry,
    goal_type: VersionedRef | None,
    reports: Mapping[str, ApplicabilityReport],
    *,
    mission_id: str | None,
) -> tuple[ApplicabilityNote, ...]:
    if mission_id is None or goal_type is None:
        return ()
    notes: list[ApplicabilityNote] = []
    for candidate in registry.candidates_for(goal_type, mission_id=MissionRef(mission_id)):
        key = f"{candidate.method_ref.method_id}@{int(candidate.method_ref.version)}"
        report = reports.get(key)
        if report is None or report.applicable:
            continue
        notes.append(ApplicabilityNote.of(candidate, report))
    return tuple(notes)


def build_context(
    goal: TaskSemanticBindingV1,
    capabilities: CapabilitySnapshot,
    registry: MethodRegistry,
    *,
    catalog: TaskTypeCatalog,
    reports: Mapping[str, ApplicabilityReport] | None = None,
    mission_id: str | None = None,
    domain: str | None = None,
    max_operators: int = DEFAULT_MAX_OPERATORS,
) -> MethodProposalContext:
    """The typed context for "this compound goal has no usable method".

    ``reports`` is keyed by ``method_id@version`` and is what turns "nothing applied"
    into something actionable: the model is told which methods exist for this goal
    type and which axis each of them failed on.  ``mission_id`` scopes the candidate
    lookup and is deliberately *not* written into the context: which Mission this is
    belongs to the dispatch that carries the package, never to the payload the model
    reads (§18.5).
    """

    if not isinstance(goal, TaskSemanticBindingV1):
        raise ContractError("build_context expects the goal's TaskSemanticBindingV1")
    if not isinstance(capabilities, CapabilitySnapshot):
        raise ContractError("build_context expects a CapabilitySnapshot")
    if not isinstance(registry, MethodRegistry):
        raise ContractError("build_context expects a MethodRegistry")
    if not isinstance(catalog, TaskTypeCatalog):
        raise ContractError("build_context expects a TaskTypeCatalog")
    if int(max_operators) < 1:
        raise ContractError("max_operators must be at least 1")
    if goal.form is not TaskForm.COMPOUND:
        raise ContractError(
            "a method is proposed for a compound goal; a primitive is dispatched to "
            "its operator and needs no decomposition (§6.2)"
        )
    signature = goal.goal_signature
    offers = _offers(catalog, capabilities, domain=domain, max_operators=int(max_operators))
    goal_type = goal_type_ref(catalog, signature)
    return MethodProposalContext(
        goal_task_id=str(goal.task_id),
        obligation_id=str(goal.obligation_id),
        goal_signature=signature.to_json(),
        required_criteria=tuple(goal.requirement_refs),
        goal_parameters=dict(goal.typed_parameters),
        operators=offers,
        rejected_methods=_notes(registry, goal_type, reports or {}, mission_id=mission_id),
        unavailable_capabilities=tuple(
            sorted(
                {
                    capability
                    for offer in offers
                    for capability in offer.unavailable_capabilities
                }
            )
        ),
        goal_type_ref=None if goal_type is None else goal_type.to_json(),
        criterion_evidence=tuple(
            {
                "id": str(criterion),
                "evidence_requirement": str(signature.statement),
            }
            for criterion in signature.coverage_criteria
        ),
    )


def admit_proposal(
    proposal: MethodProposal, *, registry: MethodRegistry, policy: AdmissionPolicy
) -> AdmissionReceipt:
    """A decoded proposal → §7.3's six steps → at most ``TRIAL_ADMITTED``.

    The author is **fixed** at :data:`PROPOSAL_AUTHOR`.  The status the payload declares
    is *kept* and refused by the protocol rather than normalised here: a silent rewrite
    to ``DRAFT`` would leave no record that a model-authored submission tried to award
    itself an admission (§6.3).  A payload that declares a *different* author than the
    one presented is refused for the same reason — authorship is not self-asserted.
    """

    if not isinstance(proposal, MethodProposal):
        raise ContractError("admit_proposal expects a decoded MethodProposal")
    if not isinstance(registry, MethodRegistry):
        raise ContractError("admit_proposal expects a MethodRegistry")
    if not isinstance(policy, AdmissionPolicy):
        raise ContractError("admit_proposal expects an AdmissionPolicy")
    receipt = registry.admit(proposal, author=PROPOSAL_AUTHOR, policy=policy)
    if receipt.verdict not in (AdmissionVerdict.TRIAL_ADMITTED, AdmissionVerdict.REJECTED):
        raise ContractError(
            f"the registry answered {receipt.verdict!s} for a proposed method; the lifecycle "
            "is implemented only as far as TRIAL_ADMITTED (§7.3)"
        )
    if receipt.status not in TERMINAL_STATUSES:
        raise ContractError(
            f"a proposed method reached {receipt.status!s}; succeeding once is not a "
            "promotion (§7.3)"
        )
    return receipt


__all__ = (
    "DEFAULT_MAX_OPERATORS",
    "MAX_PROPOSED_METHOD_STEPS",
    "METHOD_SHAPE",
    "PROPOSAL_AUTHOR",
    "TERMINAL_STATUSES",
    "VALUE_CONTAINERS",
    "ApplicabilityNote",
    "MethodProposalContext",
    "OperatorOffer",
    "admit_proposal",
    "authority_claims",
    "build_context",
    "goal_type_ref",
    "rejection_problems",
)
