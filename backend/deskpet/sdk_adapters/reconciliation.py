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


class ProviderUnknownRetryOncePolicy:
    """F06（2026-09-07）：provider 传输超时后的 UNKNOWN 调用给出可判定结论。

    OpenAI 兼容 chat completion 没有请求级查询 API，Host 无法证明"那次请求
    已完成"，所以 ``COMPLETED`` 不可达；可行的结论只有：

    * 同一 request 首次未知（``rehandoff_count == 0``）→ ``CONFIRMED_NOT_STARTED``，
      授权 SDK 用同一 ``request_id`` 重新 hand off 一次（``handoff_attempt`` 1→2）；
    * 再次未知（``rehandoff_count >= 1``，SDK 只允许一次 rehandoff）→ 保持
      ``STILL_UNKNOWN`` 并把 run 记入 ``exhausted_runs``，由前台运行时 cancel 收尾，
      绝不让 Run 静默停在 RUNNING/waiting。
    """

    def __init__(self) -> None:
        self.exhausted_runs: set[str] = set()

    @staticmethod
    def _run_id(invocation) -> str:
        run_id = getattr(invocation, "run_id", None)
        return str(getattr(run_id, "value", run_id))

    def __call__(self, invocation) -> ProviderReconciliationObservation:
        invocation_id = getattr(invocation, "invocation_id", "")
        attempt = int(getattr(invocation, "handoff_attempt", 0) or 0)
        if int(getattr(invocation, "rehandoff_count", 0) or 0) == 0:
            return ProviderReconciliationObservation(
                ProviderReconciliationState.CONFIRMED_NOT_STARTED,
                f"product-policy:provider-retry-once:{invocation_id}:a{attempt}",
            )
        self.exhausted_runs.add(self._run_id(invocation))
        return ProviderReconciliationObservation(
            ProviderReconciliationState.STILL_UNKNOWN,
            f"product-policy:provider-retry-exhausted:{invocation_id}:a{attempt}",
        )


class ProductProviderRetryOnceReconciliation(ProductProviderReconciliationAdapter):
    """``ProviderReconciliationPort``：retry-once 策略承载在既有适配器上。"""

    def __init__(self, policy: ProviderUnknownRetryOncePolicy | None = None) -> None:
        self.policy = policy if policy is not None else ProviderUnknownRetryOncePolicy()
        super().__init__(observer=self.policy)

    @property
    def exhausted_runs(self) -> set[str]:
        return self.policy.exhausted_runs


def waiting_runs_blocked_on_provider(uow, *, exclude=frozenset()):
    """F06：返回被 UNKNOWN/HANDED_OFF provider 调用挂住、状态为 waiting 的 Run 记录。

    SDK 的 ``list_recoverable_root_runs`` 不含 waiting；这类 Run 会在启动 reconcile 后
    立刻重驱，Host 必须先恢复其历史工具授权（否则 ``sdk_runtime_tool_exposure_unavailable``）。
    """
    found: dict[str, object] = {}
    for invocation in uow.list_incomplete_provider_invocations():
        run_id = str(getattr(invocation.run_id, "value", invocation.run_id))
        if run_id in exclude or run_id in found:
            continue
        run = uow.read_run(run_id)
        state = str(getattr(getattr(run, "state", None), "value", "") or "").lower()
        if run is not None and state == "waiting":
            found[run_id] = run
    return tuple(found.values())


class ProductRuntimeReconciliation:
    """``RuntimeReconciliationPort``：SDK 内核启动 ``_start_once`` 与运行时
    ``runtime.reconcile()`` 都会调用这里；Host 在此把 UNKNOWN 的 provider 调用交给
    ``ProviderInvocationCoordinator.reconcile_incomplete``（SDK 自身不会调用它）。

    ``ports_getter`` 惰性取生产 ``RuntimePorts``（构造 config 时 ports 还没建）。
    """

    def __init__(self, ports_getter, provider_reconciliation) -> None:
        self._ports_getter = ports_getter
        self._provider_reconciliation = provider_reconciliation
        self.settled_total = 0

    async def reconcile(self) -> int:
        ports = self._ports_getter()
        if ports is None:
            return 0
        reconcile = getattr(getattr(ports, "provider", None), "reconcile_incomplete", None)
        if not callable(reconcile):
            raise RuntimeError("production provider coordinator lacks reconcile_incomplete")
        settled = await reconcile(provider_reconciliation=self._provider_reconciliation)
        self.settled_total += int(settled or 0)
        return int(settled or 0)


__all__ = (
    "ProductProviderReconciliationAdapter",
    "ProductProviderRetryOnceReconciliation",
    "ProductReconciliationAdapter",
    "ProductRuntimeReconciliation",
    "ProductStartupReconciliationAdapter",
    "ProviderUnknownRetryOncePolicy",
    "waiting_runs_blocked_on_provider",
)
