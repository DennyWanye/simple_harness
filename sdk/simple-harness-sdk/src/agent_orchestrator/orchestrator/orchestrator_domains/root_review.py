# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Decisions, root review, repairs and human adjudication."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from functools import partial
from typing import Any

from ...artifacts.bound_workspace import (
    UnifiedDiffApplyError,
    decode_unified_diff_text,
    files_patched_by_unified_diff,
)
from ...assurance.codec import AssuranceError
from ...contracts import (
    TERMINAL_MISSION,
    TERMINAL_TASK,
    Artifact,
    Attempt,
    ContractError,
    Mission,
    MissionStatus,
    TaskStatus,
    ids,
)
from ...contracts.planning_decisions import HumanOptionV1, RequestHumanDecision
from ...contracts.resolution import DeliveryStage
from ...contracts.semantic_base import content_hash_of
from ...deployment.root import current_statements
from ...graph.projection_validation import GraphIntegrityError
from ...runtime.action_schema import ACTION_PREFIX
from ...scheduling.allocator import allocate_v2
from ...storage import htn_store, planning_human_store
from ...storage.store import StoreError
from ..assurance_purpose_reviews import purpose_review_key
from ..composition_review import CompositionAcceptanceAssembly
from ..failure_classes import review_exhausted_by_interruption
from ..hierarchical_dispatch import HierarchicalDispatch, append_hierarchical_event
from ..operation_runtime import (
    advance_operation_outcomes,
    dispatch_materialized_operations,
    dispatch_step_actions,
    ensure_assured_proposal_reviews,
    recover_materializations,
)
from ..plan_commits import PlanPrincipal
from ..planning_repair_requests import REQUESTED, pending_requests, record_request
from ..resolution_commits import eligible_root_receipts
from ..review_adjudication import adjudication_of
from ..root_review import RootReviewCoordinator, RootReviewStatus
from ..system_operations import prepare_system_operations
from .core import OrchestratorCore
from .support import sha256_hex_text

#: 裁决题在回答前过期：这份审查不会再有结论，如实交给规划器（同 method_plan_reviews 的口径）。
_RULING_STALE = {
    "outcome": "NO_VERDICT",
    "reason": "the question asking the person to rule on this review went stale before "
    "it was answered (the plan, the requirements or the management epoch "
    "changed); no ruling was given",
}


