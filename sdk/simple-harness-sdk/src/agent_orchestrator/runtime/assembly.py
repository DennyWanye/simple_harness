# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Assemble the one BaseAgent runtime the orchestrator drives (D10', D13').

``OrchestratorConfig`` is the deployment binding ORCH §2 demands: explicit
concurrency and token reserves.  Orchestration records token usage only; every
pool runs under the native runtime's local default policies.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.agents.context.tokenizer import TokenizerPort, UpperBoundTokenizer
from simple_harness.agents.ports import AgentRuntimePorts
from simple_harness.agents.runtime import AgentRuntime
from simple_harness.contracts import canonical_json
from simple_harness.execution.provider_admission import ProviderHandoffFence
from simple_harness.runtime.consumer_adapter import ConsumerRuntimePolicies

from ..artifacts.workspace import WorkspaceManager
from ..contracts import Budget
from ..governance.policies import DeploymentPolicy
from ..orchestrator.root_review import DEFAULT_MAX_CUTS_PER_REVISION
from ..scheduling.backpressure import BackpressureLimits
from .agent_worker import AgentBridge
from .model_router import DEFAULT_PROFILE, RuntimeProfile
from .tool_gateway import TOOL_NAMES, WorkspaceToolGateway, read_tool_schemas
from .domain_tools import DomainTool
from ..planning.htn.backend_port import PlanningBackend, PlanningLimits

OWNER_SCOPE = "agent-orchestrator"  # D3-10': one scope shared by every orchestrator instance




