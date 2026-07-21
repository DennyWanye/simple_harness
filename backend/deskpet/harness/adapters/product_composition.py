"""Dormant product composition; R5.5 production must not import this module."""

from collections.abc import Sequence
from typing import Any
from deskpet.agent.run_presenter import RunPresenter, build_legacy_run_presenter
from deskpet.agent.turn_preparer import ProductTurnPreparer
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.adapters.venues import ProductVenueRunAdapter, ProviderLaunchPolicyRegistry, VenueContextResolver
from deskpet.harness.bootstrap import HarnessRuntime, build_harness_runtime
from deskpet.harness.kernel import RegisteredDriver
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.router import RouteClassifier

async def build_product_harness_composition(
    *,
    uow: ExecutionUnitOfWork,
    classifier: RouteClassifier,
    profiles: ProfileRegistry,
    drivers: Sequence[RegisteredDriver],
    resolver: VenueContextResolver,
    preparer: ProductTurnPreparer | None = None,
    presenter: RunPresenter | None = None,
    provider_launch_policies: ProviderLaunchPolicyRegistry | None = None,
    **runtime_options: Any,
) -> tuple[HarnessRuntime, ProductVenueRunAdapter]:
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=classifier,
        profiles=profiles,
        drivers=drivers,
        resolver=resolver,
        **runtime_options,
    )
    return (
        runtime,
        ProductVenueRunAdapter(
            preparer=preparer or ProductTurnPreparer(),
            run_client=runtime.run_client,
            presenter=presenter or build_legacy_run_presenter(),
            provider_launch_policies=provider_launch_policies,
        ),
    )
