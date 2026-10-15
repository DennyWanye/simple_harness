# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Closing the root goal: review readiness, root resolution inputs and the root commit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ...assurance.codec import AssuranceError
from ...contracts import ContractError
from ...contracts.evidence_state import Validity
from ...contracts.htn import ReadItem, ReadItemKind, ScopeEpochRead, SemanticReadSet, TaskForm
from ...contracts.resolution import (
    GoalResolution,
    GoalResolutionId,
    ReviewPackage,
    ReviewPurpose,
    ReviewVerdict,
    WorkspaceAccess,
)
from ...graph.eligibility import OccurrenceOutcome
from ...graph.task_network import GATING_REQUIREDNESS, TaskNetworkSnapshot
from ...storage.store import StoreError
from ...verification.acceptance_rules import CompoundFacts, ExecutionPosture, IndependenceFacts
from ..completion_status import (
    EFFECT_SETTLED_STATES,
    read_current_effect,
    read_occurrence_completion,
)
from ..completion_support import current_child_supports
from ..plan_commits import PlanPrincipal
from ..resolution_commits import (
    CommitGoalResolutionCommand,
    ResolutionCommitRejected,
    ResolutionPrincipal,
)
from ..review_adjudication import accepted_or_adjudicated
from ..scoped_composition_review import root_content_projection
from .core import HierarchicalDispatchCore
from .support import (
    ROOT_RESOLUTION_REFUSED,
    ROOT_REVIEW_CUT,
    ROOT_REVIEW_SUPERSEDED,
    NetworkView,
    RootResolutionInputs,
    RootResolutionOutcome,
    _root_criteria,
)


