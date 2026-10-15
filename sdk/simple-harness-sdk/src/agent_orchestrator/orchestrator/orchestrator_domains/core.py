# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Orchestrator construction, instance state and the cross-domain interface."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...artifacts.versioning import UpstreamInput
from ...context.retrieval import KnowledgeContext
from ...contracts import Artifact, Attempt, Mission, MissionStopReason, Task
from ...contracts.planning_decisions import PlanningRefV1
from ...governance.policies import deployed_layers
from ...runtime.actions import ActionExecutor
from ...runtime.agent_worker import AgentBridge, Liveness
from ...runtime.assembly import AssembledOrchestratorRuntime, OrchestratorConfig
from ...runtime.first_request_budget import FirstRequestBudget, FirstRequestBudgetUnknown
from ...runtime.legacy_provider_slots import profile_has_frozen_admission
from ...runtime.model_router import (
    DEFAULT_PROFILE,
    ModelRouter,
    RoutingDecision,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
)
from ...runtime.output_blocks import PortClaim
from ...runtime.provider_budget_guard import ProviderBudgetGuard
from ...runtime.sandbox import resolve_executor
from ...scheduling.backpressure import BacklogResponse, BackpressureState
from ...storage.store import DispatchIntent, Store
from ...verification.critics import CriticVerdict
from ...verification.verifier_router import VerifierRouter
from ..budget_accounts import Reservation
from ..commit_service import CommitService
from ..deferred_planning import DeferredPlanning
from ..hierarchical_dispatch import HierarchicalDispatch
from ..recovery_coordinator import RecoveryCoordinator

if TYPE_CHECKING:
    from ...graph.task_network import TaskNetworkSnapshot
    from ...runtime.provider_budget_guard import ProviderBudgetGuard


if TYPE_CHECKING:
    from ..event_handler import Orchestrator


def _provider_kind(
    provider: Any, profiles: Mapping[str, RuntimeProfile], default_profile: str
) -> str:
    """Plan D9-3' (review P1-5 / P2-6): fixtures, a real model, or unknown — judged from
    the provider classes of every profile; "fixtures" only when all of them are."""

    def classify(candidate: Any, declared: str | None = None) -> str:
        module = type(candidate).__module__
        if module == "fixtures_provider" or module.startswith("agent_orchestrator.testing"):
            return "fixtures"
        if module.startswith("simple_harness.providers") or declared == "env":
            return "real"
        return "unknown"

    if provider is not None:
        return classify(provider)
    kinds = {classify(p.provider, str(p.provider_kind)) for p in profiles.values()}
    if kinds == {"fixtures"}:
        return "fixtures"
    return "real" if "real" in kinds else "unknown"


