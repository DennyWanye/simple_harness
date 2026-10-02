# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c part 2b: a verified primitive leaf → an ``Acceptance`` → the output index.

Part 2 built ``accept_review`` and part 2's §13 listed what was missing around it:
nothing in ``src`` turned a *verified result* into the AER anchors the command
quotes, so ``acceptance_outputs`` was never written and ``_recorded_outputs`` still
had nothing to read.  This module is that bridge, and it is deliberately a **bridge
and not a decision**: every value it writes is read off something that already
happened.

Where each anchor comes from — the point being that none of them is invented here:

``RequirementsRevision``
    the leaf's own goal signature plus the root criteria the adopted method's
    ``criterion_links`` hang on this occurrence (P2.3h, :func:`criteria_for`).  Its
    ``coverage_criteria`` are what the plan says this occurrence has to cover; the
    *required checks* of each criterion are the verification layers that actually
    ran on this result.  A criterion nobody can check is therefore not silently
    satisfied — it is a criterion with no gate, and the acceptance formula treats it
    as such.  A root criterion the plan did **not** link to this leaf is absent from
    its package, never PASS.
``ReviewPackage`` / ``ReviewRecord``
    frozen from the verification verdict and **stored before the command**, because
    ``accept_review`` re-reads both from the store and compares content hashes: an
    anchor supplied with the command is exactly what an immutable anchor is not.
``CriterionOutcome.evidence_refs``
    one ``TOOL_RECEIPT`` per layer that ran, attributed ``Provenance.TOOL`` by *this*
    side.  A model may only ever claim ``model``/``human`` for itself, so the
    attribution written here is the signal ``receipt_source`` is allowed to trust —
    and it is written only for layers the router really recorded.
``ValidityWitness``
    a fresh ``purpose=ACCEPT`` witness for this task, at the scope's current epoch.
    A ``START`` witness that licensed the dispatch does not license the acceptance.
``AcceptedOutput``
    the artifacts this result produced, filed under the ports the **plan** declares
    for this occurrence.  Where the plan declares no port, nothing is indexed: a leaf
    whose result no occurrence consumes feeds nothing, and an entry for it would be
    the all-ancestors sweep §24.1 decision 4 removed.

Two things this module refuses to do:

* **It never manufactures a PASS.**  :func:`outcomes_for` reports what each layer
  reported; a layer that errored or failed becomes a non-PASS outcome and the
  acceptance formula refuses.  A caller that only calls this on a passed verdict is
  not relying on that, and a caller that calls it on a failed one gets a refusal
  rather than an acceptance.
