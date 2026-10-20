# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Mission creation, planning, cancellation, failure, judgement and stopping."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from ...assurance.codec import AssuranceError
from ...contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_TASK,
    Attempt,
    AttemptStatus,
    ContractError,
    Event,
    Mission,
    MissionStatus,
    MissionStopReason,
    Task,
    TaskStatus,
    ids,
)
from ...contracts.evidence_state import Validity
from ...contracts.htn import TaskForm
from ...contracts.models import jsonable, sha256_hex
from ...governance.budget_limits import inherit_limits
from ...governance.domains import DomainProfileV1, resolve_domain
from ...graph.projection_validation import GraphIntegrityError
from ...graph.terminal import accepted_tasks, finalizer_task_id
from ...observability.lineage import lineage
from ...runtime import operation_reconciliation
from ...scheduling.allocator import OPEN_ATTEMPT_STATES
from ...storage import htn_store
from ...storage.assurance_store import AssuranceStore
from ...storage.assurance_work import atomic
from ...storage.source_records import replay_scope_digest
from ...storage.store import DispatchIntent
from ...verification.conflicts import mission_disputes
from ..assurance_factory import record_mission_creation, validate_creation_replay
from ..assurance_final_writer import (
    assured_closeout_pending,
    request_assured_closeout,
    request_assured_notification,
)
from ..budget_accounts import Reservation, global_account_id, mission_account
from ..completion_status import read_occurrence_completion
from ..hierarchical_dispatch import HierarchicalDispatch
from ..plan_commits import SEMANTICS_KEY, normalise_semantics
from ..planning_protocol_binding import (
    bind_planning_protocol,
    checked_planning_protocol,
    planning_protocol_replay_conflict,
)
from ..state_machine import next_mission, next_task
from .core import CommitServiceCore
from .support import HIERARCHICAL_JUDGMENT_REFUSED, CommitRejected, MissionConflict, MissionSpec

if TYPE_CHECKING:
    from ..commit_service import CommitService


