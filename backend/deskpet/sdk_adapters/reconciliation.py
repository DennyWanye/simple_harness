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


def _invocation_run_id(invocation) -> str:
    run_id = getattr(invocation, "run_id", None)
    return str(getattr(run_id, "value", run_id))


class RunScopedProviderReconciliation:
    """F06 修复（2026-09-08）：把 ``ProviderReconciliationPort`` 限定在目标 Run。

    ``ProviderInvocationCoordinator.reconcile_incomplete``（SDK ``execution/dispatch.py``）
    是**全库扫描**，端口会看到其它 Run 的 invocation。SDK 契约里表示"不裁决 / 保持现状"
    的结论只有 ``STILL_UNKNOWN``（``reconcile_incomplete`` 对它 ``continue``，不写决议、
    不动账本），所以对非目标 Run 一律返回 ``STILL_UNKNOWN``；绝不能返回
    ``CONFIRMED_NOT_STARTED`` —— 那会授权 SDK 用同一 ``request_id`` 重发**别人的**请求，
    造成重复物理发送与重复计费。

    SDK 端口没有 per-run 作用域参数（0.7.10），而 ``reconcile_incomplete`` 允许按调用
    传入端口，所以作用域做成"每次调用新建一个实例"，并发 reconcile 之间互不共享。
    """

    def __init__(self, inner, target_run_id) -> None:
        self._inner = inner
        self._target_run_id = str(getattr(target_run_id, "value", target_run_id))

    @property
    def target_run_id(self) -> str:
        return self._target_run_id

    async def observe(self, invocation):
        if _invocation_run_id(invocation) != self._target_run_id:
            return ProviderReconciliationObservation(
                ProviderReconciliationState.STILL_UNKNOWN,
                f"product-scope:other-run:{getattr(invocation, 'invocation_id', '')}",
            )
        observe = getattr(self._inner, "observe", None)
        result = observe(invocation) if callable(observe) else self._inner(invocation)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ProviderReconciliationObservation):
            raise TypeError("Provider observer returned an invalid observation")
        return result


def provider_invocations_in_flight(uow) -> tuple[str, ...]:
    """仍在飞行（``handed_off``）的 provider 调用 —— 物理请求此刻正在发出、还没 settle。

    ``reconcile_incomplete`` 对 ``HANDED_OFF`` 记录**无条件**先 ``_settle_unknown``
    （``dispatch.py`` 的 ``for record in list_incomplete_provider_invocations()`` 循环，
    发生在 ``provider_reconciliation.observe`` 之前），端口再怎么"不裁决"也拦不住；
    因此运行期必须在调用**之前**用这个闸门判断。
    """
    inflight: list[str] = []
    for invocation in uow.list_incomplete_provider_invocations():
        state = str(getattr(getattr(invocation, "state", None), "value", "") or "").lower()
        if state == "handed_off":
            inflight.append(str(getattr(invocation, "invocation_id", "")))
    return tuple(inflight)


def _uow_from_ports(uow_or_ports):
    """生产 ``RuntimePorts`` 的 ``react_checkpoint`` 就是 ``SqliteExecutionUnitOfWork``
    （``main.py`` 的 ``_production_ports_for`` 用 ``cached.react_checkpoint is not uow``
    守住这个等式）。这里只按能力取，不依赖 SDK 私有属性。"""
    if uow_or_ports is None:
        return None
    if callable(getattr(uow_or_ports, "list_incomplete_provider_invocations", None)):
        return uow_or_ports
    for name in ("react_checkpoint", "uow"):
        candidate = getattr(uow_or_ports, name, None)
        if candidate is not None and callable(
            getattr(candidate, "list_incomplete_provider_invocations", None)
        ):
            return candidate
    return None


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
        # F06 修复（2026-09-08）：因"有其它调用在飞行中"而放弃的运行期 reconcile 次数。
        self.inflight_skips = 0
        self.last_inflight: tuple[str, ...] = ()

    async def reconcile(self) -> int:
        """SDK ``RuntimeReconciliationPort``：进程启动 ``_start_once`` 的**全量**调和。

        静默期语义：此刻本进程没有任何在途 provider 调用，账本里残留的 ``HANDED_OFF``
        全是上一世崩溃留下的，必须整库结清。启动路径不加闸门，行为与 F06 一致。
        """
        return await self._reconcile(self._provider_reconciliation)

    async def reconcile_for_run(self, sdk_run_id) -> int:
        """F06 运行期入口（前台 Run 观察到 ``BOUND_WAITING`` 时调用）：在途闸门 + Run 作用域。

        运行期 ``reconcile_incomplete`` 的静默期前提消失：并发 Run（``main.py`` 的并发
        会话、``delegate_run`` 子 Run）共享同一个 coordinator 与 uow。SDK 会把任何
        ``HANDED_OFF`` 记录无条件判 UNKNOWN，于是那个 Run 真实成功的响应在
        ``settle_provider_invocation`` 处版本不匹配 → ``ProviderInvocationUnknownError``
        被丢弃，并被授权重发 → 重复发送 / 重复计费。

        两道闸：

        1. **在途闸门**（本方法）：只要账本里存在任何 ``handed_off`` 记录，本次直接返回 0，
           不调 ``reconcile_incomplete``；前台按既有 ``settled == 0`` 分支维持
           ``BOUND_WAITING``，由下一轮观察 / 重启的启动路径兜底。``_settle_unknown``
           发生在端口 ``observe`` **之前**，所以这一步不能靠端口代替。
        2. **Run 作用域**（``RunScopedProviderReconciliation``）：即使闸门放行，也只对目标
           Run 的 UNKNOWN 记录裁决，其它 Run 一律 ``STILL_UNKNOWN``（不写决议、不动账本）。
           副作用是 ``settled`` 从"全库计数"收敛成"目标 Run 计数"，
           ``exhausted_runs`` 也不会再被非目标 Run 污染。
        """
        ports = self._ports_getter()
        if ports is None:
            return 0
        uow = _uow_from_ports(ports)
        if uow is None:
            # 证不出"当前无在途调用"→ fail-closed，不裁决。
            self.inflight_skips += 1
            return 0
        inflight = provider_invocations_in_flight(uow)
        if inflight:
            self.inflight_skips += 1
            self.last_inflight = inflight
            return 0
        scoped = RunScopedProviderReconciliation(self._provider_reconciliation, sdk_run_id)
        return await self._reconcile(scoped, ports=ports)

    async def _reconcile(self, provider_reconciliation, *, ports=None) -> int:
        if ports is None:
            ports = self._ports_getter()
        if ports is None:
            return 0
        reconcile = getattr(getattr(ports, "provider", None), "reconcile_incomplete", None)
        if not callable(reconcile):
            raise RuntimeError("production provider coordinator lacks reconcile_incomplete")
        settled = await reconcile(provider_reconciliation=provider_reconciliation)
        self.settled_total += int(settled or 0)
        return int(settled or 0)


__all__ = (
    "ProductProviderReconciliationAdapter",
    "ProductProviderRetryOnceReconciliation",
    "ProductReconciliationAdapter",
    "ProductRuntimeReconciliation",
    "ProductStartupReconciliationAdapter",
    "ProviderUnknownRetryOncePolicy",
    "RunScopedProviderReconciliation",
    "provider_invocations_in_flight",
    "waiting_runs_blocked_on_provider",
)
