# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Planning rejection, planner retries and plan collection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from simple_harness.agents import AgentTurnState

from ...contracts import ContractError, Mission, MissionStatus, MissionStopReason, TaskStatus
from ...contracts.error_table import refusal_charges_planner, refusal_free_reask
from ...contracts.htn import TaskForm
from ...contracts.models import jsonable
from ...contracts.semantic_base import content_hash_of
from ...graph.projection_validation import GraphIntegrityError
from ...planning.decision_feedback import refusal_text
from ...runtime.model_router import _error_codes
from ...storage.store import DispatchIntent, StoreError
from ..assurance_review_collect import collect_assurance_review
from ..failure_classes import output_exhausted
from ..hierarchical_dispatch import HierarchicalDispatch
from ..planning_method_proposal import MAX_METHOD_PROPOSALS_PER_GOAL, proposals_for
from ..planning_repair_requests import pending_requests, record_request
from ..planning_retry import retry_decision_required
from .core import OrchestratorCore

#: ``plan`` intents that are not Planner rounds (NEXT-TG-1.0 §0.6 overlap 3).
NOT_PLANNER_ROLES = frozenset(
    {
        "operation_proposal_reviewer",
        "operation_outcome_reviewer",
    }
)

#: Deterministic 4xx provider refusals.  Runtime already settles
#: ``ProviderAuthenticationError`` / ``ProviderPaymentRequiredError`` as FAILED
#: (``_DEFINITE_PROVIDER_FAILURES``); this set is what the orchestrator reads off
#: a FAILED turn so it does not climb the planning ladder or RETRY_WAIT.
DEFINITE_AUTH_CODES = frozenset({"provider_authentication_failed", "provider_payment_required"})
#: One re-hand-off per subject.  A second executor asking the same question is a
#: retry; a third is a loop that spends the Mission account on a Provider that is
#: down, which is what the deadline exists to end.
MAX_SERVICE_REHANDOFFS = 1
#: P2.3p: consecutive after-handoff 0-token UNKNOWNs on one Mission before the
#: loop stops as ``runtime_unavailable``.  Same width as P2.3f's per-subject
#: retry — the original hand-off plus :data:`MAX_SERVICE_REHANDOFFS` re-hand-offs.
#: Not a config item.
MAX_CONSECUTIVE_AFTER_HANDOFF_UNKNOWNS = MAX_SERVICE_REHANDOFFS + 1


# 2026-09-28: how many Planner turns that produced no reply (provider error, timeout,
# an interrupted run) one planning question may absorb before they count again.
PLANNER_TURN_FAILURE_GRACE = 6


class PlannerTurnFailed(ContractError):
    """The Planner's turn ended without a reply; nothing it said was refused."""


def planning_failure_detail(error: Exception, detail: dict[str, Any]) -> dict[str, Any]:
    if isinstance(error, PlannerTurnFailed):
        detail["turn_failed"] = True
    return detail


def _turn_failed(event: Any) -> bool:
    detail = event.payload.get("detail") if isinstance(event.payload, Mapping) else None
    return isinstance(detail, Mapping) and detail.get("turn_failed") is True


def _planning_turn_unanswered(event: Any) -> bool:
    """The Planner was not heard this round: the turn failed, or its outcome is unknown
    (handed off, line dropped).  Both share one grace (``PLANNER_TURN_FAILURE_GRACE``);
    V30: ``_planning_attempts`` and ``_planner_turn_failure_forgiven`` read this one predicate."""
    return _turn_failed(event) or event.payload.get("reason") == "provider_outcome_unknown"


