"""Fail-closed SDK reconciliation adapters over product evidence."""

from __future__ import annotations

import inspect

from simple_harness.providers import (
    ProviderReconciliationObservation,
    ProviderReconciliationState,
)
from simple_harness.tools import ReconciliationObservation, ReconciliationState

from deskpet.product_state.authorization_saga import (
    AuthorizationSagaRepository,
    AuthorizationSagaState,
)


class ProductReconciliationAdapter:
    """Tool reconciliation: only pre-handoff product states prove not-started."""

    def __init__(self, repository: AuthorizationSagaRepository, *, observer=None):
        self._repository = repository
        self._observer = observer

    async def observe(self, effect):
        saga = self._repository.read_for_effect(effect.effect_id.value)
        if saga is not None and saga.state in {
            AuthorizationSagaState.PREPARED,
            AuthorizationSagaState.DECISION_BOUND,
            AuthorizationSagaState.EFFECT_BOUND,
            AuthorizationSagaState.ABORTED,
            AuthorizationSagaState.EXPIRED,
            AuthorizationSagaState.REVOKED,
        }:
            return ReconciliationObservation(
                ReconciliationState.CONFIRMED_NOT_STARTED,
                f"product-saga:{saga.identity.authorization_id}:{saga.version}",
            )
        if self._observer is not None:
            result = self._observer(effect, saga)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, ReconciliationObservation):
                raise TypeError("Tool observer returned an invalid observation")
            return result
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN,
            f"product-saga:unknown:{effect.effect_id.value}",
        )


class ProductProviderReconciliationAdapter:
    def __init__(self, observer=None) -> None:
        self._observer = observer

    async def observe(self, invocation):
        if self._observer is not None:
            result = self._observer(invocation)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, ProviderReconciliationObservation):
                raise TypeError("Provider observer returned an invalid observation")
            return result
        return ProviderReconciliationObservation(
            ProviderReconciliationState.STILL_UNKNOWN,
            f"product-provider:unknown:{invocation.invocation_id}",
        )


class ProductStartupReconciliationAdapter:
    def __init__(self, *steps) -> None:
        self._steps = steps

    async def reconcile(self) -> None:
        for step in self._steps:
            result = step()
            if inspect.isawaitable(result):
                await result


__all__ = (
    "ProductProviderReconciliationAdapter",
    "ProductReconciliationAdapter",
    "ProductStartupReconciliationAdapter",
)