@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    evidence_root: Path
    model: str = "agent-model"
    owner_id: str = "agent-orchestrator"
    max_concurrency: int = 2
    max_concurrent_model_calls: int = 2
    #: 规划预算·答错次数：自上一次提交成功起，规划器的回答被拒几次即停（片 A 第 8 项）。
    max_planning_attempts: int = 3
    # P2.3c part 3a: how many times one ``requirements_revision`` may have its root
    # MISSION_FINAL review cut.  A re-cut is the correct answer to a leaf accepted or
    # revoked after the review was cut (review P1-7); an *unbounded* re-cut is a loop
    # that spends the Mission account every cycle, so the bound is configuration and
    # not a constant buried in the coordinator.
    max_root_review_cuts: int = DEFAULT_MAX_CUTS_PER_REVISION
    # P2.3d / defect D5-A: how many times ONE plan revision may answer a *blocking*
    # root-review rejection by asking the Planner again (§9.1's minimal branch).  The
    # Grok acceptance run had no such branch at all — ``REVIEW_REJECTED`` was recorded,
    # announced and then returned False, so 10 episodes whose leaves had all passed
    # went straight to an idle stall and ``NO_DISPATCHABLE_WORK``.  One round is the
    # default because the repair is a whole new plan revision and a second one on the
    # same revision would be the same question asked twice.
    max_root_review_repairs: int = 1
    lease_seconds: float = 60.0
    sdk_lease_ttl_seconds: float | None = None  # D3-10': SDK Run lease; default lease_seconds / 2
    stall_seconds: float = 180.0
    #: 2026-10-10：一次已交出、仍在线路上的模型调用不算"没进展"（输出上限放到 131,072 后一次思考可达
    #: 5 分钟；parse 重跑里一次 3.6 分钟的调用在 180 秒被当成卡死杀掉）。这是它单独的上限：超过它仍在
    #: 线路上，才按卡死处理；线路本身的读超时另有保护。
    provider_call_seconds: float = 900.0
    test_timeout_seconds: float = 120.0
    default_max_output_tokens: int = 4096
    max_output_tokens_ceiling: int = 8192  # SDK empty-response escalation cap (F-BA-1)
    empty_response_retries: int = 2
    planner_reserve_tokens: int = 4_000
    critic_reserve_tokens: int = 6_000
    attempt_reserve_tokens: int = 20_000
    # P3.2 (plan D4): how long a finished Mission's workspace directories are kept before
    # cleanup removes them (the registry rows and the content-addressed bytes stay)
    workspace_retention_seconds: float = 7 * 24 * 3600.0
    turn_deadline_seconds: float = 900.0
    max_model_calls_per_turn: int = 24
    max_tool_calls_per_turn: int = 48
    knowledge_sharing: bool = True  # step 4 (D4-19): the layer's kill switch
    on_retrieval_failure: str = "block"  # step 4 (D4-11'): block | degrade
    max_retrieval_failures: int = 3
    max_knowledge_items: int = 12
    # Live episode capability. Its identity/data/budgets belong in the external
    # experiment manifest; a Mission alone cannot manufacture this capability.
    appworld_execute: Callable[[str], Mapping[str, Any]] | None = field(
        default=None, repr=False, compare=False
    )
    domain_tools: Mapping[str, DomainTool] = field(default_factory=dict, kw_only=True, repr=False, compare=False)
    # step 5 (D5-2 / D5-6 / D5-7 / D5-8 / D5-15)
    planning_backend: PlanningBackend | None = field(default=None, repr=False, compare=False, kw_only=True)
    planning_backend_limits: PlanningLimits | None = field(default=None, kw_only=True)
    aging_window_seconds: float = 300.0
    # step 6 (D6-1 / D6-8)
    global_budget: Budget | None = None  # §18.2 Global Budget above every Mission; None = uncapped
    # 2026-09-25 user decision: a materialised leaf gets this fixed token allowance instead
    # of an even share of the Mission pool (None = the even share, every earlier config
    # unchanged).  Still bounded by what the pool has left, so conservation holds.
    task_max_tokens: int | None = field(default=None, kw_only=True)
    # step 6 (D6-2 / D6-3): the §18.5 caps and the gate they drive
    max_running_attempts: int | None = (
        None  # deployment-wide open Attempts; None = max_concurrency × 4
    )
    max_pending_dispatch: int = 8
    max_pending_verifications: int = 4
    low_watermark_ratio: float = 0.5
    reduced_concurrency_ratio: float = 0.5
    reduced_reserve_ratio: float = 0.5
    exploration_slots: int = 1
    verifier_workers: int = 2  # §29.1 "2 个 Verifier Worker" as the verification concurrency
    # 推后第 3 批 H12（§18.5"提高 Verifier 资源""禁止新任务继续分裂"）：待审结果积压时审阅并发
    # 升到这个上限，回落后恢复；已有计划的任务暂停开新规划轮，最长这么多秒。都不改并发上限默认值、
    # 不进准入身份。None = max(审阅数, min(2 × 审阅数, 模型调用名额))：多开的审阅拿不到模型名额只会
    # 排队、按回合墙钟超时（裁决 2026-10-07 第 6 件 B，偏离 #53），桌面默认 2/2 时不加。
    verifier_workers_ceiling: int | None = None
    decomposition_pause_seconds: float = 600.0
    deployment_policy: DeploymentPolicy = field(default_factory=DeploymentPolicy)  # D6-7
    # P3.2 (plan D2): the executor model-written code runs through — required (a probed
    # SeatbeltExecutor) when the deployment says code_execution="sandboxed"
    sandbox_executor: Any = None
    # step 6 (D6-5'): runtime profile health — unavailability cooldown and the bounded wait
    profile_failure_threshold: int = 2
    profile_cooldown_seconds: float = 60.0
    profile_wait_seconds: float = 300.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.max_planning_attempts < 1:
            raise ValueError("max_planning_attempts must be >= 1")
        if self.max_root_review_cuts < 1:
            raise ValueError("max_root_review_cuts must be >= 1")
        if self.max_root_review_repairs < 0:
            raise ValueError("max_root_review_repairs must be >= 0")
        if self.workspace_retention_seconds < 0:
            raise ValueError("workspace_retention_seconds must be >= 0")
        if self.on_retrieval_failure not in {"block", "degrade"}:
            raise ValueError("on_retrieval_failure must be 'block' or 'degrade'")
        if self.max_retrieval_failures < 1 or self.max_knowledge_items < 1:
            raise ValueError("max_retrieval_failures and max_knowledge_items must be >= 1")
        if self.max_running_attempts is None:
            object.__setattr__(self, "max_running_attempts", self.max_concurrency * 4)
        if self.max_running_attempts < 1 or self.verifier_workers < 1:  # type: ignore[operator]
            raise ValueError("max_running_attempts and verifier_workers must be >= 1")
        if not self.decomposition_pause_seconds > 0:
            raise ValueError("decomposition_pause_seconds must be > 0")
        if self.sdk_lease_ttl_seconds is None:
            object.__setattr__(self, "sdk_lease_ttl_seconds", self.lease_seconds / 2)
        elif self.lease_seconds < 2 * self.sdk_lease_ttl_seconds:
            raise ValueError(
                "lease_seconds must be at least twice sdk_lease_ttl_seconds (D3-10': an "
                "orchestration lease may only be taken over after the SDK Run lease lapsed)"
            )
        # Physical capacity is a deployment limit, independent of logical candidate
        # count. Provider admission reports slot waiting explicitly to liveness.
        if (isinstance(self.max_concurrent_model_calls, bool)
                or not isinstance(self.max_concurrent_model_calls, int)
                or self.max_concurrent_model_calls < 1):
            raise ValueError("max_concurrent_model_calls must be a positive integer")
        if self.verifier_workers_ceiling is None:
            object.__setattr__(self, "verifier_workers_ceiling", max(
                self.verifier_workers, min(2 * self.verifier_workers, self.max_concurrent_model_calls)))
        if self.verifier_workers_ceiling < self.verifier_workers:  # type: ignore[operator]
            raise ValueError("verifier_workers_ceiling must be >= verifier_workers")

    def backpressure_limits(self) -> BackpressureLimits:
        """The §18.5 caps as one registry (D6-2)."""

        return BackpressureLimits(
            max_running_attempts=int(self.max_running_attempts or 1),
            max_pending_dispatch=self.max_pending_dispatch,
            max_pending_verifications=self.max_pending_verifications,
            max_attempts_per_task=None,
            low_watermark_ratio=self.low_watermark_ratio,
        )

    @property
    def orchestrator_db(self) -> Path:
        return self.evidence_root / "orchestrator.db"

    @property
    def execution_db(self) -> Path:
        return self.evidence_root / "execution.db"

    @property
    def workspaces_root(self) -> Path:
        return self.evidence_root / "workspaces"

    def to_json(self) -> dict[str, Any]:
        return {
            "planning": {
                "backend_id": None if self.planning_backend is None else self.planning_backend.backend_id,
                "limits": None if self.planning_backend_limits is None else self.planning_backend_limits.to_json(),
            },
            "model": self.model,
            "owner_id": self.owner_id,
            "max_concurrency": self.max_concurrency,
            "max_concurrent_model_calls": self.max_concurrent_model_calls,
            "max_planning_attempts": self.max_planning_attempts,
            "max_root_review_cuts": self.max_root_review_cuts,
            "max_root_review_repairs": self.max_root_review_repairs,
            "lease_seconds": self.lease_seconds,
            "sdk_lease_ttl_seconds": self.sdk_lease_ttl_seconds,
            "stall_seconds": self.stall_seconds,
            "provider_call_seconds": self.provider_call_seconds,
            "test_timeout_seconds": self.test_timeout_seconds,
            "reserves": {
                "planner_tokens": self.planner_reserve_tokens,
                "critic_tokens": self.critic_reserve_tokens,
                "attempt_tokens": self.attempt_reserve_tokens,
            },
            "aging_window_seconds": self.aging_window_seconds,
            "global_budget": None if self.global_budget is None else self.global_budget.to_json(),
            **({} if self.task_max_tokens is None else {"task_max_tokens": self.task_max_tokens}),
            "deployment_policy": self.deployment_policy.to_json(),
            "backpressure": {
                **self.backpressure_limits().to_json(),
                "reduced_concurrency_ratio": self.reduced_concurrency_ratio,
                "reduced_reserve_ratio": self.reduced_reserve_ratio,
                "exploration_slots": self.exploration_slots,
                "verifier_workers": self.verifier_workers,
                "verifier_workers_ceiling": self.verifier_workers_ceiling,
                "decomposition_pause_seconds": self.decomposition_pause_seconds,
            },
            "knowledge": {
                "knowledge_sharing": self.knowledge_sharing,
                "on_retrieval_failure": self.on_retrieval_failure,
                "max_retrieval_failures": self.max_retrieval_failures,
                "max_knowledge_items": self.max_knowledge_items,
            },
        }


