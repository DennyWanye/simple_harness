# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Mission creation, terminal commits and startup recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from simple_harness.agents import AgentTurnState

from ...api.missions import MissionRequestError, spec_from_request, validate_spec
from ...contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    ContractError,
    Mission,
    MissionStopReason,
)
from ...contracts.error_table import CodedFault, RoundFaultCode
from ...governance.budgets import BudgetError
from ...governance.domains import resolve_domain
from ...governance.permissions import Principal
from ...governance.policies import action_decision
from ...runtime import operation_reconciliation
from ...runtime.action_params import check_action_params
from ...runtime.action_schema import parse_action_criterion
from ...runtime.actions import publication_overlaps_storage
from ...runtime.planning_operations import SourceUnavailable
from ...storage.recovery_store import RecoveryStore
from ...storage.store import DispatchIntent, InjectedCrash, StoreBusy
from ...storage.taskgraph_binding import NotBoundError, require_bound
from .. import taskgraph_runtime_imports
from ..assurance_final_writer import is_assured, request_assured_notification
from ..assurance_point_use import certify_recovery_locked
from ..commit_service import CommitRejected, MissionSpec
from ..operation_runtime import ensure_operation_runtime
from .core import OrchestratorCore
from .support import logger


class ServiceTurnIdentityMismatch(ContractError, CodedFault):
    """The Critic turn bound at start differs from the frozen intent: retrying meets the same row."""

    code = RoundFaultCode.SERVICE_TURN_IDENTITY_MISMATCH


#: A stopped Mission's action whose outcome became known after the stop (H-2).
ACTION_SETTLED_AFTER_STOP = "ActionSettledAfterMissionStopped"

FAULT_POINTS = (
    "after_agent_created",
    "after_submit",
    "after_turn_committed",
    "after_result_submitted",
    "after_layer_pass",
    "mid_commit",
    "after_accept_before_supersede",  # step 3 (inside the accept transaction → rolls back)
    "after_task_completed",  # step 3 (accept committed, release / next cycle not yet run)
    "retrieval_unavailable",  # step 4 (S4-07): the knowledge index cannot be read
    # 崩溃切点 K06、K08、K09
    "before_goal_resolution",  # a goal's review is saved, its conclusion not committed yet
    "after_handoff_before_call",  # an operation's hand-off is recorded, the external call not made
    "after_external_effect",  # the external call returned, its outcome not recorded yet
)


