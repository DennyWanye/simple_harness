# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Commit Service: the single logical writer of formal orchestration state (§15, §17.5).

Every public method is one ``Store.transaction()`` that applies a checked
proposal, appends the corresponding Events (§16.2) and, where a proposal carries a
base version, records a commit receipt so a replay returns the *same* receipt
instead of applying twice (§17.4).  Agents never call this; only the
Orchestrator, the API and the recovery path do.
"""

from __future__ import annotations

import hashlib
import contextlib

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .taskgraph_dispatch import TaskGraphDispatchBinding

from simple_harness.agents import AgentTurnState

from ..artifacts.store import ArtifactStore
from ..artifacts.versioning import next_versions
from ..contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    TERMINAL_TASK,
    Artifact,
    Attempt,
    AttemptStatus,
    Budget,
    Claim,
    ClaimStatus,
    ContractError,
    Event,
    Mission,
    MissionStatus,
    MissionStopReason,
    ResultEnvelope,
    ResultOutcome,
    Task,
    TaskStatus,
    ids,
)
from ..contracts.htn import TaskForm
from ..contracts.models import (
    STEP2_IMPLEMENTED_LAYERS,
    jsonable,
    sha256_hex,
)
from ..contracts.planning_decisions import PLANNING_DECISION_V1
from ..governance.budgets import AccountSnapshot, BudgetError, BudgetLedger, UsageFact
from ..governance.domains import (
    CODE_DOMAIN,
    DomainProfileV1,
    check_against_domain,
    resolve_domain,
)
from ..governance.policies import DeploymentPolicy
from ..memory.claims import grade_claim
from ..memory.knowledge_standing import CURRENT as KNOWLEDGE_CURRENT
from .review_adjudication import accepted_or_adjudicated, adjudication_of
from ..memory.knowledge_standing import knowledge_standing
from ..memory.verified_knowledge import KnowledgeIndex, KnowledgeRecord, parse_knowledge_ref
from ..observability.lineage import lineage
from ..governance.budget_limits import inherit_limits
from ..graph.terminal import terminal_task
from ..scheduling.allocator import OPEN_ATTEMPT_STATES
from ..scheduling.backpressure import (
    BACKLOG_KEY,
    STATE_KEY,
    BacklogResponse,
    BackpressureLimits,
    BackpressureState,
    Observation,
    Transition,
    evaluate,
)
from ..storage.source_records import replay_scope_digest
from ..storage.store import DispatchIntent, Store, StoredResult, StoreError
from ..verification.conflicts import (
    Contradiction,
    find_contradiction,
    mission_disputes,
)
from .action_commits import ActionCommitsMixin
from .human_commits import HumanCommitsMixin
from .obligation_commits import ObligationCommitsMixin
from .plan_commits import (
    HIERARCHICAL_SEMANTICS,
    SEMANTICS_KEY,
    PlanCommitsMixin,
    normalise_semantics,
)
from .planning_admission_commits import PlanningAdmissionCommitsMixin
from .planning_protocol_binding import (
    bind_planning_protocol,
    checked_planning_protocol,
    planning_protocol_replay_conflict,
)
from .policy_commits import PolicyCommitsMixin
from .protected_tail_commits import ProtectedTailCommitsMixin
from .resolution_commits import ResolutionCommitsMixin
from .operation_completion import OperationCompletionCommitsMixin
from .operation_reconciliation import OperationReconciliationCommitsMixin
from .operation_materialization import OperationMaterializationCommitsMixin
from .source_commits import SourceCommitsMixin
from .state_machine import next_attempt, next_claim, next_mission, next_task

#: P2.3k / defect N3.  Appended once per hierarchical Mission when the Mission Judge
#: builds its integrated tree: the former all-ancestors merge (override legal only along
#: ``Task.dependency_ids``, anything else an ``ArtifactConflict``; removed 2026-10-06) is not applied, because
#: in this mode ``dependency_ids`` is empty by design (§18.5 constraint 4) and the rule
#: read every pair of leaves that wrote the same path as "independent branches".  The
#: Grok C3 episodes lost a root ``GoalResolution`` that already stood to exactly that
#: (``MissionFailed{artifact_conflict}`` one event after ``GoalResolutionCommitted``).
#: The tree is read from the resolution's own contributions instead; the record says so
#: and names every path more than one contribution wrote and which writer was kept.
ARTIFACT_MERGE_NOT_APPLICABLE = "ArtifactMergeNotApplicableUnderHierarchical"

#: P2.3c part 2c (review F6).  Appended when ``judge_mission`` is asked to conclude a
#: hierarchical Mission whose root duty carries no adopted ``GoalResolution``.  A new
#: event type for the same reason as the one above: the judgment was not *wrong*, it
#: was asked at a door that does not decide this — in this mode a Mission is completed
#: out of its root resolution and never out of a sweep of Task statuses (§6.3, §8.1).
HIERARCHICAL_JUDGMENT_REFUSED = "HierarchicalJudgmentRefused"

#: P2.3f.  A hierarchical Mission's service intent (Planner, root
#: reviewer, Critic) whose executor turn was left waiting on a Provider hand-off with
#: an *unknown* outcome was handed off once more — same subject, same reservation, a
#: new executor.  The runtime records the unknown invocation honestly and by design
#: settles nothing for it (the request may have reached the model), and nothing in
#: the orchestration loop resolved that wait: the intent stayed SUBMITTED until the
#: caller's deadline.  Durable so the bound ("once") survives a restart and an
#: operator can see which turn was abandoned and which one answered.
SERVICE_INTENT_REHANDED_OFF = "ServiceIntentRehandedOff"

SUBMITTED_STATES = frozenset({AttemptStatus.SUBMITTED, AttemptStatus.VERIFYING})

ACTOR_SYSTEM = "system"
ORCHESTRATOR_ID = "orchestrator"

class CommitRejected(StoreError):
    """The proposal violates a contract, a budget or the state machine; nothing was written."""


class MissionConflict(CommitRejected):
    """Same (tenant, idempotency_key) with a different specification."""


class NonModelFailuresExhausted(CommitRejected):
    """同一步不扣次数的失败（格式、服务、打断）已到上限；什么都没预留。"""

    def __init__(self, task_id: str, failure_count: int, cap: int) -> None:
        self.task_id = task_id
        self.failure_count = failure_count
        self.cap = cap
        super().__init__(
            f"task {task_id} failed {failure_count} times for reasons that are not the model's"
            f" own mistakes (format, service or interruption); cap {cap}"
        )


@dataclass(frozen=True, slots=True)
class MissionSpec:
    """Input of ``create_mission`` (§5: 任务章程)."""

    goal: str
    success_criteria: tuple[str, ...]
    tenant_id: str
    idempotency_key: str
    # 第 2 批车道 H（H06）：两种计数型停止默认开启，阈值取部署政策（``stop_conditions`` 模块）
    stop_conditions: tuple[str, ...] = (
        "verification_passed", "budget_exhausted", "no_new_knowledge", "result_duplication",
    )
    allowed_tools: tuple[str, ...] = ()
    risk_level: str = "sandbox"
    budget: Budget = field(default_factory=Budget)
    task_kind: str = "code"
    workspace_seed: Mapping[str, str] = field(default_factory=dict)
    untrusted_sources: tuple[str, ...] = ()  # step 4 (D4-12): path prefixes of external content
    domain: str = CODE_DOMAIN  # P3.3 (D1): the domain profile this Mission freezes
    runtime_profile_id: str | None = None
    # 2026-10-01: the default is the hierarchical mode on the one planning protocol.
    # The flat mode was removed on 2026-10-02: naming it is refused here, before any
    # entry (facade, CLI, benchmark runner) can write a row.
    orchestration_semantics_version: str = HIERARCHICAL_SEMANTICS
    planning_protocol_version: str = PLANNING_DECISION_V1

    def __post_init__(self) -> None:
        normalise_semantics(self.orchestration_semantics_version)
        checked_planning_protocol(self.planning_protocol_version)

    @property
    def bound_planning_protocol(self) -> str:
        """The planning protocol this Mission is created under."""

        return self.planning_protocol_version

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "goal": self.goal,
            "success_criteria": list(self.success_criteria),
            "tenant_id": self.tenant_id,
            "idempotency_key": self.idempotency_key,
            "stop_conditions": list(self.stop_conditions),
            "allowed_tools": list(self.allowed_tools),
            "risk_level": self.risk_level,
            "budget": self.budget.to_json(),
            "task_kind": self.task_kind,
            "workspace_seed": dict(self.workspace_seed),
        }
        if self.untrusted_sources:
            data["untrusted_sources"] = list(self.untrusted_sources)
        if self.domain != CODE_DOMAIN:
            # A07: the default must not change ``spec_hash`` — a Host that re-sends the same
            # request after upgrading would otherwise get a MissionConflict
            data["domain"] = self.domain
        if self.runtime_profile_id is not None:
            data["runtime_profile_id"] = self.runtime_profile_id
        data[SEMANTICS_KEY] = self.orchestration_semantics_version
        data["planning_protocol_version"] = self.planning_protocol_version
        return data


@dataclass(frozen=True, slots=True)
class Reservation:
    tokens: int
    tool_calls: int = 0  # step 6 (D6-8): the Attempt's tool-call cap, reserved up front
    search_calls: int = 0  # 推后第 3 批 H08：这次尝试预留的检索次数


def mission_account(mission_id: str) -> str:
    return f"budget:{mission_id}"


#: step 6 (D6-1): §18.2 "Global Budget" above every Mission.  第 2 批车道 P（2026-10-06 用户定）：
#: 全局预算是按月配额——每个自然月一个全局总账 ``budget:global:YYYY-MM``，月初自然是一个新的空账。
GLOBAL_ACCOUNT_PREFIX = "budget:global:"


def global_account_id(at: float) -> str:
    """The Global Budget account of the calendar month ``at`` falls in (this machine's local
    time; ``at`` is a store-clock instant).  The one place that spells the account id."""

    return GLOBAL_ACCOUNT_PREFIX + time.strftime("%Y-%m", time.localtime(at))


def is_global_account(account_id: str) -> bool:
    """Whether ``account_id`` is a (monthly) Global Budget account — the one test for it."""

    return account_id.startswith(GLOBAL_ACCOUNT_PREFIX)


def task_account(task_id: str) -> str:
    return f"budget:{task_id}"


class CommitService(ProtectedTailCommitsMixin,
    ActionCommitsMixin, HumanCommitsMixin, PolicyCommitsMixin, SourceCommitsMixin, ObligationCommitsMixin,
    PlanningAdmissionCommitsMixin, PlanCommitsMixin, ResolutionCommitsMixin, OperationCompletionCommitsMixin,
    OperationMaterializationCommitsMixin, OperationReconciliationCommitsMixin,
):  # step 7: the action ledger + approvals half; step 9: the policy registry half; P2.3a: the plan revision half; P2.3c: the accept half
    def __init__(
        self,
        store: Store,
        *,
        global_budget: Budget | None = None,
        task_max_tokens: int | None = None,
        deployed_layers: frozenset[str] = STEP2_IMPLEMENTED_LAYERS,
        artifact_store: ArtifactStore | None = None,
        mission_profile_validator: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        #: The deployment's completion of a new Mission (its root and its TaskGraph
        #: binding), run inside the creation transaction; installed by
        #: ``deployment.assembly.UserMissionDeployment``.  Every creation door ends here.
        self._mission_completer: Callable[[Mission], None] | None = None
        self._assurance_factory: Any = None
        #: the clock high-water mark this process has seen (see ``assurance_clock``)
        self._assurance_clock_seen: Any = None
        self._assurance_root_gate: Any = None
        self._assurance_check_importer: Any = None
        self._assurance_review_handoff: Any = None
        self._assurance_settlement: Any = None
        self._assurance_validity: Any = None
        self._taskgraph_dispatch: TaskGraphDispatchBinding | None = None
        self._taskgraph_participant_factory: Any = None
        self._mission_profile_validator = mission_profile_validator
        self._source_artifact_store = artifact_store
        if self._source_artifact_store is None and str(store.path) != ":memory:":
            self._source_artifact_store = ArtifactStore(store.path.parent / "artifacts")
        self._ledger = BudgetLedger(store)
        self._global_budget = global_budget  # D6-1: None = no deployment-wide cap
        self._follow_global_quota_on_start()
        if task_max_tokens is not None and int(task_max_tokens) < 1:
            raise ValueError("task_max_tokens must be a positive token count")
        # fixed per-leaf allowance (None = even share of the pool); see plan_commits
        self._task_max_tokens = None if task_max_tokens is None else int(task_max_tokens)
        # host support 0.9.8: the verification layers this deployment runs — without local
        # code execution ``code_test`` is not among them; the Graph Manager checks, the
        # system default policies and the conflict path all follow it
        self._deployed_layers = frozenset(deployed_layers)
        # D6-8: the orchestrator installs the gateway's executed-call counter (subject → count)
        # so every settlement path books the tool-call fact without threading it through
        self.tool_calls_for: Callable[[str], int] | None = None

    # ------------------------------------------------------- global quota
    def _follow_global_quota(self, account_id: str) -> None:
        """The one rule for a Global account's limits (N3-24): they equal the deployment's
        configured monthly quota.  Must run inside a transaction."""

        if self._global_budget is not None and self._ledger.account(account_id).limits != self._global_budget:
            self._ledger.set_limits(account_id, self._global_budget)

    def _follow_global_quota_on_start(self) -> None:
        """夜间 N3-24：服务启动时，账本里已有的每个全局账户（不分月份）上限都跟上当前配置的月配额。

        不只同步当月：按任务建立月份计（车道 P），上月建立、仍在跑的任务还在上月的总账里花；
        "全局账户上限 = 当前配置的月配额"只留这一条规则，不按月份分两种对待。已用、已预留的数字
        照旧留在账上（配额调小时余额可能为负，之后的预留会被拒——多算方向）。"""

        if self._global_budget is None:
            return
        with self._store.transaction():
            rows = self._store.connection.execute(
                "SELECT account_id FROM budget_accounts WHERE account_id LIKE ? ORDER BY account_id",
                (GLOBAL_ACCOUNT_PREFIX + "%",),
            ).fetchall()
            for row in rows:
                if is_global_account(str(row[0])):
                    self._follow_global_quota(str(row[0]))

    # ----------------------------------------------------------- backpressure
    def backpressure_state(self) -> BackpressureState:
        return BackpressureState.from_json(self._store.get_scheduler_state(STATE_KEY))

    def record_backpressure(
        self,
        observation: Observation,
        *,
        limits: BackpressureLimits,
        mission_ids: Sequence[str],
    ) -> tuple[BackpressureState, list[Transition]]:
        """One evaluation step (D6-2'): the new state and, when a dimension crossed a
        watermark, the ``BackpressureRaised`` / ``BackpressureCleared`` events on every
        active Mission's timeline — state and events in one transaction (review P1-4).
        The state document keeps a bounded log of transitions: that log is the single
        truth for ``scheduler.json`` / ``metrics.json`` (review P1-11)."""

        with self._store.transaction():
            previous = BackpressureState.from_json(self._store.get_scheduler_state(STATE_KEY))
            state, transitions = evaluate(previous, observation, limits)
            raw = self._store.get_scheduler_state(STATE_KEY) or {}
            peaks = {str(k): int(v) for k, v in dict(raw.get("peaks") or {}).items()}
            new_peak = False
            for dimension in ("running_attempts", "pending_dispatch", "pending_verifications"):
                observed = observation.value(dimension)
                if observed > peaks.get(dimension, 0):  # review P2-4: the real peak
                    peaks[dimension] = observed
                    new_peak = True
            if not transitions and not new_peak and previous.observation is not None:
                return state, []
            document = state.to_json()
            document["peaks"] = peaks
            log = list(raw.get("log") or [])
            for transition in transitions:
                log.append({**transition.to_json(), "at": observation.observed_at})
            document["log"] = log[-200:]
            document["limits"] = limits.to_json()
            self._store.put_scheduler_state(STATE_KEY, document)
            for transition in transitions:
                for mission_id in mission_ids:
                    self._emit(
                        transition.event_type,
                        mission_id,
                        key=f"{transition.dimension}:{state.changes}:{mission_id}",
                        payload={**transition.to_json(), "since": state.since},
                    )
            return state, transitions

    def record_backlog_response(
        self, response: BacklogResponse, *, mission_ids: Sequence[str]
    ) -> bool:
        """推后第 3 批 H12：积压应对一变（升起、回落、暂停到期），状态与每个在跑任务时间线上的
        ``BacklogResponseChanged`` 同一个事务落库；没变什么也不写。第一次记录且没有积压时只存状态。"""

        document = response.to_json()
        with self._store.transaction():
            previous = self._store.get_scheduler_state(BACKLOG_KEY)
            if previous is not None and {key: previous.get(key) for key in document} == document:
                return False
            changes = 0 if previous is None else int(previous.get("changes", 0))
            if previous is None and response.reason == "normal":
                self._store.put_scheduler_state(BACKLOG_KEY, {**document, "changes": 0})
                return False
            changes += 1
            self._store.put_scheduler_state(BACKLOG_KEY, {**document, "changes": changes})
            before = None if previous is None else {
                key: previous.get(key) for key in ("verifier_workers", "decomposition_paused", "reason")}
            for mission_id in mission_ids:
                self._emit(
                    "BacklogResponseChanged",
                    mission_id,
                    key=f"backlog:{changes}:{mission_id}",
                    payload={**document, "previous": before},
                )
            return True

    # ------------------------------------------------------------ tool audit
    def record_tool_rejected(
        self,
        mission_id: str,
        *,
        task_id: str | None,
        attempt_id: str | None,
        run_id: str,
        call_key: str,
        record: Mapping[str, Any],
    ) -> Event:
        """§21.1 last step / §21.3 "对可疑指令进行隔离和审计": a refused tool call is
        a durable fact on the Mission's timeline (arguments reduced to their keys and
        the path, never file contents)."""

        arguments = dict(record.get("arguments") or {})
        with self._store.transaction():
            return self._emit(
                "ToolCallRejected",
                mission_id,
                key=call_key,  # review P1-3: the SDK call id — stable across a restart
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "tool": record.get("tool"),
                    "reason": record.get("error_code"),
                    "stage": record.get("stage"),
                    "outcome": record.get("outcome"),
                    "argument_keys": sorted(arguments),
                    "path": str(arguments["path"])[:200]  # review P2-5: bounded, never content
                    if isinstance(arguments.get("path"), str)
                    else None,
                    "run_id": run_id,
                },
            )

    def record_tool_call(
        self, *, call_key: str, subject_id: str, mission_id: str, tool: str
    ) -> bool:
        """An executed tool call of an Attempt — the tool-call dimension's fact (review P1-3)."""

        return self._store.record_tool_call(
            call_key=call_key,
            subject_id=subject_id,
            mission_id=mission_id,
            tool=tool,
            outcome="succeeded",
        )

    def record_reservation_held(
        self, subject_id: str, mission_id: str, *, task_id: str | None, reason: str
    ) -> Event:
        """L3-3 (plan §6.2): a reservation an UNKNOWN charge keeps occupied is visible on the
        timeline — once per subject — and listed in ``costs.json``; never auto-released."""

        with self._store.transaction():
            reservation = self._ledger.reservation(subject_id) or {}
            return self._emit(
                "ReservationHeld",
                mission_id,
                key=subject_id,
                task_id=task_id,
                attempt_id=subject_id if task_id else None,
                payload={
                    "subject_id": subject_id,
                    "reason": reason,
                    "reserved_tokens": reservation.get("reserved_tokens"),
                },
            )

    # --------------------------------------------------------- profile health
    PROFILE_HEALTH_KEY = "profile_health"

    def profile_health(self) -> dict[str, dict[str, Any]]:
        raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {}
        return {str(k): dict(v) for k, v in dict(raw.get("profiles") or {}).items()}

    def unavailable_until(self) -> dict[str, float]:
        """profile → cooldown end, for the router (D6-5')."""

        return {
            pid: float(entry["unavailable_until"])
            for pid, entry in self.profile_health().items()
            if entry.get("unavailable_until") is not None
        }

    def record_profile_failure(
        self,
        profile_id: str,
        *,
        error: Mapping[str, Any] | None,
        threshold: int,
        cooldown_seconds: float,
        mission_ids: Sequence[str],
    ) -> float | None:
        """A provider-unavailable turn failure on ``profile_id``; ``threshold`` of them in
        a row put the profile into a cooldown (``RuntimeProfileUnavailable`` on every
        active Mission's timeline).  Returns the cooldown end when tripped."""

        with self._store.transaction():
            raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {"profiles": {}}
            profiles = dict(raw.get("profiles") or {})
            entry = dict(profiles.get(profile_id) or {"failures": 0, "unavailable_until": None})
            entry["failures"] = int(entry.get("failures", 0)) + 1
            entry["last_error"] = jsonable(error or {})
            entry["last_failure_at"] = self._store.now
            until: float | None = None
            if entry["failures"] >= max(1, threshold):
                until = self._store.now + float(cooldown_seconds)
                entry["unavailable_until"] = until
                entry["trips"] = int(entry.get("trips", 0)) + 1
                entry["failures"] = 0
            profiles[profile_id] = entry
            self._store.put_scheduler_state(self.PROFILE_HEALTH_KEY, {"profiles": profiles})
            if until is not None:
                for mission_id in mission_ids:
                    self._emit(
                        "RuntimeProfileUnavailable",
                        mission_id,
                        key=f"{profile_id}:{entry['trips']}:{mission_id}",
                        payload={
                            "profile_id": profile_id,
                            "reason": "provider_unavailable",
                            "until": until,
                            "trips": entry["trips"],
                            "last_error": entry["last_error"],
                        },
                    )
            return until

    def record_profile_success(self, profile_id: str) -> None:
        """A committed turn on ``profile_id`` closes its failure streak and any cooldown."""

        with self._store.transaction():
            raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {"profiles": {}}
            profiles = dict(raw.get("profiles") or {})
            entry = dict(profiles.get(profile_id) or {})
            if not entry or (
                entry.get("failures", 0) == 0 and entry.get("unavailable_until") is None
            ):
                return
            entry["failures"] = 0
            entry["unavailable_until"] = None
            entry["recovered_at"] = self._store.now
            profiles[profile_id] = entry
            self._store.put_scheduler_state(self.PROFILE_HEALTH_KEY, {"profiles": profiles})

    def record_profile_unavailable(
        self,
        profile_id: str,
        *,
        reason: str,
        until: float | None,
        mission_ids: Sequence[str],
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self._store.transaction():
            for mission_id in mission_ids:
                self._emit(
                    "RuntimeProfileUnavailable",
                    mission_id,
                    key=f"{profile_id}:{reason}:{(detail or {}).get('intent_id', '')}:{mission_id}",
                    payload={
                        "profile_id": profile_id,
                        "reason": reason,
                        "until": until,
                        **dict(detail or {}),
                    },
                )

    def global_account(self) -> AccountSnapshot | None:
        """This month's deployment-wide account (§18.2 Global Budget, a monthly quota), if this
        deployment set one and a Mission was created this month (the account opens then)."""

        if self._global_budget is None:
            return None
        with self._store.transaction():
            try:
                return self._ledger.account(global_account_id(self._store.now))
            except BudgetError:
                return None

    @property
    def store(self) -> Store:
        return self._store

    def install_taskgraph_dispatch(self, binding: TaskGraphDispatchBinding) -> None:
        """Called by fixed deployment assembly, never from model or Host JSON."""
        from .taskgraph_dispatch import TaskGraphDispatchBinding
        if not isinstance(binding, TaskGraphDispatchBinding) or binding.store is not self._store:
            raise ValueError("TaskGraph dispatch binding must use this Commit Store")
        if self._taskgraph_dispatch is not None and self._taskgraph_dispatch is not binding:
            raise ValueError("TaskGraph dispatch assembly is already installed")
        self._taskgraph_dispatch = binding

    def taskgraph_attempt_context(self, mission_id: str, attempt_id: str) -> Any:
        """System recovery/review path through the installed, validated history reader."""
        self._require_mission(mission_id)
        if self._taskgraph_dispatch is None:
            raise CommitRejected("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")
        return self._taskgraph_dispatch.read_attempt(mission_id, attempt_id)

    def require_taskgraph_handoff(self, intent: DispatchIntent) -> None:
        from ..storage.taskgraph_store import require_bound
        require_bound(self._store, intent.mission_id)
        if self._taskgraph_dispatch is None:
            raise StoreError("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")
        from ..runtime.planning_operations import SourceUnavailable
        try:
            self._taskgraph_dispatch.require_handoff(intent)
        except (SourceUnavailable, ContractError) as error:
            raise StoreError("TASKGRAPH_CURRENT_EXECUTION_SOURCE_UNAVAILABLE") from error

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    # -------------------------------------------------------------- events
    def _terminal_binding(self, mission_id: str, task_id: str) -> str:
        """``bound`` / ``outside`` (an auxiliary Task outside the semantic graph) /
        ``missing`` (a TaskGraph member whose semantic binding cannot be read).

        The one query behind both the terminal-event gate and the stop cascade: a member
        with a missing binding cannot have a terminal record written for it."""

        from ..storage.htn_store import HtnStore
        from ..storage.taskgraph_store import TaskGraphStore

        if HtnStore(self._store).task_semantics_of(mission_id, task_id) is not None:
            return "bound"
        return "missing" if TaskGraphStore(self._store).ever_member(mission_id, task_id) else "outside"

    def _unbound_open_tasks(self, mission_id: str) -> list[str]:
        """Open TaskGraph members whose binding is damaged: they end with the Mission and
        get no terminal record (2026-10-03 阶段 B 裁决第 7 类); the final report names them."""

        return sorted(task.id for task in self._store.list_tasks(mission_id)
                      if task.status in {TaskStatus.READY, TaskStatus.ACTIVE, TaskStatus.VERIFYING}
                      and self._terminal_binding(mission_id, task.id) == "missing")

    def _emit(
        self,
        event_type: str,
        mission_id: str,
        *,
        key: str,
        task_id: str | None = None,
        attempt_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        actor_type: str = ACTOR_SYSTEM,
        actor_id: str = ORCHESTRATOR_ID,
    ) -> Event:
        idempotency_key = f"{event_type}:{key}"
        from .taskgraph_terminal import TERMINAL_EVENTS, record_terminal_event
        bind_terminal = event_type in TERMINAL_EVENTS and task_id is not None
        if bind_terminal:
            binding = self._terminal_binding(mission_id, str(task_id))
            if binding == "missing":
                raise CommitRejected("TASKGRAPH_TERMINAL_BINDING_MISSING")
            bind_terminal = binding == "bound"  # "outside": an auxiliary Task outside the graph
        if bind_terminal:
            task = self._require_task(str(task_id))
            # A later generation may reach the same terminal status. It must not
            # replay an earlier Task-only event and borrow its conclusion.
            idempotency_key += f":taskgraph:{task.version}"
        # The terminal event and its TaskGraph record are one unit: a caller that
        # emitted outside a transaction (a cancellation path, real run 2026-09-27)
        # failed every loop round with TASKGRAPH_TERMINAL_TRANSACTION_REQUIRED.
        with self._store.transaction() if bind_terminal else contextlib.nullcontext():
            event = self._store.append_event(
                Event(
                    id=ids.event_id(idempotency_key),
                    type=event_type,
                    trace_id=ids.trace_id(mission_id),
                    mission_id=mission_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload=dict(payload or {}),
                    idempotency_key=idempotency_key,
                    created_at=self._store.now,
                )
            )
            if bind_terminal:
                record_terminal_event(self._store, event)
        return event

    # ------------------------------------------------------------- missions
    def approve_assurance_check_policy(self, **command: Any):
        from .assurance_check_policy import approve_check_policy

        return approve_check_policy(self, **command)

    def ensure_assurance_review_invocation(self, **command: Any):
        from .assurance_review_transport import ensure_review_invocation

        return ensure_review_invocation(self, **command)

    def ensure_assurance_format_repair(self, *, tenant_id: str, prior_failure: Any):
        from .assurance_review_transport import ensure_format_repair_invocation

        return ensure_format_repair_invocation(self, tenant_id=tenant_id, prior_failure=prior_failure)

    def import_assurance_check_locked(self, *, mission_id: str, execution_ref: Any,
                                      completion_scope: Any):
        from ..assurance.codec import AssuranceError

        if self._assurance_check_importer is None:
            raise AssuranceError("CHECK_IMPORTER_UNAVAILABLE")
        return self._assurance_check_importer.import_check_locked(
            mission_id=mission_id, execution_ref=execution_ref, completion_scope=completion_scope)

    def install_assurance_root(self, *, principal: Any, tenant_id: str, command_id: str):
        from .assurance_root_commits import install_native_root

        if self._assurance_root_gate is None:
            from ..assurance.codec import AssuranceError

            raise AssuranceError("ASSURANCE_ROOT_GATE_UNBOUND")
        return install_native_root(self, self._assurance_root_gate, principal=principal,
                                   tenant_id=tenant_id, command_id=command_id)


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
        from ..storage.assurance_work import atomic
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
                from .assurance_factory import validate_creation_replay
                validate_creation_replay(self, mission)
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
                    **({} if spec.runtime_profile_id is None else {
                        "runtime_profile_id": spec.runtime_profile_id,
                    }),
                    SEMANTICS_KEY: semantics_version,
                },
            )
            self._store.insert_mission(mission, spec_hash=spec_hash)
            parent: str | None = None
            limits = spec.budget
            if self._global_budget is not None:  # D6-1: Global → Mission (§18.2), never the reverse
                # 按任务建立月份计（第 2 批车道 P）：任务挂在建立当月的全局总账下，它的全部用量
                # 都记在这个月，哪怕它跨月才跑完。当月第一个任务把这个月的账户打开；上限始终是
                # 部署配置的月配额（配置改了，服务启动时与新任务建立时随之改，夜间 N3-24）。
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
                    raise CommitRejected("runtime profile selection requires an Orchestrator binding")
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
            from .assurance_factory import record_mission_creation
            record_mission_creation(self, mission, spec, creation_event)
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
                if kind == "critic" and attempt_id is not None and task_id is not None else None
            )
            first_reservation = (
                self._ledger.reservation(first_hold["subject_id"]) if first_hold else None
            )
            if first_reservation and first_reservation["state"] != "SETTLED" and (
                first_reservation["reserved_tokens"] > 0
            ):
                assert isinstance(attempt_id, str) and isinstance(task_id, str)
                self.consume_critic_tail(
                    attempt_id=attempt_id, task_id=task_id, subject_id=subject_id,
                    account_id=account_id, reservation=reservation,
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

    def _close_attempt(self, attempt: Attempt, target: AttemptStatus, *, reason: str) -> Attempt:
        """Attempt → SUPERSEDED / CANCELLED with its result (if any) rejected as history,
        its intent closed and its reservation settled unless an UNKNOWN charge holds it."""

        updated = next_attempt(attempt, target, failure={"reason": reason})
        self._store.update_attempt(updated, expected_version=attempt.version)
        stored = self._store.find_result_for_attempt(attempt.id)
        if stored is not None and stored.verification_state in {"PENDING", "RUNNING", "SUSPENDED"}:
            self._store.set_result_verification(
                stored.envelope.id, state="REJECTED", verdict="superseded"
            )
            if stored.verification_state == "SUSPENDED":  # D7-8': the review goes with it
                self._cancel_review_request(stored.envelope.id, reason=reason)
            self._reject_open_claims(stored.envelope.id)
        intent = self._store.get_intent_for_subject(attempt.id)
        if intent is not None and intent.state in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
            self._settle_intent(intent, "FAILED")
        # A SUBMITTED intent stays open: its SDK turn is still running and the loop
        # collects it later (usage imported, late result kept as history, D3-6'); the
        # late-accounting scanner settles the reservation once the turn's cost is known.
        self._emit(
            "AttemptSuperseded" if target is AttemptStatus.SUPERSEDED else "AttemptCancelled",
            attempt.mission_id,
            key=attempt.id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            payload={"reason": reason, "from": str(attempt.status)},
        )
        return updated

    def _reject_open_claims(self, result_id: str) -> None:
        for claim in self._store.list_claims(result_id):
            if claim.status in {ClaimStatus.PROPOSED, ClaimStatus.UNDER_REVIEW}:
                target_claim = (
                    ClaimStatus.UNDER_REVIEW
                    if claim.status is ClaimStatus.PROPOSED
                    else claim.status
                )
                moved = claim if target_claim is claim.status else next_claim(claim, target_claim)
                self._store.upsert_claim(next_claim(moved, ClaimStatus.REJECTED))

    def set_aside_result(self, result_id: str, *, detail: Mapping[str, Any]) -> Attempt | None:
        """A stored result whose completion scope went stale before it was verified (the user
        amended the requirements, or a new plan revision was adopted): archived as superseded —
        not the worker's fault, no repair request, no review package — and its Attempt goes to
        RETRY_WAIT, exactly as a result collected while the requirements are amended is (阶段 E).
        Whether the step is done again is the planner's call.  ``None`` when the result or
        Attempt has already moved on."""

        detail = {**jsonable(detail), "result_id": result_id}
        with self._store.transaction():
            stored = self._store.get_result(result_id)
            if stored is None or stored.verification_state not in {"PENDING", "RUNNING"}:
                return None
            attempt = self._require_attempt(stored.envelope.attempt_id)
            if attempt.status not in {AttemptStatus.RUNNING, AttemptStatus.SUBMITTED, AttemptStatus.VERIFYING}:
                return None
            task = self._require_task(stored.envelope.task_id)
            self._store.set_result_verification(result_id, state="REJECTED", verdict="superseded")
            self._reject_open_claims(result_id)
            self._store.update_attempt(
                next_attempt(attempt, AttemptStatus.RETRY_WAIT, failure={"reason": "superseded", **detail}),
                expected_version=attempt.version,
            )
            others_submitted = any(other.id != attempt.id and other.status in SUBMITTED_STATES
                                   for other in self._store.list_attempts(task.id))
            if task.status is TaskStatus.VERIFYING and not others_submitted:
                self._store.update_task(next_task(task, TaskStatus.ACTIVE), expected_version=task.version)
            self._release_attempt_charge(self._require_attempt(attempt.id))
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt.id}:{stored.turn_id}",
                task_id=task.id,
                attempt_id=attempt.id,
                payload={"reason": "superseded", "detail": detail, "turn_id": stored.turn_id},
            )
            return self._require_attempt(attempt.id)

    # ---------------------------------------------------------- knowledge (step 4)
    def _grade_and_project(
        self,
        mission: Mission,
        task: Task,
        attempt: Attempt,
        stored: StoredResult,
        review: Any = None,
        certificate: Any = None,
    ) -> list[dict[str, Any]]:
        """D4-2/D4-3/D4-4/D4-5 inside the accept transaction: grade every claim of the
        accepted result from the verification that ran, project the VERIFIED ones into
        the Verified Knowledge store with their provenance, record the reuse chain of
        ``used_knowledge`` and apply an explicit, legal supersession.

        阶段 C：一条结论成为"已验证"有两个来源——系统自己跑过的测试观察，或独立审阅员在
        正式审阅记录（``review``）里逐条确认。每条入库的知识自带它的依据（``support``：
        来源验收、确认时引用的证据产物、本步用过的知识），之后是否仍然当前只读它。

        第 2 批 K02：审阅确认入库的知识另带三个登记项（有效期、允许用途、保障等级），从这次验收
        用的使用证书（``certificate``）与任务的保证通道取；取不到就是 None。"""

        envelope = stored.envelope
        confirmed = self._review_confirmations(mission, envelope.id, review)
        registration = self._knowledge_registration(mission, certificate) if confirmed else {}
        used = {knowledge_id: version
                for knowledge_id, version in map(parse_knowledge_ref, envelope.used_knowledge)}
        domain = self.domain_for(mission.id)
        untrusted = [str(p) for p in (mission.final_report or {}).get("untrusted_sources", [])]
        artifact_paths = [
            artifact.path
            for artifact_id in stored.artifacts
            for artifact in [self._store.get_artifact(artifact_id)]
            if artifact is not None
        ]
        # Only runtime-recorded verification is evidence; caller-supplied acceptance
        # summaries are not execution receipts.
        from .completion_inputs import frozen_requirements_revision

        layers = [dict(item) for item in self._store.list_verifications(
            envelope.id, requirements_revision=frozen_requirements_revision(self._store, stored))]
        from ..memory.code_observations import scoped_test_observations

        artifacts = [self._store.get_artifact(item) for item in stored.artifacts]
        observations = scoped_test_observations(
            envelope, [a for a in artifacts if a is not None], layers, self._store.now, task
        )
        grading_layers = [{"layer": "code_test", "detail": {"runs": [
            {"target": record.verifier["target"], "passed": True}
            for _, record in observations
        ]}}]
        resolved_refs: set[str] = set()
        for proposal in envelope.claims:
            for ref in proposal.evidence or envelope.evidence:
                if ref.startswith("knowledge:"):
                    record = self._store.get_knowledge(ref.removeprefix("knowledge:"))
                    if (record is not None and used.get(record.id) == record.version
                            and record.mission_id == mission.id
                            and knowledge_standing(self._store, record) == KNOWLEDGE_CURRENT
                            and self._accepted_result(record.source_result)):
                        resolved_refs.add(ref)
                elif ref.startswith("tool-run:"):
                    run = self._store.get_tool_call(ref.removeprefix("tool-run:"))
                    if (run is not None and run["mission_id"] == mission.id
                            and run["subject_id"] == attempt.id and run["outcome"] == "succeeded"):
                        resolved_refs.add(ref)
        report: list[dict[str, Any]] = []
        existing_claims = [
            other
            for other in self._store.list_mission_claims(mission.id)
            if other.result_id != envelope.id and self._accepted_result(other.result_id)
        ]
        for claim in self._store.list_claims(envelope.id):
            grade = grade_claim(
                claim.id,
                claim.evidence,
                verifier_results=grading_layers,
                artifact_paths=artifact_paths,
                untrusted_prefixes=untrusted,
                domain=domain,
                resolved_refs=frozenset(resolved_refs),
            )
            metadata = {
                **dict(claim.confidence_metadata),
                "grade": str(grade.status).lower()
                if grade.status is not ClaimStatus.UNDER_REVIEW
                else "unsupported",
                "basis": dict(grade.basis),
                "evidence_trust": list(grade.evidence_trust),
            }
            supersedes: str | None = None
            proposed = claim.confidence_metadata.get("proposed_supersedes")
            if proposed is not None:
                target = self._store.get_knowledge(str(proposed))
                if grade.status is not ClaimStatus.VERIFIED:
                    metadata["supersedes_rejected"] = (
                        f"only a VERIFIED claim may supersede knowledge (graded {grade.status})"
                    )
                elif (
                    target is None or target.mission_id != mission.id or target.status != "VERIFIED"
                ):
                    metadata["supersedes_rejected"] = (
                        f"{proposed!r} is not VERIFIED knowledge of this Mission"
                    )
                elif claim.key is None or claim.key != target.key:
                    metadata["supersedes_rejected"] = "supersession requires the same key"
                else:
                    supersedes = target.id
            results = (
                *claim.verifier_results,
                *layers,
                dict(grade.basis, layer=grade.basis.get("layer", "grading")),
            )
            target_status = grade.status
            verifier = dict(grade.basis)
            confirmation = confirmed.get(claim.id)
            if (confirmation is not None and target_status is not ClaimStatus.VERIFIED
                    and confirmation["content_sha256"]
                    == hashlib.sha256(claim.content.encode("utf-8")).hexdigest()):
                # The independent reviewer confirmed this claim against evidence other
                # than the reviewed result itself: that is what makes it knowledge.
                target_status = ClaimStatus.VERIFIED
                verifier = {key: value for key, value in confirmation.items() if key != "content_sha256"}
                metadata["grade"] = "verified"
                metadata["basis"] = {"layer": "review", "reason": "confirmed by the independent review"}
            # D4-6': conflict precedes grading — a contested claim is capped at DISPUTED
            # and never projected, whatever its own evidence says
            contradiction = find_contradiction(claim, existing_claims)
            if contradiction is not None:
                target_status = ClaimStatus.DISPUTED
                metadata["grade_before_dispute"] = metadata["grade"]
                metadata["grade"] = "disputed"
                supersedes = None
            updated = next_claim(
                claim,
                target_status if target_status is not claim.status else None,
                verifier_results=tuple(results),
                confidence_metadata=metadata,
                supersedes=supersedes,
            )
            self._store.upsert_claim(updated)
            report.append({"claim_id": claim.id, "status": str(updated.status), "key": claim.key})
            if contradiction is not None:
                self._dispute(mission, updated, contradiction)
                continue
            if updated.status is ClaimStatus.VERIFIED:
                record = KnowledgeRecord(
                    id=updated.id,
                    mission_id=mission.id,
                    claim_id=updated.id,
                    content=updated.content,
                    type=updated.type,
                    status="VERIFIED",
                    version=1,
                    key=updated.key,
                    stance=updated.stance,
                    proposed_by=updated.proposed_by,
                    source_task=task.id,
                    source_attempt=attempt.id,
                    source_result=envelope.id,
                    evidence=updated.evidence,
                    verifier=verifier,
                    created_at=self._store.now,
                    supersedes=supersedes,
                    evidence_trust=grade.evidence_trust,
                    support=self._knowledge_support(
                        mission, task, stored, updated.id, updated.evidence, confirmation),
                    **(registration if confirmation is not None else {}),
                )
                self._store.upsert_knowledge(record)
                self._emit(
                    "KnowledgeCommitted",
                    mission.id,
                    key=record.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    payload={
                        "knowledge_id": record.id,
                        "key": record.key,
                        "stance": record.stance,
                        "verifier": dict(record.verifier),
                        "supersedes": supersedes,
                        "basis": record.verifier.get("basis", "test_observation"),
                        "support": dict(record.support),
                    },
                )
                if supersedes is not None:
                    self._supersede_knowledge(supersedes, by=record.id)
        for observation, record in observations:
            record = replace(
                record, verifier={**dict(record.verifier), "basis": "test_observation"},
                support=self._knowledge_support(mission, task, stored, record.id, record.evidence, None))
            self._store.upsert_claim(observation)
            self._store.upsert_knowledge(record)
            report.append({"claim_id": observation.id, "status": "VERIFIED", "key": None})
            self._emit("KnowledgeCommitted", mission.id, key=record.id,
                       task_id=task.id, attempt_id=attempt.id,
                       payload={"knowledge_id": record.id, "verifier": dict(record.verifier),
                                "system_observation": True, "basis": "test_observation",
                                "support": dict(record.support)})
        for knowledge_id in used:
            source = self._store.get_knowledge(knowledge_id)
            if source is None or source.mission_id != mission.id or source.status != "VERIFIED":
                continue
            if task.id not in source.used_by:
                self._store.upsert_knowledge(replace(source, used_by=(*source.used_by, task.id)))
            self._emit(
                "KnowledgeUsed",
                mission.id,
                key=f"{envelope.id}:{source.id}",
                task_id=task.id,
                attempt_id=attempt.id,
                payload={
                    "knowledge_id": source.id,
                    "version": source.version,
                    "result_id": envelope.id,
                    "source_task": source.source_task,
                    "source_attempt": source.source_attempt,
                },
            )
        return report

    def _review_confirmations(self, mission: Mission, result_id: str, review: Any) -> dict[str, dict[str, Any]]:
        """The claims the official content review confirmed, by claim id (阶段 C).

        Read from the record's authenticated manifest.  A confirmation counts only when
        the review as a whole stands (ACCEPT, or INCONCLUSIVE and the person passed it)
        and it cites at least one piece of evidence that is not the reviewed result
        itself — a result cannot be the proof of its own claims."""
        validity = getattr(self, "_assurance_validity", None)
        if review is None or validity is None or not accepted_or_adjudicated(self._store, review):
            return {}
        from ..assurance.codec import decode
        from ..assurance.refs import AssuranceRef, Pin
        from ..storage.assurance_reads import AssuranceReader

        reader = AssuranceReader(self._store, tenant_id=validity.tenant_id, mission_id=mission.id)
        manifest = decode(reader.read_exact_metadata(AssuranceRef(
            "input_manifest", Pin(review.evidence_manifest_hash, 0, review.evidence_manifest_hash))).body_json)
        ruling = adjudication_of(self._store, str(review.record_id))
        found: dict[str, dict[str, Any]] = {}
        for row in manifest.get("claims", ()):
            independent = [ref for ref in row["evidence_refs"]
                           if not (ref["kind"] == "result" and ref["pin"]["id"] == result_id)]
            if not row["confirmed"] or not independent:
                continue
            found[row["claim_id"]] = {
                "basis": "review_confirmed", "record_id": str(review.record_id),
                "review_key": manifest["review_key"], "reviewer_agent_id": str(review.reviewer_agent_id),
                "manifest_hash": review.evidence_manifest_hash, "reason": row["reason"],
                "evidence_refs": row["evidence_refs"], "adjudication": ruling,
                "content_sha256": row["content_sha256"],
            }
        return found

    def _knowledge_registration(self, mission: Mission, certificate: Any) -> dict[str, Any]:
        """第 2 批 K02：审阅确认入库的知识的三个登记项，只从已有记录取——有效期取这次验收用的
        使用证书的签发/失效时刻与任务纪元；保障等级取任务的保证通道；允许用途没有任何记录写它
        （审阅回复与证书都没有这一项），一律 None。取不到的就是 None，不猜。"""
        from ..assurance.codec import AssuranceError
        from ..storage.assurance_store import AssuranceStore

        out: dict[str, Any] = {"validity_interval": None, "permitted_uses": None, "assurance_level": None}
        if certificate is not None:
            out["validity_interval"] = {
                "valid_from_ms": int(certificate.issued_at_ms),
                "valid_until_ms": None if certificate.not_after_ms is None else int(certificate.not_after_ms),
                "mission_epoch": int(certificate.mission_epoch),
            }
        try:
            out["assurance_level"] = AssuranceStore(self._store).lane(mission.id)
        except AssuranceError:
            out["assurance_level"] = None
        return out

    def _knowledge_support(
        self, mission: Mission, task: Task, stored: StoredResult, knowledge_id: str,
        evidence: Sequence[str], confirmation: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """What a piece of knowledge rests on, computed here and stored on its record: the
        acceptance of its source step, the evidence artifacts (the ones the reviewer cited
        or the claim names; all of the result's when neither says), and the knowledge the
        step used.  Nothing is written by this — the record carries it."""
        from ..contracts.semantic_base import content_hash_of
        from .assurance_validity import acceptance_id_for
        from .completion_inputs import frozen_requirements_revision

        envelope = stored.envelope
        cited = {ref["pin"]["id"] for ref in (confirmation or {}).get("evidence_refs", ())
                 if ref["kind"] == "artifact"}
        artifacts = [a for a in map(self._store.get_artifact, stored.artifacts) if a is not None]
        named = [a for a in artifacts if a.id in cited or a.path in evidence]
        knowledge = []
        for source_id, _ in map(parse_knowledge_ref, envelope.used_knowledge):
            source = self._store.get_knowledge(source_id)
            if source is None or source.mission_id != mission.id or source.id == knowledge_id:
                continue
            knowledge.append({"id": source.id, "version": int(source.version),
                              "content_hash": content_hash_of(source.content)})
        return {
            "acceptance_id": acceptance_id_for(
                task.id, envelope.id, frozen_requirements_revision(self._store, stored)),
            "artifacts": [{"id": a.id, "version": int(a.version), "content_hash": a.content_hash}
                          for a in (named or artifacts)],
            "knowledge": knowledge,
        }

    def _accepted_result(self, result_id: str) -> bool:
        stored = self._store.get_result(result_id)
        return (
            stored is not None and stored.verification_state == "DONE" and stored.verdict == "PASS"
        )

    def _dispute(self, mission: Mission, claim: Claim, contradiction: Contradiction) -> None:
        """§14.4 保留双方、都标"有争议"、互记对方；后来的这条不进知识库 (D4-6')."""

        # re-read: an earlier claim of the same result may already have marked it
        other = self._store.get_claim(contradiction.other.id) or contradiction.other
        if other.status is ClaimStatus.VERIFIED:
            # VERIFIED has no edge to DISPUTED (§25.3): the knowledge stays formal and is
            # marked as contested on both the claim and its projection
            if claim.id not in other.disputed_by:
                self._store.upsert_claim(
                    next_claim(other, disputed_by=(*other.disputed_by, claim.id))
                )
            record = self._store.get_knowledge(other.id)
            if record is not None and claim.id not in record.disputed_by:
                self._store.upsert_knowledge(
                    replace(record, disputed_by=(*record.disputed_by, claim.id))
                )
        elif other.status is not ClaimStatus.DISPUTED:
            self._store.upsert_claim(
                next_claim(other, ClaimStatus.DISPUTED, disputed_by=(*other.disputed_by, claim.id))
            )
        elif claim.id not in other.disputed_by:
            self._store.upsert_claim(next_claim(other, disputed_by=(*other.disputed_by, claim.id)))
        if other.id not in claim.disputed_by:
            # both sides name each other: "who disagrees" is read from the claim itself
            self._store.upsert_claim(next_claim(claim, disputed_by=(*claim.disputed_by, other.id)))
        self._emit(
            "ClaimDisputed",
            mission.id,
            key=f"{claim.id}:{other.id}",
            task_id=claim.source_task,
            attempt_id=claim.source_attempt,
            payload={
                "claim_id": claim.id,
                "contradicts": other.id,
                "key": contradiction.key,
                "reason": contradiction.reason,
                "other_status": str(other.status),
            },
        )

    def _require_claim(self, claim_id: str) -> Claim:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            raise CommitRejected(f"unknown claim {claim_id}")
        return claim

    def record_retrieval_unavailable(self, task_id: str, *, reason: str, policy: str) -> int:
        """S4-07 / D4-11': one durable event per failed retrieval; returns the count so
        far for this Task (it survives restarts — it is derived from the events)."""

        with self._store.transaction():
            task = self._require_task(task_id)
            previous = sum(
                1
                for event in self._store.list_events(task.mission_id)
                if event.type == "RetrievalUnavailable" and event.task_id == task_id
            )
            count = previous + 1
            self._emit(
                "RetrievalUnavailable",
                task.mission_id,
                key=f"{task_id}:retrieval:{count}",
                task_id=task_id,
                payload={"reason": reason, "policy": policy, "count": count},
            )
            return count

    def _supersede_knowledge(self, knowledge_id: str, *, by: str) -> None:
        """VERIFIED → SUPERSEDED (§25.3, the one legal edge out of VERIFIED) on both the
        claim and its knowledge projection, pointing at the newer version."""

        record = self._store.get_knowledge(knowledge_id)
        if record is None or record.status != "VERIFIED":
            return
        self._store.upsert_knowledge(
            replace(record, status="SUPERSEDED", superseded_by=by)  # P2-13: version = identity
        )
        claim = self._store.get_claim(record.claim_id)
        if claim is not None and claim.status is ClaimStatus.VERIFIED:
            self._store.upsert_claim(next_claim(claim, ClaimStatus.SUPERSEDED, superseded_by=by))
        self._emit(
            "KnowledgeSuperseded",
            record.mission_id,
            key=f"{knowledge_id}:{by}",
            task_id=record.source_task,
            payload={"knowledge_id": knowledge_id, "superseded_by": by, "key": record.key},
        )

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
        from ..runtime.operation_reconciliation import action_outcome_unresolved

        rows = [{"action_key": str(action["action_key"]), "action_id": str(action.get("action_id") or ""),
                 "operation": str(action.get("operation") or ""), "target": str(action.get("target") or ""),
                 "state": str(action.get("state") or "")}
                for action in self._store.list_actions(mission_id)
                if action_outcome_unresolved(self._store, action)]
        if rows:
            report["unresolved_actions"] = rows
            report["unresolved_actions_note"] = "这些对外操作已经交出、结果还不明；系统会继续核对，有结果再通知"

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
                        self._close_attempt(attempt, AttemptStatus.CANCELLED, reason="mission_stopped")
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

    def settle_intent(self, intent_id: str, state: str) -> DispatchIntent:
        """Close a dispatch intent (SETTLED / FAILED) through the single writer (D2)."""

        with self._store.transaction():
            intent = self._require_intent(intent_id)
            return self._settle_intent(intent, state)

    def _settle_intent(self, intent: DispatchIntent, state: str) -> DispatchIntent:
        if intent.state == state:
            return intent
        updated = DispatchIntent(
            **{**intent.to_json(), "state": state, "version": intent.version + 1}
        )
        self._store.update_intent(updated, expected_version=intent.version)
        self._emit(
            "IntentSettled",
            intent.mission_id,
            key=f"{intent.subject_id}:{state}",
            attempt_id=intent.subject_id if intent.kind == "attempt" else None,
            payload={"intent_id": intent.intent_id, "kind": intent.kind, "state": state},
        )
        return updated

    # ------------------------------------------------------------- attempts
    def authorize_planning_retry(self, *, mission_id: str, task_id: str,
                                 payload: Any, expected_plan_revision: int,
                                 decision_id: str, canonical_hash: str, request_id: str) -> dict[str, Any]:
        """Commit one retry permit; caller atomically records the admitted decision."""
        from .planning_retry import RETRY_AUTHORIZED, retry_binding
        with self._store.transaction():
            binding = retry_binding(self._store, mission_id, task_id, payload,
                                    expected_plan_revision=expected_plan_revision)
            data = {**binding, "decision_id": decision_id, "canonical_hash": canonical_hash,
                    "request_id": request_id}
            self._emit(RETRY_AUTHORIZED, mission_id, key=decision_id, task_id=task_id, payload=data)
            return data

    def create_attempt(
        self,
        task_id: str,
        *,
        role: str,
        model: str,
        prompt_version: str,
        context_version: str,
        reservation: Reservation,
        intent_config: Mapping[str, Any],
        input_hash: str,
        retry_of: str | None = None,
        feedback: Sequence[str] = (),
        inputs: Sequence[Mapping[str, Any]] = (),
        max_open_attempts: int | None = None,
        max_running_attempts: int | None = None,
        runtime_profile_id: str = "default",
        routing: Mapping[str, Any] | None = None,
        critic_tail: Reservation | None = None,
        context_evidence: Sequence[Any] = (),
    ) -> tuple[Attempt, DispatchIntent]:
        """Atomic Reserve + Attempt(PENDING) + dispatch intent (ORCH-BUILD §4.3 step 1).

        Refuses (nothing written) when the Task is not READY/ACTIVE/VERIFYING, when it
        already has an open Attempt (one at a time per Task) or when the budget does not
        fit; the caller turns ``BudgetExhausted`` into a stop.  Every Attempt counts
        against ``max_attempts``.

        ``context_evidence``: the knowledge and checked summaries frozen into this
        Attempt's context.  A CONTEXT use certificate over them is issued in this same
        transaction (推后第 1 批 A26); evidence that is no longer current refuses the
        Attempt with the named reason, and the next round assembles a current context.
        """

        with self._store.transaction():
            task = self._require_task(task_id)
            if task.accepted_result_id:
                raise CommitRejected("accepted preparation waits for completion, not another Worker")
            if task.status not in {TaskStatus.READY, TaskStatus.ACTIVE, TaskStatus.VERIFYING}:
                raise CommitRejected(f"task {task_id} is {task.status}; no new Attempt")
            from .failure_classes import NON_MODEL_FAILURE_CAP, non_model_failures

            spent = non_model_failures(self._store.list_attempts(task_id))
            if spent >= NON_MODEL_FAILURE_CAP:
                raise NonModelFailuresExhausted(task_id, spent, NON_MODEL_FAILURE_CAP)
            if task.paused and task.pause_reason == "provider_admission:usage_unresolved":
                raise CommitRejected("provider admission is waiting for unresolved usage")
            from .planning_runtime_block import pending_block
            if pending_block(self._store, task.mission_id) is not None:
                raise CommitRejected("H4 runtime block prevents new Attempts")
            from .planning_retry import pending_retry_permit, retry_decision_required
            if retry_decision_required(self._store, task.mission_id, task.id):
                permit = pending_retry_permit(self._store, task.mission_id, task.id)
                if permit is None or permit["failed_attempt_id"] != retry_of:
                    raise CommitRejected("H4 retry needs a current committed RETRY_SAME_METHOD decision")
                intent_config = {**dict(intent_config), "planning_retry_decision_id": permit["decision_id"]}
            from ..storage.taskgraph_store import require_bound
            try:
                require_bound(self._store, task.mission_id)
            except StoreError as error:
                raise CommitRejected(str(error)) from error
            if self._taskgraph_dispatch is None:
                raise CommitRejected("TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED")
            from ..artifacts.store import ArtifactStoreError
            from ..artifacts.versioning import ArtifactConflict
            from ..runtime.planning_operations import SourceUnavailable
            try:
                graph_prepared = self._taskgraph_dispatch.prepare(
                    task_id, intent_config=intent_config, inputs=inputs, input_hash=input_hash,
                )
            except (StoreError, ContractError, ArtifactStoreError, ArtifactConflict, SourceUnavailable) as error:
                # A named per-Mission refusal stays in the original admission
                # path; it must not abort scheduling for unrelated Missions.
                raise CommitRejected(str(error)) from error
            intent_config = {**dict(intent_config), "taskgraph_inputs": graph_prepared.intent_binding()}
            existing = self._store.list_attempts(task_id)
            open_attempts = [a for a in existing if a.status in OPEN_ATTEMPT_STATES]
            if open_attempts:
                raise CommitRejected(
                    f"task {task_id} already has an open Attempt: {open_attempts[0].id}"
                )
            if max_open_attempts is not None:  # D3-4: the Mission-wide bound, checked here
                open_in_mission = sum(
                    1
                    for other in self._store.list_tasks(task.mission_id)
                    for a in self._store.list_attempts(other.id)
                    if a.status in OPEN_ATTEMPT_STATES
                )
                if open_in_mission >= max_open_attempts:
                    raise CommitRejected(
                        f"mission {task.mission_id} already has {open_in_mission} open Attempts "
                        f"(max_concurrency={max_open_attempts})"
                    )
            if max_running_attempts is not None:  # D6-1' / review P1-12: the deployment-wide cap
                open_everywhere = self._store.count_attempts_by_status(
                    *(str(s) for s in OPEN_ATTEMPT_STATES)
                )
                if open_everywhere >= max_running_attempts:
                    raise CommitRejected(
                        f"{open_everywhere} Attempts are open across all Missions "
                        f"(max_running_attempts={max_running_attempts})"
                    )
            ordinal = len(existing) + 1
            attempt_id = ids.attempt_id(task_id, ordinal)
            from .assurance_point_use import ATTEMPT_CONSUMER, PointUseRefused, require_point_use_locked
            try:
                require_point_use_locked(
                    self, mission_id=task.mission_id, purpose="CONTEXT", consumer_kind=ATTEMPT_CONSUMER,
                    consumer_id=attempt_id, claims=tuple(context_evidence), subject=("task", task_id))
            except PointUseRefused as refused:
                raise CommitRejected(f"CONTEXT_EVIDENCE_NOT_CURRENT: {'; '.join(refused.use.refusals)}") from refused
            from .completion_inputs import freeze_attempt_completion_inputs
            frozen_completion = freeze_attempt_completion_inputs(
                self._store, self, task,
                attempt_id=attempt_id, request_id=ids.intent_id("attempt", attempt_id),
            )
            if self._source_artifact_store is not None:
                from .attempt_execution import freeze_attempt_execution

                execution = freeze_attempt_execution(
                    self._store, self._source_artifact_store, task=task,
                    intent_config=intent_config, inputs=inputs, retry_of=retry_of,
                    validated_input_identities=frozenset(
                        (str(item["artifact_id"]), str(item["path"]), str(item["content_hash"])) for item in inputs),
                )
                if ("attempt_execution" in intent_config
                        and sha256_hex(intent_config["attempt_execution"]) != sha256_hex(execution)):
                    raise CommitRejected("attempt execution conflicts with actual frozen inputs")
                intent_config = {**dict(intent_config), "attempt_execution": execution}
            elif "attempt_execution" in intent_config:
                raise CommitRejected("attempt execution requires an explicit artifact store")
            if critic_tail is not None:
                from ..governance.tail_budget import TailReserve

                self.reserve_critic_tail(
                    attempt_id=attempt_id, task_id=task.id,
                    reserve=TailReserve(tokens=critic_tail.tokens),
                    semantic_revision=self.protected_tail_revision(task.id),
                )
            self._ledger.reserve(  # BudgetExhausted propagates; nothing was written
                account_id=task_account(task_id),
                subject_id=attempt_id,
                mission_id=task.mission_id,
                tokens=reservation.tokens,
                counts_attempt=True,
                tool_calls=reservation.tool_calls,
                search_calls=reservation.search_calls,
            )
            attempt = Attempt(
                id=attempt_id,
                task_id=task_id,
                mission_id=task.mission_id,
                role=role,
                model=model,
                prompt_version=prompt_version,
                context_version=context_version,
                budget_reserved=Budget(max_tokens=reservation.tokens),
                lease_owner=None,
                lease_expires_at=None,
                status=AttemptStatus.PENDING,
                retry_of=retry_of,
                created_at=self._store.now,
                version=1,
                ordinal=ordinal,
                creation_key=attempt_id,
                input_id="attempt-input",
                runtime_profile_id=runtime_profile_id,
                task_version=task.version,
                input_hash=input_hash,
                feedback=tuple(feedback),
            )
            self._store.insert_attempt(attempt)
            intent = DispatchIntent(
                intent_id=ids.intent_id("attempt", attempt_id),
                kind="attempt",
                subject_id=attempt_id,
                mission_id=task.mission_id,
                state="PENDING",
                version=1,
                creation_key=attempt.creation_key,
                input_id=attempt.input_id,
                input_hash=input_hash,
                config={
                    **dict(intent_config),
                    "attempt_id": attempt_id,  # authoritative (P1-7): never the caller's guess
                    "inputs": [dict(item) for item in inputs],
                    "completion_inputs": frozen_completion.to_json(),
                },
                expected_turn_id=None,
                agent_id=None,
                receipt=None,
                lease_owner=None,
                lease_expires_at=None,
                replays=0,
                created_at=self._store.now,
            )
            self._store.insert_intent(intent)
            self._taskgraph_dispatch.record(graph_prepared, attempt, intent)
            if task.status is TaskStatus.READY:
                self._store.update_task(
                    next_task(task, TaskStatus.ACTIVE, attempt_count=task.attempt_count + 1),
                    expected_version=task.version,
                )
            else:
                self._store.update_task(
                    next_task(task, attempt_count=task.attempt_count + 1),
                    expected_version=task.version,
                )
            self._emit(
                "AttemptCreated",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "role": role,
                    "model": model,
                    "retry_of": retry_of,
                    "ordinal": ordinal,
                    "feedback": list(feedback),
                    "inputs": [dict(item) for item in inputs],
                },
            )
            self._emit(
                "BudgetReserved",
                task.mission_id,
                key=attempt_id,
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "subject_id": attempt_id,
                    "tokens": reservation.tokens,
                },
            )
            if (
                routing
            ):  # step 6 (S6-03): the physical route is on the timeline, frozen before the call
                self._emit(
                    "ModelRouted",
                    task.mission_id,
                    key=attempt_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    payload={
                        "runtime_profile_id": runtime_profile_id,
                        "model": model,
                        **dict(routing),
                    },
                )
            allocation = intent_config.get("allocation")
            if allocation:  # step 5 (S5-09): the §29.3 decision is on the timeline as well
                self._emit(
                    "AllocationDecided",
                    task.mission_id,
                    key=attempt_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    payload=dict(allocation),
                )
            return attempt, intent

    def claim_intent(
        self, intent_id: str, *, owner: str, lease_seconds: float
    ) -> DispatchIntent | None:
        """CAS PENDING → CLAIMED (§17.1: only one executor per Attempt)."""

        with self._store.transaction():
            intent = self._store.get_intent(intent_id)
            if intent is None:
                return None
            now = self._store.now
            if (
                intent.state in {"CLAIMED", "AGENT_CREATED"}
                and intent.lease_expires_at is not None
                and intent.lease_expires_at > now
                and intent.lease_owner != owner
            ):
                return None  # someone else holds a live lease
            if intent.state not in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
                return None
            claimed = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "CLAIMED" if intent.state == "PENDING" else intent.state,
                    "version": intent.version + 1,
                    "lease_owner": owner,
                    "lease_expires_at": now + lease_seconds,
                    "replays": intent.replays + (1 if intent.state != "PENDING" else 0),
                }
            )
            self._store.update_intent(claimed, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                if attempt.status is AttemptStatus.PENDING:
                    self._store.update_attempt(
                        next_attempt(
                            attempt,
                            AttemptStatus.CLAIMED,
                            lease_owner=owner,
                            lease_expires_at=now + lease_seconds,
                        ),
                        expected_version=attempt.version,
                    )
                    self._emit(
                        "AttemptClaimed",
                        attempt.mission_id,
                        key=attempt.id,
                        task_id=attempt.task_id,
                        attempt_id=attempt.id,
                        payload={"owner": owner},
                    )
            return claimed

    def record_agent_created(
        self, intent_id: str, *, agent_id: str, expected_turn_id: str
    ) -> DispatchIntent:
        with self._store.transaction():
            intent = self._require_intent(intent_id)
            if intent.state in {"AGENT_CREATED", "SUBMITTED", "SETTLED"}:
                if intent.agent_id != agent_id:
                    raise CommitRejected(
                        f"intent {intent_id} is bound to agent {intent.agent_id}, not {agent_id}"
                    )
                return intent
            updated = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "AGENT_CREATED",
                    "version": intent.version + 1,
                    "agent_id": agent_id,
                    "expected_turn_id": expected_turn_id,
                }
            )
            self._store.update_intent(updated, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                self._store.update_attempt(
                    next_attempt(attempt, agent_id=agent_id, turn_id=expected_turn_id),
                    expected_version=attempt.version,
                )
            self._emit(
                "AgentCreated",
                intent.mission_id,
                key=self._intent_event_key(intent),
                attempt_id=intent.subject_id if intent.kind == "attempt" else None,
                payload={
                    "agent_id": agent_id,
                    "expected_turn_id": expected_turn_id,
                    "kind": intent.kind,
                },
            )
            return updated

    @staticmethod
    def _intent_event_key(intent: DispatchIntent) -> str:
        """The idempotency key of an intent's AgentCreated / InputSubmitted record.

        P2.3f: a re-handed-off service intent creates a second executor for the same
        subject, and the record of that second creation must not be swallowed by the
        first one's key.  Only an intent whose ``creation_key`` carries the re-hand-off
        suffix gets the widened key — every intent written before this slice, and every
        legacy intent after it, keeps ``subject_id`` byte for byte.
        """

        marker = f"{intent.subject_id}:rehandoff:"
        if intent.creation_key.startswith(marker):
            return intent.creation_key
        return intent.subject_id


    def record_submitted(self, intent_id: str, *, receipt: Mapping[str, Any]) -> DispatchIntent:
        """Save the real SDK receipt; the Attempt becomes RUNNING (§25.2 start)."""

        with self._store.transaction():
            intent = self._require_intent(intent_id)
            if intent.state in {"SUBMITTED", "SETTLED"}:
                return intent
            if intent.state != "AGENT_CREATED":
                raise CommitRejected(
                    f"intent {intent_id} is {intent.state}; cannot record a submission"
                )
            if receipt.get("turn_id") != intent.expected_turn_id:
                raise CommitRejected("SDK receipt turn_id differs from the expected turn identity")
            updated = DispatchIntent(
                **{
                    **intent.to_json(),
                    "state": "SUBMITTED",
                    "version": intent.version + 1,
                    "receipt": dict(receipt),
                }
            )
            self._store.update_intent(updated, expected_version=intent.version)
            if intent.kind == "attempt":
                attempt = self._require_attempt(intent.subject_id)
                self._store.update_attempt(
                    next_attempt(attempt, AttemptStatus.RUNNING), expected_version=attempt.version
                )
                self._emit(
                    "AttemptStarted",
                    intent.mission_id,
                    key=attempt.id,
                    task_id=attempt.task_id,
                    attempt_id=attempt.id,
                    payload={"receipt": dict(receipt)},
                )
            else:
                self._emit(
                    "InputSubmitted",
                    intent.mission_id,
                    key=self._intent_event_key(intent),
                    payload={"receipt": dict(receipt), "kind": intent.kind},
                )
            return updated

    def renew_lease(
        self,
        attempt_id: str,
        *,
        owner: str,
        lease_seconds: float,
        liveness: Mapping[str, Any],
        minimum_remaining_seconds: float = 0.0,
    ) -> Attempt:
        """HeartbeatReceived (§16.2): renew only on evidence the executor is alive (D6')."""

        if not 0 <= minimum_remaining_seconds <= lease_seconds:
            raise ValueError("minimum remaining lease must be within the lease duration")
        with self._store.transaction() as connection:
            attempt = self._require_attempt(attempt_id)
            task = self._require_task(attempt.task_id)
            mission = self._require_mission(attempt.mission_id)
            if (
                attempt.status in TERMINAL_ATTEMPT
                or task.status in TERMINAL_TASK
                or mission.status in TERMINAL_MISSION
            ):
                raise CommitRejected("a terminal Attempt, Task or Mission cannot renew its lease")
            if attempt.lease_owner not in (None, owner):
                # §17.6: a lapsed lease may be taken over; a live one may not.
                if (
                    attempt.lease_expires_at is not None
                    and attempt.lease_expires_at > self._store.now
                ):
                    raise CommitRejected(f"attempt {attempt_id} is leased to {attempt.lease_owner}")
            expires = self._store.now + lease_seconds
            progress = liveness.get("progress")
            marker = None if progress is None else int(progress)
            # Persist blocker transitions even between lease renewals. The last
            # real observation survives reopening the Store; polling is not SDK
            # progress, and time spent blocked must not age the next stall window.
            blocker_changed = resumed = False
            if liveness.get("state") == str(AgentTurnState.RUNNING):
                previous = connection.execute(
                    "SELECT payload_json FROM events WHERE mission_id = ? "
                    "AND attempt_id = ? AND type = 'HeartbeatReceived' "
                    "ORDER BY seq DESC LIMIT 1",
                    (attempt.mission_id, attempt.id),
                ).fetchone()
                prior = {} if previous is None else json.loads(previous[0]).get("liveness", {})
                was_blocked = prior.get("blocked") is True
                blocked = liveness.get("blocked") is True
                blocker_changed = was_blocked != blocked
                resumed = was_blocked and not blocked
            # Critic polling is much more frequent than lease renewal. Keep its
            # authority check in this transaction, but do not rewrite history on
            # every poll when the same owner still has ample time and no progress.
            if (
                minimum_remaining_seconds > 0
                and attempt.lease_owner == owner
                and attempt.lease_expires_at is not None
                and attempt.lease_expires_at - self._store.now > minimum_remaining_seconds
                and marker == attempt.progress_marker
                and attempt.progress_at is not None
                and not blocker_changed
            ):
                return attempt
            progress_at = attempt.progress_at
            if marker != attempt.progress_marker or progress_at is None or resumed:
                progress_at = self._store.now
            updated = next_attempt(
                attempt,
                lease_owner=owner,
                lease_expires_at=expires,
                progress_marker=marker,
                progress_at=progress_at,
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._emit(
                "HeartbeatReceived",
                attempt.mission_id,
                key=f"{attempt_id}:{int(expires * 1000)}:{updated.version}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={"owner": owner, "lease_expires_at": expires, "liveness": dict(liveness)},
            )
            return updated

    def _release_attempt_charge(self, attempt: Attempt) -> bool:
        """A failure that is not the model's own mistake gives its attempt back.

        2026-09-28 用户决定：格式没写对、服务出错、执行被打断不扣任务次数。账本（任务→
        任务总账→全局）与 ``task.attempt_count`` 同一事务里一起退——能否再试、剩余次数、
        人工重试上限读的是后者。只退普通预留扣的次数：选择合成与系统尾部额度扣的不退，
        否则等于凭空多给次数。按尝试幂等。
        """

        from .failure_classes import classify_failure, NON_MODEL

        category = classify_failure(attempt.failure)
        if category not in NON_MODEL:
            return False
        if self._store.connection.execute(
            "SELECT 1 FROM events WHERE idempotency_key = ?",
            (f"AttemptChargeReleased:{attempt.id}",),
        ).fetchone() is not None:
            return False
        reservation = self._ledger.reservation(attempt.id)
        if reservation is None or reservation["account_id"] != task_account(attempt.task_id):
            return False
        self._ledger.release_attempt(reservation["account_id"])
        task = self._require_task(attempt.task_id)
        self._store.update_task(
            next_task(task, attempt_count=max(0, task.attempt_count - 1)),
            expected_version=task.version,
        )
        self._emit(
            "AttemptChargeReleased",
            attempt.mission_id,
            key=attempt.id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            payload={"failure_class": category, "reason": (attempt.failure or {}).get("reason")},
        )
        return True

    def mark_attempt_lost(
        self, attempt_id: str, *, reason: str, detail: Mapping[str, Any] | None = None
    ) -> Attempt:
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.LOST:
                return attempt
            updated = next_attempt(
                attempt, AttemptStatus.LOST, failure={"reason": reason, **jsonable(dict(detail or {}))})
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            # Loss is a control-plane observation, not proof that the SDK call
            # stopped; the original late-accounting scanner owns settlement.
            self._emit(
                "AttemptLost",
                attempt.mission_id,
                key=attempt.id,
                task_id=attempt.task_id,
                attempt_id=attempt.id,
                payload={"reason": reason, **jsonable(dict(detail or {}))},
            )
            return updated

    def mark_attempt_timed_out(
        self, attempt_id: str, *, reason: str, detail: Mapping[str, Any]
    ) -> Attempt:
        """D6' stall: alive executor with no blocker and no progress within stall_seconds."""

        detail = jsonable(detail)
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.TIMED_OUT:
                return attempt
            updated = next_attempt(
                attempt, AttemptStatus.TIMED_OUT, failure={"reason": reason, **dict(detail)}
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            # A stalled live turn must first lose execution rights and receive
            # cancellation; the original late-accounting scanner owns settlement.
            self._emit(
                "AttemptTimedOut",
                attempt.mission_id,
                key=attempt.id,
                task_id=attempt.task_id,
                attempt_id=attempt.id,
                payload={"reason": reason, **dict(detail)},
            )
            return updated

    # -------------------------------------------------------------- results
    def import_usage(self, subject_id: str, mission_id: str, facts: Sequence[UsageFact]) -> int:
        with self._store.transaction():
            return self._ledger.import_usage(
                subject_id=subject_id, mission_id=mission_id, facts=facts
            )

    def settle_subject(
        self,
        subject_id: str,
        mission_id: str,
        *,
        task_id: str | None = None,
        tool_calls: int | None = None,
    ) -> Mapping[str, Any]:
        with self._store.transaction():
            return self._settle_subject(
                subject_id, mission_id, task_id=task_id, tool_calls=tool_calls
            )

    def _settle_subject(
        self,
        subject_id: str,
        mission_id: str,
        *,
        task_id: str | None,
        tool_calls: int | None = None,
    ) -> Mapping[str, Any]:
        # A terminal business state cannot downgrade UNKNOWN to known-only zero: the
        # ledger's strict settlement check is the only one.
        if self._assurance_settlement is None:
            raise BudgetError("Assurance physical settlement reader is not installed; reservation held")
        self._assurance_settlement.require_settled_locked(subject_id, mission_id)
        if self._taskgraph_dispatch is None:
            raise CommitRejected("TASKGRAPH_SETTLEMENT_ASSEMBLY_REQUIRED")
        # A terminal business state and even a known-only accounting request
        # cannot erase a live historical executor or an unresolved tool effect.
        self._taskgraph_dispatch.recheck_settlement(self._store, subject_id, mission_id)
        if tool_calls is None:
            tool_calls = 0 if self.tool_calls_for is None else int(self.tool_calls_for(subject_id))
        settled = self._ledger.settle(subject_id=subject_id, tool_calls=tool_calls)
        self.release_terminal_tail_holds(mission_id=mission_id, task_id=task_id)
        self._emit(
            "BudgetReleased",
            mission_id,
            key=subject_id,
            task_id=task_id,
            payload={
                "subject_id": subject_id,
                "settled_tokens": settled["settled_tokens"],
                "released_tokens": int(settled["reserved_tokens"])
                - int(settled["settled_tokens"] or 0),
                "settled_tool_calls": int(settled.get("settled_tool_calls") or 0),
            },
        )
        return settled

    def check_result_evidence(self, mission_id: str, envelope: ResultEnvelope) -> None:
        """P33-08/09: refuse disallowed evidence kinds before result admission.

        This is a domain vocabulary gate, not proof that a reference resolves. Code
        evidence retains its legacy semantics (D2); source/knowledge validation is
        still performed by the verifier. Inspect all claims and the envelope, even
        when claim-local evidence would otherwise override the envelope's evidence.
        """

        domain = self.domain_for(mission_id)
        if domain.id == CODE_DOMAIN:
            return
        references = [
            *envelope.evidence,
            *(ref for claim in envelope.claims for ref in claim.evidence),
        ]
        kinds = tuple(
            sorted({ref.partition(":")[0] if ":" in ref else "file" for ref in references})
        )
        problems = check_against_domain(
            domain, key="result evidence", success_criteria=(), evidence_kinds=kinds
        )
        if problems:
            raise CommitRejected("result_evidence_kind_not_allowed: " + "; ".join(problems))

    def record_result(
        self,
        attempt_id: str,
        *,
        envelope: ResultEnvelope,
        turn_id: str,
        artifacts: Sequence[Artifact],
        usage_refs: Sequence[str],
        port_claims: Sequence[Any] = (),
    ) -> StoredResult:
        """ResultSubmitted (§16.2): Attempt RUNNING → SUBMITTED, Task ACTIVE → VERIFYING.

        Idempotent on ``(attempt_id, turn_id)``: a duplicate delivery returns the
        stored result and appends nothing (§17.4).
        """

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            existing = self._store.find_result_for_attempt(attempt_id)
            if existing is not None and existing.turn_id == turn_id:
                from .completion_inputs import load_completion_result_inputs
                frozen = load_completion_result_inputs(self._store, existing)
                if tuple(port_claims) != tuple(frozen.port_claims):
                    raise CommitRejected("result replay changed its frozen output claims")
                return existing
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(
                    f"attempt {attempt_id} is {attempt.status}; cannot accept a result"
                )
            if envelope.attempt_id != attempt_id or envelope.task_id != attempt.task_id:
                raise CommitRejected("result identity does not match the Attempt")
            if attempt.turn_id != turn_id:
                raise CommitRejected("result turn differs from the Attempt's bound turn")
            self.check_result_evidence(attempt.mission_id, envelope)
            stored = StoredResult(
                envelope=envelope,
                turn_id=turn_id,
                verification_state="PENDING",
                verdict=None,
                received_at=self._store.now,
                artifacts=tuple(artifact.id for artifact in artifacts),
                usage_refs=tuple(usage_refs),
            )
            # D4-15 (L3-2): the version is assigned here, inside the transaction, along the
            # (mission, path) lineage — two candidates snapshotting concurrently never collide
            lineage = next_versions(self._store.list_mission_artifacts(attempt.mission_id))
            registered: list[Artifact] = []
            for artifact in artifacts:
                if self._store.get_artifact(artifact.id) is None:
                    version = lineage.get(artifact.path, 0) + 1
                    lineage[artifact.path] = version
                    artifact = replace(artifact, version=version)
                self._store.upsert_artifact(artifact)
                registered.append(artifact)
            stored = replace(stored, artifacts=tuple(artifact.id for artifact in registered))
            from .completion_inputs import validate_result_port_claims
            completion_claims = validate_result_port_claims(
                self._store, commit=self, mission=self._require_mission(attempt.mission_id),
                task=self._require_task(attempt.task_id), result=stored,
                artifacts=registered, port_claims=port_claims,
            )
            self._store.insert_result(stored)
            self._store.fault("mid_commit", "attempt")
            for index, proposal in enumerate(envelope.claims, start=1):
                self._store.upsert_claim(
                    Claim(
                        id=ids.claim_id(envelope.id, index),
                        content=proposal.content,
                        type=proposal.type,
                        status=ClaimStatus.PROPOSED,
                        source_task=attempt.task_id,
                        source_attempt=attempt_id,
                        evidence=proposal.evidence or envelope.evidence,
                        dependencies=envelope.used_knowledge,
                        verifier_results=(),
                        confidence_metadata={
                            "self_reported_confidence": proposal.confidence,
                            "proposed_supersedes": proposal.supersedes,
                        },
                        supersedes=None,
                        mission_id=attempt.mission_id,
                        result_id=envelope.id,
                        key=proposal.key,
                        stance=proposal.stance,
                        proposed_by=attempt.agent_id or "",
                        contradicts=proposal.contradicts,
                    )
                )
            self._store.update_attempt(
                next_attempt(attempt, AttemptStatus.SUBMITTED, result_id=envelope.id),
                expected_version=attempt.version,
            )
            task = self._require_task(attempt.task_id)
            if (
                task.status is TaskStatus.ACTIVE
            ):  # D3-5': another candidate may already be VERIFYING
                self._store.update_task(
                    next_task(task, TaskStatus.VERIFYING), expected_version=task.version
                )
            self._emit(
                "ResultSubmitted",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "result_id": envelope.id,
                    "outcome": str(envelope.outcome),
                    "artifacts": list(stored.artifacts),
                    "claims": len(envelope.claims),
                    **({"completion_port_claims": list(completion_claims)}
                       if completion_claims is not None else {}),
                },
                actor_type="agent",
                actor_id=attempt.agent_id or attempt_id,
            )
            return stored

    def record_outcome_result(
        self,
        attempt_id: str,
        *,
        envelope: ResultEnvelope,
        turn_id: str,
        usage_refs: Sequence[str],
    ) -> StoredResult:
        """A non-candidate Result Envelope (§13 blocked / failure / no_progress /
        proposed_subtasks; D5-5): kept as history (never verified), the Attempt ends in
        RETRY_WAIT with the outcome as its failure and the Task stays ACTIVE: it is tried
        again within its attempt allowance.  Idempotent on (attempt, turn).

        Settlement is the caller's next step, after it closes the dispatch intent
        (2026-09-25): settling in here ran before the intent was closed, which the
        Assurance lane refuses -- every loop round then failed on the same row."""

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            existing = self._store.find_result_for_attempt(attempt_id)
            if existing is not None and existing.turn_id == turn_id:
                return existing
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(
                    f"attempt {attempt_id} is {attempt.status}; cannot accept a result"
                )
            if envelope.attempt_id != attempt_id or envelope.task_id != attempt.task_id:
                raise CommitRejected("result identity does not match the Attempt")
            if envelope.outcome is ResultOutcome.CANDIDATE:
                raise CommitRejected("a candidate result goes through record_result / verification")
            stored = StoredResult(
                envelope=envelope,
                turn_id=turn_id,
                verification_state="REJECTED",
                verdict=f"outcome:{envelope.outcome}",
                received_at=self._store.now,
                artifacts=(),
                usage_refs=tuple(usage_refs),
            )
            self._store.insert_result(stored)
            for index, proposal in enumerate(envelope.claims, start=1):
                claim = Claim(
                    id=ids.claim_id(envelope.id, index),
                    content=proposal.content,
                    type=proposal.type,
                    status=ClaimStatus.PROPOSED,
                    source_task=attempt.task_id,
                    source_attempt=attempt_id,
                    evidence=proposal.evidence or envelope.evidence,
                    dependencies=envelope.used_knowledge,
                    verifier_results=(),
                    confidence_metadata={
                        "self_reported_confidence": proposal.confidence,
                        "grade": "not_verified_non_candidate",
                    },
                    supersedes=None,
                    mission_id=attempt.mission_id,
                    result_id=envelope.id,
                    key=proposal.key,
                    stance=proposal.stance,
                    proposed_by=attempt.agent_id or "",
                )
                self._store.upsert_claim(claim)
                self._store.upsert_claim(
                    next_claim(next_claim(claim, ClaimStatus.UNDER_REVIEW), ClaimStatus.REJECTED)
                )
            failure = {
                "reason": f"outcome_{envelope.outcome}",
                "summary": envelope.summary,
                "proposed_tasks": [dict(item) for item in envelope.proposed_tasks],
                "risks": list(envelope.risks),
                "result_id": envelope.id,
            }
            self._store.update_attempt(
                next_attempt(
                    attempt, AttemptStatus.RETRY_WAIT, result_id=envelope.id, failure=failure
                ),
                expected_version=attempt.version,
            )
            self._emit(
                "ResultSubmitted",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "result_id": envelope.id,
                    "outcome": str(envelope.outcome),
                    "artifacts": [],
                    "claims": len(envelope.claims),
                    "proposed_tasks": [dict(item) for item in envelope.proposed_tasks],
                },
                actor_type="agent",
                actor_id=attempt.agent_id or attempt_id,
            )
            self._emit(
                "OutcomeRecorded",
                attempt.mission_id,
                key=envelope.id,
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={"outcome": str(envelope.outcome), "summary": envelope.summary[:400]},
            )
            return stored

    def record_artifact_merge_not_applicable(
        self,
        mission_id: str,
        *,
        subject: str,
        contributions: int,
        artifacts: int,
        superseded: Sequence[Mapping[str, Any]],
    ) -> Event:
        """P2.3k / defect N3: the legacy artifact merge a hierarchical Mission does not run.

        Keyed by the subject so the durable record is written once per Mission judgment
        tree rather than once per cycle.
        It carries the same reason code the mode gate would have raised
        (``SEMANTICS_IS_HIERARCHICAL``) and, so the reader can see what the tree holds
        instead, every path more than one contribution wrote — with the writer that was
        kept and why (``acceptance_order`` or ``criterion_link``).
        """

        return self._emit(
            ARTIFACT_MERGE_NOT_APPLICABLE,
            mission_id,
            key=subject,
            payload={
                "reason": "SEMANTICS_IS_HIERARCHICAL",
                "subject": subject,
                "redirect": "root_resolution_contributions",
                "contributions": int(contributions),
                "artifacts": int(artifacts),
                "superseded": [dict(item) for item in superseded],
                "detail": (
                    "this Mission runs under the hierarchical semantics; an all-ancestors "
                    "merge would read 'independent branches' off Task.dependency_ids, "
                    "which the materialised occurrences leave empty by design, so two leaves "
                    "writing one path would be an artifact_conflict overturning a root "
                    "GoalResolution that already stands. The judgment tree is built from "
                    "the resolution's CURRENT contributions instead: later acceptances "
                    "override earlier ones and a criterion-linked step's accepted output "
                    "overrides the rest (§9.1, §21.5)."
                ),
            },
        )

    def record_late_result(
        self, attempt_id: str, *, turn_id: str, summary: str, artifacts: Sequence[str]
    ) -> Attempt:
        """A result that arrived after its Attempt was superseded / cancelled (D3-6'):
        history only — ``ResultRejected(reason=superseded)``, no state transition."""

        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status not in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}:
                raise CommitRejected(f"attempt {attempt_id} is {attempt.status}; not a late result")
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt_id}:{turn_id}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={
                    "reason": "superseded",
                    "detail": {"summary": summary, "artifacts": list(artifacts)},
                    "turn_id": turn_id,
                },
            )
            return attempt

    def reject_result(
        self, attempt_id: str, *, turn_id: str, reason: str, detail: Mapping[str, Any]
    ) -> Attempt:
        """An invalid / forged submission: ResultRejected, Attempt → RETRY_WAIT, Task stays ACTIVE (S2-07)."""

        detail = jsonable(detail)
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            if attempt.status is AttemptStatus.RETRY_WAIT:
                return attempt
            if attempt.status is not AttemptStatus.RUNNING:
                raise CommitRejected(f"attempt {attempt_id} is {attempt.status}; nothing to reject")
            updated = next_attempt(
                attempt, AttemptStatus.RETRY_WAIT, failure={"reason": reason, **dict(detail)}
            )
            self._store.update_attempt(updated, expected_version=attempt.version)
            self._release_attempt_charge(updated)
            self._emit(
                "ResultRejected",
                attempt.mission_id,
                key=f"{attempt_id}:{turn_id}",
                task_id=attempt.task_id,
                attempt_id=attempt_id,
                payload={"reason": reason, "detail": dict(detail), "turn_id": turn_id},
            )
            return updated

    def wait_for_admission_usage(self, attempt_id: str) -> Task:
        """Persist an accounting wait after a real, already rejected SDK denial.

        Pausing reuses TaskPaused; it neither settles an UNKNOWN reservation nor
        changes the original Task contract. Only reconciled, settled usage can
        clear this system pause.
        """
        with self._store.transaction():
            attempt = self._require_attempt(attempt_id)
            failure = attempt.failure or {}
            error = failure.get("error", {})
            detail = error.get("detail", {}) if isinstance(error, Mapping) else {}
            if (
                attempt.status is not AttemptStatus.RETRY_WAIT
                or failure.get("reason") != "provider_admission_denied"
                or not isinstance(detail, Mapping)
                or detail.get("reason_code") != "usage_unresolved"
            ):
                raise CommitRejected("no rejected admission usage wait to persist")
            task = self._require_task(attempt.task_id)
            reason = "provider_admission:usage_unresolved"
            if task.paused and task.pause_reason == reason:
                return task
            if task.status in TERMINAL_TASK:
                return task
            updated = next_task(task, paused=True, pause_reason=reason)
            self._store.update_task(updated, expected_version=task.version)
            self._emit("TaskPaused", task.mission_id, key=f"{attempt_id}:admission_usage",
                       task_id=task.id, attempt_id=attempt_id,
                       payload={"reason": reason, "admission": dict(detail)})
            return updated

    def resume_admission_usage(self, task_id: str) -> bool:
        """Clear only our accounting pause, after all real reservations settle."""
        with self._store.transaction():
            task = self._require_task(task_id)
            if (not task.paused or task.pause_reason != "provider_admission:usage_unresolved"
                    or task.status in TERMINAL_TASK):
                return False
            attempts = self._store.list_attempts(task_id)
            for attempt in attempts:
                reservation = self._ledger.reservation(attempt.id)
                if self._ledger.has_unknown_usage(attempt.id) or (
                    reservation is not None and reservation["state"] != "SETTLED"
                ):
                    return False
            updated = next_task(task, paused=False, pause_reason=None)
            self._store.update_task(updated, expected_version=task.version)
            self._emit("TaskResumed", task.mission_id,
                       key=f"{task_id}:admission_usage:{task.version}", task_id=task.id,
                       payload={"reason": "provider_usage_reconciled"})
            return True

    def start_verification(self, result_id: str) -> StoredResult:
        with self._store.transaction():
            stored = self._require_result(result_id)
            attempt = self._require_attempt(stored.envelope.attempt_id)
            if stored.verification_state == "PENDING":
                self._store.set_result_verification(result_id, state="RUNNING", verdict=None)
                self._store.update_attempt(
                    next_attempt(attempt, AttemptStatus.VERIFYING), expected_version=attempt.version
                )
                for claim in self._store.list_claims(result_id):
                    self._store.upsert_claim(next_claim(claim, ClaimStatus.UNDER_REVIEW))
                self._emit(
                    "VerificationStarted",
                    attempt.mission_id,
                    key=result_id,
                    task_id=attempt.task_id,
                    attempt_id=attempt.id,
                    payload={"result_id": result_id},
                )
            return self._require_result(result_id)

    def record_verification_layer(
        self, result_id: str, *, layer: str, status: str, detail: Mapping[str, Any],
        requirements_revision: int | None = None,
    ) -> None:
        """Record one layer under the requirements revision it was judged against: the
        revision the result was produced under (``None``), or — for an accepted result
        reviewed again under amended requirements (TaskGraph 补全第四批) — the newer one.
        A revision under which the result is already accepted is immutable."""
        from .completion_inputs import frozen_requirements_revision

        with self._store.transaction():
            stored = self._require_result(result_id)
            frozen = frozen_requirements_revision(self._store, stored)
            revision = frozen if requirements_revision is None else int(requirements_revision)
            from ..storage.operation_completion_store import OperationCompletionStore
            from .assurance_validity import acceptance_id_for

            accepted = (
                stored.verification_state == "DONE" and stored.verdict == "PASS" and revision == frozen
            ) or OperationCompletionStore(self._store).get_acceptance_scope_exact(
                stored.envelope.mission_id, acceptance_id_for(stored.envelope.task_id, result_id, revision)
            ) is not None
            if accepted:
                known = next(
                    (
                        row
                        for row in self._store.list_verifications(result_id, requirements_revision=revision)
                        if row["layer"] == layer
                    ),
                    None,
                )
                if (
                    known is not None
                    and known["status"] == status
                    and sha256_hex(known["detail"]) == sha256_hex(dict(detail))
                ):
                    return
                raise CommitRejected("accepted result verification history is immutable")
            self._store.upsert_verification(
                result_id=result_id,
                attempt_id=stored.envelope.attempt_id,
                requirements_revision=revision,
                layer=layer,
                status=status,
                detail=detail,
            )
            self._emit(
                "VerificationLayerRecorded",
                stored.envelope.mission_id,
                # 同一结果同一层再验一次、结论或明细变了，要有自己的事件（阶段 G）；一模一样的重放
                # 仍是同一条。键里带要求版本：改要求后按新版重审同一份结果，某一层的结论与明细可能
                # 和旧版一字不差，那也是新的一行，要有自己的事件（联测真机库重建核对发现的静默改动）
                key=f"{result_id}:{layer}:r{revision}:{sha256_hex([status, dict(detail)])[:16]}",
                task_id=stored.envelope.task_id,
                attempt_id=stored.envelope.attempt_id,
                payload={
                    "layer": layer,
                    "status": status,
                    "summary": detail.get("summary"),
                    "verifier_version": detail.get("verifier_version"),
                    "requirements_revision": revision,
                },
            )

    def _acceptance_materials(
        self, stored: StoredResult, task: Task, attempt: Attempt, mission: Mission,
        *, verifier_results: Sequence[Mapping[str, Any]], owner: str | None,
        connectors: Mapping[str, Any] | None, deployment: DeploymentPolicy | None,
    ) -> dict[str, Any] | Task:
        """Shared nonpublishing eligibility; failures retain their actual failure path.

        A valid return has not accepted the result, graded a claim, published an
        action, or completed/superseded an Attempt.
        """
        result_id = stored.envelope.id
        stale = KnowledgeIndex.load(self._store, mission.id).check(
            stored.envelope.used_knowledge
        )
        if stale:  # D4-4': the reference check is repeated inside the Commit (TOCTOU)
            return self.fail_result(
                result_id,
                failures=[
                    {
                        "layer": "rule_check",
                        "status": "FAIL",
                        "summary": "used_knowledge_stale: " + "; ".join(stale),
                        "detail": {"problems": stale, "reason": "used_knowledge_stale"},
                    }
                ],
                owner=owner,
            )
        # 2026-09-29（plans/2026-09-28-system-operations）：操作申请单由系统按已批准效果
        # 生成，步骤结果里的 actions/*.json 只是普通文件，不检查也不退回。
        return {"verifier_results": verifier_results}

    def accept_result(
        self, result_id: str, *, verifier_results: Sequence[Mapping[str, Any]],
        owner: str | None = None, connectors: Mapping[str, Any] | None = None,
        deployment: DeploymentPolicy | None = None,
    ) -> Task:
        from .resolution_commits import ResolutionCommitRejected

        prepared = self._prepare_assured_acceptance(result_id)
        for retry in (False, True):
            try:
                with self._store.transaction():
                    completed = self._accept_result(result_id, verifier_results=verifier_results,
                                                    owner=owner, connectors=connectors,
                                                    deployment=deployment)
            except ResolutionCommitRejected as error:
                # A source moved between the fresh preparation and BEGIN IMMEDIATE.
                # Exactly one bounded re-preparation; never a licence by retry.
                if error.reason != "RECHECK_REQUIRED" or retry or prepared is None:
                    raise
                prepared = self._prepare_assured_acceptance(result_id)
                continue
            break
        if prepared is not None:
            self._assurance_validity.forget(prepared.identity.mission_id,
                                            str(prepared.record.record_id))
        return completed

    def accept_carried_result(self, result_id: str, *, requirements_revision: int) -> Any:
        """TaskGraph 补全第四批：一份按旧版要求通过、被新计划沿用的结果，审阅员按新版要求重审
        通过后，写一条按新版要求的验收（同一份结果与产物、带要求版本的新验收编号）。
        不动尝试 / 任务 / 产物状态、不结清预算——那些在它当初通过时都已落定。"""
        from ..assurance.codec import AssuranceError
        from .resolution_commits import ResolutionCommitRejected

        def prepare() -> Any:
            with self._store.read_view():
                stored = self._require_result(result_id)
                mission_id = stored.envelope.mission_id
            validity = getattr(self, "_assurance_validity", None)
            if validity is None:
                return None
            try:
                return validity.prepare_accept_use_for_result(mission_id, result_id, requirements_revision)
            except AssuranceError as error:
                raise ResolutionCommitRejected(
                    error.code, "the current use certificate could not be prepared") from error

        prepared = prepare()
        for retry in (False, True):
            try:
                with self._store.transaction():
                    receipt = self._accept_carried(result_id, int(requirements_revision))
            except ResolutionCommitRejected as error:
                if error.reason != "RECHECK_REQUIRED" or retry or prepared is None:
                    raise
                prepared = prepare()
                continue
            break
        if prepared is not None:
            self._assurance_validity.forget(prepared.identity.mission_id, str(prepared.record.record_id))
        return receipt

    def _accept_carried(self, result_id: str, requirements_revision: int) -> Any:
        from .completion_inputs import load_completion_result_inputs
        from .leaf_acceptance import LeafAcceptanceAssembly

        stored = self._require_result(result_id)
        if stored.verification_state != "DONE" or stored.verdict != "PASS":
            raise CommitRejected("only an accepted result is reviewed again under newer requirements")
        attempt = self._require_attempt(stored.envelope.attempt_id)
        mission_id, task_id = stored.envelope.mission_id, stored.envelope.task_id
        frozen = load_completion_result_inputs(self._store, stored, requirements_revision=requirements_revision)
        self._lock_assured_acceptance(mission_id, task_id, result_id, requirements_revision)
        receipt = LeafAcceptanceAssembly(self._store, self).accept(
            mission_id, task_id, result_id=result_id,
            layers=self._store.list_verifications(result_id, requirements_revision=requirements_revision),
            artifacts=tuple(self._store.get_artifact(key) for key in stored.artifacts),
            producer_agent_ids=(attempt.agent_id,),
            reviewer_agent_id=f"critic:{attempt.id}",
            now_ms=int(self._store.now * 1000),
            input_manifest_hash=frozen.frozen.manifest_hash,
            port_claims=frozen.port_claims,
        )
        self._emit(
            "CarriedResultAccepted", mission_id,
            key=f"{result_id}:r{requirements_revision}",
            task_id=task_id, attempt_id=attempt.id,
            payload={"result_id": result_id, "requirements_revision": int(requirements_revision),
                     "acceptance_id": str(receipt.acceptance_id)},
        )
        return receipt

    def _prepare_assured_acceptance(self, result_id: str) -> Any:
        """Assurance 1.1: compute the current ACCEPT use right before the acceptance UoW.

        Every layer the router recorded (an inventoried source) has already moved
        the mission epoch by now, so a candidate prepared earlier would be stale.
        Outside the write transaction, read only; the UoW then locks it first."""
        from ..assurance.codec import AssuranceError
        from .resolution_commits import ResolutionCommitRejected

        with self._store.read_view():
            stored = self._store.get_result(result_id)
            if stored is None:
                return None
            mission_id = stored.envelope.mission_id
            if stored.verification_state == "DONE" and stored.verdict == "PASS":
                return None  # replay; the committed certificate licenses it
        validity = getattr(self, "_assurance_validity", None)
        if validity is None:
            return None  # accept_review refuses USE_CERTIFICATE_REQUIRED; never a fallback
        try:
            return validity.prepare_accept_use_for_result(mission_id, result_id)
        except AssuranceError as error:
            raise ResolutionCommitRejected(
                error.code, "the current use certificate could not be prepared"
            ) from error

    def _lock_assured_acceptance(self, mission_id: str, task_id: str, result_id: str,
                                 requirements_revision: int | None = None) -> Any:
        """Assurance 1.1: the prepared ACCEPT use is locked before this UoW's own writes.

        The freshness/authority/root gates must see the world as it was when the
        transaction began; the result, artifact and acceptance rows this UoW then
        moves are its intended effect, not foreign changes. The certificate itself
        is committed later by ``accept_review`` in this same generation. A missing
        candidate is not licensed here; ``accept_review`` refuses it.  Returns the locked
        candidate (its official review record is what claim confirmation reads), or None."""
        from ..assurance.codec import AssuranceError
        from .assurance_validity import ACCEPTANCE_CONSUMER, acceptance_id_for
        from .resolution_commits import ResolutionCommitRejected

        validity = getattr(self, "_assurance_validity", None)
        if validity is None:
            return None
        from .completion_inputs import frozen_requirements_revision

        revision = (frozen_requirements_revision(self._store, self._require_result(result_id))
                    if requirements_revision is None else int(requirements_revision))
        candidate = validity.candidate_for_consumer(
            mission_id, ACCEPTANCE_CONSUMER, acceptance_id_for(task_id, result_id, revision)
        )
        if candidate is None:
            return None
        try:
            validity.lock_use_locked(candidate, now_ms=int(self._store.now * 1000))
        except AssuranceError as error:
            raise ResolutionCommitRejected(
                error.code, "the current use certificate refused this acceptance"
            ) from error
        return candidate

    def _accept_result(
        self,
        result_id: str,
        *,
        verifier_results: Sequence[Mapping[str, Any]],
        owner: str | None = None,
        connectors: Mapping[str, Any] | None = None,
        deployment: DeploymentPolicy | None = None,
    ) -> Task:
        """PASS (§24 step 11) in one transaction (D3-6'): claims → VERIFIED, Attempt →
        COMPLETED, Task → COMPLETED (via VERIFYING when a sibling candidate had not
        moved it yet), the losing candidates → SUPERSEDED with their results kept as
        history, and every dependent whose dependencies are now all COMPLETED → READY."""

        from .completion_inputs import frozen_requirements_revision

        with self._store.transaction():
            stored = self._require_result(result_id)
            if stored.verification_state == "DONE" and stored.verdict == "PASS":
                from ..storage.operation_completion_store import OperationCompletionStore
                from .assurance_validity import acceptance_id_for
                acceptance_id = acceptance_id_for(
                    stored.envelope.task_id, result_id, frozen_requirements_revision(self._store, stored))
                if OperationCompletionStore(self._store).get_acceptance_scope_exact(
                    stored.envelope.mission_id, acceptance_id
                ) is None:
                    raise CommitRejected("verified result has no atomic scoped Acceptance")
                return self._require_task(stored.envelope.task_id)
            licensed = self._lock_assured_acceptance(stored.envelope.mission_id,
                                                     stored.envelope.task_id, result_id)
            attempt = self._require_attempt(stored.envelope.attempt_id)
            self._require_lease(attempt, owner)
            task = self._require_task(stored.envelope.task_id)
            mission = self._require_mission(stored.envelope.mission_id)
            from .completion_inputs import load_completion_result_inputs
            frozen_completion = load_completion_result_inputs(self._store, stored)
            materials = self._acceptance_materials(
                stored, task, attempt, mission, verifier_results=verifier_results, owner=owner,
                connectors=connectors, deployment=deployment,
            )
            if isinstance(materials, Task):
                return materials
            verifier_results = materials["verifier_results"]
            if task.status is TaskStatus.ACTIVE:
                verifying = next_task(task, TaskStatus.VERIFYING)
                self._store.update_task(verifying, expected_version=task.version)
                task = verifying
            self._store.set_result_verification(result_id, state="DONE", verdict="PASS")
            grading = self._grade_and_project(
                mission,
                task,
                attempt,
                stored,
                review=None if licensed is None else licensed.record,
                certificate=None if licensed is None else licensed.certificate,
            )
            self._store.update_attempt(
                next_attempt(attempt, AttemptStatus.COMPLETED), expected_version=attempt.version
            )
            completed = next_task(
                task,
                (TaskStatus.VERIFYING
                 if frozen_completion.scope.required_effect_keys
                 else TaskStatus.COMPLETED),
                accepted_result_id=result_id,
                accepted_artifacts=stored.artifacts,
            )
            self._store.update_task(completed, expected_version=task.version)
            for artifact_id in stored.artifacts:  # P3.1 fix F-ORCH-3: in this transaction
                self._store.update_artifact_verification(artifact_id, "VERIFIED")
            # step 9 (plan D9-10'): an Agent's file that tries to set policy is refused on
            # record in the same transaction; it never reaches the registry
            self.refuse_policy_files(
                stored, mission_id=mission.id, task_id=task.id, result_id=result_id
            )
            from .leaf_acceptance import LeafAcceptanceAssembly
            # Preparation, its accepted bytes and contribution share the result
            # transaction. No effect proposal is inferred from a Worker file.
            LeafAcceptanceAssembly(self._store, self).accept(
                mission.id, task.id, result_id=result_id,
                layers=self._store.list_verifications(
                    result_id, requirements_revision=frozen_requirements_revision(self._store, stored)),
                artifacts=tuple(self._store.get_artifact(key) for key in stored.artifacts),
                producer_agent_ids=(attempt.agent_id,),
                reviewer_agent_id=f"critic:{attempt.id}",
                now_ms=int(self._store.now * 1000),
                input_manifest_hash=frozen_completion.frozen.manifest_hash,
                port_claims=frozen_completion.port_claims,
            )
            if not self._ledger.has_unknown_usage(attempt.id):  # ORCH §12.2 (P2-12)
                self._settle_subject(attempt.id, mission.id, task_id=task.id)
            self._emit(
                "VerificationPassed",
                mission.id,
                key=result_id,
                task_id=task.id,
                attempt_id=attempt.id,
                payload={
                    "result_id": result_id,
                    "layers": [dict(item) for item in verifier_results],
                    "claims": grading,
                },
            )
            self._store.fault("after_accept_before_supersede", "attempt")
            superseded = []
            for other in self._store.list_attempts(task.id):
                if other.id != attempt.id and other.status in OPEN_ATTEMPT_STATES:
                    self._close_attempt(other, AttemptStatus.SUPERSEDED, reason="sibling_accepted")
                    superseded.append(other.id)
            self._emit(
                "TaskCompleted" if completed.status is TaskStatus.COMPLETED else "PreparationAccepted",
                mission.id,
                key=task.id,
                task_id=task.id,
                payload={
                    "result_id": result_id,
                    "artifacts": list(stored.artifacts),
                    "superseded": superseded,
                },
            )
            return completed

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
            from ..deployment.root import current_statements

            criteria = list(current_statements(self._store, mission))
            if [item.get("criterion") for item in judgments] != criteria:
                raise CommitRejected("judgments must cover the Mission's current requirements in order")
            met = all(bool(item.get("met")) for item in judgments)
            terminal = terminal_task(tasks)
            report = {
                **dict(mission.final_report or {}),
                "accepted_result_id": terminal.accepted_result_id,
                "accepted_artifacts": list(terminal.accepted_artifacts),
                "terminal_task_id": terminal.id,
                "summary": summary,
                "attempts": sum(task.attempt_count for task in tasks),
                "tasks": self._task_reports(mission_id),
                "success_criteria": [dict(item) for item in judgments],
                "disputes": mission_disputes(self._store, mission_id),
                "knowledge": [
                    k.id for k in self._store.list_knowledge(mission_id, status="VERIFIED")
                ],
                "lineage": lineage(self._store, mission_id),  # D4-14 / 30-27
            }
            judged = self._emit(
                "MissionSuccessJudged",
                mission_id,
                key=f"{mission_id}:{mission.version}",
                payload={"met": met, "judgments": [dict(item) for item in judgments]},
            )
            if met:
                from ..assurance.codec import AssuranceError
                from ..storage.assurance_store import AssuranceStore
                from .assurance_final_writer import request_assured_closeout

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
                        f"mission {mission_id} cannot be judged complete: {error.code}") from error
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

        from ..graph.projection_validation import GraphIntegrityError
        from .hierarchical_dispatch import HierarchicalDispatch

        try:
            return HierarchicalDispatch.for_commit(self, mission.id).network(mission.id)
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

        from ..contracts.evidence_state import Validity
        from ..storage.htn_store import HtnStore

        semantics = HtnStore(self._store)
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

        from .assurance_final_writer import request_assured_notification

        request_assured_notification(self, mission_id, final, state_version=state_version)

    def assured_closeout_pending(self, mission_id: str) -> bool:
        """An ACTIVE assured Mission judged successful and waiting for its closeout."""

        from .assurance_final_writer import assured_closeout_pending

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

        from ..storage.htn_store import HtnStore

        mission = self._store.get_mission(mission_id)
        if mission is None:
            return
        network = self._judgment_network(mission)
        from .completion_status import read_occurrence_completion
        # Assurance §7.2（2026-10-06 车道 O）：判定读的是根的内容是否满足（根结论据此形成）；根的
        # 必需效果由收尾核对收敛（结果不明 → BLOCKED_UNKNOWN），不是判定的前置。
        if not all(read_occurrence_completion(self._store, mission_id, str(root)).content_ready
                   for root in network.root_occurrence_ids):
            raise CommitRejected("approved completion Scope still has unmet content")
        semantics = HtnStore(self._store)
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

    def fail_result(
        self,
        result_id: str,
        *,
        failures: Sequence[Mapping[str, Any]],
        owner: str | None = None,
    ) -> Task:
        """FAIL: the result's claims are rejected. Retry is separate."""

        with self._store.transaction():
            stored = self._require_result(result_id)
            if stored.verification_state == "DONE" and stored.verdict == "FAIL":
                return self._require_task(stored.envelope.task_id)
            attempt = self._require_attempt(stored.envelope.attempt_id)
            self._require_lease(attempt, owner)
            task = self._require_task(stored.envelope.task_id)
            failure_reason = "verification_failed"
            self._store.set_result_verification(result_id, state="DONE", verdict="FAIL")
            for artifact_id in stored.artifacts:  # P3.1 fix F-ORCH-3: judged, and not accepted
                self._store.update_artifact_verification(artifact_id, "REJECTED")
            for claim in self._store.list_claims(result_id):
                self._store.upsert_claim(
                    next_claim(
                        claim,
                        ClaimStatus.REJECTED,
                        verifier_results=tuple(dict(item) for item in failures),
                    )
                )
            self._store.update_attempt(
                next_attempt(
                    attempt,
                    AttemptStatus.RETRY_WAIT,
                    failure={
                        "reason": failure_reason,
                        "failures": [dict(item) for item in failures],
                    },
                ),
                expected_version=attempt.version,
            )
            others_submitted = any(
                other.id != attempt.id and other.status in SUBMITTED_STATES
                for other in self._store.list_attempts(task.id)
            )
            active = task
            if task.status is TaskStatus.VERIFYING and not others_submitted:
                active = next_task(task, TaskStatus.ACTIVE)
                self._store.update_task(active, expected_version=task.version)
            self._settle_subject(attempt.id, attempt.mission_id, task_id=task.id)
            if self._release_attempt_charge(self._require_attempt(attempt.id)):
                active = self._require_task(task.id)
            self._emit(
                "VerificationFailed",
                attempt.mission_id,
                key=result_id,
                task_id=task.id,
                attempt_id=attempt.id,
                payload={"result_id": result_id, "failures": [dict(item) for item in failures]},
            )
            return active

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

    # ------------------------------------------------------------ lookups
    def _require_lease(self, attempt: Attempt, owner: str | None) -> None:
        """A verdict is committed only by the Attempt's current live lease holder (P1-3):
        a stale owner whose lease lapsed and was taken over is refused."""

        if owner is None:
            return
        if attempt.lease_owner != owner:
            raise CommitRejected(
                f"attempt {attempt.id} is leased to {attempt.lease_owner}, not {owner}"
            )
        if attempt.lease_expires_at is not None and attempt.lease_expires_at <= self._store.now:
            raise CommitRejected(f"lease of attempt {attempt.id} held by {owner} has lapsed")

    def _require_mission(self, mission_id: str) -> Mission:
        mission = self._store.get_mission(mission_id)
        if mission is None:
            raise CommitRejected(f"unknown mission {mission_id}")
        return mission

    def _require_task(self, task_id: str) -> Task:
        task = self._store.get_task(task_id)
        if task is None:
            raise CommitRejected(f"unknown task {task_id}")
        return task

    def _require_attempt(self, attempt_id: str) -> Attempt:
        attempt = self._store.get_attempt(attempt_id)
        if attempt is None:
            raise CommitRejected(f"unknown attempt {attempt_id}")
        return attempt

    def _require_intent(self, intent_id: str) -> DispatchIntent:
        intent = self._store.get_intent(intent_id)
        if intent is None:
            raise CommitRejected(f"unknown dispatch intent {intent_id}")
        return intent

    def _require_result(self, result_id: str) -> StoredResult:
        stored = self._store.get_result(result_id)
        if stored is None:
            raise CommitRejected(f"unknown result {result_id}")
        return stored


__all__ = (
    "CommitRejected",
    "CommitService",
    "GLOBAL_ACCOUNT_PREFIX",
    "MissionConflict",
    "MissionSpec",
    "Reservation",
    "global_account_id",
    "is_global_account",
    "mission_account",
    "task_account",
)
