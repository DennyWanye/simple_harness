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
from typing import TYPE_CHECKING, Any, cast

from ..assurance.codec import AssuranceError
from ..assurance.root_gate import AssuranceRootGate
from ..context.knowledge_tools import read_knowledge_tool
from ..runtime.actions import ActionExecutor
from ..runtime.assembly import assemble_orchestrator_runtime
from ..runtime.legacy_provider_slots import LegacyProviderSlots, create_slot_table
from ..runtime.provider_budget_guard import ProviderBudgetGuard
from ..storage.recovery_store import StoreSandboxLedger
from ..storage.store import InjectedCrash, Store
from .accounting_recovery import import_late_accounting
from .commit_service import CommitService
from .recovery_coordinator import RecoveryCoordinator

if TYPE_CHECKING:
    from ..runtime.provider_budget_guard import ProviderBudgetGuard
from .orchestrator_domains.attempt_collection import AttemptCollectionMixin
from .orchestrator_domains.attempt_dispatch import AttemptDispatchMixin
from .orchestrator_domains.attempt_judgment import AttemptJudgmentMixin
from .orchestrator_domains.cycle import CycleMixin
from .orchestrator_domains.deployment import DeploymentMixin
from .orchestrator_domains.idle_and_stall import IdleStallMixin
from .orchestrator_domains.mission_lifecycle import (
    ACTION_SETTLED_AFTER_STOP,
    FAULT_POINTS,
    MissionLifecycleMixin,
)
from .orchestrator_domains.planning_decision_branches import PlanningDecisionBranchMixin
from .orchestrator_domains.planning_decisions import PlanningDecisionMixin
from .orchestrator_domains.planning_feedback import PlanningFeedbackMixin
from .orchestrator_domains.planning_rounds import PlanningRoundMixin
from .orchestrator_domains.planning_waits import PlanningWaitMixin
from .orchestrator_domains.policy_routing import PolicyRoutingMixin
from .orchestrator_domains.resource_limits import ResourceLimitMixin
from .orchestrator_domains.root_review import RootReviewMixin
from .orchestrator_domains.service_turns import ServiceTurnMixin
from .orchestrator_domains.support import logger
from .orchestrator_domains.verification import VerificationMixin

HOLLOW_CYCLES_NOTED = 100
WAIT_BACKOFF_MAX = 1.0
MAX_CRITIC_ATTEMPTS = 2
SYSTEM_CRITIC_MODEL_CALLS = 12


RECONCILE_EVERY_CYCLES = 50  # D7-5': UNKNOWN actions are asked about again while a run goes on