* **It never accepts a compound.**  A compound goal is satisfied by
  ``commit_goal_resolution`` out of its children's acceptances, never by a review of
  its own (§6.3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..artifacts.input_bindings import AcceptedOutput, DisclosureState, ResourceIdentity
from ..contracts.htn import (
    OccurrenceId,
    ReadItem,
    ReadItemKind,
    ScopeEpochRead,
    SemanticReadSet,
    TaskForm,
    TaskRef,
    TaskSemanticBindingV1,
)
from ..contracts.models import ContractError
from ..contracts.resolution import (
    Criterion,
    CriterionOrigin,
    EvaluationKind,
    RequiredEvidencePolicy,
    RequirementClass,
    RequirementsRevision,
    ReviewBinding,
    ReviewPackage,
    ReviewPackageId,
    ReviewPurpose,
    ReviewRecord,
    WorkspaceAccess,
)
from ..contracts.semantic_base import (
    Provenance,
    TypedRef,
    TypedRefKind,
    content_hash_of,
)
from ..runtime.output_blocks import PortClaim
from ..storage.htn_store import HtnStore
from ..storage.store import StoreError
from ..verification.acceptance_rules import ExecutionPosture, IndependenceFacts
from .accepted_outputs import CarriedCriterion, output_ports_in_revision
from .resolution_commits import AcceptanceReceipt, AcceptReviewCommand, ResolutionPrincipal

#: The review policy this deployment reviews a hierarchical leaf under.  A named
#: constant rather than a literal at three call sites, so the package, the record
#: and the command cannot drift into quoting two policies.
LEAF_REVIEW_POLICY = "hierarchical-leaf-review-v1"

#: The layer statuses that mean the check *ran to a conclusion*.  ``ERROR`` and
#: ``NEEDS_HUMAN`` are deliberately absent: a check that could not finish is not a
#: check that passed, and AER-V04 exists because "the model said it passed" is not
#: the same fact as "the layer ran".
CONCLUSIVE_LAYER_STATUSES = frozenset({"PASS", "FAIL"})

#: What the reviewer is allowed to do to the candidate.  ``WRITE`` is refused at
#: construction, so stating ``READ_ONLY`` here is a claim the package can hold.
REVIEWER_ACCESS = WorkspaceAccess.READ_ONLY

#: The one criterion a leaf owes when the plan hangs no root criterion on it and its
#: own goal type declares none (P2.3h).  Such a step — ``facts``, ``reproduce`` — is
#: still accepted on its verified result; what it is *not* is a witness to the root
#: goal, and its acceptance must not say otherwise.  Before P2.3h the fallback was
#: the obligation's ``requirement_refs``, i.e. the **parent's** criteria, so every
#: leaf's review stamped ``c-test-passes`` PASS and the root reviewer of the Grok C3
#: run correctly refused to count those stamps as evidence.
LEAF_LOCAL_CRITERION = "c-leaf-verified"


@dataclass(frozen=True, slots=True)
class LayerOutcome:
    """One verification layer's result, as this module needs it.

    A tiny value rather than the router's ``LayerResult`` so the assembly can also
    be driven from stored rows (a restart re-reads the recorded layers) without
    either side importing the other's shape.
    """

    layer: str
    status: str
    summary: str = ""

    @property
    def conclusive(self) -> bool:
        return self.status in CONCLUSIVE_LAYER_STATUSES

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def layer_outcomes(layers: Sequence[Any]) -> tuple[LayerOutcome, ...]:
    """Normalise whatever the caller holds — router results or stored rows."""

    out: list[LayerOutcome] = []
    for item in layers:
        if isinstance(item, LayerOutcome):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(
                LayerOutcome(
                    layer=str(item.get("layer", "")),
                    status=str(item.get("status", "")),
                    summary=str(item.get("summary", "")),
                )
            )
        else:
            out.append(
                LayerOutcome(
                    layer=str(getattr(item, "layer", "")),
                    status=str(getattr(item, "status", "")),
                    summary=str(getattr(item, "summary", "")),
                )
            )
    return tuple(out)


def check_ids(layers: Sequence[LayerOutcome]) -> tuple[str, ...]:
    """The checks a criterion of this leaf is gated on: the layers that ran.

    Derived from the verdict rather than configured, because a criterion gated on a
    check this deployment never runs can never be satisfied — and one gated on
    *nothing* would be satisfied by a review's own say-so, which is the
    self-attested PASS AER-V04 refuses.
    """

    return tuple(sorted({item.layer for item in layers if item.conclusive}))


def local_content_criterion(
    binding: TaskSemanticBindingV1, layers: Sequence[LayerOutcome]
) -> Criterion:
    """Fixed local output review; this never attests a root goal or an effect."""
    return Criterion(
        criterion_id=LEAF_LOCAL_CRITERION,
        revision=1,
        origin=CriterionOrigin.DERIVED,
        statement=(f"the result of {binding.task_id!s} passed the verification layers that ran; "
                   "this acceptance vouches only for its own declared outputs, "
                   "not for the root goal or any external effect"),
        requirement_class=RequirementClass.REQUIRED_OUTCOME,
        evaluation_kind=EvaluationKind.DETERMINISTIC,
        required_evidence_policy=RequiredEvidencePolicy(required_check_ids=check_ids(layers)),
    )


def criteria_for(
    binding: TaskSemanticBindingV1,
    layers: Sequence[LayerOutcome],
    *,
    carried: Sequence[CarriedCriterion] = (),
) -> tuple[Criterion, ...]:
    """The leaf's own criteria, each gated on the checks that actually ran.

    P2.3h: a leaf owes exactly three kinds of criterion and nothing else —

    * its goal type's own ``coverage_criteria``, by name;
    * the root criteria the adopted method's ``criterion_links`` hang on **this**
      occurrence, under the link's ``child_criterion_id`` (``c-green`` for
      ``verify``), with the link's ``evidence_requirement`` as the statement;
    * when there are none of either, :data:`LEAF_LOCAL_CRITERION`.

    The obligation's ``requirement_refs`` are no longer a fallback.  They are the
    *parent's* requirements (a ``refines_parent`` step inherits them verbatim), so
    reading them here made every leaf's ``ReviewRecord`` stamp every root criterion
    PASS — the ``facts`` step vouching for ``c-test-passes`` — which is a stamp no
    reviewer may take as evidence and the Grok C3 reviewer rightly did not.
    """

    gates = check_ids(layers)
    statements: dict[str, str] = {}
    for name in binding.goal_signature.coverage_criteria:
        statements.setdefault(
            str(name), f"{name} is covered by the accepted result of {binding.task_id!s}"
        )
    for link in carried:
        statements.setdefault(
            str(link.leaf_criterion_id),
            f"{link.leaf_criterion_id} covers root criterion {link.parent_criterion_id} of "
            f"{link.parent_task_id}: {link.evidence_requirement}",
        )
    if not statements:
        statements[LEAF_LOCAL_CRITERION] = (
            f"the result of {binding.task_id!s} passed the verification layers that ran; "
            "no root criterion is linked to this step, so this acceptance vouches for its "
            "own declared outputs and for nothing about the root goal"
        )
    return tuple(
        Criterion(
            criterion_id=name,
            revision=1,
            origin=CriterionOrigin.DERIVED,
            statement=statement,
            requirement_class=RequirementClass.REQUIRED_OUTCOME,
            evaluation_kind=EvaluationKind.DETERMINISTIC,
            required_evidence_policy=RequiredEvidencePolicy(required_check_ids=gates),
        )
        for name, statement in statements.items()
    )


def receipt_ref(result_id: str, layer: str) -> TypedRef:
    """A dispatcher-attributed receipt for one layer that ran on this result."""

    digest = content_hash_of({"result": str(result_id), "layer": str(layer)})
    return TypedRef(
        kind=TypedRefKind.TOOL_RECEIPT,
        id=f"vlayer-{digest[:32]}",
        revision=1,
        content_hash=digest,
        produced_by=Provenance.TOOL,
    )


def accepted_outputs_for(
    ports: Mapping[str, Any],
    *,
    occurrence: OccurrenceId,
    task_id: TaskRef,
    result_id: str,
    acceptance_id: str,
    support_revision: int,
    artifacts: Sequence[Any],
    namespace: str,
    claims: Sequence[PortClaim] = (),
) -> tuple[AcceptedOutput, ...]:
    """The accepted artifacts, filed at the ports the producer **said** they belong to.

    P2.3c part 2d, decision 4.  The pairing is *declared*, not derived.  Until now
    this function guessed: an artifact went to the port whose key appeared anywhere
    inside its path (so a port called ``facts`` claimed ``artifacts/anything.json``),
    or, when there was exactly one port and one file, to that port by position.  TG
    design §10.2 forbids exactly that — "two different hashes onto one final file"
    may not be settled by topology or by name — and §21.5's "wrongly declared
    complete = 0" is what a mis-paired artifact ends up violating downstream.

    So the port comes from a :class:`~..runtime.output_blocks.PortClaim`, which the
    Worker wrote and the block parser already checked against this plan's declared
    ports and this Attempt's real files.  Everything else on the
    :class:`~..artifacts.input_bindings.AcceptedOutput` is system-bound: the schema
    comes from the ``DataRequirement`` that declares the edge (never from the
    producer, which could otherwise relabel its own output), and the acceptance id,
    content hash, support revision and occurrence are filled here.

    An artifact no claim names stays an artifact: it is evidence of the run and is
    not indexed.  A *port* nobody claimed is the accept side's problem, not this
    function's — :meth:`ResolutionCommitsMixin.accept_review` refuses the acceptance
    with ``OUTPUT_PORT_UNCLAIMED`` rather than letting the leaf pass with a gap.
    """

    if not ports or not artifacts or not claims:
        return ()
    by_path: dict[str, Any] = {}
    for artifact in artifacts:
        path = str(getattr(artifact, "path", "") or getattr(artifact, "id", ""))
        by_path.setdefault(path, artifact)
    out: list[AcceptedOutput] = []
    for ordinal, claim in enumerate(claims):
        schema = ports.get(claim.port_key)
        artifact = by_path.get(claim.path)
        if schema is None or artifact is None:
            # The parser refuses both of these at the block, so reaching here means a
            # caller assembled claims by hand.  Skipping is the conservative answer —
            # inventing a port or an artifact is the guess this whole change removes.
            continue
        out.append(
            AcceptedOutput(
                producer_occurrence=occurrence,
                producer_task_ref=task_id,
                output_port=claim.port_key,
                producer_result_id=str(result_id),
                acceptance_id=str(acceptance_id),
                support_revision=int(support_revision),
                artifact_id=str(artifact.id),
                content_hash=str(getattr(artifact, "content_hash", "") or artifact.id),
                schema_ref=schema,
                source_revision=str(getattr(artifact, "version", "") or "1"),
                source_identity=ResourceIdentity(
                    namespace=namespace, path=claim.path or str(artifact.id)
                ),
                producer_ordinal=ordinal,
                disclosure=DisclosureState.DISCLOSABLE,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------------------
# the assembly
# --------------------------------------------------------------------------------------


@dataclass
class LeafAcceptanceAssembly:
    """Turns one verified primitive result into a committed ``Acceptance``.

    Holds a store and the one Commit Service; holds no state about a Mission,
    because the anchors it writes are read back by the commit from the same store
    inside the transaction.
    """

    store: Any
    commit: Any
    #: The hierarchical assembly, when the caller has one.  It is how the acceptance
    #: learns *what this dispatch consumed*: the resolved ``InputManifest`` of the
    #: occurrence.  Without it the acceptance may only claim the empty manifest, and
    #: it says so rather than quoting a manifest it did not see.
    dispatch: Any = None
    scope_id: str = "mission"
    reviewer_agent_id: str = "verifier-router"

    @property
    def semantics(self) -> HtnStore:
        return HtnStore(self.store)

    def accept(
        self,
        mission_id: str,
        task_id: str,
        *,
        result_id: str,
        layers: Sequence[Any],
        artifacts: Sequence[Any] = (),
        producer_agent_ids: Sequence[str] = (),
        reviewer_agent_id: str | None = None,
        now_ms: int,
        command_id: str | None = None,
        input_manifest_hash: str = "",
        namespace: str = "workspace",
        port_claims: Sequence[PortClaim] = (),
    ) -> AcceptanceReceipt:
        """Freeze the anchors, then commit the acceptance.  Nothing else decides.

        ``port_claims`` is what the Worker said about its own files — "this path is
        the ``repository_facts`` the plan asked for" — already checked against the
        declared ports and the Attempt's artifacts by the block parser.  Without it
        no output is indexed and ``accept_review`` refuses any required port that a
        consumer is waiting on (``OUTPUT_PORT_UNCLAIMED``, decision 4).
        """

        outcomes = layer_outcomes(layers)
        from .taskgraph_review import read_review_origin
        origin = read_review_origin(self.commit, mission_id, task_id, result_id)
        binding = origin.semantic
        if binding is None:
            raise ContractError(
                f"task {task_id!r} has no TaskSemanticBindingV1 in mission {mission_id!r}; in "
                "the hierarchical mode a missing binding is corruption, not a legacy task"
            )
        if binding.form is not TaskForm.PRIMITIVE:
            raise ContractError(
                f"task {task_id!r} is {binding.form!s}; a compound goal is satisfied by "
                "commit_goal_resolution out of its children's acceptances, never by a review "
                "of its own (§6.3)"
            )
        semantics = self.semantics
        from .scoped_content_review import read_task_content_projection

        # What is judged — criteria, layers, producer — is read back from the stored
        # result under the frozen completion scope, never taken from the caller.
        projection = read_task_content_projection(self.store, mission_id, task_id, result_id)
        revision = projection.requirements
        outcomes = layer_outcomes(self.store.list_verifications(result_id))
        producer_agent_ids = (projection.producer_agent_id,)
        manifest = origin.context.inputs.binding.manifest_hash
        if input_manifest_hash and input_manifest_hash != manifest:
            raise ContractError("TASKGRAPH_REVIEW_MANIFEST_MISMATCH")
        from ..storage.assurance_store import AssuranceStore

        AssuranceStore(self.store).require_assured(mission_id)
        package = self._package(
            mission_id,
            binding,
            revision,
            result_id,
            manifest,
            producer_agent_ids,
            projection=projection,
        )
        record = self._official_record(package)
        acceptance_id = f"acc-{content_hash_of({'task': task_id, 'result': result_id})[:32]}"
        try:
            previous = self.semantics.get_acceptance(acceptance_id)
        except StoreError:
            previous = None
        if previous is not None:
            now_ms = previous.accepted_at_ms
        # The acceptance is licensed by a current UseCertificate prepared outside
        # this transaction and committed by accept_review beside the Acceptance.
        from .resolution_commits import ResolutionCommitRejected

        validity = getattr(self.commit, "_assurance_validity", None)
        candidate = (
            None
            if validity is None
            else validity.candidate_for(mission_id, str(record.record_id))
        )
        committed = self.store.connection.execute(
            "SELECT certificate_id FROM assurance_use_certificates WHERE mission_id=? "
            "AND consumer_kind='ACCEPTANCE' AND consumer_id=? AND purpose='ACCEPT' "
            "AND json_extract(certificate_json,'$.decision')='USABLE' "
            "ORDER BY issued_at_ms DESC LIMIT 1",
            (mission_id, acceptance_id),
        ).fetchone()
        if committed is not None and previous is not None:
            # Exact replay of an already licensed acceptance: the command names
            # the certificate that was committed beside it, never a new one.
            witness_id = str(committed[0])
        elif candidate is None:
            raise ResolutionCommitRejected(
                "USE_CERTIFICATE_REQUIRED",
                f"no current use certificate is prepared for official review "
                f"{record.record_id!s}; an assured acceptance is not licensed by a "
                "cached verdict",
            )
        else:
            witness_id = candidate.certificate_id
        command = AcceptReviewCommand(
            command_id=command_id or f"accept:{result_id}",
            mission_id=mission_id,
            task_id=task_id,
            obligation_id=str(binding.obligation_id),
            acceptance_id=acceptance_id,
            package=package,
            record=record,
            requirements=revision,
            witness_id=witness_id,
            independence=IndependenceFacts(
                producer_agent_ids=tuple(producer_agent_ids),
                reviewer_can_write_candidate=False,
            ),
            posture=ExecutionPosture(),
            read_set=self._read_set(mission_id, binding, revision),
            accepted_at_ms=int(now_ms),
            outputs=self._outputs(
                mission_id,
                binding,
                result_id=result_id,
                acceptance_id=acceptance_id,
                artifacts=artifacts,
                namespace=namespace,
                port_claims=port_claims,
                pinned_origin=origin,
            ),
            artifact_refs=tuple(
                TypedRef(kind=TypedRefKind.ARTIFACT, **ref.to_json(), produced_by=Provenance.TOOL)
                for ref in projection.artifacts
            ),
            purpose=ReviewPurpose.TASK_CONTENT,
            # The Critic layer is the independent semantic review; this deployment
            # requires it, and a verdict built without it refuses rather than being
            # quietly accepted as "the rules did not ask".
            semantic_review_required=True,
            policy_ref=LEAF_REVIEW_POLICY,
            issued_by=self.reviewer_agent_id,
            scope_id=self.scope_id,
            source={"result_id": str(result_id), "layers": [item.layer for item in outcomes]},
        )
        principal = ResolutionPrincipal(
            principal_id=self.reviewer_agent_id,
            scope_id=self.scope_id,
            manager_epoch=semantics.epoch(mission_id, self.scope_id),
        )
        return self.commit.accept_review(command, principal)

    # -- the anchors ----------------------------------------------------------------
    def _occurrence_in_active_revision(
        self, mission_id: str, task_id: str
    ) -> tuple[int, OccurrenceId] | None:
        semantics = self.semantics
        active = semantics.active_plan_revision(mission_id)
        if active is None:
            return None
        revision = int(active.revision)
        occurrence = next(
            (
                spec.occurrence_id
                for spec in semantics.list_plan_memberships(mission_id, revision)
                if str(spec.task_id) == str(task_id)
            ),
            None,
        )
        if occurrence is None:
            return None
        return revision, occurrence

    def _package(
        self,
        mission_id: str,
        binding: TaskSemanticBindingV1,
        revision: RequirementsRevision,
        result_id: str,
        manifest_hash: str,
        producer_agent_ids: Sequence[str],
        *,
        projection: Any,
        persist: bool = True,
    ) -> ReviewPackage:
        package = ReviewPackage(
            package_id=ReviewPackageId(
                f"pkg-{content_hash_of({'r': result_id, 'rev': revision.revision})[:32]}"
            ),
            purpose=ReviewPurpose.TASK_CONTENT,
            binding=ReviewBinding(
                mission_id=mission_id,
                obligation_id=str(binding.obligation_id),
                subject_ref=TypedRef(
                    kind=TypedRefKind.TASK,
                    id=str(binding.task_id),
                    revision=int(binding.contract_revision),
                    content_hash=binding.contract_hash,
                ),
                requirements_revision=int(revision.revision),
                input_manifest_hash=manifest_hash,
                policy_ref=TypedRef(
                    kind=TypedRefKind.SOURCE,
                    id=LEAF_REVIEW_POLICY,
                    revision=1,
                    content_hash=content_hash_of(LEAF_REVIEW_POLICY),
                ),
            ),
            criteria=projection.criteria,
            success_expression=projection.expression,
            candidate_refs=(
                TypedRef(
                    # The candidate is the recorded *result*, referenced as the
                    # artifact-kind ref the annex schema has for it — there is no
                    # ``result`` kind, and inventing one would put a value in the
                    # contract that nothing else can read.
                    kind=TypedRefKind.ARTIFACT,
                    id=str(result_id),
                    revision=1,
                    content_hash=content_hash_of(str(result_id)),
                    produced_by=Provenance.TOOL,
                ),
            ),
            producer_agent_ids=tuple(producer_agent_ids),
            reviewer_workspace_access=REVIEWER_ACCESS,
            requirements_content_hash=revision.content_hash(),
        )
        if not persist:
            # Assurance freezes the same original package before the reserve/
            # intent transaction. Its transport owns the atomic insertion.
            return package
        return self._stored_package(package)

    def _stored_package(self, package: ReviewPackage) -> ReviewPackage:
        try:
            stored = self.semantics.get_review_package(str(package.package_id))
        except StoreError:
            self.semantics.insert_review_package(package)
            return package
        # A replay re-presents the *same* frozen anchor, so re-inserting it would be
        # a conflict about nothing.  A differing one is a real conflict and is left
        # to the store to refuse rather than silently replaced.
        if stored.content_hash() == package.content_hash():
            return package
        if (
            stored.purpose == package.purpose
            and stored.binding == package.binding
            and stored.candidate_refs == package.candidate_refs
            and tuple(stored.producer_agent_ids) == tuple(package.producer_agent_ids)
        ):
            # The official review judged the package Assurance froze
            # before its Critic ran (read_task_content_candidate: criteria from the
            # approved check policy).  Accept exactly that package; this reader's own
            # criteria view (checks that ran) is a different spelling of the same
            # subject, not a different subject (Host native run arp.14, 2026-09-24:
            # the first ACCEPT crashed six cycles on the duplicate package id).
            return stored
        self.semantics.insert_review_package(package)
        return package

    def _official_record(self, package: ReviewPackage) -> ReviewRecord:
        """Only the authenticated runtime importer writes the official record; the
        local layers never assemble one."""
        official = self.semantics.official_review_record(str(package.package_id))
        if official is None:
            from .resolution_commits import ResolutionCommitRejected

            raise ResolutionCommitRejected(
                "REVIEW_NOT_OFFICIAL",
                f"no official Assurance review record is stored for package "
                f"{package.package_id!s}",
            )
        return official


    def _read_set(
        self, mission_id: str, binding: TaskSemanticBindingV1, revision: RequirementsRevision
    ) -> SemanticReadSet:
        epoch = self.semantics.epoch(mission_id, self.scope_id)
        return SemanticReadSet(
            requirements_revision=int(revision.revision),
            manager_epoch=epoch,
            scope_epochs=(ScopeEpochRead(scope_id=self.scope_id, validity_epoch=epoch),),
            goal_revisions=(
                ReadItem(
                    kind=ReadItemKind.TASK,
                    id=str(binding.task_id),
                    semantic_revision=int(binding.contract_revision),
                    content_hash=binding.contract_hash,
                ),
            ),
        )

    def _outputs(
        self,
        mission_id: str,
        binding: TaskSemanticBindingV1,
        *,
        result_id: str,
        acceptance_id: str,
        artifacts: Sequence[Any],
        namespace: str,
        port_claims: Sequence[PortClaim] = (),
        pinned_origin: Any = None,
    ) -> tuple[AcceptedOutput, ...]:
        semantics = self.semantics
        if pinned_origin is not None:
            occurrence = OccurrenceId(pinned_origin.context.inputs.binding.occurrence_id)
            ports = dict(pinned_origin.ports)
        else:
            located = self._occurrence_in_active_revision(mission_id, str(binding.task_id))
            if located is None:
                return ()
            revision, occurrence = located
            ports = output_ports_in_revision(
                semantics, mission_id, revision, occurrence, str(binding.task_id)
            )
        return accepted_outputs_for(
            ports,
            occurrence=occurrence,
            task_id=binding.task_id,
            result_id=result_id,
            acceptance_id=acceptance_id,
            support_revision=len(semantics.list_observations(mission_id)),
            artifacts=artifacts,
            namespace=namespace,
            claims=port_claims,
        )


__all__ = (
    "CONCLUSIVE_LAYER_STATUSES",
    "LEAF_LOCAL_CRITERION",
    "LEAF_REVIEW_POLICY",
    "LayerOutcome",
    "LeafAcceptanceAssembly",
    "accepted_outputs_for",
    "check_ids",
    "criteria_for",
    "layer_outcomes",
    "receipt_ref",
)
