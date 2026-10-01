# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c: ``accept_review`` / ``commit_goal_resolution`` (AER §6–§7, §25.1 decision 4).

Four properties, ordered by what they would cost to get wrong:

1. **"Wrongly declared complete" = 0.**  A Mission root ``GoalResolution`` is formed
   only by ``commit_goal_resolution``, only from the success formula plus the final
   acceptance, and — when the requirements declare a delivery contract — only once a
   ``DeliveryReceipt`` says the output actually travelled that far.  A compound whose
   required child has no valid ``Acceptance`` is refused; a root requirement the
   resolution does not carry is refused **and writes nothing**.
2. **Two actions, not one.**  ``ACCEPT`` writes an ``Acceptance`` and leaves the duty
   open; only the resolution moves it to ``SATISFIED`` with its ``resolution_ref``.
3. **A commit re-checks current applicability.**  The scope epoch, the support-set
   *revision* and the ``purpose=ACCEPT`` witness are re-read inside the transaction;
   a stale one refuses with nothing written.  Independence and in-flight facts have
   to be *stated* — the command cannot be built without them.
4. **Nothing is half-written.**  A failure anywhere in the write half leaves no
   acceptance, no resolution, no receipt and an untouched duty lifecycle.

世界（2026-10-02 迁移）：这里的任务和生产一样带"协议绑定"。世界建在枢纽夹具
（``test_htn_end_to_end``）的真实世界上——已确认的根要求（纯内容，判据 ``c-root``）、真实
提交的计划（根目标 → ``leaf`` 与 ``review`` 两步，两步都细化根目标的义务）、一条真实的已
验证结果。合法的验收命令与根结论命令都**由生产装配器装出来**（把提交入口临时换成只截取
不提交的替身），每条测试再用 ``dataclasses.replace`` 逐条篡改。手工拼命令的旧世界已删。
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from dataclasses import dataclass, field
from typing import Any

