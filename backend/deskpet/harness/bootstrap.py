"""Single composition root for the product-neutral agent harness."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.adapters.venues import KernelRunClient, VenueContextResolver
from deskpet.harness.child_runs import ChildRunCoordinator, ChildRunScheduler
from deskpet.harness.contracts import driver_catalog
from deskpet.harness.kernel import KernelChildLauncher, RegisteredDriver, RunKernel, kernel_public_operations
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.projector import (
    ExecutionDeliveryDispatcher, GoalTerminalProjection, SinkRegistration,
)
from deskpet.harness.router import RegisteredRouter, RouteClassifier
from deskpet.harness.recovery import HarnessRecoveryCoordinator
from deskpet.harness.tool_executor import UnifiedToolExecutor
from deskpet.workflows.store.schema import WORKFLOW_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class HarnessDiagnostics:
    """One serializable DTO for the runtime's manifest and health snapshots."""

    values: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return deepcopy(dict(self.values))


@dataclass(frozen=True, slots=True)
class HarnessRuntime:
    kernel: RunKernel
    run_client: KernelRunClient
    manifest: HarnessDiagnostics
    health: HarnessDiagnostics
    recovery: HarnessRecoveryCoordinator
    child_scheduler: ChildRunScheduler | None
    drivers: tuple[RegisteredDriver, ...]
    tool_executor: UnifiedToolExecutor | None
    delivery_dispatcher: ExecutionDeliveryDispatcher | None = None

    async def close(self, *, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        if self.child_scheduler is not None:
            await self.child_scheduler.close()
        await self.recovery.close()
        try:
            if (effects := self.tool_executor) is not None:
                await effects.close(max(0.0, timeout) / 2)
                if (run_ids := effects.ready_late_run_ids()):
                    await self.recovery.reconcile_ready(
                        run_ids, timeout=max(0.0, deadline - loop.time())
                    )
            await self.kernel._drain_active(max(0.0, deadline - loop.time()))
        finally:
            await asyncio.gather(*(item.driver.close() for item in self.drivers))


async def build_harness_runtime(
    *,
    uow: ExecutionUnitOfWork,
    classifier: RouteClassifier,
    profiles: ProfileRegistry,
    drivers: Sequence[RegisteredDriver],
    resolver: VenueContextResolver,
    child_runs: ChildRunCoordinator | None = None,
    tool_executor: UnifiedToolExecutor | None = None,
    goal_store: object | None = None,
    owner_generation: int | None = None,
    compatibility_reader: bool = True,
) -> HarnessRuntime:
    """Initialize one Kernel or fail startup without a legacy fallback."""

    await uow.initialize()
    registered_drivers = driver_catalog(tuple(drivers))
    missing = sorted(
        {profile.driver_kind for profile in profiles.specs.values()} - set(registered_drivers)
    )
    if missing:
        raise ValueError(f"profiles reference unavailable drivers: {','.join(missing)}")

    workflow_registration = registered_drivers.get("workflow")
    if workflow_registration is not None:
        actual = frozenset(getattr(workflow_registration.driver, "profile_keys", ()))
        expected = frozenset(profiles.workflow_specs)
        if actual != expected:
            raise ValueError("router workflow keys and WorkflowDriver catalog differ")
    router = RegisteredRouter(classifier, profiles)
    goal_projection = GoalTerminalProjection(goal_store) if goal_store is not None else None
    kernel = RunKernel(
        uow=uow,
        router=router,
        drivers=registered_drivers,
        child_runs=child_runs,
        tool_executor=tool_executor,
        terminal_projection=goal_projection,
    )
    if goal_projection is not None and owner_generation is None:
        raise ValueError("Goal projection requires the activated owner generation")
    delivery_dispatcher = None if goal_projection is None else ExecutionDeliveryDispatcher(
        uow,
        (SinkRegistration("goal_projection", "session-goals", goal_projection),),
        owner_generation=owner_generation or 0,
    )
    child_scheduler = (
        ChildRunScheduler(child_runs, KernelChildLauncher(kernel), owner="harness-child")
        if child_runs is not None else None
    )
    degraded_reasons = tuple(
        reason
        for available, reason in (
            (tool_executor is not None, "tool_executor_unavailable"),
            (
                child_scheduler is not None,
                "child_run_coordinator_unavailable",
            ),
        )
        if not available
    )
    driver_kinds = sorted(registered_drivers)
    manifest = HarnessDiagnostics({
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "operations": list(kernel_public_operations()),
        "drivers": driver_kinds,
        "profiles": [
            {"key": profile.profile_key, "driver_kind": profile.driver_kind}
            for profile in sorted(router.profiles.values(), key=lambda item: item.profile_key)
        ],
        "event_contract": "execution.run-event.v1",
        "tool_stage": "prepared-call.v1",
    })
    health = HarnessDiagnostics({
        "status": "ready" if not degraded_reasons else "degraded",
        "active_owner": "sqlite_execution_uow",
        "ledger_schema_version": WORKFLOW_SCHEMA_VERSION,
        "drivers": {kind: "ready" for kind in driver_kinds},
        "compatibility_reader": "enabled" if compatibility_reader else "disabled",
        "degraded_reasons": list(degraded_reasons),
    })
    recovery = HarnessRecoveryCoordinator(uow, kernel, tool_executor)
    runtime = HarnessRuntime(
        kernel=kernel,
        run_client=KernelRunClient(kernel, resolver),
        manifest=manifest,
        health=health,
        recovery=recovery,
        child_scheduler=child_scheduler,
        drivers=tuple(drivers),
        tool_executor=tool_executor,
        delivery_dispatcher=delivery_dispatcher,
    )
    if child_scheduler is not None:
        await child_scheduler.reconcile_commands_once()
    await recovery.recover_pending()
    if child_scheduler is not None:
        await child_scheduler.reconcile_signals_once()
        await child_scheduler.start()
    await recovery.start()
    return runtime
