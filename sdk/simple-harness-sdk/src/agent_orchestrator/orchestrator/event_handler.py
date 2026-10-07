# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""The Orchestrator control loop (§4, §7.5, §24) for a static Task DAG (step 3).

Observe → Plan → Allocate → Execute → Verify → Commit → Repeat, as a
deterministic *workflow shell* around the model calls (theory 08-8): every
action below is an idempotent Commit, so ``run()`` can be interrupted at any
instruction and restarted (``recover()`` first) without a second execution, a
second delivery or a second charge.  Fault points (``self._fault(...)``) mark the
cross-database crash instants of the recovery matrix (plan D14', D3-6').

The Planner speaks the typed planning-decision contract and the hierarchical
assembly commits its plan; the Allocator grants Attempts only to occurrences the
readiness gate admitted, under the concurrency bound; an Attempt starts from its
resolved input manifest; accepting a result also supersedes the sibling
candidates; a stop cascades to every open Task; and the Mission is judged on the
tree its root resolution names.  The flat orchestration mode was removed on
2026-10-02: a flat Mission left in a library is stopped by name.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simple_harness.agents import AgentConfig, AgentLimits, AgentTurnState
from simple_harness.execution.provider_admission import ProviderAdmissionDenied

from .accounting_recovery import import_late_accounting

if TYPE_CHECKING:
    from ..runtime.provider_budget_guard import ProviderBudgetGuard

from ..artifacts.bound_workspace import (
    UnifiedDiffApplyError,
    bound_artifacts_named_in_envelope,
    decode_unified_diff_text,
    files_patched_by_unified_diff,
)
from ..artifacts.store import ArtifactStoreError, read_nofollow, read_verified
from ..artifacts.versioning import (
    ArtifactConflict,
    UpstreamInput,
    next_versions,
)
from ..artifacts.workspace import WorkspaceError
from ..context.context_builder import (
    CONTEXT_BUILDER_VERSION,
    ContextRejected,
    build_worker_package,
)
from ..context.knowledge_tools import MAX_PUSHED_SUMMARIES, step_summaries
from ..context.retrieval import (
    KnowledgeContext,
    RetrievalUnavailable,
    candidate_claims,
    disputed_claims,
    knowledge_view,
    rank_knowledge,
)
from ..contracts.error_table import refusal_charges_planner
from ..contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    TERMINAL_TASK,
    Artifact,
    Attempt,
    AttemptStatus,
    ClaimStatus,
    ContractError,
    Mission,
    MissionStatus,
    MissionStopReason,
    ResultEnvelope,
    ResultOutcome,
    Task,
    TaskStatus,
    ids,
)
from ..contracts.error_table import CodedFault, RoundFaultCode, SharingRefused
from ..contracts.models import jsonable, sha256_hex
from ..contracts.planning_decisions import (
    PlanningRefKind,
    PlanningRefV1,
    PlanningRequestBinding,
    UnsupportedPlanningPackage,
)
from ..contracts.resolution import DeliveryStage
from ..planning.decision_feedback import refusal_text
from ..contracts.semantic_base import content_hash_of
from ..contracts.state_machines import IllegalTransition
from ..governance.budgets import BudgetError, BudgetExhausted
from ..governance.permissions import Principal
from ..governance.policies import action_decision, deployed_layers, effective_tools
from ..governance.promotion import diff_params, interpreter_versions, resolve_params
from ..graph.eligibility import EligiblePrimitiveTask
from ..graph.projection_validation import GraphIntegrityError
from ..memory.knowledge_standing import STALE as KNOWLEDGE_STALE, knowledge_standing
from ..memory.verified_knowledge import KnowledgeIndex
from ..graph.terminal import terminal_task
from ..runtime.actions import ActionExecutor, publication_overlaps_storage
from ..runtime.agent_worker import AgentBridge, Liveness, user_message_json
from ..runtime.assembly import (
    AssembledOrchestratorRuntime,
    OrchestratorConfig,
    assemble_orchestrator_runtime,
)
from ..runtime.first_request_budget import (
    FirstRequestBudget,
    FirstRequestBudgetUnknown,
    actual_output_ceiling,
    first_request_budget,
    frozen_provider_input_cap,
)
from ..runtime.model_router import (
    DEFAULT_PROFILE,
    ModelRouter,
    RoutingDecision,
    RoutingRules,
    RoutingUnavailable,
    RuntimeProfile,
    _error_codes,
    classify_turn_error,
)
from ..runtime.output_blocks import (
    BlockError,
    PortClaim,
    extract_block,
    outside_text,
    parse_port_claims,
)
from ..runtime.role_templates import (
    PLANNER_HIERARCHICAL,
    RESULT_ENVELOPE_TAG,
    role_for_task,
    template_for_domain,
)
from ..runtime.sandbox import resolve_executor
from ..runtime.tool_gateway import CRITIC_TOOLS, WORKER_TOOLS, WorkspaceBinding, run_pytest
from ..scheduling.allocator import (
    OPEN_ATTEMPT_STATES,
    allocate_v2,
)
from ..scheduling.backpressure import BackpressureState, Observation
from ..storage.store import (
    DispatchIntent,
    InjectedCrash,
    Store,
    StoreBusy,
    StoreConflict,
    StoreError,
)
from ..verification.critics import CriticVerdict
from ..verification.deterministic_checks import LayerResult
from ..verification.human_review import (
    NEEDS_HUMAN,
    arbitration_request_id,
    judgment_conflict,
    reusable_layers,
    review_request_id,
)
from ..verification.verifier_router import VERIFIER_VERSION, VerifierRouter
from .action_commits import (
    ACTION_PREFIX,
    HANDOFF_READY_STATES,
    IN_FLIGHT_ACTION_STATES,
    OPEN_ACTION_STATES,
    ActionCommitError,
    judgment_key,
    parse_action_criterion,
)
from .commit_service import (
    CommitRejected,
    CommitService,
    MissionSpec,
    Reservation,
    mission_account,
    is_global_account,
    task_account,
)
from ..deployment.root import current_criteria, current_statements
from .carried_review import CARRIED_RESULT_REJECTED, carried_reviews
from .hierarchical_dispatch import (
    MISSION_STALLED,
    HierarchicalDispatch,
    WriteConflictPending,
    append_hierarchical_event,
    is_hierarchical,
    record_assembly_missing,
)
from .occurrence_tasks import (
    read_only_existing_paths,
    read_only_leaf,
    read_only_rewrites,
)
from .plan_commits import PlanCommitRejected, PlanPrincipal
from .progress import IdleFacts, Route, idle_verdict
from .recovery_coordinator import RecoveryCoordinator

#: Events that observe the world without changing it (NEXT-TG-1.0 §3.6): a cycle
#: that wrote only these made no progress.
OBSERVATION_EVENTS = frozenset({
    "HeartbeatReceived",
    "TaskGraphConvergenceWakeRequested",
    "AssuranceCloseoutEvaluated",
    "AssuranceUseValidityChecked",
    "HierarchicalMissionStalled",
    # 存储层记账（全业务重放 v3）：跟着业务写入走，本身不是进展（偏差裁决 1 R13）
    "RowsWritten",
})
HOLLOW_CYCLES_NOTED = 100
WAIT_BACKOFF_MAX = 1.0
def _sharing_refusal_codes(error: BaseException) -> list[str] | None:
    """共享核对的拒绝换成给规划器的码；只认 :class:`SharingRefused` 这个类型，不读异常文字。

    别的异常（哪怕消息以 ``TASKGRAPH_SHARED_`` 开头）返回 ``None``——那是库故障，不归规划器。
    """

    if not isinstance(error, SharingRefused):
        return None
    return [str(error.planner_code)]


class ServiceTurnIdentityMismatch(ContractError, CodedFault):
    """The Critic turn bound at start differs from the frozen intent: retrying meets the same row."""

    code = RoundFaultCode.SERVICE_TURN_IDENTITY_MISMATCH


#: A stopped Mission's action whose outcome became known after the stop (H-2).
ACTION_SETTLED_AFTER_STOP = "ActionSettledAfterMissionStopped"
#: 裁决题在回答前过期：这份审查不会再有结论，如实交给规划器（同 method_plan_reviews 的口径）。
_RULING_STALE = {"outcome": "NO_VERDICT",
                 "reason": "the question asking the person to rule on this review went stale before "
                           "it was answered (the plan, the requirements or the management epoch "
                           "changed); no ruling was given"}


class DeferredPlanning(dict):
    """mission id → (since, ordinal) of a Planner round waiting for its pool.

    NEXT-TG-1.0 §3.6: it lived only in memory, so a restart while the planner pool
    cooled down forgot the round — counted as used and never asked. The map is
    now kept in ``scheduler_state`` and reloaded by :meth:`Orchestrator.recover`;
    ``since`` survives too, so the wait bound is not reset by a restart.
    """

    KEY = "deferred_planning"

    def __init__(self) -> None:
        super().__init__()
        self._store: Any = None

    def bind(self, store: Any) -> None:
        self._store = store
        saved = store.get_scheduler_state(self.KEY) or {}
        for mission_id, (since, ordinal) in dict(saved.get("missions") or {}).items():
            super().setdefault(str(mission_id), (float(since), int(ordinal)))

    def _save(self) -> None:
        if self._store is not None:
            self._store.put_scheduler_state(
                self.KEY, {"missions": {key: [since, ordinal] for key, (since, ordinal) in self.items()}}
            )

    def __setitem__(self, key: str, value: tuple[float, int]) -> None:
        if self.get(key) == value:
            return
        super().__setitem__(key, value)
        self._save()

    def pop(self, key: str, *default: Any) -> Any:
        present = key in self
        value = super().pop(key, *default)
        if present:
            self._save()
        return value


#: ``plan`` intents that are not Planner rounds (NEXT-TG-1.0 §0.6 overlap 3).
NOT_PLANNER_ROLES = frozenset({
    "operation_proposal_reviewer", "operation_outcome_reviewer",
})
from .resolution_commits import ResolutionCommitRejected, eligible_root_receipts
from .taskgraph_epochs import planning_scope_digest

logger = logging.getLogger("agent_orchestrator")


#: The Mission was handed back to the Planner ``max_root_review_repairs`` times and
#: the final review still stands rejected.  A named stop, not idle
#: ``no_dispatchable_work``.
ROOT_REVIEW_REPAIRS_EXHAUSTED = "root_review_repairs_exhausted"

#: Deterministic 4xx provider refusals.  Runtime already settles
#: ``ProviderAuthenticationError`` / ``ProviderPaymentRequiredError`` as FAILED
#: (``_DEFINITE_PROVIDER_FAILURES``); this set is what the orchestrator reads off
#: a FAILED turn so it does not climb the planning ladder or RETRY_WAIT.
DEFINITE_AUTH_CODES = frozenset({"provider_authentication_failed", "provider_payment_required"})

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
    # HTN 补齐 F1-2（崩溃切点 K06、K08、K09）
    "before_goal_resolution",  # a goal's review is saved, its conclusion not committed yet
    "after_handoff_before_call",  # an operation's hand-off is recorded, the external call not made
    "after_external_effect",  # the external call returned, its outcome not recorded yet
)
MAX_CRITIC_ATTEMPTS = 2
SYSTEM_CRITIC_MODEL_CALLS = 12


RECONCILE_EVERY_CYCLES = 50  # D7-5': UNKNOWN actions are asked about again while a run goes on

#: How many times one Mission's stall confirmation may say "the world moved, go round
#: again" within a single ``run()`` (P2.3c part 3a, third-round review P1-A).  The
#: confirmation cycle has side effects — it re-issues licences and records
#: observations — so a deployment whose observers answer something new each time would
#: move the fingerprint for ever.  Past this bound the run returns with the Mission
#: still ACTIVE and its stall recorded, which is an answer to the caller rather than a
#: verdict about the Mission.
MAX_STALL_CARRY_ONS = 2
#: P2.3f: how long a hierarchical Mission's service turn (Planner,
#: root reviewer, Critic) may sit on a Provider hand-off whose outcome is *unknown*
#: before the loop acts — the smaller of ``stall_seconds`` and this ceiling.  The
#: runtime is right not to settle such an invocation (the request may have reached
#: the model), and it is equally right that a Mission does not spend its whole
#: deadline on one question nobody can answer: the loop hands the same request off
#: once more, and if that is unknown too it ends the round through the role's own
#: failure door.
#: 2026-10-02 用户决定：300 秒改 30 秒。原来实际生效的是 ``stall_seconds``（180 秒）——
#: 服务重启打断一次模型调用后，任务要原地不动等满三分钟才重做。结果不明的调用再等也等不
#: 来答案，30 秒足够把"慢"和"丢了"分开；真正还在进行的调用不走这条路（那是
#: ``provider_response_wait``，由 ``stall_seconds`` 管）。
MAX_SERVICE_BLOCKER_SECONDS = 30.0
#: One re-hand-off per subject.  A second executor asking the same question is a
#: retry; a third is a loop that spends the Mission account on a Provider that is
#: down, which is what the deadline exists to end.
MAX_SERVICE_REHANDOFFS = 1
#: P2.3p: consecutive after-handoff 0-token UNKNOWNs on one Mission before the
#: loop stops as ``runtime_unavailable``.  Same width as P2.3f's per-subject
#: retry — the original hand-off plus :data:`MAX_SERVICE_REHANDOFFS` re-hand-offs.
#: Not a config item.
MAX_CONSECUTIVE_AFTER_HANDOFF_UNKNOWNS = MAX_SERVICE_REHANDOFFS + 1
# step 9 (plan D9-3'): the whitelisted items a deployment configuration also names — a
# difference from the ACTIVE version is recorded as drift (the version still governs)
CONFIG_DERIVED = frozenset(
    {
        "exploration_slots",
        "mission_concurrency",
        "aging_window_seconds",
        "routing",
    }
)


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


class _AcceptedSiblingSupersededVerification(Exception):
    """Only the recorder's rejected lease may signal this obsolete verifier."""


class _CriticAdmissionFailure(ContractError):
    """An SDK admission failure, not a malformed completed Critic verdict."""

    def __init__(self, error):
        super().__init__("critic provider admission denied")
        self.error = jsonable(error)


class _AssuranceReviewUnavailable(ContractError):
    """An admitted planning subject could not open its independent Assurance review."""

    def __init__(self, purpose: str, error: Any) -> None:
        self.purpose = purpose
        self.code = str(getattr(error, "code", error))
        super().__init__(f"Assurance {purpose} review unavailable: {error}")


# 2026-09-28: how many Planner turns that produced no reply (provider error, timeout,
# an interrupted run) one planning question may absorb before they count again.
PLANNER_TURN_FAILURE_GRACE = 6

#: 没有新事件的任务多久无论如何全量处理一次（秒）：卡死检测、租约到期等按时间发生的事
#: 靠它照常发生（2026-09-29 第 4 批）。
MISSION_RECHECK_SECONDS = 10.0


class PlannerTurnFailed(ContractError):
    """The Planner's turn ended without a reply; nothing it said was refused."""


def planning_failure_detail(error: Exception, detail: dict[str, Any]) -> dict[str, Any]:
    if isinstance(error, PlannerTurnFailed):
        detail["turn_failed"] = True
    return detail


def _turn_failed(event: Any) -> bool:
    detail = event.payload.get("detail") if isinstance(event.payload, Mapping) else None
    return isinstance(detail, Mapping) and detail.get("turn_failed") is True


def _refusal_codes(event: Any) -> list[str]:
    """The problem codes a ``PlanningRejected`` event names."""
    detail = event.payload.get("detail") if isinstance(event.payload, Mapping) else None
    problems = detail.get("problems") if isinstance(detail, Mapping) else None
    preview = detail.get("preview") if isinstance(detail, Mapping) else None
    mapped = preview.get("mapped_problems") if isinstance(preview, Mapping) else None
    return [*(str(item.get("code")) for item in problems or () if isinstance(item, Mapping)),
            *(str(item) for item in mapped or ())]


def _stale_commit_problems(reason: object) -> dict[str, Any]:
    """A commit refused because the world moved between preview and commit (the read set or
    the plan revision went stale) is the same fact as a stale request: named so on the
    refusal, it is not counted as the Planner answering wrongly (阶段 D)."""
    if str(reason) in {"READ_SET_STALE", "PLAN_REVISION_STALE"}:
        return {"problems": [{"code": "REQUEST_BINDING_STALE", "detail": str(reason)}]}
    return {}


class Orchestrator:
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
            from ..runtime.legacy_provider_slots import profile_has_frozen_admission

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
        self._round_faults: dict[tuple[str, str], tuple[int, float]] = {}
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

    # ------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> Orchestrator:
        self._store = Store.open(self._config.orchestrator_db)
        try:
            self._commit = CommitService(
                self._store,
                global_budget=self._config.global_budget,
                task_max_tokens=self._config.task_max_tokens,
                deployed_layers=self._deployed,
                mission_profile_validator=self._validate_mission_profile,
            )
            from ..assurance.root_gate import AssuranceRootGate
            from ..assurance.codec import AssuranceError

            # 车道 J H04（§14.4）：模型写的代码起的子进程，身份与回收材料落库；重启后按它回收。
            if self._executor is not None:
                from ..storage.recovery_store import StoreSandboxLedger

                self._executor.ledger = StoreSandboxLedger(self._store)
            if (self._assurance_root_setup is not None
                    or AssuranceRootGate.required(self._store, self._config.evidence_root)):
                self._assurance_root_gate = AssuranceRootGate(self._store, self._config.evidence_root)
                self._commit._assurance_root_gate = self._assurance_root_gate
                self._store._assurance_root_gate = self._assurance_root_gate
                #: 根进隔离时的匿名阻塞码（原计划 §10.3 允许披露的一项）；NATIVE 时为 None
                self._assurance_quarantine_code: str | None = None
                try:
                    # This callback has only Store/Commit, never live runtime pools.
                    # The Host may bind its CURRENT authenticated management authority.
                    if self._assurance_root_setup is not None:
                        self._assurance_root_setup(self)
                    self._assurance_root_gate.require_execution()
                except AssuranceError as error:
                    # 原计划 §10.1（第 2 批 A02）：标记缺失 / 不符、安装身份冲突 → 根进隔离：服务起来，
                    # 只开非披露诊断与隔离只读分支；不装配、不派发、不自动写回状态文件。
                    # Keep the authenticated management API available. No SDK
                    # pools, recovery, workspace cleanup, Context or dispatch runs.
                    self._assurance_quarantine_code = error.code
                    self._assurance_management_only = True
                    return self
            if self._provider_token_estimator is not None:
                from ..runtime.provider_budget_guard import ProviderBudgetGuard

                self._provider_admission = ProviderBudgetGuard(
                    self._commit,
                    owner=self._owner,
                    estimator=self._provider_token_estimator,
                    max_slots=self._config.max_concurrent_model_calls,
                    profile_slots=(
                        {
                            key: min(
                                profile.max_concurrent_model_calls
                                or self._config.max_concurrent_model_calls,
                                self._config.max_concurrent_model_calls,
                            )
                            for key, profile in self._profiles.items()
                        }
                        if any(
                            profile.max_concurrent_model_calls is not None
                            for profile in self._profiles.values()
                        )
                        else None
                    ),
                )
            if self._provider_token_estimators is not None:
                from ..runtime.provider_budget_guard import ProviderBudgetGuard

                slots = (
                    {
                        key: min(
                            profile.max_concurrent_model_calls
                            or self._config.max_concurrent_model_calls,
                            self._config.max_concurrent_model_calls,
                        )
                        for key, profile in self._profiles.items()
                    }
                    if any(
                        p.max_concurrent_model_calls is not None for p in self._profiles.values()
                    )
                    else None
                )
                def guard_for(key: str, estimator: Any) -> Any:
                    return ProviderBudgetGuard(
                        self._commit,
                        owner=self._owner,
                        estimator=estimator,
                        max_slots=self._config.max_concurrent_model_calls,
                        profile_slots=slots,
                    )

                def admission_for(key: str) -> Any:
                    """A pool may offer several candidate estimators (a counter identity
                    changed while the pool kept intents frozen with the older one).  The
                    admission identity persisted in its intents picks the candidate; a
                    pool with no persisted intent takes the first.  Nothing is migrated."""
                    estimator = self._provider_token_estimators[key]
                    if estimator is None:
                        return None
                    candidates = tuple(estimator) if isinstance(estimator, (tuple, list)) else (estimator,)
                    guards = [guard_for(key, candidate) for candidate in candidates]
                    if len(guards) == 1:
                        return guards[0]
                    frozen = self._frozen_admission_fingerprints(key)
                    return next((guard for guard in guards if any(guard.accepts(f) for f in frozen)), guards[0])

                self._provider_admissions = {key: admission_for(key) for key in self._profiles}
                self._provider_admission = self._provider_admissions[self._default_profile]
            local_admissions = {}
            if self._provider_admissions is None and self.store.has_table(
                "legacy_provider_slots_v1"
            ):
                # Once activated, a later deployment cannot silently return an
                # old pool to process-local slots by omitting the estimator map.
                self._provider_admissions = {
                    key: self._provider_admission for key in self._profiles
                }
            if self._provider_admissions is not None and any(
                admission is None for admission in self._provider_admissions.values()
            ):
                from ..runtime.legacy_provider_slots import LegacyProviderSlots, create_slot_table

                create_slot_table(self.store)
                local_admissions = {
                    key: LegacyProviderSlots(
                        self.store,
                        profile_id=key,
                        max_slots=self._config.max_concurrent_model_calls,
                        profile_slots=min(
                            profile.max_concurrent_model_calls
                            or self._config.max_concurrent_model_calls,
                            self._config.max_concurrent_model_calls,
                        ),
                        fence=self._provider_handoff_fence,
                    )
                    for key, profile in self._profiles.items()
                    if self._provider_admissions[key] is None
                }
            self._open_policy_library()  # step 9 (plan D9-3'): seed, drift
            self._assembled = assemble_orchestrator_runtime(
                self._config,
                profiles=self._profiles,
                default_profile=self._default_profile,
                provider_admission=(
                    self._provider_admission if self._provider_admissions is None else None
                ),
                provider_admissions=self._provider_admissions,
                local_provider_admissions=local_admissions,
                provider_handoff_fence=self._provider_handoff_fence,
            )
            self._actions = ActionExecutor(  # D7-5: the only caller of connectors
                self._commit,
                self._connectors,
                self._config.deployment_policy,
                owner=self._owner,
                require_execution_root=self._require_assurance_execution_root,
                source_storage_roots=(
                    self.assembled.workspaces.artifact_store.root,
                    self.assembled.workspaces.root,
                ),
            )
            # Trusted deployment collaborators must exist before restoring frozen
            # workspaces or allowing SDK startup to resume a physical call.
            if self._startup_assembly is not None:
                self._startup_assembly(self)
            self._assembled.gateway.executed_lookup = self._executed_tool_proof
            # Before ANY pool may resume, import every old physical handoff.
            # This does not change the external admission identity of old intents.
            for key, admission in local_admissions.items():
                admission.recover(self._assembled.pool(key).runtime.uow)
            # SDK startup itself reconciles grants. Consume already durable late
            # receipts before it can raise on an actual overrun; no runtime is started
            # by this accounting-only pass. Tool counts also come from durable rows.
            self._commit.tool_calls_for = self._executed_tool_calls
            import_late_accounting(self)
            # Reconcile orphan execution identities before any runtime can resume tools.
            workspaces = self._assembled.workspaces
            await asyncio.to_thread(workspaces.sweep_exec_copies)
            # SDK __aenter__ can reconcile an UNKNOWN tool and immediately run
            # its successor. Install Host bindings and durable accounting first.
            self._assembled.gateway.on_rejected = self._audit_tool_rejection
            self._assembled.gateway.before_execute = self._taskgraph_tool_handoff
            self._assembled.gateway.on_executed = self._record_tool_call
            from ..context.knowledge_tools import read_knowledge_tool

            self._assembled.gateway.knowledge_reader = lambda mission_id, tool, args, reader: (
                read_knowledge_tool(self.store, mission_id, tool, args,
                                    handover=self._knowledge_handover(mission_id, reader))
            )
            self._assembled.gateway.executed_counter = self.store.count_tool_calls
            self._assembled.gateway.execution_refusal = self._tool_execution_refusal
            self._bind_startup_tools()
            try:
                await self._assembled.__aenter__()
            except ProviderAdmissionDenied as error:
                if error.detail.get("reason_code") == "bound_overrun":
                    # A receipt may appear during startup reconciliation, after the
                    # first scan. Pay its original actual cost even when SDK startup
                    # has failed; do not pretend that failed runtime has started.
                    import_late_accounting(self)
                raise
            # execution copies a crash left behind are removed
            workspaces = self._assembled.workspaces
            self.cleanup_workspaces()  # P3.2 D4: finished Missions past their retention
            self._bridge = self._assembled.pool(self._default_profile).bridge
            self._pressure = self._commit.backpressure_state()
            return self
        except BaseException as error:
            # Enter failures do not trigger async-with's exit. Preserve the startup
            # error (including cleanup reports) even if resource shutdown also fails.
            try:
                await self.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:
                error.add_note(f"Startup resource cleanup failed: {type(cleanup_error).__name__}")
            raise

    def _tool_execution_refusal(self, attempt_id: str) -> str | None:
        with self.store.read_view():
            attempt = self.store.get_attempt(attempt_id)
            if attempt is None:
                # A Mission-level judgment view ("<mission>-judge-<owner>", see
                # ``_judge_mission``) is not a Worker Attempt.  Refusing it here left
                # the judge unable to read a single file after the root resolution
                # stood, so it judged every criterion unmet and failed the Mission
                # (2A upstream run, 2026-09-27).  It keeps authority while its Mission
                # is live.
                mission_id, sep, _owner = attempt_id.partition("-judge-")
                mission = self.store.get_mission(mission_id) if sep else None
                if mission is not None and mission.status not in TERMINAL_MISSION:
                    return None
                return "attempt_unavailable"
        return None

    @contextlib.contextmanager
    def _provider_handoff_fence(self, agent_id: str, turn_id: str):
        """Fence legacy local-slot handoff with the same Store as public cancel.

        No estimator is required for lifecycle authority. Submitted service turns
        remain collectible after cancellation, but cannot start another request.
        """
        self._require_assurance_execution_root()
        with self.store.transaction():
            rows = self.store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE agent_id=? AND expected_turn_id=?",
                (agent_id, turn_id),
            ).fetchall()
            if len(rows) != 1:
                raise ProviderAdmissionDenied(
                    public_message="Provider has no unique dispatch intent."
                )
            intent = self.store.get_intent(rows[0][0])
            mission = None if intent is None else self.store.get_mission(intent.mission_id)
            if (
                intent is None
                or mission is None
                or mission.status in TERMINAL_MISSION
                or intent.state not in {"AGENT_CREATED", "SUBMITTED"}
            ):
                raise ProviderAdmissionDenied(
                    public_message="Provider subject Mission or intent stopped."
                )
            try:
                self.commit.require_taskgraph_handoff(intent)
            except StoreError as error:
                raise ProviderAdmissionDenied(public_message="TaskGraph attempt authority changed.") from error
            yield

    # ------------------------------------------------------------ policies (step 9)
    def _config_policy(self) -> dict[str, Any]:
        """The deployment configuration read as a resolved policy (plan D9-1')."""

        return resolve_params(self._config, routing=self._model_router.rules)

    def _open_policy_library(self) -> None:
        """Plan D9-3': a library gets its seed (the resolved built-in policy) on first
        use; a configuration whose whitelisted values differ from the ACTIVE version is
        recorded as drift, and the ACTIVE version still governs."""

        configured = self._config_policy()
        config_hash = sha256_hex(configured)[:16]
        active = self.commit.seed_policy(
            configured,
            detail={
                "config_hash": config_hash,
                "sources": "OrchestratorConfig + code constants (allocator WEIGHTS, role templates)",
            },
        )
        if active.get("params"):
            differences = [
                d
                for d in diff_params(active["params"], configured)
                if str(d["key"]).split(".")[0] in CONFIG_DERIVED
            ]
            if differences:
                self.commit.record_policy_drift(config_hash=config_hash, differences=differences)

    def policy_version_of(self, mission_id: str) -> str | None:
        binding = self.store.get_mission_policy(mission_id)
        return None if binding is None else str(binding["version_id"])

    def _selected_profile(self, mission_id: str) -> str | None:
        mission = self.store.get_mission(mission_id)
        if mission is None:
            return None
        selected = (mission.final_report or {}).get("runtime_profile_id")
        return selected if isinstance(selected, str) and selected else None

    def policy_for(self, mission_id: str) -> dict[str, Any]:
        """The resolved parameters of the version ``mission_id`` is bound to (plan
        D9-4'); ``legacy`` Missions follow the deployment configuration, as they did."""

        version_id = self.policy_version_of(mission_id)
        if version_id is None:
            raise ContractError(f"mission {mission_id} is bound to no policy version")
        cached = self._policies.get(version_id)
        if cached is None:
            version = self.store.get_policy_version(version_id)
            if version is None:
                raise ContractError(
                    f"mission {mission_id} is bound to {version_id}, which this library does not have"
                )
            params = version.get("params")
            cached = dict(params) if params else self._config_policy()
            self._policies[version_id] = cached
        return cached

    def _validate_mission_profile(self, selected: str, params: Mapping[str, Any]) -> None:
        """Creation-transaction guard: a selected pool cannot mask a policy route."""
        from ..api.missions import MissionRequestError

        if selected not in self._profiles:
            raise MissionRequestError(f"runtime profile {selected!r} is not configured")
        base = self._model_router.rules
        policy_routing = dict(params.get("routing") or {})
        routes = {
            **{f"role:{key}": value for key, value in base.by_role.items()},
            **{f"task_kind:{key}": value for key, value in base.by_task_kind.items()},
            **{
                f"task_kind:{key}": value
                for key, value in dict(policy_routing.get("by_task_kind") or {}).items()
            },
        }
        conflict = next((name for name, target in routes.items() if target != selected), None)
        if conflict is not None:
            raise MissionRequestError(
                f"runtime profile {selected!r} conflicts with policy route {conflict}"
            )
        for name, overrides in (("escalate", base.escalate), ("fallback", base.fallback)):
            if selected in overrides and overrides[selected] != selected:
                raise MissionRequestError(
                    f"runtime profile {selected!r} conflicts with {name} route"
                )

    def _template(self, template: Any, mission_id: str) -> Any:
        return template_for_domain(
            template,
            self.commit.domain_for(mission_id),
            self.policy_for(mission_id)["prompt_versions"],
        )

    def _frozen_default_route(self, mission_id: str) -> str | None:
        """Recover an existing Mission's default from its original dispatch facts.

        Policy params freeze by-task-kind routing, not the deployment default.
        Only an explicit default decision proves that default; a role override,
        escalation or fallback must never be mistaken for it. No intent is edited.
        """
        defaults = set()
        for (encoded,) in self.store.connection.execute(
            "SELECT config_json FROM dispatch_intents WHERE mission_id=?",
            (mission_id,),
        ):
            frozen = json.loads(encoded)
            decision = frozen.get("routing") or {}
            if decision.get("reason") != "default":
                continue
            target = frozen.get("runtime_profile_id") or DEFAULT_PROFILE
            if (
                decision.get("profile_id") != target
                or (frozen.get("agent_config") or {}).get("model_profile_ref") != target
            ):
                raise ContractError("frozen default routing identities differ")
            defaults.add(target)
        if len(defaults) > 1:
            raise ContractError("Mission has conflicting frozen default routes")
        return next(iter(defaults)) if defaults else None

    def _router_for(self, mission_id: str) -> ModelRouter:
        """One router per policy version: the deployment's rules with the version's
        routing items on top; an item naming a profile this deployment lacks falls back
        to the deployment's rule, on record (plan D9-4')."""

        version_id = self.policy_version_of(mission_id) or ""
        selected = self._selected_profile(mission_id)
        frozen_default = None if selected is not None else self._frozen_default_route(mission_id)
        cache_key = (version_id, selected, frozen_default)
        router = self._routers.get(cache_key)
        if router is not None:
            if selected is None:
                self._note_route_drops(mission_id, version_id)
            return router
        if selected is not None:
            # All roles, including Planner/Critic/system tasks, use the Mission's
            # persisted choice. No fallback or escalation may leave that pool.
            from ..api.missions import MissionRequestError

            try:
                self._validate_mission_profile(selected, self.policy_for(mission_id))
            except MissionRequestError as error:
                raise ContractError(f"bound Mission runtime route unavailable: {error}") from error
            router = ModelRouter(self._profiles, RoutingRules(default=selected))
            self._routers[cache_key] = router
            return router
        routing = dict(self.policy_for(mission_id).get("routing") or {})
        base = self._model_router.rules
        if frozen_default is not None:
            if frozen_default not in self._profiles:
                raise ContractError(f"frozen default profile {frozen_default!r} is not configured")
            base = replace(base, default=frozen_default)
        by_kind = dict(base.by_task_kind)
        dropped: dict[str, str] = {}
        for kind, target in dict(routing.get("by_task_kind") or {}).items():
            if target in self._profiles:
                by_kind[str(kind)] = str(target)
            else:
                dropped[str(kind)] = str(target)
        self._route_drops[version_id] = dropped
        self._note_route_drops(mission_id, version_id)
        rules = replace(
            base,
            by_task_kind=by_kind,
            escalate_after_failures=int(
                routing.get("escalate_after_failures", base.escalate_after_failures)
            ),
        )
        router = ModelRouter(self._profiles, rules)
        self._routers[cache_key] = router
        return router

    def _note_route_drops(self, mission_id: str, version_id: str) -> None:
        dropped = self._route_drops.get(version_id)
        if dropped and mission_id not in self._route_noted:
            self._route_noted.add(mission_id)
            self.commit.record_policy_route_unavailable(
                mission_id, version_id=version_id, dropped=dropped
            )

    def _check_interpreter(self, mission: Mission) -> None:
        """Plan D9-4': a resumed Mission whose bound version was recorded under other
        interpreter versions says so on its timeline (once) — never silently."""

        version_id = self.policy_version_of(mission.id)
        version = None if version_id is None else self.store.get_policy_version(version_id)
        bound = dict((version or {}).get("interpreter_versions") or {})
        if not bound or version_id is None:
            return
        running = interpreter_versions()
        differences = [
            {"key": key, "bound": bound[key], "running": running.get(key)}
            for key in sorted(bound)
            if running.get(key) != bound[key]
        ]
        if differences:
            self.commit.record_interpreter_drift(
                mission.id, version_id=version_id, differences=differences
            )

    def policy_snapshot(self) -> dict[str, Any]:
        """Step 8 (plan D8-5'): where this orchestrator's behaviour comes from."""

        from ..governance.policies import policy_snapshot

        return policy_snapshot(
            self._config,
            profiles=self._profiles,
            routing=self._routing,
            connectors=self._connectors,
            provider=self._provider,
        )

    @property
    def actions(self) -> ActionExecutor:
        assert self._actions is not None, "use `async with Orchestrator(...)`"
        return self._actions

    def _audit_tool_rejection(self, run_id: str, record: Mapping[str, Any]) -> None:
        """Every gateway refusal becomes a ``ToolCallRejected`` event on its Mission."""

        attempt_id = str(record.get("attempt_id") or "")
        attempt = self.store.get_attempt(attempt_id) if attempt_id else None
        mission_id: str | None = None
        task_id: str | None = None
        if attempt is not None:
            mission_id, task_id = attempt.mission_id, attempt.task_id
        elif attempt_id:  # a Mission-level judgment view: "<mission>-judge-<owner>"
            candidate = attempt_id.split(":", 1)[0].split("-judge-", 1)[0]
            if self.store.get_mission(candidate) is not None:
                mission_id = candidate
        else:  # review P2-5: an unbound run (e.g. a released zombie turn) — find its intent
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED"
            ):
                if intent.agent_id == run_id:
                    mission_id = intent.mission_id
                    attempt = self.store.get_attempt(intent.subject_id)
                    task_id = None if attempt is None else attempt.task_id
                    break
        if mission_id is None:
            return
        self.commit.record_tool_rejected(
            mission_id,
            task_id=task_id,
            attempt_id=None if attempt is None else attempt.id,
            run_id=run_id,
            call_key=f"{run_id}:{record.get('call_id')}",
            record=record,
        )
        if record.get("error_code") == "read_only_leaf_kept_writing" and attempt is not None:
            self._schedule_read_only_kept_writing_stop(attempt)

    def _record_tool_call(self, run_id: str, record: Mapping[str, Any]) -> None:
        """Review P1-3: an executed Worker tool call is a durable fact (once per SDK call id)."""

        if record.get("view") != "work":
            return  # Critic / judgment views are read-only and not charged to the dimension
        attempt = self.store.get_attempt(str(record.get("attempt_id") or ""))
        if attempt is None:
            return
        self.commit.record_tool_call(
            call_key=f"{run_id}:{record.get('call_id')}",
            subject_id=attempt.id,
            mission_id=attempt.mission_id,
            tool=str(record.get("tool")),
        )

    def _executed_tool_proof(self, call_key: str) -> Mapping[str, Any] | None:
        record = self.store.get_tool_call(call_key)
        if record is None:
            return None
        attempt = self.store.get_attempt(record["subject_id"])
        if attempt is None or attempt.mission_id != record["mission_id"]:
            return None
        return {**record, "agent_id": attempt.agent_id}

    def _read_only_inputs(self, attempt_id: str) -> tuple[str, ...]:
        """D6-6: the upstream inputs this Attempt may read but not rewrite (the Task did
        not declare them as ``outputs``); seed files keep the step-2 tamper detection."""

        attempt = self.store.get_attempt(attempt_id)
        if attempt is None:
            return ()
        task = self.store.get_task(attempt.task_id)
        mission = self.store.get_mission(attempt.mission_id)
        if task is None or mission is None:
            return ()
        try:
            protected = self._protected_files(mission, task, attempt)
        except ArtifactConflict:
            return ()
        seed = self._protected_seed(mission, task)
        return tuple(sorted(path for path in protected if path not in seed))

    def echoed_models_for(self, mission_id: str) -> dict[str, list[str]]:
        """attempt_id → the model names its provider echoed (read from the Attempt's own
        pool library) — the physical-route evidence ``trace.json`` carries (S6-03/S6-09)."""

        echoes: dict[str, list[str]] = {}
        for task in self.store.list_tasks(mission_id):
            for attempt in self.store.list_attempts(task.id):
                intent = self.store.get_intent_for_subject(attempt.id)
                if intent is None or intent.agent_id is None:
                    continue
                if self.profile_of(intent) not in self.assembled.pools:
                    continue
                models = self.bridge_for(intent).echoed_models(agent_id=intent.agent_id)
                if models:
                    echoes[attempt.id] = sorted(models)
        return echoes

    def _executed_tool_calls(self, subject_id: str) -> int:
        """The gateway's executed-call count for an Attempt (0 for service intents or an
        Attempt that never got an agent) — the fact the tool-call dimension settles on."""

        return self.store.count_tool_calls(subject_id)  # durable (review P1-3)

    async def __aexit__(self, *exc_info: object) -> None:
        for task in list(self._verifying.values()):
            if not task.done():
                task.cancel()
        for task in list(self._verifying.values()):
            with contextlib.suppress(BaseException):
                await task
        self._verifying.clear()
        try:
            if self._assembled is not None:
                await self._assembled.__aexit__(*exc_info)
        finally:
            self._assembled = None
            store, self._store = self._store, None
            if store is not None:
                store.close()

    @property
    def store(self) -> Store:
        assert self._store is not None
        return self._store

    def _require_assurance_execution_root(self) -> None:
        if self._assurance_root_gate is not None:
            self._assurance_root_gate.require_execution()
        if self._assurance_management_only:
            from ..assurance.codec import AssuranceError

            raise AssuranceError("ROOT_RUNTIME_NOT_STARTED")

    @property
    def commit(self) -> CommitService:
        assert self._commit is not None
        return self._commit

    @property
    def hierarchical(self) -> HierarchicalDispatch | None:
        """The hierarchical assembly, or None when this deployment has not installed one."""

        return self._hierarchical

    def install_hierarchical(self) -> HierarchicalDispatch:
        """Install the hierarchical assembly (P2.3b).  Without it nothing is planned,
        dispatched or judged: a Mission is recorded as waiting for the assembly.  Each
        Mission's planning world is bound per Mission (``_dispatch_for``)."""

        self._hierarchical = HierarchicalDispatch(self.store, self.commit)
        return self._hierarchical

    def set_between_cycles(self, duty: Callable[[], Any] | None, *, every_seconds: float) -> None:
        """Let the embedding host run its own duties while :meth:`run` is still going.

        NEXT-TG-1.0 2A.1g (real run 2026-09-27): the Host issues planning grants,
        check-policy approvals and TaskGraph enables only after ``run()`` returns, and
        ``run()`` does not return while any turn anywhere is in flight. One Mission's
        long model turn held every other Mission's grant for eleven minutes, and the
        Mission waiting for it could never start. The duty runs between cycles, never
        inside one, at most once per ``every_seconds``; its failure is logged and does
        not end the run.
        """

        self._between_cycles = duty
        self._between_every = max(0.0, float(every_seconds))
        self._between_last = 0.0

    async def _run_between_cycles(self) -> bool:
        if self._between_cycles is None:
            return False
        now = asyncio.get_running_loop().time()
        if now - self._between_last < self._between_every:
            return False
        self._between_last = now
        try:
            outcome = self._between_cycles()
            if asyncio.iscoroutine(outcome):
                outcome = await outcome
        except Exception as error:  # noqa: BLE001 - a host duty never ends the run
            logger.warning("orchestrator.between_cycles_failed error=%s", error)
            return False
        return bool(outcome)

    def install_hierarchical_deployment(self, world_factory: Any, *, start_gate: Any) -> None:
        """Install a deployment-owned, Mission-isolated world resolver.

        Each dispatcher has its own registry and evidence scope. Async turns never
        mutate a shared world's mission_id. Rebuilds reconstruct from durable data.
        """
        self._planning_world_factory = world_factory
        self._planning_start_gate = start_gate
        self._mission_dispatches.clear()
        self.install_hierarchical()

    def _blame_replaced_method(self, mission_id: str, decision: Any, decision_id: str) -> None:
        """换做法提交成功、规划器写明"换下的做法本身有错"（阶段 C3）：换下的做法若是照全库先例
        写的，记一条归因。同一事务；程序只记规划器明确写出的，怎么数在 method_library 里。"""
        from ..contracts.planning_decisions import RepairReplaceMethodDecision
        from .method_library import record_attribution

        payload = decision.payload
        if not isinstance(payload, RepairReplaceMethodDecision) or not payload.method_at_fault:
            return
        from ..storage.htn_store import HtnStore

        replaced = HtnStore(self.store).get_method_instance(mission_id, str(payload.rejected_method_instance.id))
        record_attribution(self.store, mission_id=mission_id, method_ref=replaced.method_ref,
                           source_ref=decision_id, source_kind="PLANNER", reason=payload.method_at_fault)

    def _dispatch_for(self, mission_id: str) -> HierarchicalDispatch | None:
        if self._planning_world_factory is None:
            return self._hierarchical
        mission = self.store.get_mission(mission_id)
        if mission is None:
            return None
        if mission_id not in self._mission_dispatches:
            world = self._planning_world_factory(mission)
            if world.mission_id != mission_id:
                raise ContractError("planning world belongs to another Mission")
            self._mission_dispatches[mission_id] = HierarchicalDispatch(
                self.store, self.commit, planning=world,
            )
        dispatch = self._mission_dispatches[mission_id]
        if self._taskgraph_dispatch_setup is not None:
            self._taskgraph_dispatch_setup(dispatch)
        return dispatch

    def _new_mode(self, mission: Mission) -> HierarchicalDispatch | None:
        """The assembly for this Mission, or None when this deployment installed none."""

        if self._hierarchical is None:
            return None
        return self._dispatch_for(mission.id)

    def install_taskgraph_notifications(self, *, history: Any, convergence: Any,
                                       validate_current: Any) -> Any:
        from .taskgraph_notifications import TaskGraphNotifications
        if self._taskgraph_notifications is not None:
            raise ValueError("TaskGraph notifications are already installed")
        if self._hierarchical is None:
            raise ValueError("TaskGraph notifications require the hierarchical assembly")
        self._taskgraph_notifications = TaskGraphNotifications(
            self, history=history, convergence=convergence, validate_current=validate_current)
        return self._taskgraph_notifications

    def install_taskgraph(self, ports: Any) -> Any:
        """Fixed deployment setup; never called with model or Host request JSON."""
        from .taskgraph_assembly import install_taskgraph
        return install_taskgraph(self, ports)

    def install_taskgraph_read_api(self, api: Any, *, tenant_id: str, principal: Any) -> None:
        from ..api.taskgraph import TaskGraphReadApi
        if not isinstance(api, TaskGraphReadApi) or not api.matches_binding(self.commit, tenant_id, principal):
            raise ValueError("TaskGraph read API must bind this Store and authenticated caller")
        if any(item.matches_binding(self.commit, tenant_id, principal) for item in self._taskgraph_read_apis):
            raise ValueError("TaskGraph read API is already installed for this caller")
        self._taskgraph_read_apis.append(api)

    def taskgraph_read_api(self, *, tenant_id: str, principal: Any) -> Any:
        for api in self._taskgraph_read_apis:
            if api.matches_binding(self.commit, tenant_id, principal):
                return api
        from ..api.taskgraph import _fail
        _fail("SOURCE_UNAVAILABLE", "TaskGraph source assembly is not installed", retry="OPERATOR_REPAIR")

    def install_assurance_read_api(self, api: Any, *, tenant_id: str, principal: Any) -> None:
        """Fixed-caller Assurance read verbs (S25); bound by the deployment assembly only."""
        from ..api.assurance import AssuranceApi
        if not isinstance(api, AssuranceApi) or not api.matches_binding(self.commit, tenant_id, principal):
            raise ValueError("Assurance read API must bind this Store and authenticated caller")
        if any(item.matches_binding(self.commit, tenant_id, principal) for item in self._assurance_read_apis):
            raise ValueError("Assurance read API is already installed for this caller")
        self._assurance_read_apis.append(api)

    def assurance_read_api(self, *, tenant_id: str, principal: Any) -> Any:
        for api in self._assurance_read_apis:
            if api.matches_binding(self.commit, tenant_id, principal):
                return api
        from ..api.assurance import AssuranceReadError
        raise AssuranceReadError("PROFILE_UNBOUND", "Assurance read assembly is not installed for this caller")

    def taskgraph_operator_api(self, *, tenant_id: str, principal: Any) -> Any:
        """Authenticated internal operator access, never part of model tools."""
        operator = self._taskgraph_operator
        if operator is None or not operator.matches_binding(self.commit, tenant_id, principal):
            raise StoreError("TASKGRAPH_OPERATOR_ASSEMBLY_UNAVAILABLE")
        return operator

    def taskgraph_policy_api(self, *, tenant_id: str, principal: Any) -> Any:
        """Explicit kernel activation through fixed authenticated Host setup."""
        policy = self._taskgraph_policy
        if policy is None or not policy.matches_binding(self.commit, tenant_id, principal):
            raise StoreError("TASKGRAPH_POLICY_ASSEMBLY_UNAVAILABLE")
        return policy

    def taskgraph_recheck_mission(self, mission_id: str) -> dict[str, Any]:
        """The notification re-enters the original typed reducer, without dispatching."""
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise StoreError("TASKGRAPH_MISSION_UNAVAILABLE")
        if mission.status in TERMINAL_MISSION:
            return {"status": "MISSION_TERMINAL", "mission_status": str(mission.status)}
        dispatch = self._new_mode(mission)
        if dispatch is None:
            raise StoreError("TASKGRAPH_HIERARCHICAL_ASSEMBLY_REQUIRED")
        if not self.store.connection.in_transaction:
            raise StoreError("TASKGRAPH_RECHECK_REQUIRES_TRANSACTION")
        # Re-enter the actual witness producers before reducing readiness. Reading
        # the cached witnesses alone does not discharge a revision/evidence wakeup.
        # These producers interpret the existing evidence snapshot; they perform
        # no provider calls and never promote a PLAN witness into START authority.
        dispatch._world()
        network = dispatch.network(mission_id)
        moment = int(self.store.now * 1000)
        inputs = dispatch.issue_input_witnesses(mission_id, network, now_ms=moment)
        starts = dispatch.issue_start_witnesses(mission_id, network, now_ms=moment)
        phases = dispatch.advance_compound_phases(mission_id)
        view = dispatch.read(mission_id, now_ms=moment)
        from .taskgraph_execution_sources import _document
        # ReadinessReport is a typed dataclass, not a JSON-contract object. Keep
        # its complete read set and detail identities in the durable observation.
        # This receipt proves a fresh reducer read; it does not clear arbitrary
        # validity_dirty entries or grant an evidence/support revalidation.
        reports = [_document(view.reports[key]) for key in sorted(view.reports)]
        return {"revision": int(view.network.plan_revision),
                "readiness_hash": sha256_hex(reports),
                "input_witness_ids": sorted(item.witness_id for item in inputs),
                "start_witness_ids": sorted(item.witness_id for item in starts),
                "pending_validity": [_document(item) for state in ("PENDING", "RECHECKING")
                    for item in dispatch.semantics().list_dirty(mission_id, state=state)],
                "evaluated_occurrence_ids": [str(key) for key in sorted(view.reports)],
                "phases": {str(key): str(value) for key, value in sorted(phases.items())}}

    async def taskgraph_request_composition(self, mission_id: str,
                                           occurrence_id: Any) -> dict[str, Any]:
        from ..contracts.htn import TaskForm
        from .hierarchical_dispatch import CompoundPhase, next_compound_phase
        from .root_review import RootReviewStatus
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise StoreError("TASKGRAPH_MISSION_UNAVAILABLE")
        if mission.status in TERMINAL_MISSION:
            return {"status": "MISSION_TERMINAL", "mission_status": str(mission.status)}
        dispatch = self._new_mode(mission)
        if dispatch is None:
            raise StoreError("TASKGRAPH_HIERARCHICAL_ASSEMBLY_REQUIRED")
        with self.store.read_view():
            view = dispatch.read(mission_id)
            specs = [item for item in view.network.occurrences if item.occurrence_id == occurrence_id]
            if not specs:
                return {"status": "SUPERSEDED", "revision": int(view.network.plan_revision)}
            spec = specs[0]
            if spec.form is not TaskForm.COMPOUND:
                raise StoreError("TASKGRAPH_COMPOSITION_SUBJECT_INVALID")
            phase = next_compound_phase(spec, view.network, view.reports[occurrence_id],
                child_outcomes={child.occurrence_id: view.outcomes.get(child.occurrence_id)
                                for child in view.network.adopted_children(occurrence_id)},
                resolved=occurrence_id in view.resolved)
        if phase is not CompoundPhase.COMPOSITION_REVIEW:
            return {"status": "NOT_READY", "phase": str(phase), "revision": int(view.network.plan_revision)}
        if occurrence_id in view.network.root_occurrence_ids:
            await self._advance_root_review(mission, dispatch)
            state = self._root_review(mission, dispatch).state(mission_id)
            if state.status is RootReviewStatus.UNREADABLE_PLAN:
                raise StoreError("TASKGRAPH_COMPOSITION_SOURCE_UNAVAILABLE")
            return {"status": str(state.status),
                    "package_id": None if state.package is None else str(state.package.package_id)}
        receipt = self._composition_assembly(mission, dispatch).resolve_one(mission_id, occurrence_id)
        return {"status": "NOT_READY" if receipt is None else "RESOLVED",
                "resolution_id": None if receipt is None else receipt.resolution_id}

    def _assembly_missing(self, mission: Mission, *, at: str) -> bool:
        """Fail-closed: with no assembly installed a Mission is not scheduled at all.

        Review finding F1: :data:`~.hierarchical_dispatch.ASSEMBLY_MISSING` is recorded
        once for the Mission and its callers do nothing rather than fall back.  There
        is no auto-assembly to prefer over it: ``build_planning_world`` needs the
        deployment's own facts (which domains, which worktree, which layers are
        deployed, which observers) and an Orchestrator that guessed them would be
        inventing the declarations the plan is admitted against.
        """

        if self._hierarchical is not None:
            return False
        record_assembly_missing(self.store, mission, at=at)
        self._note(
            f"mission {mission.id} runs under hierarchical semantics and this deployment "
            f"installed no assembly; nothing is dispatched or judged at {at} "
            "(§18.5 rule 1 — call install_hierarchical())"
        )
        return True

    def _refuse_unsupported_contract(self, mission: Mission) -> bool:
        """Stop a Mission built under a contract this build dropped.

        2026-10-01 / 10-02: one orchestration mode, one planning protocol, one package
        version, no old-data compatibility.  A flat-mode Mission left in the library, a
        hierarchical one with no protocol binding (created under the removed
        proposal-text protocol) or with a binding to another package is ended here, by
        name, before anything is planned, dispatched or judged for it — never served on
        a fallback path and never allowed to take the loop down.
        """

        if mission.id in self._contract_checked:
            return False
        if not is_hierarchical(mission):
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_orchestration_semantics",
                detail={"error": "the flat orchestration mode was removed on 2026-10-02"},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: flat orchestration mode removed → stopped")
            return True
        try:
            # 删旧平面模式第三刀第 4 步: a domain snapshot frozen under another profile
            # schema is not read through a compatibility path.
            self.commit.domain_for(mission.id)
        except CommitRejected as error:
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_domain_profile",
                detail={"error": str(error)[:300]},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: {error} → stopped")
            return True
        from ..storage.taskgraph_store import NotBoundError, require_bound
        from .assurance_final_writer import is_assured
        try:
            require_bound(self.store, mission.id)
            bound = True
        except NotBoundError:
            bound = False
        if not bound or not is_assured(self.store, mission.id):
            # 2026-10-03 (A′ release review): a Mission of a development library created
            # before every Mission was TaskGraph-bound and assured at creation is ended
            # here, by name, never served nor allowed to stop the loop for the others.
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_unbound_mission",
                detail={"error": "created before every Mission was TaskGraph-bound and assured"},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: not TaskGraph-bound/assured at creation → stopped")
            return True
        from .planning_backend_runtime import frozen_deployment_conflict
        from .planning_protocol_binding import current_planning_protocol

        try:
            current_planning_protocol(self.store, mission.id)
            # The same goes for the planning deployment the Mission froze at its first
            # round: an identity this process does not produce is not replanned.
            conflict = frozen_deployment_conflict(self, mission.id)
            if conflict is not None:
                raise UnsupportedPlanningPackage(conflict)
        except UnsupportedPlanningPackage as error:
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_planning_package",
                detail={"error": str(error)[:300]},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: {error} → stopped")
            return True
        self._contract_checked.add(mission.id)
        return False

    async def _plan_integrity_stop(self, mission: Mission, error: Exception) -> None:
        """Stop *this* Mission for a damaged plan and leave the run alone (§24.1 dec. 11).

        ``GraphIntegrityError`` is a ``RuntimeError``, and ``_cycle`` only forgives
        ``StoreBusy`` / ``CommitRejected`` / ``IllegalTransition`` — so an unguarded
        one would end ``run()`` and take every *other* Mission in this process down
        with it.  One Mission's corruption is one Mission's stop: the diagnosis is
        recorded, the Mission fails with its open work cascaded, and the loop carries
        on with the rest.
        """

        if isinstance(error, GraphIntegrityError):
            if self._hierarchical is not None:
                dispatch = self._dispatch_for(mission.id)
                if dispatch is not None:
                    dispatch.record_integrity_failure(mission.id, error)
            detail = {
                "code": str(error.code),
                "subjects": sorted(str(item) for item in error.remaining),
                "cycle": [str(item) for item in error.cycle],
                "diagnose": error.diagnose()[:600],
            }
        else:  # a stored record that fails its own integrity check (history, inputs, sources)
            detail = {"code": str(error.code) if isinstance(error, CodedFault) else type(error).__name__,
                      "subjects": [], "cycle": [], "diagnose": str(error)[:600]}
        current = self.store.get_mission(mission.id)
        status = mission.status if current is None else current.status
        if status is MissionStatus.CREATED:
            # Damaged before its first planning round: stopped the same way, by name
            # (TaskGraph 补全第二批; the contract gate does the same for an unbound one).
            self.commit.begin_planning(mission.id)
            status = MissionStatus.PLANNING
        if status is MissionStatus.PLANNING:
            self._commit_fail_planning(mission.id, reason="plan_integrity", detail=detail)
        elif status is MissionStatus.ACTIVE:
            self._commit_fail_mission(
                mission.id, stop_reason=MissionStopReason.PLANNING_FAILED, detail=detail
            )
        else:  # already terminal, or not yet planning: the record is the whole answer
            self._note(f"mission {mission.id}: plan integrity failure while {status!s}")
            return
        await self._release_mission(mission.id)
        self._note(f"mission {mission.id} stopped: plan integrity ({detail['code']})")

    @property
    def connectors(self) -> Mapping[str, Any]:
        """The connectors this deployment enabled (P3.2 D8: a compensation is proposed
        against the same connector the original action ran on)."""

        return dict(self._connectors)

    @property
    def bridge(self) -> AgentBridge:
        """The default pool's bridge (single-profile callers and tests)."""

        assert self._bridge is not None
        return self._bridge

    @property
    def model_router(self) -> ModelRouter:
        return self._model_router

    def _expected_model(self, intent: DispatchIntent) -> str:
        """The model frozen in the intent (review P0-3): the echo is checked against what
        was routed at dispatch time, never against the current configuration."""

        frozen = intent.config.get("model")
        if frozen:
            return str(frozen)
        profile = self._profiles.get(self.profile_of(intent))
        return profile.model if profile is not None else self._config.model

    def _route_service(self, role: str, mission_id: str) -> RoutingDecision:
        """Planner / Critic routing (D6-4'): by role, with the profile health
        applied; a cooling-down profile without fallback fails the caller fast."""

        return self._router_for(mission_id).route(
            role=role,
            task_kind=None,
            previous_attempts=(),
            unavailable_until=self.commit.unavailable_until(),
            now=self.store.now,
        )

    def _admission_for(self, profile_id: str) -> ProviderBudgetGuard | None:
        if self._provider_admissions is not None:
            return self._provider_admissions[profile_id]
        return self._provider_admission

    def _frozen_admission_fingerprints(self, profile_id: str) -> set[str]:
        """Admission identities persisted in this pool's dispatch intents."""
        if not self.store.has_table("dispatch_intents"):
            return set()
        found: set[str] = set()
        for (encoded,) in self.store.connection.execute("SELECT config_json FROM dispatch_intents"):
            try:
                frozen = json.loads(encoded)
            except (TypeError, ValueError):
                continue
            if str(frozen.get("runtime_profile_id") or DEFAULT_PROFILE) == profile_id:
                value = frozen.get("provider_admission_fingerprint")
                if isinstance(value, str):
                    found.add(value)
        return found

    def _prepare_terminal_ledger(self, mission_id: str) -> None:
        """Withdraw future execution and import what is known; keep every
        physical/accounting obligation (P2.3r / N9).  An unknown call is never
        settled as known-only zero usage."""

        mission = self.store.get_mission(mission_id)
        if mission is None:
            return
        from ..storage.taskgraph_store import NotBoundError, require_bound
        try:
            require_bound(self.store, mission_id)
        except NotBoundError:
            # An unbound Mission of an older development library (stopped by name by the
            # contract gate): its runtime history is not read through the TaskGraph, so
            # its reservations stay held — never settled as known zero usage.
            for intent in self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"):
                if intent.mission_id == mission_id:
                    self._settle_intent(intent, "FAILED")
            return
        from .taskgraph_runtime_imports import TaskGraphRuntimeImports
        from ..runtime.planning_operations import SourceUnavailable
        reader = TaskGraphRuntimeImports(self)
        for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "FAILED", "SETTLED"):
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
        self._after_handoff_zero_streak[mission_id] = 0


    @staticmethod
    def _assured_review_intent(intent: DispatchIntent) -> bool:
        """Assured reviews of every purpose (kind ``critic`` or ``plan``) bind the
        read-only evidence tools through ``_bind_critic``; nothing else does."""
        return intent.config.get("assurance_protocol") == "assurance-exec-v1.1"


    def _service_config(self, decision: RoutingDecision) -> dict[str, Any]:
        config: dict[str, Any] = {
            "runtime_profile_id": decision.profile_id,
            "model": decision.model,
            "routing": decision.to_json(),
        }
        snapshot = self._profiles[decision.profile_id].context_snapshot()
        if snapshot is not None:
            config["runtime_context"] = snapshot
        admission = self._admission_for(decision.profile_id)
        if admission is not None:
            config["provider_admission_fingerprint"] = admission.fingerprint
        return config

    def _first_critic_budget(
        self, decision: RoutingDecision
    ) -> FirstRequestBudget | FirstRequestBudgetUnknown:
        profile = self._profiles[decision.profile_id]
        guard = self._admission_for(decision.profile_id)
        cap = frozen_provider_input_cap(
            profile_id=decision.profile_id,
            model=decision.model,
            runtime_context=profile.context_snapshot(),
            estimator_fingerprint=(None if guard is None else guard.estimator.fingerprint),
        )
        return first_request_budget(
            provider_input_cap=cap,
            output_ceiling=actual_output_ceiling(
                profile_default_max_output_tokens=profile.default_max_output_tokens,
                profile_max_output_tokens_ceiling=profile.max_output_tokens_ceiling,
                config_default_max_output_tokens=self._config.default_max_output_tokens,
                config_max_output_tokens_ceiling=self._config.max_output_tokens_ceiling,
            ),
            guard_input_cap_protocol=(
                None if guard is None else getattr(guard, "input_cap_protocol", None)
            ),
        )

    def _context_profile_for(self, config: Mapping[str, Any]) -> RuntimeProfile:
        profile_id = str(config.get("runtime_profile_id") or DEFAULT_PROFILE)
        profile = self.assembled.pool(profile_id).profile
        if config.get("runtime_context") != profile.context_snapshot():
            raise ContractError("context identity differs from the frozen dispatch intent")
        from ..runtime.assembly import admission_accepts

        if not admission_accepts(self._admission_for(profile_id), config.get("provider_admission_fingerprint")):
            raise ContractError(
                "provider admission identity differs from the frozen dispatch intent"
            )
        return profile

    def _note_turn_health(self, intent: DispatchIntent, result) -> str:  # type: ignore[no-untyped-def]
        """D6-5' / review P1-8: a committed turn closes the profile's failure streak; a
        provider-unavailable failure counts towards its cooldown.  Returns the error class."""

        profile_id = self.profile_of(intent)
        if result.state is AgentTurnState.COMMITTED:
            self.commit.record_profile_success(profile_id)
            return "ok"
        kind = classify_turn_error(result.error)
        if kind == "provider_unavailable":
            until = self.commit.record_profile_failure(
                profile_id,
                error=result.error,
                threshold=self._config.profile_failure_threshold,
                cooldown_seconds=self._config.profile_cooldown_seconds,
                mission_ids=[m.id for m in self._active_missions()],
            )
            if until is not None:
                self._note(f"runtime profile {profile_id!r} unavailable until {until:.3f}")
        return kind

    def profile_of(self, intent: DispatchIntent) -> str:
        return str(intent.config.get("runtime_profile_id") or DEFAULT_PROFILE)

    def bridge_for(self, intent: DispatchIntent) -> AgentBridge:
        """The pool an intent is bound to (D6-5'): an intent bound to a profile this
        process does not run raises — it is never handed to another model."""

        return self.assembled.pool(self.profile_of(intent)).bridge

    def _pool_missing(self, intent: DispatchIntent) -> bool:
        """S6-08: an intent whose profile is not configured here stays untouched (its
        lease lapses like any dead executor's); recorded once per intent."""

        profile_id = self.profile_of(intent)
        if profile_id in self.assembled.pools:
            return False
        key = f"{intent.intent_id}:not_configured"
        if key not in self._released:
            self._released.add(key)
            self.commit.record_profile_unavailable(
                profile_id,
                reason="not_configured",
                until=None,
                mission_ids=[intent.mission_id],
                detail={"intent_id": intent.intent_id, "subject_id": intent.subject_id},
            )
            self._note(
                f"{intent.subject_id}: bound to profile {profile_id!r} which is not configured here"
            )
        return True

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
        self.progress_log.append(text)
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

        from ..api.missions import MissionRequestError, spec_from_request, validate_spec

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
        from ..api.missions import MissionRequestError, spec_from_request, validate_spec

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
        if {name for name, tool in self._config.domain_tools.items() if tool.read_only} != set(self._config.deployment_policy.domain_read_only_tools):
            raise ContractError("read-only domain tool metadata must match deployment policy")
        if spec.domain == "appworld-v1" and self._config.appworld_execute is None:
            raise ContractError("AppWorld Mission requires a bound episode environment")
        if spec.runtime_profile_id is not None:
            from ..api.missions import MissionRequestError

            if (
                not isinstance(spec.runtime_profile_id, str)
                or not spec.runtime_profile_id.strip()
                or spec.runtime_profile_id not in self._profiles
            ):
                raise MissionRequestError(
                    f"runtime profile {spec.runtime_profile_id!r} is not configured"
                )
        self.check_requirement_statements(spec.success_criteria)
        self._check_source_publish_roots(spec)

    def check_requirement_statements(self, statements: Sequence[str]) -> None:
        """What this deployment can do with these requirement statements: an ``action:`` one
        must be well-formed and name an operation the deployment would run; a ``pytest:`` one
        needs local code execution.  One door for creating a Mission and for amending its
        requirements (阶段 E)."""
        self._check_action_criteria(statements)
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
        from ..governance.domains import resolve_domain

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

    def _check_action_criteria(self, criteria: Sequence[str]) -> None:
        """D7-3' / review P2-10: an action criterion must name an enabled connector and an
        operation this deployment would run; otherwise the Mission is refused up front."""

        for criterion in criteria:
            parsed = parse_action_criterion(criterion)  # malformed → ContractError
            if parsed is None:
                continue
            name, operation, _target = parsed
            decision = action_decision(
                self._config.deployment_policy, self._connectors.get(name), operation
            )
            if decision.refused is not None:
                raise ContractError(f"action criterion {criterion!r} refused: {decision.refused}")

    def _domain_unreadable(self, mission_id: str) -> bool:
        """A Mission this build does not serve: a frozen domain it cannot read (another
        profile schema), or one created before every Mission was TaskGraph-bound and
        assured at creation (A′ release review 2026-10-03).

        Startup binding and recovery read the frozen domain and the TaskGraph; such a
        Mission is left unbound here and stopped by name by the loop's contract gate in
        its first round, instead of taking ``__aenter__`` / ``recover`` down for every
        Mission.
        """
        from ..storage.taskgraph_store import NotBoundError, require_bound
        from .assurance_final_writer import is_assured
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
                self._startup_faults.append((intent.mission_id, f"startup_bind:{intent.intent_id}", error))

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
                    raise ServiceTurnIdentityMismatch("SERVICE_TURN_IDENTITY_MISMATCH: Critic SDK turn differs from frozen intent")
            self._bind_critic(intent.agent_id, intent.config)

    async def recover(self) -> None:
        """重启恢复协议（原计划 §25.1 第 11 条、§16.4；车道 J H01）：RECOVERY_LOCKED → 清单核对 →
        reducer 重建 → inbox/outbox → 未决核对 → fence 收敛 → 孤儿回收 → READY / DEGRADED_RECOVERY。
        顺序与每步的事实在 :mod:`.recovery_coordinator`；这里只建协调者、跑一遍。READY 之前不派发、
        不交接、不发通知、不唤醒执行池；降级后本进程只开只读与诊断（``run()`` 不进周期）。"""

        self._require_assurance_execution_root()
        if self._recovery is None:
            self._recovery = RecoveryCoordinator(self)
        await self._recovery.run()

    def recovery_status(self) -> dict[str, Any]:
        """只读诊断：恢复状态（RECOVERY_LOCKED / READY / DEGRADED_RECOVERY）、是否禁副作用、
        库里最近一次恢复的八步结果。还没恢复过时 ``state`` 为 ``RECOVERY_LOCKED``、``latest`` 是库里的记录。"""

        if self._recovery is not None:
            return self._recovery.status()
        from ..storage.recovery_store import RecoveryStore

        return {"state": "RECOVERY_LOCKED", "side_effects_disabled": True, "recovery_id": None,
                "latest": RecoveryStore(self.store).latest()}

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
                # 推后第 1 批 A26（AER §12.2）：冻结的请求不静默替换上下文。恢复前按它开工时装进
                # 上下文的证据重签 RECOVERY；签不出就不恢复：按"被打断"记丢失（不扣次数），失败明细
                # 写明哪一项、为什么；这一步下一轮拿当前上下文重做。
                from .assurance_point_use import certify_recovery_locked

                with self.store.transaction():
                    use = certify_recovery_locked(self.commit, attempt)
                if not use.usable:
                    self.commit.mark_attempt_lost(attempt.id, reason="recovery_use_refused",
                                                  detail={"refusals": list(use.refusals)})
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
        self._unrecovered.discard(mission.id)  # the boundary must not skip the recovery itself
        await self._mission_round(mission.id, "recover", lambda: self._recover_mission(mission))
        current = self.store.get_mission(mission.id)
        if (mission.id, "recover") in self._round_faults and current is not None \
                and current.status not in TERMINAL_MISSION:
            self._unrecovered.add(mission.id)

    async def _reconcile_actions(self) -> list[dict[str, Any]]:
        """Reconcile every live action — with the operation runtime bound first when this
        deployment has a publisher, so the registered reconciler answers even in a fresh
        process (阶段 B 裁决第 1、3 类); without one, operation-linked actions stay as they are.

        Each action is reconciled behind its Mission's round boundary (2026-10-03 收尾裁决
        第 3 张): a store fault on one action is that Mission's, and the others go on."""
        from .operation_runtime import ensure_operation_runtime

        if self.connectors:
            try:
                ensure_operation_runtime(self)
            except ContractError:
                pass  # no trusted publisher registered here
        settled: list[dict[str, Any]] = []
        live = [(action, True) for action in self.store.list_actions(None, "UNKNOWN", "HANDED_OFF")]
        # 阶段 B 裁决第 1 类: a failed action that left our hands has no proof yet that it
        # did not happen; the registered reconciler is the one that decides that too.
        live += [(action, False) for action in self.store.list_actions(None, "FAILED")
                 if self.actions._failed_unproven(action)]
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
        from ..runtime.operation_reconciliation import action_outcome_unresolved
        from .assurance_final_writer import request_assured_notification

        rows = self.store.connection.execute(
            "SELECT mission_id FROM missions WHERE status IN ('CANCELLED','FAILED')"
            " AND json_extract(json, '$.final_report.unresolved_actions') IS NOT NULL").fetchall()
        for (mission_id,) in rows:
            if self.recovery_isolated(str(mission_id)):
                continue
            # one Mission's fault is that Mission's; the scan goes on (阻断核验 2026-10-04)
            with self._round_boundary(str(mission_id), "notice:settled-actions"):
                mission = self.store.get_mission(str(mission_id))
                for listed in (mission.final_report or {}).get("unresolved_actions") or ():
                    key = str(listed.get("action_key") or "")
                    action = self.store.get_action(key)
                    if action is None or action_outcome_unresolved(self.store, action):
                        continue
                    state = str(action["state"])
                    if self.store.connection.execute(
                            "SELECT 1 FROM events WHERE idempotency_key=?",
                            (f"{ACTION_SETTLED_AFTER_STOP}:{key}:{state}",)).fetchone() is not None:
                        continue  # already said
                    with self.store.transaction():
                        event = self.commit._emit(
                            ACTION_SETTLED_AFTER_STOP, mission.id, key=f"{key}:{state}",
                            task_id=action.get("task_id"),
                            payload={"action_key": key, "action_id": str(action.get("action_id") or ""),
                                     "operation": str(action.get("operation") or ""),
                                     "target": str(action.get("target") or ""), "state": state,
                                     "applied": state == "SUCCEEDED"})
                        request_assured_notification(self.commit, mission.id, event, state_version=mission.version)

    async def run(self, *, max_cycles: int = 10_000, until_idle: bool = True) -> None:
        """Drive the loop until idle.  ``max_cycles`` bounds *progressing* cycles (work
        done), never the waiting: a slow real model turn may keep the loop polling for
        many minutes and must not end the run early (step 4 real-run finding)."""

        await self.recover()
        if self._recovery is not None and self._recovery.degraded:
            return  # DEGRADED_RECOVERY：只开只读与诊断（``recovery_status()``），不进周期
        await self._reconcile_actions()  # D7-5': every run() first asks about UNKNOWN actions
        cycles = 0
        idle_rounds = 0
        # P1-A's carry-on budget is per ``run()``: a fresh execution cycle is allowed
        # to give a Mission the same benefit of the doubt the last one did.
        self._stall_carry_ons.clear()
        mark = self._durable_watermark()
        hollow = 0
        # A wait that writes nothing backs off from ``_poll`` up to WAIT_BACKOFF_MAX:
        # every cycle re-reads plans, accounting and admissions, and polling that
        # every 50 ms held a core at 100% for the whole of a model turn (real run
        # 2026-09-28). Real progress resets it.
        backoff = self._poll
        while cycles < max_cycles:
            await self._run_between_cycles()
            progressed = await self._cycle()
            if progressed:
                cycles += 1
                idle_rounds = 0
                moved = self._durable_watermark()
                if moved == mark:
                    # NEXT-TG-1.0 §3.6: a cycle that claims progress and left no
                    # durable change (only observation events, only row versions)
                    # is not allowed to skip the sleep — that is the busy loop that
                    # held the CPU with nothing written. It still counts toward
                    # ``max_cycles``, so the run keeps its old bound and verdicts.
                    hollow += 1
                    if hollow == HOLLOW_CYCLES_NOTED:
                        self._note(f"{hollow} cycles in a row claimed progress with no durable change")
                        logger.warning("orchestrator.hollow_progress cycles=%s", hollow)
                    await asyncio.sleep(backoff)
                    backoff = min(max(self._poll, backoff * 2), WAIT_BACKOFF_MAX)
                    continue
                mark, hollow, backoff = moved, 0, self._poll
                if cycles % RECONCILE_EVERY_CYCLES == 0:
                    await self._reconcile_actions()
                # P2.3e: a progressing cycle does not sleep, and the in-process bridge
                # answers without suspending — so a run of progressing cycles used to
                # starve the runtime's own turn tasks (H-L3-C1: two turns submitted at
                # t+0 whose preflight ran at t+28.5 s, the instant this loop finally
                # returned).  One yield per cycle costs nothing and keeps the loop's
                # progress from being the only thing that is allowed to happen.
                await asyncio.sleep(0)
                continue
            if not until_idle:
                # P2.3c part 2d, P2-17: a caller that drives the loop one step at a
                # time gets the same *record* an idle ``run(until_idle=True)`` gets.
                # Not the same *decision*: ``_confirm_and_stop_stalled`` spends a
                # whole confirming cycle and then fails the Mission, and a stepping
                # caller has not asked this loop to decide anything — it asked it to
                # take one step and hand control back.  The record is free; the
                # verdict is not the stepper's to make (§9.1: the repetition is what
                # licenses the stop, and one step is not a repetition).
                await self._record_hierarchical_stall()
                return
            if self._has_inflight():
                idle_rounds = 0
                moved = self._durable_watermark()
                if moved != mark:
                    mark, backoff = moved, self._poll
                await asyncio.sleep(backoff)
                backoff = min(max(self._poll, backoff * 2), WAIT_BACKOFF_MAX)
                continue
            idle_rounds += 1
            if idle_rounds == 1:
                # 第 4 批（审阅 2026-09-29）："空闲"只能由一次全量处理得出：清掉安静标记，
                # 下一轮每个任务都看一遍，仍无进展才算空闲返回。
                self._mission_marks.clear()
            if idle_rounds >= 2:
                # review P2-6: one more look at hand-offs whose lease lapsed (a crashed owner)
                settled = await self._reconcile_actions()
                if any(a["state"] != "UNKNOWN" for a in settled):
                    idle_rounds = 0
                    continue
                await self._record_hierarchical_stall()
                if await self._confirm_and_stop_stalled():
                    # Third-round review P1-A.  The confirmation cycle moved the world,
                    # and the work it unblocked is this loop's to dispatch — returning
                    # here would hand the caller an "idle" run with a ready occurrence
                    # and no Attempt.  Bounded by ``MAX_STALL_CARRY_ONS`` inside the
                    # confirmation itself, so a world that keeps changing under the
                    # gates ends the run rather than spinning in it.
                    idle_rounds = 0
                    continue
                return
            await asyncio.sleep(self._poll)
        # P2.3c part 2d, P2-17: ``max_cycles`` progressing cycles were spent and the
        # loop is leaving with work still on the plan.  The same record as the idle
        # path, for the same reason: a Mission left ACTIVE with nothing written down
        # is the one ending the smoke test refuses.  No stop here either — running
        # out of cycles is the caller's bound, not a statement about the Mission.
        await self._record_hierarchical_stall()

    def durable_watermark(self) -> tuple[Any, ...]:
        """Public: what a real step of progress leaves behind (see ``_durable_watermark``).

        An embedding host compares it across its own rounds to tell a quiet library
        from a busy one without counting the loop's observation-only events.
        """

        return self._durable_watermark()

    def _durable_watermark(self) -> tuple[Any, ...]:
        """What a real step of progress leaves behind in the store.

        Observation-only events (heartbeats, periodic closeout and validity checks,
        convergence wake-ups, stall records) are not progress; a new business event,
        an intent / Attempt / result row change is.
        """

        connection = self.store.connection
        try:
            recent = connection.execute(
                "SELECT seq, type FROM events ORDER BY seq DESC LIMIT 64"
            ).fetchall()
            seq = next((int(row[0]) for row in recent if row[1] not in OBSERVATION_EVENTS), None)
            if seq is None and recent:
                marks = ",".join("?" * len(OBSERVATION_EVENTS))
                row = connection.execute(
                    f"SELECT MAX(seq) FROM events WHERE type NOT IN ({marks})",  # noqa: S608
                    tuple(sorted(OBSERVATION_EVENTS)),
                ).fetchone()
                seq = row[0]
            rows = tuple(
                tuple(connection.execute(sql).fetchone())
                for sql in (
                    "SELECT COUNT(*), MAX(updated_at) FROM dispatch_intents",
                    "SELECT COUNT(*), MAX(updated_at) FROM attempts",
                    "SELECT COUNT(*), MAX(updated_at) FROM results",
                )
            )
        except sqlite3.Error:
            return (object(),)  # unreadable: never equal, never suppresses progress
        return (seq, *rows)

    def _has_pending_assurance_work(self, mission_id: str) -> bool:
        """Assurance work still queued for this Mission is progress in waiting.

        2026-09-26 Host run: all six steps accepted, the root's final review answered
        with malformed JSON, and its one format repair sat in the REVIEW queue.  The
        stall check ran in the same idle cycle, before the Assurance tick took that
        work, and failed the Mission as NO_DISPATCHABLE_WORK.  Work that ran out of
        rechecks and waits for a person (MANUAL_REQUIRED) is a real stall and does
        not count.

        The same run's sixth try: the final review's reply was classified, and the
        stall check ran before the Assurance consumers had even read that event, so
        no work row existed yet.  An event a consumer has not read is queued work too.
        """

        try:
            row = self.store.connection.execute(
                "SELECT 1 FROM assurance_pending_work WHERE mission_id=? "
                "AND state NOT IN ('DONE','REJECTED') "
                "AND (wait_reason IS NULL OR wait_reason<>'MANUAL_REQUIRED') LIMIT 1",
                (mission_id,),
            ).fetchone()
            if row is None:
                row = self.store.connection.execute(
                    # Only the REVIEW consumer and only the events it turns into work:
                    # periodic Assurance events (closeout evaluations) must not keep a
                    # truly stalled Mission alive forever.
                    "SELECT 1 FROM assurance_event_cursors c WHERE c.mission_id=? "
                    "AND c.consumer='REVIEW' AND EXISTS("
                    "SELECT 1 FROM events e WHERE e.mission_id=c.mission_id "
                    "AND e.seq>c.last_event_seq AND e.type IN "
                    "('AssuranceReviewClassified','AssuranceReviewFormatRejected')) LIMIT 1",
                    (mission_id,),
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def _preview_read_facts(self, mission_id: str) -> dict[str, Any]:
        """What a plan proposal reads that the plan-revision gate does not cover, as it
        stands when the preview inputs are frozen (阶段 D): the scope epochs — the
        Assurance lane's own ``assurance:`` scopes are not plan facts — and every duty of
        the Mission and the newest observation of every recorded proposition, each read with
        the commit checker's own formula."""
        from ..contracts.htn import ReadItemKind
        from ..storage.htn_store import HtnStore
        from ..storage.obligation_store import ObligationStore
        from ._read_set import SemanticReadSetChecker
        from .taskgraph_epochs import current_scope_epochs

        checker = SemanticReadSetChecker(self.store, HtnStore(self.store), mission_id=mission_id)
        duties = ObligationStore(self.store).obligation_ids(mission_id)
        mission = self.store.get_mission(mission_id)
        return {
            "scope_epochs": tuple(sorted(
                (scope, int(epoch)) for scope, epoch in current_scope_epochs(self.store, mission_id).items()
                if not scope.startswith("assurance:"))),
            "obligation_items": tuple(
                (str(duty), checker.read_item(ReadItemKind.OBLIGATION, str(duty))) for duty in sorted(map(str, duties))),
            "observation_items": tuple(
                (key, checker.read_item(ReadItemKind.FACT, str(record.observation_id)))
                for key, record in sorted({
                    str(item.proposition_key): item
                    for item in HtnStore(self.store).list_observations(mission_id)}.items())),
            # 现行要求里的 ``file:X``：要求编号 → 文件
            "criterion_files": tuple(
                (name, statement.strip()[len("file:"):].strip())
                for name, statement in (() if mission is None else current_criteria(self.store, mission))
                if statement.strip().startswith("file:") and statement.strip()[len("file:"):].strip()),
        }

    def _handoff_ground_gone(self, action_key: str) -> bool:
        from ..contracts.error_table import HANDOFF_VALIDITY_STALE

        actions = getattr(self, "_actions", None)
        return actions is not None and str(actions.last_refusal.get(action_key, "")).startswith(
            HANDOFF_VALIDITY_STALE)

    def _handoff_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: the operations whose hand-off is refused because the step
        they belong to no longer stands on current ground, with the refusal as given."""
        actions = getattr(self, "_actions", None)
        rows = [{"action_key": str(a["action_key"]), "task_id": a.get("task_id"),
                 "reason": actions.last_refusal.get(str(a["action_key"]), "")}
                for a in (self.store.list_actions(mission_id) if actions is not None else ())
                if self._handoff_ground_gone(str(a["action_key"]))]
        return {"handoff_refused": rows} if rows else {}

    def _materialization_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: reviewed operation requests whose materialisation is refused
        (every round the same refusal) — an effect waiting on one of these will never move."""
        from ..storage.operation_intent_store import OperationIntentStore

        intents = OperationIntentStore(self.store).for_mission(mission_id)
        replaced = {item["supersedes_intent_id"] for item in intents if item["supersedes_intent_id"]}
        heads = {item["intent_id"]: item for item in intents
                 if item["intent_id"] not in replaced
                 and self.store.get_receipt("materialize:" + item["intent_id"]) is None}
        rows = [{"intent_id": str(e.payload.get("intent_id")), "reason": str(e.payload.get("reason")),
                 "effect_key": heads[e.payload["intent_id"]]["binding"].get("completion", {}).get("effect_key")}
                for e in self.store.list_events(mission_id)
                if e.type == "OperationMaterializationDeferred" and e.payload.get("intent_id") in heads] if heads else []
        return {"materialization_refused": rows} if rows else {}

    def _outcome_source_refusals(self, mission_id: str) -> dict[str, Any]:
        """For the stall record: executed operations whose result review cannot be prepared because
        its source is missing for good (a SUCCEEDED action without a receipt, a ruling receipt nobody
        recorded, a broken T1 link) — refused the same way every round; the effect waiting on it
        will never move (2026-10-06 第 1 批车道 E)."""
        from ..storage.operation_completion_store import OperationCompletionStore
        from ..storage.operation_intent_store import OperationIntentStore

        intents = OperationIntentStore(self.store).for_mission(mission_id)
        replaced = {item["supersedes_intent_id"] for item in intents if item["supersedes_intent_id"]}
        completion = OperationCompletionStore(self.store)
        heads: dict[str, Any] = {}
        for item in intents:
            if item["intent_id"] in replaced:
                continue
            materialized = self.store.get_receipt("materialize:" + item["intent_id"])
            action = None if materialized is None else self.store.get_action(materialized["action_key"])
            if action is None or action["state"] != "SUCCEEDED":
                continue
            if any(completion.get_acceptance_scope_exact(mission_id, "acc-" + row["binding_id"])
                   for row in completion.list_outcome_bindings_for_intent(mission_id, item["intent_id"])):
                continue  # its result was accepted; nothing is waiting on it
            heads[item["intent_id"]] = item
        rows = [{"intent_id": str(e.payload.get("intent_id")), "reason": str(e.payload.get("reason")),
                 "effect_key": heads[e.payload["intent_id"]]["binding"].get("completion", {}).get("effect_key")}
                for e in self.store.list_events(mission_id)
                if e.type == "OperationOutcomeDeferred" and e.payload.get("intent_id") in heads
                and e.payload.get("reason") == "OP_OUTCOME_SOURCE_UNAVAILABLE"] if heads else []
        return {"outcome_source_unavailable": rows} if rows else {}

    def _operation_dead_end(self, mission: Mission) -> bool:
        """An effect of this Mission that the operation path has named as never converging.

        Each of these is a named finding, not a wait: it does not get better on its own, so
        it is handed to the stall check (which tells the planner once and then stops by
        name).  Read by :meth:`_has_pending_operation_completion` and, since the judgment no
        longer waits for the effects (Assurance §7.2, 2026-10-06 车道 O), by the idle facts:
        a judged Mission waits for its closeout only while the closeout can still converge.
        """
        from .operation_outcomes import outcome_exhaustion_is_final

        if self._materialization_refusals(mission.id):
            return True  # 2026-10-05：物化一直被拒的申请单不是合法等待，交给卡死检测
        if self._outcome_source_refusals(mission.id):
            return True  # 2026-10-06：成功了却读不出回执的操作，结果审阅永远准备不出来，同样交给卡死检测
        if any(outcome_exhaustion_is_final(self.store, mission.id, item["review_key"])
               for item in self._exhausted_reviews(mission.id, "assurance-operation-outcome:")):
            # 2026-09-29 真机第七局：一份发布的结果审阅的调用两次都没回来，这项效果永远核不完；
            # 再把它当合法等待，任务就一直挂着。交给卡死检测明确停下。被重启打断而用完的
            # 还有一次重审（outcome_retake_due），重审没用完前仍是合法等待。
            return True
        if self._handoff_refusals(mission.id):
            # 这项效果的交接因为所属步骤的地基没了而一直被拒：同样不会自己好，不当合法等待。
            return True
        if any(item["kind"] == "outcome" and item["ruling"] in {"stale", "fail"}
               for item in self._inconclusive_reviews(mission.id)):
            # 阶段 C 第 3 条：结果审查判不下来，人打回了或裁决题在回答前过期——不会再有放行，
            # 同样交给卡死确认（如实告诉规划器），不当合法等待。裁决题待答仍是合法等待。
            return True
        return False

    def _has_pending_operation_completion(self, mission: Mission) -> bool:
        """Accepted preparation with real unmet effects is work, not an idle failure."""
        if self._operation_dead_end(mission):
            return False
        from ..storage.htn_store import HtnStore
        from .completion_status import read_occurrence_completion
        try:
            htn = HtnStore(self.store)
            active = htn.active_plan_revision(mission.id)
            if active is None:
                return False
            from ..contracts.htn import TaskForm
            from ..graph.task_network import GATING_REQUIREDNESS

            dispatch = self._new_mode(mission)
            if dispatch is None:
                return False
            view = dispatch.read(mission.id)
            if view.plan.integrity_error is not None:
                return False
            statuses = {spec.occurrence_id: read_occurrence_completion(
                self.store, mission.id, str(spec.occurrence_id)) for spec in view.network.occurrences}

            def prepared(occurrence: Any, visiting: frozenset[Any] = frozenset()) -> bool:
                if occurrence in visiting:
                    return False
                spec = view.network.occurrence(occurrence)
                if spec.form is TaskForm.PRIMITIVE:
                    task = self.store.get_task(str(spec.task_id))
                    if task is None or not task.accepted_result_id:
                        return False
                    result = self.store.get_result(task.accepted_result_id)
                    return bool(result is not None and result.envelope.task_id == task.id
                        and result.envelope.mission_id == mission.id
                        and result.verification_state == "DONE" and result.verdict == "PASS"
                        and statuses[occurrence].preparation_ready)
                # An AGGREGATE owner has no Worker result of its own. Its adopted
                # gating children carry the preparation; requiring VERIFYING on
                # the parent's legacy Task row incorrectly kills this legal wait.
                children = [child.occurrence_id for child in view.network.adopted_children(occurrence)
                            if child.requiredness in GATING_REQUIREDNESS]
                return bool(children) and all(prepared(child, visiting | {occurrence}) for child in children)

            return any(status.scope.required_effect_keys and not status.effects_ready
                       and prepared(occurrence) for occurrence, status in statuses.items())
        except (ContractError, StoreError):
            return False
        return False

    def _idle_facts(
        self, mission: Mission, *, admissions: Any = None, read_plan: bool = True
    ) -> tuple[IdleFacts, Any, Sequence[Any]] | None:
        """The facts :func:`idle_verdict` routes on; None without the assembly.

        Cheap named waits are read first and short-circuit: the plan is read only
        when no wait holds, because only a stall candidate needs its admissions.
        ``admissions`` already read by the caller are reused rather than read again
        (the confirmation reads them exactly once); ``read_plan=False`` asks only
        whether a named wait holds.
        """

        if mission.id in self._unrecovered:
            # Its recovery is still being retried and the rest of its round is skipped, so
            # it cannot dispatch: that is a wait with its own bound (the round-fault cap),
            # never a stall to confirm, ask the Planner about, or stop on.
            return None
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return None
        rows = self.store.list_tasks(mission.id)
        actions = self.store.list_actions(mission.id)
        # 一步被换掉之后（换做法、换后继），旧任务行留作历史，状态不再推进；只有现行计划里的步骤
        # 才算"还在跑"——否则计划停住时它会让任务永远像在等一个不存在的执行者（阶段 E）。
        try:
            planned = {str(spec.task_id) for spec in new_mode.network(mission.id).occurrences}
        except (GraphIntegrityError, ContractError, StoreError):
            planned = {task.id for task in rows}
        # 2026-10-06（Assurance §7.2，车道 O）：判定不再等效果收敛，所以"判定已记、等收尾"只在收尾还能
        # 收敛时才是合法等待——操作那条线已具名判死的效果（成功却无回执、物化一直被拒、结果审阅用完、
        # 交接被拒、裁决打回）让它回到卡死检测：先问规划器一次，再具名停下，不会永远挂着。
        judged = self.commit.assured_closeout_pending(mission.id)
        dead_end = self._operation_dead_end(mission)
        waits = {
            "all_rows_terminal": bool(rows) and not dead_end
            and all(task.status in TERMINAL_TASK for task in rows),
            "closeout_pending": judged and not dead_end,
            "root_resolved": not judged and self._root_resolved(mission, new_mode),
            "running_rows": any(
                task.status is TaskStatus.ACTIVE
                and task.id in planned
                and not self._awaiting_retry_decision(mission.id, task)
                for task in rows
            ),
            "unknown_actions": any(a["state"] in IN_FLIGHT_ACTION_STATES for a in actions),
            # A person's pending approval of any kind: an action awaiting approval, or a
            # review / arbitration / source-change request still open (2026-09-30 real
            # run: a result suspended for a review was failed "no dispatchable work" in
            # the same cycle its review request was made).
            # An action whose hand-off was refused because its step's ground is gone is not
            # waiting on a person: left counted here it would hang for ever (阶段 C 核验).
            "approvals_pending": any(a["state"] in OPEN_ACTION_STATES
                                     and not self._handoff_ground_gone(str(a["action_key"]))
                                     for a in actions)
            or bool(self.store.list_approvals(mission.id, "PENDING"))
            # 阶段 E：现行要求在等人确认（用户刚改了要求）——规划器这时不被问、旧计划也不再开工，
            # 这是在等人，不是停滞。
            or self._requirements_unconfirmed(mission),
            "operation_completion": self._has_pending_operation_completion(mission),
            "assurance_work": self._has_pending_assurance_work(mission.id),
            "planning_wait": self._has_pending_planning_waits(mission.id)
            or mission.id in self._planning_evidence_waits,
            "taskgraph_sources": bool(
                self._taskgraph_notifications is not None
                and self._taskgraph_notifications.awaiting_sources(mission.id)
            ),
        }
        if any(waits.values()):
            return IdleFacts(mission.id, **waits), None, rows
        if admissions is None and not read_plan:
            return IdleFacts(mission.id, plan_has_work=True, **waits), None, rows
        try:
            if admissions is None:
                admissions = new_mode.admissions(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            # An unreadable plan is already reported by the integrity path; it is
            # not this method's finding and must not become a second verdict.
            self._note(f"mission {mission.id}: stall check could not read the plan ({error})")
            return IdleFacts(mission.id, **waits), None, rows
        plan_has_work = bool(admissions.refusals) or bool(admissions.readiness)
        return IdleFacts(mission.id, plan_has_work=plan_has_work, **waits), admissions, rows

    def _root_resolved(self, mission: Mission, new_mode: Any) -> bool:
        """The root goal has an adopted resolution and the Mission is not judged yet."""

        from ..storage.htn_store import HtnStore

        try:
            network = new_mode.network(mission.id)
            htn = HtnStore(self.store)
            for occurrence in network.root_occurrence_ids:
                binding = htn.task_semantics_of(mission.id, str(network.occurrence(occurrence).task_id))
                if binding is not None and htn.adopted_goal_resolution(
                    mission.id, str(binding.obligation_id)
                ) is not None:
                    return True
        except (GraphIntegrityError, ContractError, StoreError):
            return False
        return False


    async def _record_hierarchical_stall(self) -> None:
        """A hierarchical Mission that idles with work left over says so, once.

        P2.3c part 2c, found by the real-model smoke.  :meth:`run` returns when the
        loop is genuinely idle — nothing dispatched this cycle, nothing in flight,
        nothing deferred, no hand-off left to settle — and until now that left a
        hierarchical Mission sitting at ``ACTIVE`` with occurrences every gate had
        withheld and **nothing written down**: an operator saw a Mission that had
        simply stopped moving and had to re-derive which gate was holding what.

        The record is deliberately not a verdict, and part 2d keeps it that way: the
        Mission keeps its status and its rows here.  The *decision* is
        :meth:`_confirm_and_stop_stalled`, which runs immediately after this and only
        stops a Mission whose world has not moved across one more complete cycle —
        §9.1's "repeated no progress", where the repetition is what licenses the stop
        and a single idle cycle is not.

        A Mission with an admissible occurrence is not stalled — it is between cycles.
        """

        for mission in self._active_missions():
            if mission.status is not MissionStatus.ACTIVE:
                continue
            # NEXT-TG-1.0 2B: one verdict for the record and the confirmation. A
            # legal wait (a judged Mission converging its closeout, an UNKNOWN
            # action under reconciliation, a pending approval, ...) is not a stall.
            observed = self._idle_facts(mission)
            if observed is None:
                continue
            facts, admissions, rows = observed
            if idle_verdict(facts).route is not Route.STOP:
                continue
            blocking = [item.to_json() for item in admissions.refusals]
            # An occurrence that every readiness gate admitted and that still did not
            # run is *also* part of the answer — the refusal then came from the
            # allocator (budget, concurrency, attempt policy), not from the plan — so
            # it is named here instead of being silently dropped from the record.
            admitted = sorted(admissions.readiness)
            append_hierarchical_event(
                self.store,
                MISSION_STALLED,
                mission.id,
                # Keyed by the plan revision and the reasons, so one stall is recorded
                # once however many times the loop is re-entered, and a *different*
                # stall — another revision, or another gate — is a new record.
                key=f"{mission.id}:{admissions.plan_revision}:"
                + sha256_hex_text("|".join(sorted(item["reason"] for item in blocking) + admitted))[
                    :16
                ],
                payload={
                    "code": "hierarchical_no_dispatchable_work",
                    "plan_revision": int(admissions.plan_revision),
                    # Every refusal, not a sample: the point is that nobody has to
                    # re-derive which gate is holding which occurrence.
                    "withheld": blocking[:32],
                    "withheld_count": len(blocking),
                    "admitted_not_dispatched": admitted[:32],
                    "unfinished": sorted(
                        task.id for task in rows if task.status not in TERMINAL_TASK
                    )[:32],
                    # §9.1: "the count is not reset by a rename".  The fingerprint is
                    # what makes two stalls comparable *by identity* rather than by
                    # coincidence, and it is what the confirmation cycle re-computes.
                    "fingerprint": self._stall_fingerprint(mission, admissions, rows),
                },
            )
            self._note(
                f"mission {mission.id}: the loop went idle with work left over "
                f"({len(blocking)} withheld, {len(admitted)} admitted and not dispatched)"
            )
            self._stalled_at[mission.id] = self._stall_fingerprint(mission, admissions, rows)

    def _stall_fingerprint(self, mission: Mission, admissions: Any, rows: Sequence[Any]) -> str:
        """Everything that would have to change for this stall to be a different one.

        §9.1 requires that a repeated-no-progress count is **not** reset by a rename,
        so the identity is the *situation*: which revision, which occurrences were
        refused and for exactly which reasons and detail codes, which were admitted
        and not dispatched, which rows are still open, which scope epochs are in
        force, how much evidence has been recorded, and which duties currently hold an
        admitted demand.  Those last three are the world-facing ones: an observation
        recorded, an epoch bumped or a demand admitted from outside is precisely the
        thing that makes the very same plan runnable again, and each of them moves
        this digest.
        """

        from simple_harness.contracts import canonical_json

        from ..contracts.htn import ObligationId
        from ..storage.htn_store import HtnStore
        from ..storage.obligation_store import ObligationStore

        new_mode = self._new_mode(mission)
        semantics = HtnStore(self.store)
        duties = ObligationStore(self.store)
        epochs: Mapping[str, int] = {}
        demands: list[str] = []
        if new_mode is not None:
            try:
                epochs = new_mode.scope_epochs(mission.id)
            except (ContractError, StoreError, GraphIntegrityError):
                epochs = {}
            for duty in sorted(duties.obligation_ids(mission.id)):
                account = duties.account(mission.id, ObligationId(duty))
                if account.has_admitted_demand:
                    demands.append(duty)
        situation: dict[str, Any] = {
            "plan_revision": int(admissions.plan_revision),
            "withheld": sorted(str(canonical_json(item.to_json())) for item in admissions.refusals),
            "admitted_not_dispatched": sorted(admissions.readiness),
            "unfinished": sorted(task.id for task in rows if task.status not in TERMINAL_TASK),
            "scope_epochs": {str(key): int(value) for key, value in sorted(epochs.items())},
            "support_revision": len(semantics.list_observations(mission.id)),
            "admitted_demands": demands,
        }
        return sha256_hex_text(canonical_json(situation))

    async def _confirm_and_stop_stalled(self) -> bool:
        """Look once more, and if the world has not moved, end this execution cycle.

        P2.3c part 2d, decision 2.  §15 makes a Mission a **bounded** cycle: "somebody
        could admit a demand later" belongs to the next cycle or to the Commitment
        above it, not to this one, and a run that never ends cannot enter the paired
        evaluation §21.5 asks for.  But §9.1 licenses stopping on *repeated* no
        progress, not on the first idle turn — and part 2c's smoke showed why: one
        evidence round or one witness re-issue moved the world twice.

        So exactly **one** more complete cycle runs — re-issue both licence lanes,
        one evidence round, advance the compound phases, re-read the admissions — and
        the fingerprint is taken again.  Moved: nothing happens and the loop is free to
        carry on.  Identical: :meth:`CommitService.fail_mission` ends the Mission with
        ``NO_DISPATCHABLE_WORK`` and a §6.4-shaped report — the structure that *was*
        expanded and the duties that are still outstanding, never a claim that the goal
        is impossible (§7.4).

        One cycle, hard-coded.  A ``while`` here would turn an idle loop into a busy
        one, which is the failure this method exists to end.

        Returns whether the loop should **carry on** — third-round review P1-A.
        The confirmation cycle is a cycle *with side effects*: it re-issues both
        licence lanes, records observations and advances compound phases, and the
        decision memo's own wording for a moved fingerprint is "do nothing, **let the
        loop carry on**".  :meth:`run` used to return unconditionally afterwards, so a
        Mission the confirmation had just unblocked — one admitted demand is enough —
        was handed back to the caller as "idle" with a ``READY_CANDIDATE`` occurrence
        and not one Attempt created.  That is exactly the case the memo protected when
        it rejected the alternative design.

        So the answer is "the world moved, go round again" — and it is bounded by
        :data:`MAX_STALL_CARRY_ONS` per Mission per :meth:`run`.  The bound is not
        decoration: a confirmation cycle *records observations*, so a deployment whose
        observers answer something new every time would move the fingerprint for ever
        and the carry-on would be the busy loop this whole path exists to end.  Past
        the bound the run simply returns — the Mission stays ACTIVE with its stall
        recorded, which is the caller's answer and not a verdict about the Mission.

        A ``False`` therefore means "nothing here needs another cycle": either a
        Mission was stopped, or there was no stalled hierarchical Mission at all.
        """

        from ..contracts.htn import ObligationId
        from ..contracts.obligations import ObligationLifecycle
        from ..storage.obligation_store import ObligationStore

        carry_on = False
        for mission in self._active_missions():
            if mission.status is not MissionStatus.ACTIVE:
                continue
            # Handoff item 7 / NEXT-TG-1.0 2B: the same verdict as the record. A
            # legal wait is never turned into NO_DISPATCHABLE_WORK.
            observed = self._idle_facts(mission, read_plan=False)
            if observed is None or idle_verdict(observed[0]).route is not Route.STOP:
                self._stalled_at.pop(mission.id, None)
                continue
            before = self._stalled_at.pop(mission.id, None)
            if before is None:
                continue
            new_mode = self._new_mode(mission)
            if new_mode is None:
                continue
            try:
                now_ms = int(self.store.now * 1000)
                network = new_mode.network(mission.id)
                new_mode.issue_input_witnesses(mission.id, network, now_ms=now_ms)
                new_mode.issue_start_witnesses(mission.id, network, now_ms=now_ms)
                self._gather_evidence(mission)
                new_mode.advance_compound_phases(mission.id)
                admissions = new_mode.admissions(mission.id)
            except (GraphIntegrityError, ContractError, StoreError) as error:
                # An unreadable plan is the integrity path's finding, not this one's.
                self._note(f"mission {mission.id}: stall confirmation could not read ({error})")
                continue
            rows = self.store.list_tasks(mission.id)
            # The confirmation cycle may itself have opened a legal wait (an
            # evidence round, a phase advance): ask the same verdict again.
            observed = self._idle_facts(mission, admissions=admissions)
            if observed is None or idle_verdict(observed[0]).route is not Route.STOP:
                continue
            after = self._stall_fingerprint(mission, admissions, rows)
            if after != before:
                spent = self._stall_carry_ons.get(mission.id, 0)
                if spent < MAX_STALL_CARRY_ONS:
                    self._stall_carry_ons[mission.id] = spent + 1
                    carry_on = True
                    self._note(
                        f"mission {mission.id}: the confirmation cycle moved the world; "
                        "the stall is not confirmed and the loop carries on"
                    )
                else:
                    self._note(
                        f"mission {mission.id}: the confirmation cycle moved the world for the "
                        f"{spent + 1}th time; this execution cycle ends with the Mission active "
                        "and its stall recorded"
                    )
                continue
            duties = ObligationStore(self.store)
            outstanding: list[dict[str, Any]] = []
            for duty in sorted(duties.obligation_ids(mission.id)):
                account = duties.account(mission.id, ObligationId(duty))
                if account.lifecycle is ObligationLifecycle.UNSATISFIED:
                    outstanding.append(
                        {
                            "obligation_id": duty,
                            "has_admitted_demand": bool(account.has_admitted_demand),
                            "remaining_fuel": int(account.remaining_fuel),
                        }
                    )
            # A named stop shares this idle path and must not collapse into
            # no_dispatchable_work: the final review stands rejected and the Planner
            # has no turn left on it.
            if self._root_review_repairs_are_exhausted(mission, new_mode):
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=ROOT_REVIEW_REPAIRS_EXHAUSTED,
                    detail={
                        "plan_revision": int(admissions.plan_revision),
                        "withheld": [item.to_json() for item in admissions.refusals],
                        "admitted_not_dispatched": [],
                        "outstanding_obligations": outstanding,
                        **self._handoff_refusals(mission.id), **self._materialization_refusals(mission.id),
                        **self._outcome_source_refusals(mission.id),
                        "fingerprint": after,
                        "confirmed_after_one_more_cycle": True,
                        **self._root_review_stop_detail(mission, new_mode),
                    },
                )
                self._note(
                    f"mission {mission.id}: root review repairs exhausted; "
                    "this execution cycle ends"
                )
                continue
            # 片 D 第 1 项："计划卡住了要不要改"是规划器的判断。确认停滞之后先把局面如实交给
            # 它一次（每个计划版本一条请求）；同一版计划问过之后又停在原地，才判停。
            from ..runtime.planning_operations import SourceUnavailable
            from . import planning_repair_requests as repair_requests

            withheld = [item.to_json() for item in admissions.refusals]
            # 表二 20（2026-10-03 收尾裁决）：规划器改了计划却没派出任何新尝试、又停在原地——每个新
            # 计划版本都能再问一次，等于不限。只数次数：自上次有新尝试以来问满上限就不再问。
            stall_cap = int(self._config.max_planning_attempts)
            stall_asks = repair_requests.stall_asks_since_new_work(self.store, mission.id)
            try:
                asked = stall_asks < stall_cap and repair_requests.request_planner_for_stall(
                    new_mode, mission, plan_revision=int(admissions.plan_revision),
                    detail={"withheld": withheld[:32], "withheld_count": len(withheld),
                            "admitted_not_dispatched": sorted(admissions.readiness)[:32],
                            "outstanding_obligations": outstanding,
                            **self._handoff_refusals(mission.id), **self._materialization_refusals(mission.id),
                            **self._outcome_source_refusals(mission.id),
                            # 最终审查没给出结论（回复用完仍无法采用）或被打回，是事实，一并交给规划器。
                            **self._root_review_stop_detail(mission, new_mode)})
            except (GraphIntegrityError, ContractError, StoreError, SourceUnavailable) as error:
                # SourceUnavailable：算影响范围要读操作台账，读不了时问不成，照旧判停。
                self._note(f"mission {mission.id}: the stall could not be handed to the Planner ({error})")
                asked = False
            if asked:
                carry_on = True
                self._note(
                    f"mission {mission.id}: no dispatchable work, confirmed by one more cycle; "
                    "the Planner is asked once for this plan revision before the Mission is stopped"
                )
                continue
            # 车道 J H03（原计划 §10.5、§24.1 第 10 条）：问过规划器、它不改、等待关系成环 → 按"死锁"停，
            # 不借"没有可派发的工作"；环的事实随停止详情写出。
            from ..scheduling.wait_for import collect_wait_facts, deadlock_facts, has_deadlock
            wait_for = deadlock_facts(collect_wait_facts(self.store, new_mode.network(mission.id)))
            self._commit_fail_mission(
                mission.id,
                stop_reason=(MissionStopReason.DEADLOCK if has_deadlock(wait_for)
                             else MissionStopReason.NO_DISPATCHABLE_WORK),
                detail={
                    "plan_revision": int(admissions.plan_revision),
                    "wait_for": wait_for,
                    # 这一版计划问过规划器（请求编号、它那一轮有没有开出来）之后仍停在原地。
                    "planner_asked": repair_requests.stall_request_asked(
                        self.store, mission.id, int(admissions.plan_revision)),
                    "stall_asks_without_new_work": stall_asks,
                    "stall_asks_cap": stall_cap,
                    # §6.4: the report names the structure that was expanded and the
                    # duties still outstanding.  Every refusal, not a sample — an
                    # operator must not have to re-derive which gate held what.
                    "withheld": withheld,
                    "admitted_not_dispatched": sorted(admissions.readiness),
                    "outstanding_obligations": outstanding,
                    **self._handoff_refusals(mission.id), **self._materialization_refusals(mission.id),
                    **self._outcome_source_refusals(mission.id),
                    "fingerprint": after,
                    "confirmed_after_one_more_cycle": True,
                    # P2.3j: a Mission that idles *because* its root review rejected the
                    # plan and every repair route is spent says so here, rather than
                    # leaving "no dispatchable work" to be read as a scheduling problem.
                    **self._root_review_stop_detail(mission, new_mode),
                },
            )
            self._note(
                f"mission {mission.id}: no dispatchable work, confirmed by one more cycle; "
                "this execution cycle ends"
            )
        return carry_on

    # ---------------------------------------------------------------- cycle
    def recovery_isolated(self, mission_id: str) -> bool:
        """重启恢复第 3 步核对没通过、已隔离的任务（AER 恢复第 3、8 条"隔离该流、只为核对可继续的
        范围开放执行"）。按任务干活的每个入口都读这一个判断：主循环的任务列表、``_mission_round``、
        保证通道四个消费者、执行图通知、迟到用量导入、已结束任务的通知扫描。"""
        return mission_id in self._recovery_isolated

    def _active_missions(self) -> list[Mission]:
        # 2026-09-29（第 4 批）：每轮要调好几次；先按状态列筛，只解码未结束的任务，
        # 不再每次把几十个已结束任务整份解码一遍。
        ended = sorted(str(status) for status in TERMINAL_MISSION)
        rows = self.store.connection.execute(
            f"SELECT mission_id FROM missions WHERE status NOT IN ({','.join('?' * len(ended))})"
            " ORDER BY created_at, mission_id",
            ended,
        ).fetchall()
        missions = (self.store.get_mission(str(row[0])) for row in rows
                    if not self.recovery_isolated(str(row[0])))
        return [m for m in missions if m is not None and m.status not in TERMINAL_MISSION]

    def _event_cursor(self) -> int:
        """The newest event that is not a liveness heartbeat, across every Mission.

        Global on purpose: a Mission waiting for a concurrency slot, a budget or a
        backpressure drop is freed by *another* Mission's events, never by its own."""
        row = self.store.connection.execute(
            "SELECT seq FROM events WHERE type NOT IN ('HeartbeatReceived', 'RowsWritten')"
            " ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return -1 if row is None else int(row[0])

    def _missions_due(self, missions: Sequence[Mission], cursor: int) -> set[str]:
        """2026-09-29（第 4 批：主循环只处理有变化的任务）：自上次"无进展"的一轮以来库里
        有新事件（心跳不算；任何任务的事件都算，名额、额度是大家共用的）才逐项处理；每
        ``MISSION_RECHECK_SECONDS`` 秒无论如何全量处理一次，好让卡死检测、租约到期、冷却
        结束这类按时间发生的事照常发生；总时限已到的任务马上处理。"""
        now = float(self.store.now)  # 仓库时钟：生产即墙钟，测试可拨快
        due: set[str] = set()
        for mission in missions:
            mark = self._mission_marks.get(mission.id)
            limit = mission.budget.max_runtime_seconds
            if (mark is None or mission.status is MissionStatus.CREATED
                    or mark[0] != cursor
                    or now - mark[1] >= MISSION_RECHECK_SECONDS
                    # 总时限到了要马上停：宁可多处理一轮（等人时间留给 _runtime_exhausted 扣）
                    or (limit is not None and now - mission.created_at >= limit)):
                due.add(mission.id)
        return due

    def _mark_quiet(self, mission_ids: Iterable[str], cursor: int) -> None:
        """``cursor`` is the one read when the round *began*: an event written while the
        round ran (a verification finishing, another Mission freeing a slot) must wake
        the Mission next round, not be folded into its quiet mark (审阅 2026-09-29)."""
        now = float(self.store.now)  # 仓库时钟：生产即墙钟，测试可拨快
        for mission_id in mission_ids:
            self._mission_marks[mission_id] = (cursor, now)

    def _raise_if_verification_crashed(self) -> None:
        # The task table is the sole completion owner. Do not discard successful
        # siblings or report an error again from a delayed done callback.
        for result_id, task in list(self._verifying.items()):
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    del self._verifying[result_id]
                    raise error

    def _has_inflight(self) -> bool:
        if self._assurance_tick is not None and self._assurance_tick.has_pending():
            return True
        if self._taskgraph_notifications is not None and self._taskgraph_notifications.has_pending():
            return True
        """A submitted turn counts as in flight until it is collected — also for a
        terminal Mission (a superseded / cancelled Attempt's cost and late result are
        still collected); critic turns are collected inline by their runner."""

        if self._actions is not None and self._actions.inflight:
            return True  # D7-5': a hand-off this process is waiting on
        if any(not task.done() for task in self._verifying.values()):
            return True
        self._prune_deferred()  # review P0-2: only live waits keep the loop alive
        if self._deferred or self._deferred_planning:  # D6-5': bounded, not idle
            return True
        return any(
            (
                (intent.kind != "critic" and intent.state == "SUBMITTED")
                or (intent.kind == "critic" and self._critic_subject_stopped(intent))
            )
            and self.profile_of(intent) in self.assembled.pools
            and not self.recovery_isolated(intent.mission_id)  # 已隔离：没人会收它，不是在途
            for intent in self.store.list_intents(
                "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
            )
        )  # review P1-4: a turn bound to a pool this process does not run is not ours to wait for

    def _resume_planning_services(self, mission: Mission) -> bool:
        """Consume service receipts and reserve the next Planner atomically."""
        from ..storage.planning_human_store import PlanningHumanStore
        from .planning_runtime_block import pending_block, last_wake
        runtime_block = pending_block(self.store, mission.id)
        if runtime_block is not None and last_wake(self.store, mission.id, runtime_block) is None:
            return False
        questions = PlanningHumanStore(self.store)
        questions.retire_stale(mission.id)
        if questions.pending(mission.id) or self._planner_intents_in_flight(mission.id):
            return False
        if self._requirements_unconfirmed(mission):
            return False
        from .assurance_point_use import PointUseRefused
        try:
            return self._resume_planning_services_now(mission, questions)
        except PointUseRefused as refused:
            return self._planning_round_waits(mission.id, refused, path="service_resume")

    def _resume_planning_services_now(self, mission: Mission, questions: Any) -> bool:
        with self.store.transaction():
            if questions.pending(mission.id) or self._planner_intents_in_flight(mission.id):
                return False
            events = tuple(self.store.iter_events(mission.id))
            resumed = {e.payload.get("service_id", e.payload.get("decision_id"))
                       for e in events if e.type == "PlanningServiceResumed"}
            service_types = {"PlanningEvidenceRecorded", "PlanningMethodProposed", "PlanningMethodReviewed",
                             "PlanningHumanAnswered", "PlanningHumanStale", "PlanningHumanRequested", "PlanningRuntimeBlockWoken",
                             "PlanningRepairRequested", "PlanningLibraryRead"}
            addressed = {request_id for e in events if e.type == "PlanningRepairAddressed"
                         for request_id in e.payload.get("repair_request_ids", ())}
            def service_key(event: Any) -> str:
                identity = event.payload.get("decision_id", event.payload.get("request_id"))
                return f"{event.type}:{identity}"

            def needs_resume(event: Any) -> bool:
                identity = event.payload.get("decision_id", event.payload.get("request_id"))
                if service_key(event) in resumed:
                    return False
                if event.type == "PlanningMethodProposed" and event.payload.get("assurance_review_key"):
                    # 片 A 第 7 项：送审的做法不叫醒规划器；它的审阅结论
                    # （PlanningMethodReviewed）入库后才叫醒。
                    return False
                # Older receipts used an unqualified ID; match their source too,
                # so resuming a question never suppresses its later answer.
                if identity in resumed and any(e.type == "PlanningServiceResumed"
                        and e.payload.get("service_id", e.payload.get("decision_id")) == identity
                        and e.payload.get("source_type") == event.type for e in events):
                    return False
                if event.type in {"PlanningHumanRequested", "PlanningHumanAnswered"}:
                    row = questions.get(str(identity))
                    # 审阅升级（2026-09-30）：根终审"判不下来"问人的裁决题，答案由
                    # ``_advance_root_review`` 消费（写裁决回执），不开规划轮。
                    if row and (row["request"].get("repair_context") or {}).get("kind") == "review_adjudication":
                        return False
                if event.type == "PlanningHumanRequested":
                    row = questions.get(str(identity))
                    return bool(row and row["state"] == "PENDING"
                                and not row["request"]["payload"]["blocking"])
                return event.type != "PlanningRepairRequested" or identity not in addressed

            pending = [e for e in events if e.type in service_types and needs_resume(e)]
            if not pending:
                return False
            # 本任务有做法正在送审、结论还没入库：这时开一轮规划，规划器看到的只能是"审阅中"，
            # 采用不了、也没有别的可做（联测真机：它只好选等待，等待又被退回，白丢一轮）。等结论
            # 入库（PlanningMethodReviewed）再开，别的待处理请求那时一并交给它。
            from .method_plan_reviews import awaiting as method_review_awaiting
            if method_review_awaiting(self.store, mission.id):
                return False
            if self._planning_ladder_spent(mission.id):
                self._stop_planning_round(mission.id, reason="planning_bound_reached",
                    detail={"phase": "planning_service_resume"}, stop_reason=MissionStopReason.PLANNING_FAILED)
                return True
            ordinal = self._next_planning_ordinal(mission.id)
            intent = self._create_planner_intent_now(mission.id, ordinal=ordinal)
            for event in pending:
                service_id = service_key(event)
                append_hierarchical_event(self.store, "PlanningServiceResumed", mission.id,
                    key=str(service_id), payload={
                        "service_id": service_id, "source_type": event.type,
                        "decision_id": event.payload.get("decision_id"),
                        "intent_id": intent.intent_id, "ordinal": ordinal})
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
        from ..storage.planning_human_store import PlanningHumanStore
        from .planning_selection import awaits_authority
        pending_intents = self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
        from .planning_runtime_block import pending_block
        for mission in missions:
            if pending_block(self.store, mission.id) is not None:
                return True
            if any(intent.mission_id == mission.id and awaits_authority(self.store, intent)
                   for intent in pending_intents):
                return True
            if PlanningHumanStore(self.store).pending(mission.id):
                return True
            if self._pending_planning_wait(mission.id) is not None:
                return True
            from .method_plan_reviews import awaiting as method_review_awaiting
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
            from ..contracts.htn import ObligationId
            from ..contracts.obligations import ObligationLifecycle
            from ..contracts.resolution import Validity
            from ..storage.htn_store import HtnStore
            from ..storage.obligation_store import ObligationStore

            htn = HtnStore(self.store)
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
        from ..storage.htn_store import HtnStore

        task = self.store.get_task(ref.id)
        semantics = HtnStore(self.store).task_semantics_of(mission.id, ref.id)
        return bool(
            task is not None
            and task.mission_id == mission.id
            # After registration a live task can temporarily return to READY for
            # a retry. Its exact identity is still the registered producer.
            # A WAIT needs a producer that is actually running: an ACTIVE leaf that only
            # waits for its own retry decision would never wake it (2026-09-25).
            and (registered or (task.status in {TaskStatus.ACTIVE, TaskStatus.VERIFYING}
                                and not self._awaiting_retry_decision(mission.id, task)))
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
        from ..contracts.htn import ObligationId
        from ..storage.htn_store import HtnStore
        from ..storage.obligation_store import ObligationStore
        from .planning_wait_targets import steps_under

        waited: list[PlanningRefV1] = []
        for ref in refs:
            if self._planning_wait_ref_waitable(mission, ref):
                waited.append(ref)
                continue
            dispatch = self._dispatch_for(mission.id)
            if dispatch is None or ref.kind not in {
                PlanningRefKind.METHOD_INSTANCE, PlanningRefKind.OBLIGATION
            }:
                return None
            try:
                network = dispatch.network(mission.id)
                if ref.kind is PlanningRefKind.METHOD_INSTANCE:
                    instance = next((item for item in network.method_instances
                                     if str(item.instance_id) == ref.id
                                     and item.instance_id in set(network.adopted_instance_ids)), None)
                    if (instance is None
                            or max(1, int(instance.plan_revision)) != ref.semantic_revision
                            or instance.parameters_digest() != ref.content_hash):
                        return None
                    task_ids = steps_under(network, instance_id=ref.id)
                else:
                    obligation = ObligationStore(self.store).obligation(mission.id, ObligationId(ref.id))
                    if (ref.semantic_revision != 1
                            or content_hash_of(obligation.to_json()) != ref.content_hash):
                        return None
                    task_ids = steps_under(network, obligation_id=ref.id)
            except (ContractError, KeyError, StoreError, ValueError):
                return None
            running = []
            for task_id in task_ids:
                semantics = HtnStore(self.store).task_semantics_of(mission.id, task_id)
                if semantics is None:
                    continue
                step = PlanningRefV1(kind=PlanningRefKind.TASK, id=task_id,
                                     semantic_revision=int(semantics.contract_revision),
                                     content_hash=semantics.content_hash())
                if (self._planning_wait_ref_waitable(mission, step)
                        and not self._planning_wait_ref_satisfied(mission, step)):
                    running.append(step)
            if not running:
                return None
            waited.extend(running)
        return tuple({(str(item.kind), item.id): item for item in waited}.values())

    async def _wake_planning_waits(self) -> bool:
        """Commit target validation, Planner intent and wake receipt atomically."""

        progressed = False
        for listed in self._active_missions():
            if listed.id in self._unrecovered:
                continue
            with self._round_boundary(listed.id, "planning_wait"):
                progressed = self._wake_planning_wait(listed) or progressed
        return progressed

    def _wake_planning_wait(self, listed: Mission) -> bool:
        from .assurance_point_use import PointUseRefused

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
                                    ref.kind is PlanningRefKind.TASK
                                    and ref.id == row["task_id"]
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

    async def _mission_round(self, mission_id: str, where: str, step: Callable[[], Awaitable[Any]]) -> bool:
        """One Mission's share of this round, behind one boundary (阶段 B 裁决第 9 类).

        Whatever escapes ``step`` — a disk or I/O error, a read-side integrity refusal, a
        trigger's ABORT, a refused Commit — is this Mission's fault for this round only:
        the transaction it was in has rolled back, the fault is recorded, the rest of
        this Mission's round is skipped and every other Mission carries on.  Next round
        it is tried again in place; nothing is asked of a model again (raw replies and
        decisions are durable and collection is idempotent).  What a fault means is read
        from one table (``classify_round_fault``): damaged data stops the Mission at
        once; anything else is retried and stops it, by name, only after
        ``NON_MODEL_FAILURE_CAP`` consecutive rounds at the same place spanning at least
        ``ROUND_FAULT_MIN_SECONDS``.  ``StoreBusy`` (another instance holds the lock)
        is the whole store's, not this Mission's, and still skips the round; an
        ``InjectedCrash`` stands for the process dying and still ends ``run()``.

        ``where`` names the place and, for an intent, the intent: one healthy intent's
        success must not reset another intent's streak."""

        if mission_id in self._unrecovered and not where.startswith(("recover", "startup_bind")):
            return False  # its restart recovery has not held yet; retried first next round
        if self.recovery_isolated(mission_id):
            return False  # 重启核对没通过、已隔离：本进程不替它做任何事（AER 恢复第 3、8 条）
        progressed = False
        with self._round_boundary(mission_id, where):
            progressed = bool(await step())
        return await self._settle_parked_faults() or progressed

    @contextlib.contextmanager
    def _round_boundary(self, mission_id: str, where: str) -> Iterator[None]:
        """The boundary itself — the one place a Mission's fault is caught.

        ``_mission_round`` is this plus settling the fault at once.  The round's global
        scans (late accounting, the Assurance tick, TaskGraph notifications, planning
        waits and blocks) walk every Mission inside one synchronous or interleaved pass,
        so each wraps one Mission's share in this boundary directly: the fault is parked,
        the scan carries on with the next Mission, and ``_settle_parked_faults`` applies
        the same table and the same cap right after the scan."""
        try:
            yield
        except (StoreBusy, InjectedCrash):
            raise
        except Exception as error:  # noqa: BLE001 - the boundary: never the loop's end
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            self._parked_faults.append((mission_id, where, error))
        else:
            self._round_faults.pop((mission_id, where), None)

    async def _settle_parked_faults(self) -> bool:
        progressed = False
        while self._parked_faults:
            mission_id, where, error = self._parked_faults.pop(0)
            progressed = await self._round_fault(mission_id, where, error) or progressed
        return progressed

    async def _round_fault(self, mission_id: str, where: str, error: Exception) -> bool:
        from .failure_classes import (
            NON_MODEL_FAILURE_CAP, ROUND_CORRUPT, ROUND_FAULT_MIN_SECONDS, classify_round_fault,
        )

        kind, code = classify_round_fault(error)
        now = self.store.now
        count, first = self._round_faults.get((mission_id, where), (0, now))
        count += 1
        self._round_faults[(mission_id, where)] = (count, first)
        summary = f"{type(error).__name__}: {str(error)[:300]}"
        self._note(f"mission {mission_id}: round fault at {where} #{count} ({kind}) {summary}")
        logger.warning("orchestrator.round_fault mission=%s where=%s count=%s kind=%s error=%s",
                       mission_id, where, count, kind, summary)
        if count == 1:
            # A separate short transaction; when even that cannot be written (a full
            # disk) the in-memory count is the record and the cap still applies.
            try:
                with self.store.transaction():
                    self.commit._emit("MissionRoundFault", mission_id,
                                      key=f"{mission_id}:{where}:{int(first * 1000)}",
                                      payload={"where": where, "class": kind, "code": code,
                                               "error_type": type(error).__name__,
                                               "summary": summary, "count": count})
            except Exception:  # noqa: BLE001
                self._note(f"mission {mission_id}: round fault not recorded (store unwritable)")
        try:
            mission = self.store.get_mission(mission_id)
        except Exception:  # noqa: BLE001
            return False
        if mission is None or mission.status in TERMINAL_MISSION:
            # Nothing left to stop; the streak (and its single record) stays, so an
            # ended Mission's failing collection does not write one event per round.
            # At the cap the intent itself is closed (2026-10-03 收尾裁决第 3 张): left
            # SUBMITTED it keeps ``run()`` waiting and its reservation can never be settled
            # at the bound ("宁可多算、不冻结").  If even that cannot be written, keep waiting.
            intent_id = where.partition(":")[2]
            if (mission is not None and intent_id and where.startswith("collect")
                    and count >= NON_MODEL_FAILURE_CAP and now - first >= ROUND_FAULT_MIN_SECONDS):
                try:
                    intent = self.store.get_intent(intent_id)
                    if intent is not None and intent.state in {"AGENT_CREATED", "SUBMITTED"}:
                        with self.store.transaction():
                            self._settle_intent(intent, "FAILED")
                            self.commit._emit("MissionRoundFault", mission_id,
                                              key=f"{mission_id}:{where}:closed",
                                              payload={"where": where, "class": kind, "count": count,
                                                       "closed_intent": intent_id,
                                                       "reason": "round_fault_after_mission_end",
                                                       "error_type": type(error).__name__, "summary": summary})
                        self._round_faults.pop((mission_id, where), None)
                        self._note(f"mission {mission_id}: intent {intent_id} closed after {count} failing rounds")
                        return True
                except Exception:  # noqa: BLE001
                    self._note(f"mission {mission_id}: failing intent {intent_id} could not be closed; waiting")
            return False
        try:
            if kind == ROUND_CORRUPT:
                await self._plan_integrity_stop(mission, error)
                ended = self.store.get_mission(mission_id)
                if ended is not None and ended.status in TERMINAL_MISSION:
                    self._round_faults.pop((mission_id, where), None)
                    return True
                return False  # not stoppable from its state (still CREATED): retried next round
            if count >= NON_MODEL_FAILURE_CAP and now - first >= ROUND_FAULT_MIN_SECONDS:
                detail = {"where": where, "error_type": type(error).__name__, "summary": summary,
                          "rounds": count, "seconds": round(now - first, 3)}
                if mission.status is MissionStatus.PLANNING:
                    self._commit_fail_planning(mission.id, reason="store_fault", detail=detail,
                                               stop_reason=MissionStopReason.STORE_FAULT)
                elif mission.status is MissionStatus.ACTIVE:
                    self._commit_fail_mission(mission.id, stop_reason=MissionStopReason.STORE_FAULT,
                                              detail=detail)
                else:
                    return False
                await self._release_mission(mission.id)
                self._round_faults.pop((mission_id, where), None)
                self._note(f"mission {mission.id} stopped: store fault at {where} ({count} rounds)")
                return True
        except Exception as stop_error:  # noqa: BLE001 - the stop itself could not be written
            self._note(f"mission {mission_id}: stop not written ({type(stop_error).__name__}); retrying")
        return False

    async def _cycle(self) -> bool:
        try:
            return await self._cycle_inner()
        except StoreBusy as error:
            # D3-10': another instance holds the write lock; nothing was applied, retry next cycle
            self._note(f"store busy, cycle skipped: {error}")
            await asyncio.sleep(self._poll)
            return False
        except (CommitRejected, IllegalTransition) as error:
            # a Commit refused because the library moved under us (another instance, a
            # cascade): nothing was written; the next cycle re-observes (P1-4)
            self._note(f"commit refused, cycle skipped: {error}")
            await asyncio.sleep(self._poll)
            return False

    async def _close_finished_agents(self, *, force: bool = False, limit: int = 50) -> int:
        """Close the Agents of Missions that have ended (2026-09-30, structural-repair run 6).

        Every model turn creates an Agent and nothing closed them, while a pool's instance
        cap counts the Agents that are not closed: a desktop library filled its pool with
        1000 finished Agents in a few days and no new step could get an executor.  Only the
        lifecycle moves; bindings, sessions and journals stay for audit.  Bounded per sweep
        and throttled; a failure to close one Agent never stops the loop."""

        now = self.store.now
        if not force and self._agent_sweep_at is not None and now - self._agent_sweep_at < 30.0:
            return 0
        self._agent_sweep_at = now
        rows = self.store.connection.execute(
            "SELECT d.intent_id, d.agent_id FROM dispatch_intents d JOIN missions m ON m.mission_id=d.mission_id"
            " WHERE d.agent_id IS NOT NULL AND d.state IN ('SETTLED','FAILED')"
            " AND m.status IN ('COMPLETED','FAILED','CANCELLED') ORDER BY d.updated_at").fetchall()
        closed = 0
        for intent_id, agent_id in rows:
            if agent_id in self._agents_closed:
                continue
            if closed >= limit:
                break
            intent = self.store.get_intent(str(intent_id))
            if intent is None or self._pool_missing(intent):
                continue
            try:
                if await self.bridge_for(intent).close(agent_id=str(agent_id)):
                    closed += 1
            except Exception as error:  # noqa: BLE001 - housekeeping never stops the loop
                self._note(f"agent {agent_id}: close skipped ({type(error).__name__})")
            self._agents_closed.add(str(agent_id))
        return closed

    async def _cycle_inner(self) -> bool:
        self._require_assurance_execution_root()
        progressed = False
        # Before any tick reads a Mission: one this build cannot serve (a flat-mode row,
        # a dropped planning contract) is stopped by name, never left to make every
        # later step of the round refuse — ``_cycle`` skips a whole round on a refusal.
        for mission in self._active_missions():
            with self._round_boundary(mission.id, "contract_check"):
                if self._refuse_unsupported_contract(mission):
                    progressed = True
        await self._close_finished_agents()
        progressed = import_late_accounting(self) or progressed
        if self._assurance_tick is not None and await self._assurance_tick.tick():
            progressed = True
        from .planning_runtime_block import wake_blocks
        progressed = wake_blocks(self) or progressed
        if self._taskgraph_notifications is not None and await self._taskgraph_notifications.tick():
            progressed = True
        if await self._wake_planning_waits():
            progressed = True
        # 上面几处全局扫描里各任务的那一份都在同一个边界里；出了错的在这里按同一张表结清
        if await self._settle_parked_faults():
            progressed = True
        for mission in self._active_missions():
            if mission.id in self._unrecovered:
                await self._recover_mission_round(mission)
        self._unrecovered &= {mission.id for mission in self._active_missions()}
        busy: set[str] = set()  # 本轮有进展的任务：下一轮照样处理，不记"安静"
        round_cursor = self._event_cursor()
        due = self._missions_due(self._active_missions(), round_cursor)
        # P2.3c part 2c: before anything is planned, look at what is still unknown.
        # It runs *first* because the Planner package is built out of the evidence
        # snapshot: gathering after the intent was created would show the model the
        # world as it was one round ago.
        for mission in self._active_missions():
            if mission.id not in due:
                continue

            async def before_planning(mission: Mission = mission) -> bool:
                from .method_plan_reviews import advance as advance_method_reviews
                from .planning_repair_requests import collect_triggers
                moved = False
                try:
                    moved = advance_method_reviews(self, mission) or moved
                    moved = collect_triggers(self, mission) or moved
                    moved = self._resume_planning_services(mission) or moved
                except BudgetExhausted as error:
                    self._stop_planning_round(mission.id, reason="budget_exhausted",
                        detail={"phase": "planning_service_resume", "dimension": error.dimension,
                                "requested": error.requested, "remaining": error.remaining},
                        stop_reason=MissionStopReason.BUDGET_EXHAUSTED)
                    return True
                except RoutingUnavailable:
                    pass  # the durable receipt remains pending until a bound planner is available
                return self._gather_evidence(mission) or moved

            # A damaged plan, a store fault, a refused Commit: this Mission's round, never
            # the loop's (§24.1 decision 11; 阶段 B 裁决第 9 类).
            if await self._mission_round(mission.id, "before_planning", before_planning):
                progressed = True
                busy.add(mission.id)
        for mission in self._active_missions():
            if mission.id not in due:
                continue
            if mission.status is MissionStatus.CREATED:
                busy.add(mission.id)
                if self._assembly_missing(mission, at="start_planning"):
                    continue
                if await self._mission_round(mission.id, "start_planning",
                                             lambda mission=mission: self._start_planning(mission)):
                    progressed = True
        if await self._retry_deferred_planning():
            progressed = True
        active = {mission.id for mission in self._active_missions()}
        for intent in self.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED"):
            if (intent.kind == "critic" or intent.config.get("assurance_protocol") == "assurance-exec-v1.1") and self._critic_subject_stopped(intent):
                if await self._mission_round(intent.mission_id, f"collect_stopped_critic:{intent.intent_id}",
                                             lambda intent=intent: self._collect_stopped_critic(intent)):
                    progressed = True
                continue
            if intent.mission_id not in active:
                continue
            if await self._mission_round(intent.mission_id, f"dispatch:{intent.intent_id}",
                                         lambda intent=intent: self._dispatch(intent)):
                progressed = True
        for intent in self.store.list_intents("SUBMITTED"):
            if intent.kind == "critic":
                if self._critic_subject_stopped(intent) and await self._mission_round(
                        intent.mission_id, f"collect_after_stop:{intent.intent_id}",
                        lambda intent=intent: self._collect_after_stop(intent)):
                    progressed = True
                continue  # critics are collected inline by the critic runner
            if intent.mission_id not in active:
                if await self._mission_round(intent.mission_id, f"collect_after_stop:{intent.intent_id}",
                                             lambda intent=intent: self._collect_after_stop(intent)):
                    progressed = True
                continue
            if await self._mission_round(intent.mission_id, f"collect:{intent.intent_id}",
                                         lambda intent=intent: self._collect(intent)):
                progressed = True
        # D6-9': verification runs in a bounded set of tasks (``verifier_workers``); the loop
        # reaps finished ones and starts new ones.  A crash inside a verification is raised at
        # the next phase boundary — nothing else is decided after it (fault-injection tests)
        self._raise_if_verification_crashed()
        for result_id, task in list(self._verifying.items()):
            if task.done():
                del self._verifying[result_id]
                if task.result():
                    progressed = True
        for stored in self.store.list_results_by_verification("PENDING", "RUNNING"):
            if (
                stored.envelope.mission_id not in active
                or stored.envelope.id in self._verifying
            ):
                continue
            if len(self._verifying) >= self._config.verifier_workers:
                break
            task = asyncio.create_task(self._verify(stored.envelope.id))
            self._verifying[stored.envelope.id] = task
            await asyncio.sleep(0)  # let the verification reach its first Commit before deciding
        # TaskGraph 补全第四批：改要求后沿用的叶子按新要求重审，与验证共用同一组名额
        for mission_id in sorted(active):
            if len(self._verifying) >= self._config.verifier_workers:
                break
            new_mode = self._new_mode(self.store.get_mission(mission_id))
            if new_mode is None:
                continue
            for item in carried_reviews(self.store, new_mode, mission_id):
                if item.key in self._verifying:
                    continue
                if len(self._verifying) >= self._config.verifier_workers:
                    break
                self._verifying[item.key] = asyncio.create_task(self._carried_review(item))
                await asyncio.sleep(0)
        self._raise_if_verification_crashed()
        self._observe_pressure(active)
        missions = self._active_missions()
        if missions:  # D6-1 fair progress: the allocation order rotates across Missions
            start = self._rotation % len(missions)
            self._rotation += 1
            missions = missions[start:] + missions[:start]
        for mission in missions:
            if mission.id not in due:
                continue
            self._raise_if_verification_crashed()
            if await self._mission_round(mission.id, "decide", lambda mission=mission: self._decide(mission)):
                progressed = True
                busy.add(mission.id)
        self._mark_quiet(due - busy, round_cursor)
        return progressed

    def _gather_evidence(self, mission: Mission) -> bool:
        """One read-only evidence round for a hierarchical Mission (P2.3c part 2c).

        ``True`` only when something was actually **recorded**.  An observer that is
        down returns nothing and the round is not progress — reporting it as progress
        would turn an outage into a loop that never goes idle and never gives the
        operator the quiet the ``OBSERVER_UNAVAILABLE`` record is supposed to stand out
        against.

        A deployment with no ``PlanningWorld`` is skipped rather than raised at: the
        planning path already refuses visibly (``require_planning_world``), and a
        Mission that cannot plan does not also need the loop to die here.
        """

        new_mode = self._new_mode(mission)
        if new_mode is None or new_mode.planning is None:
            return False
        try:
            result = new_mode.run_evidence_round(mission.id, now_ms=int(self.store.now * 1000))
        except GraphIntegrityError:
            # The plan is damaged; ``_decide`` is where that is diagnosed and stopped.
            return False
        recorded = tuple(result.recorded)
        if recorded:
            self._note(
                f"mission {mission.id} evidence round recorded {len(recorded)} observation(s)"
            )
        return bool(recorded)

    # ------------------------------------------------------------- planning
    def _prune_deferred(self) -> None:
        """Review P0-2: a wait whose Task or Mission has ended is dropped, whoever ended it."""

        for task_id in list(self._deferred):
            task = self.store.get_task(task_id)
            mission = None if task is None else self.store.get_mission(task.mission_id)
            if (
                task is None
                or task.status in TERMINAL_TASK
                or mission is None
                or mission.status is not MissionStatus.ACTIVE
            ):
                self._deferred.pop(task_id, None)
        for mission_id in list(self._deferred_planning):
            mission = self.store.get_mission(mission_id)
            # Review P2-4: the test used to be ``is not PLANNING``, which was right while
            # the only planning round was the one that *produced* the first plan.  P2.3d
            # opens rounds on a Mission that is already ACTIVE (D5-A's repair, D5-B's
            # refinement), and dropping those silently left the Mission with the round
            # counted as used and never asked.  A Mission that has ended still drops.
            if mission is None or mission.status in TERMINAL_MISSION:
                self._deferred_planning.pop(mission_id, None)

    def _requirements_unconfirmed(self, mission: Mission) -> bool:
        """The Mission's current requirements have no confirmed completion mapping yet (the
        Mission was just created, or the user just amended them): the Planner is not asked —
        a plan it made would be refused when its completion scopes are frozen.  The
        confirmation page shows "waiting for the completion requirements to be confirmed".
        Every path that opens a Planner round asks this one question (阶段 E)."""
        return self._planning_start_gate is not None and not self._planning_start_gate(mission)

    def _knowledge_handover(self, mission_id: str, reader: Mapping[str, Any]) -> Any:
        """``knowledge_read`` 交出正文前的那道门（裁决 2026-10-07 第 5 件）。执行者：与装上下文同一个
        使用证书签发方，消费方是这次工具调用。审阅员读黑板算披露（DISCLOSE），按第 2 批 A03 定；
        在那之前只过同一套判定、不签证书。"""
        from .assurance_point_use import judge_claims, knowledge_handover

        if reader.get("review_key") is not None:
            return lambda claim: judge_claims(self.store, mission_id, (claim,))
        attempt = self.store.get_attempt(str(reader["attempt_id"]))
        if attempt is None or attempt.mission_id != mission_id:
            return lambda claim: ("TOOL_CALL_ATTEMPT_UNKNOWN",)
        return knowledge_handover(self.commit, mission_id=mission_id, consumer_id=str(reader["call_id"]),
                                  task_id=attempt.task_id)

    def _planning_round_waits(self, mission_id: str, refused: Any, *, path: str) -> bool:
        """推后第 1 批 A26（裁决 2026-10-07 建议 1）：要当事实交给规划器的证据此刻签不出 PLAN 证书
        （证据刚过时、时钟回拨、授权变了）。三条开轮的路——普通开轮、服务恢复、等待唤醒——都走这里：
        这一轮不开（开轮事务已回滚），原因写进进度记录，不算一轮故障、不停任务；下一轮按当时的世界
        重新组包再签。"""
        now = self.store.now
        since, last = self._planning_evidence_waits.get(mission_id, (now, now))
        if now - last > self._config.profile_wait_seconds:
            since = now  # 上一段等待早已断开（中间没再签不出）：重新计时，不把旧的开始时刻算进来
        self._planning_evidence_waits[mission_id] = (since, now)
        refusals = list(refused.use.refusals)
        waited = now - since
        if waited >= self._config.profile_wait_seconds:
            # opt.169 评估建议 1：原因一直不消失时不无声地等下去——与"规划池不可用"同一个上限，
            # 到点按名停下，明细写清哪条路、哪几条拒绝原因、等了多久。
            self._planning_evidence_waits.pop(mission_id, None)
            self._stop_planning_round(
                mission_id,
                reason="planning_evidence_uncertifiable",
                detail={"path": path, "refusals": refusals[:16], "waited_seconds": round(waited, 3),
                        "profile_wait_seconds": self._config.profile_wait_seconds},
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            )
            self._note(f"mission {mission_id}: planning evidence never certifiable → stopped")
            return False
        self._note(f"mission {mission_id}: planning round waits at {path} ({'; '.join(refusals)})")
        return False

    async def _try_planner_intent(self, mission_id: str, *, ordinal: int) -> bool:
        """Create the Planner intent, or — review P0-1 — wait (bounded) while its pool is
        cooling down; a package that would carry a credential stops planning visibly."""

        mission = self.store.get_mission(mission_id)
        if mission is not None and self._assembly_missing(mission, at="planner_retry"):
            return False
        if mission is not None and self._requirements_unconfirmed(mission):
            return False
        from .assurance_point_use import PointUseRefused
        try:
            await self._create_planner_intent(mission_id, ordinal=ordinal)
        except PointUseRefused as refused:
            return self._planning_round_waits(mission_id, refused, path="ask")
        except UnsupportedPlanningPackage as error:
            # 2026-09-25: a Mission bound to a package this build no longer serves stops
            # here, by itself — it must not take the orchestrator loop (and every other
            # Mission in the library) down with it.
            self._deferred_planning.pop(mission_id, None)
            self._stop_planning_round(
                mission_id,
                reason="unsupported_planning_package",
                detail={"error": str(error)[:300], "ordinal": ordinal},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission_id}: {error} → stopped")
            return False
        except RoutingUnavailable as unavailable:
            since = self._deferred_planning.get(mission_id, (self.store.now, ordinal))[0]
            self._deferred_planning[mission_id] = (since, ordinal)
            if self.store.now - since >= self._config.profile_wait_seconds:
                self._deferred_planning.pop(mission_id, None)
                self._stop_planning_round(
                    mission_id,
                    reason="runtime_unavailable",
                    detail={
                        "profile_id": unavailable.profile_id,
                        "waited_seconds": round(self.store.now - since, 3),
                        "profile_wait_seconds": self._config.profile_wait_seconds,
                        "ordinal": ordinal,
                    },
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                )
                self._note(
                    f"mission {mission_id}: planner pool {unavailable.profile_id!r} unavailable → stopped"
                )
            return False
        except ContextRejected as error:
            self._deferred_planning.pop(mission_id, None)
            # Verification P2-F / mutation VO: ``ordinal`` is added only where it is
            # new information.  A Mission still in PLANNING writes exactly the payload
            # it wrote on main — the legacy ``MissionFailed.detail`` is a shipped shape
            # and this slice has no business widening it — while a Mission past PLANNING
            # is in a round that only P2.3d opens, where "which round" is the one thing
            # an operator cannot otherwise work out.
            rejected = self.store.get_mission(mission_id)
            detail: dict[str, Any] = {"error": str(error)[:300]}
            if rejected is not None and rejected.status is not MissionStatus.PLANNING:
                detail["ordinal"] = ordinal
            self._stop_planning_round(
                mission_id,
                reason="context_rejected",
                detail=detail,
                stop_reason=MissionStopReason.CONTEXT_REJECTED,
            )
            self._note(f"mission {mission_id}: planner package refused ({error})")
            return False
        self._deferred_planning.pop(mission_id, None)
        return True

    def _stop_planning_round(
        self,
        mission_id: str,
        *,
        reason: str,
        detail: Mapping[str, Any],
        stop_reason: MissionStopReason,
    ) -> None:
        """End a Mission whose Planner round cannot be opened, in its own phase's terms.

        Review P0-1 / P2-4.  ``fail_planning`` files the stop as a *planning* failure and
        it was the only ending here, which was right while every Planner round belonged
        to the phase that produces the first plan.  P2.3d opens rounds on a Mission that
        already holds a committed plan, and calling ``fail_planning`` on one of those
        says something untrue about it (``final_report.planning_failure`` on a Mission
        that was planned) and skips the cascade a Mission-level stop owes its open work.

        So: a Mission still in PLANNING ends exactly as it did before — byte for byte,
        which is what the legacy goldens read — and a Mission past it ends through
        ``fail_mission``, whose report carries the same reason and the Task rows.
        """

        mission = self.store.get_mission(mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return
        if mission.status is MissionStatus.PLANNING:
            self._commit_fail_planning(
                mission_id, reason=reason, detail=detail, stop_reason=stop_reason
            )
            return
        self._commit_fail_mission(
            mission_id, stop_reason=stop_reason, detail={"reason": reason, **dict(detail)}
        )

    async def _planner_round_on_committed_plan(
        self, mission_id: str, *, ordinal: int, phase: str
    ) -> bool:
        """Open a Planner round for a Mission that already holds a plan (P2.3d).

        Review P0-1: ``_create_planner_intent`` reserves ``planner_reserve_tokens``
        against the Mission account and raises ``BudgetExhausted`` — a ``StoreError``
        that ``_cycle`` does not forgive and ``run()`` does not catch.  ``_start_planning``
        has caught it since step 6; D5-A's repair round and D5-B's refinement round had
        not, so a Mission that ran its account down — which is precisely the state the
        repair branch is reached in — took the whole process with it.

        One Mission's exhaustion is one Mission's stop (§24.1 decision 11).
        """

        try:
            return await self._try_planner_intent(mission_id, ordinal=ordinal)
        except BudgetExhausted as error:
            self._stop_planning_round(
                mission_id,
                reason="budget_exhausted",
                detail={
                    "dimension": error.dimension,
                    "requested": error.requested,
                    "remaining": error.remaining,
                    "account": error.account_id,
                    "phase": phase,
                    "ordinal": ordinal,
                    "scope": "global" if is_global_account(error.account_id) else "mission",
                },
                stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
            )
            self._note(
                f"mission {mission_id} stopped in {phase}: budget_exhausted ({error.dimension})"
            )
            return False

    async def _retry_deferred_planning(self) -> bool:
        progressed = False
        self._prune_deferred()
        for mission_id, (_since, ordinal) in list(self._deferred_planning.items()):
            mission = self.store.get_mission(mission_id)
            if mission is not None and mission.status is not MissionStatus.PLANNING:
                # Review P0-1: a deferred round belonging to a Mission that is already
                # ACTIVE is one of P2.3d's, and the retry must not carry its exhaustion
                # out of the loop either.
                step = (lambda mission_id=mission_id, ordinal=ordinal: self._planner_round_on_committed_plan(
                    mission_id, ordinal=ordinal, phase="deferred_planning"))
            else:
                step = lambda mission_id=mission_id, ordinal=ordinal: self._try_planner_intent(  # noqa: E731
                    mission_id, ordinal=ordinal)
            if await self._mission_round(mission_id, "deferred_planning", step):
                progressed = True
        return progressed


    async def _start_planning(self, mission: Mission) -> bool:
        """``False`` when nothing could start (assembly missing, or the start gate is
        still waiting for a person).  2026-09-26 (Host 真机): the caller counted such a
        no-op as progress, and a progressing cycle does not sleep, so one Mission
        waiting for its completion mapping spun the loop at 100% CPU and starved the
        Host's event loop."""
        if self._assembly_missing(mission, at="start_planning"):
            return False
        if self._requirements_unconfirmed(mission):
            return False
        self.commit.begin_planning(mission.id)
        try:
            await self._try_planner_intent(mission.id, ordinal=1)
        except BudgetExhausted as error:
            # D6-8 / D3-12': a pool that cannot even fund the Planner is exhausted on that
            # dimension; the Mission stops visibly instead of the loop crashing
            detail = {
                "dimension": error.dimension,
                "requested": error.requested,
                "remaining": error.remaining,
                "account": error.account_id,
                "phase": "planning",
                # review P1-1: the Global pool is named as such — no Mission is to blame
                "scope": "global" if is_global_account(error.account_id) else "mission",
            }
            self._commit_fail_planning(
                mission.id,
                reason="budget_exhausted",
                detail=detail,
                stop_reason=MissionStopReason.BUDGET_EXHAUSTED,
            )
            self._note(
                f"mission {mission.id} stopped in planning: budget_exhausted ({error.dimension})"
            )
        return True

    def _planning_retry_budgets(self, mission: Mission, *, format_retries: int) -> Any:
        """The §39 budget view a feedback value reports (same count admission uses)."""

        from ..contracts.planning_decisions import PlanningRetryBudgetView

        allowance = max(0, int(self._config.max_planning_attempts))
        return PlanningRetryBudgetView(
            same_request_format_retries_remaining=max(0, int(format_retries)),
            planning_rounds_remaining=max(0, allowance - self._planning_attempts(mission.id)),
            root_review_repairs_remaining=max(0, int(self._config.max_root_review_repairs)),
            repeated_failure_before_escalation_remaining=None,
        )

    def _latest_planning_feedback(self, mission: Mission) -> Any:
        """2026-09-30：新请求的 ``previous_feedback``——本任务最近一条规划决定被拒时，
        告诉规划器拒在哪（字段路径）；最近一条没被拒就是 ``None``。"""

        from ..planning.decision_feedback import feedback_from_decision
        from ..storage.planning_decision_store import PlanningDecisionStore

        row = PlanningDecisionStore(self.store).latest_planning_decision(mission.id)
        return feedback_from_decision(
            row, budgets=self._planning_retry_budgets(mission, format_retries=1)
        )

    def _hierarchical_planner_package(
        self, new_mode: HierarchicalDispatch, mission: Mission, *, ordinal: int,
        previous_feedback: Any = None,
    ) -> Any:
        """Seal the hierarchical Planner's package.

        The network is read through :meth:`HierarchicalDispatch.network`, the same
        call the plan compiler makes, so the package describes exactly the plan the
        commit will be checked against — and before the first revision exists that
        call already answers with the *seed* network, because the Planner's first job
        is to refine the root goal the Mission was opened for.  A damaged plan raises
        rather than producing a package about a plan that is not readable, and a
        deployment without a ``PlanningWorld`` raises rather than falling back to the
        other mode's package.
        """

        # ``_seal`` is private to the context builder and is reached anyway, on
        # purpose: it renders the package *and* derives the context hash, and a second
        # renderer here would be a second answer to "what did the model see".
        from ..context.context_builder import _seal
        from ..runtime.role_templates import PLANNING_DECISION_PACKAGE_VERSION
        from .planner_views import read_planner_package
        from .planning_protocol_binding import current_planning_protocol

        del ordinal  # the round number is not a fact the Planner reads
        current_planning_protocol(self.store, mission.id)
        world = new_mode.require_planning_world()
        network = new_mode.network(mission.id)
        # One assessment, rendered into the request *and* recorded.  Computing it twice
        # would let the record and the message disagree about a world that moved
        # between them, and the record exists precisely to say what the model was told.
        reports = new_mode.method_applicability(mission.id)
        new_mode.record_method_applicability(mission.id, reports=reports)
        usage = self.store.mission_budget_usage(mission.id)
        if usage is None:
            raise ContractError("planner budget ledger is unavailable")
        token_limit = mission.budget.max_tokens
        remaining_tokens = (self._config.planner_reserve_tokens if token_limit is None else
            max(0, int(token_limit) - int(usage["settled_tokens"]) - int(usage["reserved_tokens"])))
        return _seal(read_planner_package(
            store=self.store, mission=mission, network=network, world=world, dispatch=new_mode,
            reports=reports, previous_feedback=previous_feedback,
            package_version=PLANNING_DECISION_PACKAGE_VERSION,
            budget={
                "planning_remaining": max(
                    0, self._config.max_planning_attempts - self._planning_attempts(mission.id)),
                "method_proposals_remaining": self._method_proposals_remaining(mission, new_mode),
                "root_repair_remaining": max(
                    0, self._config.max_root_review_repairs - self._root_review_repairs(mission.id)),
                "max_method_candidates": 12,
                "max_new_steps": int(getattr(world, "max_steps", 64)),
                "max_repair_actions": 1,
                "token_budget": remaining_tokens,
            },
        ))

    @staticmethod
    def _planning_decision_package_version(body: Mapping[str, Any]) -> int:
        """The package version a sealed request states, as the durable integer.

        Refuse malformed values and booleans instead of letting ``int()`` coerce or
        crash at the store boundary.
        """

        raw = body.get("package_version")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ContractError(f"unpairable planning package_version: {raw!r}")
        return raw

    def _bind_hierarchical_planning_request(
        self,
        *,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        package: Any,
        template: Any,
        request_id: str | None = None,
    ) -> None:
        """Persist the exact request facts a decision will be admitted against.

        The package is already sealed before the intent is created.  The intent id is
        therefore the durable request identity; replaying an idempotent intent can
        safely replay this insert as well.  Every hash is derived from the same
        package/template/world snapshot sent to the provider; no empty context is
        substituted for an unavailable field.
        """

        from ..planning.htn.planner_package import (
            package_hash,
            subject_bindings_hash,
            visible_refs_digest,
        )
        from ..storage.planning_decision_store import PlanningDecisionStore
        from .planning_protocol_binding import current_planning_protocol

        stored = current_planning_protocol(self.store, mission.id)
        body = package.package
        protocol = body.get("planning_protocol")
        if (
            not isinstance(protocol, Mapping)
            or protocol.get("protocol") != stored["protocol_version"]
        ):
            raise ContractError(
                "planning package is missing its durable protocol binding"
            )
        epochs = new_mode.scope_epochs(mission.id)
        latest = new_mode.semantics().latest_requirements_revision(mission.id)
        binding = PlanningRequestBinding(
            request_id=intent.intent_id if request_id is None else request_id,
            mission_id=mission.id,
            protocol_version=stored["protocol_version"],
            package_version=self._planning_decision_package_version(body),
            package_hash=package_hash(body),
            base_plan_revision=int(body["views"]["plans"][0]["plan_revision"]),
            requirements_revision=0 if latest is None else int(latest.revision),
            scope_epoch_digest=planning_scope_digest(epochs),
            subject_bindings_hash=subject_bindings_hash(body.get("planning_subjects", ())),
            visible_refs_digest=visible_refs_digest(body.get("visible_refs", ())),
            prompt_version=template.prompt_version,
            prompt_hash=sha256_hex(template.instructions),
            created_at=intent.created_at,
            intent_id=intent.intent_id,
        )
        decisions = PlanningDecisionStore(self.store)
        if request_id is None or request_id == intent.intent_id:
            decisions.insert_planning_request(binding)
            return
        existing = decisions.get_planning_request(request_id)
        if existing is None:
            raise ContractError(
                f"planning request {request_id!r} is not persisted; retry cannot rebind"
            )
        # The retry may only move the answering intent.  Every request fact is
        # frozen from the opener, including created_at and all package/prompt hashes.
        candidate = replace(
            binding,
            request_id=existing.request_id,
            intent_id=existing.intent_id,
            created_at=existing.created_at,
        )
        if candidate != existing:
            raise ContractError(f"planning request {request_id!r} changed while retry was pending")
        try:
            decisions.rebind_planning_request_intent(
                request_id=request_id,
                expected_opener_intent_id=existing.intent_id,
                retry_intent_id=intent.intent_id,
            )
        except StoreConflict as error:
            raise ContractError(str(error)) from error

    def _planning_request_retry_id(
        self, *, intent: DispatchIntent, mission: Mission, new_mode: HierarchicalDispatch
    ) -> str | None:
        """Find the opener request for the one permitted unreadable format retry."""

        return self._planning_request_retry_id_for_ordinal(
            mission=mission,
            new_mode=new_mode,
            ordinal=int(intent.config.get("ordinal", 1)),
        )

    def _planning_request_retry_id_for_ordinal(
        self, *, mission: Mission, new_mode: HierarchicalDispatch, ordinal: int
    ) -> str | None:
        """Find the opener request before constructing a retry package.

        A format retry answers the same durable request.  The package therefore has to
        be rendered with the opener's ordinal; rendering the new attempt ordinal would
        change ``planning_attempt`` (and the package hash) before the request can be
        rebound, which is precisely the contract error this gate is meant to prevent.
        """

        del new_mode
        from ..contracts.planning_decisions import PlanningDecisionStatus
        from ..storage.planning_decision_store import PlanningDecisionStore

        existing = self.store.get_intent_for_subject(f"{mission.id}:planner:{ordinal}")
        if existing is not None and existing.config.get("planning_decision_attempt_ordinal") == 1:
            bound = PlanningDecisionStore(self.store).get_planning_request_for_intent(existing.intent_id)
            if bound is None:
                raise ContractError("format retry lost its original planning request")
            return bound.request_id
        if ordinal < 2:
            return None
        opener = self.store.get_intent_for_subject(f"{mission.id}:planner:{ordinal - 1}")
        if opener is None or self._planning_decision_attempt_ordinal(opener) != 0:
            return None
        decisions = PlanningDecisionStore(self.store)
        binding = decisions.get_planning_request(opener.intent_id)
        if binding is None:
            return None
        row = decisions.get_planning_decision_by_attempt(binding.request_id, 0)
        if row is None or row["status"] != str(PlanningDecisionStatus.UNREADABLE):
            return None
        return binding.request_id

    @staticmethod
    def _planning_decision_attempt_ordinal(intent: DispatchIntent) -> int:
        # New requests have their own 0/1 syntax ladder. Historical intents retain
        # their original global ordinal so existing decision IDs/replays never move.
        value = intent.config.get("planning_decision_attempt_ordinal")
        if value is None:
            return max(0, int(intent.config.get("ordinal", 1)) - 1)
        if type(value) is not int or value not in (0, 1):
            raise ContractError("invalid request-local planning attempt ordinal")
        return value

    def _planning_format_retry_remaining(self, *, intent: DispatchIntent, mission: Mission) -> int:
        """The same-request format retries left (one per request)."""

        return max(0, 1 - self._planning_decision_attempt_ordinal(intent))

    def _format_retry_exhausted(
        self, *, intent: DispatchIntent, reason: str, mission: Mission
    ) -> bool:
        return (
            reason == "proposal_unreadable"
            and self._planning_format_retry_remaining(intent=intent, mission=mission) == 0
        )

    def _hierarchical_admission_context(
        self,
        *,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        raw_text: str,
        include_plan_sources: bool = True,
    ) -> Any:
        """Assemble admission from the request snapshot and the live HTN world."""

        from ..contracts.htn import MethodRef, MethodRegistryStatus
        from ..contracts.planning_decisions import (
            ENABLED_DECISIONS,
            PlanningRefKind,
            PlanningRefV1,
        )
        from ..governance.planning_authorization import (
            planning_policy_for_mission,
            StorePlanningAuthorityReader,
            build_planning_authorization,
        )
        from ..governance.planning_authorization import (
            SourceUnavailable as AuthoritySourceUnavailable,
        )
        from ..orchestrator.plan_commits import PlanPrincipal
        from .assurance_point_use import planning_evidence_stale
        from ..planning.decision_admission import (
            AdmissionContext,
            AuthorizationView,
            BudgetView,
            CapabilityView,
            MethodInstanceView,
            MethodView,
            OperationStateView,
            PlanningRetryBudgetView,
            PlanShapeView,
        )
        from ..planning.decision_codec import allocate_decision_id, hash_raw_output
        from ..planning.htn.registry import iter_predicates
        from ..runtime.planning_operations import (
            SourceUnavailable as OperationSourceUnavailable,
        )
        from ..runtime.planning_operations import (
            StoreOperationReader,
            build_operation_snapshot,
        )
        from ..storage.planning_admission_store import PlanningAdmissionStore
        from ..storage.planning_decision_store import PlanningDecisionStore
        from .planning_protocol_binding import current_planning_protocol

        body = intent.config.get("planning_package")
        if not isinstance(body, Mapping):
            raise ContractError("planning intent has no sealed planning package")
        decision_store = PlanningDecisionStore(self.store)
        request = decision_store.get_planning_request_for_intent(intent.intent_id)
        if request is None:
            raise ContractError(f"planning request {intent.intent_id!r} is not persisted")
        current_planning_protocol(self.store, mission.id)

        from ..planning.htn.planner_package import (
            package_hash,
        )

        network = new_mode.network(mission.id)
        world = new_mode.require_planning_world()
        operation_snapshot = None
        # Authority and operation producers are read through one deferred view.  The
        # readers themselves also use ``read_view``; Store joins that nested view to
        # this connection, so the two snapshots cannot straddle a concurrent write.
        with self.store.read_view():
            from .planning_runtime_block import resume_source_current
            if not resume_source_current(self, intent, mission):
                return AuthoritySourceUnavailable(
                    "runtime_resume_binding", "runtime wake or its authoritative sources changed"
                )
            try:
                request_authority_binding = PlanningAdmissionStore(self.store).get_request_binding(
                    request.request_id
                )
            except (sqlite3.Error, StoreError, json.JSONDecodeError):
                return AuthoritySourceUnavailable(
                    "request_authority_binding", "authoritative source could not be read"
                )
            bound_planner = (
                str(request_authority_binding.get("planner_principal_id"))
                if isinstance(request_authority_binding, Mapping)
                and request_authority_binding.get("planner_principal_id")
                else (intent.agent_id or self._owner)
            )
            authority = build_planning_authorization(
                request.request_id,
                read=StorePlanningAuthorityReader(PlanningAdmissionStore(self.store), self.store),
                caller=PlanPrincipal(
                    # The authority is bound to the durable planning request, not
                    # to the executor lease assigned to a particular retry intent.
                    # Reusing ``intent.agent_id`` here made a valid format retry fail
                    # closed as a different planner principal after a worker handoff.
                    principal_id=bound_planner,
                    scope_id="mission",
                ),
                policy=planning_policy_for_mission(self.store, mission.id),
                now_ms=int(self.store.now * 1000),
            )
            if isinstance(authority, AuthoritySourceUnavailable):
                return authority
            if include_plan_sources:
                try:
                    operation_snapshot = build_operation_snapshot(
                        mission.id,
                        reader=StoreOperationReader(self.store),
                        now_ms=int(self.store.now * 1000),
                    )
                except OperationSourceUnavailable as error:
                    return AuthoritySourceUnavailable("operations", error.reason)
        subjects = tuple(dict(item) for item in body.get("planning_subjects", ()))
        visible_refs = tuple(PlanningRefV1.from_json(item) for item in body.get("visible_refs", ()))
        subject_by_occurrence = {
            str(item.get("occurrence_id")): str(item.get("subject_key")) for item in subjects
        }
        signature_by_subject = {
            subject_by_occurrence[str(spec.occurrence_id)]: str(
                network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id
            )
            for spec in network.occurrences
            if str(spec.occurrence_id) in subject_by_occurrence
        }
        reports = new_mode.method_applicability(mission.id)
        report_by_method = {
            (
                str(item.goal_occurrence_id),
                str(getattr(item.method_ref, "method_id", "")),
                int(getattr(item.method_ref, "version", 0)),
                str(getattr(item.method_ref, "content_hash", "")),
            ): item.report
            for item in reports
        }
        methods: list[MethodView] = []
        for row in (body.get("views") or {}).get("methods", ()):
            if not isinstance(row, Mapping):
                continue
            raw_ref = row.get("method_ref")
            if not isinstance(raw_ref, Mapping):
                continue
            # the row quotes the method as a decision does: the reference quadruple
            ref = MethodRef(method_id=str(raw_ref["id"]), version=int(raw_ref["semantic_revision"]),
                            content_hash=str(raw_ref["content_hash"]))
            contract = world.registry.definition(ref)
            registration = world.registry.registration(ref)
            if contract is None or registration is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} is missing "
                    "from the live registry"
                )
            applies_to = tuple(
                sorted(
                    subject
                    for subject, signature in signature_by_subject.items()
                    if signature == str(row.get("goal_signature_id", ""))
                )
            )
            schema = world.schemas.resolve(contract.parameter_schema_ref)
            if schema is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} references "
                    f"an unavailable parameter schema {contract.parameter_schema_ref!s}"
                )
            required_parameters = tuple(
                sorted(str(getattr(field, "name")) for field in getattr(schema, "fields", ()))
            )
            predicates = tuple(
                sorted(
                    str(item.predicate_ref.id) for item in iter_predicates(contract.applicable_when)
                )
            )
            # several goals may share the method's type; the first one assessed against it
            # carries the report (a goal already refined is no longer assessed)
            report = next(
                (
                    found
                    for found in (
                        report_by_method.get(
                            (str(spec.occurrence_id), ref.method_id, ref.version, ref.content_hash)
                        )
                        for spec in network.occurrences
                        if str(spec.occurrence_id) in subject_by_occurrence
                        and subject_by_occurrence[str(spec.occurrence_id)] in applies_to
                    )
                    if found is not None
                ),
                None,
            )
            if report is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} has no "
                    "authoritative applicability report"
                )
            truth = report.truth
            authorization = report.authorization
            if authorization is None:
                raise ContractError(
                    f"planning package method {ref.method_id}@{ref.version} has no "
                    "authoritative authorization report"
                )
            methods.append(
                MethodView(
                    method_id=ref.method_id,
                    version=ref.version,
                    content_hash=ref.content_hash,
                    status=registration.status
                    if isinstance(registration.status, MethodRegistryStatus)
                    else MethodRegistryStatus(str(registration.status)),
                    applies_to=applies_to,
                    required_parameters=required_parameters,
                    predicate_keys=predicates,
                    required_capabilities=tuple(
                        str(item) for item in contract.required_capabilities
                    ),
                    # Match grounding/refinement: an absent precondition needs
                    # no evidence gate. Its vacuous TRUE still grants no authority.
                    requires_authorization=bool(contract.applicable_when),
                    authorization_granted=bool(authorization.allowed),
                    precondition_truth=truth,
                )
            )

        active_instances = tuple(
            MethodInstanceView(
                subject_key=subject_by_occurrence.get(str(item.effective_goal_occurrence_id), ""),
                instance_id=str(item.instance_id),
                semantic_revision=max(1, int(item.plan_revision)),
                # A method_instance PlanningRef identifies the grounding, so its
                # content hash is the instance parameters digest, not the method
                # definition's content hash.
                content_hash=item.parameters_digest(),
            )
            for item in network.method_instances
            if item.instance_id in set(network.adopted_instance_ids)
            and subject_by_occurrence.get(str(item.effective_goal_occurrence_id))
        )
        capability_snapshot = world.capabilities()
        capability_records = tuple(getattr(capability_snapshot, "records", ()) or ())
        available_capabilities = frozenset(
            str(item.capability_id)
            for item in capability_records
            if bool(getattr(item, "available", False))
        )
        available_predicates = frozenset(
            str(item.predicate_ref.id) for item in world.predicates.signatures()
        )
        protocol = body.get("planning_protocol")
        # 2026-09-25: the package lists decision types and repair kinds separately;
        # admission keys rows as ``REPAIR/<kind>``, so translate back here (once).
        from ..contracts.planning_decisions import internal_enablement_keys
        enabled = (
            internal_enablement_keys(
                protocol.get("enabled_decision_types", ()), protocol.get("enabled_repair_kinds", ())
            )
            if isinstance(protocol, Mapping)
            else frozenset()
        )
        enabled_effective = enabled or ENABLED_DECISIONS
        allowance = max(0, int(self._config.max_planning_attempts))
        remaining = max(0, allowance - self._planning_attempts(mission.id))
        epochs = new_mode.scope_epochs(mission.id)
        latest = new_mode.semantics().latest_requirements_revision(mission.id)
        current_package_hash = package_hash(body)
        prompt = self._hierarchical_planner_template(mission.id)
        from .planning_evidence import evidence_authority
        evidence_observers, evidence_authorized = evidence_authority(world, authority)
        from ..storage.obligation_store import ObligationStore
        from ..contracts.htn import ObligationId
        duties = ObligationStore(self.store)
        open_duties = {str(duty.obligation_id) for duty in duties.list_obligations(mission.id)
            if (account := duties.account(mission.id, ObligationId(str(duty.obligation_id)))).has_admitted_demand
            and str(account.lifecycle) == "UNSATISFIED"}
        raw_hash = hash_raw_output(raw_text.encode("utf-8"))
        ordinal = self._planning_decision_attempt_ordinal(intent)
        return AdmissionContext(
            binding=request,
            decision_id=allocate_decision_id(
                request_id=request.request_id,
                attempt_ordinal=ordinal,
                raw_output_hash=raw_hash,
            ),
            planning_subjects=subjects,
            visible_refs=visible_refs,
            plan_revision=int(network.plan_revision),
            requirements_revision=0 if latest is None else int(latest.revision),
            scope_epoch_digest=planning_scope_digest(epochs),
            package_version=self._planning_decision_package_version(body),
            package_hash=current_package_hash,
            prompt_version=prompt.prompt_version,
            prompt_hash=sha256_hex(prompt.instructions),
            enabled_decision_types=enabled_effective,
            repair_allowed="REPAIR/REPLACE_METHOD" in enabled_effective,
            methods=tuple(methods),
            predicates=available_predicates,
            evidence_observers=evidence_observers,
            evidence_authorized_predicates=evidence_authorized,
            active_method_instances=active_instances,
            open_obligations=tuple(
                ref for ref in visible_refs if ref.kind is PlanningRefKind.OBLIGATION
                and ref.id in open_duties
            ),
            authorization=AuthorizationView(
                approval_granted=all(
                    item.authorization_granted for item in methods if item.requires_authorization
                ),
                planning_snapshot=authority,
            ),
            capabilities=CapabilityView(available=available_capabilities),
            budget=BudgetView(
                planning_rounds_remaining=remaining,
                bound_remaining=remaining,
                budget_available=mission.budget.max_tokens is None
                or int(mission.budget.max_tokens) > 0,
            ),
            operations=OperationStateView(
                unresolved_operations=()
                if operation_snapshot is None
                else operation_snapshot.unresolved,
                snapshot=operation_snapshot,
            ),
            plan_shape=PlanShapeView(),
            retry_budgets=PlanningRetryBudgetView(
                same_request_format_retries_remaining=self._planning_format_retry_remaining(
                    intent=intent, mission=mission
                ),
                planning_rounds_remaining=remaining,
                root_review_repairs_remaining=max(0, int(self._config.max_root_review_repairs)),
                repeated_failure_before_escalation_remaining=None,
            ),
            # 推后第 1 批 A26：这个请求签过 PLAN 证书的证据，此刻还当前吗（只核不落库）
            planning_evidence_stale=planning_evidence_stale(self.commit, mission.id, request.request_id),
        )

    def _hierarchical_planner_template(self, mission_id: str) -> Any:
        """The hierarchical Planner's prompt: there is one.

        A deployment's frozen ``prompt_versions`` pins ``planner`` to a DAG-Planner
        version for the other mode; it says nothing here.  A Mission whose durable
        binding names a different package/prompt pair is refused by
        ``current_planning_protocol``, not served on other words.
        """

        from .planning_protocol_binding import current_planning_protocol

        current_planning_protocol(self.store, mission_id)
        return PLANNER_HIERARCHICAL

    def _hierarchical_worker_template(self, role: Any, mission_id: str) -> Any:
        """The Worker prompt that knows about output ports.

        A deployment's frozen ``prompt_versions`` pins ``worker`` to a DAG-mode version,
        and ``worker-v3`` never asks the model which port its files belong to — so the
        accept side would refuse every leaf for ``OUTPUT_PORT_UNCLAIMED``.  A role that
        already is a hierarchical Worker (a domain's own) is kept; anything else is a
        choice made for the other mode and is replaced by the domain's hierarchical
        Worker.
        """

        from ..runtime.role_templates import (
            hierarchical_worker_for_domain,
            hierarchical_worker_versions,
        )

        if role.name != "worker":
            return role
        if role.prompt_version in hierarchical_worker_versions():
            return role
        # P2.3d / defect D1: the fallback is the *domain's* hierarchical Worker, not a
        # constant.  Returning ``WORKER_HIERARCHICAL`` unconditionally threw away the
        # AppWorld template ``role_for_task`` had just selected and with it both
        # ``appworld_execute`` (``effective_tools`` walks ``role_tools``, so a tool the
        # role does not list is dropped however many other sets hold it) and the words
        # saying there is a simulated world — 20 of the Grok run's L1 episodes reported
        # ``tool_not_exposed`` and did nothing.  A domain that registers no hierarchical
        # Worker still falls back to the code-domain one.
        return hierarchical_worker_for_domain(self.commit.domain_for(mission_id))

    async def _create_planner_intent(self, mission_id: str, *, ordinal: int) -> DispatchIntent:
        # The synchronous core never yields. Keep semantic outcomes, task state,
        # package hashes, reservation and binding on one snapshot for normal asks
        # as well as WAIT wakeups (which already own an outer transaction).
        with self.store.transaction():
            return self._create_planner_intent_now(mission_id, ordinal=ordinal)

    def _create_planner_intent_now(self, mission_id: str, *, ordinal: int) -> DispatchIntent:
        """Build the package and persist its intent without an async suspension.

        WAIT wakeup joins these writes to its own transaction, including the request
        binding and budget reservation. No provider call is made here.
        """
        mission = self.store.get_mission(mission_id)
        assert mission is not None
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError("hierarchical_assembly_missing: no planner without the assembly")
        source_binding = self._active_source_binding(mission_id)
        retry_request_id = self._planning_request_retry_id_for_ordinal(
            mission=mission, new_mode=new_mode, ordinal=ordinal
        )
        retry_package_frozen = False
        from .planning_backend_runtime import bind_deployment
        bind_deployment(self, mission_id)
        # A format retry keeps the request identity and all request facts frozen;
        # use the opener's package ordinal so its package hash remains identical.
        original_intent = None if retry_request_id is None else self.store.get_intent(retry_request_id)
        if retry_request_id is not None and original_intent is None:
            raise ContractError("format retry opener is unavailable")
        package_ordinal = ordinal if original_intent is None else int(original_intent.config["ordinal"])
        package = self._hierarchical_planner_package(
            new_mode,
            mission,
            ordinal=package_ordinal,
            # A format retry answers the opener's frozen package (rehydrated below);
            # only a fresh request is told about the last refusal in its package.
            previous_feedback=(
                None if retry_request_id is not None
                else self._latest_planning_feedback(mission)
            ),
        )
        if retry_request_id is not None:
            # A format retry answers the opener's exact durable request.  The
            # rejection event and H3's durable selection ledger may change what a
            # fresh collector would render, so rehydrate the opener package and
            # provider message before rebinding the retry intent.
            from ..context.context_builder import TaskPackage
            from ..storage.planning_decision_store import PlanningDecisionStore

            opener_binding = PlanningDecisionStore(self.store).get_planning_request(
                retry_request_id
            )
            opener = (
                None
                if opener_binding is None
                else self.store.get_intent(opener_binding.intent_id)
            )
            opener_package = None if opener is None else opener.config.get("planning_package")
            opener_message = None if opener is None else opener.config.get("message")
            if (
                opener is not None
                and isinstance(opener_package, Mapping)
                and isinstance(opener_message, Mapping)
                and isinstance(opener_message.get("content"), str)
            ):
                package = TaskPackage(
                    text=str(opener_message["content"]),
                    context_version=str(
                        opener.config.get("context_version", package.context_version)
                    ),
                    package=dict(opener_package),
                )
                retry_package_frozen = True
        template = self._hierarchical_planner_template(mission_id)
        decision = self._route_service("planner", mission_id)
        config = AgentConfig(
            name=f"planner-{ordinal}",
            instructions=template.instructions,
            model_profile_ref=decision.profile_id,
            tool_names=(),
            limits=AgentLimits(
                max_model_calls_per_turn=4,
                max_tool_calls_per_turn=1,
                turn_deadline_seconds=self._config.turn_deadline_seconds,
            ),
        )
        package_text = package.text
        if retry_package_frozen and retry_request_id is not None:
            # 2026-09-30 格式三件：同一请求的格式重试原来一字不差重发原消息，模型不知道错在
            # 哪、照样再错。包仍冻结不变（请求事实不动）；只在消息末尾附上上一次的字段路径反馈。
            from ..planning.decision_feedback import feedback_from_decision
            from ..storage.planning_decision_store import PlanningDecisionStore

            feedback = feedback_from_decision(
                PlanningDecisionStore(self.store).get_planning_decision_by_attempt(
                    retry_request_id, 0
                ),
                budgets=self._planning_retry_budgets(mission, format_retries=0),
            )
            if feedback is not None:
                package_text += (
                    "\n\n上一次回复被拒（这是同一个请求的格式重试，也是最后一次）。previous_feedback: "
                    + json.dumps(feedback.to_json(), ensure_ascii=False, sort_keys=True)
                    + "\n按 problems 里的 field_path 改正，重新输出完整的 <planning_decision> 块。"
                )
        from .planning_selection import infrastructure_retry
        native_decision = infrastructure_retry(package.package)
        message = user_message_json(package_text)
        subject = f"{mission_id}:planner:{ordinal}"
        from .planning_runtime_block import planner_binding
        intent = self.commit.create_service_intent(
            kind="plan",
            subject_id=subject,
            mission_id=mission_id,
            account_id=mission_account(mission_id),
            creation_key=subject,
            input_id="attempt-input",
            input_hash=sha256_hex(message),
            config={
                "agent_config": config.to_json(),
                "message": message,
                "context_version": package.context_version,
                "prompt_version": template.prompt_version,
                "base_version": mission.version,
                "ordinal": ordinal,
                "planning_decision_attempt_ordinal": 1 if retry_request_id is not None else 0,
                **planner_binding(self.store, mission_id),
                **({"native_planning_decision": native_decision} if native_decision is not None else {}),
                "planning_package": dict(package.package),
                **source_binding,
                **self._service_config(decision),
            },
            reservation=self._reservation(0 if native_decision is not None else self._config.planner_reserve_tokens),
        )
        self._bind_hierarchical_planning_request(
            intent=intent,
            mission=mission,
            new_mode=new_mode,
            package=package,
            template=template,
            request_id=retry_request_id,
        )
        if retry_request_id is None:
            # 推后第 1 批 A26：把知识与核对过的摘要当事实交给规划器，就是一次 PLAN 使用——同一事务里
            # 签证书；签不出（证据此刻不当前、时钟不可信、授权变了）这一轮不开，回滚后下一轮重来。
            # 格式重试沿用开头那次请求冻结的包，它的回复照样在准入时复核这张证书的证据。
            from .assurance_point_use import (
                PLANNING_REQUEST_CONSUMER,
                planning_claims,
                require_point_use_locked,
            )
            require_point_use_locked(
                self.commit, mission_id=mission_id, purpose="PLAN",
                consumer_kind=PLANNING_REQUEST_CONSUMER, consumer_id=intent.intent_id,
                claims=planning_claims(package.package), subject=("requirements", mission_id))
        self._planning_evidence_waits.pop(mission_id, None)
        return intent

    # -------------------------------------------------------------- dispatch
    def _reservation(self, tokens: int) -> Reservation:
        return Reservation(tokens=tokens)

    def _first_critic_reservation(self, budget: FirstRequestBudget) -> Reservation:
        return Reservation(tokens=budget.minimum_tokens)

    async def _dispatch(self, intent: DispatchIntent) -> bool:
        """ORCH §4.3 steps 2–3 with the identity frozen in the intent (D5')."""

        self._require_assurance_execution_root()
        from .assurance_review_transport import require_review_handoff
        from ..assurance.codec import AssuranceError

        try:
            require_review_handoff(self.commit, intent)
        except AssuranceError as error:
            self._note(f"intent {intent.intent_id}: Assurance handoff waits ({error.code})")
            return False
        mission = self.store.get_mission(intent.mission_id)
        if mission is not None and self._assembly_missing(mission, at="dispatch"):
            return False
        from .planning_runtime_block import blocks_intent, resume_source_current
        if blocks_intent(self.store, intent):
            return False
        if intent.kind == "plan" and mission is not None and not resume_source_current(self, intent, mission):
            return False
        from ..storage.planning_human_store import PlanningHumanStore
        if intent.kind != "critic" and PlanningHumanStore(self.store).pending(intent.mission_id):
            # Existing handed-off work is collected normally; do not start another
            # plan or Worker while a blocking human question is pending.
            return False
        if intent.kind == "critic" and self._critic_subject_stopped(intent):
            return await self._collect_stopped_critic(intent)
        from ..storage.store import StoreError
        try:
            with self.store.read_view():
                attempt = self.store.get_attempt(intent.subject_id) if intent.kind == "attempt" else None
                # Terminal cleanup below does no handoff. Do not strand its
                # original intent/accounting behind the newly installed fence.
                if attempt is None or attempt.status not in TERMINAL_ATTEMPT:
                    self.commit.require_taskgraph_handoff(intent)
        except StoreError:
            self._note(f"intent {intent.intent_id}: TaskGraph handoff is currently blocked")
            return False
        from .planning_selection import awaits_authority
        if awaits_authority(self.store, intent):
            return False
        if intent.config.get("native_planning_decision") is None and self._pool_missing(intent):
            return False
        self._context_profile_for(intent.config)
        claimed = self.commit.claim_intent(
            intent.intent_id, owner=self._owner, lease_seconds=self._config.lease_seconds
        )
        if claimed is None:
            return False
        config = claimed.config
        if config.get("native_planning_decision") is not None:
            from .planning_selection import dispatch_local
            return await dispatch_local(self, claimed)
        if claimed.kind == "attempt":
            attempt = self.store.get_attempt(claimed.subject_id)
            assert attempt is not None
            if attempt.status in TERMINAL_ATTEMPT:  # cancelled / superseded before it ran
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                return True
            try:
                self._bind_workspace(attempt)
            except ArtifactConflict as error:  # an upstream artifact file moved / changed
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                self.commit.mark_attempt_lost(attempt.id, reason="upstream_artifact_missing")
                self._commit_stop_task(
                    attempt.task_id,
                    stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                    detail={"error": str(error)},
                )
                await self._release_mission(attempt.mission_id)
                self._note(f"attempt {attempt.id}: upstream artifacts unusable → stopped")
                return True
        if claimed.state == "CLAIMED":
            try:
                require_review_handoff(self.commit, claimed)
            except AssuranceError as error:
                self._note(f"intent {claimed.intent_id}: Assurance create waits ({error.code})")
                return False
            from simple_harness.agents.arp.errors import ArpError

            try:
                agent_id, _run_id, _ = await self.bridge_for(claimed).create(
                    creation_key=claimed.creation_key, config_json=config["agent_config"], intent=claimed
                )
            except ArpError as error:
                # NEXT-TG-1.0 §10: a named refusal of the native plane (e.g. the Mission
                # sources moved) stops this intent only — never the loop every other
                # Mission runs on.  The intent stays claimed for its own recovery paths.
                if claimed.intent_id not in self._creation_refusals_noted:
                    self._creation_refusals_noted.add(claimed.intent_id)
                    self._note(f"intent {claimed.intent_id}: Agent creation refused ({error.code})")
                return False
            expected = await self.bridge_for(claimed).expected_turn_id(
                agent_id=agent_id, input_id=claimed.input_id
            )
            self._fault("after_agent_created", claimed.kind)
            claimed = self.commit.record_agent_created(
                claimed.intent_id, agent_id=agent_id, expected_turn_id=expected
            )
            if claimed.kind == "critic" or self._assured_review_intent(claimed):
                self._bind_critic(agent_id, config)
            elif claimed.kind == "attempt":
                self._bind_agent(agent_id, config)
        if claimed.state == "AGENT_CREATED":
            assert claimed.agent_id is not None
            try:
                require_review_handoff(self.commit, claimed)
            except AssuranceError as error:
                self._note(f"intent {claimed.intent_id}: Assurance submit waits ({error.code})")
                return False
            if claimed.kind == "critic" or self._assured_review_intent(claimed):
                if self._critic_subject_stopped(claimed):
                    return await self._collect_stopped_critic(claimed)
                self._bind_critic(claimed.agent_id, config)
            elif claimed.kind == "attempt":
                self._bind_agent(claimed.agent_id, config)
            try:
                with self.store.read_view():
                    self.commit.require_taskgraph_handoff(claimed)
            except StoreError:
                self._note(f"intent {claimed.intent_id}: TaskGraph submit is currently blocked")
                return False
            try:
                receipt = await self.bridge_for(claimed).submit(
                    agent_id=claimed.agent_id,
                    input_id=claimed.input_id,
                    message_json=config["message"],
                )
            except Exception as error:  # noqa: BLE001 - the created Agent is gone (P0-3)
                if claimed.kind != "attempt":
                    raise
                self._note(f"{claimed.subject_id}: created executor unreachable ({error})")
                self.commit.mark_attempt_lost(claimed.subject_id, reason="executor_agent_missing")
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                await self._release_attempt(claimed.subject_id, cancel=False)
                return True
            self._fault("after_submit", claimed.kind)
            self.commit.record_submitted(claimed.intent_id, receipt=receipt)
            self._note(f"dispatched {claimed.kind} {claimed.subject_id} → agent {claimed.agent_id}")
        return True

    def _upstream_inputs(self, attempt: Attempt) -> list[UpstreamInput]:
        context = self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)
        return list(context.upstream)

    def _active_source_binding(self, mission_id: str) -> dict[str, Any]:
        domain = self.commit.domain_for(mission_id)
        if not domain.source_roots:
            return {}
        from ..verification.evidence_resolver import in_source_roots
        # Only what the reader will accept is frozen: registered rows outside the
        # domain's source roots (e.g. Assurance review output) are not Mission sources.
        return {
            "source_versions": {
                row["path"]: row["version_hash"]
                for row in self.store.list_sources(mission_id, active_only=True)
                if in_source_roots(row["path"], domain.source_roots)
            },
            "source_roots": list(domain.source_roots),
        }

    def _frozen_source_binding(self, attempt: Attempt) -> dict[str, Any]:
        intent = self.store.get_intent_for_subject(attempt.id)
        if intent is None or "source_versions" not in intent.config:
            return {}  # Legacy intents never acquire today's source registry.
        return {
            "source_versions": dict(intent.config["source_versions"]),
            "source_roots": list(intent.config.get("source_roots", ())),
        }

    def _current_source_files(self, mission: Mission) -> dict[str, bytes]:
        """The Mission's registered sources at their current versions, read back from the
        CAS: what the judgment tree carries alongside the accepted outputs."""
        from ..verification.evidence_resolver import EvidenceResolver, in_source_roots

        roots = tuple(self.commit.domain_for(mission.id).source_roots)
        rows = [row for row in self.store.list_sources(mission.id, active_only=True)
                if roots and in_source_roots(str(row["path"]), roots)]
        if not rows:
            return {}
        self.validate_source_storage(mission.id)
        resolver = EvidenceResolver(self.store, self.assembled.workspaces.artifact_store)
        files: dict[str, bytes] = {}
        for row in rows:
            source = resolver.read_source(tenant_id=mission.tenant_id, mission_id=mission.id, path=str(row["path"]),
                                          version=str(row["version_hash"]), source_roots=roots)
            if source.status != "resolved" or source.data is None:
                raise ArtifactConflict(f"current source {row['path']} is {source.status}")
            files[str(row["path"])] = source.data
        return files

    def _source_files(self, attempt: Attempt) -> dict[str, bytes]:
        from ..verification.evidence_resolver import EvidenceResolver

        binding = self._frozen_source_binding(attempt)
        versions = binding.get("source_versions", {})
        if not versions:
            return {}
        self.validate_source_storage(attempt.mission_id)
        mission = self.store.get_mission(attempt.mission_id)
        assert mission is not None
        resolver = EvidenceResolver(self.store, self.assembled.workspaces.artifact_store)
        files: dict[str, bytes] = {}
        for path, version in versions.items():
            source = resolver.read_source(
                tenant_id=mission.tenant_id,
                mission_id=mission.id,
                path=path,
                version=version,
                source_roots=binding["source_roots"],
            )
            if source.status != "resolved" or source.data is None:
                raise ArtifactConflict(f"frozen source {path} is {source.status}")
            files[path] = source.data
        return files

    def _taskgraph_tool_handoff(self, binding: Any) -> None:
        self._require_assurance_execution_root()
        if binding.view != "work":
            return
        from ..storage.store import StoreError
        from ..storage.taskgraph_store import require_bound
        with self.store.read_view():
            attempt = self.store.get_attempt(binding.attempt_id)
            if attempt is None:
                raise StoreError("TASKGRAPH_TOOL_ATTEMPT_MISSING")
            require_bound(self.store, attempt.mission_id)
            if binding.mission_id is not None and binding.mission_id != attempt.mission_id:
                raise StoreError("TASKGRAPH_TOOL_MISSION_MISMATCH")
            intent = self.store.get_intent_for_subject(attempt.id)
            if intent is None or attempt.status in TERMINAL_ATTEMPT:
                raise StoreError("TASKGRAPH_TOOL_ATTEMPT_STOPPED")
            self.commit.require_taskgraph_handoff(intent)

    def _taskgraph_mount_rules(self, attempt: Attempt) -> Any:
        from ..artifacts.taskgraph_inputs import decode_target_rules
        self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)
        intent = self.store.get_intent_for_subject(attempt.id)
        if intent is None:
            raise ArtifactConflict("TASKGRAPH_MATERIAL_INTENT_MISSING")
        return decode_target_rules(intent.config["taskgraph_inputs"]["target_rules"])

    def _bind_workspace(self, attempt: Attempt) -> None:
        mission = self.store.get_mission(attempt.mission_id)
        assert mission is not None
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        previous = None
        if attempt.retry_of is not None:
            previous = self.assembled.workspaces.root / attempt.retry_of
        from .taskgraph_materialization import require_mounts, verify_materialized
        graph_rules = self._taskgraph_mount_rules(attempt)
        upstream = self._upstream_inputs(attempt)
        require_mounts(upstream, graph_rules)
        taskgraph_context = self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)
        from ..artifacts.versioning import manifest_upstream_inputs
        manifest_paths = {
            item.path
            for item in manifest_upstream_inputs(
                taskgraph_context.manifest, graph_rules, network=taskgraph_context.network
            )
        }
        inputs: dict[str, Path | bytes] = {}
        for item in upstream:
            artifact = self.store.get_artifact(item.artifact_id)
            try:  # P3.2 D3: the stored bytes, hash re-checked, never through a symlink
                if (
                    artifact is None
                    or artifact.mission_id != attempt.mission_id
                    or artifact.task_id != item.task_id
                    or artifact.content_hash != item.content_hash
                ):
                    raise ArtifactStoreError("missing", item.path)
                read_verified(artifact)
            except ArtifactStoreError as error:
                raise ArtifactConflict(
                    f"upstream artifact {item.artifact_id} ({item.path}) is missing or changed"
                ) from error
            # TaskGraph DATA is written below by materialise_v2, which derives the
            # exact declared paths from the frozen manifest. Overlay/Selection
            # material remains an explicit, separately verified input so accepted
            # producer files are not silently dropped from a real TaskGraph tree.
            if item.path not in manifest_paths:
                inputs[item.path] = Path(artifact.storage_uri)
        binding = self._frozen_source_binding(attempt)
        source_roots = binding.get("source_roots", ())
        source_files = self._source_files(attempt)
        task = self.store.get_task(attempt.task_id)
        require_mounts(upstream, graph_rules, supplementary=source_files)
        combined = [*upstream, *(UpstreamInput("source", path, sha256_hex_text(data), "source")
                    for path, data in source_files.items() if path not in {item.path for item in upstream})]
        require_mounts(combined, graph_rules)
        inputs.update(source_files)
        # P3.2 D4 (review round 2 P2-4): a rebind — recover() and every dispatch — is
        # checked against the registered identity, never the directory's content
        base = sha256_hex(
            {
                "seed": {path: sha256_hex_text(content) for path, content in seed.items()},
                "inputs": {item.path: item.content_hash for item in self._upstream_inputs(attempt)},
                "previous": attempt.retry_of,
                **self._frozen_source_binding(attempt),
            }
        )
        detail = {
            "path": attempt.id,
            "seed": sorted(seed),
            "read_only_inputs": sorted(self._read_only_inputs(attempt.id)),
            "bound_workspace_files": sorted(item.path for item in self._upstream_inputs(attempt)),
            "writable_outputs": [] if task is None else list(task.outputs),
            "adopted": False,
        }
        record = self.store.get_workspace(attempt.id)
        root_path = self.assembled.workspaces.root / attempt.id
        if root_path.is_symlink():  # code review round 1 P2-10: never adopt a link as a tree
            raise ArtifactConflict(
                f"workspace_identity_mismatch: {attempt.id} is a symlink, not a workspace"
            )
        exists = root_path.exists()
        building = False
        if record is None and exists:
            raise ArtifactConflict("TASKGRAPH_UNREGISTERED_WORKSPACE")
        elif record is None or record["state"] == "CREATING":
            if record is not None:  # a tree half-made by a crash is rebuilt
                self.assembled.workspaces.remove(attempt.id)
            self.store.register_workspace(
                attempt.id,
                kind="attempt",
                mission_id=attempt.mission_id,
                attempt_id=attempt.id,
                base_snapshot=base,
                state="CREATING",
                detail=detail,
            )
            building = True
        elif record["state"] != "ACTIVE" or record["base_snapshot"] != base:
            raise ArtifactConflict(
                f"workspace_identity_mismatch: {attempt.id} is registered "
                f"{record['state']} with another seed, inputs or repair source"
            )
        try:
            workspace = self.assembled.workspaces.create(
                attempt.id,
                seed=seed,
                previous=previous,
                inputs=inputs,
                replace_input_roots=source_roots,
            )
        except WorkspaceError as error:  # P3.2 D3: e.g. a symlink in the previous tree
            raise ArtifactConflict(str(error)) from error
        if building:
            from ..artifacts.versioning import materialise_v2

            context = taskgraph_context
            artifacts = {}
            for entry in context.manifest.bindings:
                artifact = self.store.get_artifact(entry.artifact_id)
                if artifact is None:
                    raise ArtifactConflict(
                        f"TaskGraph manifest artifact {entry.artifact_id} is unavailable"
                    )
                # The frozen manifest is an authorization record, not a lookup
                # hint.  Re-check the stored record's mission, producer task and
                # bytes before handing it to the materializer; otherwise a
                # recycled artifact id or a foreign-task record could cross the
                # DATA boundary during recovery.
                if (
                    artifact.mission_id != attempt.mission_id
                    or artifact.task_id != str(entry.producer_task_ref)
                    or artifact.content_hash != entry.content_hash
                ):
                    raise ArtifactConflict(
                        f"TaskGraph manifest artifact {entry.artifact_id} identity changed"
                    )
                artifacts[entry.artifact_id] = artifact
            try:
                materialise_v2(workspace, context.manifest, artifacts,
                               target_rules=graph_rules, network=context.network)
            except (ArtifactConflict, WorkspaceError) as error:
                raise ArtifactConflict(f"TaskGraph DATA materialisation failed: {error}") from error
        if not building:
            # Detect tampering before a protected-file refresh could overwrite it.
            verify_materialized(workspace.root,
                [item for item in upstream if task is None or item.path not in task.outputs])
        if task is not None:
            protected_files = self._protected_files(mission, task, attempt)
            require_mounts(upstream, graph_rules, supplementary={
                path: content if isinstance(content, bytes) else content.encode("utf-8")
                for path, content in protected_files.items()})
            for path, content in protected_files.items():
                if isinstance(content, bytes):
                    if not building:
                        continue  # Preserve ACTIVE-tree tamper evidence across recovery.
                    try:
                        current_bytes = read_nofollow(workspace.root / path)
                    except ArtifactStoreError:
                        current_bytes = None
                    if current_bytes != content:
                        workspace.write_bytes(path, content)
                elif (
                    workspace.read_text(path) != content
                    if (workspace.root / path).is_file()
                    else True
                ):
                    workspace.write_text(path, content)

        # A declared output may legitimately change after initial binding.
        # Recovery still verifies every protected input against its origin.
        verify_materialized(workspace.root, upstream if building else
            [item for item in upstream if task is None or item.path not in task.outputs])
        if building:
            self.store.set_workspace_state(attempt.id, "ACTIVE")

    def _register_copy(
        self, kind: str, name: str, *, mission_id: str, attempt_id: str, detail: dict[str, Any]
    ) -> None:
        """P3.2 D4: a verification copy or judgment tree, registered each time it is rebuilt
        (its identity is what it was rebuilt from)."""

        self.store.register_workspace(
            name,
            kind=kind,
            mission_id=mission_id,
            attempt_id=attempt_id,
            base_snapshot=sha256_hex(detail),
            state="ACTIVE",
            detail={"path": name, **detail},
        )

    def cleanup_workspaces(self) -> list[str]:
        """P3.2 D4: remove the directories of finished Missions once the retention period
        has passed.  The registry rows (now CLEANED) and the content-addressed Artifact
        bytes stay, so snapshots, artifact reads, replay and reconciliation still work."""

        cutoff = self.store.now - self._config.workspace_retention_seconds
        removed: list[str] = []
        for row in self.store.list_workspaces():
            if row["state"] == "CLEANED" or row["updated_at"] > cutoff:
                continue
            mission = self.store.get_mission(row["mission_id"])
            if mission is None or mission.status not in TERMINAL_MISSION:
                continue
            self.assembled.workspaces.remove(str(row["detail"].get("path", row["workspace_id"])))
            self.store.set_workspace_state(row["workspace_id"], "CLEANED")
            removed.append(row["workspace_id"])
        return removed

    def _input_files(self, attempt: Attempt) -> dict[str, bytes]:
        """P3.2 review round 2 P1-3: an Attempt's upstream inputs, read back from the
        store with their hashes re-checked — the verification copy is rebuilt from them."""

        files: dict[str, bytes] = {}
        for item in self._upstream_inputs(attempt):
            artifact = self.store.get_artifact(item.artifact_id)
            try:
                if artifact is None or artifact.content_hash != item.content_hash:
                    raise ArtifactStoreError("missing", item.path)
                files[item.path] = read_verified(artifact)
            except ArtifactStoreError as error:
                raise WorkspaceError(f"upstream input unreadable: {error}") from error
        rules = self._taskgraph_mount_rules(attempt)
        additional = self._source_files(attempt)
        if rules is not None:
            from .taskgraph_materialization import require_mounts
            require_mounts([UpstreamInput("frozen", path, sha256_hex_text(data), "frozen")
                            for path, data in files.items()], rules, supplementary=additional)
        files.update(additional)
        return files

    def _read_only_initial(self, attempt: Attempt) -> dict[str, str]:
        """Path → hash of seed ∪ overlay/upstream.

        Same set ``read_only_rewrites`` uses as ``initial`` and the gateway
        snapshot uses as ``read_only_existing``.  Retry copies of a previous
        Attempt's new outputs are not in this map.
        """

        mission = self.store.get_mission(attempt.mission_id)
        if mission is None:
            raise WorkspaceError("read-only snapshot: mission missing")
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        upstream = self._upstream_inputs(attempt)
        allowed = set(read_only_existing_paths(seed, (item.path for item in upstream)))
        initial: dict[str, str] = {}
        for path, content in seed.items():
            if path in allowed:
                initial[path] = sha256_hex_text(content)
        for item in upstream:
            if item.path in allowed:
                initial[item.path] = item.content_hash
        return initial

    def _schedule_read_only_kept_writing_stop(self, attempt: Attempt) -> None:
        """End the Attempt after consecutive existing-file writes (P2.3u P2-2)."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._stop_read_only_leaf_kept_writing(attempt.id))

    async def _stop_read_only_leaf_kept_writing(self, attempt_id: str) -> None:
        attempt = self.store.get_attempt(attempt_id)
        if attempt is None or attempt.status in TERMINAL_ATTEMPT:
            return
        intent = self.store.get_intent_for_subject(attempt_id)
        if intent is not None:
            await self._cancel_turn(intent)
        attempt = self.store.get_attempt(attempt_id)
        if attempt is None or attempt.status in TERMINAL_ATTEMPT:
            return
        self.commit.mark_attempt_lost(attempt_id, reason="read_only_leaf_kept_writing")
        if intent is not None:
            self._settle_intent(intent, "FAILED")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt_id, cancel=True)
        self._note(f"attempt {attempt_id}: read-only leaf kept writing → LOST")

    def _bind_agent(self, agent_id: str, config: Mapping[str, Any]) -> None:
        cap = config.get("max_tool_calls")
        context_profile = self._context_profile_for(config)
        attempt = self.store.get_attempt(str(config["attempt_id"]))
        if attempt is None:
            raise ContractError("Cannot bind an unknown Attempt")
        if self.commit.domain_for(attempt.mission_id).id == "appworld-v1":
            self.assembled.gateway.bind_appworld(attempt.mission_id)
        read_only_existing: tuple[str, ...] = ()
        read_only_writes_blocked = False
        if config.get("read_only_leaf"):
            # P2.3u: seed ∪ overlay/upstream, the same set ``read_only_rewrites``
            # uses as ``initial``.  A retry copy of the previous Attempt's REPORT.md
            # is not in this snapshot, so the leaf can update its own report.
            try:
                read_only_existing = tuple(sorted(self._read_only_initial(attempt)))
            except WorkspaceError:
                read_only_writes_blocked = True
        self.assembled.gateway.bind(
            agent_id,
            WorkspaceBinding(
                str(config["attempt_id"]),
                "work",
                True,
                tuple(config.get("allowed_tools", WORKER_TOOLS)),
                tuple(str(p) for p in config.get("untrusted_sources", ())),
                max_tool_calls=None if cap is None else int(cap),
                mission_id=attempt.mission_id,
                protected=self._read_only_inputs(str(config["attempt_id"])),
                protected_prefixes=tuple(config.get("source_roots", ())),
                denied_prefixes=self._config.deployment_policy.denied_path_prefixes,
                context_policy=context_profile.context_policy,
                tokenizer=context_profile.tokenizer,
                read_only_existing=read_only_existing,
                read_only_writes_blocked=read_only_writes_blocked,
            ),
        )

    def _bind_critic(self, agent_id: str, config: Mapping[str, Any]) -> None:
        if config.get("assurance_protocol") == "assurance-exec-v1.1":
            # Frozen initial materials use the Assurance disclosure gate. Never
            # inherit the legacy verify-workspace tool authority implicitly; the
            # assured runtime binds exactly its read-only evidence tools (§4).
            if not config.get("agent_config", {}).get("tool_names"):
                return
            if self._assurance_reviews is None:
                raise ContractError("Assurance evidence tool binding is not installed")
            self._assurance_reviews.evidence_tools.bind(agent_id, config)
            return
        context_profile = self._context_profile_for(config)
        self.assembled.gateway.bind(
            agent_id,
            WorkspaceBinding(
                str(config["attempt_id"]),
                "verify",
                False,
                tuple(t for t in CRITIC_TOOLS if t in self._config.deployment_policy.allowed_tools),
                tuple(str(p) for p in config.get("untrusted_sources", ())),
                protected_prefixes=tuple(config.get("source_roots", ())),
                denied_prefixes=self._config.deployment_policy.denied_path_prefixes,
                context_policy=context_profile.context_policy,
                tokenizer=context_profile.tokenizer,
            ),
        )

    # ------------------------------------------------------------ knowledge (step 4)
    def _gather_knowledge(
        self,
        mission: Mission,
        task: Task,
        tasks_by_id: Mapping[str, Task],
    ) -> KnowledgeContext:
        """§10 items 4/5/7 for one Task: ranked Verified Knowledge (read back in full),
        the disputed claims (marked), the candidate / rejected claims for the templates
        that may see them, and the checked step summaries.  Raises
        ``RetrievalUnavailable`` instead of pretending the Mission has no knowledge."""

        if not self._config.knowledge_sharing:
            return KnowledgeContext.unavailable("knowledge_sharing disabled", status="disabled")
        try:
            self._fault("retrieval_unavailable", "attempt")
        except InjectedCrash as error:
            raise RetrievalUnavailable(str(error)) from error
        try:
            # 过时的知识不推给任何人：是否当前只在 knowledge_standing 一处判定（已取代的
            # 照旧交给排序，它会列进"已被取代"名单）
            records = [record for record in self.store.list_knowledge(mission.id)
                       if not knowledge_standing(self.store, record).startswith(KNOWLEDGE_STALE)]
            claims = self.store.list_mission_claims(mission.id)
            disputes = disputed_claims(claims, mission_id=mission.id)
        except (StoreBusy, OSError, ValueError) as error:  # index unreadable / not ready
            raise RetrievalUnavailable(str(error)) from error
        ranked = rank_knowledge(
            task,
            records,
            tasks_by_id=tasks_by_id,
            limit=self._config.max_knowledge_items,
        )
        by_id = {record.id: record for record in records}
        visible_records = [by_id[item.id] for item in ranked.items]
        scored = {item.id: item for item in ranked.items}
        return KnowledgeContext(
            retrieval=ranked,
            verified=tuple(knowledge_view(record, scored[record.id]) for record in visible_records),
            disputed=tuple(disputes),
            candidates=tuple(
                candidate_claims(
                    claims,
                    mission_id=mission.id,
                    statuses=(
                        ClaimStatus.PROPOSED,
                        ClaimStatus.UNDER_REVIEW,
                        ClaimStatus.SUPPORTED,
                    ),
                )
            ),
            rejected=tuple(
                candidate_claims(claims, mission_id=mission.id, statuses=(ClaimStatus.REJECTED,))
            ),
            step_summaries=tuple(row for row in step_summaries(self.store, mission.id)
                                 if row["source_task"] != task.id)[:MAX_PUSHED_SUMMARIES],
        )


    # --------------------------------------------------------------- collect
    async def _collect(self, intent: DispatchIntent) -> bool:
        assert intent.agent_id is not None and intent.expected_turn_id is not None
        if self._pool_missing(intent):
            return False
        try:
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
        except Exception as error:  # noqa: BLE001 - AgentNotFound & co.: not alive (P0-3)
            self._note(f"{intent.subject_id}: executor unreachable ({error})")
            result = None
        if result is None:
            return await self._observe_liveness(intent)
        self._note_turn_health(intent, result)
        self._fault("after_turn_committed", intent.kind)
        if intent.config.get("assurance_protocol") == "assurance-exec-v1.1":
            from .assurance_review_collect import collect_assurance_review
            await collect_assurance_review(self, intent)
            self.assembled.gateway.unbind(intent.agent_id)
        elif intent.kind == "plan":
            await self._collect_plan(intent, result)
        elif intent.kind == "attempt":
            await self._collect_attempt(intent, result)
        return True

    def _critic_subject_stopped(self, intent: DispatchIntent) -> bool:
        """Terminal ownership is global; a merely changed lease owner is not a stop."""

        mission = self.store.get_mission(intent.mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return True
        attempt_id = intent.config.get("attempt_id")
        attempt = self.store.get_attempt(attempt_id) if isinstance(attempt_id, str) else None
        if attempt is None:  # Mission judge has a view id, not a worker Attempt.
            return False
        if intent.config.get("carried_requirements_revision") is not None:
            # 第四批：重审已验收的结果——原尝试早已结束，看任务是否还在现行计划里
            from .assurance_review_import import carried_review_alive

            return not carried_review_alive(self.store, intent.mission_id, attempt.task_id)
        task = self.store.get_task(attempt.task_id)
        return attempt.status in TERMINAL_ATTEMPT or task is None or task.status in TERMINAL_TASK

    async def _collect_stopped_critic(self, intent: DispatchIntent) -> bool:
        # AGENT_CREATED can already have a real SDK turn after a lost submit
        # receipt. Inspect that exact turn; never submit again merely to find it.
        if intent.agent_id is not None and intent.expected_turn_id is not None:
            return await self._collect_after_stop(intent)
        self._settle_intent(intent, "FAILED")
        self._settle_service_if_known(intent.subject_id, intent.mission_id)
        return True

    async def _collect_after_stop(self, intent: DispatchIntent) -> bool:
        """A turn still running for a terminal Mission (D3-6'): import its usage when
        it settles, keep a committed late result as history, settle the reservation
        and the intent; never plan, verify or accept anything for it."""

        assert intent.agent_id is not None and intent.expected_turn_id is not None
        try:
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
        except Exception:  # noqa: BLE001 - executor gone: nothing more to collect
            result = None
            liveness = Liveness(False, None, False, None, None, False)
        else:
            liveness = (
                Liveness(True, None, False, None, None, True)
                if result is not None
                else await self.bridge_for(intent).liveness(
                    agent_id=intent.agent_id, turn_id=intent.expected_turn_id
                )
            )
        if result is None and liveness.alive:
            if intent.kind == "attempt":
                await self._release_attempt(intent.subject_id, cancel=True)
            else:
                self.assembled.gateway.unbind(intent.agent_id)
                key = f"service:{intent.subject_id}:cancel"
                if key not in self._released:
                    self._released.add(key)
                    await self._cancel_turn(intent)
                # 阶段 B 裁决第 6 类: a service turn of a stopped Mission that never comes
                # back (its model call ignores the cancel) is closed after the service
                # bound — nothing more can be decided for it; a late usage record is
                # still imported by the accounting scan and an unknown charge keeps its
                # reservation.  Without this the loop waited on it for ever.
                since = self._service_blocked_since.setdefault(key, self.store.now)
                if self.store.now - since >= self._service_blocker_limit:
                    self._service_blocked_since.pop(key, None)
                    self._import_usage(intent)
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, intent.mission_id)
                    self._note(f"{intent.subject_id}: closed after the Mission stopped (turn never came back)")
                    return True
            return False
        if result is None:
            from .taskgraph_runtime_imports import TaskGraphRuntimeImports
            from ..runtime.planning_operations import SourceUnavailable
            try:
                source = TaskGraphRuntimeImports(self).read_subject(intent)
                self.commit.import_usage(intent.subject_id, intent.mission_id, source.usage)
            except (SourceUnavailable, BudgetError, ContractError, ValueError):
                return False  # retain its original late-result collection identity
            if not source.accounting_complete or not source.physical_settled:
                return False
        if (result is not None
                and intent.config.get("assurance_protocol") == "assurance-exec-v1.1"):
            from .assurance_review_collect import collect_assurance_review
            await collect_assurance_review(self, intent)
            self.assembled.gateway.unbind(intent.agent_id)
            return True
        self._import_usage(intent)
        if intent.kind == "attempt":
            attempt = self.store.get_attempt(intent.subject_id)
            assert attempt is not None
            if result is not None and attempt.status in {
                AttemptStatus.SUPERSEDED,
                AttemptStatus.CANCELLED,
            }:
                self._record_late_result(attempt, result)
            # The intent closes first: settlement needs the dispatch closed (阶段 B 裁决第 8 类)
            self._settle_intent(intent, "SETTLED" if result is not None else "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
        else:
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, intent.mission_id)
            self.assembled.gateway.unbind(intent.agent_id)
        self._note(f"{intent.subject_id}: collected after the Mission stopped")
        return True

    def _record_late_result(self, attempt: Attempt, result) -> None:  # type: ignore[no-untyped-def]
        text = "" if result.public_output is None else str(result.public_output.content)
        summary, late_paths = "", []
        try:
            raw = extract_block(text, RESULT_ENVELOPE_TAG)
            summary = str(raw.get("summary", ""))[:400]
            late_paths = [str(p) for p in raw.get("artifacts", [])]
        except BlockError:
            summary = text[:200] or f"turn {result.state}: {jsonable(result.error or {})}"[:200]
        self.commit.record_late_result(
            attempt.id, turn_id=result.turn_id, summary=summary, artifacts=late_paths
        )
        self._note(f"attempt {attempt.id}: late result recorded as history")

    async def _observe_liveness(self, intent: DispatchIntent) -> bool:
        assert intent.agent_id and intent.expected_turn_id
        liveness: Liveness = await self.bridge_for(intent).liveness(
            agent_id=intent.agent_id, turn_id=intent.expected_turn_id
        )
        if intent.kind == "plan" and self._assured_review_intent(intent):
            # A review call (method review, root final review, …) is timed like an
            # Attempt and ended as an interrupted call when it never comes back
            # (阶段 B 裁决第 6 类); a review turn is never a Planner round.
            return await self._end_overdue_review_call(intent, liveness)
        if intent.kind == "plan":
            if liveness.exists:
                # P2.3f: an existing turn is still "ours to wait for" — unless it is
                # waiting on a Provider hand-off nobody can resolve, which is the one
                # wait this loop ends itself (a re-hand-off, then the role's failure
                # door).  Everything else about a plan turn — slow, queued, mid-call —
                # is the executor's business and is not timed here.
                return (await self._resolve_provider_blocked_service(intent, liveness)) is not None
            self._import_usage(intent)
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, intent.mission_id)
            await self._planning_rejected(
                intent, reason="planner_turn_missing", detail={"agent_id": intent.agent_id, "turn_failed": True}
            )
            return True
        if intent.kind != "attempt":
            return False
        attempt = self.store.get_attempt(intent.subject_id)
        assert attempt is not None
        if attempt.status in TERMINAL_ATTEMPT:  # closed by a cascade while its turn ran
            if liveness.alive:
                await self._release_attempt(attempt.id, cancel=True)
                return False  # collected (cost, late result) once the turn settles
            self._import_usage(intent)
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            return True
        now = self.store.now
        if liveness.alive:
            try:
                # The commit deduplicates unchanged observations, but persists
                # blocker exits before testing their newly started stall window.
                attempt = self.commit.renew_lease(
                    attempt.id,
                    owner=self._owner,
                    lease_seconds=self._config.lease_seconds,
                    liveness=liveness.to_json(),
                    minimum_remaining_seconds=self._config.lease_seconds / 2,
                )
            except CommitRejected:
                return False  # another live owner; not ours yet (review P1-2)
            running = liveness.state == str(AgentTurnState.RUNNING)
            if running and not liveness.blocked and attempt.progress_at is not None:
                # D3-4': only a *running* turn is timed; queued / semaphore-waiting ones are not
                stalled_for = now - attempt.progress_at
                if stalled_for > self._config.stall_seconds:
                    # D6': alive, no blocker, no provider progress within stall_seconds.
                    self._import_usage(intent)
                    self.commit.mark_attempt_timed_out(
                        attempt.id,
                        reason="executor_stalled",
                        detail={
                            "stalled_seconds": round(stalled_for, 3),
                            "progress_marker": attempt.progress_marker,
                        },
                    )
                    self.commit.settle_intent(intent.intent_id, "FAILED")
                    await self._release_attempt(attempt.id, cancel=True)
                    self._note(
                        f"attempt {attempt.id} TIMED_OUT: no progress for {stalled_for:.1f}s"
                    )
                    return True
            # P2.3p: a Worker blocked on an after-handoff UNKNOWN must not sit until
            # the wall clock.  Reuses P2.3f's resolver (no extra ``_new_mode`` site);
            # attempt intents are never re-handed off.
            if self._provider_blocked(liveness):
                outcome = await self._resolve_provider_blocked_service(intent, liveness)
                return outcome is not None
            return False
        if not liveness.exists:
            self._import_usage(intent)
            self.commit.mark_attempt_lost(attempt.id, reason="executor_turn_missing")
            self.commit.settle_intent(intent.intent_id, "FAILED")
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id} LOST: turn missing")
            return True
        return False

    def _review_call_overdue(self, intent: DispatchIntent, liveness: Liveness) -> dict[str, Any] | None:
        """How long this review call has waited past its bound, or None while it may wait.

        A turn gone from this process (a restart) is overdue at once; one blocked on a
        provider hand-off nobody can resolve waits ``_service_blocker_limit``; a running
        turn that reports no provider progress waits the per-turn deadline (what a
        content review waits); a queued one is not timed.  The first look in a process counts from the
        intent's submission, so a restart does not reset the clock."""

        if liveness.exists and liveness.settled:
            return None  # the ordinary collector imports it next round
        now = self.store.now
        if not liveness.exists:
            shape: Any = ("missing",)
            limit: float | None = 0.0
        elif self._provider_blocked(liveness) or self._definite_auth_failure(liveness.blocker):
            shape, limit = ("blocked",), float(self._service_blocker_limit)
        elif liveness.state == str(AgentTurnState.RUNNING) and not liveness.blocked:
            # One model call reports no provider progress while it runs (thinking replies
            # took over 220 s on the desktop, 核验 2026-10-03), so a running review gets
            # the same per-turn deadline a content review waits — not the Attempt stall.
            shape, limit = ("running", liveness.progress), float(self._critic_wait)
        else:
            shape, limit = ("untimed",), None
        mark = self._review_call_marks.get(intent.intent_id)
        if mark is None:
            row = self.store.connection.execute(
                "SELECT created_at FROM events WHERE idempotency_key=?",
                ("InputSubmitted:" + self.commit._intent_event_key(intent),)).fetchone()
            since = now if row is None else float(row[0])
        else:
            since = mark[1] if mark[0] == shape else now
        self._review_call_marks[intent.intent_id] = (shape, since)
        if limit is None or now - since < limit:
            return None
        return {"waited_seconds": round(now - since, 3), "limit_seconds": limit, "shape": shape[0],
                "blocker": dict(liveness.blocker or {})}

    async def _end_overdue_review_call(self, intent: DispatchIntent, liveness: Liveness) -> bool:
        """True only when the overdue call was ended (a durable write); waiting is not progress."""

        overdue = self._review_call_overdue(intent, liveness)
        if overdue is None:
            return False
        if overdue["shape"] == "blocked" and self._provider_blocked(liveness):
            # 第 2 批 A27 (§6.2): before the call is written off and reopened in a fresh
            # session, its own call key is re-read.  A result that turned up is collected,
            # not re-asked (one more window); otherwise the record says what the check found
            # and which call the fresh session stands in for.
            from ..runtime.provider_budget_guard import resend_record
            check = self._unknown_handoff_check(intent)
            if check.verdict == "result_known" and intent.intent_id not in self._handoff_known_waits:
                self._handoff_known_waits.add(intent.intent_id)
                self._review_call_marks[intent.intent_id] = (("blocked",), self.store.now)
                self._note(f"{intent.subject_id}: the lost review call has a result; collecting instead of reopening")
                return False
            self._handoff_known_waits.discard(intent.intent_id)
            overdue = {**overdue, "handoff_check": check.to_json(),
                       "resend": resend_record(check, ordinal=None)}
        from .assurance_review_collect import abandon_assurance_review

        await abandon_assurance_review(self, intent, detail=overdue)
        self._review_call_marks.pop(intent.intent_id, None)
        self._note(f"{intent.subject_id}: review call ended as interrupted after "
                   f"{overdue['waited_seconds']}s ({overdue['shape']})")
        return True

    async def _cancel_turn(self, intent: DispatchIntent) -> None:
        """Best-effort cooperative cancel of a superseded SDK turn (never a kernel cancel)."""

        if intent.agent_id is None or intent.expected_turn_id is None:
            return
        try:
            receipt = await self.bridge_for(intent).runtime.cancel_turn(
                intent.agent_id,
                intent.expected_turn_id,
                command_id=f"{intent.subject_id}:cancel",
                wait_timeout=0.0,
            )
            self.cancel_receipts.append(
                {
                    "attempt_id": intent.subject_id,
                    "agent_id": receipt.agent_id,
                    "turn_id": receipt.turn_id,
                    "command_id": receipt.command_id,
                    "state": str(receipt.state),
                }
            )
        except Exception as error:  # noqa: BLE001 - cancellation is advisory here
            self._note(f"cancel_turn for {intent.subject_id} not applied: {error}")

    async def _release_attempt(self, attempt_id: str, *, cancel: bool) -> None:
        """D3-17: a terminal Attempt's executor loses its workspace binding at once (a
        zombie turn can no longer write) and, when asked, its SDK turn is cancelled."""

        intent = self.store.get_intent_for_subject(attempt_id)
        if intent is None or intent.agent_id is None:
            return
        self.assembled.gateway.unbind(intent.agent_id)
        key = f"{attempt_id}:{'cancel' if cancel else 'unbind'}"
        if key in self._released:
            return
        self._released.add(key)
        if cancel and intent.expected_turn_id is not None:
            try:
                liveness = await self.bridge_for(intent).liveness(
                    agent_id=intent.agent_id, turn_id=intent.expected_turn_id
                )
            except Exception:  # noqa: BLE001
                liveness = Liveness(False, None, False, None, None, False)
            if liveness.alive:
                await self._cancel_turn(intent)

    async def _release_mission(self, mission_id: str) -> None:
        """After a stop cascade: unbind and cancel every Attempt the cascade closed."""

        for task in self.store.list_tasks(mission_id):
            for attempt in self.store.list_attempts(task.id):
                if attempt.status in {AttemptStatus.CANCELLED, AttemptStatus.SUPERSEDED}:
                    await self._release_attempt(attempt.id, cancel=True)

    def _settle_intent(self, intent: DispatchIntent, state: str) -> None:
        self.commit.settle_intent(intent.intent_id, state)

    def _import_usage(self, intent: DispatchIntent) -> None:
        from ..storage.taskgraph_store import require_bound
        from .taskgraph_runtime_imports import TaskGraphRuntimeImports
        require_bound(self.store, intent.mission_id)
        # Read the latest control row but retain all original executor/input
        # identities. A service rehandoff cannot drop an older physical cost.
        current = self.store.get_intent(intent.intent_id)
        if current is None:
            raise BudgetError("ORIGINAL_ACCOUNTING_INTENT_MISSING")
        source = TaskGraphRuntimeImports(self).read_subject(current)
        self.commit.import_usage(current.subject_id, current.mission_id, source.usage)

    def _reimport_unsettled(self, mission: Mission) -> None:
        """D3-6': LOST / TIMED_OUT / SUPERSEDED / CANCELLED Attempts whose reservation is
        still open get their SDK usage imported again and settled when it is known."""

        for task in self.store.list_tasks(mission.id):
            for attempt in self.store.list_attempts(task.id):
                if attempt.status not in TERMINAL_ATTEMPT:
                    continue
                with self.store.transaction():
                    reservation = self.commit.ledger.reservation(attempt.id)
                if reservation is None or reservation["state"] == "SETTLED":
                    continue
                intent = self.store.get_intent_for_subject(attempt.id)
                if intent is not None and intent.config.get("provider_admission_fingerprint"):
                    # Guarded subjects use the all-Mission, original-binding
                    # accounting scanner after SDK recovery and on every cycle.
                    continue
                if intent is not None and intent.agent_id is not None:
                    try:
                        self._import_usage(intent)
                    except Exception as error:  # noqa: BLE001 - SDK ledger unreachable
                        self._note(f"attempt {attempt.id}: usage re-import failed ({error})")
                        continue
                self._settle_if_known(attempt)

    def _settle_service_if_known(
        self, subject_id: str, mission_id: str, task_id: str | None = None
    ) -> None:
        with self.store.transaction():
            unknown_imported = self.commit.ledger.imported_unknown_count(subject_id) > 0
            unknown = self.commit.ledger.has_unknown_usage(subject_id)
        if unknown_imported or unknown:
            self._note(f"{subject_id}: unknown provider charge, original reservation held")
            return
        try:
            self.commit.settle_subject(subject_id, mission_id, task_id=task_id)
        except BudgetError:
            self._note(f"{subject_id}: physical settlement pending, reservation held")

    #: The same result refused for the same reason this many rounds in a row is a
    #: deterministic refusal, not a race with another Commit.
    VERDICT_REFUSAL_LIMIT = 3

    def _verdict_refused(self, result_id: str, error: Exception) -> bool:
        """A dropped verdict is normally a race (the Attempt was closed / taken over,
        a source moved) and the next round decides again.  2026-09-25 UI 全量点击: a
        gate that refuses the same result for the same reason every round re-verified
        it every 2-3 s forever while the UI said "running".  After
        ``VERDICT_REFUSAL_LIMIT`` identical refusals the result fails with the reason
        recorded, so the ordinary retry path takes over, visibly."""

        signature = f"{type(error).__name__}:{error}"
        previous = self._verdict_refusals.get(result_id)
        count = previous[1] + 1 if previous is not None and previous[0] == signature else 1
        if count < self.VERDICT_REFUSAL_LIMIT:
            self._verdict_refusals[result_id] = (signature, count)
            return True
        self._verdict_refusals.pop(result_id, None)
        failure = {
            "layer": "acceptance",
            "status": "FAIL",
            "summary": "acceptance refused repeatedly for the same reason",
            "detail": {"reason": "acceptance_refused", "error_type": type(error).__name__,
                       "error": str(error)[:2000], "refusals": count},
        }
        try:
            self.commit.fail_result(result_id, failures=[failure], owner=self._owner)
        except (CommitRejected, IllegalTransition) as late:
            self._note(f"result {result_id}: refusal fail dropped ({late})")
            return True
        self._note(f"result {result_id}: acceptance refused {count}x ({error}) -> FAIL")
        return True

    def _scope_carried(self, result_id: str) -> bool:
        """The plan moved while this result was verified, but its step's completion scope
        did not change in anything but the revision (TaskGraph 补全第 8a 条): the
        verification started under the old revision is dropped and the same result is
        verified again next round under the current scope — not set aside."""

        from .completion_inputs import load_completion_result_inputs
        from .operation_completion import OperationCompletionError

        stored = self.store.get_result(result_id)
        if stored is None:
            return False
        try:
            with self.store.read_view():
                frozen = load_completion_result_inputs(self.store, stored)
        except OperationCompletionError:
            return False
        return frozen.scope.plan_ref.revision != frozen.frozen.plan_revision

    async def _set_aside_stale(self, result_id: str) -> bool:
        """Archive a result whose completion scope went stale under it (see
        :meth:`CommitService.set_aside_result`) and free its executor."""

        updated = self.commit.set_aside_result(result_id, detail={"error": "completion_scope_stale"})
        if updated is None:
            return False
        self._settle_if_known(updated)
        await self._release_attempt(updated.id, cancel=False)
        self._note(f"result {result_id}: set aside, its completion scope is no longer current")
        return True

    def _settle_if_known(self, attempt: Attempt) -> None:
        """Settle the Attempt's reservation unless an UNKNOWN charge keeps it occupied (ORCH §12.2)."""

        with self.store.transaction():
            unknown = self.commit.ledger.has_unknown_usage(attempt.id)
        if unknown:
            self._note(f"attempt {attempt.id}: unknown provider charge, reservation held")
            self.commit.record_reservation_held(  # L3-3: visible and traceable, never auto-released
                attempt.id, attempt.mission_id, task_id=attempt.task_id, reason="unknown_usage"
            )
            return
        try:
            self.commit.settle_subject(attempt.id, attempt.mission_id, task_id=attempt.task_id)
        except BudgetError:
            # Assurance 1.1 (Host real-model run 4, 2026-09-23): an Attempt whose
            # physical/accounting responsibility is still open (a provider turn that
            # failed before any usage fact, an UNKNOWN charge) keeps its reservation,
            # visible and traceable, exactly like the service path above — it must
            # not crash the loop, which would re-raise on every later round.
            self.commit.record_reservation_held(attempt.id, attempt.mission_id,
                task_id=attempt.task_id, reason="assurance_settlement_pending")
            self._note(f"attempt {attempt.id}: settlement pending, reservation held")

    async def _planning_rejected(
        self, intent: DispatchIntent, *, reason: str, detail: Mapping[str, Any]
    ) -> None:
        """规划器的一次回答没被采纳之后，还能不能再问（规划预算，片 A 第 8 项）。

        此前这里是六条各自计数的阶梯（格式重试、回合重试、格式阶梯、规划阶梯、修复阶梯、
        "已有计划不判失败"），再叠上"合成成功多给一次"。现在只有两个数：

        * **服务故障宽限**（``PLANNER_TURN_FAILURE_GRACE``）：没拿到回复的回合（服务端报错、
          超时、被重启打断）不算答错，宽限内原样再问；超出即按"运行环境不可用"停。
        * **答错次数**（``max_planning_attempts``）：自上一次提交成功起，被拒的回答——读不懂、
          不被准入、提交被拒——累计到上限即停。同一请求的格式重试计入其中，不另开阶梯。

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
        streak = self._after_handoff_zero_streak.get(mission.id, 0)
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
                mission.id, reason="planner_turn_failures_exhausted",
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
                    mission.id, reason=reason,
                    detail={"attempts": ordinal, **dict(detail)}, stop_reason=stop,
                )
            elif owed:
                self._stop_planning_round(
                    mission.id,
                    reason="planning_attempts_exhausted",
                    detail={"attempts": ordinal, "rejected_rounds": self._planning_attempts(mission.id),
                            "last_reason": reason, **dict(detail)},
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
        from ..contracts.htn import TaskForm

        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return False
        return any(spec.form is TaskForm.COMPOUND
                   and network.adopted_instance_for(spec.occurrence_id) is None
                   for spec in network.occurrences)

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
        from .planning_repair_requests import record_request
        # This hook receives an original durable failure. It opens a request; it
        # must never invent RETRY_SAME_METHOD on behalf of a model.
        record_request(new_mode, mission.id, event_type=event_type,
            trigger_refs=(trigger_ref,), source_key=f"hook:{event_type}:{trigger_ref}:" + content_hash_of(dict(detail)),
            detail=dict(detail))

    @staticmethod
    def _definite_auth_failure(error: Any) -> bool:
        """HTTP 401/402-class provider refusals the runtime already settled FAILED."""

        return bool(_error_codes(error) & DEFINITE_AUTH_CODES)

    async def _collect_plan(self, intent: DispatchIntent, result) -> None:  # type: ignore[no-untyped-def]
        mission = self.store.get_mission(intent.mission_id)
        assert mission is not None
        self._import_usage(intent)
        if intent.config.get("assurance_protocol") == "assurance-exec-v1.1":
            from .assurance_review_collect import collect_assurance_review

            await collect_assurance_review(self, intent)
            self.assembled.gateway.unbind(intent.agent_id)
            return
        if result.state is AgentTurnState.COMMITTED:
            self._reset_after_handoff_unknown_streak(mission.id)
        new_mode = self._new_mode(mission)
        if result.state is not AgentTurnState.COMMITTED and self._definite_auth_failure(result.error):
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

    def submit_operation_intent(self, command: Any, *, tenant_id: str, principal: Any) -> dict[str, Any]:
        from .operation_runtime import ensure_operation_runtime

        ensure_operation_runtime(self)
        return self.commit.submit_operation_intent(command, tenant_id=tenant_id, principal=principal)

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
                if _turn_failed(event) and forgiven < PLANNER_TURN_FAILURE_GRACE:
                    forgiven += 1
                    continue
                if not refusal_charges_planner(_refusal_codes(event)):
                    continue  # 请求过期：规划器作答期间世界变了，不算它答错（错误码表那一列）
                count += 1
            elif event.type == "PlanningDecisionEvaluated" and event.payload.get("status") == "COMMITTED":
                count = 0
                forgiven = 0
        return count

    def _planner_turn_failure_forgiven(self, mission_id: str) -> bool:
        """The latest refusal was a turn that produced no reply and is inside the grace."""

        forgiven = 0
        latest_forgiven = False
        for event in self.store.list_events(mission_id):
            if event.type == "PlanningRejected":
                latest_forgiven = _turn_failed(event) and forgiven < PLANNER_TURN_FAILURE_GRACE
                forgiven += 1 if latest_forgiven else 0
            elif event.type == "PlanningDecisionEvaluated" and event.payload.get("status") == "COMMITTED":
                forgiven = 0
                latest_forgiven = False
        return latest_forgiven

    def _awaiting_retry_decision(self, mission_id: str, task: Any) -> bool:
        """An ACTIVE leaf whose latest attempt ended failed and that waits for the
        planner's retry decision: nothing of it is running."""
        from .planning_retry import retry_decision_required

        return task.status is TaskStatus.ACTIVE and retry_decision_required(self.store, mission_id, task.id)

    def _repair_still_owed(self, mission_id: str) -> bool:
        """A repair is still owed to this Mission: an unaddressed repair request, or a
        leaf waiting for a retry decision that only a planning round can give."""
        from .planning_repair_requests import pending_requests

        if pending_requests(self.store, mission_id):
            return True
        return any(self._awaiting_retry_decision(mission_id, task) for task in self.store.list_tasks(mission_id))

    def _planning_ladder_spent(self, mission_id: str) -> bool:
        """答错次数用完了：自上一次提交成功起，被拒的回答已到 ``max_planning_attempts``。"""

        return self._planning_attempts(mission_id) >= int(self._config.max_planning_attempts)

    def _method_proposals_remaining(self, mission: Mission, new_mode: HierarchicalDispatch) -> int:
        """How many more methods the Planner may propose for the goals still open."""

        from .planning_method_proposal import MAX_METHOD_PROPOSALS_PER_GOAL, proposals_for

        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError):
            return 0
        from ..contracts.htn import TaskForm

        open_goals = [str(spec.task_id) for spec in network.occurrences
                      if spec.form is TaskForm.COMPOUND
                      and network.adopted_instance_for(spec.occurrence_id) is None]
        return sum(max(0, MAX_METHOD_PROPOSALS_PER_GOAL - proposals_for(self.store, mission.id, goal))
                   for goal in open_goals)

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

    async def _collect_plan_decision(
        self,
        intent: DispatchIntent,
        result: Any,
        mission: Mission,
        text: str,
        new_mode: HierarchicalDispatch,
    ) -> None:
        """Evaluate one new-protocol reply and bridge executable decisions to HTN."""

        from ..contracts.htn import PlanProposal
        from ..contracts.planning_decisions import (
            PlanningDecisionRejectionCode,
            PlanningDecisionStatus,
            PlanningDecisionType,
        )
        from ..governance.planning_authorization import (
            SourceUnavailable as AuthoritySourceUnavailable,
        )
        from ..orchestrator.planning_admission_commits import PlanningCommitAdmission
        from ..planning.decision_adapter import (
            AdapterContext,
            adapt_admitted_decision,
            adapt_for_preview,
        )
        from ..planning.decision_admission import (
            AdmittedPlanningDecision,
            DecisionAdmissionContext,
            NoMutationDecision,
            PlanShapeView,
            PreAdmittedPlanningDecision,
            admit_planning_decision,
            pre_admit_planning_decision,
        )
        from ..planning.decision_codec import (
            PlanningDecisionCodecError,
            allocate_decision_id,
            canonical_decision_hash,
            canonical_decision_json,
            hash_raw_output,
            parse_planning_decision,
        )
        from ..planning.plan_preview import (
            CandidatePreview,
            PreviewInputs,
            PreviewUnavailable,
            _source_snapshot_payload,
        )
        from ..runtime.planning_operations import (
            SourceUnavailable as RuntimeSourceUnavailable,
        )
        from ..runtime.planning_operations import (
            StoreOperationReader,
            read_running_work,
        )
        from ..storage.planning_decision_store import PlanningDecisionStore

        del result

        reject_planning = self._planning_rejected

        def commit_rejection_code(value: object) -> str:
            """Map a commit refusal to the closed planning rejection enum.

            ``COMMIT_REJECTED`` is a lifecycle status, not a rejection code.  Keep the
            concrete guard reason when it is one of the H1 codes; otherwise record the
            stable internal fallback without making the durable row invalid.
            """

            try:
                return str(PlanningDecisionRejectionCode(str(value)))
            except ValueError:
                return str(PlanningDecisionRejectionCode.INTERNAL_CONTRACT_ERROR)

        store = PlanningDecisionStore(self.store)
        record_decision = store.record_planning_decision
        from .planning_protocol_binding import current_planning_protocol

        current_planning_protocol(self.store, mission.id)
        binding = store.get_planning_request_for_intent(intent.intent_id)
        if binding is None:
            raise ContractError(
                f"planning request for answering intent {intent.intent_id!r} is not persisted"
            )
        request_id = binding.request_id
        if binding.mission_id != mission.id or intent.mission_id != mission.id:
            raise StoreConflict("planning reply does not belong to this Mission")
        attempt_ordinal = self._planning_decision_attempt_ordinal(intent)
        raw_hash = hash_raw_output(text.encode("utf-8"))
        decision_id = allocate_decision_id(
            request_id=request_id,
            attempt_ordinal=attempt_ordinal,
            raw_output_hash=raw_hash,
        )

        existing = store.get_planning_decision_by_attempt(request_id, attempt_ordinal)
        if existing is not None:
            store._check_replay(existing, raw_hash=raw_hash, decision_id=decision_id)
            if existing["status"] in {
                str(PlanningDecisionStatus.UNREADABLE),
                str(PlanningDecisionStatus.REJECTED),
                str(PlanningDecisionStatus.COMMIT_REJECTED),
                str(PlanningDecisionStatus.NO_STATE_CHANGE),
                str(PlanningDecisionStatus.COMMITTED),
            }:
                # Internal collector replay consumes the already recorded outcome;
                # it neither grants fresh authority nor returns data to a new caller.
                # Public Commit receipt reads retain their own principal checks.
                # Re-admission would downgrade a committed result after revocation.
                return

        if existing is not None and existing["status"] == str(PlanningDecisionStatus.COMPILED):
            from ..graph.execution_contracts import PreviewBindingV1
            held = PreviewBindingV1.from_json(existing.get("detail", {}).get("taskgraph_preview"))
            if held.required_convergence_ids:
                if new_mode._taskgraph_preview is None or len(held.required_convergence_ids) != 1:
                    raise ContractError("SOURCE_UNAVAILABLE: TaskGraph continuation is not installed")
                row = self.store.connection.execute("SELECT state FROM taskgraph_convergence_jobs "
                    "WHERE mission_id=? AND job_id=?", (mission.id, held.required_convergence_ids[0])).fetchone()
                if row is not None and row["state"] in {"FENCED", "WAITING"}:
                    # The original reply is durable. Waiting resumes that reply
                    # from the convergence consumer, never by calling Planner.
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                    return

        # Preserve the exact reply before decoding it. A content-addressed write
        # precedes every new row; failure must not leave a fabricated/null raw ref.
        # Existing terminal receipts above remain immutable (no history backfill).
        raw_artifact_ref = self.assembled.workspaces.artifact_store.put_bytes(text.encode("utf-8"))
        canonical_hash = None
        detail: dict[str, Any]
        codes: Sequence[str]
        resuming_preview = existing is not None and existing["status"] in {
            str(PlanningDecisionStatus.ADMITTED),
            str(PlanningDecisionStatus.COMPILED),
        }
        refusal_status = (
            PlanningDecisionStatus.COMMIT_REJECTED
            if resuming_preview
            else PlanningDecisionStatus.REJECTED
        )

        def record_progress(status: PlanningDecisionStatus) -> None:
            # Rebuild all current producers on recovery, but never move the durable
            # lifecycle backwards merely to repeat its validation steps.
            stages = (
                PlanningDecisionStatus.DECODED,
                PlanningDecisionStatus.ADMITTED,
                PlanningDecisionStatus.COMPILED,
            )
            progress_detail: dict[str, Any] = {}
            if status is PlanningDecisionStatus.COMPILED and planning_commit_admission is not None:
                frozen = planning_commit_admission.taskgraph_candidate
                if frozen is not None:
                    progress_detail["taskgraph_preview"] = frozen.preview.to_json()
                    if (existing is not None and existing["status"] == str(PlanningDecisionStatus.COMPILED)
                            and existing.get("detail", {}).get("taskgraph_preview") != frozen.preview.to_json()):
                        if new_mode._taskgraph_preview is None:
                            raise ContractError("SOURCE_UNAVAILABLE: TaskGraph preview service missing")
                        new_mode._taskgraph_preview.resume_preview(
                            existing.get("detail", {}).get("taskgraph_preview"), frozen,
                            preview_command, source_principal)
            if existing is not None and stages.index(
                PlanningDecisionStatus(existing["status"])
            ) >= stages.index(status):
                return
            record_decision(
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                raw_artifact_ref=raw_artifact_ref,
                decision_id=decision_id,
                status=status,
                rejection_codes=(),
                detail=progress_detail,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )

        decoded_subject_key = ""
        # 2026-09-30：唯一的无损补齐（goal_type_ref 只缺一个字段且能唯一对上）；补了什么记进评估事件。
        autofilled: list[str] = []

        def evaluated(status: PlanningDecisionStatus, **payload: Any) -> None:
            from .planning_repair_requests import address_requests
            address_requests(self.store, mission.id, package=intent.config.get("planning_package"),
                decision_id=decision_id, decision_type=str(payload.get("decision_type") or ""),
                status=str(status), subject_key=decoded_subject_key)
            from .planning_runtime_block import resolve_after_decision
            resolve_after_decision(self, intent, status=str(status),
                decision_type=str(payload.get("decision_type") or ""), decision_id=decision_id)
            from .planning_selection import SYSTEM_RETRY_ORIGIN
            payload.setdefault("decision_origin", SYSTEM_RETRY_ORIGIN
                               if intent.config.get("native_planning_decision") is not None else "planner_reply")
            payload.setdefault("decision_type", None)
            payload.setdefault("rejection_codes", [])
            payload.setdefault("canonical_hash", canonical_hash)
            if autofilled:
                payload.setdefault("autofilled", list(autofilled))
            append_hierarchical_event(
                self.store,
                "PlanningDecisionEvaluated",
                mission.id,
                # 同一决定再评一次（另一次尝试或另一个结论）也要有自己的事件，不被同键吞掉（阶段 G）
                key=f"{decision_id}:{attempt_ordinal}:{status!s}",
                payload={
                    "request_id": request_id,
                    "decision_id": decision_id,
                    "attempt_ordinal": attempt_ordinal,
                    "raw_output_hash": raw_hash,
                    "status": str(status),
                    **payload,
                },
            )

        try:
            from ..planning.decision_feedback import package_filler

            decision = parse_planning_decision(
                text,
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                fill=package_filler(intent.config.get("planning_package"), autofilled),
            )
            canonical_json = canonical_decision_json(decision)
            canonical_hash = canonical_decision_hash(decision)
            decoded_subject_key = decision.subject_key
            phase_key = (
                f"REPAIR/{getattr(decision.payload, 'repair_kind', '')}"
                if decision.decision_type is PlanningDecisionType.REPAIR
                else str(decision.decision_type)
            )
            context = self._hierarchical_admission_context(
                intent=intent,
                mission=mission,
                new_mode=new_mode,
                raw_text=text,
                include_plan_sources=phase_key != "REPAIR/DECLARE_RUNTIME_BLOCKED"
                and decision.decision_type not in {
                    PlanningDecisionType.WAIT,
                    PlanningDecisionType.NO_CHANGE,
                    PlanningDecisionType.REQUEST_EVIDENCE,
                    PlanningDecisionType.REQUEST_HUMAN,
                    PlanningDecisionType.PROPOSE_METHOD,
                    PlanningDecisionType.READ_METHOD_LIBRARY,
                },
            )
        except PlanningDecisionCodecError as error:
            retry_remaining = self._planning_format_retry_remaining(intent=intent, mission=mission)
            record_decision(
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                raw_artifact_ref=raw_artifact_ref,
                decision_id=decision_id,
                status=PlanningDecisionStatus.UNREADABLE,
                rejection_codes=(str(error.code),),
                detail={"error": error.detail or str(error)},
            )
            evaluated(
                PlanningDecisionStatus.UNREADABLE,
                rejection_codes=[str(error.code)],
                detail=error.detail or str(error),
            )
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            await reject_planning(
                intent,
                reason="proposal_unreadable",
                detail={
                    "error": error.detail or str(error),
                    "format_retry_remaining": retry_remaining,
                },
            )
            return
        except ContractError as error:
            # A producer contract failure cannot be repaired by asking the model
            # to rewrite an already decoded reply, especially after COMPILED.
            status = (
                refusal_status if canonical_hash is not None else PlanningDecisionStatus.UNREADABLE
            )
            detail = {"error": refusal_text(error), "internal_contract_error": True}
            with self.store.transaction():
                record_decision(
                    request_id=request_id,
                    attempt_ordinal=attempt_ordinal,
                    raw_output_hash=raw_hash,
                    raw_artifact_ref=raw_artifact_ref,
                    decision_id=decision_id,
                    status=status,
                    rejection_codes=("INTERNAL_CONTRACT_ERROR",),
                    detail=detail,
                    canonical_json=canonical_json if canonical_hash is not None else None,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type)
                    if canonical_hash is not None
                    else None,
                )
                evaluated(
                    status,
                    rejection_codes=["INTERNAL_CONTRACT_ERROR"],
                    detail=detail,
                )
                self._settle_intent(intent, "FAILED")
                self._settle_service_if_known(intent.subject_id, mission.id)
                self.commit.record_planning_rejected(
                    mission.id,
                    ordinal=int(intent.config.get("ordinal", 1)),
                    reason="planning_source_unavailable",
                    detail=detail,
                )
            return

        if isinstance(context, AuthoritySourceUnavailable):
            # The model supplied a readable decision. A missing/unreadable authority
            # producer is not a formatting defect and another model turn cannot fix
            # it. Preserve the closed wire enum while retaining the typed source
            # diagnostic in the durable detail, without opening a format retry.
            code = commit_rejection_code(context.reason_code)
            detail = {
                "reason": context.reason_code,
                "source": context.source,
                "error": context.detail,
            }
            with self.store.transaction():
                record_decision(
                    request_id=request_id,
                    attempt_ordinal=attempt_ordinal,
                    raw_output_hash=raw_hash,
                    raw_artifact_ref=raw_artifact_ref,
                    decision_id=decision_id,
                    status=refusal_status,
                    rejection_codes=(code,),
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    refusal_status,
                    decision_type=str(decision.decision_type),
                    rejection_codes=[code],
                    detail=detail,
                )
                self._settle_intent(intent, "FAILED")
                self._settle_service_if_known(intent.subject_id, mission.id)
                self.commit.record_planning_rejected(
                    mission.id,
                    ordinal=int(intent.config.get("ordinal", 1)),
                    reason="planning_source_unavailable",
                    detail=detail,
                )
            return

        record_progress(PlanningDecisionStatus.DECODED)
        pre_admitted = pre_admit_planning_decision(
            decision, context=DecisionAdmissionContext(context,
                allow_convergence_preview=new_mode._taskgraph_preview is not None), for_repair_preview=True,
        )
        from ..contracts.planning_decisions import RepairRuntimeBlockedDecision
        if isinstance(pre_admitted, PreAdmittedPlanningDecision) and isinstance(decision.payload, RepairRuntimeBlockedDecision):
            from .planning_runtime_block import register_block
            try:
                with self.store.transaction():
                    current = self._hierarchical_admission_context(
                        intent=intent, mission=mission, new_mode=new_mode, raw_text=text,
                        include_plan_sources=False)
                    if isinstance(current, AuthoritySourceUnavailable):
                        raise ContractError("runtime-block authority is unavailable")
                    checked = pre_admit_planning_decision(decision, context=current)
                    if not isinstance(checked, PreAdmittedPlanningDecision):
                        raise ContractError("runtime-block request is no longer admitted")
                    detail = register_block(self, mission, payload=decision.payload,
                        package=intent.config.get("planning_package"), decision_id=decision_id,
                        canonical_hash=canonical_hash)
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.NO_STATE_CHANGE,
                        rejection_codes=(), detail=detail, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.NO_STATE_CHANGE, decision_type=str(decision.decision_type), detail=detail)
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
            except (ContractError, StoreError) as error:
                with self.store.transaction():
                    detail = {"error": str(error), "repair_kind": "DECLARE_RUNTIME_BLOCKED"}
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.REJECTED,
                        rejection_codes=("REPAIR_NOT_ALLOWED",), detail=detail, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.REJECTED, decision_type=str(decision.decision_type),
                              rejection_codes=["REPAIR_NOT_ALLOWED"], detail=detail)
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(intent, reason="proposal_not_grounded", detail=detail)
            return
        from ..contracts.planning_decisions import RepairRetrySameMethodDecision
        if isinstance(pre_admitted, PreAdmittedPlanningDecision) and isinstance(decision.payload, RepairRetrySameMethodDecision):
            from ..runtime.planning_operations import SourceUnavailable as RetrySourceUnavailable
            try:
                with self.store.transaction():
                    current = self._hierarchical_admission_context(
                        intent=intent, mission=mission, new_mode=new_mode, raw_text=text,
                        include_plan_sources=False)
                    if isinstance(current, AuthoritySourceUnavailable):
                        raise ContractError("retry authority is unavailable")
                    checked = pre_admit_planning_decision(decision, context=current)
                    if not isinstance(checked, PreAdmittedPlanningDecision):
                        raise ContractError("retry decision is no longer admitted")
                    from ..planning.htn.planner_package import attempt_is_indexed
                    if not attempt_is_indexed(intent.config.get("planning_package") or {},
                                              decision.payload.failed_attempt_id):
                        raise ContractError("retry Attempt was not visible in this frozen request")
                    detail = self.commit.authorize_planning_retry(
                        mission_id=mission.id, task_id=str(checked.subject["task_id"]),
                        payload=decision.payload, expected_plan_revision=current.plan_revision,
                        decision_id=decision_id, canonical_hash=canonical_hash, request_id=request_id)
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.COMMITTED,
                        rejection_codes=(), detail=detail, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.COMMITTED, decision_type=str(decision.decision_type), detail=detail)
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
            except (ContractError, StoreError, RetrySourceUnavailable) as error:
                with self.store.transaction():
                    detail = {"error": str(error), "repair_kind": "RETRY_SAME_METHOD"}
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.REJECTED,
                        rejection_codes=("REPAIR_NOT_ALLOWED",), detail=detail, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.REJECTED, decision_type=str(decision.decision_type),
                              rejection_codes=["REPAIR_NOT_ALLOWED"], detail=detail)
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(intent, reason="proposal_not_grounded", detail=detail)
            return
        if (isinstance(pre_admitted, PreAdmittedPlanningDecision)
            and decision.decision_type in {PlanningDecisionType.REQUEST_HUMAN, PlanningDecisionType.PROPOSE_METHOD,
                                           PlanningDecisionType.READ_METHOD_LIBRARY}):
            from .planning_method_proposal import (
                decode_proposal,
                persist_method,
                prepare_method,
                proposal_origin,
                read_library,
            )
            from ..contracts.planning_decisions import ReadMethodLibraryDecision
            from ..contracts.planning_decisions import RequestHumanDecision, ProposeMethodDecision
            prepared_method = None
            try:
                with self.store.transaction():
                    current = self._hierarchical_admission_context(
                        intent=intent, mission=mission, new_mode=new_mode, raw_text=text,
                        include_plan_sources=False)
                    if isinstance(current, AuthoritySourceUnavailable):
                        raise ContractError("planning service authority is unavailable")
                    checked = pre_admit_planning_decision(decision, context=current)
                    if not isinstance(checked, PreAdmittedPlanningDecision):
                        raise ContractError("planning service request is no longer admitted")
                    if isinstance(decision.payload, RequestHumanDecision):
                        question, service_detail = self._register_human_question(
                            mission, new_mode, decision_id=decision_id, subject_key=decision.subject_key,
                            payload=decision.payload, current=current,
                            next_ordinal=int(intent.config.get("ordinal", 1)) + 1, repair_context=None)
                        event_type = "PlanningHumanRequested"
                    elif isinstance(decision.payload, ReadMethodLibraryDecision):
                        # 只读（阶段 C3）：记一条读取事件，下一轮规划包带上原文
                        service_detail = read_library(new_mode, mission, decision.payload)
                        event_type = "PlanningLibraryRead"
                    else:
                        assert isinstance(decision.payload, ProposeMethodDecision)
                        prepared_method = prepare_method(new_mode, mission.id, decision.payload, checked.subject)
                        # 每个目标最多提几次是按这条事件里的目标任务数的，所有通道都要带。
                        # 来源（阶段 C3）：写这个做法时的类型目录哈希、它参照的全库做法
                        service_detail = {**persist_method(new_mode, *prepared_method),
                                          "subject_task_id": str(checked.subject["task_id"]),
                                          **proposal_origin(new_mode, mission.id,
                                                            decode_proposal(decision.payload))}
                        event_type = "PlanningMethodProposed"
                        # BW03: the admitted draft gets its independent METHOD_PLAN
                        # review on the round transport, authored by this intent.
                        from ..assurance.codec import AssuranceError
                        from ..assurance.refs import Pin as AssurancePin
                        if self._assurance_reviews is None:
                            raise ContractError("Assurance review builder is not installed for METHOD_PLAN")
                        method_reference = prepared_method[1].method_ref()
                        # 审阅以被规划的目标为归属任务；根目标在第一份计划前还没有任务行。
                        subject_spec = next(
                            spec for spec in new_mode.network(mission.id).occurrences
                            if str(spec.task_id) == str(checked.subject["task_id"]))
                        self.commit.materialise_planning_subject(mission.id, subject_spec)
                        try:
                            review = self._assurance_reviews.ensure_method_plan(
                                mission, task_id=str(checked.subject["task_id"]),
                                method_ref=AssurancePin(method_reference.method_id,
                                                        int(method_reference.version),
                                                        method_reference.content_hash),
                                producer_agent_ids=(str(intent.agent_id or ""),))
                        except AssuranceError as error:
                            raise _AssuranceReviewUnavailable("METHOD_PLAN", error) from error
                        service_detail = {**service_detail,
                                          "assurance_review_key": review.to_json()["review_key"]}
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.NO_STATE_CHANGE,
                        rejection_codes=(), detail=service_detail, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.NO_STATE_CHANGE,
                        decision_type=str(decision.decision_type), detail=service_detail)
                    append_hierarchical_event(self.store, event_type, mission.id, key=decision_id,
                        payload={"decision_id": decision_id, "next_ordinal": int(intent.config.get("ordinal", 1)) + 1, **service_detail})
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
            except (ContractError, StoreError) as error:
                # An admitted proposal whose independent METHOD_PLAN review cannot be
                # opened (no approved check policy, review runtime unavailable) is not
                # a malformed proposal: it waits for authorization, and the record
                # says so instead of blaming the Planner.
                code, reason, error_detail = "PARAMETER_INVALID", "proposal_not_grounded", {"error": str(error)}
                from .planning_method_proposal import MethodProposalRefused
                if isinstance(error, MethodProposalRefused):
                    # 片 A 第 5 项：被拒的做法草案把每条可修正的问题原样列出。
                    code = error.code
                    error_detail = {"error": "method proposal refused", "problems": error.feedback()}
                if isinstance(error, _AssuranceReviewUnavailable):
                    code, reason = "AUTHORIZATION_REQUIRED", "assurance_review_unavailable"
                    error_detail = {"error": str(error), "assurance_purpose": error.purpose,
                                    "assurance_error": error.code}
                    from .assurance_review_runtime import REVIEW_ROUTE_UNAVAILABLE
                    if error.code == REVIEW_ROUTE_UNAVAILABLE:
                        # A provider outage, not a wrong answer: it uses the service-failure
                        # grace, never the Planner's answer budget (user 2026-09-28).
                        error_detail["turn_failed"] = True
                with self.store.transaction():
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.REJECTED,
                        rejection_codes=(code,), detail=error_detail,
                        canonical_json=canonical_json, canonical_hash=canonical_hash,
                        decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.REJECTED, decision_type=str(decision.decision_type),
                        rejection_codes=[code], detail=error_detail)
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(intent, reason=reason, detail=error_detail)
                return
            if prepared_method is not None:
                _, contract, registration = prepared_method
                new_mode.require_planning_world().registry.restore(contract, registration)
            return
        if (isinstance(pre_admitted, PreAdmittedPlanningDecision)
            and decision.decision_type is PlanningDecisionType.REQUEST_EVIDENCE):
            from .planning_evidence import prepare_questions, observe_questions, persist_questions
            from ..contracts.planning_decisions import RequestEvidenceDecision
            assert isinstance(decision.payload, RequestEvidenceDecision)
            try:
                world = new_mode.require_planning_world()
                asks = prepare_questions(world, decision.payload, context.authorization.planning_snapshot)
                outcomes = observe_questions(world, asks, now_ms=int(self.store.now * 1000))
                with self.store.transaction():
                    current = self._hierarchical_admission_context(
                        intent=intent, mission=mission, new_mode=new_mode, raw_text=text,
                        include_plan_sources=False,
                    )
                    if isinstance(current, AuthoritySourceUnavailable):
                        raise ContractError("evidence authority is no longer available")
                    checked = pre_admit_planning_decision(decision, context=current)
                    if not isinstance(checked, PreAdmittedPlanningDecision):
                        raise ContractError("evidence request is no longer admitted")
                    if prepare_questions(world, decision.payload, current.authorization.planning_snapshot) != asks:
                        raise ContractError("evidence predicate or authority changed during observation")
                    evidence_result = persist_questions(new_mode.semantics(), mission.id, asks, outcomes,
                        scope_id=current.authorization.planning_snapshot.scope_id)
                    record_decision(
                        request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.NO_STATE_CHANGE,
                        rejection_codes=(), detail=evidence_result, canonical_json=canonical_json,
                        canonical_hash=canonical_hash, decision_type=str(decision.decision_type),
                    )
                    evaluated(PlanningDecisionStatus.NO_STATE_CHANGE,
                        decision_type=str(decision.decision_type), detail=evidence_result)
                    append_hierarchical_event(self.store, "PlanningEvidenceRecorded", mission.id,
                        key=decision_id, payload={"decision_id": decision_id, "next_ordinal": int(intent.config.get("ordinal", 1)) + 1, **evidence_result})
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
            except (ContractError, StoreError) as error:
                with self.store.transaction():
                    record_decision(request_id=request_id, attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash, raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id, status=PlanningDecisionStatus.REJECTED,
                        rejection_codes=("EVIDENCE_REQUIRED",), detail={"error": str(error)},
                        canonical_json=canonical_json, canonical_hash=canonical_hash,
                        decision_type=str(decision.decision_type))
                    evaluated(PlanningDecisionStatus.REJECTED, decision_type=str(decision.decision_type),
                        rejection_codes=["EVIDENCE_REQUIRED"], detail={"error": str(error)})
                    self._settle_intent(intent, "FAILED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(intent, reason="proposal_not_grounded", detail={"error": str(error)})
            return
        if isinstance(pre_admitted, NoMutationDecision):
            with self.store.transaction():
                waited = (
                    self._expand_planning_wait(mission, tuple(pre_admitted.wait_for))
                    if decision.decision_type is PlanningDecisionType.WAIT and pre_admitted.wait_for
                    else None
                )
                invalid_wait = decision.decision_type is PlanningDecisionType.WAIT and not waited
                status = (
                    PlanningDecisionStatus.REJECTED
                    if invalid_wait
                    else PlanningDecisionStatus.NO_STATE_CHANGE
                )
                codes = ("PARAMETER_INVALID",) if invalid_wait else ()
                detail = {"decision_type": str(decision.decision_type)}
                if invalid_wait:
                    detail["reason"] = (
                        "WAIT target has no matching active producer or completed authoritative record"
                    )
                    if any(ref.kind in {PlanningRefKind.METHOD_INSTANCE, PlanningRefKind.OBLIGATION}
                           for ref in pre_admitted.wait_for or ()):
                        detail["reason"] += (
                            " (a method instance or duty may be waited on only while a step under"
                            " it is running; no step under it is running)"
                        )
                record_decision(
                    request_id=request_id,
                    attempt_ordinal=attempt_ordinal,
                    raw_output_hash=raw_hash,
                    raw_artifact_ref=raw_artifact_ref,
                    decision_id=decision_id,
                    status=status,
                    rejection_codes=codes,
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    status,
                    decision_type=str(decision.decision_type),
                    rejection_codes=list(codes),
                    detail=detail,
                    **(
                        {"wait_for": [ref.to_json() for ref in pre_admitted.wait_for]}
                        if decision.decision_type is PlanningDecisionType.WAIT
                        else {}
                    ),
                )
                self._settle_intent(intent, "FAILED" if invalid_wait else "SETTLED")
                self._settle_service_if_known(intent.subject_id, mission.id)
                if decision.decision_type is PlanningDecisionType.WAIT and not invalid_wait:
                    append_hierarchical_event(
                        self.store,
                        "PlanningWaitRegistered",
                        mission.id,
                        key=decision_id,
                        payload={
                            "decision_id": decision_id,
                            "request_id": request_id,
                            "attempt_ordinal": attempt_ordinal,
                            "canonical_hash": canonical_hash,
                            "wait_for": [ref.to_json() for ref in waited or ()],
                            **(
                                {"requested_wait_for": [ref.to_json() for ref in pre_admitted.wait_for]}
                                if tuple(waited or ()) != tuple(pre_admitted.wait_for)
                                else {}
                            ),
                            "reason": pre_admitted.reason,
                        },
                    )
                    self.store.fault("planning_wait_before_registration_commit")
            if invalid_wait:
                await reject_planning(
                    intent,
                    reason="proposal_not_grounded",
                    detail={"rejection_codes": list(codes), **detail},
                )
                return
            return
        admitted: AdmittedPlanningDecision | Any
        preview_candidate: CandidatePreview | None = None
        planning_commit_admission: PlanningCommitAdmission | None = None
        if isinstance(pre_admitted, PreAdmittedPlanningDecision):
            adapter_context = AdapterContext.from_admission_context(context)
            proposal = adapt_for_preview(pre_admitted, context=adapter_context)
            if not isinstance(proposal, PlanProposal):
                raise ContractError("SOURCE_UNAVAILABLE: preview adapter returned no proposal")
            retiring_instance_ids = tuple(
                str(getattr(operation, "method_instance_id"))
                for operation in proposal.operations
                if getattr(operation, "method_instance_id", None) is not None
            )
            with self.store.read_view():
                context = self._hierarchical_admission_context(intent=intent, mission=mission,
                    new_mode=new_mode, raw_text=text, include_plan_sources=True)
                if isinstance(context, AuthoritySourceUnavailable):
                    raise ContractError("SOURCE_UNAVAILABLE: H1 plan source snapshot is unavailable")
                try:
                    runtime_work = read_running_work(
                        mission.id,
                        retiring_instance_ids,
                        # The planner intent being collected is itself SUBMITTED while
                        # this decision is previewed.  It is the request under commit,
                        # not sibling runtime work that needs convergence.
                        reader=StoreOperationReader(self.store, ignore_intent_ids=(intent.intent_id,)),
                    )
                except RuntimeSourceUnavailable as error:
                    raise ContractError(f"SOURCE_UNAVAILABLE: {error}") from error
                world = new_mode.require_planning_world()
                network = new_mode.network(mission.id)
                from ..storage.taskgraph_store import require_bound
                from .taskgraph_policy import read_installed_graph_policy
                from ..contracts.htn import GraphStructureBudget
                require_bound(self.store, mission.id)
                preview_budget = GraphStructureBudget.from_json(
                    read_installed_graph_policy(self.store, mission.id).to_json()["graph_structure_budget"])
                if new_mode._taskgraph_preview is None or context.authorization.planning_snapshot is None:
                    raise ContractError("SOURCE_UNAVAILABLE: TaskGraph preview assembly is missing")
                source_principal = PlanPrincipal(
                    principal_id=context.authorization.planning_snapshot.planner_principal_id,
                    scope_id="mission")
                taskgraph_sources = new_mode._taskgraph_preview.capture(request_id, decision_id, source_principal)
                from .repair_impact import read_repair_impact_indexes
                from ..contracts.htn import CancelBranchOperation, ProposeSuccessorOperation, RebindInputOperation
                graph_mutation = any(isinstance(op, (CancelBranchOperation, ProposeSuccessorOperation, RebindInputOperation))
                                     for op in proposal.operations)
                frozen_inputs = PreviewInputs(
                        decision_id=decision_id,
                        decision_hash=canonical_hash,
                        request_id=request_id,
                        source_plan_revision=int(network.plan_revision),
                        source_network_hash=sha256_hex(_source_snapshot_payload(network)),
                        proposal=proposal,
                        network=network,
                        registry=world.registry,
                        catalog=world.catalog,
                        schemas=world.schemas,
                        evidence=world.snapshot(),
                        predicates=world.predicates,
                        requirements_revision=context.requirements_revision,
                        budget=preview_budget,
                        taskgraph_contract=True,
                        sharing_entries=taskgraph_sources.sharing.entries,
                        system_identity_seed=self._owner,
                        now_ms=int(self.store.now * 1000),
                        capabilities=world.capabilities(),
                        runtime_work=runtime_work,
                        repair_impact=read_repair_impact_indexes(self.store, network, mission.id) if graph_mutation else None,
                        **self._preview_read_facts(mission.id),
                )
            preview = new_mode.preview_plan_proposal(proposal, inputs=frozen_inputs)
            if isinstance(preview, PreviewUnavailable):
                try:
                    preview_codes = tuple(
                        str(PlanningDecisionRejectionCode(code))
                        for code in preview.mapped_problems
                    ) or ("INTERNAL_CONTRACT_ERROR",)
                except (TypeError, ValueError):
                    # A producer with an unknown reason cannot extend the wire
                    # protocol through a durable decision or evaluation event.
                    preview_codes = ("INTERNAL_CONTRACT_ERROR",)
                detail = {
                    "reason": preview.reason,
                    "detail": preview.detail,
                    "mapped_problems": list(preview.mapped_problems),
                }
                record_decision(
                    request_id=request_id,
                    attempt_ordinal=attempt_ordinal,
                    raw_output_hash=raw_hash,
                    raw_artifact_ref=raw_artifact_ref,
                    decision_id=decision_id,
                    status=refusal_status,
                    rejection_codes=preview_codes,
                    detail=detail,
                    canonical_json=canonical_json,
                    canonical_hash=canonical_hash,
                    decision_type=str(decision.decision_type),
                )
                evaluated(
                    refusal_status,
                    decision_type=str(decision.decision_type),
                    rejection_codes=list(preview_codes),
                    detail=detail,
                )
                self._settle_intent(intent, "FAILED")
                self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(
                    intent,
                    reason="proposal_not_grounded",
                    detail={"preview": detail},
                )
                return
            if not isinstance(preview, CandidatePreview):
                raise ContractError("SOURCE_UNAVAILABLE: preview did not return a typed candidate")
            preview_candidate = preview
            if context.authorization.planning_snapshot is None:
                raise ContractError(
                    "SOURCE_UNAVAILABLE: preview has no planning authority snapshot"
                )
            if context.operations.snapshot is None:
                raise ContractError("SOURCE_UNAVAILABLE: preview has no operation snapshot")
            from ..planning.decision_admission import _enablement_key
            decision_key = _enablement_key(decision)
            planning_commit_admission = PlanningCommitAdmission(
                request_id=request_id,
                decision_hash=canonical_hash,
                decision_key=decision_key,
                authority=context.authorization.planning_snapshot,
                operations=context.operations.snapshot,
                runtime_work=runtime_work,
                preview_request_id=preview.request_id,
                preview_decision_hash=preview.decision_hash,
                preview_compilation_hash=preview.compilation_hash,
                preview_read_set_hash=sha256_hex(preview.compilation.delta.read_set.to_json()),
            )
            preview_command = new_mode.build_command(mission.id, proposal, preview.compilation,
                principal=source_principal, command_id=f"plan:{intent.intent_id}",
                source={"intent_id": intent.intent_id, "agent_id": intent.agent_id,
                    "preview_compilation_hash": preview.compilation_hash,
                    "preview_source_snapshot_hash": preview.source_snapshot_hash})
            try:
                frozen_graph = new_mode._taskgraph_preview.freeze(preview_command, preview, taskgraph_sources)
            except SharingRefused as error:
                # 共用的秩序核对没过（规划器提示词里说的"TaskGraph 开头的原因代码"）：是这份决定的
                # 问题，退回规划器；不是库故障，原地重试多少次结果都一样（联测真机第三局）。
                # 按异常类型码判（错误码表 SharingRefusalCode → 规划器码），不读异常文字；其它
                # ContractError 不在这里接，照旧往上抛当库故障原地重试（第 1 批 T01）。
                codes = _sharing_refusal_codes(error)
                if codes is None:
                    raise
                detail = {"problems": [{"code": codes[0], "detail": str(error.code),
                                        "field_path": "/payload", "subject_ref": None}]}
                record_decision(
                    request_id=request_id, attempt_ordinal=attempt_ordinal, raw_output_hash=raw_hash,
                    raw_artifact_ref=raw_artifact_ref, decision_id=decision_id, status=refusal_status,
                    rejection_codes=codes, detail=detail, canonical_json=canonical_json,
                    canonical_hash=canonical_hash, decision_type=str(decision.decision_type))
                evaluated(refusal_status, decision_type=str(decision.decision_type),
                          rejection_codes=codes, detail=detail)
                self._settle_intent(intent, "FAILED")
                self._settle_service_if_known(intent.subject_id, mission.id)
                await reject_planning(intent, reason="proposal_not_grounded",
                                      detail={"rejection_codes": codes, **detail})
                return
            planning_commit_admission = replace(planning_commit_admission, taskgraph_candidate=frozen_graph)
            from ..graph.planning_scope import planning_convergence_scope
            context = replace(context, taskgraph_scope=planning_convergence_scope(
                taskgraph_sources.before, frozen_graph.document, context.operations.snapshot,
                taskgraph_sources.operation_producers))
            context = replace(
                context,
                plan_shape=PlanShapeView(
                    mapped_problems=tuple(
                        PlanningDecisionRejectionCode(code) for code in preview.mapped_problems
                    )
                ),
            )
        admitted = admit_planning_decision(decision, context=context)
        if not isinstance(admitted, AdmittedPlanningDecision):
            codes = [str(code) for code in admitted.rejection_codes]
            detail = {"problems": [item.to_json() for item in admitted.problems]}
            record_decision(
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                raw_artifact_ref=raw_artifact_ref,
                decision_id=decision_id,
                status=refusal_status,
                rejection_codes=codes,
                detail=detail,
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                refusal_status,
                decision_type=str(decision.decision_type),
                rejection_codes=codes,
                detail=detail,
            )
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            await reject_planning(
                intent,
                reason="proposal_not_grounded",
                detail={"rejection_codes": codes, **detail},
            )
            return

        outcome = adapt_admitted_decision(admitted, context=context)
        if outcome.durable_only is not None:
            record_decision(
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                raw_artifact_ref=raw_artifact_ref,
                decision_id=decision_id,
                status=PlanningDecisionStatus.NO_STATE_CHANGE,
                rejection_codes=(),
                detail={"decision_type": str(decision.decision_type)},
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                PlanningDecisionStatus.NO_STATE_CHANGE,
                decision_type=str(decision.decision_type),
            )
            self._settle_intent(intent, "SETTLED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            return

        assert outcome.proposal is not None
        record_progress(PlanningDecisionStatus.ADMITTED)
        try:
            record_progress(PlanningDecisionStatus.COMPILED)
            authority_snapshot = context.authorization.planning_snapshot
            principal = PlanPrincipal(
                # Commit must use the same durable authority identity that Admission
                # checked.  A retry's executor lease may have a different agent id;
                # using it here would turn an admitted decision into a false
                # ``caller is not the bound planner principal`` refusal.
                principal_id=(
                    authority_snapshot.planner_principal_id
                    if authority_snapshot is not None
                    else (intent.agent_id or self._owner)
                ),
                scope_id="mission",
            )
            if preview_candidate is None:
                # This branch is retained for durable-only/legacy adapters.  New
                # executable decisions must carry the exact preview compilation into
                # commit; silently recompiling would admit a different candidate.
                raise ContractError("SOURCE_UNAVAILABLE: final commit has no candidate preview")
            if planning_commit_admission is None:
                raise ContractError("SOURCE_UNAVAILABLE: final commit has no H1-H admission")
            if planning_commit_admission.taskgraph_candidate is not None:
                if new_mode._taskgraph_preview is None:
                    raise ContractError("SOURCE_UNAVAILABLE: TaskGraph convergence service missing")
                with self.store.transaction():
                    job = new_mode._taskgraph_preview.ensure_convergence(
                        preview_command, principal, planning_commit_admission.taskgraph_candidate)
                    if job is not None and job.state != "READY":
                        self._settle_intent(intent, "SETTLED")
                        self._settle_service_if_known(intent.subject_id, mission.id)
                        return
            from .planning_backend_runtime import bind_deployment
            bind_deployment(self, mission.id)
            solver_lane = None
            solver_snapshot = None
            solver_result = None
            if self._config.planning_backend is not None:
                from .planning_backend_runtime import solve_decision
                from ..planning.htn.backend_port import BackendStatus
                solver_lane = new_mode.solver_preview_lane(mission.id, outcome.proposal,
                    preview=preview_candidate, admission=planning_commit_admission,
                    principal=principal, command_id=(f"plan:{intent.intent_id}"
                        if planning_commit_admission.taskgraph_candidate is not None
                        else f"plan:{intent.intent_id}:solver"),
                    source={"intent_id": intent.intent_id, "agent_id": intent.agent_id})
                solver_snapshot, solver_result = await solve_decision(self, mission_id=mission.id,
                    decision_id=decision_id, lane=solver_lane)
                if solver_result.status is not BackendStatus.SOLVED:
                    raise ContractError(f"planning backend refused: {solver_result.status}: {solver_result.detail}")
            with self.store.transaction():
                if solver_lane is not None:
                    from ..planning.htn.backend_port import PlanningBackendBridge
                    from .hierarchical_dispatch import PlanRoundOutcome
                    assert solver_result is not None and solver_snapshot is not None
                    receipt = PlanningBackendBridge(solver_lane).commit(solver_result, snapshot=solver_snapshot)
                    plan_outcome = PlanRoundOutcome(proposal_id=outcome.proposal.proposal_id, receipt=receipt)
                else:
                    plan_outcome = new_mode.commit_preview_plan_proposal(
                        mission.id,
                        outcome.proposal,
                        preview=preview_candidate,
                        admission=planning_commit_admission,
                        principal=principal,
                        command_id=f"plan:{intent.intent_id}",
                        source={"intent_id": intent.intent_id, "agent_id": intent.agent_id},
                        owner=self._owner,
                        proposal_text=text,
                    )
                if plan_outcome.committed:
                    assert plan_outcome.receipt is not None
                    record_decision(
                        request_id=request_id,
                        attempt_ordinal=attempt_ordinal,
                        raw_output_hash=raw_hash,
                        raw_artifact_ref=raw_artifact_ref,
                        decision_id=decision_id,
                        status=PlanningDecisionStatus.COMMITTED,
                        rejection_codes=(),
                        detail={"plan_revision": plan_outcome.receipt.new_plan_revision},
                        canonical_json=canonical_json,
                        canonical_hash=canonical_hash,
                        decision_type=str(decision.decision_type),
                    )
                    evaluated(
                        PlanningDecisionStatus.COMMITTED,
                        decision_type=str(decision.decision_type),
                        plan_revision=plan_outcome.receipt.new_plan_revision,
                    )
                    self._blame_replaced_method(mission.id, decision, decision_id)
                    new_mode.advance_compound_phases(mission.id)
                    self._settle_intent(intent, "SETTLED")
                    self._settle_service_if_known(intent.subject_id, mission.id)
                    return
        except (GraphIntegrityError, ContractError, StoreConflict, PlanCommitRejected) as error:
            refusal_code = commit_rejection_code(
                getattr(error, "reason", "INTERNAL_CONTRACT_ERROR")
            )
            record_decision(
                request_id=request_id,
                attempt_ordinal=attempt_ordinal,
                raw_output_hash=raw_hash,
                raw_artifact_ref=raw_artifact_ref,
                decision_id=decision_id,
                status=PlanningDecisionStatus.COMMIT_REJECTED,
                rejection_codes=(refusal_code,),
                detail={"error": refusal_text(error)},
                canonical_json=canonical_json,
                canonical_hash=canonical_hash,
                decision_type=str(decision.decision_type),
            )
            evaluated(
                PlanningDecisionStatus.COMMIT_REJECTED,
                decision_type=str(decision.decision_type),
                rejection_codes=[refusal_code],
                detail=refusal_text(error),
            )
            self._release_refused_fence(mission.id, decision_id)
            self._settle_intent(intent, "FAILED")
            self._settle_service_if_known(intent.subject_id, mission.id)
            await reject_planning(
                intent,
                reason="proposal_not_grounded",
                detail={"error": refusal_text(error),
                        **_stale_commit_problems(getattr(error, "reason", ""))},
            )
            return

        refusals = [item.to_json() for item in plan_outcome.refusals]
        refusal_code = commit_rejection_code(
            plan_outcome.refusals[-1].reason if plan_outcome.refusals else "INTERNAL_CONTRACT_ERROR"
        )
        record_decision(
            request_id=request_id,
            attempt_ordinal=attempt_ordinal,
            raw_output_hash=raw_hash,
            raw_artifact_ref=raw_artifact_ref,
            decision_id=decision_id,
            status=PlanningDecisionStatus.COMMIT_REJECTED,
            rejection_codes=(refusal_code,),
            detail={"refusals": refusals},
            canonical_json=canonical_json,
            canonical_hash=canonical_hash,
            decision_type=str(decision.decision_type),
        )
        evaluated(
            PlanningDecisionStatus.COMMIT_REJECTED,
            decision_type=str(decision.decision_type),
            rejection_codes=[refusal_code],
            detail={"refusals": refusals},
        )
        self._release_refused_fence(mission.id, decision_id)
        self._settle_intent(intent, "FAILED")
        self._settle_service_if_known(intent.subject_id, mission.id)
        await reject_planning(
            intent,
            reason="proposal_not_grounded",
            detail={"refusals": refusals,
                    **_stale_commit_problems(plan_outcome.refusals[-1].reason if plan_outcome.refusals else "")},
        )

    def _release_refused_fence(self, mission_id: str, decision_id: str) -> None:
        """A decision whose commit was rejected never applies: its fence ends with it
        (阶段 B 裁决第 5 类), so the old plan's fenced steps can run and publish again."""

        if self._taskgraph_notifications is None:
            return  # no convergence service installed: no fence was ever raised
        with self.store.transaction():
            self._taskgraph_notifications.convergence.jobs.release_for_decision(
                mission_id, decision_id, reason="decision_refused", now_ms=int(self.store.now * 1000))

    async def _collect_attempt(self, intent: DispatchIntent, result) -> None:  # type: ignore[no-untyped-def]
        attempt = self.store.get_attempt(intent.subject_id)
        assert attempt is not None
        self._import_usage(intent)
        if result.state is AgentTurnState.COMMITTED:
            self._reset_after_handoff_unknown_streak(attempt.mission_id)
        if attempt.status is not AttemptStatus.RUNNING:
            closed = attempt.status in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}
            if closed:
                # D3-6': a late result on a closed Attempt is history, never a transition
                self._record_late_result(attempt, result)
            self._settle_intent(intent, "SETTLED")
            if closed:
                self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            return
        echoed = self.bridge_for(intent).echoed_models(agent_id=intent.agent_id or "")
        if echoed and echoed != {self._expected_model(intent)}:
            # D10': the provider answered as a different model; charges are unknown and
            # the deployment binding is wrong.  Fail fast and visibly, hold the reservation.
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="model_echo_mismatch",
                detail={"expected": self._expected_model(intent), "echoed": sorted(echoed)},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._commit_stop_task(
                attempt.task_id,
                stop_reason=MissionStopReason.MODEL_ECHO_MISMATCH,
                detail={"expected": self._expected_model(intent), "echoed": sorted(echoed)},
            )
            await self._release_mission(attempt.mission_id)
            self._note(f"attempt {attempt.id}: model echo mismatch {sorted(echoed)} → stopped")
            return
        if result.state is AgentTurnState.FAILED:
            error = result.error
            context_limit = (
                isinstance(error, Mapping)
                and error.get("error_code") == "context_required_content_too_large"
            )
            admission = (
                error.get("detail")
                if isinstance(error, Mapping)
                and error.get("error_code") == "provider_admission_denied"
                and error.get("source_kind") == "provider_admission"
                and error.get("retryable") is False
                else None
            )
            if not (
                isinstance(admission, Mapping)
                and type(admission.get("schema_version")) is int
                and admission.get("schema_version") == 1
            ):
                admission = None
            mission_now = self.store.get_mission(attempt.mission_id)
            if mission_now is not None and self._definite_auth_failure(error):
                self.commit.reject_result(
                    attempt.id,
                    turn_id=result.turn_id,
                    reason="turn_failed",
                    detail={
                        "error": jsonable(result.error or {}),
                        "error_kind": "provider_unavailable",
                    },
                )
                self._settle_intent(intent, "FAILED")
                self._settle_if_known(attempt)
                await self._release_attempt(attempt.id, cancel=False)
                self._commit_stop_task(
                    attempt.task_id,
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                    detail={
                        "source_kind": "provider",
                        "retryable": False,
                        "error": jsonable(error or {}),
                    },
                )
                await self._release_mission(attempt.mission_id)
                self._note(
                    f"attempt {attempt.id}: definite provider auth/payment failure → stopped"
                )
                return
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason=(
                    "context_required_content_too_large"
                    if context_limit
                    else "provider_admission_denied"
                    if admission is not None
                    else "turn_failed"
                ),
                detail={
                    "error": jsonable(result.error or {}),
                    "error_kind": classify_turn_error(result.error),  # D6-4' classification
                },
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            if context_limit:
                self._commit_stop_task(
                    attempt.task_id,
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                    detail={
                        "source_kind": "runtime_context",
                        "retryable": False,
                        "error": jsonable(error),
                    },
                )
                await self._release_mission(attempt.mission_id)
                self._note(f"attempt {attempt.id}: required context exceeds limit → stopped")
                return
            if admission is not None:
                reason = admission.get("reason_code")
                if reason == "usage_unresolved":
                    self.commit.wait_for_admission_usage(attempt.id)
                    self._note(f"attempt {attempt.id}: admission waits for usage; no new Attempt")
                    return
                if reason == "lease_lost":
                    # 2026-10-02 真机：强杀后重启，这一轮的执行权还记在旧进程名下。被打断，
                    # 不是这一步做不了：原地重做（不扣次数，同一步合计有上限）。
                    self._note(f"attempt {attempt.id}: executor lost its lease → RETRY_WAIT")
                    return
                if reason == "cancelled":
                    self._commit_cancel_mission(attempt.mission_id)
                elif reason == "deadline" and await self._runtime_exhausted(
                    self.store.get_mission(attempt.mission_id), ()
                ):
                    # 2026-09-29：排队到点往往就是任务总时限到了：按任务总时限停（带用时明细），
                    # 不再和主循环的时限检查抢先后。
                    return
                else:
                    # Runtime/deadline is the existing budget time-cap category.
                    # Other admission failures retain their exact configuration /
                    # authority cause; they never enter provider health fallback.
                    stop = (
                        MissionStopReason.BUDGET_EXHAUSTED
                        if reason in {"budget_exhausted", "deadline"}
                        else MissionStopReason.RUNTIME_UNAVAILABLE
                    )
                    self._commit_stop_task(
                        attempt.task_id,
                        stop_reason=stop,
                        detail={
                            "source_kind": "provider_admission",
                            "retryable": False,
                            "admission": dict(admission),
                            **({"dimension": "runtime"} if reason == "deadline" else {}),
                        },
                    )
                await self._release_mission(attempt.mission_id)
                self._note(f"attempt {attempt.id}: admission denied ({reason}) → stopped")
                return
            self._note(f"attempt {attempt.id}: SDK turn failed → RETRY_WAIT")
            return
        text = "" if result.public_output is None else str(result.public_output.content)
        stale_plan = self._dispatch_for(attempt.mission_id)
        if stale_plan is not None and stale_plan.requirements_changed(attempt.mission_id):
            # 阶段 E：用户改了要求、按新版的计划还没提交。这份结果是按旧版要求的计划做出来的：
            # 归档为"被取代"，不当成执行者做错（不发修复请求、不为它切审查包）；
            # 新计划要不要再做这一步由规划器定。
            self.commit.reject_result(
                attempt.id, turn_id=result.turn_id, reason="superseded",
                detail={"error": "requirements_changed", "output_head": text[:400]})
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: result set aside, the requirements were amended")
            return
        try:
            envelope, client_result_id = self._parse_envelope(text, attempt, turn_id=result.turn_id)
            self.commit.check_result_evidence(attempt.mission_id, envelope)
        except (ContractError, CommitRejected) as error:
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason=(
                    "result_evidence_kind_not_allowed"
                    if isinstance(error, CommitRejected)
                    else "envelope_invalid"
                ),
                detail={"error": str(error), "output_head": text[:400]},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: envelope invalid → RETRY_WAIT ({error})")
            return
        mission = self.store.get_mission(attempt.mission_id)
        task = self.store.get_task(attempt.task_id)
        assert mission is not None and task is not None
        if envelope.outcome is not ResultOutcome.CANDIDATE:
            # D5-5: execution evidence, not a candidate — kept as history; the Attempt
            # ends in RETRY_WAIT and the Task is tried again within its allowance
            self.commit.record_outcome_result(
                attempt.id,
                envelope=envelope,
                turn_id=result.turn_id,
                usage_refs=tuple(result.usage_refs),
            )
            self._settle_intent(intent, "SETTLED")
            # Same order as every other outcome: close the intent, then settle (an
            # open Assurance responsibility keeps the reservation held, visibly).
            self._settle_if_known(self.store.get_attempt(attempt.id) or attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: outcome {envelope.outcome} → RETRY_WAIT")
            return
        workspace = self.assembled.workspaces.get(attempt.id)
        artifacts = workspace.snapshot(
            mission_id=attempt.mission_id,
            task_id=attempt.task_id,
            produced_by=intent.agent_id or attempt.id,
            versions=next_versions(self.store.list_mission_artifacts(attempt.mission_id)),
        )
        known = {artifact.path for artifact in artifacts}
        source_roots = tuple(self._frozen_source_binding(attempt).get("source_roots", ()))
        source_hashes = {
            path: sha256_hex_text(content) for path, content in self._source_files(attempt).items()
        }
        # Check the physical snapshot first: case aliases in a reported path must not
        # hide a changed source behind a generic missing-artifact diagnostic.
        source_rewritten = {
            artifact.path
            for artifact in artifacts
            if _under_source_root(artifact.path, source_roots)
            and artifact.content_hash != source_hashes.get(artifact.path)
        }
        missing = [path for path in envelope.artifacts if path not in known]
        if missing and not source_rewritten:
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="envelope_invalid",
                detail={"error": f"artifacts not in the workspace: {missing}"},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: artifacts missing → RETRY_WAIT")
            return
        # D3-7': the Attempt's artifact set = what it listed ∪ what it changed relative to
        # its initial inputs (seed + upstream); protected paths are never registered as
        # produced work (a rewrite there is tampering, reported by rule_check instead).
        # P2.3u: same function ``_read_only_initial`` the gateway snapshot uses.
        initial = self._read_only_initial(attempt)
        guarded = {
            path: sha256_hex_text(content)
            for path, content in self._protected_files(mission, task, attempt).items()
        }
        listed = set(envelope.artifacts)
        by_path = {artifact.path: artifact for artifact in artifacts}
        rewritten = sorted(
            source_rewritten
            | {
                path
                for path in listed & known
                if path in guarded and by_path[path].content_hash != guarded[path]
            }
        )
        if rewritten:  # P1-6: a protected path is never registered as produced work
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="protected_path_rewritten",
                detail={"paths": rewritten},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: rewrote protected {rewritten} → RETRY_WAIT")
            return
        # P2.3k verification P1-2: a hierarchical leaf whose type declares itself
        # read-only may not have changed a file it started from.  Grok C3's ``facts``
        # / ``reproduce`` leaves rewrote ``stats/window.py``; with ``code_test`` no
        # longer on such leaves, the declaration has to be enforced where the files
        # come in, not merely trusted.
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError("hierarchical_assembly_missing: no result collection without the assembly")
        try:
            current_task_ids = {
                str(spec.task_id) for spec in new_mode.network(mission.id).occurrences
            }
        except (GraphIntegrityError, ContractError, StoreError, KeyError):
            current_task_ids = set()
        accepted_hashes = self._accepted_path_hashes(mission.id, current_task_ids=current_task_ids)
        binding = new_mode.semantics().task_semantics_of(mission.id, task.id)
        rewrote = (
            []
            if binding is None
            else read_only_rewrites(
                binding,
                artifacts,
                initial,
                guarded=guarded,
                accepted=accepted_hashes,
            )
        )
        if rewrote:
            assert binding is not None
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="read_only_leaf_rewrote_workspace",
                detail={
                    "paths": rewrote,
                    "side_effect_kind": str(binding.side_effect_kind),
                    "capabilities": list(binding.capability_requirements),
                    "hint": (
                        "this leaf's task type is read-only: observe and report at "
                        "its declared output ports; do not change existing files"
                    ),
                },
            )
            # The refusal is the permission rule and stays.  What happens next is
            # the Planner's call: the ``ResultRejected`` event becomes an ordinary
            # repair request carrying the paths and how many times it has happened.
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: read-only leaf rewrote {rewrote} → RETRY_WAIT")
            return
        # P2.3m: drop files whose bytes already belong to a completed leaf.
        # P2.3v: a *write* leaf that reproduces a retired method's accepted bytes
        # (Grok M2-r1's new apply leaf vs the retired patch) must still record
        # those paths — otherwise ``rule_check`` refuses the envelope.  Same-hash
        # drop stays on read-only leaves, where P2.3o re-adds bound inputs.
        drop_same_hash = binding is not None and read_only_leaf(binding)
        consistent = (
            set()
            if not drop_same_hash
            else {
                artifact.path
                for artifact in artifacts
                if artifact.path in initial
                and accepted_hashes.get(artifact.path) == artifact.content_hash
            }
        )
        referenced = [
            artifact
            for artifact in artifacts
            if artifact.path not in consistent
            and (
                (
                    artifact.path not in guarded
                    and (
                        artifact.path in listed
                        or initial.get(artifact.path) != artifact.content_hash
                    )
                )
                or (artifact.path in guarded and artifact.path in listed)
            )
        ]
        if binding is None or not read_only_leaf(binding):
            try:
                referenced = self._record_applied_diff_files(
                    referenced,
                    workspace=workspace,
                    attempt=attempt,
                    seed=dict((mission.final_report or {}).get("workspace_seed", {})),
                )
            except UnifiedDiffApplyError as error:
                self.commit.reject_result(
                    attempt.id,
                    turn_id=result.turn_id,
                    reason="unified_diff_apply_failed",
                    detail={"path": error.path, "reason": error.reason},
                )
                self._settle_intent(intent, "FAILED")
                self._settle_if_known(attempt)
                await self._release_attempt(attempt.id, cancel=False)
                self._note(
                    f"attempt {attempt.id}: unified diff {error.path} {error.reason} → RETRY_WAIT"
                )
                return
        from .operation_completion import OperationCompletionError

        try:
            self.commit.record_result(
                attempt.id,
                envelope=envelope,
                turn_id=result.turn_id,
                artifacts=referenced,
                usage_refs=tuple(result.usage_refs),
                port_claims=self._port_claims.get(envelope.id, ()),
            )
        except OperationCompletionError as error:
            if error.code != "OP_COMPLETION_PORT_CLAIMS_UNAVAILABLE":
                # Not the Worker's envelope but the Mission's own completion inputs:
                # refused collection, isolated by the caller and retried visibly.
                raise CommitRejected(f"{error.code}: {error}") from error
            # 2026-10-01: the envelope's port claims do not satisfy the frozen completion
            # inputs (a declared port left unclaimed, a claim that binds no artifact).
            # That is the model's mistake: a refused result, never an exception out of
            # the loop — it used to end ``run()`` for every Mission in the process.
            self.commit.reject_result(
                attempt.id,
                turn_id=result.turn_id,
                reason="completion_inputs_refused",
                detail={"code": error.code, "error": str(error)[:300]},
            )
            self._settle_intent(intent, "FAILED")
            self._settle_if_known(attempt)
            await self._release_attempt(attempt.id, cancel=False)
            self._note(f"attempt {attempt.id}: port claims refused ({error}) → RETRY_WAIT")
            return
        self._fault("after_result_submitted", "attempt")
        self._settle_intent(intent, "SETTLED")
        await self._release_attempt(attempt.id, cancel=False)
        self._client_ids[envelope.id] = client_result_id
        self._note(f"attempt {attempt.id}: result {envelope.id} submitted")
        # P2.3b / TG §7: a child's result advances its parent compound's *typed* phase.
        # It never creates an Attempt for the compound and never writes its Task row —
        # the phase is a projection of typed state, recorded as an event.
        # (``new_mode`` was asked once, above, before the read-only check.)
        if new_mode is not None:
            new_mode.advance_compound_phases(mission.id)

    def _parse_envelope(
        self, text: str, attempt: Attempt, *, turn_id: str
    ) -> tuple[ResultEnvelope, str | None]:
        try:
            raw = extract_block(text, RESULT_ENVELOPE_TAG)
        except BlockError as error:
            raise ContractError(str(error)) from error
        # P2.3c part 2d, decision 4.  ``outputs`` is the hierarchical Worker's
        # "this file is what I produced at that port".  It is taken off the block
        # before ``ResultEnvelope.from_json`` because that contract refuses unknown
        # keys by design (§18.5: a model may not add fields to a system contract),
        # and it is checked *here* — against the ports this occurrence declares and
        # the files this Attempt actually wrote — so a bad claim is a bounded repair
        # on the same Attempt rather than a wrong artifact bound downstream.
        claims = self._port_claims_from(raw, attempt)
        if isinstance(raw.get("artifacts"), list):
            from ..runtime.action_schema import with_candidate_targets

            raw["artifacts"] = with_candidate_targets(
                raw["artifacts"], self.assembled.workspaces.get(attempt.id)
            )
        client_ids = {raw.get("id"), raw.get("result_id")} - {None}
        if len(client_ids) > 1:
            raise ContractError("result carries both id and result_id with different values")
        client_result_id = None if not client_ids else str(next(iter(client_ids)))
        raw.pop("id", None)
        raw.pop("result_id", None)
        raw.setdefault("mission_id", attempt.mission_id)
        provisional = ResultEnvelope.from_json({**raw, "id": "result-provisional"})
        if provisional.attempt_id != attempt.id or provisional.task_id != attempt.task_id:
            raise ContractError(
                "result identity does not match the Attempt / Task it was submitted for"
            )
        if provisional.mission_id != attempt.mission_id:
            raise ContractError("result mission_id does not match")
        body = provisional.to_json()
        body.pop("id")
        result_id = ids.result_id(attempt.id, turn_id, sha256_hex(body))
        envelope = ResultEnvelope.from_json({**body, "id": result_id})
        if outside_text(text, RESULT_ENVELOPE_TAG):
            logger.info("orchestrator.envelope_prose", extra={"attempt_id": attempt.id})
        if claims:
            self._port_claims[result_id] = claims
        return envelope, client_result_id

    def _port_claims_from(self, raw: dict[str, Any], attempt: Attempt) -> tuple[PortClaim, ...]:
        """Pop ``outputs`` off the block and check it, or refuse the block."""

        if "outputs" not in raw:
            return ()
        stated = raw.pop("outputs")
        mission = self.store.get_mission(attempt.mission_id)
        new_mode = None if mission is None else self._new_mode(mission)
        if new_mode is None:
            raise ContractError(
                "result has unknown fields: ['outputs']"  # the contract's own wording
            )
        declared = new_mode.declared_output_ports_for(attempt.mission_id, attempt.task_id)
        single = [item["port"] for item in declared if item.get("cardinality") == "single"]
        # The files this Attempt wrote are the ones this envelope *declares*: the
        # artifact rows are written from ``envelope.artifacts`` after the result is
        # accepted, so at parse time ``list_artifacts`` is empty and checking against
        # it refused every honest claim (found by the part 2d smoke, round 1).  Each of
        # those paths is separately checked against the real workspace before it
        # becomes an artifact, so a claim can never outlive a file that was not there.
        produced = [str(item) for item in (raw.get("artifacts") or []) if isinstance(item, str)]
        try:
            return parse_port_claims(
                stated,
                declared_ports=[item["port"] for item in declared],
                attempt_paths=produced,
                single_valued=single,
            )
        except BlockError as error:
            raise ContractError(str(error)) from error

    # ---------------------------------------------------------------- verify
    def _verification_superseded_by_accepted_sibling(self, result_id: str) -> bool:
        """Read the durable accept transaction; never settle or change a verdict."""
        losing = self.store.get_result(result_id)
        if losing is None:
            return False
        envelope = losing.envelope
        attempt = self.store.get_attempt(envelope.attempt_id)
        task = self.store.get_task(envelope.task_id)
        if (
            attempt is None
            or task is None
            or attempt.status is not AttemptStatus.SUPERSEDED
            or (attempt.failure or {}).get("reason") != "sibling_accepted"
            or attempt.task_id != task.id
            or attempt.mission_id != envelope.mission_id
            or task.mission_id != envelope.mission_id
            or task.status is not TaskStatus.COMPLETED
            or not task.accepted_result_id
            or task.accepted_result_id == result_id
            or losing.verification_state != "REJECTED"
            or losing.verdict != "superseded"
        ):
            return False
        accepted = self.store.get_result(task.accepted_result_id)
        if (
            accepted is None
            or accepted.verification_state != "DONE"
            or accepted.verdict != "PASS"
            or accepted.envelope.task_id != task.id
            or accepted.envelope.mission_id != task.mission_id
            or accepted.envelope.attempt_id == attempt.id
            or set(accepted.artifacts) != set(task.accepted_artifacts)
        ):
            return False
        winner = self.store.get_attempt(accepted.envelope.attempt_id)
        return (
            winner is not None
            and winner.status is AttemptStatus.COMPLETED
            and winner.task_id == task.id
            and winner.mission_id == task.mission_id
        )

    async def _verify(self, result_id: str) -> bool:
        stored = self.store.get_result(result_id)
        assert stored is not None
        attempt = self.store.get_attempt(stored.envelope.attempt_id)
        task = self.store.get_task(stored.envelope.task_id)
        mission = self.store.get_mission(stored.envelope.mission_id)
        assert attempt is not None and task is not None and mission is not None
        if attempt.status in TERMINAL_ATTEMPT:
            return False  # closed by a cascade; its result was rejected as history
        try:  # D3-10': verification is done by the Attempt's lease holder only
            attempt = self.commit.renew_lease(
                attempt.id,
                owner=self._owner,
                lease_seconds=self._config.lease_seconds,
                liveness={"progress": attempt.progress_marker, "phase": "verifying"},
            )
        except CommitRejected:
            return False
        stored = self.commit.start_verification(result_id)
        protected = self._protected_files(mission, task, attempt)
        tampered = self.assembled.workspaces.tampered_protected(attempt.id, protected)
        artifacts = [
            a for a in self.store.list_artifacts(attempt.id) if a.id in set(stored.artifacts)
        ]
        # P2.3o: a bound input the envelope named is a recorded workspace file.
        # Grok C3's verify leaf listed the patched ``stats/window.py`` the patch
        # leaf had already accepted; the collector dropped it (P2.3m same-hash)
        # and ``rule_check`` refused it as unrecorded.
        artifacts = bound_artifacts_named_in_envelope(
            stored.envelope.artifacts,
            artifacts,
            self._upstream_inputs(attempt),
            self.store.get_artifact,
        )
        # P3.2 review round 2 P1-3: rebuilt from the recorded bytes, never the live tree —
        # what is verified is what was recorded and what an approval will bind
        copy = self.assembled.workspaces.verification_copy(
            attempt.id,
            protected=protected,
            seed=dict((mission.final_report or {}).get("workspace_seed", {})),
            inputs=self._input_files(attempt),
            artifacts=artifacts,
        )
        self._register_copy(
            "verify",
            f"{attempt.id}-verify",
            mission_id=attempt.mission_id,
            attempt_id=attempt.id,
            detail={"artifacts": sorted(a.id for a in artifacts), "protected": sorted(protected)},
        )

        critic_admission_failure: _CriticAdmissionFailure | None = None

        async def recorder(layer: LayerResult) -> None:
            try:
                self._hold_lease(attempt.id)  # P1-3: a lost lease aborts verification
            except CommitRejected:
                if self._verification_superseded_by_accepted_sibling(result_id):
                    raise _AcceptedSiblingSupersededVerification from None
                raise
            detail = {"summary": layer.summary, **dict(layer.detail)}
            if layer.layer == "critic_review":
                if critic_admission_failure is not None:
                    detail["error"] = critic_admission_failure.error
                # A recovered intent may still contain an older prompt than the
                # domain now selects. Attribute only a completed Critic verdict,
                # including a reused layer, to its durable execution ordinal.
                detail.pop("critic_intent_id", None)
                detail["verifier_version"] = None
                if layer.status in {"PASS", "FAIL", NEEDS_HUMAN}:
                    detail.update(self._critic_provenance(mission.id, attempt.id))
            else:
                detail["verifier_version"] = VERIFIER_VERSION
            self.commit.record_verification_layer(
                result_id,
                layer=layer.layer,
                status=layer.status,
                detail=detail,
            )
            if layer.status == "PASS":
                self._fault("after_layer_pass", "attempt")

        async def run_critic(test_output: str | None) -> CriticVerdict:
            nonlocal critic_admission_failure
            try:
                return await self._run_critic(
                    mission,
                    task,
                    view_id=attempt.id,
                    subject_prefix=f"{attempt.id}:critic",
                    account_id=task_account(task.id),
                    artifacts=artifacts,
                    test_output=test_output,
                    attempt_id=attempt.id,
                )
            except _CriticAdmissionFailure as error:
                critic_admission_failure = error
                raise
            except BudgetExhausted as error:
                # a required layer that could not run is an ERROR, never a PASS (ORCH §12.4)
                raise ContractError(f"critic could not be funded: {error}") from error

        human, reuse, escalation_left = self._human_inputs(result_id, task)
        domain = self.commit.domain_for(mission.id)
        from ..assurance.codec import AssuranceError
        from .operation_completion import OperationCompletionError

        try:
            local_check_factory = None

            if self._assurance_local_checks is None:
                raise AssuranceError("ASSURANCE_LOCAL_CHECKS_UNBOUND")
            local_check_factory = self._assurance_local_checks.prepare
            verdict = await self._router.verify(
                mission=mission,
                task=task,
                envelope=stored.envelope,
                artifacts=artifacts,
                verification_copy=copy,
                client_result_id=self._client_ids.get(result_id),
                run_critic=run_critic,
                recorder=recorder,
                tampered=tampered,
                knowledge=KnowledgeIndex.load(self.store, mission.id),
                human=human,
                reuse=reuse,
                needs_human_allowed=escalation_left,
                domain=domain,
                local_check_recorder_factory=local_check_factory,
            )
        except _AcceptedSiblingSupersededVerification:
            self._note(f"result {result_id}: obsolete verifier after sibling acceptance")
            return True
        except (AssuranceError, OperationCompletionError) as error:
            # 这次验证依据的完成范围已经过期（用户改了要求，或计划换了一版）：结果归档为"被取代"，
            # 这一步要不要重做由规划器定（与收集处"按旧版要求做的结果归档"同一条规则）。验证开头
            # 读完成范围就会发现，检查跑到一半撞上也一样。别的错误照旧抛出（HTN 补齐 F1）
            if error.code not in {"CHECK_SCOPE_CHANGED", "OP_EFFECT_SCOPE_STALE"}:
                raise
            if self._scope_carried(result_id):
                return self._verdict_refused(result_id, error)
            return await self._set_aside_stale(result_id)
        if critic_admission_failure is not None:
            admission_detail_error = critic_admission_failure.error
            admission = admission_detail_error["detail"]
            reason = admission.get("reason_code")
            admission_failures = [
                {
                    **item,
                    "detail": {**dict(item.get("detail", {})), "error": admission_detail_error},
                }
                if item.get("layer") == "critic_review"
                else item
                for item in verdict.failures
            ]
            # Commit rejection and its terminal budget/configuration handling
            # atomically: recovery must never see a redo-eligible intermediate.
            with self.store.transaction():
                self.commit.fail_result(result_id, failures=admission_failures, owner=self._owner)
                if reason == "cancelled":
                    self._commit_cancel_mission(mission.id)
                else:
                    stop = (
                        MissionStopReason.BUDGET_EXHAUSTED
                        if reason in {"budget_exhausted", "deadline"}
                        else MissionStopReason.RUNTIME_UNAVAILABLE
                    )
                    self._commit_stop_task(
                        task.id,
                        stop_reason=stop,
                        detail={
                            "source_kind": "provider_admission",
                            "retryable": False,
                            "admission": admission,
                            "result_id": result_id,
                            **({"dimension": "runtime"} if reason == "deadline" else {}),
                        },
                    )
            await self._release_mission(mission.id)
            self._note(f"task {task.id}: Critic admission denied ({reason}) -> stopped")
            return True
        if verdict.critic is not None:
            self._critic_verdicts[result_id] = verdict.critic
        if verdict.suspended:  # D7-8': the sixth layer waits for a person
            reason = (
                "needs_human"
                if any(layer.status == NEEDS_HUMAN for layer in verdict.layers)
                else "policy"
            )
            try:
                self.commit.suspend_verification(
                    result_id,
                    owner=self._owner,
                    reason=reason,
                    layers=[layer.to_json() for layer in verdict.layers],
                )
            except (CommitRejected, IllegalTransition, ActionCommitError) as error:
                self._note(f"result {result_id}: suspension dropped ({error})")
                return True
            self._note(f"result {result_id} suspended: waiting for a person ({reason})")
            return True
        try:
            if verdict.passed:
                completed = self.commit.accept_result(
                    result_id,
                    verifier_results=[
                        layer.to_json() for layer in verdict.layers if layer.status == "PASS"
                    ],
                    owner=self._owner,
                    connectors=self._connectors,
                    deployment=self._config.deployment_policy,
                )
            else:
                self.commit.fail_result(result_id, failures=verdict.failures, owner=self._owner)
        except (AssuranceError, OperationCompletionError) as error:
            # 验完、提交结论前，用户改了要求或计划换了一版：同上，结果归档为"被取代"（阶段 G
            # 随机序列发现：这一处与验证开头是同一条规则的两个时刻）
            if error.code not in {"CHECK_SCOPE_CHANGED", "OP_EFFECT_SCOPE_STALE"}:
                raise
            if self._scope_carried(result_id):
                return self._verdict_refused(result_id, error)
            return await self._set_aside_stale(result_id)
        except (CommitRejected, IllegalTransition, ResolutionCommitRejected) as error:
            # the Attempt was closed / taken over while we verified (P1-4): the verdict is
            # dropped; the library's state is whatever the other Commit made it. An
            # assured acceptance refused by its current use certificate lands here too.
            self._note(f"result {result_id}: verdict dropped ({error})")
            # Visible in the Host log (progress notes are not): a refusal that repeats
            # every round is a stuck Mission, not progress.
            logger.warning("orchestrator.verdict_dropped result=%s mission=%s error=%s: %s",
                           result_id, mission.id, type(error).__name__, error)
            return self._verdict_refused(result_id, error)
        self._verdict_refusals.pop(result_id, None)
        accepted = verdict.passed and (
            completed.status is TaskStatus.COMPLETED or completed.accepted_result_id == result_id
        )
        if accepted:
            self._fault("after_task_completed", "attempt")
            self._note(f"result {result_id} PASS → task {completed.id} {completed.status}")
            for sibling in self.store.list_attempts(task.id):
                if sibling.status is AttemptStatus.SUPERSEDED:
                    await self._release_attempt(sibling.id, cancel=True)
        else:
            self._note(f"result {result_id} FAIL at {verdict.short_circuited_at}")
            undeployed = [
                layer.layer
                for layer in verdict.layers
                if layer.status == "ERROR" and layer.detail.get("undeployed")
            ]
            if undeployed:
                # D6-9': a required verifier that is not deployed blocks — no retry can make
                # it appear, and a missing layer is never a PASS
                self._commit_stop_task(
                    task.id,
                    stop_reason=MissionStopReason.VERIFIER_UNAVAILABLE,
                    detail={"layers": undeployed, "result_id": result_id},
                )
                await self._release_mission(mission.id)
                self._note(f"task {task.id} stopped: verifier(s) {undeployed} not deployed")
                return True
        return True

    async def _carried_review_once(self, item: Any) -> bool:
        """TaskGraph 补全第四批：一份按旧版要求通过、被现行计划沿用的结果，按现行要求重审。

        与新结果同一条"本地检查 → 审阅员独立会话 → 写验收"的流程，只是驱动不同：由计划提交后
        的扫描驱动，不要尝试租约、不建尝试、不调执行者、不动尝试 / 任务 / 产物状态；验证记录与
        验收都按（结果, 要求版本）记。通过 → 按新版要求写一条验收；没过 → 修复请求交规划器
        （``CARRIED_RESULT_REJECTED``）；审阅员两次都判不下来 → 问用户裁决。"""
        import functools

        from ..assurance.codec import AssuranceError
        from .assurance_review_import import carried_review_alive
        from .operation_completion import OperationCompletionError
        from .resolution_commits import ResolutionCommitRejected
        from .review_adjudication import adjudication_of

        revision = int(item.requirements_revision)
        stored = self.store.get_result(item.result_id)
        attempt = None if stored is None else self.store.get_attempt(stored.envelope.attempt_id)
        task = self.store.get_task(item.task_id)
        mission = self.store.get_mission(item.mission_id)
        if (stored is None or attempt is None or task is None or mission is None
                or self._assurance_reviews is None or self._assurance_local_checks is None
                or not carried_review_alive(self.store, mission.id, task.id)):
            return False
        reviews = self._assurance_reviews
        artifacts = [a for a in self.store.list_artifacts(attempt.id) if a.id in set(stored.artifacts)]
        artifacts = bound_artifacts_named_in_envelope(
            stored.envelope.artifacts, artifacts, self._upstream_inputs(attempt), self.store.get_artifact)
        copy = self.assembled.workspaces.verification_copy(
            attempt.id, protected=self._protected_files(mission, task, attempt),
            seed=dict((mission.final_report or {}).get("workspace_seed", {})),
            inputs=self._input_files(attempt), artifacts=artifacts)

        async def recorder(layer: LayerResult) -> None:
            detail = {"summary": layer.summary, **dict(layer.detail)}
            if layer.layer == "critic_review":
                detail.pop("critic_intent_id", None)
                detail["verifier_version"] = None
                if layer.status in {"PASS", "FAIL", NEEDS_HUMAN}:
                    detail.update(reviews.provenance(mission.id, attempt.id, revision))
            else:
                detail["verifier_version"] = VERIFIER_VERSION
            self.commit.record_verification_layer(
                item.result_id, layer=layer.layer, status=layer.status, detail=detail,
                requirements_revision=revision)

        async def run_critic(test_output: str | None) -> CriticVerdict:
            from ..runtime.model_router import RoutingUnavailable

            try:
                return await reviews.run_task(mission, task, attempt_id=attempt.id, requirements_revision=revision)
            except (AssuranceError, RoutingUnavailable, BudgetExhausted) as error:
                # 与新结果同一个口径：审阅员这一层跑不起来是"出错"，不是没过、更不是通过
                raise ContractError(f"Assurance review unavailable: {error}") from error

        with self.store.read_view():
            record = reviews.task_record(mission.id, attempt.id, revision)
        ruling = None if record is None else adjudication_of(self.store, str(record.record_id))
        if record is not None and ruling is None and str(record.verdict) == "INCONCLUSIVE":
            # 审阅员两次都判不下来、在等用户裁决：不重跑检查，只看问题答了没有
            return self._ask_carried_ruling(mission, task, record, item)
        human = None if ruling is None else {
            "verdict": "PASS" if ruling.get("decision") == "pass" else "FAIL",
            "note": "", "principal": ruling.get("principal_id"), "request_id": "adjudicate-carried:" + str(record.record_id)}
        verdict = await self._router.verify(
            mission=mission, task=task, envelope=stored.envelope, artifacts=artifacts,
            verification_copy=copy, client_result_id=None, run_critic=run_critic,
            recorder=recorder, tampered=(), knowledge=KnowledgeIndex.load(self.store, mission.id),
            human=human, reuse=None, needs_human_allowed=True, domain=self.commit.domain_for(mission.id),
            local_check_recorder_factory=functools.partial(
                self._assurance_local_checks.prepare, requirements_revision=revision))
        if verdict.passed:
            self.commit.accept_carried_result(item.result_id, requirements_revision=revision)
            self._note(f"result {item.result_id}: kept and passed again under requirements r{revision}")
            return True
        errored = [layer for layer in verdict.layers if layer.status == "ERROR"]
        if errored:
            # 检查或审阅员这一层出错（不是谁的判断）：按次数上限重来，不当成没过
            raise ContractError("; ".join(f"{layer.layer}: {layer.summary}" for layer in errored))
        with self.store.read_view():
            record = reviews.task_record(mission.id, attempt.id, revision)
        if verdict.suspended and record is not None:
            return self._ask_carried_ruling(mission, task, record, item)
        return self._request_carried_repair(
            mission, task, item, record=record, failures=[dict(failure) for failure in verdict.failures])

    def _ask_carried_ruling(self, mission: Mission, task: Task, record: Any, item: Any) -> bool:
        # 内容审阅判的是那份结果：裁决回执的对象写结果，按新版写验收时使用凭证才认它
        return self._ask_person_to_adjudicate(
            mission, record, target_id=item.result_id, subject_key=task.id, task_id=task.id,
            decision_id="adjudicate-carried:" + str(record.record_id),
            intro="改要求后沿用的一步（" + str(task.goal)[:60] + "）按新要求重审，两位审阅员都判不下来，"
                  "需要你裁决它是否仍然合格。",
            extra={"result_id": item.result_id, "requirements_revision": int(item.requirements_revision)})

    def _request_carried_repair(self, mission: Mission, task: Task, item: Any, *, record: Any = None,
                                failures: Sequence[Mapping[str, Any]] = (), error: str | None = None) -> bool:
        """The one durable end of a re-review that did not accept: a repair request to the
        Planner (the scan stops on it).  Recorded once per (result, requirements revision)."""
        from .carried_review import source_key
        from .planning_repair_requests import record_request

        revision = int(item.requirements_revision)
        produced = record_request(
            self._new_mode(mission), mission.id, event_type="VerifierAcceptanceRejected",
            trigger_refs=(task.id,), source_key=source_key(item.result_id, revision),
            detail={"source": "carried_review", "reason_code": CARRIED_RESULT_REJECTED,
                    "result_id": item.result_id, "requirements_revision": revision,
                    "occurrence_id": item.occurrence_id,
                    **({} if record is None else {"record_id": str(record.record_id),
                                                   "findings": self._review_record_findings(record)}),
                    **({} if error is None else {"review_error": error[:2000]}),
                    "failures": [dict(failure) for failure in failures]})
        if produced:
            self._note(f"result {item.result_id}: kept, not accepted under requirements r{revision}; repair requested")
        return produced

    async def _carried_review(self, item: Any) -> bool:
        """One round of a kept result's review, inside its own fault boundary: nothing it
        raises reaches the main loop (a fault of one Mission stops only that step's review).
        A round that could not conclude — the plan moved, the reviewer or a checker was
        unavailable, anything unexpected — is retried, at most ``MAX_CARRIED_REVIEW_ERRORS``
        times; then the Planner is told the review could not be done and decides."""
        from .carried_review import MAX_CARRIED_REVIEW_ERRORS

        try:
            done = await self._carried_review_once(item)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the boundary; the count below is the bound
            count = self._carried_errors.get(item.key, 0) + 1
            text = f"{type(error).__name__}: {error}"
            self._note(f"result {item.result_id}: re-review under r{item.requirements_revision} "
                       f"could not conclude ({count}/{MAX_CARRIED_REVIEW_ERRORS}): {text}")
            if count < MAX_CARRIED_REVIEW_ERRORS:
                self._carried_errors[item.key] = count
                return False
            self._carried_errors.pop(item.key, None)
            logger.warning("orchestrator.carried_review_gave_up result=%s mission=%s error=%s",
                           item.result_id, item.mission_id, text)
            mission, task = self.store.get_mission(item.mission_id), self.store.get_task(item.task_id)
            if mission is None or task is None:
                return False
            try:
                return self._request_carried_repair(mission, task, item, error=text)
            except Exception as late:  # noqa: BLE001 - never out of the boundary
                self._note(f"result {item.result_id}: repair request not recorded ({late})")
                return False
        self._carried_errors.pop(item.key, None)
        return done

    def _critic_provenance(self, mission_id: str, attempt_id: str) -> dict[str, str]:
        """The one parsed Critic verdict's durable intent, not a current template.

        Invalid verdicts settle FAILED; the successful ordinal settles SETTLED
        before returning its verdict. This survives a crash before layer recording
        and also interprets legacy layers that did not record an intent id.
        Missing or ambiguous execution identity is never proof of a prompt version.
        """

        if self._assurance_reviews is None:
            return {}
        return self._assurance_reviews.provenance(mission_id, attempt_id)

    def _human_inputs(
        self, result_id: str, task: Task
    ) -> tuple[dict[str, Any] | None, dict[str, LayerResult] | None, bool]:
        """D7-8': a person's answer to this result's review (if any), the recorded layers
        a resumed verification may reuse, and whether this Task may still escalate."""

        request = self.store.get_approval(review_request_id(result_id))
        human: dict[str, Any] | None = None
        reuse: dict[str, LayerResult] | None = None
        if request is not None and request["state"] in {"GRANTED", "REJECTED"}:
            human = {
                "verdict": "PASS" if request["state"] == "GRANTED" else "FAIL",
                "note": request.get("note", ""),
                "principal": request.get("decided_by"),
                "request_id": request["request_id"],
            }
            stored = self.store.get_result(result_id)
            provenance = (
                self._critic_provenance(task.mission_id, stored.envelope.attempt_id)
                if stored is not None
                and stored.envelope.task_id == task.id
                and stored.envelope.mission_id == task.mission_id
                else {}
            )
            from .completion_inputs import frozen_requirements_revision

            rows = []
            revision = None if stored is None else frozen_requirements_revision(self.store, stored)
            for row in ([] if revision is None else self.store.list_verifications(
                    result_id, requirements_revision=revision)):
                if row["layer"] == "critic_review":
                    detail = row.get("detail") or {}
                    if (
                        not provenance
                        or detail.get("verifier_version") != provenance["verifier_version"]
                        or (
                            "critic_intent_id" in detail
                            and detail["critic_intent_id"] != provenance["critic_intent_id"]
                        )
                    ):
                        continue
                rows.append(row)
            reuse = reusable_layers(
                rows,
                versions={"critic_review": provenance["verifier_version"]} if provenance else {},
                default_version=VERIFIER_VERSION,
            )
        escalated_before = any(
            r["kind"] == "review"
            and r.get("reason") == "needs_human"
            and r.get("task_id") == task.id
            and r["subject_key"] != result_id
            for r in self.store.list_approvals(task.mission_id)
        )
        return human, reuse, not escalated_before

    def _arbitrated(
        self,
        mission: Mission,
        tasks: Sequence[Task],
        key: str,
        judgments: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        """D7-8' kind ②: when the independent judge Critic disagrees with the Tasks' own
        Critics on a criterion everything else says is met, a person rules.  Returns the
        judgments to use (``None`` while waiting) and whether a request was just opened."""

        opinions: dict[
            str, list[bool]
        ] = {}  # review P2-7: what each Task Critic said, per criterion
        for task in tasks:
            # 第四批：一份结果可能按几版要求审过，取最新一版的审阅意见
            layers = self.store.list_verifications(task.accepted_result_id or "", requirements_revision=None)
            newest = max((int(row["requirements_revision"]) for row in layers), default=0)
            for layer in (row for row in layers if int(row["requirements_revision"]) == newest):
                if layer["layer"] != "critic_review" or layer["status"] != "PASS":
                    continue
                for item in (layer.get("detail") or {}).get("mission_criteria", []):
                    opinions.setdefault(str(item.get("criterion")), []).append(
                        bool(item.get("met"))
                    )
        contested = judgment_conflict(judgments, task_opinions=opinions)
        if not contested:
            return [dict(j) for j in judgments], False
        subject = f"{mission.id}:judgment:{key}"
        request = self.store.get_approval(arbitration_request_id(subject))
        if request is None:
            self.commit.request_arbitration(
                mission.id,
                subject=subject,
                topic="judgment",
                options=("met", "unmet"),
                context={"criteria": contested, "judgments": [dict(j) for j in judgments]},
            )
            self._note(f"mission {mission.id}: Verifiers disagree on {contested} → arbitration")
            return None, True
        if request["state"] != "GRANTED":
            return None, False
        ruling = str(request.get("ruling"))
        return [
            {
                **dict(j),
                "met": ruling == "met",
                "judge": "human_arbitration",
                "reason": f"arbitrated by {request.get('decided_by')}: {request.get('basis')}",
                "override_id": request.get("override_id"),
            }
            if j.get("criterion") in contested
            else dict(j)
            for j in judgments
        ], False

    def _hold_lease(self, attempt_id: str) -> Attempt:
        """Renew this owner's lease during a long verification; ``CommitRejected`` when
        another owner took the Attempt over after a lapse (P1-3)."""

        attempt = self.store.get_attempt(attempt_id)
        assert attempt is not None
        return self.commit.renew_lease(
            attempt.id,
            owner=self._owner,
            lease_seconds=self._config.lease_seconds,
            liveness={"progress": attempt.progress_marker, "phase": "verifying"},
            minimum_remaining_seconds=self._config.lease_seconds / 2,
        )

    def _protected_seed(self, mission: Mission, task: Task) -> dict[str, str]:
        """Seed files the Worker may not rewrite: pytest targets and anything under tests/ (P0-1)."""

        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        targets = [
            c.removeprefix("pytest:").strip()
            for c in (*task.success_criteria, *current_statements(self.store, mission))
            if c.startswith("pytest:")
        ]
        protected = {}
        for path, content in seed.items():
            if path.startswith("tests/") or any(
                path == target or (target and path.startswith(target.rstrip("/") + "/"))
                for target in targets
            ):
                protected[path] = content
        return protected

    def _action_candidate_outputs(self, mission: Mission, task: Task) -> tuple[str, ...]:
        """See :func:`..runtime.action_schema.declared_action_outputs` (2A.1e/1f)."""
        from ..runtime.action_schema import declared_action_outputs

        return declared_action_outputs(self.store, mission.id, task)

    def _worker_action_contract(self, mission: Mission, task: Task) -> dict[str, Any] | None:
        """What the Worker is told about operation candidates, and whether the system
        writes the candidate itself (the same answer for its package and its result)."""
        from ..runtime.action_schema import worker_action_contract

        return worker_action_contract(
            mission_criteria=dict(current_criteria(self.store, mission)),
            task_criteria=task.success_criteria,
            task_outputs=self._action_candidate_outputs(mission, task),
            connectors=self._connectors,
            deployment=self._config.deployment_policy,
        )

    def _revises_its_inputs(self, mission_id: str, task_id: str) -> bool:
        """Every declared input port is also an output port of the same schema."""
        from ..storage.htn_store import HtnStore

        binding = HtnStore(self.store).task_semantics_of(mission_id, task_id)
        if binding is None or not binding.input_ports:
            return False
        outputs = {(port.port_key, port.schema_ref) for port in binding.output_ports}
        return all((port.port_key, port.schema_ref) in outputs for port in binding.input_ports)

    def _protected_files(
        self, mission: Mission, task: Task, attempt: Attempt
    ) -> dict[str, str | bytes]:
        """Protected seed files plus every upstream input the Task did not declare as
        one of its ``outputs`` (D3-7': a downstream Worker may not silently rewrite what
        its dependencies delivered; rewriting an undeclared path needs a new Task)."""

        protected: dict[str, str | bytes] = dict(self._protected_seed(mission, task))
        declared = set(task.outputs)
        seed_paths = set((mission.final_report or {}).get("workspace_seed", {}))
        revises_inputs = self._revises_its_inputs(mission.id, task.id)
        for item in self._upstream_inputs(attempt):
            if item.path in declared:  # the Task declared it will rewrite this path
                continue
            if revises_inputs:
                # 2026-09-25 UI 全量点击: a revise-in-place Task (every input port is
                # also one of its output ports, e.g. desktop.continue-delivery) exists
                # to deliver the next version of what it received.  Protecting those
                # paths made it impossible — the document continuation could never
                # write summary.md and failed until its attempts ran out.  The upstream
                # artifact itself stays immutable (content-addressed).
                continue
            # P2.3o: a seed path overlaid from a bound producer is the consumer's
            # baseline (so ``code_test`` runs on the accepted patch), not a
            # protected port document.  Rewriting it to *new* bytes is still a
            # read-only rewrite (P2.3m); pinning it here would reclassify that
            # as ``protected_path_rewritten`` and skip the planning escalation.
            if item.path in seed_paths:
                continue
            artifact = self.store.get_artifact(item.artifact_id)
            try:  # P3.2 D3: the stored bytes, hash re-checked
                if artifact is None:
                    raise ArtifactStoreError("missing", item.path)
                raw = read_verified(artifact)
            except ArtifactStoreError as error:
                raise ArtifactConflict(
                    f"upstream artifact {item.artifact_id} ({item.path}) is missing"
                ) from error
            try:
                protected[item.path] = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ArtifactConflict(f"upstream artifact {item.path} is not text") from error
        # Source registration never grants a Worker permission to change source bytes,
        # even when it declares that path as an output.
        protected.update(self._source_files(attempt))
        return protected

    async def _run_critic(
        self,
        mission: Mission,
        task: Task | None,
        *,
        view_id: str,
        subject_prefix: str,
        account_id: str,
        artifacts: Sequence[Artifact],
        test_output: str | None,
        attempt_id: str | None = None,
    ) -> CriticVerdict:
        from ..assurance.codec import AssuranceError
        if task is None or attempt_id is None or self._assurance_reviews is None:
            raise ContractError("Assurance review builder is not installed for this purpose")
        try:
            return await self._assurance_reviews.run_task(mission, task, attempt_id=attempt_id)
        except (AssuranceError, RoutingUnavailable) as error:
            raise ContractError(f"Assurance review unavailable: {error}") from error

    async def _await_service_turn(  # type: ignore[no-untyped-def]
        self, intent: DispatchIntent, deadline: float, *, attempt_id: str | None
    ):
        """Dispatch a Critic intent and wait for its turn (the Critic runner's loop).

        Returns ``(intent, result)``; ``result`` is ``None`` when the wait window
        closed — or, P2.3f, when the turn was waiting on an unknown Provider outcome,
        was re-handed off once and was unknown again — and the caller's own
        "did not answer" door takes it from there.  Raises ``CommitRejected`` when the
        Critic's subject stopped meanwhile (the after-stop collector owns it then).
        """

        intent = await self._dispatch_until_submitted(intent, deadline)
        result = None
        while self.store.now < deadline:
            if self._critic_subject_stopped(intent):
                # The cycle's after-stop collector owns cancellation and cost
                # settlement from here, including after a process restart.
                await self._collect_after_stop(intent)
                raise CommitRejected("Critic subject stopped during verification")
            assert intent.agent_id and intent.expected_turn_id
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
            if result is not None:
                break
            # P2.3f: a Critic turn waiting on an unknown Provider outcome is not "the
            # Critic thinking"; it is a question nobody is answering.  Same bound and
            # same two steps as a Planner turn — re-hand-off once, then the caller's
            # own "did not answer" door (``result is None``).
            liveness = await self.bridge_for(intent).liveness(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
            outcome = await self._resolve_provider_blocked_service(intent, liveness)
            if outcome == "rehandoff":
                refreshed = self.store.get_intent(intent.intent_id)
                assert refreshed is not None
                intent = await self._dispatch_until_submitted(refreshed, deadline)
                continue
            if outcome == "give_up":
                break
            if attempt_id is not None:
                self._hold_lease(attempt_id)  # P1-3: keep the lease while the Critic thinks
            await asyncio.sleep(self._poll)
        return intent, result

    async def _dispatch_until_submitted(
        self, intent: DispatchIntent, deadline: float
    ) -> DispatchIntent:
        """Drive one service intent through create + submit (the Critic runner's loop)."""

        while intent.state in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
            if not await self._dispatch(intent):  # another owner holds the claim (P1-8)
                if self.store.now >= deadline:
                    raise ContractError("critic intent is claimed elsewhere; wait window over")
                await asyncio.sleep(self._poll)
            refreshed = self.store.get_intent(intent.intent_id)
            assert refreshed is not None
            intent = refreshed
        assert intent.agent_id and intent.expected_turn_id
        return intent

    # ------------------------------------------- P2.3f: unknown Provider outcomes
    @property
    def _service_blocker_limit(self) -> float:
        return min(float(self._config.stall_seconds), MAX_SERVICE_BLOCKER_SECONDS)

    @staticmethod
    def _provider_blocked(liveness: Liveness) -> bool:
        """The turn is waiting on a Provider hand-off whose outcome is unknown.

        Only the run's own *wait blocker* of kind ``provider`` counts — or ``tool`` whose
        effect is durably ``unknown`` (2026-09-29 真机第八局：重启打断了执行者正在做的工具操作，
        那一轮挂在"工具结果未知"上永远不结束；执行者的工具只作用在它自己的尝试工作区，放弃重做
        不会造成重复副作用）。正在执行的工具同样是 kind=tool，但状态不是 unknown，不算。  The bridge also
        reports ``provider_slot_wait`` (queued behind the concurrency limit) and
        ``provider_response_wait`` (a call that is genuinely in progress) as
        ``blocked``; both are the executor making progress and neither is timed here.
        """

        blocker = liveness.blocker
        return (
            liveness.exists
            and not liveness.settled
            and isinstance(blocker, Mapping)
            and (str(blocker.get("kind", "")) == "provider"
                 or (str(blocker.get("kind", "")) == "tool"
                     and str(blocker.get("effect_state", "")) == "unknown"))
        )

    async def _resolve_provider_blocked_service(
        self, intent: DispatchIntent, liveness: Liveness
    ) -> str | None:
        """End the one wait the executor cannot end: a hand-off with an unknown outcome.

        P2.3f, found by the P2.3e probe episode.  The runtime hands a request off, the
        transport fails 0.2 s later, and — correctly — the invocation is settled
        UNKNOWN rather than FAILED: the request may have reached the model, and
        replaying it blindly would be a second charge for the same question.  The run
        then waits for a reconciliation observation that this deployment has nobody to
        make, and the intent stayed SUBMITTED until the caller's deadline (1800 s in
        the acceptance runner) with the Mission at PLANNING and nothing written down.

        The loop's answer is bounded and durable: after :attr:`_service_blocker_limit`
        seconds on the same blocker, the request is handed off **once more** to a new
        executor (:data:`MAX_SERVICE_REHANDOFFS`, recorded as
        ``ServiceIntentRehandedOff``); if that one is unknown too, the round ends
        through the role's own failure door — a Planner round is rejected with
        ``provider_outcome_unknown`` and the ladder decides, an assured review call
        (method review, root review) is closed as interrupted (``REVIEW_CALL_ABANDONED``)
        and reopened once in a fresh session before its package is recut, and a Critic
        turn is handed back to its runner's own "did not answer" path.  The abandoned turn's charge stays unknown in the
        runtime ledger and keeps the reservation held, which is the honest count.

        Returns ``None`` (keep waiting), ``"rehandoff"`` (a new executor is about to
        be dispatched) or ``"give_up"`` (the round was ended, or — for a Critic — is
        the runner's to end).
        """

        key = f"{intent.intent_id}:{intent.replays}"
        auth_blocked = self._definite_auth_failure(liveness.blocker)
        if not self._provider_blocked(liveness) and not auth_blocked:
            self._service_blocked_since.pop(key, None)
            return None
        mission = self.store.get_mission(intent.mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return None
        from .assurance_review_wait import record_provider_wait
        record_provider_wait(self.commit, intent, liveness)
        # Preserve the exact original executor, reservation and UNKNOWN
        # grants. The ordinary collector can still import a later answer.
        # Neither an auth failure nor a business timeout is accounting proof.
        # Never re-handed off here (that re-sends the original request).
        planning = intent.kind == "plan" and not self._assured_review_intent(intent)
        if intent.kind == "attempt":
            # 2026-09-26 Host run: a Worker whose hand-off came back half-read
            # (200 OK, body never finished) was settled UNKNOWN and then waited on
            # the Provider blocker for 35 minutes — nothing here ended an assured
            # Attempt.  After the same bound as a Planner round it ends as LOST, so
            # the ordinary repair/retry path runs; the UNKNOWN charge keeps its
            # reservation held (overcount, never undercount, never freeze).
            since = self._service_blocked_since.setdefault(key, self.store.now)
            waited = self.store.now - since
            if waited < self._service_blocker_limit:
                return None
            self._service_blocked_since.pop(key, None)
            await self._give_up_blocked_assured_attempt(intent, detail={
                "waited_seconds": round(waited, 3),
                "limit_seconds": self._service_blocker_limit,
                "blocker": dict(liveness.blocker or {}),
                "assurance_lane": True,
            })
            return "give_up"
        new_mode = self._new_mode(mission) if planning else None
        if new_mode is None:
            # Reviews keep their original executor (§6.2): a review awaited inline is
            # handed back to its runner's own "did not answer" door; a plan-kind review
            # collected by the loop never reaches here (``_end_overdue_review_call``).
            return "give_up"
        # A Planner round is not a review: after the same bound as
        # P2.3f it ends through its own failure door instead of waiting for the wall
        # clock (host-final-arp10, 2026-09-24: ~17 minutes frozen).  On this lane that
        # door keeps the UNKNOWN grants and the reservation (never under-counted).
        since = self._service_blocked_since.setdefault(key, self.store.now)
        waited = self.store.now - since
        if waited < self._service_blocker_limit:
            return None  # still waiting: not progress (阶段 B 裁决第 6 类)
        # 第 2 批 A27 (§6.2 "原 intent 由 runtime 核对"): the bound passed — re-read this
        # hand-off's own call key before the round is ended and the ladder asks again.
        from ..runtime.provider_budget_guard import resend_record
        check = self._unknown_handoff_check(intent)
        if check.verdict == "result_known" and key not in self._handoff_known_waits:
            # the same call has a result now: nothing is asked again; the ordinary collector
            # imports it.  One more window only, so a turn that never wakes still ends.
            self._handoff_known_waits.add(key)
            self._service_blocked_since[key] = self.store.now
            self._note(f"{intent.subject_id}: the lost call has a result; collecting instead of re-asking")
            return None
        self._service_blocked_since.pop(key, None)
        self._handoff_known_waits.discard(key)
        await self._give_up_blocked_plan_intent(
            intent,
            mission,
            new_mode,
            detail={
                "waited_seconds": round(waited, 3),
                "limit_seconds": self._service_blocker_limit,
                "blocker": dict(liveness.blocker or {}),
                "rehandoffs": 0,
                "assurance_lane": True,
                "handoff_check": check.to_json(),
                "resend": resend_record(check, ordinal=int(intent.config.get("ordinal", 1))),
            },
        )
        return "give_up"
    def _unknown_handoff_check(self, intent: DispatchIntent):  # type: ignore[no-untyped-def]
        """第 2 批 A27: re-read the intent's latest hand-off (same invocation, same ordinal)
        in the pool's own runtime ledger.  No runtime to read from leaves it unresolved."""
        from ..runtime.provider_budget_guard import check_unknown_handoff

        try:
            uow = self.bridge_for(intent).runtime.uow
        except Exception:  # noqa: BLE001 - the pool is gone: nothing can be re-read
            uow = None
        return check_unknown_handoff(self.store, uow, intent_id=intent.intent_id)
    async def _give_up_blocked_plan_intent(
        self,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        *,
        detail: Mapping[str, Any],
    ) -> None:
        """The second unknown outcome ends the round through the role's own door."""


        self._import_usage(intent)  # facts of the executor that did answer, if any
        self._settle_intent(intent, "FAILED")
        self._settle_service_if_known(intent.subject_id, mission.id)
        self._note(
            f"{intent.subject_id}: unknown Provider outcome again after "
            f"{detail.get('rehandoffs')} re-hand-off(s); the round ends"
        )
        await self._planning_rejected(
            intent, reason="provider_outcome_unknown", detail=dict(detail)
        )


    async def _give_up_blocked_assured_attempt(
        self, intent: DispatchIntent, *, detail: Mapping[str, Any]
    ) -> None:
        """End an assured Worker turn stuck on an unknown Provider outcome.

        Unlike :meth:`_give_up_blocked_attempt` nothing is released: the Assurance
        lane keeps the original UNKNOWN grants and the reservation stays held,
        visible and counted, until an observation settles it (count rule 2026-09-24).
        """

        self._import_usage(intent)
        self._settle_intent(intent, "FAILED")
        attempt = self.store.get_attempt(intent.subject_id)
        if attempt is None:
            return
        self.commit.mark_attempt_lost(attempt.id, reason="provider_outcome_unknown")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt.id, cancel=True)
        self._note(f"attempt {attempt.id} LOST: unknown Provider outcome for "
                   f"{detail.get('waited_seconds')}s on the Assurance lane; reservation held")


    async def _fail_runtime_unavailable(
        self,
        mission: Mission,
        *,
        reason: str,
        detail: Mapping[str, Any],
    ) -> None:
        """Named stop: MissionFailed + runtime_unavailable, never PLANNING with no reason."""

        current = self.store.get_mission(mission.id)
        if current is None or current.status in TERMINAL_MISSION:
            return
        self._dispatch_h4_repair_trigger(
            current,
            event_type="RuntimeUnavailable",
            trigger_ref=current.id,
            detail={"reason": reason, **dict(detail)},
        )
        payload = dict(detail)
        if current.status is MissionStatus.PLANNING:
            self._commit_fail_planning(
                current.id,
                reason=reason,
                detail=payload,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            )
        else:
            self._commit_fail_mission(
                current.id,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                detail={"reason": reason, **payload},
            )
        await self._release_mission(current.id)
        self._note(f"mission {current.id}: consecutive after-handoff UNKNOWN → runtime_unavailable")

    # --------------------------------------------------------------- decide
    async def _defer_for_profile(
        self, mission: Mission, task: Task, unavailable: RoutingUnavailable
    ) -> bool:
        """S6-06 (D6-5'): the profile the Task needs is cooling down and has no fallback —
        the Task waits visibly (no Attempt, the loop stays alive) for at most
        ``profile_wait_seconds`` measured on the store clock, then stops explicitly."""

        now = self.store.now
        since = self._deferred.get(task.id)
        if since is None:
            self._deferred[task.id] = now
            self._note(
                f"task {task.id}: waiting for runtime profile {unavailable.profile_id!r} "
                f"(unavailable until {unavailable.until})"
            )
            return False
        if now - since < self._config.profile_wait_seconds:
            return False
        self._deferred.pop(task.id, None)
        self._commit_stop_task(
            task.id,
            stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            detail={
                "profile_id": unavailable.profile_id,
                "waited_seconds": round(now - since, 3),
                "profile_wait_seconds": self._config.profile_wait_seconds,
                "unavailable_until": unavailable.until,
            },
        )
        await self._release_mission(mission.id)
        self._note(
            f"task {task.id} stopped: runtime profile {unavailable.profile_id!r} unavailable "
            f"for {now - since:.1f}s"
        )
        return True

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

    @property
    def pressure(self) -> BackpressureState:
        return self._pressure

    def _tool_call_room(self, mission: Mission, task: Task) -> tuple[int | None, int | None]:
        """(reservable now, not yet spent) on the Task → Mission → Global chain."""

        accounts = [task_account(task.id), mission_account(mission.id)]
        reservable: int | None = None
        spent_room: int | None = None
        with self.store.transaction():
            # the Global account of the Mission's creation month is its pool's parent (车道 P)
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

    async def _stop_conditions_reached(self, mission: Mission, new_mode: HierarchicalDispatch) -> bool:
        """第 2 批车道 H（H06，原计划 §5 ``stop_conditions`` / §19.1）：任务自带的两种计数型停止。

        Harness 只数：规划轮之间知识库 / 验收记录有没有新增、结果内容哈希重复了多少（``stop_conditions``
        模块）。达到上限不直接停——与"没有可派发的工作"同一条路，先把事实交给规划器一次（每个计划版本
        每个条件一条请求，跨版本累计问满 ``max_planning_attempts`` 次不再问）；规划器了结了请求、这一
        版计划没有改动（计划改了版本号就变，这条请求随之过时），才按条件的名字停。规划器还没答、或在
        等人时不停。返回 True 表示这一轮写了事件（记了请求或停了任务）。
        """
        from . import planning_repair_requests as repair_requests
        from . import stop_conditions as stop_rules

        policy = self._config.deployment_policy
        if not stop_rules.parse_stop_conditions(mission.stop_conditions, policy):
            return False
        events = tuple(self.store.iter_events(mission.id))
        reached = stop_rules.reached_stop_conditions(
            mission, policy, streak=stop_rules.knowledge_streak(events),
            hashes=stop_rules.result_hashes(self.store, mission.id, events=events))
        if not reached:
            return False
        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError) as error:
            self._note(f"mission {mission.id}: stop conditions reached but the plan is unreadable ({error})")
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
                roots = tuple(str(network.occurrence(item).task_id) for item in network.root_occurrence_ids)
                try:
                    recorded = repair_requests.record_request(
                        new_mode, mission.id, event_type="NoDispatchableWork",
                        trigger_refs=roots or (mission.id,), source_key=source_key,
                        detail={"reason": "stop_condition_reached", "conditions": reached,
                                "plan_revision": plan_revision,
                                "explanation": ("任务自带的停止条件已达上限（只是计数）；这一版计划不改动，"
                                                "任务就按该条件停止"),
                                repair_requests.NO_CHANGE_SETTLES: True},
                        scope=tasks + tuple(str(spec.occurrence_id) for spec in network.occurrences))
                except (GraphIntegrityError, ContractError, StoreError) as error:
                    self._note(f"mission {mission.id}: stop condition could not be handed to the Planner ({error})")
                    return False
                if recorded:
                    self._note(f"mission {mission.id}: stop condition {name} reached; the Planner is asked "
                               "once for this plan revision before the Mission is stopped")
                return recorded
            planner_asked: dict[str, Any] | None = {"request_id": None, "asked_before": asked_before, "cap": cap}
        else:
            if not state["addressed"]:
                return False  # 规划器还没答：等
            planner_asked = dict(state)
        if (self._has_pending_planning_waits(mission.id)
                or self.store.list_approvals(mission.id, "PENDING")):
            return False  # 在等人或等规划器的别的事：不是停的时候
        self._commit_fail_mission(
            mission.id,
            stop_reason=stop_rules.reason_for(name),
            detail={"conditions": reached, "plan_revision": plan_revision, "planner_asked": planner_asked,
                    "stop_conditions": list(mission.stop_conditions)},
        )
        self._note(f"mission {mission.id}: stop condition {name} reached and the plan stayed; stopped")
        return True

    async def _decide(self, mission: Mission) -> bool:
        # Review F1, before anything else: on a deployment with no assembly a Mission is
        # not scheduled and not judged.
        if self._assembly_missing(mission, at="decide"):
            return False
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return False
        from ..storage.planning_human_store import PlanningHumanStore
        if PlanningHumanStore(self.store).pending(mission.id):
            return False
        tasks = self.store.list_tasks(mission.id)
        if not tasks or mission.status is not MissionStatus.ACTIVE:
            return False
        from .operation_runtime import (
            advance_operation_outcomes, dispatch_materialized_operations,
            ensure_assured_proposal_reviews, recover_materializations,
        )

        if ensure_assured_proposal_reviews(self, mission.id):
            return True
        from .system_operations import prepare_system_operations

        if prepare_system_operations(self, mission.id):
            return True
        if recover_materializations(self, mission.id):
            return True
        if await dispatch_materialized_operations(self, mission.id):
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
                # 2026-10-06（Assurance §7.2，车道 O）：带动作的任务判定之后每轮仍走这里——批准被撤销 /
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
        # 第 2 批车道 H（H06）：任务自带的计数型停止条件——达到上限先问规划器一次，停在原地才停
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
            concurrency_limit=min(
                int(bound["mission_concurrency"]), self._config.max_concurrency
            ),
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

        from .root_review import RootReviewCoordinator

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

        from .root_review import RootReviewStatus

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
        from functools import partial

        from .composition_review import CompositionAcceptanceAssembly

        return CompositionAcceptanceAssembly(
            self.store, self.commit, dispatch=dispatch, issued_by=self._owner,
            ask_person=partial(self._ask_person_to_adjudicate_compound, mission, dispatch),
            on_rejected=partial(self._request_composition_repair, mission, dispatch),
            on_deferred=partial(self._composition_deferred, mission),
        )

    def _composition_deferred(self, mission: Mission, occurrence_id: str, task_id: str,
                              goal_type: str, error: Exception) -> None:
        """片 B：中间目标这一轮出不了结论（审阅开不起来、提交被拒）——记一条事件。

        每轮循环都会重试，所以同一个目标、同一个原因只记一次。只陈述事实，不据此做任何决定。
        """
        reason = f"{type(error).__name__}: {error}"[:600]
        with self.store.transaction():
            append_hierarchical_event(
                self.store, "CompositionReviewDeferred", mission.id, task_id=task_id,
                key=f"{occurrence_id}:{content_hash_of(reason)[:16]}",
                payload={"occurrence_id": occurrence_id, "task_id": task_id, "goal_type": goal_type,
                         "reason": reason})
        self._note(f"mission {mission.id}: composition review of {occurrence_id} deferred ({reason})")

    def _register_human_question(self, mission: Mission, new_mode: Any, *, decision_id: str, subject_key: str,
                                 payload: Any, current: Any, next_ordinal: int,
                                 repair_context: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """登记规划器的问题；同一主题、同一问题已经答过就沿用那次回答，不再打扰人。

        2026-10-01（审阅升级具名后续）：真机第 4 局规划器对同一件事连问 13 次直到次数用完。
        带修复上下文的问题（补偿、裁决）各有各的主体，不去重。
        """
        from ..storage.planning_human_store import PlanningHumanStore

        questions = PlanningHumanStore(self.store)
        binding = {"plan_revision": current.plan_revision, "requirements_revision": current.requirements_revision}
        previous = None if repair_context is not None else questions.find_answered(
            mission.id, subject_key, payload.to_json().get("question"))
        if previous is not None:
            row = questions.register_reusing_answer(
                decision_id=decision_id, mission_id=mission.id, subject_key=subject_key, payload=payload,
                request_binding=binding, next_ordinal=next_ordinal, previous=previous)
            return row, {"question_id": decision_id, "state": row["state"], "reused_from": previous["decision_id"]}
        row = questions.register(decision_id=decision_id, mission_id=mission.id, subject_key=subject_key,
                                 payload=payload, request_binding=binding, next_ordinal=next_ordinal,
                                 repair_context=repair_context)
        return row, {"question_id": decision_id, "state": row["state"]}

    def _ask_person_to_adjudicate_compound(
        self, mission: Mission, dispatch: Any, record: Any, task_id: str, occurrence_id: str
    ) -> bool:
        """2026-10-01（第 3 项）：中间目标的组合审阅复审后仍判不下来 → 同根终审，问人裁决。"""
        decision_id = "adjudicate-compound:" + str(record.record_id)
        if self._ruling_question_stale(decision_id):
            return self._request_composition_repair(
                mission, dispatch, record, None, task_id, occurrence_id, ruling_stale=True)
        return self._ask_person_to_adjudicate(
            mission, record, target_id=str(task_id), subject_key=str(task_id),
            decision_id=decision_id,
            intro="中间目标「" + str(occurrence_id) + "」的组合审查两位审阅员都判不下来，"
                  "需要你裁决这一部分拼起来是否合格。",
            extra={"package_id": str(record.package_id), "occurrence_id": str(occurrence_id)})

    def _request_composition_repair(
        self, mission: Mission, dispatch: Any, record: Any, package: Any, task_id: str,
        occurrence_id: str, *, ruling_stale: bool = False,
    ) -> bool:
        """2026-10-01（第 3 项）：组合审阅打回 / 拒绝（或人裁决打回）→ 一条修复请求交规划器。

        走验收失败同一条路（``PlanningRepairRequested``，触发步骤 = 该中间目标），规划器
        在修复轮里拿到审阅员的具体意见；同一份记录只记一次。
        """
        from .planning_repair_requests import record_request
        from .review_adjudication import adjudication_of

        ruling = adjudication_of(self.store, str(record.record_id))
        produced = record_request(
            dispatch, mission.id, event_type="VerifierAcceptanceRejected",
            trigger_refs=(str(task_id),), source_key="composition-review:" + str(record.record_id),
            detail={"source": "composition_review", "record_id": str(record.record_id),
                    "package_id": str(record.package_id), "occurrence_id": str(occurrence_id),
                    "verdict": str(record.verdict), "findings": self._review_record_findings(record),
                    **(_RULING_STALE if ruling_stale else {}),
                    **({"human_ruling": ruling} if ruling is not None else {})})
        if produced:
            self._note(f"mission {mission.id}: composition review of {occurrence_id} concluded "
                       f"{record.verdict!s}; repair requested")
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
            mission, record, target_id=str(state.task_id), subject_key=str(state.task_id),
            decision_id=decision_id,
            intro="最终审查两位审阅员都判不下来，需要你裁决整个任务的产出是否合格。",
            extra={"package_id": str(package.package_id)})

    def _ask_person_to_adjudicate_outcome(self, mission: Mission, record: Any, binding: Any) -> bool:
        """阶段 C 第 3 条：发布结果的审查判不下来（含审阅员两次回复都无法采用）→ 问人裁决。

        裁决"通过"后按已有的使用证书路径验收这次发布；"打回"或题目过期则这项效果核不完，
        由卡死确认如实交给规划器。"""
        return self._ask_person_to_adjudicate(
            mission, record, target_id=str(binding.operation_occurrence_id),
            subject_key=str(binding.operation_occurrence_id),
            decision_id="adjudicate-outcome:" + str(record.record_id),
            intro="一次对外操作的结果审查两位审阅员都判不下来，需要你裁决这次操作的结果是否合格。",
            extra={"package_id": str(record.package_id), "effect_key": str(binding.effect_key)})

    def _ruling_question_stale(self, decision_id: str) -> bool:
        """The person was asked to rule and the question was retired unanswered (the plan,
        the requirements or the management epoch changed).  No ruling will come: reported
        to the Planner as "no verdict", once per record, never re-asked by this loop."""
        from ..storage.planning_human_store import PlanningHumanStore

        row = PlanningHumanStore(self.store).get(decision_id)
        return row is not None and row["state"] == "STALE"

    def _ask_person_to_adjudicate(
        self, mission: Mission, record: Any, *, target_id: str, subject_key: str,
        decision_id: str, intro: str, extra: Mapping[str, Any], task_id: str | None = None,
    ) -> bool:
        """One blocking two-option question per official record; its answer becomes the
        ``AssuranceReviewAdjudicated`` receipt (never a planner round). True when this
        call registered the question or consumed its answer; False while waiting."""
        from ..contracts.planning_decisions import HumanOptionV1, RequestHumanDecision
        from ..storage.htn_store import HtnStore
        from ..storage.planning_human_store import PlanningHumanStore

        questions = PlanningHumanStore(self.store)
        row = questions.get(decision_id)
        if row is None:
            findings = "\n".join(
                f"- {item.criterion_id}：{'; '.join(item.limitations) or str(item.verdict)}"
                for item in record.criteria if str(item.verdict) != "PASS"
            ) or "-（审阅员没有写明疑点）"
            question = RequestHumanDecision(
                intro + "\n审阅员的疑点：\n" + findings
                + "\n选“通过”则按合格处理；选“打回”则交规划器修改后重做。",
                (HumanOptionV1("pass", "通过"), HumanOptionV1("fail", "打回")), True)
            htn = HtnStore(self.store)
            plan = htn.active_plan_revision(mission.id)
            requirements = htn.latest_requirements_revision(mission.id)
            with self.store.transaction():
                questions.register(
                    decision_id=decision_id, mission_id=mission.id, subject_key=subject_key,
                    payload=question,
                    request_binding={"plan_revision": 0 if plan is None else int(plan.revision),
                                     "requirements_revision": 0 if requirements is None else int(requirements.revision)},
                    next_ordinal=self._next_planning_ordinal(mission.id),
                    repair_context={"kind": "review_adjudication", "record_id": str(record.record_id),
                                    "target_id": target_id, **dict(extra)})
                append_hierarchical_event(
                    self.store, "PlanningHumanRequested", mission.id, key=decision_id,
                    payload={"decision_id": decision_id, "question_id": decision_id, "state": "PENDING",
                             "origin": "review_adjudication", "record_id": str(record.record_id)})
            self._note(f"mission {mission.id}: review {record.record_id} inconclusive twice; asked the person to rule")
            return True
        if row["state"] != "ANSWERED":
            return False  # waiting for the person; the idle verdict counts the pending question
        if self.store.get_receipt("assurance-review-adjudicated:" + str(record.record_id)) is not None:
            return False  # already consumed
        answer = row["answer"] or {}
        with self.store.transaction():
            self.commit.adjudicate_review_record(
                mission.id, record, target_id=target_id, decision=str(answer.get("answer")),
                note="", principal_id=str(answer.get("principal_id") or ""),
                decision_receipt_hash=str(answer.get("receipt_hash") or ""), request_id=decision_id,
                task_id=task_id or target_id)
        self._note(f"mission {mission.id}: the person ruled {answer.get('answer')} on review {record.record_id}")
        return True

    def _request_root_review_repair(
        self, mission: Mission, new_mode: HierarchicalDispatch, state: Any, *,
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
        from .planning_repair_requests import record_request
        from .review_adjudication import adjudication_of

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
            new_mode, mission.id, event_type="VerifierAcceptanceRejected",
            trigger_refs=(str(state.task_id),), source_key=source_key,
            scope=tuple(sorted({str(spec.occurrence_id) for spec in network.occurrences}
                               | {str(spec.task_id) for spec in network.occurrences})),
            detail={"source": "root_review", "record_id": str(record.record_id),
                    "package_id": str(package.package_id), "verdict": str(record.verdict),
                    "findings": self._review_record_findings(record),
                    "repair_round": len(requested) + 1, "max_repairs": limit,
                    **(_RULING_STALE if ruling_stale else {}),
                    **({"human_ruling": ruling} if ruling is not None else {})})
        if produced:
            self._note(f"mission {mission.id}: the final review concluded {record.verdict!s}; "
                       f"repair requested ({len(requested) + 1}/{limit})")
        return produced

    @staticmethod
    def _review_record_findings(record: Any) -> list[dict[str, Any]]:
        """Every criterion the reviewer did not pass, in the reviewer's own words."""

        return [
            {"criterion_id": str(item.criterion_id), "verdict": str(item.verdict),
             "limitations": list(item.limitations)}
            for item in record.criteria if str(item.verdict) != "PASS"
        ][:16]

    def _root_review_request_keys(self, mission_id: str) -> list[str]:
        """The final-review rejections already handed to the Planner, one key per record."""

        from .planning_repair_requests import REQUESTED

        return [
            str(event.payload.get("source_key"))
            for event in self.store.iter_events(mission_id)
            if event.type == REQUESTED
            and str(event.payload.get("source_key", "")).startswith("root-review:")
        ]

    def _final_review_findings_for_workers(self, mission_id: str) -> list[dict[str, Any]]:
        """The findings of the latest final review that was handed back to the Planner."""

        from .planning_repair_requests import REQUESTED

        latest: list[dict[str, Any]] = []
        for event in self.store.iter_events(mission_id):
            if event.type != REQUESTED:
                continue
            if not str(event.payload.get("source_key", "")).startswith("root-review:"):
                continue
            context = (event.payload.get("request") or {}).get("context") or {}
            latest = [dict(item) for item in context.get("findings") or () if isinstance(item, Mapping)]
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
        for prefix, name in (("assurance-mission-final:", "final_review"),
                             ("assurance-composition:", "composition_review"),
                             ("assurance-operation-outcome:", "operation_outcome_review")):
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

    def _exhausted_reviews(self, mission_id: str, prefix: str) -> list[dict[str, str]]:
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
        from .failure_classes import review_exhausted_by_interruption

        found = []
        for (raw,) in rows:
            payload = json.loads(raw or "{}")
            key = str(payload.get("review_key", ""))
            if key.startswith(prefix):
                found.append({"reason": str(payload.get("reason", "")), "review_key": key,
                              "interrupted": review_exhausted_by_interruption(self.store, key)})
        return found

    def _inconclusive_reviews(self, mission_id: str) -> list[dict[str, str]]:
        """Inconclusive official records the person was asked to rule on and has not passed:
        ``ruling`` is ``pending`` / ``stale`` / ``fail``.  Read from the questions this loop
        registered (one per record), so every kind of review is reported the same way."""
        from ..storage.planning_human_store import PlanningHumanStore
        from .review_adjudication import adjudication_of

        found = []
        for row in PlanningHumanStore(self.store).list(mission_id):
            decision_id = str(row["decision_id"])
            if not decision_id.startswith("adjudicate-"):
                continue
            kind, _, record_id = decision_id[len("adjudicate-"):].partition(":")
            ruling = adjudication_of(self.store, record_id)
            if ruling is not None and ruling.get("decision") == "pass":
                continue
            found.append({"kind": kind, "record_id": record_id,
                          "ruling": "fail" if ruling is not None
                          else "stale" if row["state"] == "STALE" else "pending"})
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

        from .root_review import RootReviewStatus

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
                "reason": ("root_review_cut_budget_spent"
                           if state.status is RootReviewStatus.CUT_BUDGET_SPENT else "root_review_rejected"),
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

        from .planning_repair_requests import pending_requests

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

        from ..assurance.codec import AssuranceError
        if self._assurance_reviews is None:
            raise ContractError("Assurance review builder is not installed for MISSION_FINAL")
        from .assurance_purpose_reviews import purpose_review_key
        review_key = purpose_review_key("MISSION_FINAL", mission.id, str(package.package_id))
        already = self.store.connection.execute(
            "SELECT 1 FROM assurance_review_invocations WHERE mission_id=? AND review_key=?",
            (mission.id, review_key)).fetchone() is not None
        try:
            self._assurance_reviews.ensure_mission_final(
                mission, package=package, dispatch=coordinator.dispatch)
        except AssuranceError as error:
            self._note(f"mission {mission.id}: Assurance MISSION_FINAL review unavailable ({error})")
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

    def _hierarchical_judgment_inputs(
        self, mission: Mission, new_mode: HierarchicalDispatch, tasks: Sequence[Task]
    ) -> list[UpstreamInput]:
        """The Mission Judge's tree for a hierarchical Mission (P2.3k / defect N3).

        Read from what the root ``GoalResolution`` was formed out of and nothing else:
        the CURRENT acceptances of the adopted plan (``root_contributions``), each
        acceptance's accepted artifacts, and the outputs P2.3h indexed on declared
        ports.  One path written by several contributions is not a conflict here —
        ordering in this mode is the typed network's, not ``dependency_ids`` — so the
        rule is override, weighted ``(criterion-linked, port-indexed, acceptance
        order)``: the output of a step the plan made answerable for a root criterion
        wins, then an output the reviewer read at a declared port, then the later
        acceptance (the clock is the tie-break, never the rule).

        P2.3k verification P1-1 (AER I05, "what the reviewer saw is what is
        delivered"): an output a root criterion is linked to is **never dropped**.
        When two criterion-linked port outputs land on one path — C1-r1's ``verify``
        ``report`` and ``summarize`` ``summary`` were both ``REPORT.md`` — the loser
        keeps its bytes in the tree under ``accepted-outputs/<task>/<port>/<path>``,
        and the record names both artifacts.  Nothing raises; what was superseded
        and where it was kept is written down once, in
        ``ArtifactMergeNotApplicableUnderHierarchical``.
        """

        semantics = new_mode.semantics()
        contributing = {
            item for ids in new_mode.root_contributions(mission.id).values() for item in ids
        }
        # ``list_acceptances`` orders by ``accepted_at_ms`` then id: the clock is the
        # tie-break between contributions, never the rule.
        acceptances = [
            item
            for item in semantics.list_acceptances(mission.id)
            if str(item.acceptance_id) in contributing
        ]
        rank_of = {str(item.acceptance_id): index for index, item in enumerate(acceptances)}
        linked = {
            str(item.task_id)
            for item in self._root_review(mission, new_mode).carried_criteria(mission.id)
        }
        tasks_by_id = {task.id: task for task in tasks}
        # path → every placement offered for it, each (weight, task_id, artifact, port)
        offered: dict[str, list[tuple[tuple[int, int, int], str, Artifact, str | None]]] = {}

        def place(task_id: str, artifact: Artifact | None, rank: int, port: str | None) -> None:
            if artifact is None:
                return
            weight = (int(task_id in linked), int(port is not None), rank)
            offered.setdefault(artifact.path, []).append((weight, task_id, artifact, port))

        for acceptance in acceptances:
            task_id = str(acceptance.task_id)
            rank = rank_of[str(acceptance.acceptance_id)]
            task = tasks_by_id.get(task_id) or self.store.get_task(task_id)
            for artifact_id in () if task is None else task.accepted_artifacts:
                place(task_id, self.store.get_artifact(artifact_id), rank, None)
        for row in semantics.list_acceptance_outputs(mission.id):
            acceptance_id = str(row.get("acceptance_id", ""))
            if acceptance_id not in rank_of:
                continue
            place(
                str(row.get("producer_task_ref", "")),
                self.store.get_artifact(str(row.get("artifact_id", ""))),
                rank_of[acceptance_id],
                str(row.get("output_port", "")) or None,
            )
        tree: dict[str, UpstreamInput] = {}
        superseded: list[dict[str, Any]] = []
        for path in sorted(offered):
            placements = sorted(offered[path], key=lambda item: item[0], reverse=True)
            _weight, kept_task, kept_artifact, _port = placements[0]
            tree[path] = UpstreamInput(
                kept_task, path, kept_artifact.content_hash, kept_artifact.id
            )
            losers: list[dict[str, Any]] = []
            seen_artifacts = {kept_artifact.id}
            for weight, task_id, artifact, port in placements[1:]:
                if (
                    artifact.id in seen_artifacts
                    or artifact.content_hash == kept_artifact.content_hash
                ):
                    continue  # the same bytes under another placement are not a loss
                seen_artifacts.add(artifact.id)
                kept_at = None
                if weight[0] and port is not None:
                    # I05: evidence a root criterion was judged on stays deliverable.
                    kept_at = f"accepted-outputs/{task_id}/{port}/{path}"
                    tree[kept_at] = UpstreamInput(
                        task_id, kept_at, artifact.content_hash, artifact.id
                    )
                losers.append(
                    {
                        "task_id": task_id,
                        "artifact_id": artifact.id,
                        "content_hash": artifact.content_hash,
                        "linked": bool(weight[0]),
                        "port": port,
                        "kept_at": kept_at,
                    }
                )
            if not losers:
                continue
            kept_by = "acceptance_order"
            if kept_task in linked:
                kept_by = (
                    "acceptance_order_between_linked"
                    if any(item["linked"] for item in losers)
                    else "criterion_link"
                )
            superseded.append(
                {
                    "path": path,
                    "kept_task_id": kept_task,
                    "kept_artifact_id": kept_artifact.id,
                    "kept_content_hash": kept_artifact.content_hash,
                    "kept_by": kept_by,
                    "superseded_task_ids": list(dict.fromkeys(item["task_id"] for item in losers)),
                    "superseded": losers,
                }
            )
        self.commit.record_artifact_merge_not_applicable(
            mission.id,
            subject=f"{mission.id}:judge:artifact-merge",
            contributions=len(acceptances),
            artifacts=len(tree),
            superseded=superseded,
        )
        if superseded:
            self._note(
                f"hierarchical mission {mission.id}: judgment tree keeps the criterion-linked / "
                f"port / last writer of {[item['path'] for item in superseded]}; a linked loser "
                "is kept under accepted-outputs/; the legacy merge is not applied"
            )
        return [tree[path] for path in sorted(tree)]

    async def _next_attempt(
        self,
        mission: Mission,
        task: Task,
        attempts: Sequence[Attempt],
        *,
        allocation: Mapping[str, Any] | None = None,
        admission: Any = None,
    ) -> bool:
        # Review F1: every other caller of this entry (repair, a manual drive) must be
        # fail-closed too, or the refusal in ``_decide`` would only cover the common path.
        if self._assembly_missing(mission, at="next_attempt"):
            return False
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return False
        # P2.3b / §18.5 rule 4: before anything else, a compound is refused here with
        # NEEDS_REFINEMENT.  The gate is ``form`` from the semantic binding, not the
        # status string — ``TaskStatus.READY`` on a compound is a rebuildable display
        # index and never a permission to dispatch.
        intercepted = new_mode.intercept_worker_dispatch(mission.id, task.id)
        if intercepted is not None:
            self._note(
                f"task {task.id} not dispatched: {intercepted.reason} "
                f"(occurrence {intercepted.occurrence_id})"
            )
            return False
        # P2.3c part 2 / TG §8.3: the dispatch transaction re-checks.  An
        # ``EligiblePrimitiveTask`` is *not* a capability — it records that a
        # controlled check passed at ``admitted_at_ms`` and grants nothing — so
        # this entry refuses a Task that arrived without one, however it got here.
        # ``_decide`` hands its admission down so the common path does not re-read
        # the whole plan; every other caller (repair, a manual drive)
        # pays for the fresh read rather than skipping the gate.
        if admission is None:
            admission = new_mode.admissions(mission.id).admission_for(task.id)
        # Review F11: ``isinstance`` and not a duck-typed ``gate_passed`` probe.
        # ``EligiblePrimitiveTask.gate_passed`` is guarded by the admission token,
        # but a structural test would let *any* object carrying a true attribute of
        # that name through this door — which is exactly the "admission with a flag"
        # shape ``admissions()`` is written to avoid.
        if not isinstance(admission, EligiblePrimitiveTask) or not admission.gate_passed:
            self._note(
                f"task {task.id} not dispatched: no admission from the readiness gate "
                "(§18.5 constraint 4)"
            )
            return False
        from .planning_runtime_block import pending_block
        if pending_block(self.store, mission.id) is not None:
            return False
        from .planning_retry import pending_retry_permit, retry_decision_required
        if (retry_decision_required(self.store, mission.id, task.id)
                and pending_retry_permit(self.store, mission.id, task.id) is None):
            return False
        # A Worker already in flight can produce another pending verification.
        # Account for that obligation before creating more work, across Missions.
        active_missions = {item.id for item in self._active_missions()}
        pending_verification = sum(
            stored.envelope.mission_id in active_missions
            for stored in self.store.list_results_by_verification("PENDING", "RUNNING")
        )
        potential_results = sum(
            attempt.mission_id in active_missions
            and self.store.find_result_for_attempt(attempt.id) is None
            for attempt in self.store.list_attempts_by_status(
                "PENDING", "CLAIMED", "RUNNING", "SUBMITTED", "VERIFYING"
            )
        )
        if pending_verification + potential_results >= self._config.max_pending_verifications:
            return False
        # a repair follows the last *failed* Attempt
        previous = next(
            (a for a in reversed(attempts) if a.status in TERMINAL_ATTEMPT and a.failure), None
        )
        feedback, verifier_feedback = retry_feedback(attempts, previous)
        for event in self.store.list_events(mission.id):  # D7-9': a person's notes, as data
            if event.type == "HumanCommentAdded" and event.payload.get("target_id") in {
                task.id,
                mission.id,
            }:
                feedback.append(
                    f"human note from {event.payload.get('principal_id')} "
                    f"(information, not a permission change): {event.payload.get('text')}"
                )
        # P2.3b / §24.1 decision 4: the Attempt starts from the resolved InputManifest,
        # so an ORDER-only predecessor contributes nothing.
        all_tasks = {t.id: t for t in self.store.list_tasks(mission.id)}
        try:
            inputs = new_mode.attempt_inputs(mission.id, task.id)
            # P2.3o: a patch (or any DATA) binding names the port document; the
            # files the producer changed overlay the consumer seed so verify /
            # inspect / summarize start from the accepted workspace, not the
            # unpatched snapshot.  ORDER-only predecessors still contribute
            # nothing — overlay only reads producers the manifest already named.
            if inputs:
                inputs = new_mode.overlay_attempt_inputs(mission.id, inputs)
        except WriteConflictPending as error:
            # 阶段 D：上游两步把同一个文件写成了两样；这一步不开工，等规划器处理写入冲突修复请求
            self._note(f"task {task.id} not dispatched: {error}")
            return False
        except ArtifactConflict as error:
            self._commit_stop_task(
                task.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            await self._release_mission(mission.id)
            self._note(f"task {task.id} stopped: artifact conflict ({error})")
            return True
        bound = self.policy_for(mission.id)  # step 9 (plan D9-4'): the Mission's own version
        role = self._template(role_for_task(task), mission.id)  # D5-9: approach
        role = self._hierarchical_worker_template(role, mission.id)
        untrusted = [str(p) for p in (mission.final_report or {}).get("untrusted_sources", [])]
        try:
            knowledge = self._gather_knowledge(mission, task, all_tasks)
        except RetrievalUnavailable as error:
            # S4-07 / D4-11': never "no knowledge" — degrade explicitly or block visibly
            count = self.commit.record_retrieval_unavailable(
                task.id, reason=str(error), policy=self._config.on_retrieval_failure
            )
            if self._config.on_retrieval_failure == "degrade":
                knowledge = KnowledgeContext.unavailable(str(error))
                self._note(f"task {task.id}: retrieval unavailable, degraded ({error})")
            else:
                if count >= self._config.max_retrieval_failures:
                    self._commit_stop_task(
                        task.id,
                        stop_reason=MissionStopReason.RETRIEVAL_UNAVAILABLE,
                        detail={"failures": count, "reason": str(error)},
                    )
                    await self._release_mission(mission.id)
                    self._note(f"task {task.id} stopped: retrieval unavailable {count} times")
                else:
                    self._note(f"task {task.id}: retrieval unavailable, blocked ({count})")
                return True
        task_kind = str((mission.final_report or {}).get("task_kind") or "code")
        try:
            decision = self._router_for(mission.id).route(
                role=role.name,
                task_kind=task_kind,
                previous_attempts=attempts,
                unavailable_until=self.commit.unavailable_until(),
                now=self.store.now,
            )
        except RoutingUnavailable as unavailable:
            return await self._defer_for_profile(mission, task, unavailable)
        placeholder = Attempt(
            id=ids.attempt_id(task.id, len(attempts) + 1),
            task_id=task.id,
            mission_id=mission.id,
            role=role.name,
            model=decision.model,
            prompt_version=role.prompt_version,
            context_version="pending",
            budget_reserved=task.budget,
            lease_owner=None,
            lease_expires_at=None,
            status=AttemptStatus.PENDING,
            retry_of=None if previous is None else previous.id,
            created_at=self.store.now,
            version=1,
            ordinal=len(attempts) + 1,
            creation_key="pending",
            input_id="attempt-input",
            task_version=task.version,
            feedback=tuple(feedback),
        )
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        source_binding = self._active_source_binding(mission.id)
        source_versions = source_binding.get("source_versions")
        source_paths = set(source_versions or {})
        if source_binding:
            untrusted = sorted(set(untrusted) | source_paths)
        previous_files = sorted(
            {*seed, *(item.path for item in inputs), *source_paths}
        )
        if previous is not None:
            try:
                previous_files = sorted(
                    set(self.assembled.workspaces.get(previous.id).list_files()) | source_paths
                )
            except Exception:  # noqa: BLE001
                pass
        if source_binding:
            previous_files = [
                path
                for path in previous_files
                if path in source_paths
                or not _under_source_root(path, source_binding["source_roots"])
            ]
        # 第 2 批车道 H（K04，原计划 §10 第 3 项）：父目标与直接上游从分层网络读——这一步所在做法
        # 细化的目标（原文、它负责的要求原文），与数据边上把产出交给它的生产者（目标、状态、已验收
        # 结论的摘要、交到这一步的产物）。``Task.dependency_ids`` 在分层下恒空，不再按它过滤。
        from .assurance_point_use import context_claims
        from .worker_context import parent_goal as read_parent_goal
        from .worker_context import upstream_steps

        try:
            network = new_mode.network(mission.id)
            step_parent = read_parent_goal(self.store, network, mission, task)
            upstream = upstream_steps(self.store, network, mission, task, inputs)
        except (GraphIntegrityError, ContractError, StoreError, LookupError) as error:
            # 读不到网络时如实说"读不到"，不编一个空的"没有上游"
            self._note(f"task {task.id}: hierarchical context unreadable ({error})")
            step_parent = {"data_not_instruction": True, "unavailable": str(error)[:300]}
            upstream = [{"unavailable": str(error)[:300]}]
        try:
            package = build_worker_package(
                mission,
                task,
                placeholder,
                mission_requirements=current_statements(self.store, mission),
                previous_attempts=attempts,
                verifier_feedback=verifier_feedback,
                workspace_files=previous_files,
                dependencies=upstream,
                knowledge=knowledge,
                untrusted_sources=untrusted,
                role=role.name,
                domain=self.commit.domain_for(mission.id),
                source_versions=source_versions,
                action_candidate_contract=self._worker_action_contract(mission, task),
            )
        except ContextRejected as error:
            self._commit_stop_task(
                task.id,
                stop_reason=MissionStopReason.CONTEXT_REJECTED,
                detail={"error": str(error)[:300]},
            )
            await self._release_mission(mission.id)
            self._note(f"task {task.id} stopped: worker package refused ({error})")
            return True
        if step_parent is not None:
            from ..context.context_builder import _seal

            # §10 第 3 项的另一半：父目标。与 dependencies（直接上游）一样是数据，不是指令。
            package = _seal({**dict(package.package), "parent_goal": step_parent})
        # P2.3c part 2d, decision 4: tell the leaf which output ports its own
        # occurrence declares.  The names are the plan's, not the model's — the
        # model supplies the *local key* (which file) and nothing else (TG design
        # §3.2).  An empty list means nothing downstream consumes this leaf, and
        # the envelope's ``outputs`` may then be omitted.
        declared_ports = new_mode.declared_output_ports_for(mission.id, task.id)
        if declared_ports:
            from ..context.context_builder import _seal

            package = _seal(
                {
                    **dict(package.package),
                    "declared_output_ports": {
                        "data_not_instruction": True,
                        "version": "declared-output-ports-v1",
                        "ports": [dict(item) for item in declared_ports],
                    },
                }
            )
        # P2.3h: tell the leaf which **root** criteria the plan hangs on it, with
        # the method's own ``evidence_requirement`` for each — the sentence its
        # output at the listed ports has to satisfy, because that output is what
        # the root review reads for that criterion.  Absent when it carries none.
        carried = new_mode.carried_root_criteria_for(mission.id, task.id)
        if carried:
            from ..context.context_builder import _seal

            package = _seal(
                {
                    **dict(package.package),
                    "carried_root_criteria": {
                        "data_not_instruction": True,
                        "version": "carried-root-criteria-v1",
                        "note": (
                            "the root (MISSION_FINAL) review judges each root_criterion_id "
                            "below on this task's accepted output at the listed ports; that "
                            "output must show what evidence_requirement states"
                        ),
                        "criteria": [dict(item) for item in carried],
                    },
                }
            )
        # A step that runs after the final review sent the task back sees what the
        # reviewer said — the findings, verbatim, as data.  What to do about them
        # was the Planner's decision and is in the step's own instructions.
        final_review_findings = self._final_review_findings_for_workers(mission.id)
        if final_review_findings:
            from ..context.context_builder import _seal

            package = _seal(
                {
                    **dict(package.package),
                    "review_feedback": {
                        "data_not_instruction": True,
                        "version": "final-review-feedback-v2",
                        "note": (
                            "the final review of the whole task returned these findings "
                            "before this step was dispatched"
                        ),
                        "findings": final_review_findings,
                    },
                }
            )
        from .scoped_content_review import task_content_prompt_scope
        from ..context.context_builder import _seal
        content_scope = task_content_prompt_scope(self.store, mission.id, task.id)
        # The Task contract is a durable document-assessment identity. Keep it
        # byte-equivalent to the committed Task; the separate content scope
        # narrows this turn's responsibility without rewriting that contract.
        package = _seal({**dict(package.package), "task_content_scope": content_scope})
        # D6-7: Mission ∩ Task ∩ Role ∩ Deployment, frozen into the intent below
        # P2.3u: a hierarchical read-only leaf also drops patch/apply-class tools.
        read_only = False
        operator_tool_limit = None
        semantic = new_mode.semantics().task_semantics_of(mission.id, task.id)
        read_only = semantic is not None and read_only_leaf(semantic)
        if semantic is not None and semantic.operator_ref is not None:
            operator = semantic.operator_ref
            operator_tool_limit = dict(self._config.deployment_policy.operator_tool_allowlists).get(
                f"{operator.id}@{operator.version}:{operator.content_hash}",
                () if self._config.deployment_policy.require_operator_tool_policy else None)
        allowed = effective_tools(
            mission_tools=mission.allowed_tools,
            task_tools=task.allowed_tools,
            role_tools=(*role.tool_names, *self._config.domain_tools),
            deployment=self._config.deployment_policy,
            read_only_leaf=read_only,
        )
        if operator_tool_limit is not None:
            allowed = tuple(name for name in allowed if name in operator_tool_limit)
        # D6-8: the Attempt's tool-call cap = the deployment's per-turn cap, narrowed by the
        # Task budget's own dimension; it is reserved up front and enforced at the gateway
        tool_cap = self._config.max_tool_calls_per_turn
        if task.budget.max_tool_calls is not None:
            tool_cap = min(tool_cap, task.budget.max_tool_calls)
        if self._tool_calls_limited(mission, task):
            # review P1-2: never reserve more than the chain can still hold; in-flight
            # reservations are not spending — only a spent dimension is exhaustion
            reservable, spent_room = self._tool_call_room(mission, task)
            if spent_room is not None and spent_room > 0 and reservable is not None:
                if reservable <= 0:
                    self._note(f"task {task.id}: tool calls all reserved in flight; waiting")
                    return False
                tool_cap = min(tool_cap, reservable)
        config = AgentConfig(
            name=f"{role.name}-{placeholder.ordinal}",
            instructions=role.instructions,
            model_profile_ref=decision.profile_id,  # the label *is* the pool it runs in
            tool_names=allowed,
            limits=AgentLimits(
                max_model_calls_per_turn=self._config.max_model_calls_per_turn,
                max_tool_calls_per_turn=tool_cap,
                turn_deadline_seconds=min(
                    self._config.turn_deadline_seconds,
                    float(task.budget.max_runtime_seconds or self._config.turn_deadline_seconds),
                ),
            ),
        )
        message = user_message_json(package.text)
        tokens = self._config.attempt_reserve_tokens
        critic_tail = None
        first_critic_binding: dict[str, Any] = {}
        if "critic_review" in task.verification_policy:
            try:
                critic_decision = self._route_service("critic", mission.id)
            except RoutingUnavailable as unavailable:
                return await self._defer_for_profile(mission, task, unavailable)
            first = self._first_critic_budget(critic_decision)
            if isinstance(first, FirstRequestBudget):
                first_reservation = self._first_critic_reservation(first)
                first_critic_binding = {
                    "first_critic_budget": {
                        "provider_input_cap": first.provider_input_cap.to_json(),
                        "output_ceiling": first.output_ceiling,
                        "minimum_tokens": first.minimum_tokens,
                    }
                }
                critic_tail = first_reservation
            else:
                first_critic_binding = {"first_critic_budget_unknown": first.reason}
                critic_tail = self._reservation(self._config.critic_reserve_tokens)
        self._deferred.pop(task.id, None)
        if self._pressure.is_raised:  # §18.5 "缩小每个 Attempt 预算" (D6-3 ④)
            tokens = max(4_000, int(tokens * self._config.reduced_reserve_ratio))
        if task.budget.max_tokens is not None:
            tokens = min(tokens, max(1, task.budget.max_tokens))
            # step 4: a repair reserves what the Task still has rather than failing on a
            # nominal share it no longer can afford (the reservation is a cap, not a spend)
            with self.store.transaction():
                remaining = self.commit.ledger.account(task_account(task.id)).remaining_tokens()
            if remaining is not None:
                critic_share = (
                    critic_tail.tokens
                    if critic_tail is not None
                    else self._config.critic_reserve_tokens
                    if "critic_review" in task.verification_policy
                    else 0
                )
                head_room = remaining - critic_share  # keep the Critic's own share free
                if 0 < head_room < tokens:
                    tokens = head_room
        try:
            attempt, _intent = self.commit.create_attempt(
                task.id,
                critic_tail=critic_tail,
                role=role.name,
                model=decision.model,
                prompt_version=role.prompt_version,
                context_version=package.context_version,
                reservation=replace(
                    self._reservation(tokens),
                    tool_calls=tool_cap if self._tool_calls_limited(mission, task) else 0,
                ),
                runtime_profile_id=decision.profile_id,
                routing=decision.to_json(),
                intent_config={
                    "agent_config": config.to_json(),
                    "message": message,
                    "attempt_id": placeholder.id,
                    "allowed_tools": list(allowed),
                    **self._service_config(decision),
                    "max_tool_calls": tool_cap,
                    "context_version": package.context_version,
                    "prompt_version": role.prompt_version,
                    "policy_version_id": self.policy_version_of(mission.id),
                    "task_version": task.version,
                    "role": role.name,
                    **first_critic_binding,
                    "knowledge": knowledge.frozen_ids,  # D4-10: what this Attempt saw
                    "retrieval_version": knowledge.retrieval.version,
                    "retrieval_status": knowledge.retrieval.status,
                    "context_builder_version": CONTEXT_BUILDER_VERSION,
                    "untrusted_sources": untrusted,
                    **({"read_only_leaf": True} if read_only else {}),
                    **source_binding,
                    "allocation": dict(
                        allocation or {}
                    ),  # D5-8': the §29.3 score it was granted on
                },
                input_hash=sha256_hex(message),
                retry_of=placeholder.retry_of,
                feedback=feedback,
                inputs=[item.to_json() for item in inputs],
                max_open_attempts=min(
                    int(bound["mission_concurrency"]), self._config.max_concurrency
                ),
                max_running_attempts=self._config.max_running_attempts,
                context_evidence=context_claims(knowledge, upstream),
            )
        except CommitRejected as error:
            from .commit_service import NonModelFailuresExhausted

            if isinstance(error, NonModelFailuresExhausted):
                # 2026-09-28：格式、服务、打断这类不扣次数的失败同一步已到上限——多半是服务
                # 或格式本身有问题，停下这一步并写明，不无限重做。
                self._commit_stop_task(
                    task.id,
                    stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                    detail={"reason": "non_model_failures_exhausted",
                            "failures": error.failure_count, "cap": error.cap},
                )
                await self._release_mission(mission.id)
                self._note(f"task {task.id} stopped: non_model_failures_exhausted ({error.failure_count})")
                return True
            self._note(f"task {task.id}: no new attempt ({error})")
            return False
        except BudgetExhausted as error:
            detail = {
                "dimension": error.dimension,
                "requested": error.requested,
                "remaining": error.remaining,
                "account": error.account_id,
            }
            reason = (
                MissionStopReason.MAX_ATTEMPTS_REACHED
                if error.dimension == "attempts"
                else MissionStopReason.BUDGET_EXHAUSTED
            )
            if is_global_account(error.account_id):
                # review P1-1 / D6-1': the deployment-wide pool ran out — no Task and no
                # Mission is to blame; the Mission stops with the Global scope named
                reason = MissionStopReason.BUDGET_EXHAUSTED
                self._commit_fail_mission(
                    mission.id, stop_reason=reason, detail={**detail, "scope": "global"}
                )
                self._note(
                    f"mission {mission.id} stopped: {reason} ({error.dimension}, global pool)"
                )
            elif error.account_id == mission_account(mission.id):
                # D3-12': the Mission pool itself is exhausted (any dimension) — no Task
                # is to blame and the stop reason is the pool's: budget_exhausted
                reason = MissionStopReason.BUDGET_EXHAUSTED
                self._commit_fail_mission(
                    mission.id, stop_reason=reason, detail={**detail, "scope": "mission"}
                )
                self._note(
                    f"mission {mission.id} stopped: {reason} ({error.dimension}, mission pool)"
                )
            else:
                self._commit_stop_task(task.id, stop_reason=reason, detail=detail)
                self._note(f"task {task.id} stopped: {reason} ({error.dimension})")
            await self._release_mission(mission.id)
            return True
        self._note(
            f"attempt {attempt.id} created (retry_of={attempt.retry_of}, inputs={len(inputs)})"
        )
        return True

    async def _judge(self, mission: Mission, tasks: Sequence[Task]) -> bool:
        key = judgment_key(tasks)
        cached = self.commit.criteria_judgment(mission.id, key)  # booked only for an arbitration
        evaluated = cached if cached is not None else await self._evaluate_criteria(mission, tasks)
        if evaluated is None:
            return True
        judgments, summary = evaluated
        ruled, created = self._arbitrated(mission, tasks, key, judgments)
        if ruled is None:  # D7-8' ②: a person rules first; the judgment is kept meanwhile
            if cached is None:
                self.commit.record_criteria_judgment(mission.id, key, judgments, summary=summary)
            return created or cached is None
        judged = self.commit.judge_mission(mission.id, judgments=ruled, summary=summary)
        self._note(f"mission {mission.id} judged: {judged.status} ({judged.stop_reason})")
        return True

    async def _evaluate_criteria(
        self, mission: Mission, tasks: Sequence[Task]
    ) -> tuple[list[dict[str, Any]], str] | None:
        """D21 / ORCH §12.4 / D3-9': judge the Mission's own success criteria on the
        *integrated* tree — the seed plus every Task's accepted artifacts applied in
        topological order — independently of the Task PASSes.  ``pytest:`` criteria run
        there, ``file:`` criteria are checked there and free-text criteria restate the
        certified grades of the official MISSION_FINAL review."""

        self._reimport_unsettled(mission)
        # P2.3k / defect N3: the tree is read from the root resolution's own
        # contributions, never from ``Task.dependency_ids`` (a materialised occurrence
        # leaves them empty by design, §18.5 constraint 4).
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError("hierarchical_assembly_missing: no judgment without the assembly")
        try:
            merged = self._hierarchical_judgment_inputs(mission, new_mode, tasks)
        except ArtifactConflict as error:
            self._commit_fail_mission(
                mission.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            self._note(f"mission {mission.id} failed at judgment: {error}")
            return None
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        files: dict[str, Path | bytes] = {}
        artifacts: list[Artifact] = []
        try:
            for item in merged:
                artifact = self.store.get_artifact(item.artifact_id)
                if artifact is None:
                    continue
                files[item.path] = read_verified(artifact)  # P3.2 D3: hash re-checked
                artifacts.append(artifact)
            # 2026-10-06 真实模型验收（编程题）：每次尝试的工作区都带着任务的资料，模型写的测试读它们；
            # 任务级判定树却只有产物，pytest 在这里找不到 sources/ 就全红，任务被判"要求未满足"。
            # 判定树同样带现行版本的资料（产物从不写在资料根下，不会覆盖）。
            for path, data in self._current_source_files(mission).items():
                files.setdefault(path, data)
        except ArtifactStoreError as error:
            self._commit_fail_mission(
                mission.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            self._note(f"mission {mission.id} failed at judgment: {error}")
            return None
        # P0-2: one judgment tree per orchestrator instance — another instance may be
        # running pytest in its own; the judgment Commit itself is idempotent
        view_id = f"{mission.id}-judge-{self._owner}"
        copy = self.assembled.workspaces.integrated_copy(view_id, seed=seed, files=files)
        self._register_copy(
            "judge",
            f"{view_id}-verify",
            mission_id=mission.id,
            attempt_id="",
            detail={"artifacts": sorted(a.id for a in artifacts), "seed": sorted(seed)},
        )
        terminal = terminal_task(list(tasks))
        stored = self.store.get_result(terminal.accepted_result_id or "")
        summary = "" if stored is None else stored.envelope.summary
        test_runs: dict[str, dict[str, Any]] = {}
        for criterion in current_statements(self.store, mission):
            if not criterion.startswith("pytest:"):
                continue
            target = criterion.removeprefix("pytest:").strip() or None
            if not self._config.deployment_policy.local_code_execution:
                # host support 0.9.8: a criterion from before the switch is judged unmet —
                # never run on this machine
                test_runs[criterion] = {
                    "passed": False,
                    "error": "local_code_execution is off in this deployment: pytest criteria are not run on this machine",
                    "stdout": "",
                }
                continue
            try:
                if target is not None:
                    copy.resolve(target)
                test_run = await run_pytest(
                    str(copy.root),
                    path=target,
                    timeout=self._config.test_timeout_seconds,
                    executor=self._executor,
                )
                test_runs[criterion] = {**test_run.to_json(), "passed": test_run.passed}
            except Exception as error:  # noqa: BLE001
                test_runs[criterion] = {"passed": False, "error": str(error), "stdout": ""}
        # The free-text criteria were judged by the official MISSION_FINAL review and
        # restated, certified, on the adopted root resolution; the judgment restates
        # the certified grades (Host real model run 20, 2026-09-23).
        assured_grades = self._assured_root_grades(mission, new_mode)
        judgments: list[dict[str, Any]] = []
        for ordinal, criterion in enumerate(current_statements(self.store, mission)):
            if criterion.startswith("pytest:"):
                outcome = test_runs.get(criterion, {})
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": bool(outcome.get("passed")),
                        "judge": "code_test",
                        "reason": (outcome.get("stdout") or outcome.get("error") or "")[-300:],
                    }
                )
            elif criterion.startswith("file:"):
                relative = criterion.removeprefix("file:")
                try:
                    met = copy.resolve(relative).is_file()
                except Exception:  # noqa: BLE001
                    met = False
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": met,
                        "judge": "rule_check",
                        "reason": "file exists" if met else "file missing",
                    }
                )
            elif criterion.startswith(ACTION_PREFIX):
                continue  # D7-7': judged from the action ledger by the caller
            else:
                grade = None if assured_grades is None else assured_grades.get(criterion)
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": grade == "PASS",
                        "judge": "assurance_review",
                        "source": "certified_root_resolution" if grade is not None else "unavailable",
                        "reason": "official MISSION_FINAL review, current root-resolution certificate: "
                        + str(grade)
                        if grade is not None
                        else "no certified root resolution judged this criterion",
                    }
                )
        return judgments, summary

    def _assured_root_grades(self, mission: Mission, new_mode: Any) -> dict[str, str] | None:
        """Criterion statement → certified grade from the adopted root resolutions
        of the Mission; None when they are not formed."""
        if new_mode is None:
            return None
        from ..storage.htn_store import HtnStore

        htn = HtnStore(self.store)
        network = new_mode.network(mission.id)
        duties = tuple(dict.fromkeys(str(d) for d in network.required_obligations))
        resolutions = [htn.adopted_goal_resolution(mission.id, duty) for duty in duties]
        if not resolutions or any(item is None for item in resolutions):
            return None
        grades: dict[str, str] = {}
        for resolution in resolutions:
            if str(resolution.validity) != "CURRENT" or str(resolution.verdict) != "ACCEPT":
                return None
            requirements = htn.get_requirements_revision(
                mission.id, int(resolution.requirements_version)
            )
            statements = {c.criterion_id: c.statement for c in requirements.criteria}
            for item in resolution.criteria:
                statement = statements.get(item.criterion_id)
                if statement is not None:
                    grades[statement] = str(item.verdict)
        return grades

    async def _decide_actions(self, mission: Mission, tasks: Sequence[Task]) -> bool:
        """D7-7' / D7-5': judgment in two stages.  ① The non-action criteria are judged once
        per integrated tree and put on the books.  ② Only then are the actions looked at: an
        approved (or L0/L1) action is handed off as the *last* step of the judgment, a
        revoked / expired one fails the Mission (a rejected or failed system-prepared action
        is the operation line's: ``system_operations`` resubmits, asks the planner or stops).

        Assurance 1.1 §7.2（2026-10-06 车道 O）: the judgment does **not** wait for every
        required action to be SUCCEEDED.  The business requirement is judged by the root
        resolution; whether an effect really took hold is the closeout's to converge
        (UNKNOWN → BLOCKED_UNKNOWN, in flight → root scope unmet), and only the unique
        final writer completes the Mission.  Once judged, this method keeps running every
        cycle so a later revocation / expiry of an approval still stops the Mission here and
        a handoff-ready action is still handed off; a later rejection / failure of the action
        is the operation line's (``system_operations``: resubmit, ask the planner or a named
        stop), not stopped here.  With nothing to do it answers "no progress" and ``run()``
        goes idle (never a no-progress FAILED)."""

        progressed = False
        key = judgment_key(tasks)
        cached = self.commit.criteria_judgment(mission.id, key)
        if cached is None:
            evaluated = await self._evaluate_criteria(mission, tasks)
            if evaluated is None:
                return True
            self.commit.record_criteria_judgment(
                mission.id, key, evaluated[0], summary=evaluated[1]
            )
            cached = evaluated
            progressed = True
        plain, summary = cached
        ruled, created = self._arbitrated(mission, tasks, key, plain)
        if ruled is None:
            return progressed or created  # a person rules on a Verifier conflict first
        plain = ruled
        if not all(bool(item.get("met")) for item in plain):
            self._judge_with_actions(mission, plain, summary, unmet="another criterion is unmet")
            return True
        self.commit.expire_approvals(mission.id)
        actions = {
            criterion: self.commit.action_for_criterion(mission.id, criterion, self._connectors)
            for criterion in current_statements(self.store, mission)
            if criterion.startswith(ACTION_PREFIX)
        }
        for criterion, action in actions.items():
            state = None if action is None else str(action["state"])
            if action is not None and state in {"REVOKED", "EXPIRED"}:
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=MissionStopReason.APPROVAL_REJECTED,
                    detail={
                        "kind": str(state).lower(),
                        "criterion": criterion,
                        "action_key": action["action_key"],
                    },
                )
                await self._release_mission(mission.id)
                return True
            # 2026-10-06（Assurance §7.2，车道 O）：系统准备的动作被人拒绝（REJECTED）、没生效（FAILED）、
            # 还没产生（申请单在等审阅 / 批准 / 物化）或被撤销后待重交，都归系统操作那条线
            # （``system_operations`` / ``operation_runtime``：内容换了就替代重交、证实没生效按原内容重交到
            # 上限、否则交规划器一次或具名停下）；判定不再等效果，这里就不第二次判它们，也不判"没有候选"。
            # 撤销 / 过期的批准没有别的接手方，仍在这里停任务。
        waiting = any(
            a is not None and a["state"] not in HANDOFF_READY_STATES | {"SUCCEEDED"}
            for a in actions.values()
        )  # review P2-1: nothing is handed off while another action of the Mission waits
        for criterion, action in actions.items():
            if waiting or action is None or action["state"] not in HANDOFF_READY_STATES:
                continue
            key_ = str(action["action_key"])
            done = await self.actions.hand_off(key_)
            after = self.store.get_action(key_) if done is None else done
            if after is not None and after["state"] in HANDOFF_READY_STATES:
                from ..contracts.error_table import handoff_refusal_transient
                if handoff_refusal_transient(self.actions.last_refusal.get(key_, "")):
                    continue  # stays handoff-ready; tried again next round (阶段 B 裁决第 5 类)
                # the deployment will not run it (cap, budget, switched off): it cannot happen
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=MissionStopReason.ACTION_FAILED,
                    detail={
                        "criterion": criterion,
                        "action_key": key_,
                        "reason": "handoff_refused:" + self.actions.last_refusal.get(key_, ""),
                    },
                )
                await self._release_mission(mission.id)
                return True
            self._note(
                f"mission {mission.id}: action {key_} → {None if after is None else after['state']}"
            )
            progressed = True
        if progressed:
            return True
        if self.commit.assured_closeout_pending(mission.id):
            # Handoff item 7 / §7.2: judged; the CLOSEOUT consumer converges the effects
            # (BLOCKED_UNKNOWN / DRAINING keep the Mission ACTIVE) and the unique final
            # writer completes it.  Nothing to re-judge: idle, not stalled.
            return False
        # 2026-10-06（Assurance §7.2，车道 O）：判定不等每个要求动作 SUCCEEDED——业务要求由根结论判定，
        # 效果（发布有没有真的生效）由收尾核对：结果不明 → BLOCKED_UNKNOWN、在途 → 范围未满足，
        # 收敛后才 READY → 完成。
        self._judge_with_actions(mission, plain, summary, unmet=None)
        return True

    def _judge_with_actions(
        self,
        mission: Mission,
        plain: Sequence[Mapping[str, Any]],
        summary: str,
        *,
        unmet: str | None,
    ) -> None:
        by_criterion = {str(item.get("criterion")): dict(item) for item in plain}
        judgments: list[dict[str, Any]] = []
        for ordinal, criterion in enumerate(current_statements(self.store, mission)):
            if not criterion.startswith(ACTION_PREFIX):
                judgments.append(by_criterion[criterion])
                continue
            action = self.commit.action_for_criterion(mission.id, criterion, self._connectors)
            state = None if action is None else str(action["state"])
            # 2026-10-06（Assurance §7.2，车道 O）：业务要求由根结论判定；这项效果有没有真的生效由
            # 收尾核对（结果不明 → BLOCKED_UNKNOWN、在途 → 范围未满足），完成只由唯一的收尾写方写。
            # 这一行记的是判定时动作走到了哪一步，不是"效果已生效"。
            judgments.append(
                {
                    "criterion": criterion,
                    "met": unmet is None,
                    "judge": "assurance_closeout",
                    "reason": unmet or f"效果由收尾核对后才完成；判定时动作状态：{state or '尚未产生'}",
                    "action_key": None if action is None else action["action_key"],
                    "action_state": state,
                }
            )
        judged = self.commit.judge_mission(mission.id, judgments=judgments, summary=summary)
        self._note(f"mission {mission.id} judged: {judged.status} ({judged.stop_reason})")


def _under_source_root(path: str, roots: Sequence[str]) -> bool:
    return any(
        path.casefold() == root.rstrip("/").casefold()
        or path.casefold().startswith(root.rstrip("/").casefold() + "/")
        for root in roots
    )


def sha256_hex_text(content: str | bytes) -> str:
    import hashlib

    return hashlib.sha256(
        content if isinstance(content, bytes) else content.encode("utf-8")
    ).hexdigest()


__all__ = ("FAULT_POINTS", "InjectedCrash", "Orchestrator")


def retry_feedback(
    attempts: Sequence[Attempt], previous: Attempt | None
) -> tuple[list[str], list[Mapping[str, Any]]]:
    """What the repair Attempt is told about the failures before it.

    2026-09-26 真机文档任务: a provider turn failure in between hid the earlier
    content rejection, so the next Attempt repeated the rejected mistake.  Walk back
    past turn failures to the most recent failure that judged the content.
    """
    feedback: list[str] = []
    verifier_feedback: list[Mapping[str, Any]] = []
    if previous is None:
        return feedback, verifier_feedback
    chain: list[Attempt] = []
    for earlier in reversed(attempts[: list(attempts).index(previous) + 1]):
        if earlier.status not in TERMINAL_ATTEMPT or not earlier.failure:
            continue
        chain.append(earlier)
        if earlier.failure.get("reason") != "turn_failed":
            break
    for failed in chain:
        failure = failed.failure or {}
        reason = str(failure.get("reason"))
        # 2026-09-25 UI 全量点击: an "inconclusive" failure (e.g. one claim's missing
        # limitation) carries the same verifier failures; the repair Attempt used to
        # get only "inconclusive: " and could not know which pair to fix.
        if reason in {"verification_failed", "inconclusive"}:
            for item in failure.get("failures", []):
                if isinstance(item, Mapping):
                    feedback.append(f"{item.get('layer')}: {item.get('summary')}")
                    verifier_feedback.append(dict(item))
        else:
            feedback.append(f"{reason}: {failure.get('error', '')}")
    return feedback, verifier_feedback