import pytest
import test_htn_end_to_end as hub

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.contracts.evidence_state import (
    Availability,
    TruthValue,
    Validity,
    ValidityWitness,
    WitnessDecision,
    WitnessPurpose,
)
from agent_orchestrator.contracts.htn import (
    AbsenceRead,
    MethodInstanceId,
    ObligationId,
    ReadItem,
    ReadItemKind,
    ScopeEpochRead,
    SemanticReadSet,
    SupportSetRead,
)
from agent_orchestrator.contracts.obligations import (
    Obligation,
    ObligationLifecycle,
    ShapeChange,
)
from agent_orchestrator.contracts.resolution import (
    Acceptance,
    AcceptanceId,
    CheckExecution,
    Criterion,
    CriterionExpr,
    CriterionOrigin,
    CriterionOutcome,
    CriterionVerdict,
    DeliveryReceipt,
    DeliveryStage,
    EvaluationKind,
    RequiredEvidencePolicy,
    RequirementClass,
    RequirementsRevision,
    RequirementsRevisionId,
    ResolutionCriterion,
    ReviewBinding,
    ReviewPackage,
    ReviewPackageId,
    ReviewPurpose,
    ReviewRecord,
    ReviewRecordId,
    ReviewVerdict,
    WorkspaceAccess,
)
from agent_orchestrator.contracts.semantic_base import (
    Provenance,
    TypedRef,
    TypedRefKind,
    content_hash_of,
)
from agent_orchestrator.knowledge.validity import NO_SUBJECT
from agent_orchestrator.orchestrator._read_set import SemanticReadSetChecker
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.orchestrator.plan_commits import LEGACY_SEMANTICS
from agent_orchestrator.orchestrator.resolution_commits import (
    ACCEPTANCE_COMMITTED,
    ACCEPTANCE_KIND,
    DELIVERY_STAGE_ORDER,
    GOAL_RESOLUTION_COMMITTED,
    GOAL_RESOLUTION_KIND,
    AcceptReviewCommand,
    CommitGoalResolutionCommand,
    ResolutionCommitRejected,
    ResolutionCommitsMixin,
    ResolutionPrincipal,
    command_idempotency_key,
    delivery_reached,
)
from agent_orchestrator.runtime.output_blocks import PortClaim
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.storage.store import Store, StoreError
from agent_orchestrator.verification.acceptance_rules import (
    CompoundFacts,
    ExecutionPosture,
    IndependenceFacts,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

#: 枢纽世界的根目标与它的义务；两个子步骤都细化这条义务（``refines_parent``）。
ROOT_TASK = hub.ROOT_TASK
ROOT_DUTY = hub.ROOT_DUTY
#: 已确认的根要求里唯一的判据。
ROOT_CRITERION = hub.ROOT_CRITERION
SCOPE = "mission"
NOW_MS = 1_000_000
#: 叶子那条真实已验证结果的编号。
LEAF_RESULT = "result-1"
#: 下面几个手工构造器（``test_composition_review_consumer`` 也在用）的默认判据。
CRITERION = "c-done"
DELIVERY_CONTRACT = "delivery-contract-1"


# ======================================================================================
# The service under test: ``CommitService`` itself (P2.3c part 2 wired the mixin in)
# ======================================================================================

#: P2.3c part 1 composed ``ResolutionCommitsMixin`` onto ``CommitService`` here, in a
#: local subclass, because adding it to the real base list was part 2's job and the
#: P2.3b review was still in flight.  Part 2 did add it, so the alias now *is* the
#: service — and the assertion below is what stops the alias from quietly becoming a
#: second, divergent composition again.
ResolutionService = CommitService


def test_the_accept_side_is_wired_into_the_commit_service() -> None:
    """The two entry points are on the deployment's one writing authority (§18.2)."""

    assert issubclass(CommitService, ResolutionCommitsMixin)
    assert CommitService.accept_review is ResolutionCommitsMixin.accept_review
    assert CommitService.commit_goal_resolution is ResolutionCommitsMixin.commit_goal_resolution
    assert CommitService.record_delivery_receipt is ResolutionCommitsMixin.record_delivery_receipt


def test_the_accept_half_shares_no_private_name_with_anything_before_it_in_the_mro() -> None:
    """No base on ``CommitService`` may silently take (or shadow) an accept-side helper.

    P2.3c part 2c, review F10: this used to compare ``ResolutionCommitsMixin`` against
    ``PlanCommitsMixin`` alone.  ``CommitService`` mixes in eleven other bases and
    ``ResolutionCommitsMixin`` sits **last** in the MRO, so a name collision with any
    one of them shadows the accept-side method just as completely — and the pair-wise
    check would not see it.  The whole MRO is walked instead, which also means a base
    added later is covered without anyone remembering to extend a list.
    """

    from agent_orchestrator.orchestrator.commit_service import CommitService

    def own(kind: type) -> set[str]:
        return {name for name in vars(kind) if not name.startswith("__")}

    accept_names = own(ResolutionCommitsMixin)
    assert accept_names, "the accept half defines nothing; the guard would be vacuous"
    bases = [
        kind
        for kind in CommitService.__mro__
        if kind not in (object, CommitService, ResolutionCommitsMixin)
    ]
    assert len(bases) >= 10, f"the MRO shrank unexpectedly: {[k.__name__ for k in bases]}"
    collisions = {
        kind.__name__: sorted(own(kind) & accept_names)
        for kind in bases
        if own(kind) & accept_names
    }
    assert collisions == {}, collisions


# ======================================================================================
# Builders
# ======================================================================================


def tref(kind: TypedRefKind, ident: str, *, digest: str = HASH_A) -> TypedRef:
    return TypedRef(kind=kind, id=ident, revision=1, content_hash=digest)


def receipt_ref(ident: str) -> TypedRef:
    """A receipt the *system* attributed to a dispatched tool (AER §5.4)."""

    return TypedRef(
        kind=TypedRefKind.TOOL_RECEIPT,
        id=ident,
        revision=1,
        content_hash=HASH_B,
        produced_by=Provenance.TOOL,
    )


def criterion(
    criterion_id: str = CRITERION,
    *,
    requirement_class: RequirementClass = RequirementClass.REQUIRED_OUTCOME,
    checks: tuple[str, ...] = ("leaf-suite",),
) -> Criterion:
    return Criterion(
        criterion_id=criterion_id,
        revision=1,
        origin=CriterionOrigin.USER_EXPLICIT,
        statement=f"{criterion_id} holds",
        requirement_class=requirement_class,
        evaluation_kind=EvaluationKind.DETERMINISTIC,
        required_evidence_policy=RequiredEvidencePolicy(required_check_ids=checks),
    )


def requirements(
    mission_id: str,
    *,
    revision: int = 1,
    criteria: tuple[Criterion, ...] = (),
    delivery_contract_ref: str | None = None,
) -> RequirementsRevision:
    chosen = criteria or (criterion(),)
    expression: Any = CriterionExpr(chosen[0].criterion_id)
    if len(chosen) > 1:
        from agent_orchestrator.contracts.resolution import AllExpr

        expression = AllExpr(children=tuple(CriterionExpr(item.criterion_id) for item in chosen))
    return RequirementsRevision(
        revision_id=RequirementsRevisionId(f"req-{revision}"),
        mission_id=mission_id,
        revision=revision,
        criteria=chosen,
        success_expression=expression,
        delivery_contract_ref=delivery_contract_ref,
    )


def review_binding(
    mission_id: str, duty: str, task_id: str, manifest_hash: str, *, revision: int = 1
) -> ReviewBinding:
    return ReviewBinding(
        mission_id=mission_id,
        obligation_id=duty,
        subject_ref=tref(TypedRefKind.TASK, task_id),
        requirements_revision=revision,
        input_manifest_hash=manifest_hash,
        policy_ref=tref(TypedRefKind.SOURCE, "review-policy-1"),
    )


def review_package(
    package_id: str,
    binding: ReviewBinding,
    revision: RequirementsRevision,
    *,
    purpose: ReviewPurpose = ReviewPurpose.TASK_CONTENT,
    method_instance_id: str | None = None,
) -> ReviewPackage:
    return ReviewPackage(
        package_id=ReviewPackageId(package_id),
        purpose=purpose,
        binding=binding,
        criteria=revision.criteria,
        success_expression=revision.success_expression,
        candidate_refs=(tref(TypedRefKind.ARTIFACT, f"cand-{package_id}", digest=HASH_C),),
        producer_agent_ids=("agent-worker",),
        reviewer_workspace_access=WorkspaceAccess.READ_ONLY,
        requirements_content_hash=revision.content_hash(),
        method_instance_id=(
            None if method_instance_id is None else MethodInstanceId(method_instance_id)
        ),
    )


def review_record(
    record_id: str,
    package: ReviewPackage,
    *,
    verdict: ReviewVerdict = ReviewVerdict.ACCEPT,
    outcomes: tuple[CriterionOutcome, ...] | None = None,
) -> ReviewRecord:
    chosen = outcomes or tuple(
        CriterionOutcome(
            criterion_id=item.criterion_id,
            verdict=CriterionVerdict.PASS,
            check_execution=CheckExecution.SUCCEEDED,
            evidence_refs=(receipt_ref(f"{item.criterion_id}-receipt"),),
        )
        for item in package.criteria
    )
    return ReviewRecord(
        record_id=ReviewRecordId(record_id),
        package_id=package.package_id,
        purpose=package.purpose,
        binding=package.binding,
        reviewer_agent_id="agent-reviewer",
        reviewer_turn_id="turn-1",
        evidence_manifest_hash=HASH_A,
        criteria=chosen,
        verdict=verdict,
    )


def accept_witness(
    witness_id: str,
    task_id: str,
    *,
    purpose: WitnessPurpose = WitnessPurpose.ACCEPT,
    scope_epoch: int = 0,
    not_after_ms: int | None = None,
    freshness: Validity = Validity.CURRENT,
    truth: TruthValue = TruthValue.TRUE,
    support_revision: int = 1,
) -> ValidityWitness:
    return ValidityWitness(
        witness_id=witness_id,
        consumer_ref=tref(TypedRefKind.TASK, task_id),
        purpose=purpose,
        truth=truth,
        freshness=freshness,
        availability=Availability.READABLE,
        decision=WitnessDecision.USABLE,
        scope_id=SCOPE,
        scope_epoch=scope_epoch,
        support_revision=support_revision,
        as_of_ms=NOW_MS - 1_000,
        not_after_ms=not_after_ms,
    )


class _Captured(RuntimeError):
    """Raised by the stand-in commit entry once it holds what it was handed."""


def _capture(service: CommitService, entry: str, call: Any) -> tuple[Any, Any]:
    """合法命令取自生产装配器：把提交入口临时换成只截取、不提交的替身。

    装配器在调用提交入口之前已经把它引用的锚点（输入清单、审查包、正式审查记录、许可）
    存进库里，所以截到的命令原样提交就是一次合法的提交；测试再在它上面逐条篡改。
    """

    seen: dict[str, Any] = {}

    def grab(command: Any, principal: Any) -> Any:
        seen["command"], seen["principal"] = command, principal
        raise _Captured(entry)

    setattr(service, entry, grab)
    try:
        with pytest.raises(_Captured):
            call()
    finally:
        delattr(service, entry)
    return seen["command"], seen["principal"]


def _set_validity(world: World, acceptance_id: str, validity: Validity) -> None:
    """Move one stored Acceptance's validity (both the column and the document).

    Same shape as the hub's ``_revoke``: nothing in ``src/`` supersedes or revokes an
    Acceptance yet, and the readers that honour validity are what these tests are about.
    """

    from simple_harness.contracts import canonical_json

    moved = dataclasses.replace(world.semantics.get_acceptance(acceptance_id), validity=validity)
    document = moved.to_json()
    world.store.connection.execute(
        "UPDATE acceptances SET validity = ?, acceptance_json = ?, content_hash = ?"
        " WHERE acceptance_id = ?",
        (str(validity), canonical_json(document), content_hash_of(document), str(acceptance_id)),
    )
    world.store.connection.commit()


@dataclass
class World:
    """The hub's bound world, plus the legal commands its production assemblers built."""

    hub: Any
    delivery_contract: str | None = None
    #: Receipt ids the library refused to hold — see :meth:`delivery`.
    unrecorded: set[str] = field(default_factory=set)
    review_acceptance_id: str | None = None
    _accept: tuple[AcceptReviewCommand, ResolutionPrincipal] | None = None
    _root: tuple[CommitGoalResolutionCommand, ResolutionPrincipal] | None = None

    # -- the hub world ---------------------------------------------------------------
    @property
    def service(self) -> CommitService:
        return self.hub.service

    @property
    def mission(self) -> Any:
        return self.hub.mission

    @property
    def store(self) -> Store:
        return self.service.store

    @property
    def semantics(self) -> HtnStore:
        return HtnStore(self.service.store)

    @property
    def duties(self) -> ObligationStore:
        return ObligationStore(self.service.store)

    @property
    def leaf_task(self) -> str:
        return hub._leaf_task(self.hub)

    @property
    def review_task(self) -> str:
        return hub._review_task(self.hub)

    @property
    def leaf_occurrence(self) -> str:
        return self.hub.occurrence_of(self.leaf_task)

    @property
    def review_occurrence(self) -> str:
        return self.hub.occurrence_of(self.review_task)

    @property
    def root_occurrence(self) -> str:
        return self.hub.occurrence_of(ROOT_TASK)

    @property
    def leaf_duty(self) -> str:
        """The duty the leaf serves.  It *refines* the root's, so it is the root's duty."""

        return str(self.semantics.task_semantics_of(self.mission.id, self.leaf_task).obligation_id)

    # -- the leaf acceptance ---------------------------------------------------------
    def capture_leaf(self) -> None:
        """Re-run the production leaf assembly over the stored result and keep its command.

        Called once when the world is built.  A test that changes something the
        assembly reads (an observation moves the outputs' support revision) calls it
        again, exactly as a later assembly would have seen the world.
        """

        items = (hub._Artifact("artifact-1", "out/result.json"),)
        leaf = self.leaf_task
        declared = self.hub.dispatch.declared_output_ports_for(self.mission.id, leaf)
        claims = tuple(
            PortClaim(port_key=item["port"], path=items[index].path)
            for index, item in enumerate(declared)
            if index < len(items)
        )
        hub._seed_verified_result(
            self.hub, leaf, LEAF_RESULT, hub._passing_layers(), items=items, claims=claims,
            now_ms=NOW_MS,
        )
        self._accept = _capture(
            self.service,
            "accept_review",
            lambda: hub._assembly(self.hub).accept(
                self.mission.id,
                leaf,
                result_id=LEAF_RESULT,
                layers=hub._passing_layers(),
                artifacts=tuple(self.store.get_artifact(item.id) for item in items),
                producer_agent_ids=("agent-worker",),
                reviewer_agent_id="agent-critic",
                now_ms=NOW_MS,
                port_claims=claims,
            ),
        )

    @property
    def legal_accept(self) -> AcceptReviewCommand:
        assert self._accept is not None
        return self._accept[0]

    @property
    def acceptance_id(self) -> str:
        return self.legal_accept.acceptance_id

    @property
    def leaf_package(self) -> ReviewPackage:
        return self.legal_accept.package

    @property
    def leaf_record(self) -> ReviewRecord:
        return self.legal_accept.record

    @property
    def revision(self) -> RequirementsRevision:
        return self.legal_accept.requirements

    @property
    def manifest_hash(self) -> str:
        return self.leaf_package.binding.input_manifest_hash

    def read_set(self, **overrides: Any) -> SemanticReadSet:
        """The read-set the leaf assembly recorded, with some channels replaced."""

        return dataclasses.replace(self.legal_accept.read_set, **overrides)

    def principal(self, **overrides: Any) -> ResolutionPrincipal:
        assert self._accept is not None
        return dataclasses.replace(self._accept[1], **overrides)

    def accept_command(self, **overrides: Any) -> AcceptReviewCommand:
        return dataclasses.replace(self.legal_accept, **overrides)

    def accept(self, command: AcceptReviewCommand | None = None, principal: Any = None) -> Any:
        return self.service.accept_review(
            command if command is not None else self.accept_command(),
            principal if principal is not None else self.principal(),
        )

    # -- the root resolution ---------------------------------------------------------
    def _root_command(self) -> tuple[CommitGoalResolutionCommand, ResolutionPrincipal]:
        """The root command ``attempt_root_resolution`` assembles, once both children are in.

        The leaf is accepted by the test itself (``world.accept()``); the ``review``
        step — the finaliser the root criterion is linked to — is accepted here through
        the real chain, then the final review's anchors are stored and the trigger runs
        with its commit entry swapped for the capturing stand-in.
        """

        if self._root is None:
            try:
                self.semantics.get_acceptance(self.acceptance_id)
            except StoreError as error:
                raise AssertionError(
                    "the root command is assembled once the leaf is accepted: world.accept()"
                ) from error
            self.review_acceptance_id = hub._accept_leaf(
                self.hub,
                task_id=self.review_task,
                result_id="result-review",
                artifacts=(hub._Artifact("artifact-review", "out/verdict.json"),),
                now_ms=1_100_000,
                port_claims=(PortClaim(port_key="verdict", path="out/verdict.json"),),
            ).acceptance_id
            confirmed = hub._publish_final_requirements(self.hub, delivery=self.delivery_contract)
            package = hub._final_package(self.hub, confirmed)
            hub._final_record(self.hub, package)
            hub._final_witness(self.hub)
            self._root = _capture(
                self.service,
                "commit_goal_resolution",
                lambda: hub._offer_root(
                    self.hub,
                    required_delivery_stage=(
                        None if self.delivery_contract is None else DeliveryStage.CONFIRMED
                    ),
                ),
            )
        return self._root

    @property
    def root_package(self) -> ReviewPackage:
        return self._root_command()[0].package

    @property
    def instance(self) -> str:
        return str(self._root_command()[0].resolution.method_instance_id)

    def resolution(self, **overrides: Any) -> Any:
        return dataclasses.replace(self._root_command()[0].resolution, **overrides)

    def resolution_command(self, **overrides: Any) -> CommitGoalResolutionCommand:
        return dataclasses.replace(self._root_command()[0], **overrides)

    def resolve(
        self, command: CommitGoalResolutionCommand | None = None, principal: Any = None
    ) -> Any:
        return self.service.commit_goal_resolution(
            command if command is not None else self.resolution_command(),
            principal if principal is not None else self._root_command()[1],
        )

    def contributions(self) -> dict[str, tuple[str, ...]]:
        """Which child occurrences the commit's own reader counts as contributed."""

        children = self.semantics.list_child_occurrences(self.mission.id, self.instance)
        return ResolutionCommitsMixin._accepted_occurrences(
            self.semantics, self.duties, self.mission.id, children
        )

    # -- delivery --------------------------------------------------------------------
    def child_acceptance(self, **overrides: Any) -> Acceptance:
        """A bare Acceptance row: never a contribution (it has no completion scope),
        only something a delivery receipt can quote."""

        fields: dict[str, Any] = {
            "acceptance_id": AcceptanceId("acc-spare"),
            "mission_id": self.mission.id,
            "task_id": self.leaf_task,
            "obligation_id": ObligationId(self.leaf_duty),
            "requirements_revision": int(self.revision.revision),
            "contract_revision": 1,
            "input_manifest_hash": self.manifest_hash,
            "review_record_id": ReviewRecordId(str(self.leaf_record.record_id)),
            "accepted_at_ms": NOW_MS,
            "validity": Validity.CURRENT,
        }
        fields.update(overrides)
        return Acceptance(**fields)

    def delivery_receipt(
        self, stage: DeliveryStage, *, acceptance_id: str | None = None
    ) -> DeliveryReceipt:
        """The receipt value, unrecorded.  ``delivery()`` is what puts it in the store."""

        quoted = self.acceptance_id if acceptance_id is None else acceptance_id
        # The id carries the quoted acceptance whenever it is not the leaf's own, so
        # two receipts for the same stage but different acceptances are two records
        # rather than one overwriting the other.
        suffix = "" if quoted == self.acceptance_id else f"-{quoted}"
        return DeliveryReceipt(
            receipt_id=f"dlv-{stage!s}{suffix}".lower(),
            mission_id=self.mission.id,
            acceptance_id=AcceptanceId(quoted),
            stage=stage,
            observed_at_ms=NOW_MS,
            operation_id=(
                "op-1" if stage in {DeliveryStage.SENT, DeliveryStage.CONFIRMED} else None
            ),
        )

    def delivery(self, stage: DeliveryStage, *, acceptance_id: str | None = None) -> str:
        """Record one delivery receipt and return the **id** the command names.

        A receipt the library refuses to hold (an acceptance nobody stored, another
        Mission's) is returned as its id anyway: the gate then refuses it as "no such
        record", which is the same "a receipt is a claim until the store agrees" answer
        one level earlier.

        带绑定世界里，服务只收"效果验收"产出的 SENT / CONFIRMED 回执
        （``OP_OUTCOME_SOURCE_UNAVAILABLE``，见
        ``test_a_root_resolution_with_a_confirmed_delivery_is_formed``）。这一组测的是根结论
        的交付闸门，所以这类回执按枢纽 ``delivered`` 夹具的做法在库这一层写入——即效果验收
        本会写下的那条记录；PERSISTED / ENQUEUED 仍走服务。
        """

        receipt = self.delivery_receipt(stage, acceptance_id=acceptance_id)
        command_id = f"cmd-dlv-{receipt.receipt_id}"
        try:
            if receipt.operation_id is None:
                self.service.record_delivery_receipt(
                    self.mission.id, receipt, command_id=command_id
                )
            else:
                self.semantics.get_acceptance(str(receipt.acceptance_id))
                self.semantics.record_delivery_receipt(
                    self.mission.id,
                    receipt,
                    command_id=command_id,
                    intent_hash=content_hash_of(receipt.to_json()),
                )
        except StoreError:
            self.unrecorded.add(receipt.receipt_id)
        return receipt.receipt_id

    def spare_record(self, name: str) -> ReviewRecordId:
        """A stored (non-official) review record id for a spare acceptance.

        ``acceptances.review_record_id`` is UNIQUE and foreign keys are on, so an
        extra acceptance needs an extra record that really exists.
        """

        binding = review_binding(self.mission.id, self.leaf_duty, self.leaf_task, self.manifest_hash)
        package = review_package(f"pkg-{name}", binding, self.revision)
        self.semantics.insert_review_package(package)
        record = review_record(f"rec-{name}", package)
        self.semantics.insert_review_record(record, official=False)
        return ReviewRecordId(str(record.record_id))

    # -- reading ---------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        connection = self.store.connection
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608
            for table in ("acceptances", "goal_resolutions", "plan_commit_receipts")
        }

    def lifecycle(self, duty: str) -> ObligationLifecycle:
        return self.duties.account(self.mission.id, ObligationId(duty)).lifecycle

    def events(self, kind: str) -> list[Any]:
        return [item for item in self.store.list_events(self.mission.id) if item.type == kind]


