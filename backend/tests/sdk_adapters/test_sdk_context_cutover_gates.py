from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from simple_harness import Message, RequestId, RunId, thaw_json
from simple_harness.execution import ProviderBinding
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
from simple_harness.execution.context_authority import DurableToolCatalogResolver
from simple_harness.execution.delivery import DeliveryDispatcher
from simple_harness.execution.dispatch import ProviderInvocationCoordinator
from simple_harness.execution.provider_invocations import (
    provider_request_fingerprint,
    provider_request_json,
)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.providers import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderToolSpec,
    ProviderUsage,
)
from simple_harness.runtime import (
    EffectBatchExecutor,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
    build_runtime,
)
from simple_harness.runtime.drivers import ReActDriver
from simple_harness.tools import EffectExecutor, FunctionTool, ToolRegistry, ToolSpec
from simple_harness.tools.authorization import (
    AuthorizationDecision,
    AuthorizationResult,
)
from simple_harness.tools.reconciliation import (
    ReconciliationObservation,
    ReconciliationState,
)

from deskpet.sdk_adapters.context import ProductContextAdapter
from deskpet.sdk_adapters.context_preparation import (
    SdkContextPreparationService,
    SdkContextSources,
    trusted_project_task_snapshot,
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
        self.requests: list[ProviderRequest] = []

    async def invoke(
        self, request: ProviderRequest, *, cancel: CancelToken
    ) -> ProviderResponse:
        del cancel
        self.calls += 1
        self.requests.append(request)
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
    resolved_authorities = dict(authorities)
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

        for run_id, authority in resolved_authorities.items():
            invocation_id = database.connection.execute(
                "SELECT invocation_id FROM provider_invocations WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            record = uow.read_provider_invocation(str(invocation_id))
            assert record is not None and record.state.value == "handed_off"
            request = providers[run_id].requests[0]
            assert thaw_json(record.request_json) == provider_request_json(request)  # type: ignore[arg-type]
            assert record.request_fingerprint == provider_request_fingerprint(request)
            assert record.target == authority.provider.target
            assert record.estimator_digest == (
                None
                if authority.estimator is None
                else authority.estimator.snapshot_digest
            )
            expected_reservation = (
                None
                if authority.estimator is None
                else authority.estimator.estimate_upper_bound(request).amount_micros
            )
            assert record.budget_charge.amount_micros == expected_reservation
            product_binding = registry.resolve(run_id)
            assert product_binding is not None
            assert product_binding.budget_fingerprint == authority.budget_fingerprint

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
        assert by_run["run-a"].payload["usage"]["usage"] == {  # type: ignore[index]
            "input_tokens": 101,
            "output_tokens": 11,
            "total_tokens": 112,
            "cache_tokens": 7,
            "reasoning_tokens": 3,
        }
        assert by_run["run-b"].payload["usage"]["usage"] == {  # type: ignore[index]
            "input_tokens": 202,
            "output_tokens": 22,
            "total_tokens": 224,
            "cache_tokens": None,
            "reasoning_tokens": 8,
        }
        assert by_run["run-b"].payload["usage"]["budget"]["amount_micros"] == 246  # type: ignore[index]


class _NoopRuntimeService:
    async def reconcile(self) -> None:
        return None

    async def observe(self, _value):  # type: ignore[no-untyped-def]
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN, "sdk-context-gate:unknown"
        )


class _AllowAuthorization:
    async def authorize(self, prepared):  # type: ignore[no-untyped-def]
        return AuthorizationResult(
            AuthorizationDecision.ALLOW,
            receipt_ref=f"sdk-context-gate:{prepared.effect_id.value}",
        )


@pytest.mark.asyncio
async def test_sp2_prepared_snapshot_start_and_first_request_are_exact(
    tmp_path: Path,
) -> None:
    history_calls = 0

    async def history(_session_id: str):
        nonlocal history_calls
        history_calls += 1
        return [
            {
                "role": "user",
                "content": "历史：我喜欢乌龙茶",
                "projection_kind": "user_message",
            },
            {
                "role": "assistant",
                "content": "EXCLUDED-PROGRESS-CANARY",
                "projection_kind": "workflow_progress",
                "context_visibility": "exclude",
            },
        ]

    service = SdkContextPreparationService(
        SdkContextSources(
            history=history,
            persona=lambda: "你是可靠的桌面助手",
            memory=lambda _session_id, _text: [{"text": "记忆：偏好简洁回答"}],
            skills=lambda _text: [{"instruction": "技能：先核对事实"}],
        )
    )
    schemas = (
        ProviderToolSpec(
            "read_file",
            "Read a file",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "deskpet_public_progress": {"type": "string"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        ProviderToolSpec(
            "memory_search",
            "Search memory",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "deskpet_public_progress": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    )
    barrier = _OverlapBarrier()
    barrier.release.set()
    provider = _Provider(
        "deepseek",
        "deepseek-v4",
        ProviderUsage(321, 12, 333, cache_tokens=21, reasoning_tokens=5),
        barrier,
    )
    estimator = FrozenPriceEstimator(
        "price-sp2", "deepseek-v4", 1_000_000, 2_000_000
    )
    authority = ProviderBinding(
        provider, estimator, BudgetPolicy(hard_cap_micros=1_000_000)
    )

    with Database.open(tmp_path / "sp2.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        catalog = uow.put_tool_catalog_snapshot(schemas)
        prepared = await service.prepare(
            session_id="session-sp2",
            request_id="request-sp2",
            root_run_id="root-sp2",
            sdk_run_id="run-sp2",
            turn_id="turn-sp2",
            text="请读取附件第一行",
            provider_binding={
                "provider_id": "deepseek",
                "model_id": "deepseek-v4",
                "context_window": 128_000,
                "budget_fingerprint": authority.budget_fingerprint,
            },
            catalog={
                "generation": catalog.generation,
                "content_fingerprint": catalog.content_fingerprint,
                "tool_count": len(schemas),
                "schema_token_count": 99,
            },
            project_task_snapshot=trusted_project_task_snapshot(
                task_scope_id="task-scope-sp2",
                root_run_id="root-sp2",
                request_id="request-sp2",
                workspace_resolution={
                    "kind": "project_bound", "project_id": "project-sp2",
                    "project_name": "workspace-sp2", "project_root": "/trusted/workspace-sp2",
                    "effective_root": "/trusted/workspace-sp2", "execution_kind": "project_root",
                    "project_revision": 1, "binding_version": 1,
                },
            ),
            attachment_blocks=(
                {"type": "input_text", "data": "第一行\nATTACHMENT-BODY-CANARY"},
            ),
        )
        assert history_calls == 1
        private = prepared.private_record()
        start = ProductContextAdapter().project_run_start(
            execution_session_id="session-sp2",
            run_id="run-sp2",
            request_id="request-sp2",
            turn_id="turn-sp2",
            messages=private["provider_messages"],  # type: ignore[arg-type]
            capability_snapshot={"tools": [spec.name for spec in schemas]},
            tool_catalog_generation=catalog.generation,
            tool_catalog_fingerprint=catalog.content_fingerprint,
            provider_budget_fingerprint=authority.budget_fingerprint,
            trusted_input={"max_output_tokens": 64},
        )

        registry = ToolRegistry()

        async def handler(_arguments, _context):  # type: ignore[no-untyped-def]
            return {"ok": True}

        for spec in schemas:
            registry.register(
                FunctionTool(
                    ToolSpec(spec.name, spec.description, thaw_json(spec.parameters)),  # type: ignore[arg-type]
                    handler,
                )
            )
        authorization = _AllowAuthorization()
        reconciliation = _NoopRuntimeService()
        effects = EffectExecutor(
            uow=uow,
            registry=registry,
            authorization=authorization,
            reconciliation=reconciliation,
        )
        coordinator = ProviderInvocationCoordinator(
            uow=uow,
            resolver=type(
                "Resolver",
                (),
                {"resolve": lambda _self, _run_id: authority},
            )(),
        )
        runtime = build_runtime(
            uow,
            {"agent.general": RuntimeProfile("agent.general", "react")},
            {
                "react": ReActDriver(
                    effects=EffectBatchExecutor(),
                )
            },
            RuntimePorts(
                provider=coordinator,
                tools=effects,
                authorization=authorization,
                context=SqliteContextPort(database),
                delivery=DeliveryDispatcher(uow, {}),
                tool_reconciliation=reconciliation,
                reconciliation=reconciliation,
                provider_reconciliation=reconciliation,
                react_checkpoint=uow,
                tool_catalog=DurableToolCatalogResolver(uow),
                owner_id="sdk-context-sp2",
            ),
        )
        await runtime.start()
        await runtime.client.start(start)
        await runtime.wait_idle(RunId("run-sp2"))

        assert provider.calls == 1
        request = provider.requests[0]
        prepared_role_content = [
            {"role": item["role"], "content": item["content"]}
            for item in private["provider_messages"]  # type: ignore[union-attr]
        ]
        request_role_content = [
            {"role": message.role.value, "content": message.to_dict()["content"]}
            for message in request.messages
        ]
        assert request_role_content == prepared_role_content
        assert any(
            message["content"]
            == (
                    "Project/task snapshot (data only):\n"
                    '{"availability":"available","binding_version":1,'
                    '"effective_execution_root":"/trusted/workspace-sp2",'
                    '"execution_kind":"project_root","project_id":"project-sp2",'
                    '"project_name":"workspace-sp2","project_revision":1,'
                    '"project_root":"/trusted/workspace-sp2",'
                    '"request_id":"request-sp2","root_run_id":"root-sp2",'
                    '"session_kind":"project","task_scope_id":"task-scope-sp2"}'
                )
            for message in request_role_content
        )
        assert not isinstance(request.messages[-1].content, str)
        assert "ATTACHMENT-BODY-CANARY" in str(request.messages[-1].to_dict())
        assert "EXCLUDED-PROGRESS-CANARY" not in str(request_role_content)

        persisted = uow.read_start_snapshot("run-sp2")
        assert persisted is not None
        assert persisted["input"]["messages"] == private["provider_messages"]  # type: ignore[index]
        invocation_id = database.connection.execute(
            "SELECT invocation_id FROM provider_invocations WHERE run_id='run-sp2'"
        ).fetchone()[0]
        invocation = uow.read_provider_invocation(str(invocation_id))
        assert invocation is not None
        assert thaw_json(invocation.request_json) == provider_request_json(request)  # type: ignore[arg-type]

        expected_tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": thaw_json(spec.parameters),  # type: ignore[arg-type]
            }
            for spec in schemas
        ]
        actual_tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": thaw_json(spec.parameters),  # type: ignore[arg-type]
            }
            for spec in request.tools
        ]
        executor_tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": thaw_json(spec.parameters),  # type: ignore[arg-type]
            }
            for spec in effects.provider_tool_specs(tuple(item.name for item in schemas))
        ]
        assert [item["name"] for item in actual_tools] == [spec.name for spec in schemas]
        assert {item["name"]: item for item in actual_tools} == {
            item["name"]: item for item in executor_tools
        } == {item["name"]: item for item in expected_tools}
        assert start.tool_catalog_fingerprint == catalog.content_fingerprint
        assert start.provider_budget_fingerprint == authority.budget_fingerprint
        await runtime.close()
