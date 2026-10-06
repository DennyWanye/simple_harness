# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c part 3a: cutting the root ``MISSION_FINAL`` review, and cutting it again.

Part 2d's real-model smoke ended on one blocker and one blocker only::

    root resolution not formed: ROOT_REVIEW_PACKAGE_MISSING (no MISSION_FINAL
    ReviewPackage is stored for task task-root; the root review has not been cut,
    so there is nothing to resolve from)

``HierarchicalDispatch.root_resolution_inputs`` *reads* the three anchors a root
``GoalResolution`` is formed from — the ``MISSION_FINAL`` ``ReviewPackage``, its
official ``ReviewRecord`` and the ``purpose=ACCEPT`` witness — and reports each as
missing rather than inventing it.  Nothing in ``src`` produced them, so a
hierarchical Mission could never reach ``COMPLETED``.  This module is the deployment
side that produces them, and the whole of its design is in what it refuses to do:

* **It never writes a verdict.**  :meth:`RootReviewCoordinator.cut` freezes the
  anchor; the *conclusion* only ever arrives through
  :meth:`RootReviewCoordinator.record_review`, from a Critic reply the caller
  parsed.  AER I05 — "a review that ended is not a review that passed" — is not a
  rule this module checks, it is a shape it has: there is no code path that
  produces ``ReviewVerdict.ACCEPT`` without a reviewer having said ``PASS``.
* **It never resolves anything.**  The root resolution stays where it was
  (``attempt_root_resolution`` → ``commit_goal_resolution``), so the rule set that
  decides "is this Mission complete" is still in one place (§21.5).
* **It never re-cuts silently, and never re-cuts for ever.**  A cut is a promise
  that a reviewer saw *this* requirements revision over *these* contributions; when
  either moves the old package is superseded **with a record** and a new one is cut,
  and the number of cuts a single requirements revision may carry is bounded
  (:data:`DEFAULT_MAX_CUTS_PER_REVISION`).  Past the bound nothing is cut, a
  structured event is written, and the Mission takes part 2d's idle-stall path — a
  visible stop rather than a loop that spends a model call every cycle.

Why a re-cut is needed at all (part 2d, review P1-7).  Every leaf acceptance
publishes a Mission-level ``RequirementsRevision`` carrying *that leaf's* coverage
criteria, and the root resolution is checked against the revision its review package
was bound to.  So a leaf accepted (or revoked) after the root review was cut moves
the Mission's requirements underneath a review that never saw them, and the read-set
channel refuses the resolution with ``READ_SET_STALE``.  That refusal is correct;
the repair is to review again, which is what this module does.

The account (§13 v1.4, §21.5).  A ``MISSION_FINAL`` review's cost belongs to
``ReviewAccount.MISSION`` — :func:`~...contracts.resolution.account_for_purpose` is
the only place that mapping lives and this module quotes it rather than repeating
it.  It is deliberately **not** any Task's budget: the root review judges the
composition of every child, and charging it to whichever leaf happened to finish
last is exactly the mis-accounting §18.5 warns a new role purpose about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..contracts.htn import TaskForm, TaskSemanticBindingV1
from ..contracts.models import ContractError
from ..contracts.resolution import (
    CheckExecution,
    CriterionOutcome,
    CriterionVerdict,
    RequirementsRevision,
    ReviewAccount,
    ReviewBinding,
    ReviewPackage,
    ReviewPackageId,
    ReviewPurpose,
    ReviewRecord,
    ReviewVerdict,
    WorkspaceAccess,
    account_for_purpose,
)
from ..contracts.semantic_base import (
    Provenance,
    TypedRef,
    TypedRefKind,
    content_hash_of,
)
from ..storage.htn_store import HtnStore
from ..storage.store import StoreError
from .accepted_outputs import CarriedCriterion, carried_criteria_in_revision
from .method_library import methods_to_judge
from .hierarchical_dispatch import (
    ROOT_REVIEW_CUT,
    ROOT_REVIEW_SUPERSEDED,
    append_hierarchical_event,
)

#: The review policy this deployment cuts a root ``MISSION_FINAL`` package under.
#: One named constant rather than a literal at three call sites, so the package, the
#: record and the event cannot drift into quoting two policies.
ROOT_REVIEW_POLICY = "hierarchical-root-review-v1"

#: How many times one ``requirements_revision`` may be re-cut before the coordinator
#: stops cutting.  Three, not one: the first re-cut is the ordinary answer to a leaf
#: accepted late, and a second is the ordinary answer to a leaf revoked while the
#: first re-cut was in flight.  A third that still does not settle is no longer a
#: race — it is a world that keeps moving, and spending another model call on it is
#: how a loop that never terminates looks from the inside.
DEFAULT_MAX_CUTS_PER_REVISION = 3