def build_world(tmp_path: Any, *, key: str = "p23c", delivery: str | None = None) -> World:
    """The hub's bound, committed world with the leaf's verified result and legal command.

    ``hub.committed`` creates the Mission bound to the completion protocol, confirms
    the root requirements (CONTENT_ONLY, criterion ``c-root``; with ``delivery`` the
    goal also declares a delivery contract), commits the plan and admits the demand.
    """

    world = World(
        hub=hub.committed(tmp_path, key=key, demand=True, delivery=delivery),
        delivery_contract=delivery,
    )
    world.hub.dispatch.issue_input_witnesses(world.mission.id, world.hub.network(), now_ms=NOW_MS)
    world.capture_leaf()
    return world


@pytest.fixture
def world(tmp_path: Any) -> World:
    return build_world(tmp_path)


def refusal(call: Any, *args: Any, **kwargs: Any) -> str:
    return refusal_error(call, *args, **kwargs).reason


def refusal_error(call: Any, *args: Any, **kwargs: Any) -> ResolutionCommitRejected:
    with pytest.raises(ResolutionCommitRejected) as caught:
        call(*args, **kwargs)
    return caught.value


def observation(observation_id: str, proposition: str, *, polarity: bool) -> Any:
    """One stored observation.  A later one for the same proposition refutes it."""

    from agent_orchestrator.contracts.evidence_state import (
        ObservationRecord,
        QueryCompleteness,
    )

    return ObservationRecord(
        observation_id=observation_id,
        proposition_key=proposition,
        polarity=polarity,
        source_ref=receipt_ref(f"{observation_id}-source"),
        observed_at_ms=NOW_MS,
        recorded_at_ms=NOW_MS,
        coverage=(
            QueryCompleteness.BEST_EFFORT
            if polarity
            else QueryCompleteness.AUTHORITATIVE_WITH_SCOPE
        ),
        coverage_scope=None if polarity else SCOPE,
        query_watermark_ms=None if polarity else NOW_MS,
        observer_id="observer-1",
    )


def _official(world: World, record: ReviewRecord) -> ReviewRecord:
    """Store ``record`` as the official record of its package in place of the current one."""

    current = world.semantics.official_review_record(str(record.package_id))
    if current is not None:
        world.store.connection.execute(
            "UPDATE review_records SET official = 0 WHERE record_id = ?", (str(current.record_id),)
        )
    world.semantics.insert_review_record(record, official=True)
    return record


def _withdraw_demand(world: World) -> None:
    """End the Mission root's interest in the root duty through the audited entry.

    ``build_world`` admits that demand, and the store refuses to close a duty a demand
    still hangs on (``demand_admitted=0 OR lifecycle='UNSATISFIED'``); closing it by
    hand therefore withdraws the share first, as the real closing paths do.
    """

    world.service.withdraw_obligation_demand(
        world.mission.id,
        ObligationId(world.leaf_duty),
        principal="mission-submitter",
        requester={"kind": "mission_root"},
        evidence={"mission_id": world.mission.id, "reason": "test closes the duty by hand"},
    )


def _amend_requirements(world: World) -> RequirementsRevision:
    """The person amends the root requirements after the review was cut (revision 2)."""

    amended = requirements(
        world.mission.id, revision=2, criteria=(criterion(ROOT_CRITERION, checks=()),)
    )
    world.semantics.insert_requirements_revision(amended)
    return amended


# ======================================================================================
# 1. The door, identity and idempotency
# ======================================================================================


def test_a_legacy_mission_is_refused_at_the_door(world: World, tmp_path: Any) -> None:
    """The legal command, re-addressed to a legacy Mission, stops at the door."""

    (tmp_path / "legacy").mkdir()
    legacy = hub.build_world(tmp_path / "legacy", mode=LEGACY_SEMANTICS, key="legacy")
    command = world.accept_command(mission_id=legacy.mission.id)
    assert refusal(legacy.service.accept_review, command, world.principal()) == (
        "SEMANTICS_NOT_HIERARCHICAL"
    )
    assert legacy.store.connection.execute("SELECT count(*) FROM acceptances").fetchone()[0] == 0


def test_an_unsigned_command_is_not_attributed_to_the_presenter(world: World) -> None:
    assert refusal(world.accept, world.accept_command(issued_by="")) == "PRINCIPAL_MISMATCH"


def test_authorship_is_not_reassignable_at_delivery(world: World) -> None:
    command = world.accept_command(issued_by="someone-else")
    assert refusal(world.accept, command) == "PRINCIPAL_MISMATCH"


def test_a_principal_may_not_commit_into_another_scope(world: World) -> None:
    command = world.accept_command(scope_id="other-scope")
    assert refusal(world.accept, command) == "SCOPE_NOT_AUTHORIZED"


def test_identity_is_checked_before_the_receipt_is_read(world: World) -> None:
    """A forged replay of someone else's command_id must not read back their receipt."""

    world.accept()
    original = world.accept_command()
    assert refusal(world.accept, original, ResolutionPrincipal("attacker", SCOPE, 0)) == (
        "PRINCIPAL_MISMATCH"
    )


def test_the_same_accept_command_twice_is_one_acceptance_and_one_receipt(world: World) -> None:
    first = world.accept()
    before = world.counts()
    second = world.accept()
    assert second.replayed is True
    # The replay reads the stored row back; its artifact refs decode as evidence refs, so
    # the two are compared by their recorded form.
    assert second.acceptance.to_json() == first.acceptance.to_json()
    assert world.counts() == before


def test_the_same_command_id_with_another_intent_is_a_conflict(world: World) -> None:
    world.accept()
    other = world.accept_command(acceptance_id="acc-leaf-2")
    assert refusal(world.accept, other) == "COMMAND_PAYLOAD_CONFLICT"


def test_the_same_resolution_command_twice_is_one_resolution(world: World) -> None:
    world.accept()
    first = world.resolve()
    before = world.counts()
    second = world.resolve()
    assert second.replayed is True
    assert second.resolution_id == first.resolution_id
    assert world.counts() == before


def test_a_terminal_mission_accepts_no_new_acceptance(world: World) -> None:
    world.service.cancel_mission(world.mission.id)
    assert refusal(world.accept) == "MISSION_NOT_WRITABLE"


# ======================================================================================
# 2. Binding checks: contract, inputs, artefacts, review identity
# ======================================================================================


def test_a_task_with_no_semantic_binding_is_corruption(world: World) -> None:
    command = world.accept_command(task_id="task-nobody")
    assert refusal(world.accept, command) == "MISSING_SEMANTIC_BINDING"