class OrchestratorCore:
    """Orchestrator construction, instance state and the cross-domain interface."""

    if TYPE_CHECKING:

        def _accepted_path_hashes(
            self, mission_id: str, *, current_task_ids: Collection[str] | None = None
        ) -> dict[str, str]: ...
        def _active_missions(self) -> list[Mission]: ...
        def _active_source_binding(self, mission_id: str) -> dict[str, Any]: ...
        def _admission_for(self, profile_id: str) -> ProviderBudgetGuard | None: ...
        async def _advance_root_review(
            self, mission: Mission, new_mode: HierarchicalDispatch
        ) -> bool: ...
        def _arbitrated(
            self,
            mission: Mission,
            tasks: Sequence[Task],
            key: str,
            judgments: Sequence[Mapping[str, Any]],
        ) -> tuple[list[dict[str, Any]] | None, bool]: ...
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
        ) -> bool: ...
        def _assembly_missing(self, mission: Mission, *, at: str) -> bool: ...
        @staticmethod
        def _assured_review_intent(intent: DispatchIntent) -> bool: ...
        def _awaiting_retry_decision(self, mission_id: str, task: Any) -> bool: ...
        def _backlog_response(self, state: BackpressureState) -> BacklogResponse: ...
        def _bind_agent(self, agent_id: str, config: Mapping[str, Any]) -> None: ...
        def _bind_critic(self, agent_id: str, config: Mapping[str, Any]) -> None: ...
        def _bind_workspace(self, attempt: Attempt) -> None: ...
        def _blame_replaced_method(
            self, mission_id: str, decision: Any, decision_id: str
        ) -> None: ...
        async def _cancel_turn(self, intent: DispatchIntent) -> None: ...
        async def _carried_review(self, item: Any) -> bool: ...
        def _check_interpreter(self, mission: Mission) -> None: ...
        def _check_resource_request(
            self, mission: Mission, task: Task, request: Any
        ) -> dict[str, Any]: ...
        async def _collect(self, intent: DispatchIntent) -> bool: ...
        async def _collect_after_stop(self, intent: DispatchIntent) -> bool: ...
        async def _collect_attempt(self, intent: DispatchIntent, result) -> None: ...
        async def _collect_plan(self, intent: DispatchIntent, result) -> None: ...
        async def _collect_plan_decision(
            self,
            intent: DispatchIntent,
            result: Any,
            mission: Mission,
            text: str,
            new_mode: HierarchicalDispatch,
        ) -> None: ...
        async def _collect_stopped_critic(self, intent: DispatchIntent) -> bool: ...
        def _commit_cancel_mission(self, mission_id: str) -> Any: ...
        def _commit_fail_mission(
            self,
            mission_id: str,
            *,
            stop_reason: MissionStopReason | str,
            detail: Mapping[str, Any],
        ) -> Any: ...
        def _commit_fail_planning(
            self,
            mission_id: str,
            *,
            reason: str,
            detail: Mapping[str, Any],
            stop_reason: MissionStopReason = MissionStopReason.PLANNING_FAILED,
        ) -> Any: ...
        def _commit_stop_task(
            self, task_id: str, *, stop_reason: MissionStopReason, detail: Mapping[str, Any]
        ) -> Any: ...
        def _composition_assembly(self, mission: Mission, dispatch: Any) -> Any: ...
        def _config_policy(self) -> dict[str, Any]: ...
        def _context_profile_for(self, config: Mapping[str, Any]) -> RuntimeProfile: ...
        def _create_planner_intent_now(
            self, mission_id: str, *, ordinal: int
        ) -> DispatchIntent: ...
        def _critic_subject_stopped(self, intent: DispatchIntent) -> bool: ...
        def _current_source_files(self, mission: Mission) -> dict[str, bytes]: ...
        async def _decide(self, mission: Mission) -> bool: ...
        async def _decide_actions(self, mission: Mission, tasks: Sequence[Task]) -> bool: ...
        def _decomposition_paused(self, mission: Mission) -> bool: ...
        async def _defer_for_profile(
            self, mission: Mission, task: Task, unavailable: RoutingUnavailable
        ) -> bool: ...
        @staticmethod
        def _definite_auth_failure(error: Any) -> bool: ...
        async def _dispatch(self, intent: DispatchIntent) -> bool: ...
        def _dispatch_for(self, mission_id: str) -> HierarchicalDispatch | None: ...
        def _dispatch_h4_repair_trigger(
            self, mission: Mission, *, event_type: str, trigger_ref: str, detail: Mapping[str, Any]
        ) -> None: ...
        def _exhausted_reviews(self, mission_id: str, prefix: str) -> list[dict[str, str]]: ...
        def _expand_planning_wait(
            self, mission: Mission, refs: tuple[PlanningRefV1, ...]
        ) -> tuple[PlanningRefV1, ...] | None: ...
        def _expected_model(self, intent: DispatchIntent) -> str: ...
        async def _fail_runtime_unavailable(
            self, mission: Mission, *, reason: str, detail: Mapping[str, Any]
        ) -> None: ...
        def _fault(self, point: str, kind: str | None = None) -> None: ...
        def _final_review_findings_for_workers(self, mission_id: str) -> list[dict[str, Any]]: ...
        def _first_critic_budget(
            self, decision: RoutingDecision
        ) -> FirstRequestBudget | FirstRequestBudgetUnknown: ...
        def _first_critic_reservation(self, budget: FirstRequestBudget) -> Reservation: ...
        def _frozen_source_binding(self, attempt: Attempt) -> dict[str, Any]: ...
        def _gather_evidence(self, mission: Mission) -> bool: ...
        def _gather_knowledge(
            self, mission: Mission, task: Task, network: TaskNetworkSnapshot | None
        ) -> KnowledgeContext: ...
        def _has_pending_planning_waits(self, mission_id: str | None = None) -> bool: ...
        def _hierarchical_admission_context(
            self,
            *,
            intent: DispatchIntent,
            mission: Mission,
            new_mode: HierarchicalDispatch,
            raw_text: str,
            include_plan_sources: bool = True,
        ) -> Any: ...
        def _hierarchical_worker_template(self, role: Any, mission_id: str) -> Any: ...
        def _hold_lease(self, attempt_id: str) -> Attempt: ...
        def _import_usage(self, intent: DispatchIntent) -> None: ...
        def _inconclusive_reviews(self, mission_id: str) -> list[dict[str, str]]: ...
        def _input_files(self, attempt: Attempt) -> dict[str, bytes]: ...
        async def _judge(self, mission: Mission, tasks: Sequence[Task]) -> bool: ...
        def _method_proposals_remaining(
            self, mission: Mission, new_mode: HierarchicalDispatch
        ) -> int: ...
        async def _mission_round(
            self, mission_id: str, where: str, step: Callable[[], Awaitable[Any]]
        ) -> bool: ...
        def _new_mode(self, mission: Mission) -> HierarchicalDispatch | None: ...
        async def _next_attempt(
            self,
            mission: Mission,
            task: Task,
            attempts: Sequence[Attempt],
            *,
            allocation: Mapping[str, Any] | None = None,
            admission: Any = None,
        ) -> bool: ...
        def _next_planning_ordinal(self, mission_id: str) -> int: ...
        def _note(self, text: str) -> None: ...
        def _note_turn_health(self, intent: DispatchIntent, result) -> str: ...
        def _observe_pressure(self, active: set[str]) -> None: ...
        async def _plan_integrity_stop(self, mission: Mission, error: Exception) -> None: ...
        def _planner_intents_in_flight(self, mission_id: str) -> bool: ...
        async def _planner_round_on_committed_plan(
            self, mission_id: str, *, ordinal: int, phase: str
        ) -> bool: ...
        def _planning_attempts(self, mission_id: str) -> int: ...
        @staticmethod
        def _planning_decision_attempt_ordinal(intent: DispatchIntent) -> int: ...
        def _planning_format_retry_remaining(
            self, *, intent: DispatchIntent, mission: Mission
        ) -> int: ...
        def _planning_ladder_spent(self, mission_id: str) -> bool: ...
        async def _planning_rejected(
            self, intent: DispatchIntent, *, reason: str, detail: Mapping[str, Any]
        ) -> None: ...
        def _planning_round_waits(self, mission_id: str, refused: Any, *, path: str) -> bool: ...
        def _pool_missing(self, intent: DispatchIntent) -> bool: ...
        def _preview_read_facts(self, mission_id: str) -> dict[str, Any]: ...
        def _protected_files(
            self, mission: Mission, task: Task, attempt: Attempt
        ) -> dict[str, str | bytes]: ...
        def _protected_seed(self, mission: Mission, task: Task) -> dict[str, str]: ...
        @staticmethod
        def _provider_blocked(liveness: Liveness) -> bool: ...
        def _prune_deferred(self) -> None: ...
        def _read_only_initial(self, attempt: Attempt) -> dict[str, str]: ...
        def _read_only_inputs(self, attempt_id: str) -> tuple[str, ...]: ...
        def _record_applied_diff_files(
            self,
            referenced: Sequence[Artifact],
            *,
            workspace: Any,
            attempt: Attempt,
            seed: Mapping[str, str],
        ) -> list[Artifact]: ...
        def _record_late_result(self, attempt: Attempt, result) -> None: ...
        async def _recover_mission_round(self, mission: Mission) -> None: ...
        def _refuse_unsupported_contract(self, mission: Mission) -> bool: ...
        def _register_copy(
            self, kind: str, name: str, *, mission_id: str, attempt_id: str, detail: dict[str, Any]
        ) -> None: ...
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
        ) -> tuple[dict[str, Any], dict[str, Any]]: ...
        def _reimport_unsettled(self, mission: Mission) -> None: ...
        async def _release_attempt(self, attempt_id: str, *, cancel: bool) -> None: ...
        async def _release_mission(self, mission_id: str) -> None: ...
        def _release_refused_fence(self, mission_id: str, decision_id: str) -> None: ...
        def _require_assurance_execution_root(self) -> None: ...
        def _requirements_unconfirmed(self, mission: Mission) -> bool: ...
        def _reservation(self, tokens: int) -> Reservation: ...
        def _reset_after_handoff_unknown_streak(self, mission_id: str) -> None: ...
        async def _resolve_provider_blocked_service(
            self, intent: DispatchIntent, liveness: Liveness
        ) -> str | None: ...
        def _resource_grant(self, mission: Mission, task: Task) -> dict[str, Any] | None: ...
        def _resume_planning_services(self, mission: Mission) -> bool: ...
        async def _retry_deferred_planning(self) -> bool: ...
        @staticmethod
        def _review_record_findings(record: Any) -> list[dict[str, Any]]: ...
        def _root_review(self, mission: Mission, new_mode: HierarchicalDispatch) -> Any: ...
        def _root_review_repairs(self, mission_id: str) -> int: ...
        def _root_review_repairs_are_exhausted(
            self, mission: Mission, new_mode: HierarchicalDispatch
        ) -> bool: ...
        def _root_review_stop_detail(
            self, mission: Mission, new_mode: HierarchicalDispatch
        ) -> dict[str, Any]: ...
        def _round_backing_off(self, mission_id: str) -> bool: ...
        @contextlib.contextmanager
        def _round_boundary(self, mission_id: str, where: str) -> Iterator[None]: ...
        def _round_due(self, mission_id: str, where: str) -> bool: ...
        def _route_service(self, role: str, mission_id: str) -> RoutingDecision: ...
        def _router_for(self, mission_id: str) -> ModelRouter: ...
        async def _runtime_exhausted(self, mission: Mission, tasks: Sequence[Task]) -> bool: ...
        def _schedule_read_only_kept_writing_stop(self, attempt: Attempt) -> None: ...
        def _scope_carried(self, result_id: str) -> bool: ...
        def _search_call_room(self, mission: Mission, task: Task) -> int | None: ...
        @property
        def _service_blocker_limit(self) -> float: ...
        def _service_config(self, decision: RoutingDecision) -> dict[str, Any]: ...
        async def _set_aside_stale(self, result_id: str) -> bool: ...
        def _settle_if_known(self, attempt: Attempt) -> None: ...
        def _settle_intent(self, intent: DispatchIntent, state: str) -> None: ...
        async def _settle_parked_faults(self) -> bool: ...
        def _settle_service_if_known(
            self, subject_id: str, mission_id: str, task_id: str | None = None
        ) -> None: ...
        def _source_files(self, attempt: Attempt) -> dict[str, bytes]: ...
        async def _start_planning(self, mission: Mission) -> bool: ...
        async def _stop_conditions_reached(
            self, mission: Mission, new_mode: HierarchicalDispatch
        ) -> bool: ...
        def _stop_planning_round(
            self,
            mission_id: str,
            *,
            reason: str,
            detail: Mapping[str, Any],
            stop_reason: MissionStopReason,
        ) -> None: ...
        def _template(self, template: Any, mission_id: str) -> Any: ...
        def _tool_call_room(
            self, mission: Mission, task: Task
        ) -> tuple[int | None, int | None]: ...
        def _tool_calls_limited(self, mission: Mission, task: Task) -> bool: ...
        def _unknown_handoff_check(self, intent: DispatchIntent): ...
        def _upstream_inputs(self, attempt: Attempt) -> list[UpstreamInput]: ...
        def _verdict_refused(self, result_id: str, error: Exception) -> bool: ...
        def _verifier_capacity(self) -> int: ...
        async def _verify(self, result_id: str) -> bool: ...
        async def _wake_planning_waits(self) -> bool: ...
        def _worker_action_contract(
            self, mission: Mission, task: Task
        ) -> dict[str, Any] | None: ...
        @property
        def actions(self) -> ActionExecutor: ...
        def bridge_for(self, intent: DispatchIntent) -> AgentBridge: ...
        @property
        def commit(self) -> CommitService: ...
        @property
        def connectors(self) -> Mapping[str, Any]: ...
        def policy_for(self, mission_id: str) -> dict[str, Any]: ...
        def policy_version_of(self, mission_id: str) -> str | None: ...
        def profile_of(self, intent: DispatchIntent) -> str: ...
        def recovery_isolated(self, mission_id: str) -> bool: ...
        @property
        def store(self) -> Store: ...
        def validate_source_storage(self, mission_id: str) -> None: ...

    def __init__(
        self,
        config: OrchestratorConfig,
        provider=None,  # type: ignore[no-untyped-def]
        *,
        owner: str | None = None,
        poll_interval: float = 0.05,
        critic_wait_seconds: float | None = None,
        profiles: Mapping[str, RuntimeProfile] | None = None,
        routing: RoutingRules | None = None,
        connectors: Mapping[str, Any] | None = None,
        provider_kind: str | None = None,
        provider_token_estimator=None,
        provider_token_estimators: Mapping[str, Any] | None = None,
        operation_profiles: Any | None = None,
        operation_policy_for: Any | None = None,
        startup_assembly: Callable[[Orchestrator], None] | None = None,
        assurance_root_setup: Callable[[Orchestrator], None] | None = None,
    ) -> None:
        # D3-10': ``owner`` is this instance's identity for orchestration leases *and* for
        # the SDK runtime (``owner_id``); the SDK ``owner_scope`` is one constant for all.
        self._owner = owner or f"orchestrator-{os.getpid()}"
        self._config = replace(config, owner_id=self._owner)
        # P2.3b: the hierarchical assembly (§14 / §18.2 "only assemble and call").
        # ``None`` until a deployment installs one; without it nothing is planned,
        # dispatched or judged (``_assembly_missing``).
        self._hierarchical: HierarchicalDispatch | None = None
        self._planning_world_factory: Any = None
        self._planning_start_gate: Any = None
        self._mission_dispatches: dict[str, HierarchicalDispatch] = {}
        self._taskgraph_dispatch_setup: Any = None
        self._taskgraph_notifications: Any = None
        self._assurance_tick: Any = None
        self._assurance_local_checks: Any = None
        self._assurance_reviews: Any = None
        self._assurance_root_gate: Any = None
        self._assurance_root_setup = assurance_root_setup
        self._assurance_management_only = False
        self._taskgraph_read_apis: list[Any] = []
        self._assurance_read_apis: list[Any] = []
        self._taskgraph_operator: Any = None
        self._taskgraph_policy: Any = None
        self._provider = provider
        self._provider_token_estimator = provider_token_estimator
        self._provider_admission: ProviderBudgetGuard | None = None
        self._provider_token_estimators = provider_token_estimators
        self._provider_admissions: dict[str, ProviderBudgetGuard | None] | None = None
        # D6-4' / D6-5': the deployment's execution pools, routed by rules.  Every pool
        # runs on the native plane (2026-10-03: the pool without one was removed), so a
        # deployment always passes its own profiles (``deployment.native_pools``).
        if not profiles:
            raise ValueError("Orchestrator needs the deployment's native runtime profiles")
        self._profiles: dict[str, RuntimeProfile] = dict(profiles)
        if provider_token_estimators is not None and (
            provider_token_estimator is not None
            or set(provider_token_estimators) != set(self._profiles)
        ):
            raise ValueError("per-pool token estimators must explicitly cover every profile")
        if provider_token_estimators is not None:
            for key, estimator in provider_token_estimators.items():
                if (
                    estimator is None
                    and self._profiles[key].context_policy is not None
                    and profile_has_frozen_admission(config, key) is not False
                ):
                    raise ValueError("a new context pool requires its own token estimator")
        default_profile = (
            routing.default
            if routing is not None
            else (
                DEFAULT_PROFILE if DEFAULT_PROFILE in self._profiles else next(iter(self._profiles))
            )
        )
        self._model_router = ModelRouter(
            self._profiles,
            routing if routing is not None else RoutingRules(default=default_profile),
        )
        self._default_profile = default_profile
        self._deferred: dict[str, float] = {}  # task_id → first time it waited for a profile
        # review P0-1: a Planner whose pool is cooling down waits too: mission_id → (since, ordinal)
        self._deferred_planning: dict[str, tuple[float, int]] = DeferredPlanning()
        #: 推后第 1 批 A26（裁决 2026-10-07 建议 1）：开规划轮时证据签不出 PLAN 证书、正在等下一轮
        #: 重签的任务。这是合法等待，不判停滞；建出规划意图即清。
        #: mission → (这段连续等待开始的存储时钟时刻, 最近一次签不出的时刻)；等待上限从开始时刻算
        self._planning_evidence_waits: dict[str, tuple[float, float]] = {}
        #: P2.3f: when this process first saw a service turn blocked on an unknown
        #: Provider outcome, per ``intent_id:replays``.  In memory on purpose: the
        #: bound is a *wait*, and a restarted process starting the wait again costs at
        #: most one more window; the re-hand-off itself is durable (the event).
        self._service_blocked_since: dict[str, float] = {}
        #: 第 2 批 A27: blocked keys whose same-call-key re-read found a result at the bound;
        #: they get exactly one more window for the collector before the round's own door.
        self._handoff_known_waits: set[str] = set()
        #: P2.3p: consecutive after-handoff 0-token UNKNOWNs per Mission, and the
        #: invocation ids already counted so a poll does not increment twice.
        self._after_handoff_zero_streak: dict[str, int] = {}
        self._counted_after_handoff_unknowns: set[str] = set()
        self._poll = poll_interval
        self._critic_wait = (
            config.turn_deadline_seconds if critic_wait_seconds is None else critic_wait_seconds
        )
        if not 0 < self._critic_wait <= config.turn_deadline_seconds:
            raise ValueError("Critic wait must be positive and within the SDK turn deadline")
        self._store: Store | None = None
        self._commit: CommitService | None = None
        self._assembled: AssembledOrchestratorRuntime | None = None
        self._bridge: AgentBridge | None = None
        # P3.2 D2: the executor model-written code runs through (None when execution is
        # off); a sandboxed deployment without a probed seatbelt executor fails right here
        self._executor = resolve_executor(config.deployment_policy, config.sandbox_executor)
        self._router = VerifierRouter(
            test_timeout=config.test_timeout_seconds,
            local_code_execution=config.deployment_policy.local_code_execution,
            executor=self._executor,
        )
        # host support 0.9.8: the verification layers this deployment can run
        self._deployed = deployed_layers(config.deployment_policy)
        self._critic_verdicts: dict[str, CriticVerdict] = {}
        # result id -> (refusal signature, consecutive count); see _verdict_refused
        self._verdict_refusals: dict[str, tuple[str, int]] = {}
        #: 重审连续没能得出结论的次数（按（结果, 要求版本）），见 ``_carried_review``
        self._carried_errors: dict[str, int] = {}
        #: result id → the output-port claims that arrived with that envelope
        #: (P2.3c part 2d, decision 4).  ``ResultEnvelope`` is a frozen contract with
        #: ``additionalProperties`` refused, so the claims are parsed out of the block
        #: and carried beside it for the rest of this cycle — the same shape
        #: ``_critic_verdicts`` above uses, and for the same reason.  The durable
        #: guard against a lost claim is ``accept_review``'s ``OUTPUT_PORT_UNCLAIMED``,
        #: which refuses rather than indexing a port nobody named.
        self._port_claims: dict[str, tuple[PortClaim, ...]] = {}
        # 推后第 3 批 H10：结果块上取下的资源申请，按结果编号暂存到记录这次结果为止
        self._resource_requests: dict[str, Any] = {}
        #: mission id → the fingerprint of the stall just recorded for it, handed to
        #: ``_confirm_and_stop_stalled`` so the confirmation compares *this* stall
        #: against what one more cycle produces (P2.3c part 2d, decision 2).
        self._stalled_at: dict[str, str] = {}
        #: mission id → how many times this ``run()`` has already carried on because
        #: the stall confirmation moved the world (third-round review P1-A).
        self._stall_carry_ons: dict[str, int] = {}
        #: The embedding host's own duties (NEXT-TG-1.0 2B, 2A.1g): see
        #: :meth:`set_between_cycles`.
        self._between_cycles: Callable[[], Any] | None = None
        self._between_every = 0.0
        self._between_last = 0.0
        self._client_ids: dict[str, str | None] = {}
        self._released: set[str] = set()
        self.progress_log: list[str] = []
        #: intent id → the refusal last noted for its collection, so a refusal that
        #: repeats every round is noted once per distinct reason (NEXT-TG-1.0 §5.1).
        # 审阅调用的等待（阶段 B 裁决第 6 类）：意图 → (等待的形态, 这一形态开始的库时钟)
        self._review_call_marks: dict[str, tuple[Any, float]] = {}
        # 一轮故障（阶段 B 裁决第 9 类）：(任务, 出事地点) → (连续次数, 第一次的库时钟)
        #: (任务, 位置) → (连续故障轮数, 第一次的时刻, 下次可以再试的时刻)
        self._round_faults: dict[tuple[str, str], tuple[int, float, float]] = {}
        #: Missions whose restart recovery faulted: each round retries the recovery first,
        #: inside the boundary, and skips the rest of that Mission's round until it holds.
        self._unrecovered: set[str] = set()
        # 重启恢复第 3 步（reducer 重建）对不上的任务：本进程主循环不再处理（隔离该流），
        # 值是对不上的事实（哪些表）。下次启动重新核对。
        self._recovery_isolated: dict[str, Any] = {}
        #: faults caught by ``_round_boundary`` inside a global scan, settled right after it
        self._parked_faults: list[tuple[str, str, Exception]] = []
        #: Faults caught while binding frozen tool authority at startup, handed to the
        #: boundary by the first ``run()`` (the loop is not running yet in ``__aenter__``).
        self._startup_faults: list[tuple[str, str, Exception]] = []
        #: 重启恢复协议的协调者（车道 J H01，§25.1 第 11 条）：``__aenter__`` 建，``recover()`` 跑；
        #: 降级恢复时主循环只开只读与诊断。
        self._recovery: RecoveryCoordinator | None = None
        #: planning intents already noted as waiting for their TaskGraph binding.
        self._creation_refusals_noted: set[str] = set()
        # 2026-09-30: finished Missions' Agents are closed in bounded, throttled sweeps
        self._agent_sweep_at: float | None = None
        self._agents_closed: set[str] = set()
        self.cancel_receipts: list[dict[str, Any]] = []
        self._rotation = 0  # D6-1: round-robin start across active Missions
        # 任务号 → (上次无进展一轮时的全局事件游标, 时刻)；见 _missions_due（第 4 批）
        self._mission_marks: dict[str, tuple[int, float]] = {}
        #: Hierarchical Missions whose planning-protocol binding was read and is the
        #: one this build serves (see ``_refuse_unsupported_contract``).
        self._contract_checked: set[str] = set()
        self._verifying: dict[str, asyncio.Task[bool]] = {}  # D6-9': bounded verification set
        self._pressure = BackpressureState()  # D6-2: the current backpressure signal
        # 推后第 3 批 H12：积压时的两种应对（审阅并发、暂停新拆分），每轮观测后重算
        self._backlog = self._backlog_response(self._pressure)
        self._connectors: dict[str, Any] = dict(
            connectors or {}
        )  # D7-6: only the executor calls them
        self._operation_profiles = operation_profiles
        self._operation_policy_for = operation_policy_for
        self._startup_assembly = startup_assembly
        self._actions: ActionExecutor | None = None
        self._routing = routing  # step 8 (D8-5'): part of the policy snapshot
        # step 9 (plan D9-3' / D9-4'): the provider kind recorded with each binding and the
        # per-version parameter and router caches
        self._provider_kind = provider_kind or _provider_kind(
            provider, self._profiles, default_profile
        )
        self._policies: dict[str, dict[str, Any]] = {}
        self._routers: dict[tuple[str, str | None, str | None], ModelRouter] = {}
        self._route_drops: dict[str, dict[str, str]] = {}
        self._route_noted: set[str] = set()

    @property
    def assembled(self) -> AssembledOrchestratorRuntime:
        assert self._assembled is not None
        return self._assembled

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    @property
    def owner(self) -> str:
        return self._owner

    #: The same result refused for the same reason this many rounds in a row is a
    #: deterministic refusal, not a race with another Commit.
    VERDICT_REFUSAL_LIMIT = 3