class RootResolutionMixin(HierarchicalDispatchCore):
    """Closing the root goal: review readiness, root resolution inputs and the root commit."""

    __slots__ = ()

    if TYPE_CHECKING:

        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...
        def read(
            self, mission_id: str, *, now_ms: int | None = None, tolerate_integrity: bool = False
        ) -> NetworkView: ...

    def root_review_ready(self, mission_id: str) -> bool:
        """Whether the root review may run at all (§7.2, TG §6.2).

        Every gating child of every adopted root method needs an accepted outcome
        first.  The parent's own state is deliberately not consulted: a review that
        waits for the parent it is supposed to conclude does not terminate.

        The root's required effects are not waited for to be *accepted* (Assurance 1.1
        §7.2, 2026-10-06 车道 O): the root review judges the content, the root resolution
        records that the business requirement is met, and the closeout converges the
        effects (UNKNOWN → BLOCKED_UNKNOWN) before anything completes.  Each effect is
        judged once, by its own OPERATION_OUTCOME review.  What *is* waited for is an
        effect still **in flight** (awaiting its intent, review, approval, execution or
        outcome review; rejected or failed and so the operation line's to resubmit): a
        root resolution formed then would freeze the content while the operation line
        may still have the planner rewrite it (a rejected publish), and a resolved duty
        admits no new work.  Settled = accepted or result unknown
        (``completion_status.EFFECT_SETTLED_STATES``, the closeout's own vocabulary).
        """

        view = self.read(mission_id)
        if view.plan.integrity_error is not None:
            return False
        roots = view.network.root_occurrence_ids
        if not roots:
            return False

        for root in roots:
            scope = read_occurrence_completion(self.store, mission_id, str(root)).scope
            for effect_key in scope.required_effect_keys:
                effect = read_current_effect(
                    self.store, mission_id, scope.spec_hash, str(effect_key)
                )
                if str(effect["state"]) not in EFFECT_SETTLED_STATES:
                    return False
            spec = view.network.occurrence(root)
            if spec.form is not TaskForm.COMPOUND:
                if view.outcomes.get(root) is not OccurrenceOutcome.ACCEPTED:
                    return False
                continue
            children = view.network.adopted_children(root)
            if not children:
                return False
            for binding in children:
                if binding.requiredness not in GATING_REQUIREDNESS:
                    continue
                if view.outcomes.get(binding.occurrence_id) is not OccurrenceOutcome.ACCEPTED:
                    return False
        return True

    def terminal(self, mission_id: str) -> bool:
        """Whether the Mission's work is finished, read from the resolutions."""

        view = self.read(mission_id)
        required = set(view.network.required_obligations)
        if not required:
            return False

        return all(
            read_occurrence_completion(self.store, mission_id, str(root)).complete
            for root in view.network.root_occurrence_ids
        )

    # --------------------------------------------------------- the root acceptance gate
    def root_contributions(self, mission_id: str) -> dict[str, tuple[str, ...]]:
        """Which occurrences of the adopted root method have a **CURRENT** content Acceptance.

        Read from the ``acceptances`` rows and not from the outcome projection.  They
        usually agree, and where they do not the store is right: an Acceptance that was
        superseded or revoked since the projection was computed is history, and a root
        resolution quoting it would be declaring the Mission complete on the strength
        of work nobody accepts any more (§21.5 "wrongly declared complete = 0").

        Effect acceptances are not contributions to the root review (Assurance §7.2,
        2026-10-06): an effect is judged by its own OPERATION_OUTCOME review and
        converges in the closeout; the root review is cut over the content, and an
        effect landing while it runs does not re-cut it.  The accepted effects' facts
        still reach the reviewer as materials (``_root_effect_materials``).
        """

        network = self.network(mission_id)
        scoped: dict[str, tuple[str, ...]] = {}
        for spec in network.occurrences:
            status = read_occurrence_completion(self.store, mission_id, str(spec.occurrence_id))
            if status.preparation_acceptance_ids:
                scoped[str(spec.occurrence_id)] = tuple(sorted(status.preparation_acceptance_ids))
        return scoped

    def root_resolution_inputs(self, mission_id: str) -> RootResolutionInputs:
        """Everything the root ``commit_goal_resolution`` command is built from.

        Every field is *read*, never invented.  The three that a deployment has to
        have produced beforehand — the ``MISSION_FINAL`` review package, its official
        record, and the ``purpose=ACCEPT`` witness — are reported as missing rather
        than fabricated: a Mission root resolution is formed out of the success
        formula plus the final acceptance (§6.3, §8.1), so a system that writes its
        own review anchor has decided the answer it was supposed to check.
        """

        network = self.network(mission_id)
        roots = network.root_occurrence_ids
        if len(roots) != 1:
            return RootResolutionInputs(
                reason="ROOT_NOT_SINGULAR",
                detail=(
                    f"this plan revision has {len(roots)} root occurrence(s); a Mission root "
                    "resolution is formed for one root goal and choosing among several would "
                    "be this module inventing a root"
                ),
            )
        root = roots[0]
        spec = network.occurrence(root)
        binding = network.binding_for_occurrence(root)
        semantics = self.semantics()
        existing = semantics.adopted_goal_resolution(mission_id, str(spec.obligation_id))
        if existing is not None:
            return RootResolutionInputs(
                reason="ALREADY_RESOLVED",
                detail=(
                    f"duty {spec.obligation_id!s} is already resolved by {existing.resolution_id!s}"
                ),
                occurrence_id=str(root),
            )
        package = self.live_root_review_package(
            mission_id, task_id=str(spec.task_id), obligation_id=str(spec.obligation_id)
        )
        if package is None:
            return RootResolutionInputs(
                reason="ROOT_REVIEW_PACKAGE_MISSING",
                detail=(
                    f"no MISSION_FINAL ReviewPackage is stored for task {spec.task_id!s}; the "
                    "root review has not been cut, so there is nothing to resolve from"
                ),
                occurrence_id=str(root),
            )
        # Review P1-7: the root's requirements are **the ones its review package was
        # cut against**, not whatever revision happens to be latest.
        # ``latest_requirements_revision`` used to answer this, but every leaf
        # acceptance publishes a Mission-level revision carrying *that leaf's*
        # coverage criteria — so one more leaf accepted between cutting the root
        # review and forming the resolution silently moved the Mission's criteria to
        # the last leaf's, and the resolution claimed coverage of something the
        # reviewer never saw.  The package's binding is the only revision that was
        # actually reviewed.
        try:
            requirements = semantics.get_requirements_revision(
                mission_id, int(package.binding.requirements_revision)
            )
        except StoreError:
            requirements = None
        if requirements is None:
            return RootResolutionInputs(
                reason="REQUIREMENTS_MISSING",
                detail=(
                    "the root review package names requirements revision "
                    f"{int(package.binding.requirements_revision)}, which this Mission does not "
                    "hold; there is nothing for the root resolution to claim coverage of (§6.3)"
                ),
                occurrence_id=str(root),
            )
        record = semantics.official_review_record(str(package.package_id))
        if record is None:
            return RootResolutionInputs(
                reason="ROOT_REVIEW_RECORD_MISSING",
                detail=(
                    f"review package {package.package_id!s} has no official ReviewRecord; the "
                    "final review has not concluded"
                ),
                occurrence_id=str(root),
            )
        # Handoff item 7: the root is licensed by the current UseCertificate
        # ``attempt_root_resolution`` prepares.
        instance = network.adopted_instance_for(root)
        # Direct children of the adopted root method, not every Acceptance in the
        # Mission: what supports the root is each direct child's current completion
        # support (a leaf's accepted contribution, an inner goal's resolution).

        contributions = current_child_supports(
            self.store, mission_id, network.adopted_children(root)
        )
        return RootResolutionInputs(
            reason="",
            occurrence_id=str(root),
            task_id=str(spec.task_id),
            obligation_id=str(spec.obligation_id),
            contract_revision=int(binding.contract_revision),
            method_instance_id=None if instance is None else str(instance.instance_id),
            requirements=requirements,
            package=package,
            record=record,
            contributions=contributions,
            root_form=str(spec.form),
        )

    def superseded_review_packages(self, mission_id: str) -> frozenset[str]:
        """The ``MISSION_FINAL`` packages a re-cut has retired (P2.3c part 3a).

        A :class:`~...contracts.resolution.ReviewPackage` is an immutable anchor, so
        "this review no longer counts" cannot be a column on it — it is the
        :data:`ROOT_REVIEW_SUPERSEDED` event, and this is the one reader of it.
        """

        return frozenset(
            str(event.payload.get("package_id", ""))
            for event in self.store.list_events(mission_id)
            if event.type == ROOT_REVIEW_SUPERSEDED
        )

    def live_root_review_package(
        self, mission_id: str, *, task_id: str, obligation_id: str
    ) -> ReviewPackage | None:
        """The ``MISSION_FINAL`` package this root resolves from, or ``None``.

        Three rules, in order, and each of them exists because of a way this could
        answer wrongly:

        1. only packages cut for **this** root, so a Mission with two roots in its
           history cannot have one root's review resolve the other;
        2. never a **superseded** one — the whole point of a re-cut is that the older
           anchor described a world that has moved;
        3. among what is left, the **last one this deployment cut**.  The order comes
           from the :data:`ROOT_REVIEW_CUT` events rather than from ``created_at``,
           because two packages written in the same clock tick have no order in the
           rows at all.  A package nobody recorded a cut for is not ranked against
           one that was: a recorded cut is this deployment saying "this is the anchor
           now", and an unrecorded package is one somebody stored directly — so the
           recorded ones win outright, and the old first-stored answer is kept for a
           Mission that has none.
        """

        semantics = self.semantics()
        retired = self.superseded_review_packages(mission_id)
        candidates = [
            item
            for item in semantics.list_review_packages(
                mission_id, purpose=ReviewPurpose.MISSION_FINAL
            )
            if str(item.binding.subject_ref.id) == str(task_id)
            and str(item.binding.obligation_id) == str(obligation_id)
            and str(item.package_id) not in retired
        ]
        if not candidates:
            return None
        order = {
            str(event.payload.get("package_id", "")): index
            for index, event in enumerate(
                item for item in self.store.list_events(mission_id) if item.type == ROOT_REVIEW_CUT
            )
        }
        recorded = [item for item in candidates if str(item.package_id) in order]
        if not recorded:
            return candidates[0]
        recorded.sort(key=lambda item: order[str(item.package_id)])
        return recorded[-1]

    def attempt_root_resolution(
        self,
        mission_id: str,
        *,
        principal: PlanPrincipal,
        command_id: str,
        resolution_id: str | None = None,
        input_manifest_hash: str | None = None,
        required_delivery_stage: Any = None,
        delivery_receipt_ids: Sequence[str] = (),
        source: Mapping[str, Any] | None = None,
    ) -> RootResolutionOutcome:
        """Form the Mission's root :class:`GoalResolution`, or say why not.

        This is the only place a Mission's root resolution is proposed from, and it is
        deliberately a *proposal*: every rule that decides whether it may be formed —
        the adopted method, a valid Acceptance for every required child, coverage of
        every root criterion, the delivery contract, the witness — lives in
        ``ResolutionCommitsMixin.commit_goal_resolution`` and runs inside its
        transaction.  What this method does is read the facts the command is made of
        and hand them over; if it also decided, there would be two places that could
        declare a Mission complete and §21.5's "wrongly declared complete = 0" would
        depend on both of them agreeing.
        """

        self.require_hierarchical(mission_id)
        if not self.root_review_ready(mission_id):
            return RootResolutionOutcome(
                committed=False,
                reason="ROOT_REVIEW_NOT_READY",
                detail="a gating child of the adopted root method has no accepted outcome yet",
            )
        inputs = self.root_resolution_inputs(mission_id)
        if inputs.reason:
            return RootResolutionOutcome(
                committed=inputs.reason == "ALREADY_RESOLVED",
                reason=inputs.reason,
                detail=inputs.detail,
            )
        requirements = inputs.requirements
        assert requirements is not None and inputs.package is not None
        assert inputs.record is not None
        manifest_hash = input_manifest_hash or inputs.package.binding.input_manifest_hash
        resolution_id = resolution_id or f"res-{inputs.occurrence_id}"
        # Assurance §7.2（2026-10-06 车道 O）：根结论复述的是根的内容投影——根终审判的那一份；
        # 效果判据由各自的结果审阅判、收尾核对收敛，不写进根结论。

        try:
            root_scope = read_occurrence_completion(
                self.store, mission_id, inputs.occurrence_id
            ).scope
            projection = root_content_projection(requirements, root_scope)
        except (ContractError, StoreError) as error:
            return self._refuse_root_resolution(
                mission_id,
                inputs,
                command_id=command_id,
                reason="OP_COMPLETION_SCOPE_UNRESOLVED",
                detail="the root content projection could not be read: " + str(error),
            )
        # Handoff item 7: on the assured lane the licence is the current
        # UseCertificate over the bound MISSION_FINAL manifest, prepared here outside
        # the write lock and committed by ``commit_goal_resolution`` under it. The
        # resolution then restates the manifest's current effective grades.

        validity = getattr(self.commit, "_assurance_validity", None)
        try:
            if validity is None:
                raise AssuranceError("USE_CERTIFICATE_REQUIRED")
            candidate = validity.prepare_root_use(inputs.record, resolution_id=resolution_id)
        except AssuranceError as error:
            return self._refuse_root_resolution(
                mission_id,
                inputs,
                command_id=command_id,
                reason=error.code,
                detail="the current use certificate could not be prepared: " + str(error),
            )
        witness_id = candidate.certificate_id
        effective_grades: Mapping[str, str] | None = candidate.effective_grades
        resolution = GoalResolution(
            resolution_id=GoalResolutionId(resolution_id),
            mission_id=mission_id,
            obligation_id=inputs.obligation_id,
            goal_task_id=inputs.task_id,
            requirements_version=int(requirements.revision),
            contract_revision=int(inputs.contract_revision),
            method_instance_id=inputs.method_instance_id,
            input_manifest_hash=manifest_hash,
            artifact_refs=(),
            child_resolution_ids=tuple(
                str(item.resolution_id)
                for item in self.semantics().list_goal_resolutions(mission_id)
                if str(item.resolution_id)
                in {source_id for ids in inputs.contributions.values() for source_id in ids}
            ),
            # Review F4: every verdict is **read from the official review record**, and
            # a criterion the record did not judge is written ``UNKNOWN``.  Writing
            # ``PASS`` for all of them — what this used to do — put an unevidenced
            # assertion into a permanent record: the accept side only refuses a verdict
            # that *contradicts* the record and a required criterion that is *missing*,
            # so a criterion nobody reviewed and no rule quotes would have been stored
            # as passed forever.  It is the same rule this command already follows for
            # ``composition_obligation_passed``: restate the review, never overrule or
            # extend it.  A required criterion the record left unjudged now shows up as
            # UNKNOWN and the AER §6.2 formula answers for it, instead of the trigger
            # answering on the reviewer's behalf.
            criteria=_root_criteria(
                projection.criteria,
                inputs.record,
                effective_grades=effective_grades,
            ),
            review_receipt_id=str(inputs.record.record_id),
            verdict=ReviewVerdict.ACCEPT,
            validity=Validity.CURRENT,
        )
        command = CommitGoalResolutionCommand(
            command_id=command_id,
            mission_id=mission_id,
            resolution=resolution,
            package=inputs.package,
            record=inputs.record,
            requirements=requirements,
            witness_id=witness_id,
            # No defaults anywhere, and nothing asserted: the facts are **read off
            # the package**, which is the frozen anchor that recorded who produced
            # the candidate at the time it was cut (AER §5.3).  Part 3a's review
            # coordinator fills those in, so a root review whose reviewer is one of
            # the producers now refuses (``INDEPENDENT_REVIEW_MISSING``) instead of
            # passing vacuously on an empty set.  ``reviewer_can_write_candidate`` is
            # derived from the package's own workspace access rather than stated:
            # ``WRITE`` is refused at construction, so this reads False for every
            # package that exists — and it reads it from the anchor instead of from
            # a caller who could say otherwise.
            independence=IndependenceFacts(
                producer_agent_ids=tuple(inputs.package.producer_agent_ids),
                reviewer_can_write_candidate=(
                    inputs.package.reviewer_workspace_access is WorkspaceAccess.WRITE
                ),
            ),
            posture=ExecutionPosture(),
            read_set=self.read_set_for_root(mission_id, inputs),
            decided_at_ms=int(self.store.now * 1000),
            purpose=ReviewPurpose.MISSION_FINAL,
            # ``CompoundFacts`` is "the two things only the caller can know", and both
            # are *read* here rather than asserted.  ``composition_obligation_passed``
            # in particular: hard-coding ``True`` would be this trigger claiming, on
            # the reviewer's behalf, that the composition held — which is precisely the
            # shape "wrongly declared complete = 0" exists to forbid.  It is the
            # official review record's own verdict.  (The *contributions* are never
            # taken from the command either: ``commit_goal_resolution`` re-derives them
            # from the store and refuses a mismatch with
            # ``COMPOUND_FACTS_CONTRADICT_STORE``.)
            # A root that *is* one primitive occurrence (no adopted method and read
            # as ``primitive`` off the network) has no compound facts to state: the
            # accept side would otherwise refuse the statement itself
            # (``METHOD_INSTANCE_NOT_ADOPTED``) before any rule ran.  A compound root
            # without an adopted method still states them and is still refused.
            compound=(
                None
                if inputs.method_instance_id is None and inputs.root_form == str(TaskForm.PRIMITIVE)
                else CompoundFacts(
                    selected_method_legal=inputs.method_instance_id is not None,
                    contributing_occurrence_ids=tuple(sorted(inputs.contributions)),
                    # 2026-09-30 真机第 6 局：两次判不下来、人裁决通过的最终审查同样算组合义务达成
                    composition_obligation_passed=accepted_or_adjudicated(
                        self.store, inputs.record
                    ),
                )
            ),
            is_mission_root=True,
            required_delivery_stage=required_delivery_stage,
            delivery_receipts=tuple(str(item) for item in delivery_receipt_ids),
            issued_by=principal.principal_id,
            scope_id=principal.scope_id,
            source=dict(source or {}),
        )
        # The accept side authenticates against its *own* principal type.  Two types
        # rather than one shared "principal" because the two sides authorise different
        # things: ``PlanPrincipal`` carries the manager epoch a plan commit is checked
        # against, and passing it here would be refused as ``BAD_PRINCIPAL`` — which is
        # exactly what it should be, since the caller would not have said whose accept
        # authority it is claiming.
        accepting = ResolutionPrincipal(
            principal_id=principal.principal_id, scope_id=principal.scope_id
        )
        try:
            receipt = self.commit.commit_goal_resolution(command, accepting)
        except ResolutionCommitRejected as error:
            return self._refuse_root_resolution(
                mission_id, inputs, command_id=command_id, reason=error.reason, detail=error.detail
            )
        finally:
            if candidate is not None:
                # Committed or refused, the candidate is history; the next attempt
                # re-prepares from the current sources (never a licence by retry).
                self.commit._assurance_validity.forget(mission_id, str(inputs.record.record_id))
        return RootResolutionOutcome(
            committed=True,
            reason="",
            resolution_id=str(receipt.resolution_id),
            detail="",
        )

    def _refuse_root_resolution(
        self,
        mission_id: str,
        inputs: RootResolutionInputs,
        *,
        command_id: str,
        reason: str,
        detail: str,
    ) -> RootResolutionOutcome:
        self._append(
            ROOT_RESOLUTION_REFUSED,
            mission_id,
            key=f"{mission_id}:{command_id}:{reason}",
            task_id=inputs.task_id,
            payload={
                "reason": reason,
                "detail": detail,
                "command_id": command_id,
                "occurrence_id": inputs.occurrence_id,
                "obligation_id": inputs.obligation_id,
            },
        )
        return RootResolutionOutcome(committed=False, reason=reason, detail=detail)

    def read_set_for_root(self, mission_id: str, inputs: RootResolutionInputs) -> SemanticReadSet:
        """The semantic read-set the root commit is checked against.

        It names what this proposal actually read: the root task's contract revision,
        the requirements revision, the manager epoch and the mission scope epoch.  The
        commit re-reads every one of them, so an epoch that moved between this read and
        the transaction refuses the command — which is the point of recording it rather
        than letting the commit assume nothing changed.
        """

        semantics = self.semantics()
        binding = semantics.task_semantics_of(mission_id, inputs.task_id)
        goal_reads: tuple[ReadItem, ...] = ()
        if binding is not None:
            goal_reads = (
                ReadItem(
                    kind=ReadItemKind.TASK,
                    id=str(binding.task_id),
                    semantic_revision=int(binding.contract_revision),
                    content_hash=binding.contract_hash,
                ),
            )
        assert inputs.requirements is not None
        return SemanticReadSet(
            requirements_revision=int(inputs.requirements.revision),
            goal_revisions=goal_reads,
            scope_epochs=(
                ScopeEpochRead(
                    scope_id="mission", validity_epoch=semantics.epoch(mission_id, "mission")
                ),
            ),
        )