def test_a_command_naming_another_duty_than_the_task_serves_is_refused(world: World) -> None:
    # 带绑定世界里叶子细化根目标的义务（与根同一条），所以"另一条义务"取一条它不服务的。
    command = world.accept_command(obligation_id="obl-elsewhere")
    assert refusal(world.accept, command) == "BINDING_MISMATCH"


def test_a_package_bound_to_another_subject_is_refused(world: World) -> None:
    binding = review_binding(world.mission.id, world.leaf_duty, ROOT_TASK, world.manifest_hash)
    package = review_package("pkg-other", binding, world.revision)
    world.semantics.insert_review_package(package)
    record = review_record("rec-other", package)
    world.semantics.insert_review_record(record, official=True)
    command = world.accept_command(package=package, record=record)
    assert refusal(world.accept, command) == "BINDING_MISMATCH"


def test_a_record_that_does_not_describe_its_package_is_refused(world: World) -> None:
    other = dataclasses.replace(world.leaf_record, package_id=ReviewPackageId("pkg-root"))
    command = world.accept_command(record=other)
    assert refusal(world.accept, command) == "REVIEW_IDENTITY_MISMATCH"


def test_a_purpose_may_not_be_relabelled_at_delivery(world: World) -> None:
    command = world.accept_command(purpose=ReviewPurpose.MISSION_FINAL)
    assert refusal(world.accept, command) == "REVIEW_PURPOSE_MISMATCH"


def test_a_package_that_is_not_stored_cannot_be_quoted(world: World) -> None:
    binding = review_binding(world.mission.id, world.leaf_duty, world.leaf_task, world.manifest_hash)
    package = review_package("pkg-unstored", binding, world.revision)
    command = world.accept_command(package=package, record=review_record("rec-x", package))
    assert refusal(world.accept, command) == "REVIEW_NOT_STORED"


def test_a_package_edited_on_the_way_in_is_refused_by_content_hash(world: World) -> None:
    tampered = dataclasses.replace(
        world.leaf_package,
        candidate_refs=(tref(TypedRefKind.ARTIFACT, "cand-swapped", digest=HASH_B),),
    )
    record = dataclasses.replace(world.leaf_record)
    command = world.accept_command(package=tampered, record=record)
    assert refusal(world.accept, command) == "REVIEW_CONTENT_MISMATCH"


def test_a_record_that_is_not_the_official_one_is_refused(world: World) -> None:
    second = dataclasses.replace(world.leaf_record, record_id=ReviewRecordId("rec-leaf-2"))
    world.semantics.insert_review_record(second, official=False)
    command = world.accept_command(record=second)
    assert refusal(world.accept, command) == "REVIEW_NOT_OFFICIAL"


def test_a_requirements_revision_that_is_not_stored_is_refused(world: World) -> None:
    other = requirements(world.mission.id, revision=9)
    binding = review_binding(
        world.mission.id, world.leaf_duty, world.leaf_task, world.manifest_hash, revision=9
    )
    package = review_package("pkg-r9", binding, other)
    world.semantics.insert_review_package(package)
    record = review_record("rec-r9", package)
    world.semantics.insert_review_record(record, official=True)
    command = world.accept_command(package=package, record=record, requirements=other)
    assert refusal(world.accept, command) == "REQUIREMENTS_NOT_STORED"


def test_requirements_are_never_quietly_relaxed(world: World) -> None:
    """Same revision number, different content — the stored bytes decide (I01)."""

    relaxed = dataclasses.replace(
        world.revision,
        criteria=tuple(
            dataclasses.replace(item, requirement_class=RequirementClass.PREFERENCE)
            for item in world.revision.criteria
        ),
    )
    command = world.accept_command(requirements=relaxed)
    assert refusal(world.accept, command) == "REQUIREMENTS_MISMATCH"


def test_a_package_and_a_command_that_disagree_about_the_revision_are_refused(
    world: World,
) -> None:
    command = world.accept_command(requirements=_amend_requirements(world))
    assert refusal(world.accept, command) == "REQUIREMENTS_MISMATCH"


def test_an_input_manifest_nobody_stored_is_refused(world: World) -> None:
    binding = review_binding(world.mission.id, world.leaf_duty, world.leaf_task, HASH_C)
    package = review_package("pkg-nomanifest", binding, world.revision)
    world.semantics.insert_review_package(package)
    record = review_record("rec-nomanifest", package)
    world.semantics.insert_review_record(record, official=True)
    command = world.accept_command(package=package, record=record)
    assert refusal(world.accept, command) == "INPUT_MANIFEST_UNKNOWN"


def test_a_review_that_does_not_name_a_real_result_is_refused(world: World) -> None:
    """带绑定世界新增的一道：验收引用的必须是一条真实的、已验证的结果。

    命令的 ``source["result_id"]`` 由生产装配器写入；去掉它，或换成一条库里没有的结果，
    提交都在逐字段比对审查包之前就拒绝。
    """

    unnamed = world.accept_command(source={})
    assert refusal(world.accept, unnamed) == "OP_CONTENT_REVIEW_UNAVAILABLE"
    invented = world.accept_command(source={"result_id": "result-nobody"})
    with pytest.raises(ContractError, match="no verified result"):
        world.accept(invented)
    assert world.counts()["acceptances"] == 0


# ======================================================================================
# 3. Scope epoch, read-set and the support-set revision (AER §7)
# ======================================================================================


def test_a_stale_manager_epoch_refuses_the_command(world: World) -> None:
    # The epoch table starts empty and ``bump_epoch`` writes 0 for the first row, so
    # the scope has to be raised twice to actually move past what the command read.
    world.semantics.bump_epoch(world.mission.id, SCOPE, bumped_by="manager-1")
    assert world.semantics.bump_epoch(world.mission.id, SCOPE, bumped_by="manager-1") == 1
    assert refusal(world.accept) == "MANAGER_EPOCH_STALE"


def test_a_principal_holding_an_old_epoch_is_refused(world: World) -> None:
    assert refusal(world.accept, None, world.principal(manager_epoch=5)) == "MANAGER_EPOCH_STALE"


def test_a_stale_requirements_read_refuses_the_command(world: World) -> None:
    # The review was cut at revision 1; the person has since amended the requirements.
    _amend_requirements(world)
    assert refusal(world.accept) == "READ_SET_STALE"


def test_a_stale_scope_epoch_read_refuses_the_command(world: World) -> None:
    command = world.accept_command(
        read_set=world.read_set(
            scope_epochs=(ScopeEpochRead(scope_id=SCOPE, validity_epoch=7),),
        )
    )
    assert refusal(world.accept, command) == "READ_SET_STALE"


def test_a_support_set_the_store_cannot_recheck_is_unresolved_not_stale(world: World) -> None:
    command = world.accept_command(
        read_set=world.read_set(
            support_sets=(SupportSetRead(support_set_id="ss-1", revision=1, member_digest=HASH_C),),
        )
    )
    assert refusal(world.accept, command) == "READ_SET_UNRESOLVED"


def test_a_goal_read_at_another_contract_revision_is_stale(world: World) -> None:
    command = world.accept_command(
        read_set=world.read_set(
            goal_revisions=(
                ReadItem(
                    kind=ReadItemKind.TASK,
                    id=world.leaf_task,
                    semantic_revision=9,
                    content_hash=HASH_A,
                ),
            ),
        )
    )
    assert refusal(world.accept, command) == "READ_SET_STALE"


def test_an_acceptance_read_that_no_longer_exists_is_unresolved(world: World) -> None:
    command = world.accept_command(
        read_set=world.read_set(
            acceptance_revisions=(
                ReadItem(
                    kind=ReadItemKind.ACCEPTANCE,
                    id="acc-gone",
                    semantic_revision=1,
                    content_hash=HASH_A,
                ),
            ),
        )
    )
    assert refusal(world.accept, command) == "READ_SET_UNRESOLVED"


# ======================================================================================
# 4. The purpose=ACCEPT witness (§11.5)
# ======================================================================================


def test_a_witness_nobody_stored_is_refused(world: World) -> None:
    assert refusal(world.accept, world.accept_command(witness_id="wit-nobody")) == (
        "WITNESS_UNKNOWN"
    )


def test_a_start_witness_does_not_license_an_acceptance(world: World) -> None:
    world.semantics.insert_validity_witness(
        world.mission.id,
        accept_witness(
            "wit-start", world.leaf_task, purpose=WitnessPurpose.START, support_revision=4
        ),
        subject=NO_SUBJECT,
    )
    command = world.accept_command(witness_id="wit-start")
    assert refusal(world.accept, command) == "WITNESS_PURPOSE_NOT_ACCEPT"


def test_a_witness_issued_to_another_consumer_is_that_consumers_permission(world: World) -> None:
    world.semantics.insert_validity_witness(
        world.mission.id, accept_witness("wit-root", ROOT_TASK), subject=NO_SUBJECT
    )
    command = world.accept_command(witness_id="wit-root")
    assert refusal(world.accept, command) == "WITNESS_CONSUMER_MISMATCH"


def test_a_witness_whose_deadline_has_passed_is_stale(world: World) -> None:
    world.semantics.insert_validity_witness(
        world.mission.id,
        accept_witness("wit-expired", world.leaf_task, not_after_ms=NOW_MS - 1, support_revision=2),
        subject=NO_SUBJECT,
    )
    command = world.accept_command(witness_id="wit-expired")
    assert refusal(world.accept, command) == "WITNESS_STALE"


def test_a_witness_taken_before_the_epoch_barrier_is_stale(world: World) -> None:
    world.semantics.insert_validity_witness(
        world.mission.id,
        accept_witness("wit-old-epoch", world.leaf_task, scope_epoch=3, support_revision=3),
        subject=NO_SUBJECT,
    )
    command = world.accept_command(witness_id="wit-old-epoch")
    assert refusal(world.accept, command) == "WITNESS_STALE"


# ======================================================================================
# 5. The formula: independence and in-flight facts must be stated
# ======================================================================================


def test_the_command_cannot_be_built_without_independence_facts() -> None:
    with pytest.raises(TypeError):
        AcceptReviewCommand(  # type: ignore[call-arg]
            command_id="c",
            mission_id="m",
            task_id="t",
            obligation_id="o",
            acceptance_id="a",
            package=None,  # type: ignore[arg-type]
            record=None,  # type: ignore[arg-type]
            requirements=None,  # type: ignore[arg-type]
            witness_id="w",
        )