class RootReviewMixin(OrchestratorCore):
    """Decisions, root review, repairs and human adjudication."""

    __slots__ = ()

    async def _decide(self, mission: Mission) -> bool:
        # Review F1, before anything else: on a deployment with no assembly a Mission is
        # not scheduled and not judged.
        if self._assembly_missing(mission, at="decide"):
            return False
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return False

        if planning_human_store.PlanningHumanStore(self.store).pending(mission.id):
            return False
        tasks = self.store.list_tasks(mission.id)
        if not tasks or mission.status is not MissionStatus.ACTIVE:
            return False

        if ensure_assured_proposal_reviews(self, mission.id):
            return True

        if prepare_system_operations(self, mission.id):
            return True
        if recover_materializations(self, mission.id):
            return True
        if await dispatch_materialized_operations(self, mission.id):
            return True
        if await dispatch_step_actions(self, mission.id):
            return True
        if advance_operation_outcomes(self, mission.id):
            return True
        if any(t.paused and t.pause_reason == "provider_admission:usage_unresolved" for t in tasks):
            # Read the SDK's actual reconciliation records before importing and
            # settling; elapsed time and a new owner are not evidence of zero cost.
            if self._provider_admission is not None:
                for pool in self.assembled.pools.values():
                    guard = self._admission_for(pool.profile.profile_id)
                    if guard is not None:
                        guard.recover(pool.bridge.runtime.uow)
            self._reimport_unsettled(mission)
            resumed = any(
                self.commit.resume_admission_usage(t.id)
                for t in tasks
                if t.paused and t.pause_reason == "provider_admission:usage_unresolved"
            )
            if resumed:
                return True
        live = [  # D5-4 / R4: superseded work is history and a paused route is not required
            t
            for t in tasks
            if t.status is not TaskStatus.CANCELLED
            and not (t.paused and t.status in {TaskStatus.READY, TaskStatus.BLOCKED})
        ]
        # P2.3b / TG §7: "everything is done" is read from the projection and the
        # Resolutions, never from a sweep of ``TaskStatus`` — and the root review does
        # not run until every gating child has been accepted.
        # P2.3l / N7: form inner GoalResolutions before asking who is ready to
        # dispatch.  ORDER successors of a nested compound stay WAITING_ORDER
        # until the compound is ACCEPTED, which only a GoalResolution can say.
        try:
            new_mode.advance_compound_phases(mission.id)

            self._composition_assembly(mission, new_mode).resolve_ready(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            self._note(f"mission {mission.id}: inner composition review deferred ({error})")
        settled = new_mode.root_review_ready(mission.id)
        if settled:
            current = self.store.get_mission(mission.id)  # not the cycle's stale snapshot
            if current is None or current.status is not MissionStatus.ACTIVE:
                return False
            # P2.3c part 3a: the root ``MISSION_FINAL`` review is *cut and reviewed*
            # before the resolution is offered.  Part 2d's smoke stopped exactly here
            # — ``ROOT_REVIEW_PACKAGE_MISSING`` — because nothing in ``src`` produced
            # the anchor ``root_resolution_inputs`` reads.  The coordinator is
            # deliberately a separate step and not folded into the trigger: the
            # trigger reads anchors and never writes them, and a review that writes
            # its own conclusion is the shape §21.5 exists to forbid.
            if await self._advance_root_review(current, new_mode):
                return True
            if not await self._root_resolution_formed(current, new_mode):
                # §21.5 hard invariant, "wrongly declared complete = 0": a
                # Mission reaches COMPLETED only *after* its root GoalResolution is
                # formed, and that resolution is formed only by
                # ``commit_goal_resolution`` — out of the success formula, the final
                # acceptance and the delivery contract, never out of "every gating
                # child was accepted" (which is what ``settled`` says) and never out of
                # the Mission's own status string (§6.3, §8.1).  When it cannot be
                # formed the Mission stays ACTIVE with the refusal recorded, which is a
                # Mission that is visibly not finished rather than one wrongly declared
                # finished.  ``False`` and not ``True``: nothing moved, so the loop goes
                # idle instead of re-offering a resolution that is refused for the same
                # reason forever.
                return False
            if any(c.startswith(ACTION_PREFIX) for c in current_statements(self.store, current)):
                # Assurance §7.2：带动作的任务判定之后每轮仍走这里——批准被撤销 /
                # 过期在这里停任务，该交接的照样交接；动作被拒绝 / 没生效归系统操作线（``system_operations``：
                # 重交、问规划器或具名停），不在这里停。判定已记、收尾待收敛时它自己答"没进展"。
                return await self._decide_actions(current, live)  # D7-7' two-stage judgment
            if self.commit.assured_closeout_pending(current.id):
                # Handoff item 7: the assured Mission's success is judged and its
                # closeout is the CLOSEOUT consumer's to converge (DRAINING /
                # BLOCKED_UNKNOWN keep it ACTIVE); the unique final writer completes
                # it.  Nothing to re-judge and nothing to dispatch: idle, not stalled.
                return False
            return await self._judge(current, live)
        if await self._runtime_exhausted(mission, tasks):  # after the judge (review P2-9)
            return True
        # 任务自带的计数型停止条件——达到上限先问规划器一次，停在原地才停
        if await self._stop_conditions_reached(mission, new_mode):
            return True
        if any(task.status is TaskStatus.FAILED for task in tasks):
            return False  # the stop cascade already ended the Mission
        attempts = [a for task in tasks for a in self.store.list_attempts(task.id)]
        bound = self.policy_for(mission.id)  # step 9 (plan D9-4'): the Mission's own version
        # P2.3c part 2 / §18.5 constraint 4 / §24.1 decision 6: allocation is over
        # *admissions*, never over the READY string.  ``allocate_v2`` takes only records
        # ``admit_for_dispatch`` built out of a READY_CANDIDATE readiness report, so an
        # occurrence waiting on data, evidence, an approval or a refinement is withheld
        # with a named reason instead of quietly running.
        # P2.3c part 2b: re-read every acceptance a declared DATA edge rests on
        # and record the licence to bind it (I19: recompute rather than reuse
        # the old TRUE).  It runs *before* the readiness read because the
        # resolver looks the witness up by acceptance id; issuing it afterwards
        # would leave the consumer in WAITING_DATA for one whole cycle after its
        # producer was accepted.
        new_mode.issue_input_witnesses(
            mission.id,
            new_mode.network(mission.id),
            now_ms=int(self.store.now * 1000),
        )
        # P2.3c part 2c: the same act on the START-precondition lane, and for
        # the same reason.  A leaf under a gated method inherits its parent
        # method's ``applicable_when`` as a SELECT precondition, and TG §9
        # refuses to dispatch it without a purpose=START witness — which
        # nothing issued, so the real-model smoke committed a plan and then
        # withheld every leaf with ``witness_missing`` for ever.
        new_mode.issue_start_witnesses(
            mission.id,
            new_mode.network(mission.id),
            now_ms=int(self.store.now * 1000),
        )
        admissions = new_mode.admissions(mission.id)
        new_mode.record_withheld(mission.id, admissions)
        plan = allocate_v2(
            tasks,
            attempts,
            admissions.bindings,
            admissions.readiness,
            concurrency_limit=min(int(bound["mission_concurrency"]), self._config.max_concurrency),
            now=self.store.now,
            aging_window_seconds=float(bound["aging_window_seconds"]),
            mission_max_tokens=mission.budget.max_tokens,
            pressure=self._pressure,  # D6-3: the gate outside the §29.3 formula
            reduced_concurrency_ratio=self._config.reduced_concurrency_ratio,
            exploration_slots=int(bound["exploration_slots"]),
            weights=bound["allocator_weights"],
        )
        granted_ids = [(str(item.task_id), n) for item, n in plan.grants]
        progressed = False
        for task_id, _candidate in granted_ids:
            current_task = self.store.get_task(task_id)
            assert current_task is not None
            task = current_task
            if task.status in TERMINAL_TASK:
                continue
            score = plan.scores.get(task.id)
            if await self._next_attempt(
                mission,
                task,
                self.store.list_attempts(task.id),
                admission=admissions.admission_for(task.id),
                allocation=None
                if score is None
                else {
                    **score.to_json(),
                    "policy_version_id": self.policy_version_of(mission.id),
                    "concurrency_limit": plan.concurrency_limit,
                    "eligible": plan.eligible,
                    "slots": plan.slots,
                    "pressure": plan.pressure,  # S9-08: the gate the grant was made under
                },
            ):
                progressed = True
            current = self.store.get_mission(mission.id)
            if current is None or current.status in TERMINAL_MISSION:
                break
        return progressed

    def _root_review(self, mission: Mission, new_mode: HierarchicalDispatch) -> Any:
        """The deployment's root-review coordinator for this Mission.

        Built per call rather than held: it carries no Mission state, and a held one
        would outlive the store handle a restart replaces.
        """

        return RootReviewCoordinator(
            self.store,
            self.commit,
            new_mode,
            scope_id="mission",
            issued_by=self._owner,
            max_cuts_per_revision=self._config.max_root_review_cuts,
        )

    async def _advance_root_review(self, mission: Mission, new_mode: HierarchicalDispatch) -> bool:
        """Move the root ``MISSION_FINAL`` review one step, or say why it cannot.

        ``True`` means *something happened* — a package was cut, a reviewer was
        asked — and the cycle counts as progress.  ``False`` means the review needs
        nothing from this loop, which is true both when it is ``READY`` (the caller
        goes on to offer the resolution) and when it is stuck: a stuck review is left
        for part 2d's idle-stall path, which records every gate still holding the
        Mission and stops it after one confirming cycle, rather than being retried
        here every cycle on the Mission's own account.
        """

        coordinator = self._root_review(mission, new_mode)
        try:
            state = coordinator.state(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            self._note(f"mission {mission.id}: the root review state could not be read ({error})")
            return False
        if state.status in {
            RootReviewStatus.READY,
            RootReviewStatus.ALREADY_RESOLVED,
            RootReviewStatus.NOT_READY,
            RootReviewStatus.UNREADABLE_PLAN,
        }:
            return False
        if state.status is RootReviewStatus.CUT_BUDGET_SPENT:
            # Once per revision, and then silence: the stall record is what an
            # operator reads next, and a second line every cycle would bury it.
            coordinator.record_cut_budget_spent(mission.id, state)
            self._note(f"mission {mission.id}: root review not re-cut ({state.detail})")
            return False
        if state.status is RootReviewStatus.AWAITING_PERSON:
            return self._ask_person_to_adjudicate_root(mission, new_mode, coordinator, state)
        if state.status is RootReviewStatus.REVIEW_REJECTED:
            # §9.1's decision table, never a silent retry.  The official record stands;
            # this loop does not get to ask the same question again with the same
            # anchor.  What it does is report the rejection to the Planner as an
            # ordinary repair request, once per record.
            if self._request_root_review_repair(mission, new_mode, state):
                return True
            self._note(f"mission {mission.id}: {state.detail}")
            return False
        if state.needs_cut:
            try:
                package = coordinator.cut(mission.id, now_ms=int(self.store.now * 1000))
            except (ContractError, StoreError) as error:
                self._note(f"mission {mission.id}: the root review could not be cut ({error})")
                return False
            self._note(
                f"mission {mission.id}: root review cut {package.package_id} over "
                f"requirements revision {int(package.binding.requirements_revision)} and "
                f"{len(package.child_acceptance_refs)} contribution(s)"
                + (f" (re-cut: {', '.join(state.stale_reasons)})" if state.stale_reasons else "")
            )
            await self._ask_root_reviewer(mission, coordinator, package)
            return True
        if state.status is RootReviewStatus.AWAITING_REVIEW and state.package is not None:
            # The intent's creation key is the package id, so this is idempotent: a
            # package already out for review is not asked about twice.
            return await self._ask_root_reviewer(mission, coordinator, state.package)
        return False

    def _composition_assembly(self, mission: Mission, dispatch: Any) -> Any:
        """The inner-compound resolver with this loop's two assured outlets bound."""

        return CompositionAcceptanceAssembly(
            self.store,
            self.commit,
            dispatch=dispatch,
            issued_by=self._owner,
            ask_person=partial(self._ask_person_to_adjudicate_compound, mission, dispatch),
            on_rejected=partial(self._request_composition_repair, mission, dispatch),
            on_deferred=partial(self._composition_deferred, mission),
        )

    def _composition_deferred(
        self, mission: Mission, occurrence_id: str, task_id: str, goal_type: str, error: Exception
    ) -> None:
        """片 B：中间目标这一轮出不了结论（审阅开不起来、提交被拒）——记一条事件。

        每轮循环都会重试，所以同一个目标、同一个原因只记一次。只陈述事实，不据此做任何决定。
        """
        reason = f"{type(error).__name__}: {error}"[:600]
        with self.store.transaction():
            append_hierarchical_event(
                self.store,
                "CompositionReviewDeferred",
                mission.id,
                task_id=task_id,
                key=f"{occurrence_id}:{content_hash_of(reason)[:16]}",
                payload={
                    "occurrence_id": occurrence_id,
                    "task_id": task_id,
                    "goal_type": goal_type,
                    "reason": reason,
                },
            )
        self._note(
            f"mission {mission.id}: composition review of {occurrence_id} deferred ({reason})"
        )

    def _register_human_question(
        self,
        mission: Mission,
        new_mode: Any,
        *,
        decision_id: str,
        subject_key: str,
        payload: Any,
        current: Any,
        next_ordinal: int,
        repair_context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """登记规划器的问题；同一主题、同一问题已经答过就沿用那次回答，不再打扰人。

        2026-10-01（审阅升级具名后续）：真机第 4 局规划器对同一件事连问 13 次直到次数用完。
        带修复上下文的问题（补偿、裁决）各有各的主体，不去重。
        """

        questions = planning_human_store.PlanningHumanStore(self.store)
        binding = {
            "plan_revision": current.plan_revision,
            "requirements_revision": current.requirements_revision,
        }
        previous = (
            None
            if repair_context is not None
            else questions.find_answered(mission.id, subject_key, payload.to_json().get("question"))
        )
        if previous is not None:
            row = questions.register_reusing_answer(
                decision_id=decision_id,
                mission_id=mission.id,
                subject_key=subject_key,
                payload=payload,
                request_binding=binding,
                next_ordinal=next_ordinal,
                previous=previous,
            )
            return row, {
                "question_id": decision_id,
                "state": row["state"],
                "reused_from": previous["decision_id"],
            }
        row = questions.register(
            decision_id=decision_id,
            mission_id=mission.id,
            subject_key=subject_key,
            payload=payload,
            request_binding=binding,
            next_ordinal=next_ordinal,
            repair_context=repair_context,
        )
        return row, {"question_id": decision_id, "state": row["state"]}

    def _ask_person_to_adjudicate_compound(
        self, mission: Mission, dispatch: Any, record: Any, task_id: str, occurrence_id: str
    ) -> bool:
        """2026-10-01（第 3 项）：中间目标的组合审阅复审后仍判不下来 → 同根终审，问人裁决。"""
        decision_id = "adjudicate-compound:" + str(record.record_id)
        if self._ruling_question_stale(decision_id):
            return self._request_composition_repair(
                mission, dispatch, record, None, task_id, occurrence_id, ruling_stale=True
            )
        return self._ask_person_to_adjudicate(
            mission,
            record,
            target_id=str(task_id),
            subject_key=str(task_id),
            decision_id=decision_id,
            intro="中间目标「" + str(occurrence_id) + "」的组合审查两位审阅员都判不下来，"
            "需要你裁决这一部分拼起来是否合格。",
            extra={"package_id": str(record.package_id), "occurrence_id": str(occurrence_id)},
        )

    def _request_composition_repair(
        self,
        mission: Mission,
        dispatch: Any,
        record: Any,
        package: Any,
        task_id: str,
        occurrence_id: str,
        *,
        ruling_stale: bool = False,
    ) -> bool:
        """2026-10-01（第 3 项）：组合审阅打回 / 拒绝（或人裁决打回）→ 一条修复请求交规划器。

        走验收失败同一条路（``PlanningRepairRequested``，触发步骤 = 该中间目标），规划器
        在修复轮里拿到审阅员的具体意见；同一份记录只记一次。
        """

        ruling = adjudication_of(self.store, str(record.record_id))
        produced = record_request(
            dispatch,
            mission.id,
            event_type="VerifierAcceptanceRejected",
            trigger_refs=(str(task_id),),
            source_key="composition-review:" + str(record.record_id),
            detail={
                "source": "composition_review",
                "record_id": str(record.record_id),
                "package_id": str(record.package_id),
                "occurrence_id": str(occurrence_id),
                "verdict": str(record.verdict),
                "findings": self._review_record_findings(record),
                **(_RULING_STALE if ruling_stale else {}),
                **({"human_ruling": ruling} if ruling is not None else {}),
            },
        )
        if produced:
            self._note(
                f"mission {mission.id}: composition review of {occurrence_id} concluded "
                f"{record.verdict!s}; repair requested"
            )
        return produced

    def _ask_person_to_adjudicate_root(
        self, mission: Mission, new_mode: HierarchicalDispatch, coordinator: Any, state: Any
    ) -> bool:
        """Two final reviewers could not decide: the person rules on the root (2026-09-30).

        A step's INCONCLUSIVE review suspends its Result and reuses the review
        approval; the root has no Result to suspend, so the question goes through the
        planning-question channel (blocking, two options) and its answer is consumed
        here — never by a planner round — as the same ``AssuranceReviewAdjudicated``
        receipt the use certificate, the acceptance formula and the completion reads
        already honour. One question per official record; the record is not rewritten.
        """

        record, package = state.record, state.package
        if record is None or package is None:
            return False
        decision_id = "adjudicate-root:" + str(record.record_id)
        if self._ruling_question_stale(decision_id):
            return self._request_root_review_repair(mission, new_mode, state, ruling_stale=True)
        return self._ask_person_to_adjudicate(
            mission,
            record,
            target_id=str(state.task_id),
            subject_key=str(state.task_id),
            decision_id=decision_id,
            intro="最终审查两位审阅员都判不下来，需要你裁决整个任务的产出是否合格。",
            extra={"package_id": str(package.package_id)},
        )

    def _ask_person_to_adjudicate_outcome(
        self, mission: Mission, record: Any, binding: Any
    ) -> bool:
        """阶段 C 第 3 条：发布结果的审查判不下来（含审阅员两次回复都无法采用）→ 问人裁决。

        裁决"通过"后按已有的使用证书路径验收这次发布；"打回"或题目过期则这项效果核不完，
        由卡死确认如实交给规划器。"""
        return self._ask_person_to_adjudicate(
            mission,
            record,
            target_id=str(binding.operation_occurrence_id),
            subject_key=str(binding.operation_occurrence_id),
            decision_id="adjudicate-outcome:" + str(record.record_id),
            intro="一次对外操作的结果审查两位审阅员都判不下来，需要你裁决这次操作的结果是否合格。",
            extra={"package_id": str(record.package_id), "effect_key": str(binding.effect_key)},
        )

    def _ruling_question_stale(self, decision_id: str) -> bool:
        """The person was asked to rule and the question was retired unanswered (the plan,
        the requirements or the management epoch changed).  No ruling will come: reported
        to the Planner as "no verdict", once per record, never re-asked by this loop."""

        row = planning_human_store.PlanningHumanStore(self.store).get(decision_id)
        return row is not None and row["state"] == "STALE"

    def _ask_person_to_adjudicate(
        self,
        mission: Mission,
        record: Any,
        *,
        target_id: str,
        subject_key: str,
        decision_id: str,
        intro: str,
        extra: Mapping[str, Any],
        task_id: str | None = None,
    ) -> bool:
        """One blocking two-option question per official record; its answer becomes the
        ``AssuranceReviewAdjudicated`` receipt (never a planner round). True when this
        call registered the question or consumed its answer; False while waiting."""

        questions = planning_human_store.PlanningHumanStore(self.store)
        row = questions.get(decision_id)
        if row is None:
            findings = (
                "\n".join(
                    f"- {item.criterion_id}：{'; '.join(item.limitations) or str(item.verdict)}"
                    for item in record.criteria
                    if str(item.verdict) != "PASS"
                )
                or "-（审阅员没有写明疑点）"
            )
            question = RequestHumanDecision(
                intro
                + "\n审阅员的疑点：\n"
                + findings
                + "\n选“通过”则按合格处理；选“打回”则交规划器修改后重做。",
                (HumanOptionV1("pass", "通过"), HumanOptionV1("fail", "打回")),
                True,
            )
            htn = htn_store.HtnStore(self.store)
            plan = htn.active_plan_revision(mission.id)
            requirements = htn.latest_requirements_revision(mission.id)
            with self.store.transaction():
                questions.register(
                    decision_id=decision_id,
                    mission_id=mission.id,
                    subject_key=subject_key,
                    payload=question,
                    request_binding={
                        "plan_revision": 0 if plan is None else int(plan.revision),
                        "requirements_revision": 0
                        if requirements is None
                        else int(requirements.revision),
                    },
                    next_ordinal=self._next_planning_ordinal(mission.id),
                    repair_context={
                        "kind": "review_adjudication",
                        "record_id": str(record.record_id),
                        "target_id": target_id,
                        **dict(extra),
                    },
                )
                append_hierarchical_event(
                    self.store,
                    "PlanningHumanRequested",
                    mission.id,
                    key=decision_id,
                    payload={
                        "decision_id": decision_id,
                        "question_id": decision_id,
                        "state": "PENDING",
                        "origin": "review_adjudication",
                        "record_id": str(record.record_id),
                    },
                )
            self._note(
                f"mission {mission.id}: review {record.record_id} inconclusive twice; asked the person to rule"
            )
            return True
        if row["state"] != "ANSWERED":
            return False  # waiting for the person; the idle verdict counts the pending question
        if (
            self.store.get_receipt("assurance-review-adjudicated:" + str(record.record_id))
            is not None
        ):
            return False  # already consumed
        answer = row["answer"] or {}
        with self.store.transaction():
            self.commit.adjudicate_review_record(
                mission.id,
                record,
                target_id=target_id,
                decision=str(answer.get("answer")),
                note="",
                principal_id=str(answer.get("principal_id") or ""),
                decision_receipt_hash=str(answer.get("receipt_hash") or ""),
                request_id=decision_id,
                task_id=task_id or target_id,
            )
        self._note(
            f"mission {mission.id}: the person ruled {answer.get('answer')} on review {record.record_id}"
        )
        return True

    def _request_root_review_repair(
        self,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        state: Any,
        *,
        ruling_stale: bool = False,
    ) -> bool:
        """最终审查打回（或人裁决打回）→ 一条通用修复请求交规划器（片 0 第 2 步，2026-10-01）。

        此前这里是一条专用路径：只挑"阻断级"意见、先替规划器退掉根目标的做法并取消在跑的
        步骤、再开一轮专用规划。保证通道上的打回不带"阻断级"标记，专用路径什么都不做，任务
        原地停到"没有可派发的工作"。

        现在与组合审阅打回走同一条路（``PlanningRepairRequested``）：请求里是事实——审阅员
        的全部意见、人的裁决、这是第几次、上限是多少——做法不动、步骤不动，由规划器在换做法、
        补步骤、问人里选。同一份正式记录只记一次。

        Harness 只保留上限：一个任务因最终审查打回而交给规划器的次数不超过
        ``max_root_review_repairs``；用完后不再发请求，空闲判定按
        ``ROOT_REVIEW_REPAIRS_EXHAUSTED`` 停。请求的范围是整个计划——审查的是整个任务，
        任何一步上的计划改动都算处理了它。
        """

        record, package = getattr(state, "record", None), getattr(state, "package", None)
        limit = int(self._config.max_root_review_repairs)
        if record is None or package is None or limit < 1:
            return False
        source_key = "root-review:" + str(record.record_id)
        requested = self._root_review_request_keys(mission.id)
        if source_key in requested:
            return False
        if len(requested) >= limit:
            self._note(
                f"mission {mission.id}: the final review rejected again and this Mission has "
                f"already been handed back to the Planner {len(requested)} time(s) "
                f"(max_root_review_repairs={limit})"
            )
            return False
        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return False
        ruling = adjudication_of(self.store, str(record.record_id))
        produced = record_request(
            new_mode,
            mission.id,
            event_type="VerifierAcceptanceRejected",
            trigger_refs=(str(state.task_id),),
            source_key=source_key,
            scope=tuple(
                sorted(
                    {str(spec.occurrence_id) for spec in network.occurrences}
                    | {str(spec.task_id) for spec in network.occurrences}
                )
            ),
            detail={
                "source": "root_review",
                "record_id": str(record.record_id),
                "package_id": str(package.package_id),
                "verdict": str(record.verdict),
                "findings": self._review_record_findings(record),
                "repair_round": len(requested) + 1,
                "max_repairs": limit,
                **(_RULING_STALE if ruling_stale else {}),
                **({"human_ruling": ruling} if ruling is not None else {}),
            },
        )
        if produced:
            self._note(
                f"mission {mission.id}: the final review concluded {record.verdict!s}; "
                f"repair requested ({len(requested) + 1}/{limit})"
            )
        return produced

    @staticmethod
    def _review_record_findings(record: Any) -> list[dict[str, Any]]:
        """Every criterion the reviewer did not pass, in the reviewer's own words."""

        return [
            {
                "criterion_id": str(item.criterion_id),
                "verdict": str(item.verdict),
                "limitations": list(item.limitations),
            }
            for item in record.criteria
            if str(item.verdict) != "PASS"
        ][:16]

    def _root_review_request_keys(self, mission_id: str) -> list[str]:
        """The final-review rejections already handed to the Planner, one key per record."""

        return [
            str(event.payload.get("source_key"))
            for event in self.store.iter_events(mission_id)
            if event.type == REQUESTED
            and str(event.payload.get("source_key", "")).startswith("root-review:")
        ]

    def _final_review_findings_for_workers(self, mission_id: str) -> list[dict[str, Any]]:
        """The findings of the latest final review that was handed back to the Planner."""

        latest: list[dict[str, Any]] = []
        for event in self.store.iter_events(mission_id):
            if event.type != REQUESTED:
                continue
            if not str(event.payload.get("source_key", "")).startswith("root-review:"):
                continue
            context = (event.payload.get("request") or {}).get("context") or {}
            latest = [
                dict(item) for item in context.get("findings") or () if isinstance(item, Mapping)
            ]
        return latest[:16]

    def _root_review_repairs(self, mission_id: str) -> int:
        """How many times this Mission was handed back to the Planner by a final review."""

        return len(self._root_review_request_keys(mission_id))

    def _reviews_without_verdict_detail(self, mission_id: str) -> dict[str, Any]:
        """Why reviews of this Mission ended without a verdict, for the stop report.

        Two honest causes, never merged: the review *call* never came back and its
        retries ran out (an infrastructure matter); or the review is on record as
        inconclusive — the reviewers could not tell, or the reviewer's last reply could
        not be used — and the person's ruling is pending, went stale, or was "fail".
        """

        detail: dict[str, Any] = {}
        for prefix, name in (
            ("assurance-mission-final:", "final_review"),
            ("assurance-composition:", "composition_review"),
            ("assurance-operation-outcome:", "operation_outcome_review"),
        ):
            found = self._exhausted_reviews(mission_id, prefix)
            if name == "final_review":
                # 被打断而用完的最终审查已重切新包（新审阅），不是停止原因。
                found = [item for item in found if not item["interrupted"]]
            if found:
                detail[name] = found[0]
        inconclusive = self._inconclusive_reviews(mission_id)
        if inconclusive:
            detail["inconclusive_reviews"] = inconclusive[:16]
        return detail

    def _exhausted_reviews(self, mission_id: str, prefix: str) -> list[dict[str, Any]]:
        """Reviews under ``prefix`` that will never have an official record.

        The review call did not come back and its retries ran out
        (``AssuranceReviewFormatExhausted``), or the reply could not be imported for a
        reason that is not the reviewer's to repair (``AssuranceReviewImportRejected``).
        A reply that came back and could not be *used* is not here: after its one
        repair it is on record as inconclusive (see ``_inconclusive_reviews``).
        """

        rows = self.store.connection.execute(
            "SELECT payload_json FROM events WHERE mission_id=? AND type IN "
            "('AssuranceReviewFormatExhausted','AssuranceReviewImportRejected') ORDER BY seq",
            (mission_id,),
        ).fetchall()

        found = []
        for (raw,) in rows:
            payload = json.loads(raw or "{}")
            key = str(payload.get("review_key", ""))
            if key.startswith(prefix):
                found.append(
                    {
                        "reason": str(payload.get("reason", "")),
                        "review_key": key,
                        "interrupted": review_exhausted_by_interruption(self.store, key),
                    }
                )
        return found

    def _inconclusive_reviews(self, mission_id: str) -> list[dict[str, str]]:
        """Inconclusive official records the person was asked to rule on and has not passed:
        ``ruling`` is ``pending`` / ``stale`` / ``fail``.  Read from the questions this loop
        registered (one per record), so every kind of review is reported the same way."""

        found = []
        for row in planning_human_store.PlanningHumanStore(self.store).list(mission_id):
            decision_id = str(row["decision_id"])
            if not decision_id.startswith("adjudicate-"):
                continue
            kind, _, record_id = decision_id[len("adjudicate-") :].partition(":")
            ruling = adjudication_of(self.store, record_id)
            if ruling is not None and ruling.get("decision") == "pass":
                continue
            found.append(
                {
                    "kind": kind,
                    "record_id": record_id,
                    "ruling": "fail"
                    if ruling is not None
                    else "stale"
                    if row["state"] == "STALE"
                    else "pending",
                }
            )
        return found

    def _root_review_stop_detail(
        self, mission: Mission, new_mode: HierarchicalDispatch
    ) -> dict[str, Any]:
        """What the stop report says about the root review, when it is why (P2.3j).

        Empty when the review is not in a rejected or budget-spent state — an idle
        Mission whose root review never ran has nothing to say here.  Otherwise the
        status, the package, the findings the reviewer filed, and how many times the
        Mission was handed back to the Planner for it.
        """

        try:
            state = self._root_review(mission, new_mode).state(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return {}
        if state.status not in {
            RootReviewStatus.REVIEW_REJECTED,
            RootReviewStatus.CUT_BUDGET_SPENT,
        }:
            return self._reviews_without_verdict_detail(mission.id)
        package = getattr(state, "package", None)
        record = getattr(state, "record", None)
        active = new_mode.semantics().active_plan_revision(mission.id)
        return {
            "root_review": {
                # 如实写是哪一种：被打回，还是这一版要求的切包次数用完（含审阅调用一直被打断）。
                "reason": (
                    "root_review_cut_budget_spent"
                    if state.status is RootReviewStatus.CUT_BUDGET_SPENT
                    else "root_review_rejected"
                ),
                "stale_reasons": [str(item) for item in getattr(state, "stale_reasons", ()) or ()],
                "status": str(state.status),
                "package_id": "" if package is None else str(package.package_id),
                "plan_revision": 0 if active is None else int(active.revision),
                "repairs_used": self._root_review_repairs(mission.id),
                "max_root_review_repairs": int(self._config.max_root_review_repairs),
                "findings": [] if record is None else self._review_record_findings(record),
                "detail": str(state.detail)[:600],
            }
        }

    def _root_review_repairs_are_exhausted(
        self, mission: Mission, new_mode: HierarchicalDispatch
    ) -> bool:
        """True when the final review stands rejected and the Planner has no turn left on it.

        The bound is spent (``max_root_review_repairs`` requests were made for this
        Mission) and none of them is still waiting for the Planner — a request the
        Planner has not answered yet is work in progress, not an exhausted repair.
        """

        if int(self._config.max_root_review_repairs) < 1:
            return False
        detail = self._root_review_stop_detail(mission, new_mode).get("root_review") or {}
        if str(detail.get("status") or "") != "REVIEW_REJECTED":
            return False
        if int(detail.get("repairs_used") or 0) < int(self._config.max_root_review_repairs):
            return False
        return not any(
            str(row.get("source_key", "")).startswith("root-review:")
            for row in pending_requests(self.store, mission.id)
        )

    def _accepted_path_hashes(
        self,
        mission_id: str,
        *,
        current_task_ids: Collection[str] | None = None,
    ) -> dict[str, str]:
        """Files a COMPLETED leaf already produced, keyed by path.  Later writers win.

        P2.3m: Grok H-L3-C1-r0's apply-patch leaf listed ``metrics/collector.py`` on
        its result (not only the ``patch`` port file).  The verify leaf then wrote
        the same bytes.  Those files are accepted work even when they are not the
        declared port output.

        P2.3v: only CURRENT plan members count.  A retired method's accepted
        ``net/retry.py`` must not shadow the replacement apply leaf.
        """

        completed = {
            task.id
            for task in self.store.list_tasks(mission_id)
            if task.status is TaskStatus.COMPLETED
        }
        if current_task_ids is not None:
            completed &= set(current_task_ids)
        hashes: dict[str, str] = {}
        for artifact in self.store.list_mission_artifacts(mission_id):
            if artifact.task_id in completed:
                hashes[artifact.path] = artifact.content_hash
        return hashes

    def _record_applied_diff_files(
        self,
        referenced: Sequence[Artifact],
        *,
        workspace: Any,
        attempt: Attempt,
        seed: Mapping[str, str],
    ) -> list[Artifact]:
        """P2.3v: a write leaf that only delivered a unified diff still records
        the patched seed files, so overlay / ``rule_check`` / ``code_test`` see
        them.  Any file that cannot be applied refuses the whole document.
        """

        extra: list[Artifact] = []
        occupied = {artifact.path for artifact in referenced}
        store = workspace.store
        if store is None or not seed:
            return list(referenced)
        for artifact in referenced:
            if not str(artifact.path).endswith((".diff", ".patch")):
                continue
            try:
                data = (workspace.root / artifact.path).read_bytes()
            except OSError as error:
                raise UnifiedDiffApplyError(artifact.path, "unreadable") from error
            text = decode_unified_diff_text(data, path=artifact.path)
            if "--- " not in text or "+++ " not in text:
                continue
            patched = files_patched_by_unified_diff(text, seed)
            for path, content in patched.items():
                if path in occupied:
                    continue
                data = content.encode("utf-8")
                digest = sha256_hex_text(data)
                store.put_bytes(data)
                extra.append(
                    Artifact(
                        id=ids.artifact_id(attempt.id, path, digest),
                        mission_id=attempt.mission_id,
                        task_id=attempt.task_id,
                        attempt_id=attempt.id,
                        type="file",
                        path=path,
                        version=1,
                        content_hash=digest,
                        size_bytes=len(data),
                        produced_by=artifact.produced_by,
                        storage_uri=str(store.path_for(digest)),
                        workspace=attempt.id,
                    )
                )
                occupied.add(path)
        return [*referenced, *extra]

    def _next_planning_ordinal(self, mission_id: str) -> int:
        """One past the highest ordinal any planning round of this Mission has used.

        The ordinal *is* the planner intent's creation key (``…:planner:<n>``), so the
        next free one is found by asking for each in turn — reusing a spent ordinal
        would return the old intent and the repair round would never be dispatched.
        """

        ordinal = 1
        while self.store.get_intent_for_subject(f"{mission_id}:planner:{ordinal}") is not None:
            ordinal += 1
        return ordinal

    async def _ask_root_reviewer(self, mission: Mission, coordinator: Any, package: Any) -> bool:
        """Open the Assurance MISSION_FINAL review of the cut package.  Idempotent per
        review package; a package already out for review is not progress."""

        if self._wiring.assurance_reviews is None:
            raise ContractError("Assurance review builder is not installed for MISSION_FINAL")

        review_key = purpose_review_key("MISSION_FINAL", mission.id, str(package.package_id))
        already = (
            self.store.connection.execute(
                "SELECT 1 FROM assurance_review_invocations WHERE mission_id=? AND review_key=?",
                (mission.id, review_key),
            ).fetchone()
            is not None
        )
        try:
            self._wiring.assurance_reviews.ensure_mission_final(
                mission, package=package, dispatch=coordinator.dispatch
            )
        except AssuranceError as error:
            self._note(
                f"mission {mission.id}: Assurance MISSION_FINAL review unavailable ({error})"
            )
            return False
        return not already

    async def _root_resolution_formed(
        self, mission: Mission, new_mode: HierarchicalDispatch
    ) -> bool:
        """Whether this Mission's root ``GoalResolution`` stands (P2.3c part 2).

        Offers it once when it does not, and reports the refusal.  The decision itself
        is entirely the Commit Service's — this is the trigger, not a second judge —
        and the delivery contract is read from the Mission's requirements so the stage
        a root must reach is the one the goal asked for rather than one this call
        invented (AER §6.1).
        """

        semantics = new_mode.semantics()
        inputs = new_mode.root_resolution_inputs(mission.id)
        if inputs.reason == "ALREADY_RESOLVED":
            return True
        required_stage = None
        receipt_ids: tuple[str, ...] = ()
        if inputs.requirements is not None and inputs.requirements.delivery_contract_ref:
            # The goal declared a delivery contract, so the root is not resolved until
            # a recorded DeliveryReceipt says the output travelled that far.  CONFIRMED
            # is the strongest stage there is, and asking for less than the strongest
            # would be this call relaxing the contract on the goal's behalf.
            required_stage = DeliveryStage.CONFIRMED
            # Review F5: only the receipts this root may legitimately quote.  Naming
            # every receipt the Mission ever recorded meant a single one written for a
            # retired branch — or for an Acceptance later superseded — refused the
            # *whole* command (``DELIVERY_RECEIPT_INVALID``) for good.  The filter is
            # the accept side's own predicate, asked before the command is built rather
            # than written a second time here.
            receipt_ids = eligible_root_receipts(
                semantics,
                mission.id,
                obligation_id=inputs.obligation_id,
                method_instance_id=inputs.method_instance_id,
                required_stage=required_stage,
            )
        outcome = new_mode.attempt_root_resolution(
            mission.id,
            principal=PlanPrincipal(
                principal_id=self._owner,
                scope_id="mission",
            ),
            command_id=f"{mission.id}:root-resolution",
            required_delivery_stage=required_stage,
            delivery_receipt_ids=receipt_ids,
            source={"trigger": "decide", "orchestrator": self._owner},
        )
        if not outcome.committed:
            self._note(
                f"mission {mission.id} root resolution not formed: "
                f"{outcome.reason} ({outcome.detail[:200]})"
            )
        return outcome.committed
