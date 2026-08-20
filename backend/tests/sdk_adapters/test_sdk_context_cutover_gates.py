from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from simple_harness import Message, RequestId, RunId
from simple_harness.execution import ProviderBinding
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
from simple_harness.execution.dispatch import ProviderInvocationCoordinator
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.providers import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderUsage,
)

from deskpet.sdk_adapters.run_bindings import (
    SdkRunBindingRegistry,
    SdkRunBindingV1,
)


class _OverlapBarrier:
    def __init__(self) -> None:
        self.count = 0
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def arrive(self) -> None:
        self.count += 1
        if self.count == 2:
            self.both_entered.set()
        await self.release.wait()


class _Provider:
    def __init__(
        self,
        provider_id: str,
        model: str,
        usage: ProviderUsage,
        barrier: _OverlapBarrier,
    ) -> None:
        self.target = ProviderTarget(
            provider_id=provider_id,
            model=model,
            pricing_key=model,
            endpoint_identity=f"https://{provider_id}.invalid/v1/chat/completions",
            adapter_key="sdk-context-gate.v1",
        )
        self.usage = usage
        self.barrier = barrier
        self.calls = 0

    async def invoke(
        self, request: ProviderRequest, *, cancel: CancelToken
    ) -> ProviderResponse:
        del cancel
        self.calls += 1
        await self.barrier.arrive()
        return ProviderResponse(
            request.request_id,
            Message("assistant", f"reply:{self.target.model}"),
            usage=self.usage,
            model=self.target.model,
            finish_reason="stop",
        )


def _create_run(
    uow: SqliteExecutionUnitOfWork, run_id: str, request_id: str
):
    uow.create_with_start_snapshot(
        execution_session_id=f"session-{run_id}",
        run_id=run_id,
        request_id=f"root-{request_id}",
        profile_key="agent.general",
        driver_kind="react",
        snapshot={"prompt": run_id},
        event_id=f"event-{run_id}",
        now=10_000_000_000,
    )
    return uow.claim_runtime_activation(
        run_id=run_id,
        owner_id="sdk-context-gate",
        namespace="runtime.kernel",
        now=10_000_000_001,
        lease_ttl_seconds=100,
    )[1]


@pytest.mark.asyncio
async def test_sp1_real_sdk_uow_keeps_per_run_authority_during_true_overlap(
    tmp_path: Path,
) -> None:
    barrier = _OverlapBarrier()
    providers = {
        "run-a": _Provider(
            "deepseek",
            "deepseek-v4",
            ProviderUsage(101, 11, 112, cache_tokens=7, reasoning_tokens=3),
            barrier,
        ),
        "run-b": _Provider(
            "kimi",
            "kimi-k3",
            ProviderUsage(202, 22, 224, cache_tokens=None, reasoning_tokens=8),
            barrier,
        ),
    }
    authorities = {
        "run-a": ProviderBinding(
            providers["run-a"],
            None,
            BudgetPolicy(refuse_on_unknown=False),
        ),
        "run-b": ProviderBinding(
            providers["run-b"],
            FrozenPriceEstimator("price-b", "kimi-k3", 1_000_000, 2_000_000),
            BudgetPolicy(hard_cap_micros=1_000_000),
        ),
    }
    registry = SdkRunBindingRegistry()
    for run_id, authority in authorities.items():
        registry.register(
            SdkRunBindingV1.build(
                run_id=run_id,
                session_id=f"session-{run_id}",
                request_id=f"request-{run_id}",
                snapshot_id=f"snapshot-{run_id}",
                provider_id=authority.provider.target.provider_id,
                provider_incarnation_id=f"incarnation-{run_id}",
                provider_config_revision=1,
                binding_epoch=1,
                model_id=authority.provider.target.model,
                model_params={"reasoning_mode": "thinking"},
                context_window=128_000,
                catalog_generation=3,
                catalog_fingerprint="c" * 64,
                budget_fingerprint=authority.budget_fingerprint,
            )
        )

    resolve_calls: list[str] = []

    class Resolver:
        def resolve(self, run_id: RunId) -> ProviderBinding:
            product_binding = registry.resolve(run_id.value)
            assert product_binding is not None
            authority = authorities[product_binding.run_id]
            assert authority.provider.target.model == product_binding.model_id
            assert authority.budget_fingerprint == product_binding.budget_fingerprint
            resolve_calls.append(run_id.value)
            return authority

    with Database.open(tmp_path / "execution.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        leases = {
            run_id: _create_run(uow, run_id, f"request-{run_id}")
            for run_id in authorities
        }
        coordinator = ProviderInvocationCoordinator(uow=uow, resolver=Resolver())

        async def invoke(run_id: str) -> ProviderResponse:
            return await coordinator.invoke(
                RunId(run_id),
                ProviderRequest(
                    RequestId(f"request-{run_id}"),
                    (Message("user", f"hello:{run_id}"),),
                    max_output_tokens=32,
                ),
                cancel=CancelToken(),
                execution_lease=leases[run_id],
            )

        tasks = {
            run_id: asyncio.create_task(invoke(run_id)) for run_id in authorities
        }
        await asyncio.wait_for(barrier.both_entered.wait(), timeout=1)
        assert barrier.count == 2

        # A registry/config change after physical handoff cannot alter either
        # in-flight invocation's already resolved Provider/estimator/budget.
        authorities["run-a"] = authorities["run-b"]
        barrier.release.set()
        responses = dict(
            zip(tasks, await asyncio.gather(*tasks.values()), strict=True)
        )

        assert responses["run-a"].model == "deepseek-v4"
        assert responses["run-b"].model == "kimi-k3"
        assert sorted(resolve_calls) == ["run-a", "run-b"]
        assert providers["run-a"].calls == providers["run-b"].calls == 1

        receipts = uow.list_provider_projection_receipts()
        by_run = {receipt.run_id: receipt for receipt in receipts}
        assert by_run["run-a"].payload["target"]["model"] == "deepseek-v4"  # type: ignore[index]
        assert by_run["run-b"].payload["target"]["model"] == "kimi-k3"  # type: ignore[index]
        assert by_run["run-a"].payload["usage"]["budget"]["kind"] == "unknown"  # type: ignore[index]
        assert by_run["run-b"].payload["usage"]["budget"]["kind"] == "trusted_usage"  # type: ignore[index]
