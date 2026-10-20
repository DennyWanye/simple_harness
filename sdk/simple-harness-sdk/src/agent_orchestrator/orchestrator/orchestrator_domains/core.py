# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Orchestrator construction, instance state and the cross-domain interface."""

from __future__ import annotations

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
from ...runtime.provider_budget_guard import ProviderBudgetGuard
from ...runtime.sandbox import resolve_executor
from ...scheduling.backpressure import BacklogResponse, BackpressureState
from ...storage.store import DispatchIntent, Store
from ...verification.verifier_router import VerifierRouter
from ..budget_accounts import Reservation
from ..commit_service import CommitService
from ..hierarchical_dispatch import HierarchicalDispatch
from ..recovery_coordinator import RecoveryCoordinator
from .state import (
    AttemptState,
    CycleHooks,
    DeploymentWiring,
    MissionRoundState,
    PlanningRoundState,
    ReviewState,
    RoutingCache,
)

if TYPE_CHECKING:
    from ...contracts.error_table import SharingRefused
    from ...contracts.planning_decisions import PlanningDecisionEnvelopeV1, PlanningDecisionStatus
    from ...governance.planning_authorization import SourceUnavailable as AuthoritySourceUnavailable
    from ...graph.task_network import TaskNetworkSnapshot
    from ...planning import plan_preview
    from ...planning.decision_admission import NoMutationDecision
    from ...planning.decision_codec import PlanningDecisionCodecError
    from ...runtime.provider_budget_guard import ProviderBudgetGuard
    from .planning_decisions import PlanningReply


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
        def _exhausted_reviews(self, mission_id: str, prefix: str) -> list[dict[str, Any]]: ...
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
        async def _answer_evidence_request(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            context: Any,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_failed_commit(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            error: Exception,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_refused_plan(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            plan_outcome: Any,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_shared_refusal(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            error: SharingRefused,
            codes: Sequence[str],
            refusal_status: PlanningDecisionStatus,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_unadmitted_decision(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            admitted: Any,
            refusal_status: PlanningDecisionStatus,
            evaluated: Callable[..., None],
        ) -> None: ...
        def _reject_unavailable_authority(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            context: AuthoritySourceUnavailable,
            refusal_status: PlanningDecisionStatus,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_unavailable_preview(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            preview: plan_preview.PreviewUnavailable,
            refusal_status: PlanningDecisionStatus,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _reject_undecodable_reply(
            self,
            reply: PlanningReply,
            error: PlanningDecisionCodecError,
            exhausted_fact: str | None,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _repair_retry_same_method(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _repair_runtime_blocked(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _route_method_or_human_request(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            evaluated: Callable[..., None],
        ) -> None: ...
        async def _settle_no_mutation_decision(
            self,
            reply: PlanningReply,
            *,
            decision: PlanningDecisionEnvelopeV1,
            canonical_json: str,
            canonical_hash: str,
            pre_admitted: NoMutationDecision,
            evaluated: Callable[..., None],
        ) -> None: ...

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
        self._wiring = DeploymentWiring(assurance_root_setup=assurance_root_setup)
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
        self._attempts = AttemptState()
        self._planning = PlanningRoundState()
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
        self._reviews = ReviewState()
        self._between = CycleHooks()
        self._rounds = MissionRoundState()
        #: 重启恢复协议的协调者（§25.1 第 11 条）：``__aenter__`` 建，``recover()`` 跑；
        #: 降级恢复时主循环只开只读与诊断。
        self._recovery: RecoveryCoordinator | None = None
        self._pressure = BackpressureState()  # D6-2: the current backpressure signal
        # 积压时的两种应对（审阅并发、暂停新拆分），每轮观测后重算
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
        self._routes = RoutingCache()

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