#: How many times one package may be *put to* the reviewer.  Two, and the second one
#: only ever happens when the first reply could not be read: an unreadable answer is
#: not an answer, so asking again is not asking twice, and it is exactly the second
#: chance the Task Critic already gets through ``critic_schema_retry_feedback``.  A
#: reply that *was* read — PASS or FAIL — is never re-asked, whatever it said.
MAX_ROOT_REVIEW_ASKS = 2

#: :data:`~.hierarchical_dispatch.ROOT_REVIEW_CUT` and
#: :data:`~.hierarchical_dispatch.ROOT_REVIEW_SUPERSEDED` are declared beside the
#: reader that has to honour them — ``root_resolution_inputs`` picks the live package
#: and a picker that could not see a supersede record would resolve from an anchor
#: the world has moved past.  They are re-exported here because this module is the
#: one that writes them.

#: The reviewer concluded something other than ACCEPT.  §9.1's decision table, not a
#: silent retry: nothing is re-cut and nothing is resolved on the strength of it.
ROOT_REVIEW_REJECTED = "HierarchicalRootReviewRejected"

#: The reviewer's reply could not be read as a verdict.  Also **not** a retry: an
#: unreadable answer is not a conclusion, and asking again with the same package
#: would spend the Mission account on the same question (AER-V04).
ROOT_REVIEW_UNREADABLE = "HierarchicalRootReviewUnreadable"

#: One requirements revision has used up its cuts.  The Mission is left with no
#: live package, which is how it reaches part 2d's idle-stall path visibly.
ROOT_REVIEW_CUT_BUDGET_SPENT = "HierarchicalRootReviewCutBudgetSpent"


class RootReviewStatus(StrEnum):
    """Where the root review stands.  Exactly one of these is true at a time."""

    #: A gating child of the adopted root method has no accepted outcome yet.
    NOT_READY = "NOT_READY"
    #: The root duty is already resolved; there is nothing left to review.
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    #: Ready, and no package has ever been cut.
    CUT_REQUIRED = "CUT_REQUIRED"
    #: A package was cut and the world moved under it.
    RECUT_REQUIRED = "RECUT_REQUIRED"
    #: A live package is waiting for its reviewer.
    AWAITING_REVIEW = "AWAITING_REVIEW"
    #: The reviewer concluded, and did not conclude ACCEPT.
    REVIEW_REJECTED = "REVIEW_REJECTED"
    #: Two reviewers could not decide (INCONCLUSIVE): the person rules (2026-09-30).
    AWAITING_PERSON = "AWAITING_PERSON"
    #: A live package carries an official ACCEPT record: the resolution may be offered.
    READY = "READY"
    #: A re-cut is needed and this revision has no cuts left.
    CUT_BUDGET_SPENT = "CUT_BUDGET_SPENT"
    #: The plan could not be read.  Diagnosed elsewhere; never a reason to cut.
    UNREADABLE_PLAN = "UNREADABLE_PLAN"


@dataclass(frozen=True, slots=True)
class RootReviewState:
    """What :meth:`RootReviewCoordinator.state` read, and nothing it decided."""

    status: RootReviewStatus
    detail: str = ""
    task_id: str = ""
    obligation_id: str = ""
    package: ReviewPackage | None = None
    record: ReviewRecord | None = None
    #: The Mission's current requirements revision, which a fresh cut binds.
    requirements_revision: int = 0
    #: The acceptance ids that currently contribute, sorted.
    contributions: tuple[str, ...] = ()
    #: How many cuts the package's requirements revision has already spent.
    cuts_used: int = 0
    #: Why a re-cut is needed, in machine-readable form.
    stale_reasons: tuple[str, ...] = ()

    @property
    def needs_cut(self) -> bool:
        return self.status in {RootReviewStatus.CUT_REQUIRED, RootReviewStatus.RECUT_REQUIRED}

    def to_json(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "detail": self.detail,
            "task_id": self.task_id,
            "obligation_id": self.obligation_id,
            "package_id": None if self.package is None else str(self.package.package_id),
            "record_id": None if self.record is None else str(self.record.record_id),
            "requirements_revision": int(self.requirements_revision),
            "contributions": list(self.contributions),
            "cuts_used": int(self.cuts_used),
            "stale_reasons": list(self.stale_reasons),
        }


#: P2.3k verification P1-3: what an absolute path in a root parameter becomes in the
#: request.  The workspace root (the shortest absolute path among the parameters) is
#: ``<workspace>``, a path under it ``<workspace>/<relative>``, any other absolute path
#: ``<path>``.  Host file-system layout is not evidence, must not reach the model, and
#: must not move the request hash between machines.
WORKSPACE_PLACEHOLDER = "<workspace>"
PATH_PLACEHOLDER = "<path>"


