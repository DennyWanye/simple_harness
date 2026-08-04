"""Build the single production Harness ownership graph."""

from collections.abc import Mapping
from typing import Any
from deskpet.agent.run_presenter import build_product_run_presenter
from deskpet.agent.turn_preparer import ProductTurnPreparer
from deskpet.execution.uow_ports import ProductHarnessUnitOfWork
from deskpet.execution.provider_invocations import ProviderInvocationCoordinator
from deskpet.execution.fences import (
    CallableRunExecutionFence,
    UnboundRunExecutionFence,
    UnboundTerminalDeliveryFence,
)
from deskpet.harness.adapters.venues import ProductVenueRunAdapter
from deskpet.harness.bootstrap import HarnessRuntime, build_harness_runtime
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.drivers.react import ReActDriver
from deskpet.harness.drivers.react_loop import AgentLoopCollaborator
from deskpet.harness.drivers.workflow import WorkflowDriver
from deskpet.workflows.harness_delivery import workflow_sink_registrations
from deskpet.harness.kernel import RegisteredDriver
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.harness.adapters.subagent_registry import ProductDelegateFactory
from deskpet.tools.orchestration_controls import register_orchestration_controls


async def build_product_harness_composition(
    *,
    uow: ProductHarnessUnitOfWork,
    profiles: ProfileRegistry,
    workflow_launcher: Any,
    tool_registry: Any,
    loop_factory: Any,
    auto_mode_check: Any | None = None,
    authorization_runtime: Any | None = None,
    capability_refresh_staging: Any | None = None,
    capability_refresh_service: Any | None = None,
    capability_scope_store: Any | None = None,
    brokered_planner: Any | None = None,
    capability_builder_host: Any | None = None,
    provider_snapshot_resolver: Any | None = None,
    capability_hub: Any | None = None,
    capability_platform: Any | None = None,
    companion_turn_authority: Any | None = None,
    admission_authorizer: Any | None = None,
    provider_invocation_coordinator: Any | None = None,
    provider_fence_acquirer: Any | None = None,
    run_execution_fence: Any | None = None,
    terminal_delivery_fence: Any | None = None,
    delivery_handlers: Any = None,
    delivery_bound_readers: Any = None,
    extra_delivery_registrations: Any = (),
    terminal_delivery_contributors: Any = (),
    **runtime_options: Any,
) -> tuple[HarnessRuntime, ProductVenueRunAdapter]:
    if "classifier" in runtime_options:
        raise TypeError(
            "product composition has one fixed agent.general root; "
            "semantic classifiers are not accepted"
        )
    if capability_hub is not None:
        # Reconcile only the exact terminal run leases left in the tiny
        # post-terminal/pre-release crash window before accepting new ingress.
        await uow.initialize()
        await capability_hub.reconcile_terminal_run_leases()

    if run_execution_fence is None:
        bound_owner = getattr(provider_fence_acquirer, "__self__", None)
        if callable(getattr(bound_owner, "acquire", None)):
            run_execution_fence = bound_owner
        elif provider_fence_acquirer is not None:
            run_execution_fence = CallableRunExecutionFence(
                provider_fence_acquirer
            )
        else:
            run_execution_fence = UnboundRunExecutionFence()
    if not callable(getattr(run_execution_fence, "acquire", None)):
        raise TypeError("run_execution_fence must implement acquire(run_id)")
    provider_fence_acquirer = run_execution_fence.acquire
    if terminal_delivery_fence is None:
        terminal_delivery_fence = UnboundTerminalDeliveryFence()
    if provider_invocation_coordinator is None:
        provider_invocation_coordinator = ProviderInvocationCoordinator(
            uow,
            fence_reacquirer=provider_fence_acquirer,
        )

    async def release_terminal_capability_leases(record: Any, _event: Any) -> None:
        if capability_hub is None:
            return
        known_refs: set[str] = set()
        continuation = await uow.load_continuation(record.run_id)
        continuation_payload = (
            dict(continuation.payload) if continuation is not None else {}
        )
        request_payload = continuation_payload.get("request_payload")
        refresh = (
            request_payload.get("capability_refresh")
            if isinstance(request_payload, Mapping)
            else None
        )
        if isinstance(refresh, Mapping):
            snapshot_ref = str(refresh.get("catalog_snapshot_ref") or "")
            if snapshot_ref:
                known_refs.add(snapshot_ref)
        command = await uow.get_child_command_for_run(record.run_id)
        if command is not None:
            lease = command.intent.child_request.get(
                "capability_snapshot_lease"
            )
            if isinstance(lease, Mapping):
                snapshot_ref = str(lease.get("snapshot_ref") or "")
                if snapshot_ref:
                    known_refs.add(snapshot_ref)
        await capability_hub.release_run_leases(
            record.run_id,
            known_snapshot_refs=tuple(sorted(known_refs)),
        )
        if capability_platform is not None:
            await capability_platform.retire_run_catalog_ready(record.run_id)

    register_orchestration_controls(tool_registry, profiles)
    delegate_factory = ProductDelegateFactory(
        profiles,
        tool_registry,
        uow,
        capability_builder_host=capability_builder_host,
        provider_snapshot_resolver=provider_snapshot_resolver,
    )
    drivers = (
        RegisteredDriver("react", ReActDriver(
            AgentLoopCollaborator(
                loop_factory,
                call_factory=tool_registry,
                delegation_factory=delegate_factory,
                provider_invocation_coordinator=provider_invocation_coordinator,
                provider_fence_acquirer=provider_fence_acquirer,
                capability_scope_store=capability_scope_store,
            ),
            uow,
            tool_registry,
            auto_mode_check=auto_mode_check,
            authorization_runtime=authorization_runtime,
            capability_refresh_staging=capability_refresh_staging,
            capability_refresh_service=capability_refresh_service,
            capability_scope_store=capability_scope_store,
            brokered_planner=brokered_planner,
            capability_builder_host=capability_builder_host,
        ), durable_from_start=True),
        RegisteredDriver("workflow", WorkflowDriver(
            workflow_launcher, uow, profiles,
            unit_of_work=uow,
            run_execution_fence_acquirer=provider_fence_acquirer,
        ), durable_from_start=True, atomic_start=True),
    )
    runtime = await build_harness_runtime(
        uow=uow,
        profiles=profiles,
        root_profile_key="agent.general",
        drivers=drivers,
        child_runs=ChildRunCoordinator(
            uow,
            snapshot_leaser=(
                capability_platform
                if capability_platform is not None
                else capability_hub
            ),
        ),
        tool_executor=EffectBatchExecutor(
            uow,
            tool_registry,
            provider_fence_acquirer=provider_fence_acquirer,
            current_execution_scope_port=capability_platform,
        ),
        delivery_registrations=(
            *workflow_sink_registrations(
                delivery_handlers or {},
                bound_readers=delivery_bound_readers or {},
            ),
            *tuple(extra_delivery_registrations or ()),
        ),
        terminal_delivery_contributors=tuple(
            terminal_delivery_contributors or ()
        ),
        terminal_observer=release_terminal_capability_leases,
        continuation_observer=runtime_options.pop(
            "continuation_observer", None
        ),
        admission_authorizer=admission_authorizer,
        run_execution_fence=run_execution_fence,
        terminal_delivery_fence=terminal_delivery_fence,
        **runtime_options,
    )
    return (
        runtime,
        ProductVenueRunAdapter(
            preparer=ProductTurnPreparer(
                profile_registry=profiles,
                companion_turn_authority=companion_turn_authority,
            ),
            run_client=runtime.run_client,
            presenter=build_product_run_presenter(),
        ),
    )
