"""Dormant product composition; R5.5 production must not import this module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from deskpet.agent.run_presenter import RunPresenter, build_legacy_run_presenter
from deskpet.agent.turn_preparer import ProductTurnPreparer
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.adapters.venues import ProductVenueRunAdapter, VenueContextResolver
from deskpet.harness.bootstrap import HarnessRuntime, build_harness_runtime
from deskpet.harness.kernel import RegisteredDriver
from deskpet.harness.profiles import ProfileRegistry
from deskpet.harness.router import RouteClassifier

@dataclass(frozen=True, slots=True)
class ProductHarnessComposition:
    runtime: HarnessRuntime
    venue: ProductVenueRunAdapter

    async def close(self, *, timeout: float = 1.0) -> None:
        await self.runtime.close(timeout=timeout)

async def build_product_harness_composition(
    *,
    uow: ExecutionUnitOfWork,
    classifier: RouteClassifier,
    profiles: ProfileRegistry,
    drivers: Sequence[RegisteredDriver],
    resolver: VenueContextResolver,
    preparer: ProductTurnPreparer | None = None,
    presenter: RunPresenter | None = None,
    **runtime_options: Any,
) -> ProductHarnessComposition:
    """Build isolated wiring without activation, registration, or fallback."""
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=classifier,
        profiles=profiles,
        drivers=drivers,
        resolver=resolver,
        **runtime_options,
    )
    venue = ProductVenueRunAdapter(
        preparer=preparer or ProductTurnPreparer(),
        run_client=runtime.run_client,
        presenter=presenter or build_legacy_run_presenter(),
    )
    return ProductHarnessComposition(runtime, venue)