def test_a_non_independence_facts_value_is_refused(world: World) -> None:
    with pytest.raises(ContractError, match="IndependenceFacts"):
        world.accept_command(independence=object())


def test_a_non_posture_value_is_refused(world: World) -> None:
    with pytest.raises(ContractError, match="ExecutionPosture"):
        world.accept_command(posture=object())


def test_a_self_review_is_not_acceptable(world: World) -> None:
    """The official record says the producer reviewed its own candidate.

    带绑定世界里命令陈述的产出者必须就是那次结果的真实产出者（改它会在比对审查包时以
    ``OP_CONTENT_REVIEW_UNAVAILABLE`` 拒绝，见下面的变异测试），所以"自审"在这里由正式
    审查记录表达：审查者就是产出者，公式拒绝。
    """

    record = _official(
        world,
        dataclasses.replace(
            world.leaf_record, record_id=ReviewRecordId("rec-self"), reviewer_agent_id="agent-worker"
        ),
    )
    command = world.accept_command(record=record)
    assert refusal(world.accept, command) == "NOT_ACCEPTABLE"
    assert world.counts()["acceptances"] == 0


def test_a_reviewer_who_could_edit_the_candidate_is_not_independent(world: World) -> None:
    command = world.accept_command(
        independence=IndependenceFacts(
            producer_agent_ids=world.legal_accept.independence.producer_agent_ids,
            reviewer_can_write_candidate=True,
        )
    )
    assert refusal(world.accept, command) == "NOT_ACCEPTABLE"


def test_a_rework_verdict_is_not_an_acceptance(world: World) -> None:
    record = _official(
        world,
        dataclasses.replace(
            world.leaf_record, record_id=ReviewRecordId("rec-rework"), verdict=ReviewVerdict.REWORK
        ),
    )
    command = world.accept_command(record=record)
    assert refusal(world.accept, command) == "NOT_ACCEPTABLE"


def test_a_required_check_that_never_ran_is_not_a_pass(world: World) -> None:
    """带绑定世界里审查记录要和库里记下的逐层校验结果逐字段一致：一份说"必需检查没跑"
    的正式记录与真实校验结果不符，在比对处（``OP_CONTENT_REVIEW_UNAVAILABLE``）拒绝；
    校验层本身没跑的情形由枢纽的 ``test_a_layer_that_could_not_run_is_not_a_passed_check``
    覆盖。"""

    record = _official(
        world,
        dataclasses.replace(
            world.leaf_record,
            record_id=ReviewRecordId("rec-notrun"),
            criteria=tuple(
                CriterionOutcome(
                    criterion_id=item.criterion_id,
                    verdict=CriterionVerdict.UNKNOWN,
                    check_execution=CheckExecution.NOT_RUN,
                    evidence_refs=(receipt_ref("r"),),
                )
                for item in world.leaf_record.criteria
            ),
        ),
    )
    command = world.accept_command(record=record)
    assert refusal(world.accept, command) == "OP_CONTENT_REVIEW_UNAVAILABLE"
    assert world.counts()["acceptances"] == 0


def test_an_unowned_critical_operation_blocks_the_acceptance(world: World) -> None:
    command = world.accept_command(
        posture=ExecutionPosture(unowned_critical_operation_ids=("op-9",))
    )
    assert refusal(world.accept, command) == "CRITICAL_OPERATION_UNOWNED"
    assert world.counts()["acceptances"] == 0


def test_a_pending_cancellation_blocks_the_acceptance(world: World) -> None:
    command = world.accept_command(posture=ExecutionPosture(cancellation_requested=True))
    assert refusal(world.accept, command) == "CANCELLATION_PENDING"


# ======================================================================================
# 6. The happy accept path, and what it deliberately does not do
# ======================================================================================


def test_a_clean_acceptance_is_written_with_the_bindings_it_quotes(world: World) -> None:
    receipt = world.accept()
    stored = world.semantics.get_acceptance(world.acceptance_id)
    assert stored.to_json() == receipt.acceptance.to_json()
    assert stored.input_manifest_hash == world.manifest_hash
    assert str(stored.review_record_id) == str(world.leaf_record.record_id)
    assert stored.validity is Validity.CURRENT


def test_an_acceptance_leaves_the_duty_open(world: World) -> None:
    """§25.1 decision 4: ACCEPT and GoalResolution are two actions."""

    world.accept()
    assert world.lifecycle(world.leaf_duty) is ObligationLifecycle.UNSATISFIED
    assert world.duties.account(world.mission.id, ObligationId(world.leaf_duty)).resolution_ref is None


def test_the_acceptance_event_names_the_review_account(world: World) -> None:
    world.accept()
    events = world.events(ACCEPTANCE_COMMITTED)
    assert len(events) == 1
    assert events[0].payload["review_account"] == "task"
    assert events[0].payload["acceptance_id"] == world.acceptance_id


def test_the_accept_receipt_records_the_decision_it_was_built_from(world: World) -> None:
    receipt = world.accept()
    assert receipt.decision is not None and receipt.decision.acceptable
    assert receipt.commit.output_identity["kind"] == "acceptance"
    assert receipt.replayed is False


# ======================================================================================
# 7. commit_goal_resolution: compound, coverage and the duty lifecycle
# ======================================================================================
#
# 带绑定世界里根目标的结论就是任务根的结论：``attempt_root_resolution`` 装出的
# MISSION_FINAL 命令（``is_mission_root=True``）。旧世界在根目标上拼的 COMPOSITION 命令
# 在这里没有对应物——组合审查只给非根的中间目标（``read_compound_projection``）。下面
# 每条不变量都在这条真实的根命令上表达。


def test_a_compound_with_no_child_acceptance_is_refused(world: World) -> None:
    """Both children's Acceptances are revoked after the trigger read them."""

    world.accept()
    command = world.resolution_command()
    for acceptance_id in (world.acceptance_id, world.review_acceptance_id):
        _set_validity(world, str(acceptance_id), Validity.REVOKED)
    assert refusal(world.resolve, command) == "COMPOUND_FACTS_CONTRADICT_STORE"
    assert world.counts()["goal_resolutions"] == 0


def test_a_compound_whose_required_child_is_missing_is_refused_by_the_formula(
    world: World,
) -> None:
    """The store says nothing contributed, and the command agrees — the formula refuses."""

    world.accept()
    command = world.resolution_command(
        compound=CompoundFacts(
            selected_method_legal=True,
            contributing_occurrence_ids=(),
            composition_obligation_passed=True,
        )
    )
    for acceptance_id in (world.acceptance_id, world.review_acceptance_id):
        _set_validity(world, str(acceptance_id), Validity.REVOKED)
    assert refusal(world.resolve, command) == "NOT_ACCEPTABLE"
    assert world.counts()["goal_resolutions"] == 0


def test_a_stale_child_acceptance_does_not_count_as_a_contribution(world: World) -> None:
    world.accept()
    command = world.resolution_command()
    _set_validity(world, world.acceptance_id, Validity.STALE)
    assert world.leaf_occurrence not in world.contributions()
    assert refusal(world.resolve, command) == "COMPOUND_FACTS_CONTRADICT_STORE"


def test_an_illegal_selected_method_is_refused(world: World) -> None:
    world.accept()
    command = world.resolution_command(
        compound=dataclasses.replace(
            world.resolution_command().compound, selected_method_legal=False
        )
    )
    assert refusal(world.resolve, command) == "NOT_ACCEPTABLE"


def test_a_failed_composition_obligation_is_refused(world: World) -> None:
    world.accept()
    command = world.resolution_command(
        compound=dataclasses.replace(
            world.resolution_command().compound, composition_obligation_passed=False
        )
    )
    assert refusal(world.resolve, command) == "NOT_ACCEPTABLE"


def test_a_missing_root_requirement_forms_zero_resolutions(world: World) -> None:
    world.accept()
    resolution = world.resolution(
        criteria=(ResolutionCriterion(criterion_id="c-unrelated", verdict=CriterionVerdict.PASS),)
    )
    command = world.resolution_command(resolution=resolution)
    assert refusal(world.resolve, command) == "ROOT_CRITERION_MISSING"
    assert world.counts()["goal_resolutions"] == 0
    assert world.lifecycle(ROOT_DUTY) is ObligationLifecycle.UNSATISFIED


def test_a_resolution_may_not_overrule_the_review_it_binds(world: World) -> None:
    world.accept()
    resolution = world.resolution(
        criteria=(ResolutionCriterion(criterion_id=ROOT_CRITERION, verdict=CriterionVerdict.FAIL),)
    )
    command = world.resolution_command(resolution=resolution)
    assert refusal(world.resolve, command) == "RESOLUTION_CONTRADICTS_REVIEW"


def test_a_resolution_whose_verdict_is_not_accept_is_not_a_satisfaction(world: World) -> None:
    world.accept()
    resolution = world.resolution(verdict=ReviewVerdict.INCONCLUSIVE)
    command = world.resolution_command(resolution=resolution)
    assert refusal(world.resolve, command) == "RESOLUTION_VERDICT_NOT_ACCEPT"


def test_a_resolution_pointing_at_a_retired_method_instance_is_refused(world: World) -> None:
    world.accept()
    command = world.resolution_command()
    world.semantics.set_method_instance_state(world.mission.id, world.instance, "RETIRED")
    assert refusal(world.resolve, command) == "METHOD_INSTANCE_NOT_ADOPTED"


def test_a_resolution_binding_an_unknown_child_resolution_is_refused(world: World) -> None:
    world.accept()
    resolution = world.resolution(child_resolution_ids=("res-nobody",))
    command = world.resolution_command(resolution=resolution)
    assert refusal(world.resolve, command) == "CHILD_RESOLUTION_UNKNOWN"


def test_a_duty_that_is_already_satisfied_is_not_resolved_twice(world: World) -> None:
    world.accept()
    world.resolve()
    again = world.resolution_command(
        command_id="cmd-resolve-2",
        resolution=world.resolution(resolution_id="res-root-2"),
    )
    assert refusal(world.resolve, again) == "OBLIGATION_NOT_OPEN"


def test_a_clean_resolution_satisfies_the_duty_with_its_resolution_ref(world: World) -> None:
    world.accept()
    receipt = world.resolve()
    resolution_id = str(world.resolution().resolution_id)
    account = world.duties.account(world.mission.id, ObligationId(ROOT_DUTY))
    assert account.lifecycle is ObligationLifecycle.SATISFIED
    assert account.resolution_ref == resolution_id
    assert receipt.account is not None and receipt.account.resolution_ref == resolution_id
    assert world.semantics.adopted_goal_resolution(world.mission.id, ROOT_DUTY) is not None