def _is_absolute_path(value: str) -> bool:
    from pathlib import PurePosixPath, PureWindowsPath

    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def sanitised_goal_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """The root's typed parameters with every host path replaced by a placeholder.

    P2.3k verification P1-3.  ``typed_parameters.repository`` is the worktree's
    absolute path on the host that ran the episode; before this it went into the
    reviewer's JSON, into ``content_hash`` (the intent's ``context_version``) and so
    into every event and fixture — the same Mission state hashed differently on two
    machines, and the host's directory layout travelled into a model prompt.  The
    reviewer needs to know *which* test the parameter names, never *where* the
    checkout lives.  Pure and total: nothing else about a value is touched.
    """

    absolute = {
        item.rstrip("/")
        for item in parameters.values()
        if isinstance(item, str) and _is_absolute_path(item)
    }
    # The workspace root is the absolute value the others live under: the one that
    # prefixes the most of them, the shortest on a tie.  (Not simply the shortest —
    # an unrelated short path such as ``/var/log`` must not claim the checkout.)
    root = None
    if absolute:
        root = max(
            sorted(absolute, key=len),
            key=lambda candidate: sum(
                1 for other in absolute if other != candidate and other.startswith(candidate + "/")
            ),
        )

    def one(value: Any) -> Any:
        if isinstance(value, str):
            if not _is_absolute_path(value):
                return value
            if root is not None and (value == root or value.startswith(root + "/")):
                relative = value[len(root) :].lstrip("/")
                if not relative:
                    return WORKSPACE_PLACEHOLDER
                return f"{WORKSPACE_PLACEHOLDER}/{relative}"
            return PATH_PLACEHOLDER
        if isinstance(value, Mapping):
            return {str(key): one(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [one(item) for item in value]
        return value

    return {str(key): one(item) for key, item in parameters.items()}


def acceptance_ref(store: Any, acceptance_id: str) -> TypedRef:
    """One contributing acceptance, referenced as the candidate it is.

    ``Provenance.TOOL``: an ``Acceptance`` row is written by the Commit Service, so
    the attribution this side writes is the one ``receipt_source`` may trust.  A
    model never gets to attribute a candidate ref to the system.
    """

    # 2026-10-01（审阅升级具名后续）：此前是占位——修订号写死 1、哈希是 id 的哈希；而保证层
    # 披露给审阅员的同一条验收按不可变行钉（修订 0、正文指纹），审阅员看到两套号起疑。
    # 现在按真实验收正文构造，与披露的证据一字不差。
    acceptance = HtnStore(store).get_acceptance(str(acceptance_id))
    return TypedRef(
        kind=TypedRefKind.ACCEPTANCE,
        id=str(acceptance_id),
        revision=0,
        content_hash=content_hash_of(acceptance.to_json()),
        produced_by=Provenance.TOOL,
    )


#: What a contribution carrying no delivered artifact at all is labelled with, and
#: why.  ``none`` is a *statement* rather than an empty field: the reviewer is being
#: asked whether these parts compose into the root goal, and "this part delivered
#: nothing the plan recorded" is an answer it needs, not a blank to fill in.
@dataclass(slots=True)
class RootReviewCoordinator:
    """The deployment side of the root ``MISSION_FINAL`` review.

    Holds the store, the one Commit Service and the hierarchical assembly; holds no
    state about a Mission.  Everything it needs to know about where a Mission stands
    is read back from the library and from its own events, so a restarted process
    reaches the same conclusion as the one that cut the package.
    """

    store: Any
    commit: Any
    dispatch: Any
    scope_id: str = "mission"
    #: Who signs the package and the witness.  The **reviewer** is named on the
    #: record and comes from the reply, not from here.
    issued_by: str = "root-review-coordinator"
    max_cuts_per_revision: int = DEFAULT_MAX_CUTS_PER_REVISION

    @property
    def semantics(self) -> HtnStore:
        """A thin view over the same connection, never a cached one.

        ``HtnStore`` is a wrapper and not a session, and a cached one would keep the
        *previous* handle alive across a restart of the store — which is the shape
        that made part 2d's fixtures read a closed connection.
        """

        return HtnStore(self.store)

    @property
    def account(self) -> ReviewAccount:
        """§13 v1.4: which budget account a ``MISSION_FINAL`` review lands on."""

        return account_for_purpose(ReviewPurpose.MISSION_FINAL)

    # ------------------------------------------------------------------ reading
    def _root_binding(self, mission_id: str) -> TaskSemanticBindingV1 | None:
        network = self.dispatch.network(mission_id)
        roots = network.root_occurrence_ids
        if len(roots) != 1:
            return None
        spec = network.occurrence(roots[0])
        return self.semantics.task_semantics_of(mission_id, str(spec.task_id))

    def contributions(self, mission_id: str) -> tuple[str, ...]:
        """Every acceptance id that currently contributes, sorted.

        Read through ``HierarchicalDispatch.root_contributions``, which reads the
        ``acceptances`` rows rather than the outcome projection — an acceptance that
        was superseded or revoked since is history, and a review cut over it would
        be a review of work nobody accepts any more.
        """

        found = self.dispatch.root_contributions(mission_id)
        return tuple(sorted({str(item) for ids in found.values() for item in ids}))

    def producer_agent_ids(self, mission_id: str, acceptances: Sequence[str]) -> tuple[str, ...]:
        """Who produced the contributions, read off their own review packages.

        Not re-derived from Attempt rows and not taken from a caller: the leaf's
        ``ReviewPackage`` is the frozen anchor that recorded authorship at the time
        it was reviewed, and the independence rule is only worth anything if both
        sides read the same frozen fact (AER §5.3).
        """

        semantics = self.semantics
        found: list[str] = []
        for acceptance_id in acceptances:
            try:
                acceptance = semantics.get_acceptance(str(acceptance_id))
                stored = semantics.get_review_record(str(acceptance.review_record_id))
                package = semantics.get_review_package(str(stored.record.package_id))
            except (StoreError, AttributeError):
                # A contribution whose anchors cannot be re-read contributes no
                # authorship claim.  It does **not** contribute an empty one: the
                # package records what was readable, and the resolution's own
                # re-reads refuse a contribution the store cannot produce.
                continue
            found.extend(str(item) for item in package.producer_agent_ids)
        return tuple(sorted(set(found)))

    def _cut_events(self, mission_id: str) -> tuple[Any, ...]:
        return tuple(
            event for event in self.store.list_events(mission_id) if event.type == ROOT_REVIEW_CUT
        )

    def _source_versions_hash(self, mission_id: str) -> str | None:
        """The current active source set (path -> version) of the Mission, hashed; None
        for a domain without source roots."""
        from ..contracts.semantic_base import content_hash_of
        from ..verification.evidence_resolver import in_source_roots

        domain = self.commit.domain_for(mission_id)
        if not domain.source_roots:
            return None
        versions = {
            str(row["path"]): str(row["version_hash"])
            for row in self.store.list_sources(mission_id, active_only=True)
            if in_source_roots(row["path"], domain.source_roots)
        }
        return content_hash_of(versions)

    def superseded_package_ids(self, mission_id: str) -> frozenset[str]:
        """Package ids this coordinator has retired.  A row is never rewritten."""

        return self.dispatch.superseded_review_packages(mission_id)

    def cuts_for_revision(self, mission_id: str, revision: int) -> int:
        """How many packages have been cut against one requirements revision."""

        return sum(
            1
            for event in self._cut_events(mission_id)
            if int(event.payload.get("requirements_revision", -1)) == int(revision)
        )

    def live_package(self, mission_id: str) -> ReviewPackage | None:
        """The ``MISSION_FINAL`` package to resolve from, or ``None``.

        Delegated to :meth:`HierarchicalDispatch.live_root_review_package` rather than
        re-derived: the coordinator and the root resolution have to agree on *which*
        review is the live one, and two implementations of "which" is how they would
        stop agreeing.
        """

        binding = self._root_binding(mission_id)
        if binding is None:
            return None
        return self.dispatch.live_root_review_package(
            mission_id,
            task_id=str(binding.task_id),
            obligation_id=str(binding.obligation_id),
        )

    def stale_reasons(self, mission_id: str, package: ReviewPackage) -> tuple[str, ...]:
        """Why this package no longer describes the world, in machine-readable codes.

        Each code is a real refusal the root commit would otherwise produce:

        ``REQUIREMENTS_MOVED``
            the Mission's requirements revision is no longer the one the package was
            bound to, which the read-set channel refuses as ``READ_SET_STALE``
            (part 2d, review P1-7).
        ``CONTRIBUTIONS_MOVED``
            a child was accepted or revoked since the cut, so the candidate set the
            reviewer was shown is not the one the resolution would quote — and
            ``commit_goal_resolution`` re-derives the contributions and refuses a
            mismatch with ``COMPOUND_FACTS_CONTRADICT_STORE``.
        ``SCOPE_EPOCH_MOVED``
            the scope was re-opened under the review, which spends every witness
            taken in the old epoch (§11.5, I19).
        """

        semantics = self.semantics
        reasons: list[str] = []
        latest = semantics.latest_requirements_revision(mission_id)
        current = 0 if latest is None else int(latest.revision)
        if current != int(package.binding.requirements_revision):
            reasons.append("REQUIREMENTS_MOVED")
        cut_over = tuple(sorted(str(item.id) for item in package.child_acceptance_refs))
        if cut_over != self.contributions(mission_id):
            reasons.append("CONTRIBUTIONS_MOVED")
        recorded = next(
            (
                event
                for event in self._cut_events(mission_id)
                if str(event.payload.get("package_id", "")) == str(package.package_id)
            ),
            None,
        )
        if recorded is not None:
            was = int(recorded.payload.get("scope_epoch", 0))
            if was != int(semantics.epoch(mission_id, self.scope_id)):
                reasons.append("SCOPE_EPOCH_MOVED")
            # 架构方案 B 前置 1（用户 2026-09-29 决定）：此前终审不看资料版本，带资料的
            # 通用任务用旧资料会静默完成。切包时记下现行资料的版本集，变了就重切。
            was_sources = recorded.payload.get("source_versions_hash")
            if was_sources is not None and was_sources != self._source_versions_hash(mission_id):
                reasons.append("SOURCES_MOVED")
        from .assurance_purpose_reviews import purpose_review_key
        from .failure_classes import review_exhausted_by_interruption

        # 2026-09-29：这个包的审阅两次调用都用完、且第 2 次是被重启打断的——不是审阅员的
        # 结论。重切一个新包就是新审阅、新的 2 次机会（仍受每版切包次数上限约束）。
        key = purpose_review_key(str(package.purpose), mission_id, str(package.package_id))
        if review_exhausted_by_interruption(self.store, key):
            reasons.append("REVIEW_INTERRUPTED")
        return tuple(reasons)

    def state(self, mission_id: str) -> RootReviewState:
        """Where the root review stands.  Reads only; decides nothing and writes nothing."""

        from ..graph.projection_validation import GraphIntegrityError

        try:
            binding = self._root_binding(mission_id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            return RootReviewState(
                status=RootReviewStatus.UNREADABLE_PLAN,
                detail=f"the plan could not be read: {error}",
            )
        if binding is None:
            return RootReviewState(
                status=RootReviewStatus.UNREADABLE_PLAN,
                detail=(
                    "this plan revision has no single root occurrence with a semantic binding; "
                    "choosing among several would be this module inventing a root"
                ),
            )
        if binding.form is not TaskForm.COMPOUND and not self.dispatch.root_review_ready(
            mission_id
        ):
            return RootReviewState(
                status=RootReviewStatus.NOT_READY,
                detail="the root occurrence has no accepted outcome yet",
                task_id=str(binding.task_id),
                obligation_id=str(binding.obligation_id),
            )
        semantics = self.semantics
        resolved = semantics.adopted_goal_resolution(mission_id, str(binding.obligation_id))
        if resolved is not None:
            return RootReviewState(
                status=RootReviewStatus.ALREADY_RESOLVED,
                detail=f"duty {binding.obligation_id!s} is resolved by {resolved.resolution_id!s}",
                task_id=str(binding.task_id),
                obligation_id=str(binding.obligation_id),
            )
        if not self.dispatch.root_review_ready(mission_id):
            return RootReviewState(
                status=RootReviewStatus.NOT_READY,
                detail="a gating child of the adopted root method has no accepted outcome yet",
                task_id=str(binding.task_id),
                obligation_id=str(binding.obligation_id),
            )
        from .planning_repair_requests import source_change_open

        if source_change_open(self.store, mission_id):
            return RootReviewState(
                status=RootReviewStatus.NOT_READY,
                detail="a source change is still being assessed or awaits the planner's answer",
                task_id=str(binding.task_id),
                obligation_id=str(binding.obligation_id),
            )
        latest = semantics.latest_requirements_revision(mission_id)
        current = 0 if latest is None else int(latest.revision)
        contributions = self.contributions(mission_id)
        package = self.live_package(mission_id)
        if package is None:
            return RootReviewState(
                status=RootReviewStatus.CUT_REQUIRED,
                detail="no MISSION_FINAL package has been cut for this root",
                task_id=str(binding.task_id),
                obligation_id=str(binding.obligation_id),
                requirements_revision=current,
                contributions=contributions,
                cuts_used=self.cuts_for_revision(mission_id, current),
            )
        bound = int(package.binding.requirements_revision)
        spent = self.cuts_for_revision(mission_id, bound)
        stale = self.stale_reasons(mission_id, package)
        base = RootReviewState(
            status=RootReviewStatus.AWAITING_REVIEW,
            task_id=str(binding.task_id),
            obligation_id=str(binding.obligation_id),
            package=package,
            requirements_revision=bound,
            contributions=contributions,
            cuts_used=spent,
            stale_reasons=stale,
        )
        if stale:
            # The bound this refuses against is the *next* cut's, and the next cut
            # binds whatever revision is current — so the budget question is asked
            # about that revision and not about the one going stale.
            if self.cuts_for_revision(mission_id, current) >= int(self.max_cuts_per_revision):
                return RootReviewState(
                    status=RootReviewStatus.CUT_BUDGET_SPENT,
                    detail=(
                        f"requirements revision {current} has already been cut "
                        f"{self.cuts_for_revision(mission_id, current)} time(s) and the bound is "
                        f"{int(self.max_cuts_per_revision)}; the world keeps moving under the "
                        "review and another cut would be another model call on the same question"
                    ),
                    task_id=base.task_id,
                    obligation_id=base.obligation_id,
                    package=package,
                    requirements_revision=current,
                    contributions=contributions,
                    cuts_used=self.cuts_for_revision(mission_id, current),
                    stale_reasons=stale,
                )
            return RootReviewState(
                status=RootReviewStatus.RECUT_REQUIRED,
                detail=("the package no longer describes the world: " + ", ".join(stale)),
                task_id=base.task_id,
                obligation_id=base.obligation_id,
                package=package,
                requirements_revision=current,
                contributions=contributions,
                cuts_used=self.cuts_for_revision(mission_id, current),
                stale_reasons=stale,
            )
        record = semantics.official_review_record(str(package.package_id))
        if record is None:
            return base
        from .review_adjudication import adjudication_of

        ruling = (
            adjudication_of(semantics._store, str(record.record_id))
            if record.verdict is ReviewVerdict.INCONCLUSIVE else None
        )
        if record.verdict is ReviewVerdict.INCONCLUSIVE and ruling is None:
            # 审阅升级（2026-09-30）：复审后仍判不下来，不当"没通过"去修计划，交给人定。
            return RootReviewState(
                status=RootReviewStatus.AWAITING_PERSON,
                detail=(
                    f"the final review of {package.package_id!s} concluded INCONCLUSIVE twice; "
                    "a person decides"
                ),
                task_id=base.task_id,
                obligation_id=base.obligation_id,
                package=package,
                record=record,
                requirements_revision=bound,
                contributions=contributions,
                cuts_used=spent,
            )
        adjudicated_pass = ruling is not None and ruling.get("decision") == "pass"
        if record.verdict is not ReviewVerdict.ACCEPT and not adjudicated_pass:
            return RootReviewState(
                status=RootReviewStatus.REVIEW_REJECTED,
                detail=(
                    f"the final review of {package.package_id!s} concluded {record.verdict!s}; "
                    "a review that ended is not a review that passed (AER I05)"
                ),
                task_id=base.task_id,
                obligation_id=base.obligation_id,
                package=package,
                record=record,
                requirements_revision=bound,
                contributions=contributions,
                cuts_used=spent,
            )
        return RootReviewState(
            status=RootReviewStatus.READY,
            # Review round 4, P2-6.  ``READY`` means "there is nothing left for *this*
            # module to do", not "the Mission will resolve": the reviewer said ACCEPT,
            # and whether the criteria it reported actually satisfy §6.2's success
            # expression is ``acceptance_rules``' answer and not this one's.  A root
            # whose reviewer accepted while leaving a criterion unmet sits here and is
            # refused downstream every cycle, and an operator reading "READY" with
            # nothing happening deserves to be told which half is ready.
            detail=(
                "the reviewer concluded ACCEPT; whether its criterion verdicts satisfy the "
                "root goal's success expression is decided by commit_goal_resolution"
            ),
            task_id=base.task_id,
            obligation_id=base.obligation_id,
            package=package,
            record=record,
            requirements_revision=bound,
            contributions=contributions,
            cuts_used=spent,
        )

    # ------------------------------------------------------------------ cutting
    def cut(self, mission_id: str, *, now_ms: int) -> ReviewPackage:
        """Freeze the root ``MISSION_FINAL`` anchor, and the licence that spends it.

        Refuses unless :meth:`state` says a cut is required, so "cut it anyway" is
        not an available move: a package cut while one is live and fresh would give
        ``root_resolution_inputs`` two anchors for one root, and which one wins
        would be a matter of ordering rather than of judgement.

        What it writes: a ``RequirementsRevision`` for the root's own criteria (re-used
        rather than re-published when the content is unchanged, so the revision number
        moves exactly when the content does), the ``ReviewPackage``, and one
        :data:`ROOT_REVIEW_CUT` event recording everything the cut was made over.

        What it does **not** write: a ``ReviewRecord``.  There is no conclusion yet.
        """
        # 写输入清单、包与"已切包"事件在同一个事务里：中途出错不留没人认领的包（阶段 G）
        with self.store.transaction():
            return self._cut_locked(mission_id, now_ms=now_ms)

    def _cut_locked(self, mission_id: str, *, now_ms: int) -> ReviewPackage:

        state = self.state(mission_id)
        if not state.needs_cut:
            raise ContractError(
                f"the root review of mission {mission_id!r} is {state.status!s}, not a cut this "
                f"coordinator may make ({state.detail}); a second live package would leave "
                "which review the root resolves from a matter of row order"
            )
        binding = self._root_binding(mission_id)
        assert binding is not None  # state() answered UNREADABLE_PLAN otherwise
        semantics = self.semantics
        previous = state.package
        if previous is not None:
            self._supersede(mission_id, previous, reasons=state.stale_reasons)
        requirements = self._requirements(mission_id, binding)
        contributions = self.contributions(mission_id)
        producers = self.producer_agent_ids(mission_id, contributions)
        manifest = self._manifest_hash(mission_id, str(binding.task_id))
        package = ReviewPackage(
            package_id=ReviewPackageId(
                "pkg-root-"
                + content_hash_of(
                    {
                        "mission": str(mission_id),
                        "task": str(binding.task_id),
                        "requirements": int(requirements.revision),
                        "contributions": list(contributions),
                        "cut": self.cuts_for_revision(mission_id, int(requirements.revision)) + 1,
                    }
                )[:32]
            ),
            purpose=ReviewPurpose.MISSION_FINAL,
            binding=ReviewBinding(
                mission_id=mission_id,
                obligation_id=str(binding.obligation_id),
                subject_ref=TypedRef(
                    kind=TypedRefKind.TASK,
                    id=str(binding.task_id),
                    revision=int(binding.contract_revision),
                    content_hash=binding.contract_hash,
                ),
                requirements_revision=int(requirements.revision),
                input_manifest_hash=manifest,
                policy_ref=TypedRef(
                    kind=TypedRefKind.SOURCE,
                    id=ROOT_REVIEW_POLICY,
                    revision=1,
                    content_hash=content_hash_of(ROOT_REVIEW_POLICY),
                ),
            ),
            criteria=requirements.criteria,
            success_expression=requirements.success_expression,
            # The candidate of a composition review is the children's accepted work,
            # and it is named in both places the annex has for it: ``candidate_refs``
            # is what the reviewer is shown, ``child_acceptance_refs`` is what the
            # cut was made over and what :meth:`stale_reasons` compares against.
            candidate_refs=tuple(acceptance_ref(self.store, item) for item in contributions),
            child_acceptance_refs=tuple(acceptance_ref(self.store, item) for item in contributions),
            producer_agent_ids=producers,
            reviewer_workspace_access=WorkspaceAccess.READ_ONLY,
            requirements_content_hash=requirements.content_hash(),
            # 本任务采用的做法（阶段 C3）：终审顺带判可不可复用、是不是做法本身的错
            methods_to_judge=methods_to_judge(self.store, self.store.get_mission(mission_id)),
        )
        try:  # a package is frozen at its first cut
            package = semantics.get_review_package(str(package.package_id))
        except StoreError:
            semantics.insert_review_package(package)
        append_hierarchical_event(
            self.store,
            ROOT_REVIEW_CUT,
            mission_id,
            key=f"{mission_id}:{package.package_id}",
            task_id=str(binding.task_id),
            payload={
                "package_id": str(package.package_id),
                "purpose": str(ReviewPurpose.MISSION_FINAL),
                "review_account": str(self.account),
                "requirements_revision": int(requirements.revision),
                "requirements_content_hash": requirements.content_hash(),
                "contributions": list(contributions),
                "producer_agent_ids": list(producers),
                "input_manifest_hash": manifest,
                "scope_epoch": int(semantics.epoch(mission_id, self.scope_id)),
                "source_versions_hash": self._source_versions_hash(mission_id),
                "superseded": None if previous is None else str(previous.package_id),
                "recut_reasons": list(state.stale_reasons),
                "policy_ref": ROOT_REVIEW_POLICY,
            },
        )
        return package

    def _supersede(
        self, mission_id: str, package: ReviewPackage, *, reasons: Sequence[str]
    ) -> None:
        """Retire a package with a record.  The row itself is never touched.

        An anchor is immutable, so "this review no longer counts" cannot be a field
        on it; it is this event, and :meth:`live_package` is the one reader of it.
        Writing it *before* the new package is cut matters: a crash between the two
        leaves a Mission with no live package, which :meth:`state` answers as
        ``CUT_REQUIRED`` — one review too few, which is recoverable.  The other order
        would leave two live packages, which is one review too many.
        """

        append_hierarchical_event(
            self.store,
            ROOT_REVIEW_SUPERSEDED,
            mission_id,
            key=f"{mission_id}:{package.package_id}",
            task_id=str(package.binding.subject_ref.id),
            payload={
                "package_id": str(package.package_id),
                "requirements_revision": int(package.binding.requirements_revision),
                "reasons": list(reasons),
            },
        )

    def record_cut_budget_spent(self, mission_id: str, state: RootReviewState) -> None:
        """Say, once per revision, that this revision has no cuts left (§9.1)."""

        append_hierarchical_event(
            self.store,
            ROOT_REVIEW_CUT_BUDGET_SPENT,
            mission_id,
            key=f"{mission_id}:{state.requirements_revision}",
            task_id=state.task_id or None,
            payload={
                "code": "root_review_cut_budget_spent",
                "requirements_revision": int(state.requirements_revision),
                "cuts_used": int(state.cuts_used),
                "bound": int(self.max_cuts_per_revision),
                "stale_reasons": list(state.stale_reasons),
                "package_id": None if state.package is None else str(state.package.package_id),
                "detail": state.detail,
            },
        )

    def _requirements(
        self, mission_id: str, binding: TaskSemanticBindingV1
    ) -> RequirementsRevision:
        """The requirements revision the root's frozen completion scope names.

        Nothing is published here: the revision was confirmed before the plan was
        committed and the scope pins it.  A re-cut that changes nothing binds the same
        revision and therefore spends that revision's cut budget.
        """

        semantics = self.semantics
        from .operation_completion import OperationCompletionError, OperationCompletionReader
        from ..contracts.operation_completion import PlanRevisionPinV1

        active = semantics.active_plan_revision(mission_id)
        network = self.dispatch.network(mission_id)
        roots = tuple(network.root_occurrence_ids)
        if active is None or len(roots) != 1:
            raise OperationCompletionError(
                "OP_COMPLETION_SCOPE_UNRESOLVED", "no unique active root Scope"
            )
        plan_ref = PlanRevisionPinV1(
            revision=int(active.revision), snapshot_hash=active.snapshot_hash
        )
        reader = OperationCompletionReader(self.store)
        scope = reader.read_scope(mission_id, plan_ref, str(roots[0]))
        if scope.task_ref.id != str(binding.task_id):
            raise OperationCompletionError(
                "OP_COMPLETION_SCOPE_UNRESOLVED", "root Scope names another Task"
            )
        requirements = semantics.get_requirements_revision(
            mission_id, int(scope.requirements_ref.revision)
        )
        reader.read_requirements(
            mission_id,
            TypedRef(
                kind=TypedRefKind.REQUIREMENTS,
                id=scope.requirements_ref.id,
                revision=int(scope.requirements_ref.revision),
                content_hash=scope.requirements_ref.content_hash,
            ),
        )
        if (
            str(requirements.revision_id) != scope.requirements_ref.id
            or requirements.content_hash() != scope.requirements_ref.content_hash
        ):
            raise OperationCompletionError(
                "OP_EFFECT_SCOPE_STALE", "root Requirements differ from the frozen Scope"
            )
        return requirements

    def _manifest_hash(self, mission_id: str, task_id: str) -> str:
        """Freeze what the root consumed, and return the library's own digest.

        A compound root consumes nothing directly — its children do — so the honest
        manifest is the empty one, and it is *stored* rather than hashed in place
        because ``commit_goal_resolution`` re-reads it (``INPUT_MANIFEST_UNKNOWN``
        otherwise).  A deployment whose root does bind inputs gets them from the
        assembly, the same way the leaf side does.
        """

        document: dict[str, Any] = {"consumer_task_ref": str(task_id), "bindings": []}
        network = self.dispatch.network(mission_id)
        spec = next(
            (item for item in network.occurrences if str(item.task_id) == str(task_id)), None
        )
        if spec is not None:
            resolved = self.dispatch.resolved_inputs(mission_id, network, spec)
            if resolved.manifest is not None and resolved.manifest.is_frozen:
                document = dict(resolved.manifest.to_json())
        return self.semantics.insert_input_manifest(mission_id, str(task_id), document)


    def carried_criteria(self, mission_id: str) -> tuple[CarriedCriterion, ...]:
        """The adopted plan's ``criterion_links``, resolved to occurrences and Tasks."""

        semantics = self.semantics
        active = semantics.active_plan_revision(mission_id)
        if active is None:
            return ()
        return carried_criteria_in_revision(semantics, mission_id, int(active.revision))

    def _child_review(self, record_id: str) -> Mapping[str, Any]:
        """The judgement this contribution was accepted under, quoted not summarised.

        A composition review is asked whether *these accepted parts* add up to the
        root goal, and "accepted under what" is half of that question.  Read-only and
        best effort: a record the library cannot produce leaves the field empty rather
        than making the whole request unbuildable.
        """

        if not record_id:
            return {}
        try:
            stored = self.semantics.get_review_record(record_id)
        except (StoreError, ContractError):  # pragma: no cover - a library that lost it
            return {}
        return {
            "review_record_id": str(stored.record.record_id),
            "verdict": str(stored.record.verdict),
            "criteria": {
                str(item.criterion_id): str(item.verdict) for item in stored.record.criteria
            },
        }


    @staticmethod
    def _outcome(
        criterion_id: str, verdict: CriterionVerdict, limitations: Sequence[str]
    ) -> CriterionOutcome:
        """One criterion's outcome, with the execution axis kept honest (I07).

        A composition review is a judgement rather than a check run, but "the
        judgement reached a conclusion" is still an execution fact and the contract
        refuses a PASS whose execution says otherwise.  So a criterion the reviewer
        *judged* — either way — carries ``SUCCEEDED``, and a criterion it left alone
        carries ``NOT_RUN`` beside its ``UNKNOWN``.  There is no shape here that
        turns a criterion nobody looked at into a passed one.
        """

        resolved = CriterionVerdict(verdict)
        return CriterionOutcome(
            criterion_id=str(criterion_id),
            verdict=resolved,
            check_execution=(
                CheckExecution.NOT_RUN
                if resolved is CriterionVerdict.UNKNOWN
                else CheckExecution.SUCCEEDED
            ),
            limitations=tuple(limitations),
        )


__all__ = (
    "DEFAULT_MAX_CUTS_PER_REVISION",
    "MAX_ROOT_REVIEW_ASKS",
    "ROOT_REVIEW_CUT",
    "ROOT_REVIEW_CUT_BUDGET_SPENT",
    "ROOT_REVIEW_POLICY",
    "ROOT_REVIEW_REJECTED",
    "ROOT_REVIEW_SUPERSEDED",
    "ROOT_REVIEW_UNREADABLE",
    "RootReviewCoordinator",
    "RootReviewState",
    "RootReviewStatus",
    "acceptance_ref",
)