def _refusal_codes(event: Any) -> list[str]:
    """The problem codes a ``PlanningRejected`` event names."""
    detail = event.payload.get("detail") if isinstance(event.payload, Mapping) else None
    problems = detail.get("problems") if isinstance(detail, Mapping) else None
    preview = detail.get("preview") if isinstance(detail, Mapping) else None
    mapped = preview.get("mapped_problems") if isinstance(preview, Mapping) else None
    codes = detail.get("rejection_codes") if isinstance(detail, Mapping) else None
    return [
        *(str(item.get("code")) for item in problems or () if isinstance(item, Mapping)),
        *(str(item) for item in mapped or ()),
        *(str(item) for item in (codes if isinstance(codes, (list, tuple)) else ())),
    ]


def _free_reask(event: Any) -> bool:
    """这条拒绝是同一请求的第一次回答读不懂（错误码表 free_reask 列），重问一次不算答错
    （2026-10-09 四项修复第 2 条）。重问仍读不懂（本请求第 2 次回答）照常算；输出上限用完
    不是格式错，照常算。"""

    detail = event.payload.get("detail") if isinstance(event.payload, Mapping) else None
    if not isinstance(detail, Mapping) or detail.get("output_exhausted") is True:
        return False
    return detail.get("request_attempt_ordinal") == 0 and refusal_free_reask(_refusal_codes(event))


