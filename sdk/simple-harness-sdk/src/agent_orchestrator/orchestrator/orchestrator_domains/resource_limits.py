# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Backpressure, tool-call room, resource grants and stop conditions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...contracts import (
    TERMINAL_TASK,
    AttemptStatus,
    ContractError,
    Mission,
    MissionStopReason,
    Task,
    TaskStatus,
)
from ...governance.budgets import BudgetError
from ...graph.projection_validation import GraphIntegrityError
from ...scheduling.allocator import OPEN_ATTEMPT_STATES
from ...scheduling.backpressure import (
    BacklogResponse,
    BackpressureState,
    Observation,
    backlog_response,
)
from ...storage.store import StoreError
from .. import planning_repair_requests as repair_requests
from .. import resource_requests
from .. import stop_conditions as stop_rules
from ..budget_accounts import mission_account, task_account
from ..hierarchical_dispatch import HierarchicalDispatch
from ..planning_retry import pending_retry_permit, retry_decision_required
from ..resource_requests import quota_check
from .core import OrchestratorCore


class ResourceLimitMixin(OrchestratorCore):
    """Backpressure, tool-call room, resource grants and stop conditions."""

    __slots__ = ()

    def _observe_pressure(self, active: set[str]) -> None:
        """D6-2: one watermark evaluation per cycle, before any allocation."""

        running = self.store.count_attempts_by_status(
            str(AttemptStatus.CLAIMED), str(AttemptStatus.RUNNING)
        )
        pending_dispatch = sum(
            1
            for intent in self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
            if intent.kind == "attempt"
            and intent.mission_id in active
            and self.profile_of(intent) in self.assembled.pools  # review P1-4
        )
        pending_verifications = sum(
            1
            for stored in self.store.list_results_by_verification("PENDING", "RUNNING")
            if stored.envelope.mission_id in active
        )
        observation = Observation(
            running_attempts=running,
            pending_dispatch=pending_dispatch,
            pending_verifications=pending_verifications,
            observed_at=self.store.now,
        )
        state, transitions = self.commit.record_backpressure(
            observation, limits=self._config.backpressure_limits(), mission_ids=sorted(active)
        )
        self._pressure = state
        for transition in transitions:
            self._note(
                f"backpressure {transition.to_level.lower()} on {transition.dimension}: "
                f"{transition.observed} (high {transition.high} / low {transition.low})"
            )
        self._backlog = self._backlog_response(state)
        if self.commit.record_backlog_response(self._backlog, mission_ids=sorted(active)):
            self._note(
                f"backlog response: verifiers {self._backlog.verifier_workers}"
                f" (ceiling {self._backlog.verifier_ceiling}), new planning rounds "
                f"{'paused' if self._backlog.decomposition_paused else 'open'} ({self._backlog.reason})"
            )

    def _backlog_response(self, state: BackpressureState) -> BacklogResponse:
        """推后第 3 批 H12：这一轮的积压应对（纯计算，见 ``backlog_response``）。"""

        return backlog_response(
            state,
            verifier_workers=self._config.verifier_workers,
            verifier_ceiling=int(
                self._config.verifier_workers_ceiling or self._config.verifier_workers
            ),
            now=self._store.now if self._store is not None else 0.0,
            pause_seconds=self._config.decomposition_pause_seconds,
        )

    def _verifier_capacity(self) -> int:
        """D6-9' 的审阅名额：平时是 ``verifier_workers``，积压时升到上限（推后第 3 批 H12）。"""

        return self._backlog.verifier_workers

    def _decomposition_paused(self, mission: Mission) -> bool:
        """推后第 3 批 H12：审阅积压中，自己有结果在排队等审的任务先不开新规划轮。

        只停排队的那几个任务（试用前第 5 步）：别的任务没有往积压里加东西，照常规划；首次
        规划没有结果，也不受影响。人答完问题的那一轮由调用方放行。"""

        if not self._backlog.decomposition_paused:
            return False
        return self.store.has_unverified_results(mission.id)

    @property
    def pressure(self) -> BackpressureState:
        return self._pressure

    def _tool_call_room(self, mission: Mission, task: Task) -> tuple[int | None, int | None]:
        """(reservable now, not yet spent) on the Task → Mission → Global chain."""

        accounts = [task_account(task.id), mission_account(mission.id)]
        reservable: int | None = None
        spent_room: int | None = None
        with self.store.transaction():
            # the Global account of the Mission's creation month is its pool's parent
            try:
                pool_parent = self.commit.ledger.account(mission_account(mission.id)).parent_id
            except BudgetError:
                pool_parent = None
            if pool_parent is not None:
                accounts.append(pool_parent)
            for account_id in accounts:
                try:
                    snap = self.commit.ledger.account(account_id)
                except BudgetError:
                    continue
                limit = snap.limits.max_tool_calls
                if limit is None:
                    continue
                room = limit - snap.reserved_tool_calls - snap.settled_tool_calls
                left = limit - snap.settled_tool_calls
                reservable = room if reservable is None else min(reservable, room)
                spent_room = left if spent_room is None else min(spent_room, left)
        return reservable, spent_room

    def _base_tool_cap(self, task: Task) -> int:
        """一次尝试的基础工具上限：部署的单回合上限，Task 预算有这一维时取小。"""

        cap = self._config.max_tool_calls_per_turn
        if task.budget.max_tool_calls is not None:
            cap = min(cap, task.budget.max_tool_calls)
        return int(cap)

    def _check_resource_request(self, mission: Mission, task: Task, request: Any) -> dict[str, Any]:
        """推后第 3 批 H10：只核数——上限（一次最多再要一份基础额度）与账户链上还没花掉的工具次数。"""

        room = (
            self._tool_call_room(mission, task)[1]
            if self._tool_calls_limited(mission, task)
            else None
        )
        return {
            "request": request.to_json(),
            "check": quota_check(request, base_cap=self._base_tool_cap(task), room=room),
        }

    def _resource_grant(self, mission: Mission, task: Task) -> dict[str, Any] | None:
        """推后第 3 批 H10：规划器的原样重试许可指向的那次失败尝试带着核过的申请 → 再核一次额度后发放。

        额度这时已不够就不发放（记 ``ResourceGrantLapsed``），这次尝试按基础额度开工。"""

        if not retry_decision_required(self.store, mission.id, task.id):
            return None
        permit = pending_retry_permit(self.store, mission.id, task.id)
        if permit is None:
            return None
        failed = str(permit["failed_attempt_id"])
        event = resource_requests.request_for_attempt(self.store.iter_events(mission.id), failed)
        if event is None or event.payload["check"].get("fits") is not True:
            return None
        base = self._base_tool_cap(task)
        amount = int(event.payload["request"]["amount"])
        room = (
            self._tool_call_room(mission, task)[0]
            if self._tool_calls_limited(mission, task)
            else None
        )
        if amount > base or (room is not None and base + amount > room):
            self.commit.record_resource_grant_lapsed(
                mission.id,
                task_id=task.id,
                failed_attempt_id=failed,
                detail={"amount": amount, "base_cap": base, "reservable": room},
            )
            return None
        return {
            "failed_attempt_id": failed,
            "dimension": "tool_calls",
            "amount": amount,
            "base_cap": base,
            "decision_id": permit.get("decision_id"),
        }

    def _search_call_room(self, mission: Mission, task: Task) -> int | None:
        """推后第 3 批 H08：Task → Mission → Global 链上检索次数还能预留多少（不小于 0）；
        链上都不限时为 None（不预留、网关不拦）。"""

        room: int | None = None
        with self.store.transaction():
            accounts = [task_account(task.id), mission_account(mission.id)]
            try:
                parent = self.commit.ledger.account(mission_account(mission.id)).parent_id
            except BudgetError:
                parent = None
            if parent is not None:
                accounts.append(parent)
            for account_id in accounts:
                try:
                    left = self.commit.ledger.account(account_id).remaining_search_calls()
                except BudgetError:
                    continue
                if left is not None:
                    room = max(0, left) if room is None else max(0, min(room, left))
        return room

    def _tool_calls_limited(self, mission: Mission, task: Task) -> bool:
        """Whether any account on the Task's chain caps tool calls (only then is a
        reservation meaningful; an unlimited dimension is never reserved)."""

        if task.budget.max_tool_calls is not None or mission.budget.max_tool_calls is not None:
            return True
        limits = self._config.global_budget
        return limits is not None and limits.max_tool_calls is not None

    async def _runtime_exhausted(self, mission: Mission, tasks: Sequence[Task]) -> bool:
        """D6-8 / §18.1 wall-clock dimension: a Mission (or Task) past ``max_runtime_seconds``
        gets no new allocation.  Hierarchical Missions release HELD/UNKNOWN grants
        through ``_commit_fail_mission`` (P2.3r / N9); unknown usage stays on the
        books.  Legacy still holds the reservation (ORCH §12.2)."""

        now = self.store.now
        limit = mission.budget.max_runtime_seconds
        waited = self.store.human_wait_seconds(
            mission.id, now
        )  # D7-7': a person's time is not run time
        if limit is not None and now - mission.created_at - waited >= limit:
            detail = {
                "dimension": "runtime",
                "elapsed_seconds": round(now - mission.created_at - waited, 3),
                "human_wait_seconds": round(waited, 3),
                "max_runtime_seconds": limit,
                "account": mission_account(mission.id),
            }
            self._commit_fail_mission(
                mission.id, stop_reason=MissionStopReason.BUDGET_EXHAUSTED, detail=detail
            )
            await self._release_mission(mission.id)
            self._note(f"mission {mission.id} stopped: budget_exhausted (runtime)")
            return True
        for task in tasks:
            cap = task.budget.max_runtime_seconds
            if cap is None or task.status in TERMINAL_TASK:
                continue
            attempts = self.store.list_attempts(task.id)
            if not attempts:
                continue
            started = min(a.created_at for a in attempts)
            if now - started >= cap and not any(a.status in OPEN_ATTEMPT_STATES for a in attempts):
                self._commit_stop_task(
                    task.id,
                    stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
                    detail={
                        "dimension": "runtime",
                        "elapsed_seconds": round(now - started, 3),
                        "max_runtime_seconds": cap,
                        "account": task_account(task.id),
                    },
                )
                await self._release_mission(mission.id)
                self._note(f"task {task.id} stopped: budget_exhausted (runtime)")
                return True
        return False

    async def _stop_conditions_reached(
        self, mission: Mission, new_mode: HierarchicalDispatch
    ) -> bool:
        """第 2 批车道 H（H06，原计划 §5 ``stop_conditions`` / §19.1）：任务自带的两种计数型停止。

        Harness 只数：规划轮之间知识库 / 验收记录有没有新增、结果内容哈希重复了多少（``stop_conditions``
        模块）。达到上限不直接停——与"没有可派发的工作"同一条路，先把事实交给规划器一次（每个计划版本
        每个条件一条请求，跨版本累计问满 ``max_planning_attempts`` 次不再问）；规划器了结了请求、这一
        版计划没有改动（计划改了版本号就变，这条请求随之过时），才按条件的名字停。规划器还没答、或在
        等人时不停。返回 True 表示这一轮写了事件（记了请求或停了任务）。
        """

        policy = self._config.deployment_policy
        if not stop_rules.parse_stop_conditions(mission.stop_conditions, policy):
            return False
        events = tuple(self.store.iter_events(mission.id))
        reached = stop_rules.reached_stop_conditions(
            mission,
            policy,
            streak=stop_rules.knowledge_streak(events),
            hashes=stop_rules.result_hashes(self.store, mission.id, events=events),
        )
        if not reached:
            return False
        # 有步骤已放行、还没开工时，不问规划器"这一版计划不改就停？"——它会在一步正要开工时被问
        # 是否结束，最后去问人。等那一步开了工（或被挡住）再说。只看事实：READY 的步骤。
        if any(
            task.status is TaskStatus.READY and not task.paused
            for task in self.store.list_tasks(mission.id)
        ):
            return False  # 暂停的步骤不会开工，不算"正要开工"
        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            self._note(
                f"mission {mission.id}: stop conditions reached but the plan is unreadable ({error})"
            )
            return False
        plan_revision = int(network.plan_revision)
        first = reached[0]
        name = str(first["condition"])
        source_key = stop_rules.stop_condition_key(mission.id, name, plan_revision)
        state = stop_rules.stop_request_state(events, source_key)
        if state is None:
            cap = int(self._config.max_planning_attempts)
            asked_before = stop_rules.stop_condition_asks(events, name)
            if asked_before < cap:
                tasks = tuple(str(spec.task_id) for spec in network.occurrences)
                roots = tuple(
                    str(network.occurrence(item).task_id) for item in network.root_occurrence_ids
                )
                try:
                    recorded = repair_requests.record_request(
                        new_mode,
                        mission.id,
                        event_type="NoDispatchableWork",
                        trigger_refs=roots or (mission.id,),
                        source_key=source_key,
                        detail={
                            "reason": "stop_condition_reached",
                            "conditions": reached,
                            "plan_revision": plan_revision,
                            "explanation": (
                                "任务自带的停止条件已达上限（只是计数）；这一版计划不改动，"
                                "任务就按该条件停止"
                            ),
                            repair_requests.NO_CHANGE_SETTLES: True,
                        },
                        scope=tasks
                        + tuple(str(spec.occurrence_id) for spec in network.occurrences),
                    )
                except (GraphIntegrityError, ContractError, StoreError) as error:
                    self._note(
                        f"mission {mission.id}: stop condition could not be handed to the Planner ({error})"
                    )
                    return False
                if recorded:
                    self._note(
                        f"mission {mission.id}: stop condition {name} reached; the Planner is asked "
                        "once for this plan revision before the Mission is stopped"
                    )
                return recorded
            planner_asked: dict[str, Any] | None = {
                "request_id": None,
                "asked_before": asked_before,
                "cap": cap,
            }
        else:
            if not state["addressed"]:
                return False  # 规划器还没答：等
            planner_asked = dict(state)
        if self._has_pending_planning_waits(mission.id) or self.store.list_approvals(
            mission.id, "PENDING"
        ):
            return False  # 在等人或等规划器的别的事：不是停的时候
        self._commit_fail_mission(
            mission.id,
            stop_reason=stop_rules.reason_for(name),
            detail={
                "conditions": reached,
                "plan_revision": plan_revision,
                "planner_asked": planner_asked,
                "stop_conditions": list(mission.stop_conditions),
            },
        )
        self._note(
            f"mission {mission.id}: stop condition {name} reached and the plan stayed; stopped"
        )
        return True
