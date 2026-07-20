"""Single composition root for the product-neutral agent harness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deskpet.execution.ledger import ExecutionLedger
from deskpet.harness.adapters.venues import KernelRunClient, VenueContextResolver
from deskpet.harness.child_runs import ChildLauncher, ChildRunCoordinator
from deskpet.harness.decisions import DecisionStore
from deskpet.harness.kernel import RegisteredDriver, RunKernel, kernel_public_operations
from deskpet.harness.router import RegisteredRouter, RouteClassifier, RouteProfile
from deskpet.harness.recovery import HarnessRecoveryCoordinator
from deskpet.harness.tool_executor import UnifiedToolExecutor
from deskpet.workflows.store.schema import WORKFLOW_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class HarnessManifest:
    schema_version: int
    operations: tuple[str, ...]
    drivers: tuple[str, ...]
    profiles: tuple[tuple[str, str], ...]
    event_contract: str = "execution.run-event.v1"
    tool_stage: str = "prepared-call.v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "operations": list(self.operations),
            "drivers": list(self.drivers),
            "profiles": [
                {"key": key, "driver_kind": driver_kind}
                for key, driver_kind in self.profiles
            ],
            "event_contract": self.event_contract,
            "tool_stage": self.tool_stage,
        }


@dataclass(frozen=True, slots=True)
class HarnessHealth:
    status: str
    active_owner: str
    ledger_schema_version: int
    drivers: Mapping[str, str]
    compatibility_reader: str
    degraded_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "active_owner": self.active_owner,
            "ledger_schema_version": self.ledger_schema_version,
            "drivers": dict(self.drivers),
            "compatibility_reader": self.compatibility_reader,
            "degraded_reasons": list(self.degraded_reasons),
        }


@dataclass(frozen=True, slots=True)
class HarnessRuntime:
    kernel: RunKernel
    run_client: KernelRunClient
    manifest: HarnessManifest
    health: HarnessHealth
    recovery: HarnessRecoveryCoordinator


async def build_harness_runtime(
    *,
    ledger: ExecutionLedger,
    classifier: RouteClassifier,
    profiles: Sequence[RouteProfile],
    drivers: Sequence[RegisteredDriver],
    resolver: VenueContextResolver,
    decision_store: DecisionStore | None = None,
    child_runs: ChildRunCoordinator | None = None,
    child_launcher: ChildLauncher | None = None,
    tool_executor: UnifiedToolExecutor | None = None,
    compatibility_reader: bool = True,
) -> HarnessRuntime:
    """Initialize one Kernel or fail startup without a legacy fallback."""

    await ledger.initialize()
    driver_catalog = {registration.kind: registration for registration in drivers}
    if len(driver_catalog) != len(drivers):
        raise ValueError("duplicate harness driver registration")
    if not profiles:
        raise ValueError("at least one harness route profile is required")
    missing = sorted({profile.driver_kind for profile in profiles} - set(driver_catalog))
    if missing:
        raise ValueError(f"profiles reference unavailable drivers: {','.join(missing)}")

    router = RegisteredRouter(classifier, profiles)
    kernel = RunKernel(
        ledger=ledger,
        router=router,
        drivers=drivers,
        decision_store=decision_store,
        child_runs=child_runs,
        child_launcher=child_launcher,
        tool_executor=tool_executor,
    )
    degraded_reasons = tuple(
        reason
        for available, reason in (
            (tool_executor is not None, "tool_executor_unavailable"),
            (decision_store is not None, "decision_store_unavailable"),
            (
                child_runs is not None and child_launcher is not None,
                "child_run_coordinator_unavailable",
            ),
        )
        if not available
    )
    manifest = HarnessManifest(
        schema_version=WORKFLOW_SCHEMA_VERSION,
        operations=kernel_public_operations(),
        drivers=tuple(sorted(driver_catalog)),
        profiles=tuple(
            sorted((profile.key, profile.driver_kind) for profile in router.profiles.values())
        ),
    )
    health = HarnessHealth(
        status="ready" if not degraded_reasons else "degraded",
        active_owner="execution_ledger",
        ledger_schema_version=WORKFLOW_SCHEMA_VERSION,
        drivers={kind: "ready" for kind in manifest.drivers},
        compatibility_reader="enabled" if compatibility_reader else "disabled",
        degraded_reasons=degraded_reasons,
    )
    return HarnessRuntime(
        kernel=kernel,
        run_client=KernelRunClient(kernel, resolver),
        manifest=manifest,
        health=health,
        recovery=HarnessRecoveryCoordinator(ledger, kernel),
    )


__all__ = [
    "HarnessHealth",
    "HarnessManifest",
    "HarnessRuntime",
    "build_harness_runtime",
]
