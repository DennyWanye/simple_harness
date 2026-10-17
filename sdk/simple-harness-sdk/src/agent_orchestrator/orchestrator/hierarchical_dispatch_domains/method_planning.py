# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Planner context, method applicability and the preview-and-commit lane."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...contracts import ContractError, Event, models
from ...contracts.htn import MissionRef, TaskForm, TaskRef
from ...contracts.semantic_base import content_hash_of
from ...graph.task_network import TaskNetworkSnapshot
from ...planning import plan_preview
from ...planning.htn.applicability import assess_method
from ...planning.htn.applicability import assess_method as _assess
from ...planning.htn.compiler import RefinementCompilation
from ...planning.htn.method_proposals import MAX_PROPOSED_METHOD_STEPS, build_context
from ...planning.htn.method_selection import MethodSelectionCandidateV1, filter_candidates
from ...planning.htn.planner_package import (
    MethodApplicability,
    applicability_reports,
    task_type_row,
)
from ...planning.htn.registry import AdmissionPolicy
from ...planning.htn.repair_adapter import RepairEventAdapter
from ...planning.plan_preview import preview_candidate
from ...storage import planning_decision_store
from ..operation_completion import OperationCompletionError
from ..plan_commits import (
    PLAN_REVISION_COMMITTED,
    CommitPlanCommand,
    PlanCommitRejected,
    PlanPrincipal,
)
from ..planning_admission_commits import PlanningCommitAdmission
from ..planning_backend_commit import SolverPlanCommitLane
from ..repair_impact import read_repair_impact_indexes
from .support import (
    MAX_RECORDED_REFUSALS,
    METHOD_APPLICABILITY_ASSESSED,
    REPAIR_DECISION_DISPATCHED,
    PlanRefusal,
    PlanRoundOutcome,
    append_hierarchical_event,
    criterion_statement,
)

if TYPE_CHECKING:
    from ...contracts.htn import PlanProposal
from .core import HierarchicalDispatchCore