class Orchestrator(
    PolicyRoutingMixin,
    DeploymentMixin,
    MissionLifecycleMixin,
    IdleStallMixin,
    PlanningWaitMixin,
    CycleMixin,
    PlanningRoundMixin,
    AttemptDispatchMixin,
    AttemptCollectionMixin,
    PlanningFeedbackMixin,
    PlanningDecisionMixin,
    PlanningDecisionBranchMixin,
    VerificationMixin,
    ServiceTurnMixin,
    ResourceLimitMixin,
    RootReviewMixin,
    AttemptJudgmentMixin,
):
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

            # §14.4：模型写的代码起的子进程，身份与回收材料落库；重启后按它回收。
            if self._executor is not None:
                cast(Any, self._executor).ledger = StoreSandboxLedger(self._store)
            if self._wiring.assurance_root_setup is not None or AssuranceRootGate.required(
                self._store, self._config.evidence_root
            ):
                self._wiring.assurance_root_gate = AssuranceRootGate(
                    self._store, self._config.evidence_root
                )
                self._commit._assurance_root_gate = self._wiring.assurance_root_gate
                cast(Any, self._store)._assurance_root_gate = self._wiring.assurance_root_gate
                #: 根进隔离时的匿名阻塞码（原计划 §10.3 允许披露的一项）；NATIVE 时为 None
                self._assurance_quarantine_code: str | None = None
                try:
                    # This callback has only Store/Commit, never live runtime pools.
                    # The Host may bind its CURRENT authenticated management authority.
                    if self._wiring.assurance_root_setup is not None:
                        self._wiring.assurance_root_setup(self)
                    self._wiring.assurance_root_gate.require_execution()
                except AssuranceError as error:
                    # 原计划 §10.1：标记缺失 / 不符、安装身份冲突 → 根进隔离：服务起来，
                    # 只开非披露诊断与隔离只读分支；不装配、不派发、不自动写回状态文件。
                    # Keep the authenticated management API available. No SDK
                    # pools, recovery, workspace cleanup, Context or dispatch runs.
                    self._assurance_quarantine_code = error.code
                    self._wiring.assurance_management_only = True
                    return self
            if self._provider_token_estimator is not None:
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
                    # 外层已判 _provider_token_estimators 不为空，闭包里 mypy 不沿用收窄
                    estimator = cast(Any, self._provider_token_estimators)[key]
                    if estimator is None:
                        return None
                    candidates = (
                        tuple(estimator) if isinstance(estimator, (tuple, list)) else (estimator,)
                    )
                    guards = [guard_for(key, candidate) for candidate in candidates]
                    if len(guards) == 1:
                        return guards[0]
                    frozen = self._frozen_admission_fingerprints(key)
                    return next(
                        (guard for guard in guards if any(guard.accepts(f) for f in frozen)),
                        guards[0],
                    )

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
            # receipts first so an actual overrun is recorded with its real numbers
            # (OVERRUN is a recorded fact, never raised); no runtime is started by this
            # accounting-only pass. Tool counts also come from durable rows.
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
            self._assembled.gateway.on_external_tool = self._audit_external_tool

            self._assembled.gateway.knowledge_reader = lambda mission_id, tool, args, reader: (
                read_knowledge_tool(
                    self.store,
                    mission_id,
                    tool,
                    args,
                    handover=self._knowledge_handover(mission_id, reader),
                )
            )
            self._assembled.gateway.executed_counter = self.store.count_tool_calls
            self._assembled.gateway.search_counter = self.store.count_search_calls
            self._assembled.gateway.execution_refusal = self._tool_execution_refusal
            self._bind_startup_tools()
            await self._assembled.__aenter__()
            # execution copies a crash left behind are removed
            workspaces = self._assembled.workspaces
            self.cleanup_workspaces()  # P3.2 D4: finished Missions past their retention
            self._bridge = self._assembled.pool(self._default_profile).bridge
            self._pressure = self._commit.backpressure_state()
            self._backlog = self._backlog_response(self._pressure)
            return self
        except BaseException as error:
            # Enter failures do not trigger async-with's exit. Preserve the startup
            # error (including cleanup reports) even if resource shutdown also fails.
            try:
                await self.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:
                logger.debug("aenter_aexit_failed", exc_info=True)
                error.add_note(f"Startup resource cleanup failed: {type(cleanup_error).__name__}")
            raise

    async def __aexit__(self, *exc_info: object) -> None:
        for task in list(self._attempts.verifying.values()):
            if not task.done():
                task.cancel()
        for task in list(self._attempts.verifying.values()):
            with contextlib.suppress(BaseException):
                await task
        self._attempts.verifying.clear()
        try:
            if self._assembled is not None:
                await self._assembled.__aexit__(*exc_info)
        finally:
            self._assembled = None
            store, self._store = self._store, None
            if store is not None:
                store.close()

    async def recover(self) -> None:
        """重启恢复协议（原计划 §25.1 第 11 条、§16.4；车道 J H01）：RECOVERY_LOCKED → 清单核对 →
        reducer 重建 → inbox/outbox → 未决核对 → fence 收敛 → 孤儿回收 → READY / DEGRADED_RECOVERY。
        顺序与每步的事实在 :mod:`.recovery_coordinator`；这里只建协调者、跑一遍。READY 之前不派发、
        不交接、不发通知、不唤醒执行池；降级后本进程只开只读与诊断（``run()`` 不进周期）。"""

        self._require_assurance_execution_root()
        if self._recovery is None:
            self._recovery = RecoveryCoordinator(self)
        await self._recovery.run()

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
        self._planning.stall_carry_ons.clear()
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
                        self._note(
                            f"{hollow} cycles in a row claimed progress with no durable change"
                        )
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
                # "空闲"只能由一次全量处理得出：清掉安静标记，
                # 下一轮每个任务都看一遍，仍无进展才算空闲返回。
                self._rounds.mission_marks.clear()
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


__all__ = (
    "FAULT_POINTS",
    "InjectedCrash",
    "Orchestrator",
    "ACTION_SETTLED_AFTER_STOP",
)