class MissionLifecycleMixin(OrchestratorCore):
    """Mission creation, terminal commits and startup recovery."""

    __slots__ = ()

    def _prepare_terminal_ledger(self, mission_id: str) -> None:
        """Withdraw future execution and import what is known; keep every
        physical/accounting obligation (P2.3r / N9).  An unknown call is never
        settled as known-only zero usage."""

        mission = self.store.get_mission(mission_id)
        if mission is None:
            return

        try:
            require_bound(self.store, mission_id)
        except NotBoundError:
            # An unbound Mission of an older development library (stopped by name by the
            # contract gate): its runtime history is not read through the TaskGraph, so
            # its reservations stay held — never settled as known zero usage.
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            ):
                if intent.mission_id == mission_id:
                    self._settle_intent(intent, "FAILED")
            return

        reader = taskgraph_runtime_imports.TaskGraphRuntimeImports(self)
        for intent in self.store.list_intents(
            "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "FAILED", "SETTLED"
        ):
            if intent.mission_id != mission_id:
                continue
            try:
                source = reader.read_subject(intent)
                self.commit.import_usage(intent.subject_id, mission_id, source.usage)
            except (SourceUnavailable, BudgetError, ContractError) as error:
                self._note(f"{intent.subject_id}: TaskGraph terminal accounting retained ({error})")
            if intent.state not in {"FAILED", "SETTLED"}:
                self._settle_intent(intent, "FAILED")
        # The fixed late-accounting reader owns eventual settlement.

    def _commit_fail_mission(
        self,
        mission_id: str,
        *,
        stop_reason: MissionStopReason | str,
        detail: Mapping[str, Any],
    ) -> Any:
        self._prepare_terminal_ledger(mission_id)
        return self.commit.fail_mission(mission_id, stop_reason=stop_reason, detail=detail)

    def _commit_fail_planning(
        self,
        mission_id: str,
        *,
        reason: str,
        detail: Mapping[str, Any],
        stop_reason: MissionStopReason = MissionStopReason.PLANNING_FAILED,
    ) -> Any:
        self._prepare_terminal_ledger(mission_id)
        return self.commit.fail_planning(
            mission_id, reason=reason, detail=detail, stop_reason=stop_reason
        )

    def _commit_stop_task(
        self,
        task_id: str,
        *,
        stop_reason: MissionStopReason,
        detail: Mapping[str, Any],
    ) -> Any:
        task = self.store.get_task(task_id)
        if task is not None:
            self._prepare_terminal_ledger(task.mission_id)
        return self.commit.stop_task(task_id, stop_reason=stop_reason, detail=detail)

    def _commit_cancel_mission(self, mission_id: str) -> Any:
        self._prepare_terminal_ledger(mission_id)
        return self.commit.cancel_mission(mission_id)

    def _reset_after_handoff_unknown_streak(self, mission_id: str) -> None:
        self._planning.after_handoff_zero_streak[mission_id] = 0

    @staticmethod
    def _assured_review_intent(intent: DispatchIntent) -> bool:
        """Assured reviews of every purpose (kind ``critic`` or ``plan``) bind the
        read-only evidence tools through ``_bind_critic``; nothing else does."""
        return intent.config.get("assurance_protocol") == "assurance-exec-v1.1"

    def arm_fault(
        self, point: str, *, kind: str | None = None, skip: int = 0, times: int = 1
    ) -> None:
        """Arm a crash at ``point``; ``kind`` restricts it to plan / attempt / critic intents;
        ``skip`` lets that many hits pass first (crash on the n+1-th); ``times`` repeats."""

        if point not in FAULT_POINTS:
            raise ValueError(f"unknown fault point {point}")
        self.store.arm(point if kind is None else f"{point}:{kind}", skip=skip, times=times)

    def _fault(self, point: str, kind: str | None = None) -> None:
        self.store.fault(point, kind)

    def _note(self, text: str) -> None:
        self._rounds.progress_log.append(text)
        logger.info("orchestrator.progress", extra={"detail": text})

    # ------------------------------------------------------------------ api

    def create_mission(self, *, tenant_id: str, request: Mapping[str, Any]) -> tuple[Mission, bool]:
        """Host support 0.9.8 (plan review P1-1): the one door that knows the deployment —
        parse the request, ``validate_spec`` against the deployment's tools, the action
        criteria, local code execution, then the Commit with the provider kind and the
        policy binding :meth:`submit_mission` uses.  Idempotent on ``(tenant_id,
        idempotency_key)``: the same request returns ``(mission, False)``; a *different*
        request under the same key raises ``MissionConflict`` (review round 1 P2-4).  Every
        other refusal is a ``MissionRequestError``; no refusal writes anything."""

        deployment = self._config.deployment_policy
        spec = spec_from_request(tenant_id, request, default_tools=deployment.allowed_tools)
        validate_spec(spec, available_tools=deployment.allowed_tools)
        try:
            self._check_mission_door(spec)
        except ContractError as error:
            raise MissionRequestError(str(error)) from error
        return self._commit_mission(spec)

    def create_mission_with_sources(
        self,
        *,
        tenant_id: str,
        request: Mapping[str, Any],
        sources: Sequence[Mapping[str, Any]],
        principal: Principal,
    ) -> dict[str, Any]:
        """Apply the deployment door before the single atomic Mission/source commit."""

        deployment = self._config.deployment_policy
        spec = spec_from_request(tenant_id, request, default_tools=deployment.allowed_tools)
        validate_spec(spec, available_tools=deployment.allowed_tools)
        try:
            self._check_mission_door(spec)
        except ContractError as error:
            raise MissionRequestError(str(error)) from error
        return self.commit.create_mission_with_sources(
            spec,
            sources=sources,
            principal=principal,
            provider_kind=self._provider_kind,
            policy_defaults=self._config_policy(),
        )

    def _commit_mission(self, spec: MissionSpec) -> tuple[Mission, bool]:
        return self.commit.create_mission(
            spec,
            provider_kind=self._provider_kind,
            policy_defaults=self._config_policy(),
        )

    def _check_mission_door(self, spec: MissionSpec) -> None:
        self._require_assurance_execution_root()
        if set(self._config.domain_tools) != set(self._config.deployment_policy.domain_tools):
            raise ContractError("domain tool implementations must match deployment policy")
        if {name for name, tool in self._config.domain_tools.items() if tool.read_only} != set(
            self._config.deployment_policy.domain_read_only_tools
        ):
            raise ContractError("read-only domain tool metadata must match deployment policy")
        if spec.domain == "appworld-v1" and self._config.appworld_execute is None:
            raise ContractError("AppWorld Mission requires a bound episode environment")
        if spec.runtime_profile_id is not None:
            if (
                not isinstance(spec.runtime_profile_id, str)
                or not spec.runtime_profile_id.strip()
                or spec.runtime_profile_id not in self._profiles
            ):
                raise MissionRequestError(
                    f"runtime profile {spec.runtime_profile_id!r} is not configured"
                )
        self.check_requirements(
            [
                (statement, spec.action_params.get(f"c-user-{index + 1}"))
                for index, statement in enumerate(spec.success_criteria)
            ]
        )
        self._check_source_publish_roots(spec)

    def check_requirements(self, items: Sequence[tuple[str, Mapping[str, Any] | None]]) -> None:
        """What this deployment can do with these requirements (statement, action params): an
        ``action:`` one must be well-formed, name an operation the deployment would run and
        carry the parameters it needs; a ``pytest:`` one needs local code execution.  One door
        for creating a Mission and for amending its requirements (阶段 E)."""
        self._check_action_criteria(items)
        statements = [statement for statement, _ in items]
        if not self._config.deployment_policy.local_code_execution:
            tests = [c for c in statements if c.startswith("pytest:")]
            if tests:
                raise ContractError(
                    "pytest criteria need local code execution, which this deployment has "
                    f"turned off: {tests}"
                )

    def _check_source_publish_roots(self, spec: MissionSpec) -> None:
        """A publisher cannot write the actual source CAS or its workspace mounts.

        Domain roots are logical paths, so compare physical deployment roots here.
        This is a write-boundary check, not a claim about the origin of user text.
        """

        if not resolve_domain(spec.domain).source_roots:
            return
        self._ensure_source_storage_disjoint()

    def validate_source_storage(self, mission_id: str) -> None:
        """Recheck physical storage when an existing Mission imports source material."""
        if self.commit.domain_for(mission_id).source_roots:
            self._ensure_source_storage_disjoint()

    def _ensure_source_storage_disjoint(self) -> None:
        protected = (
            self.assembled.workspaces.artifact_store.root.resolve(),
            self.assembled.workspaces.root.resolve(),
        )
        for name, connector in self._connectors.items():
            if (
                name != "file_publish"
                or name not in self._config.deployment_policy.enabled_connectors
            ):
                continue
            root = getattr(connector, "root", None)
            if not isinstance(root, (str, Path)):
                raise ContractError("source_publish_root_unavailable")
            if publication_overlaps_storage(root, protected):
                raise ContractError("source_publish_root_overlap")

    def _check_action_criteria(self, items: Sequence[tuple[str, Mapping[str, Any] | None]]) -> None:
        """D7-3' / review P2-10: an action criterion must name an enabled connector and an
        operation this deployment would run, with the parameters it needs; otherwise the
        Mission is refused up front.  Parameters belong to action criteria only."""

        for criterion, params in items:
            parsed = parse_action_criterion(criterion)  # malformed → ContractError
            if parsed is None:
                if params is not None:
                    raise ContractError(f"only an action: requirement takes params: {criterion!r}")
                continue
            name, operation, _target = parsed
            connector = self._connectors.get(name)
            decision = action_decision(self._config.deployment_policy, connector, operation)
            if connector is None or decision.refused is not None:
                raise ContractError(f"action criterion {criterion!r} refused: {decision.refused}")
            check_action_params(name, operation, connector.operations[operation], params)

    def _domain_unreadable(self, mission_id: str) -> bool:
        """A Mission this build does not serve: a frozen domain it cannot read (another
        profile schema), or one created before every Mission was TaskGraph-bound and
        assured at creation (A′ release review 2026-10-03).

        Startup binding and recovery read the frozen domain and the TaskGraph; such a
        Mission is left unbound here and stopped by name by the loop's contract gate in
        its first round, instead of taking ``__aenter__`` / ``recover`` down for every
        Mission.
        """

        try:
            self.commit.domain_for(mission_id)
            require_bound(self.store, mission_id)
        except (CommitRejected, NotBoundError):
            return True
        return not is_assured(self.store, mission_id)

    def _bind_startup_tools(self) -> None:
        """Reconstruct frozen tool authority before SDK automatic recovery starts.

        One intent at a time: what one Mission's stored rows refuse is that Mission's
        fault (handed to the round boundary by the first ``run()``), never a reason the
        whole service cannot start (2026-10-03 收尾裁决第 3 张)."""
        for intent in self.store.list_intents("AGENT_CREATED", "SUBMITTED"):
            try:
                self._bind_startup_intent(intent)
            except (StoreBusy, InjectedCrash):
                raise
            except Exception as error:  # noqa: BLE001 - the boundary, deferred to run()
                self._rounds.startup_faults.append(
                    (intent.mission_id, f"startup_bind:{intent.intent_id}", error)
                )

    def _bind_startup_intent(self, intent: DispatchIntent) -> None:
        if intent.agent_id is None or self._pool_missing(intent):
            return
        if self._domain_unreadable(intent.mission_id):
            return
        if intent.kind == "attempt":
            attempt = self.store.get_attempt(intent.subject_id)
            if attempt is not None and attempt.status not in TERMINAL_ATTEMPT:
                self._bind_workspace(attempt)
                self._bind_agent(intent.agent_id, intent.config)
        elif (
            intent.kind == "critic" or self._assured_review_intent(intent)
        ) and not self._critic_subject_stopped(intent):
            if intent.state == "AGENT_CREATED":
                # A created Agent is not necessarily a submitted SDK turn.
                # With no durable turn there is nothing startup can resume;
                # recover() retains its cancel + Mission FAILED semantics for
                # invalid source authority. A crash after submit still leaves
                # a durable turn even if Host has not recorded SUBMITTED.
                uow = self.bridge_for(intent).runtime.uow
                turn = uow.read_agent_turn_by_input(intent.agent_id, intent.input_id)
                if turn is None and intent.expected_turn_id is not None:
                    turn = uow.read_agent_turn(intent.expected_turn_id)
                if turn is None:
                    if any(
                        row.phase not in {AgentTurnState.COMMITTED, AgentTurnState.FAILED}
                        for row in uow.list_agent_turns(intent.agent_id)
                    ):
                        raise ServiceTurnIdentityMismatch(
                            "SERVICE_TURN_IDENTITY_MISMATCH: Critic SDK turn identity differs from frozen intent"
                        )
                    return
                if (
                    turn.agent_id != intent.agent_id
                    or turn.input_id != intent.input_id
                    or turn.turn_id != intent.expected_turn_id
                ):
                    raise ServiceTurnIdentityMismatch(
                        "SERVICE_TURN_IDENTITY_MISMATCH: Critic SDK turn differs from frozen intent"
                    )
            self._bind_critic(intent.agent_id, intent.config)

    def recovery_status(self) -> dict[str, Any]:
        """只读诊断：恢复状态（RECOVERY_LOCKED / READY / DEGRADED_RECOVERY）、是否禁副作用、
        库里最近一次恢复的八步结果。还没恢复过时 ``state`` 为 ``RECOVERY_LOCKED``、``latest`` 是库里的记录。"""

        if self._recovery is not None:
            return self._recovery.status()

        return {
            "state": "RECOVERY_LOCKED",
            "side_effects_disabled": True,
            "recovery_id": None,
            "latest": RecoveryStore(self.store).latest(),
        }

    async def _recover_intent(self, intent: DispatchIntent) -> bool:
        if intent.agent_id is None:
            return False
        if self._pool_missing(intent):
            # The frozen turn belongs to another runtime pool. Leave its
            # workspace and SDK turn untouched for that pool to recover.
            return False
        if self._domain_unreadable(intent.mission_id):
            return False  # stopped by name by the contract gate in the first round
        if intent.kind == "attempt":
            attempt = self.store.get_attempt(intent.subject_id)
            if attempt is not None and attempt.status not in TERMINAL_ATTEMPT:
                # 冻结的请求不静默替换上下文。恢复前按它开工时装进
                # 上下文的证据重签 RECOVERY；签不出就不恢复：按"被打断"记丢失（不扣次数），失败明细
                # 写明哪一项、为什么；这一步下一轮拿当前上下文重做。

                with self.store.transaction():
                    use = certify_recovery_locked(self.commit, attempt)
                if not use.usable:
                    self.commit.mark_attempt_lost(
                        attempt.id,
                        reason="recovery_use_refused",
                        detail={"refusals": list(use.refusals)},
                    )
                    self.commit.settle_intent(intent.intent_id, "FAILED")
                    await self._release_attempt(attempt.id, cancel=True)
                    self._note(f"attempt {attempt.id} not resumed: {'; '.join(use.refusals)}")
                    return True
            if attempt is not None:
                self._bind_workspace(attempt)
                self._bind_agent(intent.agent_id, intent.config)
        elif intent.kind == "critic" or self._assured_review_intent(intent):
            if self._critic_subject_stopped(intent):
                self.assembled.gateway.unbind(intent.agent_id)
                await self._cancel_turn(intent)
                return False
            self._bind_critic(intent.agent_id, intent.config)
        return False

    async def _recover_mission(self, mission: Mission) -> bool:
        try:
            report = self.commit.heal_mission(mission.id)
        except StoreBusy as error:  # another instance is healing; the loop retries
            self._note(f"recover {mission.id}: store busy ({error})")
            return False
        if report["closed_attempts"]:
            self._note(f"recover {mission.id}: {report}")
        for attempt_id in report["closed_attempts"]:
            await self._release_attempt(attempt_id, cancel=True)
        self._reimport_unsettled(mission)
        self._check_interpreter(mission)  # step 9 (plan D9-4')
        return False

    async def _recover_mission_round(self, mission: Mission) -> None:
        """Recover one Mission behind the boundary; until it holds, the Mission is
        ``_unrecovered`` and the rest of its round is skipped (its data is not ready)."""
        self._rounds.unrecovered.discard(
            mission.id
        )  # the boundary must not skip the recovery itself
        await self._mission_round(mission.id, "recover", lambda: self._recover_mission(mission))
        current = self.store.get_mission(mission.id)
        if (
            (mission.id, "recover") in self._rounds.round_faults
            and current is not None
            and current.status not in TERMINAL_MISSION
        ):
            self._rounds.unrecovered.add(mission.id)

    async def _reconcile_actions(self) -> list[dict[str, Any]]:
        """Reconcile every live action — with the operation runtime bound first when this
        deployment has a publisher, so the registered reconciler answers even in a fresh
        process (阶段 B 裁决第 1、3 类); without one, operation-linked actions stay as they are.

        Each action is reconciled behind its Mission's round boundary (2026-10-03 收尾裁决
        第 3 张): a store fault on one action is that Mission's, and the others go on."""

        if self.connectors:
            try:
                ensure_operation_runtime(self)
            except ContractError:
                pass  # no trusted publisher registered here
        settled: list[dict[str, Any]] = []
        live = [(action, True) for action in self.store.list_actions(None, "UNKNOWN", "HANDED_OFF")]
        # 阶段 B 裁决第 1 类: a failed action that left our hands has no proof yet that it
        # did not happen; the registered reconciler is the one that decides that too.
        live += [
            (action, False)
            for action in self.store.list_actions(None, "FAILED")
            if self.actions._failed_unproven(action)
        ]
        for action, rehandoff in live:
            key = str(action["action_key"])

            async def one(key: str = key, rehandoff: bool = rehandoff) -> bool:
                updated = await self.actions.reconcile_one(key, allow_rehandoff=rehandoff)
                if updated is not None:
                    settled.append(updated)
                return False

            await self._mission_round(str(action["mission_id"]), f"reconcile:{key}", one)
        self._notice_actions_settled_after_stop()
        await self._settle_parked_faults()
        return settled

    def _notice_actions_settled_after_stop(self) -> None:
        """A stopped Mission whose report listed actions with no known outcome: once one of
        them is known — a late answer, a lookup, a proof it did not happen, a person's
        ruling — say so once, through the same notice the stop itself went out on (HTN
        一致性补改 H-2).  Read here, after every reconcile pass; whoever settled it."""

        rows = self.store.connection.execute(
            "SELECT mission_id FROM missions WHERE status IN ('CANCELLED','FAILED')"
            " AND json_extract(json, '$.final_report.unresolved_actions') IS NOT NULL"
        ).fetchall()
        for (mission_id,) in rows:
            if self.recovery_isolated(str(mission_id)) or not self._round_due(
                str(mission_id), "notice:settled-actions"
            ):
                continue
            # one Mission's fault is that Mission's; the scan goes on (阻断核验 2026-10-04)
            with self._round_boundary(str(mission_id), "notice:settled-actions"):
                # 编号刚从 missions 表查出
                mission = cast("Mission", self.store.get_mission(str(mission_id)))
                for listed in (mission.final_report or {}).get("unresolved_actions") or ():
                    key = str(listed.get("action_key") or "")
                    action = self.store.get_action(key)
                    if action is None or operation_reconciliation.action_outcome_unresolved(
                        self.store, action
                    ):
                        continue
                    state = str(action["state"])
                    if (
                        self.store.connection.execute(
                            "SELECT 1 FROM events WHERE idempotency_key=?",
                            (f"{ACTION_SETTLED_AFTER_STOP}:{key}:{state}",),
                        ).fetchone()
                        is not None
                    ):
                        continue  # already said
                    with self.store.transaction():
                        event = self.commit._emit(
                            ACTION_SETTLED_AFTER_STOP,
                            mission.id,
                            key=f"{key}:{state}",
                            task_id=action.get("task_id"),
                            payload={
                                "action_key": key,
                                "action_id": str(action.get("action_id") or ""),
                                "operation": str(action.get("operation") or ""),
                                "target": str(action.get("target") or ""),
                                "state": state,
                                "applied": state == "SUCCEEDED",
                            },
                        )
                        request_assured_notification(
                            self.commit, mission.id, event, state_version=mission.version
                        )
