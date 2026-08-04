"""Single composition root for the product-neutral agent harness."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deskpet.execution.uow_ports import HarnessBootstrapUnitOfWork
from deskpet.execution.fences import (
    RunExecutionFencePort,
    TerminalDeliveryFencePort,
)
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.contracts import TerminalDeliveryContributor, driver_catalog
from deskpet.harness.kernel import RegisteredDriver, RunKernel, kernel_public_operations
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.projector import (
    ExecutionDeliveryDispatcher, GoalTerminalProjection, SinkRegistration,
)
from deskpet.harness.router import RegisteredRouter, RouteClassifier
from deskpet.harness.reconciler import HarnessReconciler
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.workflows.store.schema import WORKFLOW_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class HarnessRuntime:
    kernel: RunKernel
    run_client: KernelRunClient
    manifest: Mapping[str, object]
    health: Mapping[str, object]
    reconciler: HarnessReconciler
    drivers: tuple[RegisteredDriver, ...]
    tool_executor: EffectBatchExecutor | None

    async def close(self, *, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        await self.reconciler.close(max(0.0, timeout) / 4)
        try:
            if (effects := self.tool_executor) is not None:
                if (run_ids := await effects.drain(max(0.0, timeout) / 2)):
                    await self.reconciler.reconcile_ready(
                        run_ids, timeout=max(0.0, deadline - loop.time())
                    )
            await self.kernel._drain_active(max(0.0, deadline - loop.time()))
        finally:
            try:
                await asyncio.gather(*(item.driver.close() for item in self.drivers))
            finally:
                async with self.kernel._lock:
                    self.kernel._live.finish_all()


async def build_harness_runtime(
    *,
    uow: HarnessBootstrapUnitOfWork,
    classifier: RouteClassifier | None = None,
    profiles: ProfileRegistry,
    drivers: Sequence[RegisteredDriver],
    root_profile_key: str | None = None,
    child_runs: ChildRunCoordinator | None = None,
    tool_executor: EffectBatchExecutor | None = None,
    goal_store: object | None = None,
    owner_generation: int | None = None,
    delivery_registrations: Sequence[SinkRegistration] = (),
    terminal_delivery_contributors: Sequence[
        TerminalDeliveryContributor
    ] = (),
    terminal_observer: object | None = None,
    continuation_observer: object | None = None,
    admission_authorizer: object | None = None,
    run_execution_fence: RunExecutionFencePort | None = None,
    terminal_delivery_fence: TerminalDeliveryFencePort | None = None,
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
    if root_profile_key is not None:
        root_profile = profiles.resolve(root_profile_key)
        if root_profile.driver_kind not in registered_drivers:
            raise ValueError(
                f"root profile references unavailable driver: {root_profile.driver_kind}"
            )
        if root_profile.launch_policy == "legacy_recovery":
            raise ValueError("root profile cannot be legacy recovery only")
    if classifier is None and root_profile_key is None:
        raise ValueError("classifier is required when no fixed root profile is configured")
    router = None if classifier is None else RegisteredRouter(classifier, profiles)
    goal_projection = GoalTerminalProjection(goal_store) if goal_store is not None else None
    contributors = tuple(terminal_delivery_contributors) + (
        () if goal_projection is None else (goal_projection,)
    )
    reconciler_ref: dict[str, HarnessReconciler] = {}
    delivery_wakeup_ref: dict[str, object] = {}

    async def observe_terminal(record, event) -> None:
        if terminal_observer is not None:
            value = terminal_observer(record, event)
            if inspect.isawaitable(value):
                await value
        if reconciler := reconciler_ref.get("current"):
            reconciler.trigger()

    kernel = RunKernel(
        uow=uow,
        router=router,
        drivers=registered_drivers,
        profiles=profiles,
        root_profile_key=root_profile_key,
        child_runs=child_runs,
        tool_executor=tool_executor,
        terminal_projection=goal_projection,
        terminal_delivery_contributors=contributors,
        terminal_observer=observe_terminal,
        continuation_observer=continuation_observer,
        admission_authorizer=admission_authorizer,
        run_execution_fence=run_execution_fence,
    )
    registrations = tuple(delivery_registrations) + (
        () if goal_projection is None else (
            SinkRegistration("goal_projection", "session-goals", goal_projection),
        )
    )
    delivery_dispatcher = None if not registrations else ExecutionDeliveryDispatcher(
        uow,
        registrations,
        owner_generation=owner_generation or 0,
        terminal_delivery_fence=terminal_delivery_fence,
        retry_wakeup=lambda delay: (
            callback(delay)
            if callable(callback := delivery_wakeup_ref.get("current"))
            else None
        ),
    )
    degraded_reasons = tuple(
        reason
        for available, reason in (
            (tool_executor is not None, "tool_executor_unavailable"),
            (
                child_runs is not None,
                "child_run_coordinator_unavailable",
            ),
        )
        if not available
    )
    driver_kinds = sorted(registered_drivers)
    manifest = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "operations": list(kernel_public_operations()),
        "drivers": driver_kinds,
        "profiles": [
            {"key": profile.profile_key, "driver_kind": profile.driver_kind}
            for profile in sorted(profiles.specs.values(), key=lambda item: item.profile_key)
        ],
        "event_contract": "execution.run-event.v1",
        "tool_stage": "prepared-call.v1",
    }
    health = {
        "status": "ready" if not degraded_reasons else "degraded",
        "active_owner": "sqlite_execution_uow",
        "ledger_schema_version": WORKFLOW_SCHEMA_VERSION,
        "drivers": {kind: "ready" for kind in driver_kinds},
        "degraded_reasons": list(degraded_reasons),
    }
    reconciler = HarnessReconciler(
        uow, kernel, tool_executor,
        coordinator=child_runs,
        delivery=delivery_dispatcher,
        owner="harness-child",
    )
    reconciler_ref["current"] = reconciler
    delivery_wakeup_ref["current"] = reconciler._schedule_after
    if child_runs is not None:
        child_runs.bind_reconcile_trigger(reconciler.trigger)
    if tool_executor is not None:
        tool_executor._bind_ready_callback(
            lambda _run_id: reconciler.trigger()
        )
    runtime = HarnessRuntime(
        kernel=kernel,
        run_client=KernelRunClient(kernel),
        manifest=manifest,
        health=health,
        reconciler=reconciler,
        drivers=tuple(drivers),
        tool_executor=tool_executor,
    )
    try:
        await reconciler._reconcile_startup()
    except BaseException:
        try:
            await runtime.close()
        except BaseException:
            # Preserve the bootstrap failure that kept ingress closed.
            pass
        raise
    return runtime
