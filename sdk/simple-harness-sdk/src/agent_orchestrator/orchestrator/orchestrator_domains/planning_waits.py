# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Planning service resumption and planning wait wake-ups."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...context.context_builder import ContextRejected
from ...contracts import (
    TERMINAL_MISSION,
    TERMINAL_TASK,
    ContractError,
    Mission,
    MissionStopReason,
    TaskStatus,
)
from ...contracts.htn import ObligationId
from ...contracts.obligations import ObligationLifecycle
from ...contracts.planning_decisions import PlanningRefKind, PlanningRefV1
from ...contracts.resolution import Validity
from ...contracts.semantic_base import content_hash_of
from ...governance.budgets import BudgetExhausted
from ...runtime.model_router import RoutingUnavailable
from ...storage import htn_store, planning_human_store
from ...storage.obligation_store import ObligationStore
from ...storage.store import StoreError
from .. import planning_selection
from ..assurance_point_use import PointUseRefused
from ..hierarchical_dispatch import append_hierarchical_event
from ..method_plan_reviews import awaiting as method_review_awaiting
from ..planning_runtime_block import last_wake, pending_block
from ..planning_wait_targets import steps_under
from .core import OrchestratorCore


class PlanningWaitMixin(OrchestratorCore):
    """Planning service resumption and planning wait wake-ups."""

    __slots__ = ()

    def _resume_planning_services(self, mission: Mission) -> bool:
        """Consume service receipts and reserve the next Planner atomically."""

        runtime_block = pending_block(self.store, mission.id)
        if runtime_block is not None and last_wake(self.store, mission.id, runtime_block) is None:
            return False
        questions = planning_human_store.PlanningHumanStore(self.store)
        questions.retire_stale(mission.id)
        if questions.pending(mission.id) or self._planner_intents_in_flight(mission.id):
            return False
        if self._requirements_unconfirmed(mission):
            return False

        try:
            return self._resume_planning_services_now(mission, questions)
        except PointUseRefused as refused:
            return self._planning_round_waits(mission.id, refused, path="service_resume")

    def _resume_planning_services_now(self, mission: Mission, questions: Any) -> bool:
        with self.store.transaction():
            if questions.pending(mission.id) or self._planner_intents_in_flight(mission.id):
                return False
            events = tuple(self.store.iter_events(mission.id))
            resumed = {
                e.payload.get("service_id", e.payload.get("decision_id"))
                for e in events
                if e.type == "PlanningServiceResumed"
            }
            service_types = {
                "PlanningEvidenceRecorded",
                "PlanningMethodProposed",
                "PlanningMethodReviewed",
                "PlanningHumanAnswered",
                "PlanningHumanStale",
                "PlanningHumanRequested",
                "PlanningRuntimeBlockWoken",
                "PlanningRepairRequested",
                "PlanningLibraryRead",
            }
            addressed = {
                request_id
                for e in events
                if e.type == "PlanningRepairAddressed"
                for request_id in e.payload.get("repair_request_ids", ())
            }

            def service_key(event: Any) -> str:
                identity = event.payload.get("decision_id", event.payload.get("request_id"))
                return f"{event.type}:{identity}"

            def needs_resume(event: Any) -> bool:
                identity = event.payload.get("decision_id", event.payload.get("request_id"))
                if service_key(event) in resumed:
                    return False
                if event.type == "PlanningMethodProposed" and event.payload.get(
                    "assurance_review_key"
                ):
                    # 片 A 第 7 项：送审的做法不叫醒规划器；它的审阅结论
                    # （PlanningMethodReviewed）入库后才叫醒。
                    return False
                # Older receipts used an unqualified ID; match their source too,
                # so resuming a question never suppresses its later answer.
                if identity in resumed and any(
                    e.type == "PlanningServiceResumed"
                    and e.payload.get("service_id", e.payload.get("decision_id")) == identity
                    and e.payload.get("source_type") == event.type
                    for e in events
                ):
                    return False
                if event.type in {"PlanningHumanRequested", "PlanningHumanAnswered"}:
                    row = questions.get(str(identity))
                    # 审阅升级（2026-09-30）：根终审"判不下来"问人的裁决题，答案由
                    # ``_advance_root_review`` 消费（写裁决回执），不开规划轮。
                    if (
                        row
                        and (row["request"].get("repair_context") or {}).get("kind")
                        == "review_adjudication"
                    ):
                        return False
                if event.type == "PlanningHumanRequested":
                    row = questions.get(str(identity))
                    return bool(
                        row
                        and row["state"] == "PENDING"
                        and not row["request"]["payload"]["blocking"]
                    )
                return event.type != "PlanningRepairRequested" or identity not in addressed

            pending = [e for e in events if e.type in service_types and needs_resume(e)]
            if not pending:
                return False
            # 推后第 3 批 H12：审阅积压时，自己有结果在排队等审的任务先不开新规划轮（积压消退或
            # 暂停到时限后再开）。人刚答了问题的那一轮不等：人在等结果（试用前第 5 步）。
            if self._decomposition_paused(mission) and not any(
                e.type == "PlanningHumanAnswered" for e in pending
            ):
                return False
            # 本任务有做法正在送审、结论还没入库：这时开一轮规划，规划器看到的只能是"审阅中"，
            # 采用不了、也没有别的可做（联测真机：它只好选等待，等待又被退回，白丢一轮）。等结论
            # 入库（PlanningMethodReviewed）再开，别的待处理请求那时一并交给它。

            if method_review_awaiting(self.store, mission.id):
                return False
            if self._planning_ladder_spent(mission.id):
                self._stop_planning_round(
                    mission.id,
                    reason="planning_bound_reached",
                    detail={"phase": "planning_service_resume"},
                    stop_reason=MissionStopReason.PLANNING_FAILED,
                )
                return True
            ordinal = self._next_planning_ordinal(mission.id)
            intent = self._create_planner_intent_now(mission.id, ordinal=ordinal)
            for event in pending:
                service_id = service_key(event)
                append_hierarchical_event(
                    self.store,
                    "PlanningServiceResumed",
                    mission.id,
                    key=str(service_id),
                    payload={
                        "service_id": service_id,
                        "source_type": event.type,
                        "decision_id": event.payload.get("decision_id"),
                        "intent_id": intent.intent_id,
                        "ordinal": ordinal,
                    },
                )
        return True

    def _has_pending_planning_waits(self, mission_id: str | None = None) -> bool:
        """Return whether an active Mission has an unfired durable WAIT registration.

        A WAIT is external work.  It must not keep ``run(until_idle=True)`` alive,
        because the caller may be the component that will later publish the target
        state.  The helper is still used to suppress planner/synthesis work and false
        stall stops while that external condition is pending.
        """

        missions = self._active_missions()
        if mission_id is not None:
            missions = [mission for mission in missions if mission.id == mission_id]

        pending_intents = self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")

        for mission in missions:
            if pending_block(self.store, mission.id) is not None:
                return True
            if any(
                intent.mission_id == mission.id
                and planning_selection.awaits_authority(self.store, intent)
                for intent in pending_intents
            ):
                return True
            if planning_human_store.PlanningHumanStore(self.store).pending(mission.id):
                return True
            if self._pending_planning_wait(mission.id) is not None:
                return True

            if method_review_awaiting(self.store, mission.id):
                return True  # 提出的做法还在审：在等，不是卡住
        return False

    def _pending_planning_wait(self, mission_id: str) -> Any:
        """Only the latest accepted decision can suspend a Mission's planner."""

        if self.store.count_events(mission_id, "PlanningWaitRegistered") == 0:
            return None
        events = self.store.iter_events(mission_id)
        accepted = [
            event
            for event in events
            if event.type == "PlanningDecisionEvaluated"
            and event.payload.get("status") in {"NO_STATE_CHANGE", "COMMITTED"}
        ]
        if not accepted or accepted[-1].payload.get("decision_type") != "WAIT":
            return None
        decision_id = accepted[-1].payload.get("decision_id")
        registration = next(
            (
                event
                for event in reversed(events)
                if event.type == "PlanningWaitRegistered"
                and event.payload.get("decision_id") == decision_id
            ),
            None,
        )
        if registration is None:
            return None
        if any(
            event.type == "PlanningWaitWoken"
            and event.payload.get("registration_key") == registration.idempotency_key
            for event in events
        ):
            return None
        return registration

    @staticmethod
    def _planning_wait_fingerprint(refs: Sequence[PlanningRefV1]) -> str:
        """Stable identity for one set of external conditions."""

        ordered = sorted(
            {(str(ref.kind), ref.id, ref.semantic_revision, ref.content_hash) for ref in refs}
        )
        return content_hash_of({"wait_for": [list(row) for row in ordered]})

    def _planning_wait_ref_satisfied(self, mission: Mission, ref: PlanningRefV1) -> bool:
        """Resolve one WAIT ref from its authoritative store, never event mentions.

        There is no latest-by-id fallback: every store check compares the requested
        semantic revision and content hash. Unsupported targets are refused before
        registration; an event mentioning a reference is never completion evidence.
        """

        try:
            htn = htn_store.HtnStore(self.store)
            if ref.kind is PlanningRefKind.TASK:
                semantics = htn.task_semantics_of(mission.id, ref.id)
                task = self.store.get_task(ref.id)
                return bool(
                    task is not None
                    and task.mission_id == mission.id
                    and task.status in TERMINAL_TASK
                    and semantics is not None
                    and int(semantics.contract_revision) == ref.semantic_revision
                    and ref.content_hash == semantics.content_hash()
                )
            if ref.kind is PlanningRefKind.OBLIGATION:
                obligation = ObligationStore(self.store).obligation(
                    mission.id, ObligationId(ref.id)
                )
                account = ObligationStore(self.store).account(mission.id, obligation.obligation_id)
                return bool(
                    obligation.mission_id == mission.id
                    and account.lifecycle is ObligationLifecycle.SATISFIED
                    and content_hash_of(obligation.to_json()) == ref.content_hash
                    and ref.semantic_revision == 1
                )
            if ref.kind is PlanningRefKind.REVIEW:
                stored = htn.get_review_record(ref.id)
                return bool(
                    stored.official
                    and stored.record.binding.mission_id == mission.id
                    and ref.semantic_revision == 1
                    and content_hash_of(stored.record.to_json()) == ref.content_hash
                )
            if ref.kind is PlanningRefKind.ACCEPTANCE:
                acceptance = htn.get_acceptance(ref.id)
                return bool(
                    acceptance.mission_id == mission.id
                    and acceptance.validity is Validity.CURRENT
                    and ref.semantic_revision == 1
                    and content_hash_of(acceptance.to_json()) == ref.content_hash
                )
            if ref.kind is PlanningRefKind.RESOLUTION:
                resolution = htn.get_goal_resolution(ref.id)
                adopted = htn.adopted_goal_resolution(mission.id, resolution.obligation_id)
                return bool(
                    resolution.mission_id == mission.id
                    and resolution.validity is Validity.CURRENT
                    and adopted is not None
                    and adopted.resolution_id == resolution.resolution_id
                    and ref.semantic_revision == 1
                    and content_hash_of(resolution.to_json()) == ref.content_hash
                )
        except (ContractError, KeyError, StoreError, ValueError):
            return False
        return False

    def _planning_wait_ref_waitable(
        self, mission: Mission, ref: PlanningRefV1, *, registered: bool = False
    ) -> bool:
        """A visible object alone is not proof that work will make progress.

        Completed immutable records can receive immediate feedback. Pending Tasks
        must already be executing or verifying under the exact semantic contract.
        Other pending records need their own authoritative producer relationship;
        without one, refuse the WAIT rather than suspend planning indefinitely.
        """
        if self._planning_wait_ref_satisfied(mission, ref):
            return True
        if ref.kind is not PlanningRefKind.TASK:
            return False

        task = self.store.get_task(ref.id)
        semantics = htn_store.HtnStore(self.store).task_semantics_of(mission.id, ref.id)
        return bool(
            task is not None
            and task.mission_id == mission.id
            # After registration a live task can temporarily return to READY for
            # a retry. Its exact identity is still the registered producer.
            # A WAIT needs a producer that is actually running: an ACTIVE leaf that only
            # waits for its own retry decision would never wake it (2026-09-25).
            and (
                registered
                or (
                    task.status in {TaskStatus.ACTIVE, TaskStatus.VERIFYING}
                    and not self._awaiting_retry_decision(mission.id, task)
                )
            )
            and semantics is not None
            and int(semantics.contract_revision) == ref.semantic_revision
            and ref.content_hash == semantics.content_hash()
        )

    def _expand_planning_wait(
        self, mission: Mission, refs: tuple[PlanningRefV1, ...]
    ) -> tuple[PlanningRefV1, ...] | None:
        """What a WAIT really waits for, or ``None`` when nothing it names can progress.

        2026-09-30（资料换版真机）：规划器等的对象常写成正在跑的那一步所在的方法实例或
        目标，旧规则一律拒，白花规划次数。方法实例 / 目标先按规划包给的身份核对（版本与
        内容哈希），再换成它下面正在跑的步骤；下面没有正在跑的步骤就不能等。步骤与已完成
        记录照旧原样认。
        """

        waited: list[PlanningRefV1] = []
        for ref in refs:
            if self._planning_wait_ref_waitable(mission, ref):
                waited.append(ref)
                continue
            dispatch = self._dispatch_for(mission.id)
            if dispatch is None or ref.kind not in {
                PlanningRefKind.METHOD_INSTANCE,
                PlanningRefKind.OBLIGATION,
            }:
                return None
            try:
                network = dispatch.network(mission.id)
                if ref.kind is PlanningRefKind.METHOD_INSTANCE:
                    instance = next(
                        (
                            item
                            for item in network.method_instances
                            if str(item.instance_id) == ref.id
                            and item.instance_id in set(network.adopted_instance_ids)
                        ),
                        None,
                    )
                    if (
                        instance is None
                        or max(1, int(instance.plan_revision)) != ref.semantic_revision
                        or instance.parameters_digest() != ref.content_hash
                    ):
                        return None
                    task_ids = steps_under(network, instance_id=ref.id)
                else:
                    obligation = ObligationStore(self.store).obligation(
                        mission.id, ObligationId(ref.id)
                    )
                    if (
                        ref.semantic_revision != 1
                        or content_hash_of(obligation.to_json()) != ref.content_hash
                    ):
                        return None
                    task_ids = steps_under(network, obligation_id=ref.id)
            except (ContractError, KeyError, StoreError, ValueError):
                return None
            running = []
            for task_id in task_ids:
                semantics = htn_store.HtnStore(self.store).task_semantics_of(mission.id, task_id)
                if semantics is None:
                    continue
                step = PlanningRefV1(
                    kind=PlanningRefKind.TASK,
                    id=task_id,
                    semantic_revision=int(semantics.contract_revision),
                    content_hash=semantics.content_hash(),
                )
                if self._planning_wait_ref_waitable(
                    mission, step
                ) and not self._planning_wait_ref_satisfied(mission, step):
                    running.append(step)
            if not running:
                return None
            waited.extend(running)
        return tuple({(str(item.kind), item.id): item for item in waited}.values())

    async def _wake_planning_waits(self) -> bool:
        """Commit target validation, Planner intent and wake receipt atomically."""

        progressed = False
        for listed in self._active_missions():
            if listed.id in self._unrecovered or not self._round_due(listed.id, "planning_wait"):
                continue
            with self._round_boundary(listed.id, "planning_wait"):
                progressed = self._wake_planning_wait(listed) or progressed
        return progressed

    def _wake_planning_wait(self, listed: Mission) -> bool:

        progressed = False
        for _ in (0,):  # one Mission; ``continue`` below ends its share
            if self.store.count_events(listed.id, "PlanningWaitRegistered") == 0:
                continue
            try:
                # No await in this transaction: competing processes observe either
                # the pending WAIT or its complete intent/binding/reservation/receipt.
                with self.store.transaction():
                    mission = self.store.get_mission(listed.id)
                    if mission is None or mission.status in TERMINAL_MISSION:
                        continue
                    event = self._pending_planning_wait(mission.id)
                    if event is None or self._planner_intents_in_flight(mission.id):
                        continue
                    if self._requirements_unconfirmed(mission):
                        continue
                    dispatch = self._dispatch_for(mission.id)
                    if dispatch is None:
                        continue
                    try:
                        refs = tuple(
                            PlanningRefV1.from_json(item, "planning_wait.ref")
                            for item in event.payload.get("wait_for", ())
                        )
                    except ContractError:
                        continue
                    if not refs or not all(
                        self._planning_wait_ref_waitable(mission, ref, registered=True)
                        for ref in refs
                    ):
                        self._stop_planning_round(
                            mission.id,
                            reason="wait_target_unavailable",
                            detail={
                                "phase": "wait_wakeup",
                                "decision_id": event.payload.get("decision_id"),
                            },
                            stop_reason=MissionStopReason.PLANNING_FAILED,
                        )
                        progressed = True
                        continue
                    if not all(self._planning_wait_ref_satisfied(mission, ref) for ref in refs):
                        continue
                    fingerprint = self._planning_wait_fingerprint(refs)
                    if any(
                        old.type == "PlanningWaitWoken"
                        and old.payload.get("wait_fingerprint") == fingerprint
                        for old in self.store.iter_events(mission.id)
                    ):
                        self._stop_planning_round(
                            mission.id,
                            reason="repeated_wait_without_progress",
                            detail={
                                "phase": "wait_wakeup",
                                "decision_id": event.payload.get("decision_id"),
                            },
                            stop_reason=MissionStopReason.PLANNING_FAILED,
                        )
                        progressed = True
                        continue
                    if self._planning_ladder_spent(mission.id):
                        self._stop_planning_round(
                            mission.id,
                            reason="planning_bound_reached",
                            detail={"phase": "wait_wakeup"},
                            stop_reason=MissionStopReason.PLANNING_FAILED,
                        )
                        progressed = True
                        continue
                    ordinal = self._next_planning_ordinal(mission.id)
                    intent = self._create_planner_intent_now(mission.id, ordinal=ordinal)
                    append_hierarchical_event(
                        self.store,
                        "PlanningWaitWoken",
                        mission.id,
                        key=f"{event.idempotency_key}:woken",
                        payload={
                            "decision_id": event.payload.get("decision_id"),
                            "wait_for": [ref.to_json() for ref in refs],
                            "wait_fingerprint": fingerprint,
                            "ordinal": ordinal,
                            "intent_id": intent.intent_id,
                            "registration_key": event.idempotency_key,
                            "settled_tasks": {
                                row["task_id"]: row["task_status"]
                                for row in intent.config["planning_package"]["views"]["goals"]
                                if "task_status" in row
                                and any(
                                    ref.kind is PlanningRefKind.TASK and ref.id == row["task_id"]
                                    for ref in refs
                                )
                            },
                        },
                    )
                    self.store.fault("planning_wait_before_wake_commit")
                progressed = True
            except BudgetExhausted as error:
                self._stop_planning_round(
                    listed.id,
                    reason="budget_exhausted",
                    detail={
                        "phase": "wait_wakeup",
                        "dimension": error.dimension,
                        "requested": error.requested,
                        "remaining": error.remaining,
                    },
                    stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
                )
                progressed = True
            except RoutingUnavailable:
                # Retain the WAIT; do not enqueue an unbound deferred planner that
                # could bypass target revalidation on the next cycle.
                continue
            except PointUseRefused as refused:
                # 同普通开轮：保留 WAIT，这一轮不开，不算故障（A26 裁决建议 1）
                self._planning_round_waits(listed.id, refused, path="wait_wakeup")
                continue
            except ContextRejected as error:
                self._stop_planning_round(
                    listed.id,
                    reason="context_rejected",
                    detail={"phase": "wait_wakeup", "error": str(error)[:300]},
                    stop_reason=MissionStopReason.CONTEXT_REJECTED,
                )
                progressed = True
        return progressed