def test_the_resolution_event_names_the_contributing_occurrences(world: World) -> None:
    world.accept()
    world.resolve()
    events = world.events(GOAL_RESOLUTION_COMMITTED)
    assert len(events) == 1
    assert events[0].payload["contributing_occurrences"] == sorted(
        [world.leaf_occurrence, world.review_occurrence]
    )
    # 根结论由 MISSION_FINAL 审查决定，记在任务账上（旧世界的 COMPOSITION 记在父目标账上）。
    assert events[0].payload["review_account"] == "mission"


def test_an_admitted_demand_is_released_when_the_duty_is_satisfied(world: World) -> None:
    """TG decision 9: withdrawing the share is part of resolving the duty."""

    # ``build_world`` admits the root's demand the way the Mission submission does.
    assert world.duties.account(world.mission.id, ObligationId(ROOT_DUTY)).has_admitted_demand
    world.accept()
    world.resolve()
    account = world.duties.account(world.mission.id, ObligationId(ROOT_DUTY))
    assert account.lifecycle is ObligationLifecycle.SATISFIED
    assert account.has_admitted_demand is False


# ======================================================================================
# 8. The Mission-root delivery gate (§6.3, AER §6.1)
# ======================================================================================


def root_world(tmp_path: Any) -> World:
    """A world whose confirmed root requirements declare a delivery contract.

    The leaf is accepted and the root command assembled, so every test below starts
    where the trigger stands just before it commits.
    """

    world = build_world(tmp_path, key="p23c-root", delivery=DELIVERY_CONTRACT)
    world.accept()
    world.resolution_command()
    return world


def root_command(world: World, **overrides: Any) -> CommitGoalResolutionCommand:
    fields: dict[str, Any] = {
        "delivery_receipts": (world.delivery(DeliveryStage.CONFIRMED),),
    }
    fields.update(overrides)
    return world.resolution_command(**fields)


def test_the_delivery_stage_order_is_how_far_the_output_travelled() -> None:
    assert DELIVERY_STAGE_ORDER[DeliveryStage.CONFIRMED] > DELIVERY_STAGE_ORDER[DeliveryStage.SENT]
    assert DELIVERY_STAGE_ORDER[DeliveryStage.SENT] > DELIVERY_STAGE_ORDER[DeliveryStage.ENQUEUED]
    assert DELIVERY_STAGE_ORDER[DeliveryStage.FAILED] == 0


def test_a_failed_delivery_reaches_no_stage() -> None:
    receipt = DeliveryReceipt(
        receipt_id="dlv-failed",
        mission_id="m",
        acceptance_id=AcceptanceId("a"),
        stage=DeliveryStage.FAILED,
        observed_at_ms=1,
    )
    assert not delivery_reached(receipt, DeliveryStage.PERSISTED)