@dataclass(frozen=True, slots=True)
class RuntimePool:
    """One physical execution pool (plan D6-5'): a profile, its own AgentRuntime and its
    own SDK execution library — never shared with another model."""

    profile: RuntimeProfile
    runtime: AgentRuntime
    bridge: AgentBridge
    execution_db: Path


@dataclass(frozen=True, slots=True)
class AssembledOrchestratorRuntime:
    pools: Mapping[str, RuntimePool]
    gateway: WorkspaceToolGateway  # shared: one Tool Gateway for every pool (review P0-4)
    workspaces: WorkspaceManager  # shared: one workspace tree for every pool
    config: OrchestratorConfig
    default_profile: str

    @property
    def runtime(self) -> AgentRuntime:  # the default pool's runtime (single-profile callers)
        return self.pools[self.default_profile].runtime

    def pool(self, profile_id: str) -> RuntimePool:
        try:
            return self.pools[profile_id]
        except KeyError as error:
            raise KeyError(f"runtime profile {profile_id!r} is not configured") from error

    async def __aenter__(self) -> AssembledOrchestratorRuntime:
        for pool in self.pools.values():
            await pool.runtime.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        # all pools stop together: closing them one after another would let the pools
        # still open keep driving turns during shutdown (S6-08 finding)
        results = await asyncio.gather(
            *(pool.runtime.__aexit__(*exc_info) for pool in self.pools.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result


def execution_db_for(config: OrchestratorConfig, profile_id: str) -> Path:
    """The ``default`` pool keeps ``execution.db`` (older evidence directories still open);
    every other profile gets ``execution-<profile>.db`` — separate SDK storage per pool."""

    if profile_id == DEFAULT_PROFILE:
        return config.execution_db
    return config.evidence_root / f"execution-{profile_id}.db"


def _context_identity_path(database: Path) -> Path:
    return database.with_name(database.name + ".context.json")


def _read_context_identity(database: Path) -> dict[str, Any] | None:
    path = _context_identity_path(database)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise ValueError("unsupported context identity schema")
        body = {key: item for key, item in value.items() if key != "fingerprint"}
        if value.get("fingerprint") != sha256(canonical_json(body).encode("utf-8")).hexdigest():
            raise ValueError("context identity fingerprint mismatch")
        return value
    except (OSError, ValueError) as error:
        raise ValueError(f"context identity unreadable: {path.name}") from error


def resolve_profile_context_policy(
    config: OrchestratorConfig, *, profile_id: str = DEFAULT_PROFILE,
    tokenizer: TokenizerPort | None = None,
    fresh_policy: ContextPolicy | None = None,
) -> ContextPolicy | None:
    """Read-only Host choice: old pools stay legacy, fresh pools enable bounded reads.

    The default is the explicitly named fallback tokenizer. A custom tokenizer
    implementation must be supplied by the deployment owner; a fingerprint is
    an identity, not executable configuration. Existing legacy pools return None
    and must also keep their legacy (None) tokenizer port.
    """

    database = execution_db_for(config, profile_id)
    frozen = _read_context_identity(database)
    if frozen is not None:
        counter = tokenizer if tokenizer is not None else UpperBoundTokenizer()
        if frozen.get("tokenizer_fingerprint") != counter.fingerprint:
            raise ValueError("context identity requires the matching explicitly configured tokenizer")
        try:
            return ContextPolicy(**frozen["policy"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("context identity has an invalid policy") from error
    if database.exists():
        return None
    return fresh_policy or ContextPolicy(max_tool_result_tokens=16384, render_slack_tokens=0)


def _bind_context_identity(database: Path, profile: RuntimeProfile) -> None:
    """Bind before starting SDK recovery, including the pre-first-request crash gap.

    The sidecar is immutable configuration, not a new execution/approval ledger.
    An existing library without this identity is legacy; only an explicit fresh
    execution pool may acquire a new profile. Nothing migrates old requests.
    """

    wanted = profile.context_snapshot()
    frozen = _read_context_identity(database)
    if frozen is not None:
        if frozen != wanted:
            raise ValueError("context identity differs from the existing execution pool")
        return
    if wanted is None:
        return
    if database.exists():
        # Refuse even a library with an Agent but no context selections yet.
        # Empty pre-created SQLite files are not proof of a new profile either.
        raise ValueError("context identity missing on legacy execution pool; use its legacy profile")
    database.parent.mkdir(parents=True, exist_ok=True)
    path = _context_identity_path(database)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=database.parent,
                                         prefix=".context-", delete=False) as stream:
            temporary = stream.name
            stream.write(canonical_json(wanted))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # atomic create-if-absent; never overwrite another owner
        except FileExistsError:
            if _read_context_identity(database) != wanted:
                raise ValueError("context identity concurrently bound to another profile") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def admission_accepts(admission: Any, frozen: object) -> bool:
    """A frozen intent's admission identity against this deployment's admission: none on
    both sides, or one the guard accepts (the slot count is not part of it)."""
    if admission is None:
        return frozen is None
    accepts = getattr(admission, "accepts", None)
    return accepts(frozen) if callable(accepts) else frozen == admission.fingerprint


def _check_intent_contexts(
    config: OrchestratorConfig, profile: RuntimeProfile, provider_admission: Any = None,
) -> None:
    """The frozen intent also detects a missing/incorrect sidecar before SDK recovery."""

    database = config.orchestrator_db
    if not database.exists():
        return
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_intents'"
        ).fetchone():
            return
        for (encoded,) in connection.execute("SELECT config_json FROM dispatch_intents"):
            frozen = json.loads(encoded)
            if str(frozen.get("runtime_profile_id") or DEFAULT_PROFILE) == profile.profile_id:
                if frozen.get("runtime_context") != profile.context_snapshot():
                    raise ValueError("context identity differs from a persisted dispatch intent")
                if not admission_accepts(provider_admission, frozen.get("provider_admission_fingerprint")):
                    raise ValueError("provider admission identity differs from a persisted intent")


def assemble_orchestrator_runtime(
    config: OrchestratorConfig,
    provider=None,  # type: ignore[no-untyped-def]
    *,
    profiles: Mapping[str, RuntimeProfile] | None = None,
    default_profile: str | None = None,
    provider_admission: Any = None,
    provider_admissions: Mapping[str, Any] | None = None,
    local_provider_admissions: Mapping[str, Any] | None = None,
    provider_handoff_fence: ProviderHandoffFence | None = None,
) -> AssembledOrchestratorRuntime:
    """One pool per runtime profile (D6-5').  ``provider`` alone is the single-profile
    path every earlier step used: the ``default`` profile with ``config.model``."""

    config.evidence_root.mkdir(parents=True, exist_ok=True)
    if profiles is None:
        if provider is None:
            raise ValueError("either a provider or runtime profiles are required")
        profiles = {
            DEFAULT_PROFILE: RuntimeProfile(DEFAULT_PROFILE, provider, config.model)
        }
    if not profiles:
        raise ValueError("at least one runtime profile is required")
    chosen_default = default_profile or (
        DEFAULT_PROFILE if DEFAULT_PROFILE in profiles else next(iter(profiles))
    )
    if chosen_default not in profiles:
        raise ValueError(f"default profile {chosen_default!r} is not among the profiles")
    from .sandbox import resolve_executor

    executor = resolve_executor(config.deployment_policy, config.sandbox_executor)  # P3.2 D2
    workspaces = WorkspaceManager(config.workspaces_root)
    gateway = WorkspaceToolGateway(
        workspaces,
        test_timeout=config.test_timeout_seconds,
        local_code_execution=config.deployment_policy.local_code_execution,
        executor=executor,
        appworld_execute=config.appworld_execute,
        domain_tools=config.domain_tools,
    )
    pools: dict[str, RuntimePool] = {}
    if provider_admissions is not None and (
        provider_admission is not None or set(provider_admissions) != set(profiles)
    ):
        raise ValueError("per-pool admissions must cover exactly the configured profiles")
    if local_provider_admissions is not None and not set(local_provider_admissions) <= set(profiles):
        raise ValueError("local admissions name an unconfigured profile")
    for profile_id, profile in profiles.items():
        if profile_id != profile.profile_id:
            raise ValueError(f"profile key {profile_id!r} != profile_id {profile.profile_id!r}")
        database = execution_db_for(config, profile_id)
        admission = provider_admission if provider_admissions is None else provider_admissions[profile_id]
        _check_intent_contexts(config, profile, admission)
        _bind_context_identity(database, profile)
        default_out = profile.default_max_output_tokens or config.default_max_output_tokens
        ceiling = profile.max_output_tokens_ceiling or config.max_output_tokens_ceiling
        native = profile.native_plane
        if native is None:
            raise ValueError(f"profile {profile_id!r} has no native plane; every pool runs on it")
        ports = AgentRuntimePorts(
            provider=profile.provider,
            # ARP-EXEC-1.1.1: a native-plane pool runs under the deployment's real
            # authorization port; the ARP factory refuses AllowAll by name.
            authorization=native.authorization,
            database_path=str(database),
            tool_executor=gateway,
            tool_names=(*TOOL_NAMES, *config.domain_tools),
            tool_schemas={**read_tool_schemas(large=profile.context_policy is not None),
                          **{name: tool.schema for name, tool in config.domain_tools.items()}},
            context_policy=profile.context_policy or ContextPolicy(),
            tokenizer=profile.tokenizer,
            model=profile.model,
            owner_id=config.owner_id,
            lease_ttl_seconds=float(config.sdk_lease_ttl_seconds or 30.0),
            policies=replace(ConsumerRuntimePolicies.local_default(), tool_reconciliation=gateway),
            default_max_output_tokens=default_out,
            max_output_tokens_ceiling=max(ceiling, default_out),
            empty_response_retries=config.empty_response_retries,
            max_concurrent_model_calls=min(
                config.max_concurrent_model_calls,
                profile.max_concurrent_model_calls or config.max_concurrent_model_calls,
            ),
            max_concurrent_tool_calls=config.max_concurrency,
            **({"provider_admission": admission} if admission is not None else {}),
            provider_handoff_fence=provider_handoff_fence,
            local_provider_admission=(local_provider_admissions or {}).get(profile_id),
        )
        from simple_harness.agents.arp.runtime import build_arp_runtime

        runtime = build_arp_runtime(ports, native.arp_ports(database), owner_scope=OWNER_SCOPE)
        if native.after_build is not None:
            native.after_build(runtime)
        bridge = AgentBridge(runtime, caller_for=native.caller_for)
        pools[profile_id] = RuntimePool(
            profile=profile,
            runtime=runtime,
            bridge=bridge,
            execution_db=database,
        )
    return AssembledOrchestratorRuntime(
        pools=pools,
        gateway=gateway,
        workspaces=workspaces,
        config=config,
        default_profile=chosen_default,
    )


__all__ = (
    "OWNER_SCOPE",
    "AssembledOrchestratorRuntime",
    "OrchestratorConfig",
    "RuntimePool",
    "assemble_orchestrator_runtime",
    "execution_db_for",
    "resolve_profile_context_policy",
)