class PlanningFeedbackMixin(OrchestratorCore):
    """Planning rejection, planner retries and plan collection."""

    __slots__ = ()

    async def _planning_rejected(
        self, intent: DispatchIntent, *, reason: str, detail: Mapping[str, Any]
    ) -> None:
        """规划器的一次回答没被采纳之后，还能不能再问（规划预算，片 A 第 8 项）。

        此前这里是六条各自计数的阶梯（格式重试、回合重试、格式阶梯、规划阶梯、修复阶梯、
        "已有计划不判失败"），再叠上"合成成功多给一次"。现在只有两个数：

        * **服务故障宽限**（``PLANNER_TURN_FAILURE_GRACE``）：没拿到回复的回合（服务端报错、
          超时、被重启打断）不算答错，宽限内原样再问；超出即按"运行环境不可用"停。
        * **答错次数**（``max_planning_attempts``）：自上一次提交成功起，被拒的回答——读不懂、
          不被准入、提交被拒——累计到上限即停。2026-10-09（用户推翻 10-01 第 8 项那一句）：同一
          请求第一次读不懂时带着错误重问一次**不算答错**（错误码表 ``free_reask`` 列），重问仍
          读不懂才算；没拿到回复的回合不占这一次重问名额。

        已有计划的任务：被拒的这一问如果没有什么还欠着（没有待处理的修复请求、没有等重试
        决定的步骤、没有还没做法的目标），任务带着现有计划继续，不为它判失败。
        """

        mission = self.store.get_mission(intent.mission_id)
        assert mission is not None
        ordinal = int(intent.config.get("ordinal", 1))
        self._note(f"planning attempt {ordinal} rejected: {reason}")
        if reason != "task_graph_rejected":  # graph rejections are already durable events
            self.commit.record_planning_rejected(
                mission.id, ordinal=ordinal, reason=reason, detail=detail
            )
        streak = self._planning.after_handoff_zero_streak.get(mission.id, 0)
        if (
            reason == "provider_outcome_unknown"
            and streak >= MAX_CONSECUTIVE_AFTER_HANDOFF_UNKNOWNS
        ):
            # P2.3p: no further planner ordinal after consecutive after-handoff
            # 0-token UNKNOWNs (C2 r0).
            await self._fail_runtime_unavailable(
                mission,
                reason=reason,
                detail={
                    "attempts": ordinal,
                    "consecutive_after_handoff_unknowns": streak,
                    **dict(detail),
                },
            )
            return
        no_reply = detail.get("turn_failed") is True
        if no_reply and not self._planner_turn_failure_forgiven(mission.id):
            # Past the grace the model service is treated as down.
            self._stop_planning_round(
                mission.id,
                reason="planner_turn_failures_exhausted",
                detail={"attempts": ordinal, **dict(detail)},
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            )
            return
        planning = mission.status is MissionStatus.PLANNING
        owed = planning or self._planning_still_owed(mission)
        if self._planning_ladder_spent(mission.id):
            if planning:
                # P2.3l / N5: a round that never reached a model is not a planning failure.
                stop = (
                    MissionStopReason.RUNTIME_UNAVAILABLE
                    if reason == "provider_outcome_unknown"
                    else MissionStopReason.PLANNING_FAILED
                )
                self._commit_fail_planning(
                    mission.id,
                    reason=reason,
                    detail={"attempts": ordinal, **dict(detail)},
                    stop_reason=stop,
                )
            elif owed:
                self._stop_planning_round(
                    mission.id,
                    reason="planning_attempts_exhausted",
                    detail={
                        "attempts": ordinal,
                        "rejected_rounds": self._planning_attempts(mission.id),
                        "last_reason": reason,
                        **dict(detail),
                    },
                    stop_reason=MissionStopReason.PLANNING_FAILED,
                )
            else:
                self._note(
                    f"mission {mission.id}: planning round {ordinal} rejected ({reason}); the "
                    "committed plan stands and the Mission is not failed for it"
                )
            return
        if not owed:
            self._note(
                f"mission {mission.id}: planning round {ordinal} rejected ({reason}); nothing is "
                "owed to the plan, so the Planner is not asked again"
            )
            return
        format_retry = (
            reason == "proposal_unreadable"
            and "planning_decision_attempt_ordinal" in intent.config
            # 输出上限用完不是格式写错、没拿到回复也不是：都不占同一请求的格式重问名额，
            # 下一问是新一轮（2026-10-09）
            and detail.get("output_exhausted") is not True
            and not no_reply
            and self._planning_format_retry_remaining(intent=intent, mission=mission) > 0
        )
        await self._planner_round_on_committed_plan(
            mission.id,
            # the same request's format retry is the very next ordinal
            ordinal=ordinal + 1 if format_retry else self._next_planning_ordinal(mission.id),
            phase="planning_format_retry" if format_retry else "planning_ladder",
        )

    def _planning_still_owed(self, mission: Mission) -> bool:
        """Something only a planning round can give is still outstanding."""

        if self._repair_still_owed(mission.id):
            return True
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return False

        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return False
        return any(
            spec.form is TaskForm.COMPOUND
            and network.adopted_instance_for(spec.occurrence_id) is None
            for spec in network.occurrences
        )

    def _dispatch_h4_repair_trigger(
        self,
        mission: Mission,
        *,
        event_type: str,
        trigger_ref: str,
        detail: Mapping[str, Any],
    ) -> None:
        """Route a live failure into H4's durable repair boundary.

        The adapter does not choose a new method or mutate the plan.  It records the
        normalized trigger and program-computed impact so a later repair round can
        resume from the same request after a process restart.
        """

        new_mode = self._new_mode(mission)
        if new_mode is None:
            return

        # This hook receives an original durable failure. It opens a request; it
        # must never invent RETRY_SAME_METHOD on behalf of a model.
        record_request(
            new_mode,
            mission.id,
            event_type=event_type,
            trigger_refs=(trigger_ref,),
            source_key=f"hook:{event_type}:{trigger_ref}:" + content_hash_of(dict(detail)),
            detail=dict(detail),
        )

    @staticmethod
    def _definite_auth_failure(error: Any) -> bool:
        """HTTP 401/402-class provider refusals the runtime already settled FAILED."""

        return bool(_error_codes(error) & DEFINITE_AUTH_CODES)

    async def _collect_plan(self, intent: DispatchIntent, result) -> None:  # type: ignore[no-untyped-def]
        mission = self.store.get_mission(intent.mission_id)
        assert mission is not None
        self._import_usage(intent)
        if intent.config.get("assurance_protocol") == "assurance-exec-v1.1":
            await collect_assurance_review(self, intent)
            # unbind 对空值是空操作（dict.pop 带默认值）
            self.assembled.gateway.unbind(cast(str, intent.agent_id))
            return
        if result.state is AgentTurnState.COMMITTED:
            self._reset_after_handoff_unknown_streak(mission.id)
        new_mode = self._new_mode(mission)
        if result.state is not AgentTurnState.COMMITTED and self._definite_auth_failure(
            result.error
        ):
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            detail = {"error": jsonable(result.error or {}), "auth": True}
            if mission.status is MissionStatus.PLANNING:
                self._commit_fail_planning(
                    mission.id,
                    reason="provider_unavailable",
                    detail=detail,
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                )
            else:
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                    detail={"reason": "provider_unavailable", **detail},
                )
            self._note(
                f"{intent.subject_id}: definite provider auth/payment failure → runtime_unavailable"
            )
            return
        text = "" if result.public_output is None else str(result.public_output.content)
        echoed = self.bridge_for(intent).echoed_models(agent_id=intent.agent_id or "")
        if echoed and echoed != {self._expected_model(intent)}:
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            self._commit_fail_planning(
                mission.id,
                reason="model_echo_mismatch",
                detail={"expected": self._expected_model(intent), "echoed": sorted(echoed)},
                stop_reason=MissionStopReason.MODEL_ECHO_MISMATCH,
            )
            self._note(f"planner: model echo mismatch {sorted(echoed)} → mission stopped")
            return
        # P2.3b: the Planner speaks the typed contract (§18.3); the reply goes to the
        # assembly.  With no assembly installed there is nothing that could read it.
        if new_mode is None:
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            self._note(f"{intent.subject_id}: planner reply with no assembly installed")
            return
        await self._collect_plan_hierarchical(intent, result, mission, text, new_mode)

    def _planner_intents_in_flight(self, mission_id: str) -> bool:
        """An open ``plan`` intent that is a *Planner* round, not a synthesis round.

        The two ride on the same intent kind, so "is a plan intent open" answers yes to
        a synthesis round as well — which is right for "do not ask two questions at
        once" and wrong for "has the Planner already been asked".

        Operation proposal / outcome reviews ride on the same kind too, and they run
        in the middle of a Mission: counting them held a repair round for another
        branch until an unrelated publish was reviewed (NEXT-TG-1.0 §0.6 overlap 3,
        §7.3 item 4). A root review still counts: planning under it would stale it.
        """

        return any(
            intent.kind == "plan"
            and intent.mission_id == mission_id
            and str(intent.config.get("role", "")) not in NOT_PLANNER_ROLES
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            )
        )

    def _planning_attempts(self, mission_id: str) -> int:
        """Refused rounds since the Planner last had a decision committed.

        2026-09-26 Host run: counted over the Mission's whole life, two provider
        hiccups in one repair round and one misspelt reply in a later, unrelated
        round ended a Mission whose every earlier round had been answered.  The
        bound is how many times the Planner may be wrong about the *same*
        question; a committed decision answers it and the next question starts
        its own count.  Before the first commit this is the old count."""

        count = 0
        forgiven = 0
        for event in self.store.list_events(mission_id):
            if event.type == "PlanningRejected":
                # 2026-09-28 真机：12 轮规划里 5 轮是模型服务端报错与重启打断，规划器根本
                # 没被听到却照样扣次数，任务因此失败。没有回复的回合不算"答错"，但设宽限，
                # 服务一直坏着时超出部分照样计数，不会无限重试。
                # 规划器自己的调用结果不明（交出后线路断了）同样不是它答错，
                # 与回合失败走同一道宽限（用户 09-28：服务出错不计次数）；连续不明另有上限
                # （MAX_CONSECUTIVE_AFTER_HANDOFF_UNKNOWNS），不会无限重试。
                if _planning_turn_unanswered(event) and forgiven < PLANNER_TURN_FAILURE_GRACE:
                    forgiven += 1
                    continue
                if not refusal_charges_planner(_refusal_codes(event)):
                    continue  # 请求过期：规划器作答期间世界变了，不算它答错（错误码表那一列）
                if _free_reask(event):
                    continue  # 同一请求第一次读不懂：免扣重问一次（错误码表 free_reask 列）
                count += 1
            elif (
                event.type == "PlanningDecisionEvaluated"
                and event.payload.get("status") == "COMMITTED"
            ):
                count = 0
                forgiven = 0
        return count

    def _planner_turn_failure_forgiven(self, mission_id: str) -> bool:
        """The latest refusal was a turn that produced no reply and is inside the grace."""

        forgiven = 0
        latest_forgiven = False
        for event in self.store.list_events(mission_id):
            if event.type == "PlanningRejected":
                # 与 ``_planning_attempts`` 同一口径——结果不明也是"没被听到"
                latest_forgiven = (
                    _planning_turn_unanswered(event) and forgiven < PLANNER_TURN_FAILURE_GRACE
                )
                forgiven += 1 if latest_forgiven else 0
            elif (
                event.type == "PlanningDecisionEvaluated"
                and event.payload.get("status") == "COMMITTED"
            ):
                forgiven = 0
                latest_forgiven = False
        return latest_forgiven

    def _awaiting_retry_decision(self, mission_id: str, task: Any) -> bool:
        """An ACTIVE leaf whose latest attempt ended failed and that waits for the
        planner's retry decision: nothing of it is running."""

        return task.status is TaskStatus.ACTIVE and retry_decision_required(
            self.store, mission_id, task.id
        )

    def _repair_still_owed(self, mission_id: str) -> bool:
        """A repair is still owed to this Mission: an unaddressed repair request, or a
        leaf waiting for a retry decision that only a planning round can give."""

        if pending_requests(self.store, mission_id):
            return True
        return any(
            self._awaiting_retry_decision(mission_id, task)
            for task in self.store.list_tasks(mission_id)
        )

    def _planning_ladder_spent(self, mission_id: str) -> bool:
        """答错次数用完了：自上一次提交成功起，被拒的回答已到 ``max_planning_attempts``。"""

        return self._planning_attempts(mission_id) >= int(self._config.max_planning_attempts)

    def _method_proposals_remaining(self, mission: Mission, new_mode: HierarchicalDispatch) -> int:
        """How many more methods the Planner may propose for the goals still open."""

        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return 0

        open_goals = [
            str(spec.task_id)
            for spec in network.occurrences
            if spec.form is TaskForm.COMPOUND
            and network.adopted_instance_for(spec.occurrence_id) is None
        ]
        return sum(
            max(0, MAX_METHOD_PROPOSALS_PER_GOAL - proposals_for(self.store, mission.id, goal))
            for goal in open_goals
        )

    async def _collect_plan_hierarchical(  # type: ignore[no-untyped-def]
        self,
        intent: DispatchIntent,
        result,
        mission: Mission,
        text: str,
        new_mode: HierarchicalDispatch,
    ) -> None:
        """The Planner's reply → one evaluated planning Decision.

        What belongs *here* is the part that is about the dispatch intent: a turn that
        never committed produced no Decision to evaluate, so it takes the planning-
        rejection path; everything else is :meth:`_collect_plan_decision`.
        """

        if result.state is not AgentTurnState.COMMITTED:
            if output_exhausted(result.error):
                # 2026-10-09：思考写满输出、没有正文，不是服务出错，是模型这一轮没完成——按一条
                # 空回复评估（读不懂、扣答错次数、事实进下一问的 previous_feedback），不走服务故障宽限。
                await self._collect_plan_decision(intent, result, mission, "", new_mode)
                return
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            error = PlannerTurnFailed(f"planner turn failed: {dict(result.error or {})}")
            await self._planning_rejected(
                intent,
                reason="proposal_unreadable",
                detail=planning_failure_detail(error, {"error": refusal_text(error)}),
            )
            return
        await self._collect_plan_decision(intent, result, mission, text, new_mode)
