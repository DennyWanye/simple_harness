# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""启动步骤：会话库、执行账本、模型调用协调与负载路由、公开查看服务"""

from __future__ import annotations

import hashlib
from typing import Any

import paths as _paths
from deskpet.execution.fences import UnboundRunExecutionFence
from deskpet.execution.harness_public_read_service import HarnessPublicReadService
from deskpet.execution.provider_fault_scenario_runner import ProviderFaultScenarioRunner
from deskpet.execution.provider_fault_script import ProviderFaultScriptV1
from deskpet.execution.provider_invocations import ProviderInvocationCoordinator
from deskpet.execution.provider_workload_audit import ProviderWorkloadAuditStore
from deskpet.execution.provider_workloads import (
    BackgroundModelPolicy,
    ProviderWorkloadBreaker,
    ProviderWorkloadRoutePolicy,
    ProviderWorkloadRouter,
    SessionProviderWorkloadTargetResolver,
)
from deskpet.execution.tool_completion_latch_script import ToolCompletionLatchScriptV1
from deskpet.tools import registry as _runtime_tool_registry
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from providers.openai_compatible import OpenAICompatibleProvider

from host.llm_providers import _sanitize_cot_dsml
from host.runtime import SHARED_SECRET, logger, service_context
from host.stores_bootstrap import (
    _provider_workload_maintenance_tasks,
    _session_db,
    _state_db_path,
)


async def _startup_execution_ledger() -> Any:
    """启动：会话库检查、执行账本（全产品共用）及其上的模型调用服务；返回会话库。

    执行账本落在 ``<用户数据>/data/workflow.db``，自己建表、自己持有写通道。
    账本建不起来时主对话不可用，启动直接失败，不做降级。
    """
    sdb = service_context.get("session_db")
    if sdb is not None:
        try:
            await sdb.initialize()
            logger.info("p4_session_db_ready", path=str(sdb._db_path))
        except Exception as exc:  # noqa: BLE001 - 会话库失败由各读取方报不可用
            logger.warning("p4_session_db_init_failed", error=str(exc), exc_info=True)

    workflow_db = _paths.user_data_dir() / "data" / "workflow.db"
    execution_uow = SqliteExecutionUnitOfWork(workflow_db)
    await execution_uow.initialize()
    service_context.register("execution_uow", execution_uow)
    logger.info("execution_ledger_ready", db_path=str(workflow_db))

    service_context.register(
        "harness_public_read_service",
        HarnessPublicReadService(
            workflow_db_path=workflow_db,
            state_db_path=_state_db_path,
            cursor_secret=hashlib.sha256(f"harness-inspector-v3:{SHARED_SECRET}".encode()).digest(),
        ),
    )
    await _startup_provider_invocation_runtime(execution_uow)
    return sdb


async def _startup_provider_invocation_runtime(execution_uow) -> None:
    """执行围栏、模型调用协调器、模型负载路由与审计。"""
    _execution_fence = UnboundRunExecutionFence()
    _provider_fault_script = ProviderFaultScriptV1.from_environment()

    _runtime_tool_registry.set_tool_completion_latch(ToolCompletionLatchScriptV1.from_environment())
    _provider_invocation_coordinator = ProviderInvocationCoordinator(
        execution_uow,
        fence_reacquirer=_execution_fence.acquire,
        fault_script=_provider_fault_script,
    )
    service_context.register("run_execution_fence_acquirer", _execution_fence.acquire)
    service_context.register(
        "provider_invocation_coordinator",
        _provider_invocation_coordinator,
    )

    _provider_registry_for_workloads = service_context.get("provider_registry")

    def _provider_workload_factory(entry, model):
        if _provider_registry_for_workloads is None:
            raise RuntimeError("provider registry is unavailable")
        provider = OpenAICompatibleProvider(
            base_url=entry.base_url,
            api_key=(_provider_registry_for_workloads.resolve_api_key(entry.id) or "ollama"),
            model=model,
            temperature=getattr(entry, "temperature", 0.7),
            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
            code_params=getattr(entry, "code_params", None),
            is_relay=(getattr(entry, "source", "") == "relay"),
        )
        provider.provider_id = str(entry.id)
        return provider

    _provider_workload_audit = ProviderWorkloadAuditStore(_state_db_path)
    _session_workload_resolver = SessionProviderWorkloadTargetResolver(
        registry=_provider_registry_for_workloads,
        session_db=_session_db,
        readiness=getattr(service_context, "provider_routing_readiness", None),
        provider_factory=_provider_workload_factory,
        start_snapshot_reader=execution_uow,
    )
    _background_model_policy = BackgroundModelPolicy(
        registry=_provider_registry_for_workloads,
        readiness=getattr(service_context, "provider_routing_readiness", None),
        provider_factory=_provider_workload_factory,
    )
    _provider_workload_router = ProviderWorkloadRouter(
        ProviderWorkloadRoutePolicy(
            session=_session_workload_resolver,
            background=_background_model_policy,
        ),
        breaker=ProviderWorkloadBreaker(),
        audit=_provider_workload_audit,
        fault_script=_provider_fault_script,
    )
    service_context.register("provider_workload_audit", _provider_workload_audit)
    service_context.register("provider_workload_router", _provider_workload_router)
    if _provider_fault_script is not None:
        _provider_fault_scenario_runner = ProviderFaultScenarioRunner(
            _provider_fault_script,
            _provider_workload_router,
            task_owner=_provider_workload_maintenance_tasks,
        )
        await _provider_fault_scenario_runner.start()
