# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Durable RunKernel lifecycle and fixed-root public client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence

from .kernel_domains.base_agent import BaseAgentStartMixin
from .kernel_domains.driving import RunDrivingMixin
from .kernel_domains.lifecycle import RuntimeLifecycleMixin
from .kernel_domains.pumps import RuntimePumpMixin
from .kernel_domains.spawn import WorkflowSpawnControlMixin
from .kernel_domains.support import (
    BASE_AGENT_DRIVER_EXCEPTION_WAKES,
    ROOT_PROFILE_KEY,
    WORKFLOW_DRIVER_KIND,
    DriverCancellationCoordinator,
    DriverCancellationRecovery,
    DriverCancelOutcome,
    DriverInvocation,
    DriverResult,
    ReactCheckpointPort,
    RunClient,
    RuntimeDriver,
    RuntimeLifecycleState,
    RuntimePorts,
    RuntimeProfile,
    RuntimeReconciliationPort,
    RuntimeServices,
    RuntimeUnitOfWork,
    ToolCatalogGenerationPort,
)
from .kernel_domains.termination import RunTerminationMixin


class Runtime(
    BaseAgentStartMixin,
    RuntimeLifecycleMixin,
    RuntimePumpMixin,
    RunDrivingMixin,
    RunTerminationMixin,
    WorkflowSpawnControlMixin,
):
    """The runtime kernel: owns run lifecycles and drives each run through its driver."""


def build_runtime(
    uow: RuntimeUnitOfWork,
    profiles: Mapping[str, RuntimeProfile],
    drivers: Mapping[str, RuntimeDriver],
    ports: RuntimePorts,
    root_profile_key: str = ROOT_PROFILE_KEY,
    *,
    workflow_runner: object | None = None,
    close_hook: Callable[[], None] | None = None,
    start_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
    async_close_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
) -> Runtime:
    """Build a Runtime with one fixed root Profile and no classifier path."""

    if root_profile_key != ROOT_PROFILE_KEY:
        raise ValueError("root_profile_key is fixed to agent.general")
    bound_profiles = dict(profiles)
    if ROOT_PROFILE_KEY not in bound_profiles:
        raise ValueError("agent.general profile is required")
    profile = bound_profiles[ROOT_PROFILE_KEY]
    if profile.profile_key != ROOT_PROFILE_KEY:
        raise ValueError("agent.general profile binding is inconsistent")
    bound_drivers = dict(drivers)
    if WORKFLOW_DRIVER_KIND in bound_drivers:
        raise ValueError("workflow is an SDK-reserved driver key")
    workflow_profiles = tuple(
        item for item in bound_profiles.values() if item.driver_kind == WORKFLOW_DRIVER_KIND
    )
    if workflow_profiles and workflow_runner is None:
        raise ValueError("workflow profile requires the SDK-owned workflow runner")
    if profile.driver_kind not in bound_drivers and not (
        profile.driver_kind == WORKFLOW_DRIVER_KIND and workflow_runner is not None
    ):
        raise ValueError("agent.general driver is not registered")
    return Runtime(
        uow=uow,
        profiles=bound_profiles,
        drivers=bound_drivers,
        workflow_runner=workflow_runner,
        ports=ports,
        root_profile_key=ROOT_PROFILE_KEY,
        close_hook=close_hook,
        start_hooks=start_hooks,
        async_close_hooks=async_close_hooks,
    )


__all__ = (
    "BASE_AGENT_DRIVER_EXCEPTION_WAKES",
    "ROOT_PROFILE_KEY",
    "WORKFLOW_DRIVER_KIND",
    "DriverCancelOutcome",
    "DriverCancellationCoordinator",
    "DriverCancellationRecovery",
    "DriverInvocation",
    "DriverResult",
    "ReactCheckpointPort",
    "RunClient",
    "Runtime",
    "RuntimeLifecycleState",
    "RuntimeDriver",
    "RuntimePorts",
    "RuntimeProfile",
    "RuntimeReconciliationPort",
    "RuntimeServices",
    "RuntimeUnitOfWork",
    "ToolCatalogGenerationPort",
    "build_runtime",
)