def test_a_root_resolution_needs_a_mission_final_review(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    command = root_command(world)
    assert command.purpose is ReviewPurpose.MISSION_FINAL
    assert world.root_package.purpose is ReviewPurpose.MISSION_FINAL
    assert world.resolve(command).resolution_id == str(world.resolution().resolution_id)


def test_a_root_resolution_without_a_required_stage_is_refused(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    command = root_command(world, required_delivery_stage=None, delivery_receipts=())
    assert refusal(world.resolve, command) == "DELIVERY_CONTRACT_UNDECLARED"
    assert world.counts()["goal_resolutions"] == 0


def test_a_root_resolution_whose_delivery_only_persisted_is_refused(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    command = root_command(world, delivery_receipts=(world.delivery(DeliveryStage.PERSISTED),))
    assert refusal(world.resolve, command) == "DELIVERY_STAGE_NOT_REACHED"
    assert world.counts()["goal_resolutions"] == 0
    assert world.lifecycle(ROOT_DUTY) is ObligationLifecycle.UNSATISFIED


def test_a_root_resolution_with_no_receipt_at_all_is_refused(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    command = root_command(world, delivery_receipts=())
    assert refusal(world.resolve, command) == "DELIVERY_STAGE_NOT_REACHED"


def test_a_receipt_quoting_an_acceptance_nobody_stored_is_invalid(tmp_path: Any) -> None:
    """Not "does not count" — refused.  A receipt is a claim until the store agrees."""

    world = root_world(tmp_path)
    command = root_command(
        world,
        delivery_receipts=(world.delivery(DeliveryStage.CONFIRMED, acceptance_id="acc-nobody"),),
    )
    assert refusal(world.resolve, command) == "DELIVERY_RECEIPT_INVALID"
    assert world.counts()["goal_resolutions"] == 0


def test_a_root_resolution_with_a_confirmed_delivery_is_formed(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    # 带绑定世界里服务只收效果验收产出的 CONFIRMED 回执；这里的回执记录是效果验收本会
    # 写下的那条（见 ``World.delivery``）。
    stray = world.delivery_receipt(DeliveryStage.CONFIRMED)
    stray = dataclasses.replace(stray, receipt_id="dlv-not-from-an-effect")
    assert (
        refusal(world.service.record_delivery_receipt, world.mission.id, stray, command_id="c-x")
        == "OP_OUTCOME_SOURCE_UNAVAILABLE"
    )
    receipt = world.resolve(root_command(world))
    assert receipt.resolution_id == str(world.resolution().resolution_id)
    assert world.lifecycle(ROOT_DUTY) is ObligationLifecycle.SATISFIED
    payload = world.events(GOAL_RESOLUTION_COMMITTED)[0].payload
    assert payload["is_mission_root"] is True
    assert payload["required_delivery_stage"] == str(DeliveryStage.CONFIRMED)
    assert payload["delivery_receipt_id"] == "dlv-confirmed"


def test_the_mission_status_string_is_never_consulted_for_the_root(tmp_path: Any) -> None:
    """A Mission row that says COMPLETED is a projection of this decision, not evidence.

    Two halves: the gate's source never reads a status at all, and a root whose
    delivery only reached SENT is refused even though every leaf is accepted.
    """

    # ``co_names`` is every global and attribute name the compiled gate actually
    # reads, so this is a statement about the code rather than about its prose.
    names = ResolutionCommitsMixin._check_delivery.__code__.co_names
    assert "status" not in names
    assert "_require_mission" not in names
    assert "stage" in names and "required_delivery_stage" in names
    world = root_world(tmp_path)
    command = root_command(world, delivery_receipts=(world.delivery(DeliveryStage.SENT),))
    assert refusal(world.resolve, command) == "DELIVERY_STAGE_NOT_REACHED"
    assert world.counts()["goal_resolutions"] == 0


def test_a_stage_the_requirements_never_asked_for_may_not_be_invented(world: World) -> None:
    """The confirmed requirements declare no delivery contract; a demanded stage is refused.

    旧世界里这条命令带着一份 COMPOSITION 审查包，先在用途比对处拒绝；带绑定世界的根命令
    本来就是 MISSION_FINAL，所以拒绝落在交付闸门本身（``DELIVERY_CONTRACT_UNDECLARED``）。
    """

    world.accept()
    command = world.resolution_command(required_delivery_stage=DeliveryStage.CONFIRMED)
    assert refusal(world.resolve, command) == "DELIVERY_CONTRACT_UNDECLARED"


# ======================================================================================
# 9. Atomicity: a failure in the write half leaves nothing behind
# ======================================================================================


def test_a_failing_event_append_rolls_back_the_acceptance(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the event store is down")

    before = world.counts()
    monkeypatch.setattr(type(world.service), "_emit", explode)
    with pytest.raises(RuntimeError):
        world.accept()
    assert world.counts() == before
    assert before["acceptances"] == 0


def test_a_failing_receipt_write_rolls_back_the_resolution_and_the_lifecycle(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.accept()
    command = world.resolution_command()
    before = world.counts()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the event store is down")

    # ``_emit`` runs *after* the resolution row and the lifecycle move, so this is a
    # failure in the second half of the write — exactly the case a partial commit
    # would leave a satisfied duty with no resolution behind it.
    monkeypatch.setattr(type(world.service), "_emit", explode)
    with pytest.raises(RuntimeError):
        world.resolve(command)
    assert world.counts() == before
    assert world.lifecycle(ROOT_DUTY) is ObligationLifecycle.UNSATISFIED


def test_a_refused_resolution_writes_no_receipt_either(world: World) -> None:
    world.accept()
    command = world.resolution_command()
    _set_validity(world, world.acceptance_id, Validity.REVOKED)
    before = world.counts()
    refusal(world.resolve, command)
    assert world.counts() == before


# ======================================================================================
# 10. Mutation self-check
# ======================================================================================


def test_mutant_accepting_without_the_witness_purpose_check_would_reuse_a_start_witness(
    world: World,
) -> None:
    world.semantics.insert_validity_witness(
        world.mission.id,
        accept_witness(
            "wit-start2", world.leaf_task, purpose=WitnessPurpose.START, support_revision=5
        ),
        subject=NO_SUBJECT,
    )
    real = refusal(world.accept, world.accept_command(witness_id="wit-start2"))
    assert real == "WITNESS_PURPOSE_NOT_ACCEPT"
    # The mutant: treat any stored witness as usable.  The acceptance then succeeds,
    # which is exactly what the real gate must prevent.
    accept_any = world.semantics.get_validity_witness(world.legal_accept.witness_id)
    assert accept_any.purpose is WitnessPurpose.ACCEPT


def test_mutant_defaulting_the_independence_facts_is_refused_not_waved_through(
    world: World,
) -> None:
    """Forgetting to look may not become acceptable (AER §5.3).

    带绑定世界里陈述的独立性事实要和那次结果的真实产出者逐字段一致，所以"谁也没产出"
    这个最宽松的默认值在比对审查身份时就拒绝（``OP_CONTENT_REVIEW_UNAVAILABLE``），
    走不到公式。
    """

    permissive = IndependenceFacts()
    assert permissive.producer_agent_ids == ()
    command = world.accept_command(independence=permissive)
    assert refusal(world.accept, command) == "OP_CONTENT_REVIEW_UNAVAILABLE"
    assert world.counts()["acceptances"] == 0
    # The same command with the facts actually gathered is acceptable.
    assert world.accept().acceptance is not None


def test_mutant_moving_the_lifecycle_on_accept_would_close_the_duty_early(
    world: World,
) -> None:
    world.accept()
    assert world.lifecycle(world.leaf_duty) is ObligationLifecycle.UNSATISFIED
    _withdraw_demand(world)
    world.duties.set_lifecycle(
        world.mission.id,
        ObligationId(world.leaf_duty),
        ObligationLifecycle.SATISFIED,
        resolution_ref=world.acceptance_id,
    )
    assert world.lifecycle(world.leaf_duty) is ObligationLifecycle.SATISFIED


def test_mutant_taking_contributions_from_the_command_would_talk_a_child_into_existence(
    world: World, tmp_path: Any
) -> None:
    world.accept()
    claimed = world.resolution_command()
    _set_validity(world, world.acceptance_id, Validity.REVOKED)
    assert refusal(world.resolve, claimed) == "COMPOUND_FACTS_CONTRADICT_STORE"
    # The same claim, in a store that does hold the acceptance, resolves.
    (tmp_path / "twin").mkdir()
    twin = build_world(tmp_path / "twin")
    twin.accept()
    twin.resolution_command()
    assert twin.resolve(claimed).resolution_id == str(claimed.resolution.resolution_id)


def test_mutant_a_root_gate_that_read_the_mission_row_would_pass_on_a_leaf(
    tmp_path: Any,
) -> None:
    world = root_world(tmp_path)
    # Every leaf finished and was accepted; the root still needs its delivery stage.
    assert world.semantics.get_acceptance(world.acceptance_id).validity is Validity.CURRENT
    command = root_command(world, delivery_receipts=(world.delivery(DeliveryStage.ENQUEUED),))
    assert refusal(world.resolve, command) == "DELIVERY_STAGE_NOT_REACHED"


def test_mutant_skipping_the_read_set_recheck_would_accept_on_a_stale_read(
    world: World,
) -> None:
    good = world.accept_command()
    stale = world.accept_command(
        command_id="cmd-accept-stale",
        read_set=world.read_set(requirements_revision=0),
    )
    assert refusal(world.accept, stale) == "READ_SET_STALE"
    assert world.accept(good).acceptance is not None


def test_mutant_reusing_one_receipt_kind_for_both_actions_would_confuse_a_replay(
    world: World,
) -> None:
    accepted = world.accept()
    assert accepted.commit.kind == ACCEPTANCE_KIND
    assert accepted.commit.subject_id == world.acceptance_id
    resolved = world.resolve()
    assert resolved.commit.kind == GOAL_RESOLUTION_KIND
    assert resolved.commit.subject_id == str(world.resolution().resolution_id)
    # A replay of the accept command may not come back as a resolution receipt.
    assert world.accept().commit.kind == ACCEPTANCE_KIND


# ======================================================================================
# 8b. Review fixes: every delivery receipt is re-read, and an invalid one refuses
# ======================================================================================


def test_a_receipt_from_another_mission_is_invalid(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    foreign = dataclasses.replace(
        world.delivery_receipt(DeliveryStage.CONFIRMED), mission_id="mission-elsewhere"
    )
    foreign = dataclasses.replace(foreign, receipt_id="dlv-elsewhere")
    # The library refuses to hold it at all, which is the same answer one step
    # earlier: a receipt for another Mission is not this Mission's record.
    assert (
        refusal(
            world.service.record_delivery_receipt,
            world.mission.id,
            foreign,
            command_id="cmd-dlv-foreign",
        )
        == "DELIVERY_RECEIPT_INVALID"
    )
    command = root_command(world, delivery_receipts=(foreign.receipt_id,))
    assert refusal(world.resolve, command) == "DELIVERY_RECEIPT_INVALID"


def test_a_receipt_quoting_a_stale_acceptance_is_invalid_not_skipped(tmp_path: Any) -> None:
    """The review's mutant R9: a STALE acceptance's receipt used to be *skipped*."""

    world = root_world(tmp_path)
    world.semantics.insert_acceptance(
        world.child_acceptance(
            acceptance_id="acc-stale",
            validity=Validity.STALE,
            review_record_id=world.spare_record("stale"),
        )
    )
    command = root_command(
        world,
        delivery_receipts=(world.delivery(DeliveryStage.CONFIRMED, acceptance_id="acc-stale"),),
    )
    caught = refusal_error(world.resolve, command)
    assert caught.reason == "DELIVERY_RECEIPT_INVALID"
    assert "STALE" in caught.detail
    assert world.counts()["goal_resolutions"] == 0


def test_a_receipt_quoting_a_revoked_acceptance_is_invalid(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    world.semantics.insert_acceptance(
        world.child_acceptance(
            acceptance_id="acc-revoked",
            validity=Validity.REVOKED,
            review_record_id=world.spare_record("revoked"),
        )
    )
    command = root_command(
        world,
        delivery_receipts=(world.delivery(DeliveryStage.CONFIRMED, acceptance_id="acc-revoked"),),
    )
    assert refusal(world.resolve, command) == "DELIVERY_RECEIPT_INVALID"


def test_a_receipt_quoting_a_duty_outside_the_root_closure_is_invalid(tmp_path: Any) -> None:
    """A receipt for other work does not deliver this goal (AER §6.3)."""

    world = root_world(tmp_path)
    world.duties.register(
        Obligation(
            obligation_id=ObligationId("obl-unrelated"),
            mission_id=world.mission.id,
            requirement_refs=(CRITERION,),
            goal_signature_id="leaf-goal",
        ),
        recursion_fuel=1,
    )
    world.semantics.insert_acceptance(
        world.child_acceptance(
            acceptance_id="acc-unrelated",
            obligation_id=ObligationId("obl-unrelated"),
            review_record_id=world.spare_record("unrelated"),
        )
    )
    command = root_command(
        world,
        delivery_receipts=(world.delivery(DeliveryStage.CONFIRMED, acceptance_id="acc-unrelated"),),
    )
    caught = refusal_error(world.resolve, command)
    assert caught.reason == "DELIVERY_RECEIPT_INVALID"
    assert "duty closure" in caught.detail


def test_one_invalid_receipt_refuses_even_when_another_would_have_done(
    tmp_path: Any,
) -> None:
    """An invalid receipt is not scanned past: the caller is claiming something wrong."""

    world = root_world(tmp_path)
    world.semantics.insert_acceptance(
        world.child_acceptance(
            acceptance_id="acc-stale2",
            validity=Validity.STALE,
            review_record_id=world.spare_record("stale2"),
        )
    )
    command = root_command(
        world,
        delivery_receipts=(
            world.delivery(DeliveryStage.CONFIRMED, acceptance_id="acc-stale2"),
            world.delivery(DeliveryStage.CONFIRMED),
        ),
    )
    assert refusal(world.resolve, command) == "DELIVERY_RECEIPT_INVALID"


def test_the_root_duty_closure_is_the_root_plus_its_adopted_children(tmp_path: Any) -> None:
    world = root_world(tmp_path)
    # The leaf's duty is inside the closure, which is why the happy root path works.
    assert world.resolve(root_command(world)).resolution_id == str(
        world.resolution().resolution_id
    )


# ======================================================================================
# 3b. Review fixes: all eleven read-set channels, one implementation
# ======================================================================================


def test_the_accept_path_and_the_plan_path_share_one_checker() -> None:
    """Two implementations drift; the review found one that had (ADR-13 clause 2)."""

    import agent_orchestrator.orchestrator.plan_commits as plan_commits
    import agent_orchestrator.orchestrator.resolution_commits as module

    assert module.SemanticReadSetChecker is SemanticReadSetChecker
    assert plan_commits.SemanticReadSetChecker is SemanticReadSetChecker
    source = inspect.getsource(ResolutionCommitsMixin._check_reads)
    assert "SemanticReadSetChecker" in source


def test_every_channel_a_read_set_can_carry_is_re_checked() -> None:
    fields = {item.name for item in dataclasses.fields(SemanticReadSet)}
    source = inspect.getsource(SemanticReadSetChecker)
    # ``manager_epoch`` is each caller's own earlier gate; every other field is here.
    for name in fields - {"manager_epoch"}:
        assert name in source, name
    # And the five the review found missing are named in ``verify`` itself.
    verified = inspect.getsource(SemanticReadSetChecker.verify)
    for name in ("method", "observation", "obligation", "authority", "absences"):
        assert name in verified, name
    assert "_check_budget_grant" in verified


def test_a_review_resting_on_a_refuted_observation_is_refused(world: World) -> None:
    """The FACT channel.  A later observation of the same proposition is the refutation."""

    first = observation("obs-1", "prop-1", polarity=True)
    world.semantics.insert_observation(world.mission.id, first)
    # The leaf assembly reads the observation count (the outputs' support revision),
    # so the legal command is the one it assembles over this world.
    world.capture_leaf()
    item = ReadItem(
        kind=ReadItemKind.FACT,
        id=first.observation_id,
        semantic_revision=1,
        content_hash=content_hash_of(first.to_json()),
    )
    clean = world.accept_command(read_set=world.read_set(observation_revisions=(item,)))
    assert world.accept(clean).acceptance is not None

    # Now somebody observes the opposite.  The row the review read is untouched, and
    # that is exactly why the *set* has to be re-checked (AER scenario I02).
    world.semantics.insert_observation(
        world.mission.id, observation("obs-2", "prop-1", polarity=False)
    )
    later = world.accept_command(
        command_id="cmd-accept-refuted",
        acceptance_id="acc-leaf-2",
        read_set=world.read_set(observation_revisions=(item,)),
    )
    caught = refusal_error(world.accept, later)
    assert caught.reason == "READ_SET_STALE"
    # The detail truncates the hash to twelve characters, so the marker is the prefix:
    # the channel reports *supersession* rather than a hash that happens to differ.
    assert "observation 'obs-1'" in caught.detail
    assert "superseded_b" in caught.detail


def test_a_review_resting_on_a_revoked_authority_is_refused(world: World) -> None:
    """The AUTHORITY channel.  A revoked approval moves its version, nothing else."""

    world.store.put_approval(
        {
            "request_id": "appr-1",
            "kind": "action",
            "mission_id": world.mission.id,
            "subject_key": world.leaf_task,
            "state": "GRANTED",
            "version": 1,
        }
    )
    granted = world.store.get_approval("appr-1")
    assert granted is not None
    item = ReadItem(
        kind=ReadItemKind.AUTHORITY,
        id="appr-1",
        semantic_revision=1,
        content_hash=content_hash_of(dict(granted)),
    )
    clean = world.accept_command(read_set=world.read_set(authority_revisions=(item,)))
    assert world.accept(clean).acceptance is not None

    world.store.put_approval(
        {
            "request_id": "appr-1",
            "kind": "action",
            "mission_id": world.mission.id,
            "subject_key": world.leaf_task,
            "state": "REVOKED",
            "version": 2,
        }
    )
    later = world.accept_command(
        command_id="cmd-accept-revoked",
        acceptance_id="acc-leaf-3",
        read_set=world.read_set(authority_revisions=(item,)),
    )
    caught = refusal_error(world.accept, later)
    assert caught.reason == "READ_SET_STALE"
    assert "authority 'appr-1'" in caught.detail


def test_a_review_resting_on_a_re_planned_duty_is_refused(world: World) -> None:
    """The OBLIGATION channel: a duty's shape history is its semantic revision."""

    duty = ObligationId(world.leaf_duty)
    account = world.duties.account(world.mission.id, duty)
    obligation = world.duties.obligation(world.mission.id, duty)
    item = ReadItem(
        kind=ReadItemKind.OBLIGATION,
        id=world.leaf_duty,
        semantic_revision=account.shape_changes,
        content_hash=content_hash_of(
            {
                "obligation": obligation.to_json(),
                "lifecycle": str(account.lifecycle),
                "resolution_ref": account.resolution_ref,
            }
        ),
    )
    clean = world.accept_command(read_set=world.read_set(obligation_revisions=(item,)))
    assert world.accept(clean).acceptance is not None

    world.duties.note_shape_change(
        world.mission.id, duty, change=ShapeChange.METHOD_SWITCHED, detail="re-planned"
    )
    later = world.accept_command(
        command_id="cmd-accept-replanned",
        acceptance_id="acc-leaf-4",
        read_set=world.read_set(obligation_revisions=(item,)),
    )
    assert refusal(world.accept, later) == "READ_SET_STALE"


def test_a_method_the_registry_suspended_is_refused(world: World) -> None:
    """The METHOD channel reports the *status*, which is more useful than a hash."""

    item = ReadItem(
        kind=ReadItemKind.METHOD,
        id="m-nobody",
        semantic_revision=1,
        content_hash=HASH_B,
    )
    command = world.accept_command(read_set=world.read_set(method_revisions=(item,)))
    assert refusal(world.accept, command) == "READ_SET_UNRESOLVED"


def test_an_absence_that_became_false_is_refused(world: World) -> None:
    """The absence channel: "there is nothing there" goes stale by becoming false."""

    clean = world.accept_command(
        read_set=world.read_set(
            absences=(
                AbsenceRead(predicate="no_obligation", scope_id="obl-nobody", range_revision=0),
            )
        )
    )
    assert world.accept(clean).acceptance is not None
    broken = world.accept_command(
        command_id="cmd-accept-absence",
        acceptance_id="acc-leaf-5",
        read_set=world.read_set(
            absences=(
                AbsenceRead(predicate="no_obligation", scope_id=world.leaf_duty, range_revision=0),
            )
        ),
    )
    caught = refusal_error(world.accept, broken)
    assert caught.reason == "READ_SET_STALE"
    assert "absence" in caught.detail


def test_an_absence_predicate_nobody_can_recheck_is_unresolved(world: World) -> None:
    command = world.accept_command(
        read_set=world.read_set(
            absences=(
                AbsenceRead(predicate="no_conflicting_operation", scope_id=SCOPE, range_revision=0),
            )
        )
    )
    caught = refusal_error(world.accept, command)
    assert caught.reason == "READ_SET_UNRESOLVED"
    assert "not one this deployment can re-check" in caught.detail


def test_a_claimed_budget_grant_revision_nobody_can_confirm_is_unresolved(
    world: World,
) -> None:
    """Fail closed: a budget the commit cannot re-read is not one it may spend against."""

    command = world.accept_command(read_set=world.read_set(budget_grant_revision=7))
    caught = refusal_error(world.accept, command)
    assert caught.reason == "READ_SET_UNRESOLVED"
    assert "budget_grant_revision" in caught.detail


def test_the_dispatch_control_channels_of_an_eligibility_read_set_resolve(
    world: World,
) -> None:
    """An accept command's read-set is the one ``build_read_set`` produced."""

    item = ReadItem(
        kind=ReadItemKind.TASK,
        id=f"{world.leaf_task}#dispatch_generation",
        semantic_revision=0,
        content_hash=content_hash_of(
            {"task": world.leaf_task, "channel": "dispatch_generation", "value": 0}
        ),
    )
    command = world.accept_command(read_set=world.read_set(goal_revisions=(item,)))
    assert world.accept(command).acceptance is not None


def test_a_dispatch_control_channel_read_at_the_wrong_value_is_stale(world: World) -> None:
    item = ReadItem(
        kind=ReadItemKind.TASK,
        id=f"{world.leaf_task}#dispatch_generation",
        semantic_revision=4,
        content_hash=content_hash_of(
            {"task": world.leaf_task, "channel": "dispatch_generation", "value": 4}
        ),
    )
    command = world.accept_command(read_set=world.read_set(goal_revisions=(item,)))
    assert refusal(world.accept, command) == "READ_SET_STALE"


# ======================================================================================
# 1b. Review fixes: reason codes, and a replay that does not scan the Mission
# ======================================================================================


def test_every_reason_code_is_upper_snake_case(world: World) -> None:
    """A reason is a machine name the caller branches on, so it has one shape."""

    seen: set[str] = set()
    for call, command in (
        (world.accept, object()),
        (world.resolve, object()),
    ):
        with pytest.raises(ResolutionCommitRejected) as caught:
            call(command, world.principal())  # type: ignore[arg-type]
        seen.add(caught.value.reason)
    seen.update({"BAD_COMMAND", "BAD_PRINCIPAL"})
    for reason in seen:
        assert reason == reason.upper(), reason
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", reason), reason


def test_a_bad_principal_is_upper_snake_too(world: World) -> None:
    with pytest.raises(ResolutionCommitRejected) as caught:
        world.service.accept_review(world.accept_command(), object())  # type: ignore[arg-type]
    assert caught.value.reason == "BAD_PRINCIPAL"


def test_the_event_key_is_derived_from_the_command_id(world: World) -> None:
    """``append_event``'s unique key *is* the one-command-one-commit guarantee."""

    receipt = world.accept()
    event = world.events(ACCEPTANCE_COMMITTED)[0]
    assert event.idempotency_key == command_idempotency_key(
        world.mission.id, world.legal_accept.command_id, ACCEPTANCE_COMMITTED
    )
    assert receipt.commit.event_id == event.id


def test_a_replay_lookup_does_not_read_the_whole_mission(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replay lookup is a keyed read into migration 17, not a scan of the log.

    P2.3c part 1 had to page the Mission's events because ``plan_commit_receipts`` is
    plan-shaped and ``storage/`` was not its to change; it got the common case down to
    one indexed ``count_events``.  Part 2 owns storage, so ``(mission_id, command_id)``
    is a primary key and the replay reads no log.

    带绑定世界里**首个**命令会读事件：比对审查包时要找回那次结果冻结的输入
    （``load_completion_result_inputs`` 逐页读任务事件）。那是验收校验本身的读，
    不是重放查找；重放在校验之前就由键值读返回，所以这里只对重放断言。
    """

    calls: list[int] = []
    real = Store.list_events

    def counted(self: Store, mission_id: str, **kwargs: Any) -> Any:
        calls.append(1)
        return real(self, mission_id, **kwargs)

    world.accept()
    monkeypatch.setattr(Store, "list_events", counted)
    assert world.accept().replayed is True
    assert calls == []


def test_a_replay_of_the_wrong_kind_is_a_conflict(world: World) -> None:
    """One command id may not come back as the other action's receipt."""

    world.accept()
    resolution = world.resolution_command(command_id=world.legal_accept.command_id)
    assert refusal(world.resolve, resolution) == "COMMAND_PAYLOAD_CONFLICT"


# ======================================================================================
# 7b. Review fixes: a contribution needs a live duty, keyed by occurrence
# ======================================================================================
#
# 带绑定世界里必需子步骤细化父目标的义务（``refines_parent``），子步骤的义务就是根目标
# 自己的义务。所以"子义务已取消 / 被取代"就是这个目标的义务已关闭：结论在清点贡献之前就
# 以 ``OBLIGATION_NOT_OPEN`` 拒绝，而提交自己的贡献读取也不再把那条验收算进去。


def test_an_acceptance_on_a_cancelled_duty_is_not_a_contribution(world: World) -> None:
    world.accept()
    command = world.resolution_command()
    assert world.leaf_occurrence in world.contributions()
    _withdraw_demand(world)
    world.duties.set_lifecycle(
        world.mission.id, ObligationId(world.leaf_duty), ObligationLifecycle.CANCELLED
    )
    assert world.contributions() == {}
    assert refusal(world.resolve, command) == "OBLIGATION_NOT_OPEN"


def test_an_acceptance_on_a_superseded_duty_is_not_a_contribution(world: World) -> None:
    world.accept()
    command = world.resolution_command(
        compound=CompoundFacts(
            selected_method_legal=True,
            contributing_occurrence_ids=(),
            composition_obligation_passed=True,
        )
    )
    _withdraw_demand(world)
    world.duties.set_lifecycle(
        world.mission.id, ObligationId(world.leaf_duty), ObligationLifecycle.SUPERSEDED
    )
    assert world.contributions() == {}
    assert refusal(world.resolve, command) == "OBLIGATION_NOT_OPEN"


def test_the_event_records_which_acceptance_carried_which_occurrence(world: World) -> None:
    world.accept()
    world.resolve()
    payload = world.events(GOAL_RESOLUTION_COMMITTED)[0].payload
    assert payload["contributing_acceptances"] == {
        world.leaf_occurrence: [world.acceptance_id],
        world.review_occurrence: [str(world.review_acceptance_id)],
    }


def test_mutant_ignoring_the_duty_lifecycle_would_count_a_cancelled_contribution(
    world: World, tmp_path: Any
) -> None:
    world.accept()
    assert world.resolve().resolution_id == str(world.resolution().resolution_id)
    # The same store with the duty cancelled first refuses, which is the difference
    # the review asked for: an acceptance is not a contribution on a dead duty.
    (tmp_path / "cancelled").mkdir()
    other = build_world(tmp_path / "cancelled", key="p23c-cancel")
    other.accept()
    command = other.resolution_command()
    _withdraw_demand(other)
    other.duties.set_lifecycle(
        other.mission.id, ObligationId(other.leaf_duty), ObligationLifecycle.CANCELLED
    )
    assert other.leaf_occurrence not in other.contributions()
    assert refusal(other.resolve, command) == "OBLIGATION_NOT_OPEN"