class MissionLifecycleCommitsMixin(CommitServiceCore):
    """Mission creation, planning, cancellation, failure, judgement and stopping."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _close_attempt(
            self, attempt: Attempt, target: AttemptStatus, *, reason: str
        ) -> Attempt: ...
        def _follow_global_quota(self, account_id: str) -> None: ...
        def _settle_intent(self, intent: DispatchIntent, state: str) -> DispatchIntent: ...
        def _unbound_open_tasks(self, mission_id: str) -> list[str]: ...

        def bind_policy(
            self,
            mission_id: str,
            *,
            provider_kind: str,
            default_params: Mapping[str, Any] | None = ...,
        ) -> dict[str, Any]: ...

        def protected_tail_hold(self, hold_id: str) -> dict[str, Any] | None: ...

        @staticmethod
        def critic_tail_id(attempt_id: str) -> str: ...

        def consume_critic_tail(
            self,
            *,
            attempt_id: str,
            task_id: str,
            subject_id: str,
            account_id: str,
            reservation: Any,
            semantic_revision: str,
        ) -> dict[str, Any]: ...

        def protected_tail_revision(self, task_id: str) -> str: ...

        def _cancel_open_actions(self, mission_id: str, *, reason: str) -> list[dict[str, Any]]: ...

        def release_terminal_tail_holds(
            self, *, mission_id: str, task_id: str | None = ..., attempt_id: str | None = ...
        ) -> list[str]: ...

    def create_mission(
        self,
        spec: MissionSpec,
        *,
        provider_kind: str = "unknown",
        policy_defaults: Mapping[str, Any] | None = None,
    ) -> tuple[Mission, bool]:
        """Idempotent on (tenant_id, idempotency_key); a different spec is a conflict."""

        if spec.runtime_profile_id is not None and (
            not isinstance(spec.runtime_profile_id, str) or not spec.runtime_profile_id.strip()
        ):
            raise CommitRejected("runtime_profile_id must be a nonempty profile reference")
        try:  # §18.5: an unknown semantics version is refused before anything is written
            semantics_version = normalise_semantics(spec.orchestration_semantics_version)
        except ContractError as error:
            raise CommitRejected(str(error)) from error
        try:  # §8.1: a spec that skipped ``__post_init__`` is refused before any write
            checked_planning_protocol(spec.planning_protocol_version)
        except ContractError as error:
            raise CommitRejected(str(error)) from error
        spec_hash = sha256_hex(spec.to_json())
        try:  # P3.3 (D1): an unknown domain is refused before anything is written
            domain = resolve_domain(spec.domain)
        except KeyError as error:
            raise CommitRejected(f"unknown domain profile {spec.domain!r}") from error

        with atomic(self._store):
            found = self._store.find_mission(spec.tenant_id, spec.idempotency_key)
            if found is not None:
                mission, stored_hash = found
                if stored_hash != spec_hash:
                    raise MissionConflict(
                        f"mission {mission.id} already exists with a different specification"
                    )
                conflict = planning_protocol_replay_conflict(
                    self._store, mission.id, spec.planning_protocol_version
                )
                if conflict is not None:
                    raise MissionConflict(conflict)

                validate_creation_replay(cast("CommitService", self), mission)
                return mission, False
            mission_id = ids.mission_id(spec.tenant_id, spec.idempotency_key)
            # review fix: with a Global Budget the Mission's unnamed dimensions are inherited, and
            # the Mission contract must say so — graph checks compare against it (D6-1')
            effective_budget = (
                inherit_limits(spec.budget, self._global_budget)
                if self._global_budget is not None
                else spec.budget
            )
            mission = Mission(
                id=mission_id,
                goal=spec.goal,
                success_criteria=spec.success_criteria,
                stop_conditions=spec.stop_conditions,
                allowed_tools=spec.allowed_tools,
                risk_level=spec.risk_level,
                budget=effective_budget,
                tenant_id=spec.tenant_id,
                status=MissionStatus.CREATED,
                created_at=self._store.now,
                version=1,
                idempotency_key=spec.idempotency_key,
                final_report={
                    "task_kind": spec.task_kind,
                    "workspace_seed": dict(spec.workspace_seed),
                    "untrusted_sources": list(spec.untrusted_sources),
                    **(
                        {}
                        if spec.runtime_profile_id is None
                        else {
                            "runtime_profile_id": spec.runtime_profile_id,
                        }
                    ),
                    **(
                        {}
                        if not spec.action_params
                        else {
                            "action_params": {
                                key: dict(value) for key, value in spec.action_params.items()
                            }
                        }
                    ),
                    SEMANTICS_KEY: semantics_version,
                },
            )
            self._store.insert_mission(mission, spec_hash=spec_hash)
            parent: str | None = None
            limits = spec.budget
            if self._global_budget is not None:  # D6-1: Global → Mission (§18.2), never the reverse
                # 按任务建立月份计：任务挂在建立当月的全局总账下，它的全部用量
                # 都记在这个月，哪怕它跨月才跑完。当月第一个任务把这个月的账户打开；上限始终是
                # 部署配置的月配额（配置改了，服务启动时与新任务建立时随之改）。
                parent = global_account_id(mission.created_at)
                self._ledger.open_account(
                    account_id=parent,
                    scope="global",
                    parent_id=None,
                    mission_id="global",
                    limits=self._global_budget,
                )
                self._follow_global_quota(parent)
                # a dimension the Mission does not name is inherited from the Global cap
                # (review P0-2: ``fits_within`` treats an unnamed child dimension as unbounded)
                limits = effective_budget
            self._ledger.open_account(  # BudgetError (does not fit the Global) rolls everything back
                account_id=mission_account(mission_id),
                scope="mission",
                parent_id=parent,
                mission_id=mission_id,
                limits=limits,
            )
            # step 9 (plan D9-3'): the policy version this Mission runs under, in the same
            # transaction — a later configuration never changes it silently
            binding = self.bind_policy(
                mission_id,
                provider_kind=provider_kind,
                default_params=policy_defaults,
            )
            if spec.runtime_profile_id is not None:
                if self._mission_profile_validator is None:
                    raise CommitRejected(
                        "runtime profile selection requires an Orchestrator binding"
                    )
                version = self._store.get_policy_version(str(binding["version_id"]))
                if version is None:
                    raise CommitRejected("bound policy version is unavailable")
                self._mission_profile_validator(
                    spec.runtime_profile_id,
                    dict(version.get("params") or policy_defaults or {}),
                )
            self._store.bind_mission_domain(
                mission_id,
                domain_id=domain.id,
                domain_version=domain.version,
                snapshot=domain.to_json(),
            )
            bind_planning_protocol(self._store, mission_id, spec.planning_protocol_version)
            creation_event = self._emit(
                "MissionCreated",
                mission_id,
                key=mission_id,
                payload={
                    "goal": spec.goal,
                    "budget": spec.budget.to_json(),
                    "spec_hash": spec_hash,
                    "policy_version_id": binding["version_id"],
                    "domain_id": domain.id,
                    # 按哪套重放口径建的（覆盖清单 + 编码清单），重放只核口径相同的任务
                    "replay_scope": replay_scope_digest(),
                },
                actor_type="user",
                actor_id=spec.tenant_id,
            )

            record_mission_creation(cast("CommitService", self), mission, spec, creation_event)
            # 2026-10-03: a Mission is created with its root and its TaskGraph binding in
            # this one transaction, whichever door it came through.
            if self._mission_completer is None:
                raise CommitRejected("MISSION_DEPLOYMENT_UNBOUND")
            self._mission_completer(mission)
            return mission, True

    def begin_planning(self, mission_id: str) -> Mission:
        with self._store.transaction():
            mission = self._require_mission(mission_id)
            if mission.status is MissionStatus.PLANNING:
                return mission
            updated = next_mission(mission, MissionStatus.PLANNING)
            self._store.update_mission(updated, expected_version=mission.version)
            self._emit("MissionPlanning", mission_id, key=mission_id, payload={})
            return updated

    def create_service_intent(
        self,
        *,
        kind: str,
        subject_id: str,
        mission_id: str,
        account_id: str,
        creation_key: str,
        input_id: str,
        input_hash: str,
        config: Mapping[str, Any],
        reservation: Reservation,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> DispatchIntent:
        """Reserve + intent for a Planner / Critic call (D22); idempotent per ``subject_id``."""

        with self._store.transaction():
            existing = self._store.get_intent_for_subject(subject_id)
            if existing is not None:
                return existing
            first_hold = (
                self.protected_tail_hold(self.critic_tail_id(attempt_id))
                if kind == "critic" and attempt_id is not None and task_id is not None
                else None
            )
            first_reservation = (
                self._ledger.reservation(first_hold["subject_id"]) if first_hold else None
            )
            if (
                first_reservation
                and first_reservation["state"] != "SETTLED"
                and (first_reservation["reserved_tokens"] > 0)
            ):
                assert isinstance(attempt_id, str) and isinstance(task_id, str)
                self.consume_critic_tail(
                    attempt_id=attempt_id,
                    task_id=task_id,
                    subject_id=subject_id,
                    account_id=account_id,
                    reservation=reservation,
                    semantic_revision=self.protected_tail_revision(task_id),
                )
            else:
                self._ledger.reserve(
                    account_id=account_id,
                    subject_id=subject_id,
                    mission_id=mission_id,
                    tokens=reservation.tokens,
                    counts_attempt=False,
                )
            intent = DispatchIntent(
                intent_id=ids.intent_id(kind, subject_id),
                kind=kind,
                subject_id=subject_id,
                mission_id=mission_id,
                state="PENDING",
                version=1,
                creation_key=creation_key,
                input_id=input_id,
                input_hash=input_hash,
                config=dict(config),
                expected_turn_id=None,
                agent_id=None,
                receipt=None,
                lease_owner=None,
                lease_expires_at=None,
                replays=0,
                created_at=self._store.now,
            )
            self._store.insert_intent(intent)
            self._emit(
                "BudgetReserved",
                mission_id,
                key=subject_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "subject_id": subject_id,
                    "kind": kind,
                    "tokens": reservation.tokens,
                },
            )
            return intent

    def domain_for(self, mission_id: str) -> DomainProfileV1:
        """The frozen domain profile of this Mission.  Every Mission freezes one at
        creation; a Mission without one, or with a snapshot of another schema, is refused
        (no old-data compatibility) and the loop's contract gate stops it by name."""

        binding = self._store.get_mission_domain(mission_id)
        if binding is None:
            raise CommitRejected("invalid frozen domain: mission has no frozen domain profile")
        try:
            domain = DomainProfileV1.from_json(binding["json"])
            if (domain.id, domain.version) != (binding["domain_id"], binding["domain_version"]):
                raise ValueError("frozen domain identity mismatch")
            return domain
        except (KeyError, TypeError, ValueError) as error:
            raise CommitRejected(f"invalid frozen domain: {error}") from error

    def heal_mission(self, mission_id: str) -> dict[str, Any]:
        """§16.4 idempotent self-healing on restart (D3-6'): close every non-terminal
        Attempt left under a terminal Task (SUPERSEDED for a
        COMPLETED Task, CANCELLED otherwise) and reject their pending results.  A
        healthy library is left untouched; the report says what changed."""

        with self._store.transaction():
            mission = self._require_mission(mission_id)
            report: dict[str, Any] = {"closed_attempts": []}
            if mission.status is not MissionStatus.ACTIVE:
                return report
            for task in self._store.list_tasks(mission_id):
                if task.status not in TERMINAL_TASK:
                    continue
                target = (
                    AttemptStatus.SUPERSEDED
                    if task.status is TaskStatus.COMPLETED
                    else AttemptStatus.CANCELLED
                )
                for attempt in self._store.list_attempts(task.id):
                    if attempt.status in TERMINAL_ATTEMPT:
                        continue
                    self._close_attempt(attempt, target, reason="task_terminal_on_recover")
                    report["closed_attempts"].append(attempt.id)
            return report

    def record_planning_rejected(
        self,
        mission_id: str,
        *,
        ordinal: int,
        reason: str,
        detail: Mapping[str, Any],
        key: str | None = None,
    ) -> Event:
        """A Planner turn that produced no usable graph (D3-2'): durable feedback for the
        next proposal, no state transition.

        ``key`` overrides the default ``{mission}:planner:{ordinal}`` (review P2-2).
        That default says "this is what round *n* came back with", and one round has one
        answer — but P2.3d writes a second kind of record here: *why* a round is being
        opened (D5-A's root-review findings).  Sharing the key made the two collide,
        and the round's own rejection was the one dropped, so the next package told the
        Planner what the reviewer had said and nothing about its own last answer.
        """

        return self._emit(
            "PlanningRejected",
            mission_id,
            key=key or f"{mission_id}:planner:{ordinal}",
            payload={"ordinal": ordinal, "reason": reason, "detail": dict(detail)},
        )

    def fail_planning(
        self,
        mission_id: str,
        *,
        reason: str,
        detail: Mapping[str, Any],
        stop_reason: MissionStopReason = MissionStopReason.PLANNING_FAILED,
    ) -> Mission:
        detail = jsonable(detail)
        detail = jsonable(detail)
        with self._store.transaction():
            mission = self._require_mission(mission_id)
            if mission.status is MissionStatus.FAILED:
                return mission
            report = {
                **dict(mission.final_report or {}),
                "planning_failure": {"reason": reason, **dict(detail)},
                # step 6: a non-planning stop reason (the pool ran out) reads like fail_mission
                **(
                    {}
                    if stop_reason is MissionStopReason.PLANNING_FAILED
                    else {"stop_reason": str(stop_reason), "detail": dict(detail)}
                ),
            }
            report.update(self._ledger.usage_flags(mission_id))
            self._note_unresolved_actions(mission_id, report)
            updated = next_mission(
                mission,
                MissionStatus.FAILED,
                stop_reason=str(stop_reason),
                final_report=report,
            )
            self._store.update_mission(updated, expected_version=mission.version)
            final = self._emit(
                "MissionFailed",
                mission_id,
                key=mission_id,
                payload={
                    "stop_reason": updated.stop_reason,
                    "reason": reason,
                    "detail": dict(detail),
                },
            )
            self._assured_terminal_notice(mission_id, final, updated.version)
            return updated

    def cancel_mission(self, mission_id: str) -> Mission:
        with self._store.transaction():
            mission = self._require_mission(mission_id)
            if mission.status is MissionStatus.CANCELLED:
                return mission
            report = dict(mission.final_report or {})
            report.update(self._ledger.usage_flags(mission_id))
            self._note_unresolved_actions(mission_id, report)
            updated = next_mission(
                mission,
                MissionStatus.CANCELLED,
                stop_reason=str(MissionStopReason.CANCELLED),
                final_report=report,
            )
            self._store.update_mission(updated, expected_version=mission.version)
            self._cascade_stop(mission_id, skip_task=None)
            final = self._emit("MissionCancelled", mission_id, key=mission_id, payload={})
            self._assured_terminal_notice(mission_id, final, updated.version)
            return updated

    def fail_mission(
        self, mission_id: str, *, stop_reason: MissionStopReason | str, detail: Mapping[str, Any]
    ) -> Mission:
        """Mission-level stop that blames no Task (D3-12': the Mission pool itself ran
        out, or an operator condition): Mission → FAILED, open work cancelled."""

        with self._store.transaction():
            mission = self._require_mission(mission_id)
            if mission.status is MissionStatus.FAILED:
                return mission
            report = {
                **dict(mission.final_report or {}),
                "stop_reason": str(stop_reason),
                "detail": dict(detail),
                "tasks": self._task_reports(mission_id),
            }
            unbound = self._unbound_open_tasks(mission_id)
            if unbound:
                report["tasks_without_terminal_record"] = unbound
            report.update(self._ledger.usage_flags(mission_id))
            self._note_unresolved_actions(mission_id, report)
            failed = next_mission(
                mission, MissionStatus.FAILED, stop_reason=str(stop_reason), final_report=report
            )
            self._store.update_mission(failed, expected_version=mission.version)
            self._cascade_stop(mission_id, skip_task=None)
            final = self._emit(
                "MissionFailed",
                mission_id,
                key=mission_id,
                payload={"stop_reason": str(stop_reason), "final_report": report},
            )
            self._assured_terminal_notice(mission_id, final, failed.version)
            return failed

    def _note_unresolved_actions(self, mission_id: str, report: dict[str, Any]) -> None:
        """A stopped Mission's report says which actions left our hands with no known
        outcome; reconciliation goes on after the stop and says when each one is known
        (HTN 一致性补改 H-2)."""

        rows = [
            {
                "action_key": str(action["action_key"]),
                "action_id": str(action.get("action_id") or ""),
                "operation": str(action.get("operation") or ""),
                "target": str(action.get("target") or ""),
                "state": str(action.get("state") or ""),
            }
            for action in self._store.list_actions(mission_id)
            if operation_reconciliation.action_outcome_unresolved(self._store, action)
        ]
        if rows:
            report["unresolved_actions"] = rows
            report["unresolved_actions_note"] = (
                "这些对外操作已经交出、结果还不明；系统会继续核对，有结果再通知"
            )

    def _cascade_stop(self, mission_id: str, *, skip_task: str | None) -> list[str]:
        """D3-13': every READY / ACTIVE / VERIFYING Task (except ``skip_task``) is
        cancelled with its open Attempts; BLOCKED Tasks are left as they are (§25.1
        has no BLOCKED→CANCELLED edge — they end with the Mission); every open
        dispatch intent of the Mission is closed and its reservation released."""

        cancelled: list[str] = []
        unbound = set(self._unbound_open_tasks(mission_id))
        for task in self._store.list_tasks(mission_id):
            if task.id == skip_task:
                continue
            if task.id in unbound:
                # A damaged binding: no terminal record can be written truthfully. Like a
                # BLOCKED Task it ends with the Mission; its open Attempts still close.
                for attempt in self._store.list_attempts(task.id):
                    if attempt.status in OPEN_ATTEMPT_STATES:
                        self._close_attempt(
                            attempt, AttemptStatus.CANCELLED, reason="mission_stopped"
                        )
                        cancelled.append(attempt.id)
                continue
            if task.status is TaskStatus.VERIFYING:
                active = next_task(task, TaskStatus.ACTIVE)
                self._store.update_task(active, expected_version=task.version)
                self._emit(
                    "TaskVerificationAbandoned",
                    mission_id,
                    key=task.id,
                    task_id=task.id,
                    payload={},
                )
                task = active
            if task.status in {TaskStatus.READY, TaskStatus.ACTIVE}:
                self._store.update_task(
                    next_task(task, TaskStatus.CANCELLED), expected_version=task.version
                )
                self._emit("TaskCancelled", mission_id, key=task.id, task_id=task.id, payload={})
                for attempt in self._store.list_attempts(task.id):
                    if attempt.status in OPEN_ATTEMPT_STATES:
                        self._close_attempt(
                            attempt, AttemptStatus.CANCELLED, reason="mission_stopped"
                        )
                        cancelled.append(attempt.id)
        # every not-yet-submitted dispatch intent of this Mission is closed and its
        # reservation released; SUBMITTED ones (a turn is running) stay open for the
        # loop to collect — their cost is imported when the turn settles (D3-6')
        for intent in self._store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED"):
            if intent.mission_id != mission_id:
                continue
            if intent.state == "AGENT_CREATED":
                # The SDK submit may already have happened before its receipt
                # reached this database. Only the exact-turn collector can know
                # whether there is a real invocation/cost; do not settle as zero.
                continue
            self._settle_intent(intent, "FAILED")
        # D7-4' / D7-5': open actions and requests end with the Mission; handed-off and
        # UNKNOWN actions are left to the reconciliation (reality may already have moved)
        self._cancel_open_actions(mission_id, reason="mission_stopped")
        self.release_terminal_tail_holds(mission_id=mission_id)
        return cancelled

    def judge_mission(
        self, mission_id: str, *, judgments: Sequence[Mapping[str, Any]], summary: str
    ) -> Mission:
        """Mission-level success judgment, independent of the Task PASS (D21, ORCH §12.4).

        Not the "Judge" of 理论 04-7 (which picks a champion candidate; not implemented in
        this build) — this is the check of the Mission's own success criteria.

        ``judgments`` carries one entry per ``Mission.success_criteria`` item with
        ``met: bool``; all met → COMPLETED, otherwise FAILED(mission_criteria_unmet)
        while the Task stays COMPLETED.
        """

        # P2.3c part 2c / review F6: the mode gate — *before* the transaction, because the
        # refusal appends an event and an event emitted inside a transaction that then
        # raises is rolled back with it.  This entry is public and had no gate at all,
        # so a hierarchical Mission whose plan happened to contain only primitives
        # could be judged COMPLETED without its root ``GoalResolution`` ever being
        # formed — the one thing §21.5's "wrongly declared complete = 0" turns on.
        # What stopped it in practice was that a compound row is materialised BLOCKED
        # and can never reach COMPLETED, which is a coincidence of the display status
        # and not a rule.
        self._require_root_resolution(mission_id)
        with self._store.transaction():
            mission = self._require_mission(mission_id)
            if mission.status in {MissionStatus.COMPLETED, MissionStatus.FAILED}:
                return mission
            all_tasks = self._store.list_tasks(mission_id)
            tasks = [
                task
                for task in all_tasks
                if task.status is not TaskStatus.CANCELLED  # D5-4: superseded work is history
                and not (
                    task.paused and task.status in {TaskStatus.READY, TaskStatus.BLOCKED}
                )  # R4: a paused route is not required
            ]
            # P2.3c part 2c / review F6, the second half: in the hierarchical mode the
            # completeness of the work is read from the *Acceptances*, never from a
            # sweep of ``TaskStatus`` (the first half, the root-resolution gate, ran
            # before this transaction was opened so that its refusal event survives the
            # refusal).  See :meth:`_require_accepted_work`.
            network = self._judgment_network(mission)
            self._require_accepted_work(mission, network, tasks)
            tasks = self._drop_compound_rows(network, tasks)
            mutable_judgments = [dict(item) for item in judgments]
            judgments = mutable_judgments
            for task in all_tasks:  # a paused READY route ends with the Mission as not needed
                if task.paused and task.status is TaskStatus.READY:
                    self._store.update_task(
                        next_task(task, TaskStatus.CANCELLED, failure_reason="not_needed_paused"),
                        expected_version=task.version,
                    )
                    self._emit(
                        "TaskCancelled",
                        mission_id,
                        key=task.id,
                        task_id=task.id,
                        payload={"reason": "not_needed_paused"},
                    )
            # 按需导入：测试替身（agent_orchestrator.deployment.root）
            from ...deployment.root import current_statements

            criteria = list(current_statements(self._store, mission))
            if [item.get("criterion") for item in judgments] != criteria:
                raise CommitRejected(
                    "judgments must cover the Mission's current requirements in order"
                )
            met = all(bool(item.get("met")) for item in judgments)
            # 2026-10-11 偏离 5（TaskGraph §8.4）：分层计划里步骤之间没有依赖边，"最后一个叶"退化成任取
            # 一步，报告的结果、产物只有那一步的。现在：产物是全部验收步骤的（按网络顺序），"终结步"
            # 是做法声明的收尾步骤（没有就不指定），每一步各自的结果列在 accepted_results。

            accepted = accepted_tasks(tasks)
            finalizer_id = finalizer_task_id(network, htn_store.HtnStore(self._store), mission_id)
            finalizer = next((task for task in accepted if task.id == finalizer_id), None)
            artifact_ids: list[str] = []
            for task in accepted:
                artifact_ids.extend(a for a in task.accepted_artifacts if a not in artifact_ids)
            report = {
                **dict(mission.final_report or {}),
                "accepted_result_id": None if finalizer is None else finalizer.accepted_result_id,
                "accepted_artifacts": artifact_ids,
                "terminal_task_id": None if finalizer is None else finalizer.id,
                "accepted_results": [
                    {
                        "task_id": task.id,
                        "result_id": task.accepted_result_id,
                        "artifacts": list(task.accepted_artifacts),
                    }
                    for task in accepted
                ],
                "summary": summary,
                "attempts": sum(task.attempt_count for task in tasks),
                "tasks": self._task_reports(mission_id),
                "success_criteria": [dict(item) for item in judgments],
                "disputes": mission_disputes(self._store, mission_id),
                "knowledge": [
                    k.id for k in self._store.list_knowledge(mission_id, status="VERIFIED")
                ],
                "lineage": lineage(
                    self._store,
                    mission_id,  # D4-14 / 30-27
                    terminal_task_id=None if finalizer is None else finalizer.id,
                ),
            }
            judged = self._emit(
                "MissionSuccessJudged",
                mission_id,
                key=f"{mission_id}:{mission.version}",
                payload={"met": met, "judgments": [dict(item) for item in judgments]},
            )
            if met:
                report.update(self._ledger.usage_flags(mission_id))
                # The judge never completes a Mission (spec §7.1): it records that the
                # criteria are met and requests the closeout; COMPLETED is written only
                # by the unique final writer out of a READY closeout.  A Mission whose
                # creation contract does not read back as assured is refused by name —
                # there is no other path to COMPLETED (2026-10-05).
                try:
                    AssuranceStore(self._store).require_assured(mission_id)
                except AssuranceError as error:
                    raise CommitRejected(
                        f"mission {mission_id} cannot be judged complete: {error.code}"
                    ) from error
                return request_assured_closeout(self, mission, report=report, judged=judged)
            self._note_unresolved_actions(mission_id, report)
            failed = next_mission(
                mission,
                MissionStatus.FAILED,
                stop_reason="mission_criteria_unmet",
                final_report=report,
            )
            self._store.update_mission(failed, expected_version=mission.version)
            self._cancel_open_actions(mission_id, reason="mission_criteria_unmet")
            final = self._emit(
                "MissionFailed",
                mission_id,
                key=mission_id,
                payload={"stop_reason": failed.stop_reason, "final_report": report},
            )
            self._assured_terminal_notice(mission_id, final, failed.version)
            return failed

    def _judgment_network(self, mission: Mission) -> Any:
        """The hierarchical plan ``judge_mission`` reads.

        Review P2-15: a plan whose bindings are damaged used to leave here as a
        ``PlanIntegrityError`` — a ``RuntimeError`` — which ``judge_mission`` does not
        catch and no caller of a *commit* entry expects.  ``_decide`` happened to hit
        ``root_review_ready`` first and stop the Mission there, so it only showed under
        a race; but ``judge_mission`` is public, and the honest answer to "may this
        Mission be judged" on a damaged plan is a refusal that says so, not a crash.
        """

        try:
            return HierarchicalDispatch.for_commit(cast("CommitService", self), mission.id).network(
                mission.id
            )
        except GraphIntegrityError as error:
            raise CommitRejected(
                f"mission {mission.id} cannot be judged: its committed plan does not read back "
                f"({error}). A damaged plan is repaired, not judged (§9.4)"
            ) from error

    def _drop_compound_rows(self, network: Any, tasks: Sequence[Task]) -> list[Task]:
        """A compound row is never part of the judged set (review F6).

        A compound is refined and never dispatched; it is materialised BLOCKED and can
        never reach COMPLETED, so leaving it in made the *forward* path unreachable —
        a root resolution could stand and the judgment would still refuse.  Its
        conclusion is a Resolution, which :meth:`_require_root_resolution` checks.
        (A GoalResolution for each *inner* compound is P3's: today only the root duty
        is a ``required_obligation``, and inner compounds are concluded through their
        children's Acceptances feeding the root review.)
        """

        compounds = {
            str(spec.task_id) for spec in network.occurrences if spec.form is TaskForm.COMPOUND
        }
        return [task for task in tasks if task.id not in compounds]

    def _require_accepted_work(self, mission: Mission, network: Any, tasks: Sequence[Task]) -> None:
        """Every live primitive occurrence carries a CURRENT ``Acceptance`` (review F6).

        The hierarchical mode's answer to "is the work done" is the Acceptance, not the
        Task row: §18.5 makes the row a rebuildable display index, so judging on the
        status string alone would be judging the index.  *Relaxing* the check to "no
        status is checked" would judge a Mission whose leaves nobody accepted, so the
        same question is asked of the record that actually answers it.

        Review P2-14 corrects a false sentence that stood here — "nothing in this mode
        ever writes ``COMPLETED`` onto it".  ``_collect_attempt`` calls
        ``accept_result`` and *does* push the row to COMPLETED; the leaf acceptance
        runs after that.  The rows only stay READY in tests that skip the real attempt
        pipeline.  The substantive consequence was that the judgment ignored the row
        entirely, so a leaf with a CURRENT Acceptance whose row had since gone FAILED
        passed.  The Acceptance is still the record of acceptance; a FAILED row is a
        *contradiction* of it, and a contradiction is refused rather than resolved in
        favour of the more convenient half.  (A CANCELLED row is not a contradiction:
        D5-4 drops superseded work from the judged set before this runs.)
        """

        semantics = htn_store.HtnStore(self._store)
        accepted = {
            (str(item.task_id), str(item.obligation_id))
            for item in semantics.list_acceptances(mission.id)
            if item.validity is Validity.CURRENT
        }
        rows = {task.id: task for task in tasks}
        live = set(rows)
        unaccepted = sorted(
            str(spec.task_id)
            for spec in network.occurrences
            if spec.form is TaskForm.PRIMITIVE
            and str(spec.task_id) in live
            and (str(spec.task_id), str(spec.obligation_id)) not in accepted
        )
        if unaccepted:
            raise CommitRejected(
                "mission judgment requires a current Acceptance for every live primitive "
                f"occurrence; {unaccepted} carry none (§18.5: in this mode the Task row is a "
                "display index and the Acceptance is the record that the work was accepted)"
            )
        contradicted = sorted(
            str(spec.task_id)
            for spec in network.occurrences
            if spec.form is TaskForm.PRIMITIVE
            and str(spec.task_id) in live
            and rows[str(spec.task_id)].status is TaskStatus.FAILED
        )
        if contradicted:
            raise CommitRejected(
                "mission judgment refuses a leaf whose Acceptance and Task row disagree; "
                f"{contradicted} carry a current Acceptance on a FAILED row (§21.5 'wrongly "
                "declared complete = 0': the disagreement is repaired, not judged)"
            )

    # ------------------------------------------------ Assurance 1.1 final writer (item 7)
    def _assured_terminal_notice(self, mission_id: str, final: Event, state_version: int) -> None:
        """Every terminal write on the assured lane requests the NOTIFY transport."""

        request_assured_notification(self, mission_id, final, state_version=state_version)

    def assured_closeout_pending(self, mission_id: str) -> bool:
        """An ACTIVE assured Mission judged successful and waiting for its closeout."""

        return assured_closeout_pending(self._store, self._store.get_mission(mission_id))

    def _require_root_resolution(self, mission_id: str) -> None:
        """A hierarchical Mission is completed out of its root resolution (review F6).

        In this mode a Mission is complete because its root ``GoalResolution`` was
        formed — out of the AER §6.2 formula, the final acceptance and the delivery
        contract — and never because a sweep of Task statuses came back all-COMPLETED
        (§6.3, §8.1).  ``Orchestrator._decide`` already refuses to call ``judge_mission``
        before the resolution stands, but that entry is public and had no gate of its
        own, so the invariant rested on one caller remembering.

        """

        mission = self._store.get_mission(mission_id)
        if mission is None:
            return
        network = self._judgment_network(mission)

        # Assurance §7.2：判定读的是根的内容是否满足（根结论据此形成）；根的
        # 必需效果由收尾核对收敛（结果不明 → BLOCKED_UNKNOWN），不是判定的前置。
        if not all(
            read_occurrence_completion(self._store, mission_id, str(root)).content_ready
            for root in network.root_occurrence_ids
        ):
            raise CommitRejected("approved completion Scope still has unmet content")
        semantics = htn_store.HtnStore(self._store)
        unresolved = sorted(
            str(duty)
            for duty in dict.fromkeys(network.required_obligations)
            if semantics.adopted_goal_resolution(mission.id, str(duty)) is None
        )
        if unresolved:
            detail = (
                f"mission {mission.id} runs under the hierarchical semantics and its root "
                f"duties {unresolved} carry no adopted GoalResolution; a Mission in this mode "
                "is completed out of its root resolution, not out of a sweep of Task statuses "
                "(§6.3, §8.1, §21.5 'wrongly declared complete = 0'). Offer the resolution "
                "through commit_goal_resolution first."
            )
            self._emit(
                HIERARCHICAL_JUDGMENT_REFUSED,
                mission.id,
                key=f"{mission.id}:{','.join(unresolved)}",
                payload={
                    "reason": "ROOT_RESOLUTION_MISSING",
                    "detail": detail,
                    "unresolved_obligations": unresolved,
                    "redirect": "commit_goal_resolution",
                },
            )
            raise CommitRejected(f"mission judgment refused (ROOT_RESOLUTION_MISSING): {detail}")

    def stop_task(
        self, task_id: str, *, stop_reason: MissionStopReason, detail: Mapping[str, Any]
    ) -> Task:
        """Stop condition reached (§25.1 ACTIVE → FAILED) and the Mission with it."""

        with self._store.transaction():
            task = self._require_task(task_id)
            if task.status is TaskStatus.FAILED:
                return task
            mission = self._require_mission(task.mission_id)
            if task.status in {TaskStatus.VERIFYING, TaskStatus.READY}:
                # §25.1: FAILED is reached from ACTIVE only; a READY Task whose first
                # Attempt could not even be created (budget, artifact conflict) is
                # promoted first, a VERIFYING one is returned to ACTIVE (two legal edges)
                task = next_task(task, TaskStatus.ACTIVE)
                self._store.update_task(task, expected_version=task.version - 1)
            if task.status is not TaskStatus.ACTIVE:
                raise CommitRejected(f"task {task_id} is {task.status}; cannot stop it")
            failed = next_task(task, TaskStatus.FAILED, failure_reason=str(stop_reason))
            self._store.update_task(failed, expected_version=task.version)
            for attempt in self._store.list_attempts(task_id):
                if attempt.status in OPEN_ATTEMPT_STATES:
                    self._close_attempt(attempt, AttemptStatus.CANCELLED, reason="task_stopped")
            report = {
                **dict(mission.final_report or {}),
                "stop_reason": str(stop_reason),
                "failed_task_id": task_id,
                "detail": dict(detail),
                "attempts": task.attempt_count,
                "completed_parts": self._completed_parts(task_id),
                "tasks": self._task_reports(mission.id),
            }
            report.update(self._ledger.usage_flags(mission.id))
            self._note_unresolved_actions(mission.id, report)
            done = next_mission(
                mission, MissionStatus.FAILED, stop_reason=str(stop_reason), final_report=report
            )
            self._store.update_mission(done, expected_version=mission.version)
            self._cascade_stop(mission.id, skip_task=task_id)
            self._emit(
                "TaskFailed",
                mission.id,
                key=task_id,
                task_id=task_id,
                payload={"stop_reason": str(stop_reason), "detail": dict(detail)},
            )
            final = self._emit(
                "MissionFailed",
                mission.id,
                key=mission.id,
                payload={"stop_reason": str(stop_reason), "final_report": report},
            )
            self._assured_terminal_notice(mission.id, final, done.version)
            return failed

    def _task_reports(self, mission_id: str) -> list[dict[str, Any]]:
        return [
            {
                "task_id": task.id,
                "status": str(task.status),
                "dependencies": list(task.dependency_ids),
                "accepted_result_id": task.accepted_result_id,
                "accepted_artifacts": list(task.accepted_artifacts),
                "attempts": task.attempt_count,
                "failure_reason": task.failure_reason,
            }
            for task in self._store.list_tasks(mission_id)
        ]

    def _completed_parts(self, task_id: str) -> list[dict[str, Any]]:
        parts = []
        for attempt in self._store.list_attempts(task_id):
            stored = self._store.find_result_for_attempt(attempt.id)
            parts.append(
                {
                    "attempt_id": attempt.id,
                    "status": str(attempt.status),
                    "result_id": None if stored is None else stored.envelope.id,
                    "verdict": None if stored is None else stored.verdict,
                    "artifacts": [] if stored is None else list(stored.artifacts),
                }
            )
        return parts