class MethodPlanningMixin(HierarchicalDispatchCore):
    """Planner context, method applicability and the preview-and-commit lane."""

    __slots__ = ()

    if TYPE_CHECKING:

        def build_command(
            self,
            mission_id: str,
            proposal: PlanProposal,
            compilation: RefinementCompilation,
            *,
            principal: PlanPrincipal,
            command_id: str,
            source: Mapping[str, Any] | None = None,
        ) -> CommitPlanCommand: ...
        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...

    # ------------------------------------------------------------------ one plan round
    # ----------------------------------------------- context for proposing a method
    def method_proposal_context(self, mission_id: str, goal_task_id: str) -> dict[str, Any]:
        """What the Planner needs to write a method for this goal (片 A 第 4 项).

        Built from the deployment's own declarations — the operators it really
        registered, the capability table, and the four-axis report explaining why
        each existing method for this goal type does not apply — plus the original
        wording of every requirement the goal covers and the identity a new method
        should take.  Nothing about the principal or any budget account is in it.
        """

        world = self._world()
        network = self.network(mission_id)
        goal = network.binding_for_task(TaskRef(str(goal_task_id)))
        reports: dict[str, Any] = {}
        signature = goal.goal_signature
        for reference in world.registry.method_refs():
            if not world.registry.retrievable(reference, mission_id=MissionRef(mission_id)):
                continue
            contract = world.registry.definition(reference)
            if contract is None or contract.goal_type_ref.id != signature.signature_id:
                continue
            reports[f"{contract.method_id}@{int(contract.method_version)}"] = _assess(
                goal,
                contract,
                world.snapshot(),
                world.capabilities(),
                registry=world.predicates,
            )
        request = build_context(
            goal,
            world.capabilities(),
            world.registry,
            catalog=world.catalog,
            reports=reports,
            mission_id=mission_id,
        )

        # criterion_evidence 里有任务要求原文的编号配它自己的原文，不配同一句总目标：
        # 否则模型看不出哪个编号是"跑测试"、哪个是"写出 README.md"，会把文件要求链到错的步骤。
        requirements = self.semantics().latest_requirements_revision(mission_id)
        statements = (
            {}
            if requirements is None
            else {str(item.criterion_id): str(item.statement) for item in requirements.criteria}
        )
        if statements:
            request = replace(
                request,
                criterion_evidence=tuple(
                    {
                        "id": item["id"],
                        "evidence_requirement": (
                            criterion_statement(statements[item["id"]])
                            if item["id"] in statements
                            else item["evidence_requirement"]
                        ),
                    }
                    for item in request.criterion_evidence
                ),
            )
        # 片 B：中间目标的类型不声明判据，它负责的是上级做法分给它的要求（原编号、用户原话）。
        # 读不出分给它的那一份时如实写进材料（criterion_share_unreadable），不当成"没有"。
        # 按需导入：循环依赖（hierarchical_dispatch ↔ assurance_check_policy）
        from ..assurance_check_policy import goal_share

        listed = {item["id"] for item in request.criterion_evidence}
        unreadable: dict[str, str] | None = None
        try:
            share = goal_share(self.store, mission_id, goal)
        except OperationCompletionError as error:
            share, unreadable = (), {"code": str(error.code), "detail": str(error)}
        handed = [item for item in share if item in statements and item not in listed]
        if handed:
            request = replace(
                request,
                criterion_evidence=tuple(request.criterion_evidence)
                + tuple(
                    {"id": item, "evidence_requirement": criterion_statement(statements[item])}
                    for item in handed
                ),
            )
        fresh_id = "proposed-" + content_hash_of({"goal_task_id": str(goal_task_id)})[:24]
        occupied = [
            int(item.contract.method_version)
            for item in self.semantics().list_methods()
            if item.contract.method_id == fresh_id
        ]
        document = replace(
            request, new_method_identity=(fresh_id, max(occupied, default=0) + 1)
        ).to_json()
        document["subgoal_types"] = self._subgoal_types(world, signature)
        if unreadable is not None:
            document["criterion_share_unreadable"] = unreadable
        return document

    @staticmethod
    def _subgoal_types(world: Any, signature: Any) -> list[dict[str, Any]]:
        """片 B：这个目标的做法里可以放的子目标类型——比它更深一层的那些。

        层数上限由类型的层级保证（注册检查拒收同层或更浅的子目标），这里只是把"能放什么"
        如实告诉规划器；要不要再拆一层由它判断。目标类型没有层级，或没有更深的类型，就是空。
        """

        owner = next(
            (
                spec
                for spec in world.catalog.task_types()
                if spec.goal_signature.signature_id == signature.signature_id
                and int(spec.goal_signature.version) == int(signature.version)
            ),
            None,
        )
        if owner is None or owner.refinement_level is None:
            return []
        rows = []
        for spec in sorted(
            world.catalog.task_types(),
            key=lambda item: (item.refinement_level or 0, item.task_type_ref.id),
        ):
            if (
                spec.form is not TaskForm.COMPOUND
                or spec.refinement_level is None
                or spec.refinement_level <= owner.refinement_level
            ):
                continue
            rows.append({**task_type_row(spec, world.schemas), "level": int(spec.refinement_level)})
        return rows

    # ------------------------------------------------- rejected refinements (P2.3j)
    def retired_methods(self, mission_id: str) -> tuple[dict[str, Any], ...]:
        """做法在哪个目标上被采用过、又被哪次修复决定退役——事实清单（片 0 第 4 步，2026-10-01）。

        规划包据此在做法库条目上标"被退役过 + 原因"（``rejected_reasons``）。原因是规划器
        自己当时写下的理由和那次决定处理的修复请求，不是 Harness 的结论；Harness 不据此禁止
        再选这个做法。此前这里按原因分成"被最终审查拒绝""被只读步骤越权拒绝"两个标记，由
        Harness 写入并据此拦截再次采用。

        只列目标还在当前计划里的；退役它的那次计划提交找不到的（不是规划决定退役的）不列。
        """

        network = self.network(mission_id)
        present = {str(spec.occurrence_id) for spec in network.occurrences}
        commits: dict[str, Mapping[str, Any]] = {}
        addressed: dict[str, list[str]] = {}
        requests: dict[str, dict[str, Any]] = {}
        for event in self.store.iter_events(mission_id):
            if event.type == PLAN_REVISION_COMMITTED:
                for instance_id in event.payload.get("retired_method_instances", ()):
                    commits[str(instance_id)] = event.payload
            elif event.type == "PlanningRepairAddressed":
                addressed[str(event.payload.get("decision_id"))] = [
                    str(item) for item in event.payload.get("repair_request_ids", ())
                ]
            elif event.type == "PlanningRepairRequested":
                request = event.payload.get("request") or {}
                requests[str(event.payload.get("request_id"))] = {
                    "trigger_source": request.get("trigger_source"),
                    "source_key": event.payload.get("source_key"),
                }
        decisions = planning_decision_store.PlanningDecisionStore(self.store)
        rows: list[dict[str, Any]] = []
        for draft in self.semantics().list_method_instances(mission_id, state="RETIRED"):
            occurrence = str(draft.goal_occurrence_id or draft.goal_id)
            commit = commits.get(str(draft.instance_id))
            if occurrence not in present or commit is None:
                continue
            row: dict[str, Any] = {
                "method_ref": draft.method_ref.to_json(),
                "occurrence_id": occurrence,
                "retired_instance_id": str(draft.instance_id),
                "retired_at_plan_revision": int(commit.get("plan_revision", 0)),
            }
            intent_id = str((commit.get("source") or {}).get("intent_id") or "")
            decision = decisions.committed_decision_for_intent(intent_id) if intent_id else None
            if decision is not None:
                body = json.loads(decision["canonical_json"])
                row["decision_id"] = str(decision["decision_id"])
                row["rationale"] = str(body.get("rationale", ""))[:600]
                row["requests"] = [
                    requests[item]
                    for item in addressed.get(str(decision["decision_id"]), ())
                    if item in requests
                ]
            rows.append(row)
        return tuple(
            sorted(rows, key=lambda item: (item["occurrence_id"], item["retired_instance_id"]))
        )

    def planner_round_in_flight(self, mission_id: str) -> bool:
        """An open ``plan`` intent that is a *Planner* round (not a review)."""

        return any(
            intent.kind == "plan"
            and intent.mission_id == mission_id
            and str(intent.config.get("role", "")) not in {"root_reviewer"}
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            )
        )

    def method_applicability(self, mission_id: str) -> tuple[Any, ...]:
        """Why each registered method does or does not apply to each still-open goal.

        Review F16: the hierarchical Planner package always passed ``reports=()``, so
        the ``applicability`` section the package's own docstring calls load-bearing
        was empty in every deployment — the Planner was told "no method fits" with no
        axis and no reason, which is the exact state the section exists to replace.

        P2.3n: applicable verdicts are reported too.  Omitting them left a round-2
        synthesised method (empty ``applicable_when`` → APPLICABLE) present only in
        ``method_library``; the v5 prompt reads "都被 applicability 拒绝" as
        ``no_applicable_method``, and Grok H-L3-C1-r0/r1 ordinal 5 did exactly that
        while the new method sat silently in the library.

        A refined goal that a pending repair request is about is assessed as well, so
        the repair round sees which methods could replace the adopted one.
        """

        world = self._world()
        network = self.network(mission_id)
        snapshot = world.snapshot()
        capabilities = world.capabilities()
        # 按需导入：循环依赖（hierarchical_dispatch ↔ planning_repair_requests）
        from ..planning_repair_requests import repair_goal_occurrences

        repair_goals = set(repair_goal_occurrences(self.store, network))
        entries: list[Any] = []
        for spec in sorted(network.occurrences, key=lambda item: str(item.occurrence_id)):
            if spec.form is not TaskForm.COMPOUND:
                continue
            if (
                network.adopted_instance_for(spec.occurrence_id) is not None
                and str(spec.occurrence_id) not in repair_goals
            ):
                continue
            goal = network.binding_for_occurrence(spec.occurrence_id)
            signature = goal.goal_signature.signature_id
            for reference in world.registry.method_refs():
                if not world.registry.retrievable(reference, mission_id=MissionRef(mission_id)):
                    continue
                contract = world.registry.definition(reference)
                if contract is None or str(contract.goal_type_ref.id) != str(signature):
                    continue
                report = assess_method(
                    goal, contract, snapshot, capabilities, registry=world.predicates
                )
                entries.append(
                    MethodApplicability(
                        goal_occurrence_id=str(spec.occurrence_id),
                        goal_signature_id=str(signature),
                        method_ref=contract.method_ref(),
                        report=report,
                    )
                )
        return tuple(entries)

    def dispatch_repair_event(
        self,
        mission_id: str,
        event: Mapping[str, Any] | object,
        *,
        actions: Sequence[Any],
        all_items: Iterable[str] = (),
        plan_revision: int | None = None,
        **impact_indexes: Any,
    ) -> Any:
        """Enter the H4 event adapter from the authoritative dispatch context.

        The adapter remains side-effect free; callers pass the reverse indexes
        owned by their planner store, and the returned audit record is then fed
        to the existing repair/compiler boundary.  Binding the mission and
        current plan revision here prevents a repair event from being evaluated
        against an ambient or stale mission identity.
        """

        del all_items
        with self.store.read_view():
            network = self.network(mission_id)
            if plan_revision is not None and plan_revision != int(network.plan_revision):
                raise ContractError("REQUEST_BINDING_STALE: repair request uses a stale Plan")
            indexes = read_repair_impact_indexes(self.store, network, mission_id)
            # Callers may supply model diagnosis, never substitute a guessed graph.
            for name in (*indexes, "new_work"):
                impact_indexes.pop(name, None)
            result = RepairEventAdapter.dispatch(
                event,
                actions=actions,
                mission_id=mission_id,
                plan_revision=int(network.plan_revision),
                **indexes,
                **impact_indexes,
            )
        append_hierarchical_event(
            self.store,
            REPAIR_DECISION_DISPATCHED,
            mission_id,
            key=result.request.idempotency_key,
            payload=result.audit_json(),
        )
        return result

    def method_candidates(
        self, mission_id: str, *, reports: Sequence[Any] | None = None
    ) -> Mapping[str, Any]:
        """Per open goal: its candidates after the program filter (片 A 第 3 项).

        Every compound occurrence with no adopted method is listed, including one with
        no candidate at all.  The filter is capability and type only (the applicability
        read); which candidate to use, or whether to propose a new method, is the
        Planner's.  No model call, no plan mutation, nothing persisted.
        """

        network = self.network(mission_id)
        current_reports = (
            tuple(reports) if reports is not None else self.method_applicability(mission_id)
        )
        by_occurrence: dict[str, list[MethodSelectionCandidateV1]] = {}
        for entry in current_reports:
            reference = entry.method_ref
            by_occurrence.setdefault(str(entry.goal_occurrence_id), []).append(
                MethodSelectionCandidateV1(
                    method_id=str(reference.method_id),
                    method_version=int(reference.version),
                    method_content_hash=str(reference.content_hash),
                    report=entry.report,
                    bindings=dict(
                        network.binding_for_occurrence(entry.goal_occurrence_id).typed_parameters
                    ),
                )
            )
        return {
            str(spec.occurrence_id): filter_candidates(
                by_occurrence.get(str(spec.occurrence_id), ())
            )
            for spec in sorted(network.occurrences, key=lambda item: str(item.occurrence_id))
            if spec.form is TaskForm.COMPOUND
            and network.adopted_instance_for(spec.occurrence_id) is None
        }

    def record_method_applicability(
        self, mission_id: str, *, reports: Sequence[Any] | None = None
    ) -> Event | None:
        """Persist why each method was refused, at the revision it was assessed against.

        G1.  :meth:`method_applicability` fed the Planner prompt and nothing else, so
        the four axes — unknown precondition, conflict, parameter mismatch, missing
        capability, missing authority — existed only inside one rendered message.  A
        reader after the fact (the Host's acceptance runner, an operator, this suite)
        had no way to ask "why was ``code.fix-by-patch`` not chosen here", which is the
        question the axes were separated for in the first place.

        ``reports`` is passed in by the caller that also renders the prompt, so the
        record and the prompt are the *same* assessment rather than two runs of it
        against a world that may have moved between them.

        Returns ``None`` when nothing was refused: an event saying "no method was
        refused" and the absence of an event are the same fact, and writing the first
        one per round would bury the rounds that have something to say.
        """

        entries = tuple(reports) if reports is not None else self.method_applicability(mission_id)
        if not entries:
            return None
        network = self.network(mission_id)
        semantics = self.semantics()
        rendered = applicability_reports(entries, limit=MAX_RECORDED_REFUSALS)
        refused: list[dict[str, Any]] = []
        applicable: list[dict[str, Any]] = []
        for item in rendered:
            cited = tuple(item.get("unknown_preconditions", ())) + tuple(
                item.get("conflicting_preconditions", ())
            )
            seen: list[str] = []
            for key in cited:
                for record in semantics.list_observations(mission_id, proposition_key=str(key)):
                    observation = str(getattr(record, "observation_id", ""))
                    if observation and observation not in seen:
                        seen.append(observation)
            # The observations that *bear on* the cited propositions, which is not the
            # same as observations that settle them: a proposition is unknown here
            # precisely because what was observed did not settle it.  Naming them is
            # what lets a reader tell "nobody looked" from "somebody looked and the
            # answer did not decide it".
            row = {**item, "observation_ids": seen}
            if str(item.get("verdict") or "") == "APPLICABLE":
                applicable.append(row)
            else:
                refused.append(row)
        revision = int(network.plan_revision)
        library_round = self.store.count_events(mission_id, "PlanningMethodProposed")
        return append_hierarchical_event(
            self.store,
            METHOD_APPLICABILITY_ASSESSED,
            mission_id,
            key=self._applicability_record_key(mission_id, revision, library_round),
            payload={
                "plan_revision": revision,
                "library_round": int(library_round),
                "refused_methods": refused,
                "applicable_methods": applicable,
                "refusal_count": len(refused),
                "truncated": len(entries) > len(rendered),
            },
        )

    def _applicability_record_key(
        self, mission_id: str, plan_revision: int, library_round: int
    ) -> str:
        """Idempotency key for one applicability assessment.

        A method proposed by the Planner changes the library without moving the plan,
        so the key moves with the number of proposals.
        """

        if int(library_round) < 1:
            return f"{mission_id}:{int(plan_revision)}"
        return f"{mission_id}:{int(plan_revision)}:library:{int(library_round)}"

    def _admission_policy(self, mission_id: str) -> Any:
        """The policy a proposed method is decided against on this deployment.

        A world that knows how to build its own policy is asked for it; anything else
        gets one assembled from the four declaration stores plus the live capability
        table, so a deployment cannot end up admitting a method against capabilities
        nobody has.
        """

        world = self._world()
        builder = getattr(world, "policy", None)
        if callable(builder):
            policy = builder(mission_id=mission_id)
        else:
            policy = AdmissionPolicy(
                policy_ref="deployment-policy",
                policy_version=1,
                mission_id=MissionRef(mission_id),
                predicates=world.predicates,
                task_types=world.catalog,
                schemas=world.schemas,
                capabilities=world.capabilities(),
            )
        # A method the Planner proposes is capped at the method-width bound.

        current = int(getattr(policy, "max_steps", MAX_PROPOSED_METHOD_STEPS))
        if current > MAX_PROPOSED_METHOD_STEPS:
            policy = replace(policy, max_steps=MAX_PROPOSED_METHOD_STEPS)
        return policy

    def solver_preview_lane(
        self,
        mission_id: str,
        proposal: PlanProposal,
        *,
        preview: Any,
        admission: Any,
        principal: PlanPrincipal,
        command_id: str,
        source: Mapping[str, Any],
    ) -> Any:
        """Prepare H7's real compiler/Commit lane; solve outside any write transaction."""

        if not isinstance(preview, plan_preview.CandidatePreview) or not isinstance(
            admission, PlanningCommitAdmission
        ):
            raise ContractError("solver commit requires the typed preview and H1-H admission")
        self.require_hierarchical(mission_id)
        network = self.network(mission_id)
        if models.sha256_hex(
            plan_preview._source_snapshot_payload(network)
        ) != preview.source_snapshot_hash or int(proposal.expected_plan_revision) != int(
            network.plan_revision
        ):
            raise ContractError(
                "REQUEST_BINDING_STALE: solver preview no longer matches the active Plan"
            )
        command = self.build_command(
            mission_id,
            proposal,
            preview.compilation,
            principal=principal,
            command_id=command_id,
            source={
                **dict(source),
                "preview_compilation_hash": preview.compilation_hash,
                "preview_source_snapshot_hash": preview.source_snapshot_hash,
            },
        )
        return SolverPlanCommitLane(self.commit, command, principal, admission)

    def commit_preview_plan_proposal(
        self,
        mission_id: str,
        proposal: PlanProposal,
        *,
        preview: plan_preview.CandidatePreview,
        admission: Any | None = None,
        principal: PlanPrincipal,
        command_id: str,
        source: Mapping[str, Any] | None = None,
        owner: str | None = None,
        proposal_text: str = "",
    ) -> PlanRoundOutcome:
        """Commit exactly the compilation produced by the H1-H candidate preview.

        A planning decision has already frozen a typed source snapshot and a preview
        compilation; recompiling here could commit a different candidate than the one
        admitted.  This entry therefore only accepts a typed :class:`CandidatePreview`
        and the admission it was previewed under, rechecks the frozen network
        identity, and sends that compilation directly to ``CommitService``.
        """

        if not isinstance(preview, plan_preview.CandidatePreview):
            raise ContractError("SOURCE_UNAVAILABLE: final commit requires a typed preview")
        if not isinstance(admission, PlanningCommitAdmission):
            raise ContractError("SOURCE_UNAVAILABLE: final commit requires H1-H admission")
        mission = self.require_hierarchical(mission_id)
        del mission
        compilation = preview.compilation
        network = self.network(mission_id)
        current_hash = models.sha256_hex(plan_preview._source_snapshot_payload(network))
        if current_hash != preview.source_snapshot_hash:
            raise ContractError(
                "REQUEST_BINDING_STALE: preview network changed before final commit"
            )
        # ``compilation.network`` is the *result* snapshot produced by the pure
        # compiler after applying the candidate delta.  It is expected to differ
        # from the current source network; the source identity was already checked
        # above through ``preview.source_snapshot_hash``.  Comparing the result to
        # the source would reject every valid refinement immediately before commit.
        if int(proposal.expected_plan_revision) != int(network.plan_revision):
            raise ContractError(
                "REQUEST_BINDING_STALE: proposal revision changed before final commit"
            )

        command = self.build_command(
            mission_id,
            proposal,
            compilation,
            principal=principal,
            command_id=(
                command_id
                if isinstance(admission, PlanningCommitAdmission)
                and admission.taskgraph_candidate is not None
                else f"{command_id}:preview"
            ),
            source={
                **dict(source or {}),
                "preview_compilation_hash": preview.compilation_hash,
                "preview_source_snapshot_hash": preview.source_snapshot_hash,
            },
        )
        try:
            receipt = self.commit.commit_planning_revision(
                command,
                principal,
                admission=admission,
            )
        except PlanCommitRejected as refused:
            refusal = PlanRefusal(
                attempt=1,
                reason=refused.reason,
                detail=refused.detail,
                recompilable=False,
            )
            self._record_refusal(mission_id, proposal, (refusal,))
            return PlanRoundOutcome(proposal_id=proposal.proposal_id, refusals=(refusal,))
        return PlanRoundOutcome(proposal_id=proposal.proposal_id, receipt=receipt)

    @staticmethod
    def preview_plan_proposal(proposal: PlanProposal, *, inputs: Any) -> Any:
        """Run the pure candidate preview without entering dispatch or commit."""

        return preview_candidate(proposal, inputs=inputs)
