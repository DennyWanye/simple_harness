"""SDK 运行栈组装（整段搬家，拆函数在后面的阶段）与装配槽位检查"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import paths as _paths
from deskpet.capabilities.skill_install import (
    AuthorizedPreflightReceiptResolver,
    ProjectSkillInstallError,
)
from deskpet.execution.primary_context_page_reader import PrimaryContextPageReader
from deskpet.execution.termination_budget import resolve_max_wall_seconds
from deskpet.memory.evidence_kind_schema import initialize_evidence_kind_state_db
from deskpet.memory.identity import ValidatedLocalMemoryIdentityAuthority
from deskpet.operation_audit.page_in_receipts import ContextPageInReceiptLedger
from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.capability_bridge import SdkRuntimeCapabilityBridgeAdapter
from deskpet.sdk_adapters.capability_catalog import ProductCapabilityCatalogSourceAdapter
from deskpet.sdk_adapters.composition import (
    OwnedResourceCloser,
    ProductionRuntimeBuild,
    ProductSdkRuntimeStack,
    SdkRuntimeBuildInputs,
)
from deskpet.sdk_adapters.context import ProductContextAdapter
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    ProductRuntimeDecisionSink,
)
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore as _ContextRouteLedgerStore,
)
from deskpet.sdk_adapters.context_provider import ProductConversationContextProvider
from deskpet.sdk_adapters.context_route import (
    CONTEXT_ROUTE_SCHEMA,
    TASK_SCOPE_SEARCH_SCHEMA,
    ContextRouteToolService,
)
from deskpet.sdk_adapters.context_route import read_current_turn_text as _read_current_turn_text
from deskpet.sdk_adapters.context_source import ProductContextSourceRepository
from deskpet.sdk_adapters.delegation import (
    ProductDelegationService,
    is_delegated_child_start,
    waiting_runs_blocked_on_delegation,
)
from deskpet.sdk_adapters.desktop_runtime import _DeliverySink
from deskpet.sdk_adapters.effect_gate import EffectGate, project_tool_execution_context
from deskpet.sdk_adapters.null_memory_port import NoMemoryAgentPort
from deskpet.sdk_adapters.prepared_authorization import SdkPreparedAuthorizationPolicy
from deskpet.sdk_adapters.provider import ProductProviderInvocationCoordinator
from deskpet.sdk_adapters.provider_projection_pump import (
    ProviderProjectionContextV1,
    SdkProviderProjectionPump,
)
from deskpet.sdk_adapters.read_gate import WorkspaceReadGate
from deskpet.sdk_adapters.reconciliation import (
    ProductReconciliationAdapter,
    ProductRuntimeReconciliation,
    waiting_runs_blocked_on_provider,
)
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.sdk_adapters.runtime_paths import (
    ProductRuntimePathsAdapter,
    durable_sdk_run_start_exists,
)
from deskpet.sdk_adapters.sdk_candidate import build_runtime_identity
from deskpet.sdk_adapters.skill_install_verification import (
    ProductRootDriverRouter,
    SkillInstallVerificationAttemptResolver,
)
from deskpet.sdk_adapters.skill_resolver import SdkThenLegacyFrozenSkillResolver
from deskpet.sdk_adapters.task_execution import BindingRootResolver, ProductTaskExecutionAuthority
from deskpet.sdk_adapters.task_scope_mutation import (
    TASK_SCOPE_UPDATE_DESCRIPTION,
    TASK_SCOPE_UPDATE_SCHEMA,
)
from deskpet.sdk_adapters.task_scope_mutation import (
    read_current_turn_evidence_ref as _read_current_turn_evidence_ref,
)
from deskpet.sdk_adapters.tool_authority import (
    SdkRunToolAuthorityRegistry,
    SdkToolAuthorityMigrationUnavailable,
)
from deskpet.sdk_adapters.tools import (
    ProductEffectExecutor,
    ProductToolRegistration,
    active_product_tool_call_id,
    active_product_tool_context,
    build_product_tool_registry,
    extend_product_registry_with_mcp,
)
from deskpet.sdk_adapters.workflows import build_product_workflow_registrations
from deskpet.task_scope.disclosure import ScopeDisclosureReader
from deskpet.task_scope.store import CanonicalTaskScopeStore as _GateScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from deskpet.tool_catalog import ToolCatalogDependencies, build_explicit_product_tool_catalog
from deskpet.tools import registry as live_tool_registry
from deskpet.tools import skill_tools as _skill_tools
from observability.memory_probe import install_memory_probe as _install_memory_probe
from providers.extra_headers import install_httpx_extra_headers_hook
from simple_harness import RuntimeProfile
from simple_harness.execution.budget import BudgetPolicy
from simple_harness.execution.context_authority import DurableToolCatalogResolver
from simple_harness.execution.context_staging import ContextStagingRepository
from simple_harness.execution.delivery import DeliveryDispatcher
from simple_harness.runtime import (
    ProductionRuntimeConfig,
    ResourceOwnership,
    RuntimePorts,
    SqliteContextPort,
    StartModeDriverRouter,
    build_production_runtime,
)
from simple_harness.runtime.drivers import build_react_driver
from simple_harness.runtime.termination import TerminationLimits
from simple_harness.tools import ToolResult
from simple_harness.tools.reconciliation import ReconciliationObservation, ReconciliationState

from host.context_usage import _broadcast_context_usage_snapshot
from host.logging_setup import _sdk_observability
from host.runtime import config, logger, service_context
from host.sdk_recovery import (
    _ensure_sdk_recovery_watcher,
    _isolate_unrestorable_sdk_tool_authority,
    _restore_sdk_delivery_route,
)
from host.sdk_services import (
    _build_run_context_authority,
    _ensure_evidence_ingress,
    _ensure_foreground_effect_gate,
    _ensure_provider_reconciliation,
    _ensure_run_fault_memo,
    _ensure_run_route_state_memo,
    _ensure_task_scope_update_service,
    _freeze_sdk_catalog,
    _primary_history_policy,
    _ProductSdkProviderBindingResolver,
    _sdk_runtime_authority_bindings,
)
from host.state import host_state
from host.stores_bootstrap import _state_db_path
from host.tools_bootstrap import deskpet_tool_registry_v2


@dataclass
class _SdkRuntimeStackBuild:
    """_build_product_sdk_runtime_stack 的运行状态：原函数的局部变量与参数（切段用）。"""

    result: Any = None
    flow: Any = None
    _ProductionProjectionPumpProxy: Any = None
    _ProductionToolCatalogProxy: Any = None
    _authority_ports: Any = None
    _closure_tool_service: Any = None
    _context_route_binding_store: Any = None
    _context_route_service: Any = None
    _delegation_observer: Any = None
    _has_durable_sdk_run_start: Any = None
    _max_wall_seconds: Any = None
    _memory_probe: Any = None
    _occurrence_coordinator: Any = None
    _occurrence_reconcile: Any = None
    _production_ports_for: Any = None
    _release_project_run: Any = None
    _state_version: Any = None
    _typed_use_authority: Any = None
    agent_memory_port: Any = None
    authorization_adapter: Any = None
    authorization_policy: Any = None
    authorization_runtime: Any = None
    budget_policy: Any = None
    capability_bridge: Any = None
    capability_platform: Any = None
    capability_scope_store: Any = None
    capability_store: Any = None
    catalog: Any = None
    client: Any = None
    clock: Any = None
    close_projection_pump: Any = None
    configured_workspace_root: Any = None
    context_page_store: Any = None
    context_provider: Any = None
    context_route_handler: Any = None
    context_route_ledger: Any = None
    context_source_repository: Any = None
    delegation_service: Any = None
    delegation_uow: Any = None
    delivery_adapter: Any = None
    dependencies: Any = None
    driver: Any = None
    effect_gate: Any = None
    execution_context_getter: Any = None
    frozen_catalog: Any = None
    generation: Any = None
    host_control_driver: Any = None
    initial_authorization_policy: Any = None
    memory_identity_authority: Any = None
    memory_identity_resolver: Any = None
    ports_factory: Any = None
    product_state_db: Any = None
    production_ports: Any = None
    production_projection_pump: Any = None
    production_runtime_factory: Any = None
    production_tool_catalog: Any = None
    project_bindings: Any = None
    project_skill_install_service: Any = None
    projected_registrations: Any = None
    projection_pump: Any = None
    provider_binding_resolver: Any = None
    provider_reconciliation: Any = None
    provider_registry: Any = None
    read_gate: Any = None
    reconciled_project_claims: Any = None
    reconciliation_adapter: Any = None
    repository: Any = None
    resource_records: Any = None
    runtime_reconciliation: Any = None
    scope_disclosure: Any = None
    sdk_execution_db: Any = None
    sdk_product_state_db_path: Any = None
    sdk_tool_inventory: Any = None
    search_gateway: Any = None
    session_db: Any = None
    session_state_db_path: Any = None
    skill_install_handler: Any = None
    skill_install_receipts: Any = None
    skill_metas: Any = None
    skill_projection: Any = None
    stack: Any = None
    task_scope_search_handler: Any = None
    task_scope_update_handler: Any = None
    todo_session_db: Any = None
    tool_authorities: Any = None
    tool_inventory: Any = None
    tools_adapter: Any = None
    verification_driver_factory: Any = None
    visible_tool_names: Any = None
    workflow_metadata: Any = None
    execution_uow: Any = None


async def _build_product_sdk_runtime_stack(
    generation: int,
    *,
    clock=time.time,
    configured_workspace_root=None,
):
    """Build SDK Runtime Stack with product adapters (Slice C ingress)."""

    # Verify required services
    st = _SdkRuntimeStackBuild(
        generation=generation, clock=clock, configured_workspace_root=configured_workspace_root
    )
    st.session_db = service_context.get("session_db")
    st.capability_platform = service_context.get("capability_platform")
    if st.session_db is None or st.capability_platform is None:
        raise RuntimeError("SDK Runtime requires session_db and capability_platform")
    st.session_state_db_path = Path(getattr(st.session_db, "db_path", _state_db_path))
    st.memory_identity_authority = service_context.get("memory_identity_authority")
    if st.memory_identity_authority is None:
        st.memory_identity_authority = ValidatedLocalMemoryIdentityAuthority(
            st.session_state_db_path,
            user_data_dir=_paths.user_data_dir(),
        )
        service_context.register("memory_identity_authority", st.memory_identity_authority)
    st.memory_identity_resolver = st.memory_identity_authority.resolver
    service_context.register("memory_identity_resolver", st.memory_identity_resolver)
    # The runtime is stable across Provider mutations.  A physical Provider is
    # constructed only after a Run freezes its own Session binding.
    st.provider_registry = service_context.get("provider_registry")
    if st.provider_registry is None:
        raise RuntimeError("SDK Runtime requires provider_registry")

    # Grok Build lane (2026-09-16): SDK provider builds its own headers, so
    # endpoint-specific ones (llm_runtime.json extra_headers) ride a hook.
    st.client = install_httpx_extra_headers_hook(httpx.AsyncClient())
    st.budget_policy = BudgetPolicy()
    st.provider_binding_resolver = _ProductSdkProviderBindingResolver(
        st.provider_registry, st.client
    )

    # Build tool adapter

    await _prepare_tool_authorities(st)

    _build_drivers_and_dependencies(st)

    # Authorization state must exist before the Skill install registration is
    # frozen, because its physical handler resolves only durable Product saga
    # receipts and never accepts a model-provided approval token.

    _register_skill_install_tool(st)
    # S5a — five-route Context authority tools (host-composed like skill_install).

    await _prepare_context_route_service(st)

    _register_context_route_tools(st)


    _freeze_catalog_and_authorization(st)

    _prepare_reconciliation_and_gates(st)

    _define_production_runtime(st)

    st.flow = None
    _register_runtime_services(st)
    if st.flow == "stop":
        return st.result
    _assert_sdk_composition_slots()
    return st.stack


async def _prepare_tool_authorities(st: _SdkRuntimeStackBuild) -> None:
    """取会话服务与授权运行时，建工具授权登记表、记忆探针、项目绑定与技能解析。"""

    # Build tool catalog dependencies
    st.todo_session_db = service_context.get("session_db")
    st.execution_uow = service_context.get("execution_uow")
    st.context_page_store = service_context.get("context_page_in_store")
    st.search_gateway = service_context.get("search_gateway")
    st.authorization_runtime = service_context.get("authorization_runtime")
    st.capability_store = service_context.get("capability_store")
    st.capability_scope_store = service_context.get("tool_capability_scope_store")
    if (
        st.authorization_runtime is None
        or st.capability_store is None
        or st.capability_scope_store is None
    ):
        raise RuntimeError("SDK Runtime requires prepared authorization authority")
    st.initial_authorization_policy = await st.capability_store.get_policy_state()

    st.skill_projection = service_context.get("managed_skill_discovery_projection")
    st.skill_metas = (
        tuple(st.skill_projection.list_metas()) if st.skill_projection is not None else ()
    )
    st.workflow_metadata = build_product_workflow_registrations(
        generation=st.generation,
        transaction_owner=object(),
    )
    st.resource_records = ProductCapabilityCatalogSourceAdapter().sdk_resource_records(
        skills=st.skill_metas,
        workflows=st.workflow_metadata,
    )
    st.tool_authorities = SdkRunToolAuthorityRegistry(
        scope_store=st.capability_scope_store,
        resource_records=st.resource_records,
        workspace_identity_validator=getattr(
            service_context.get("project_binding_service"),
            "validate_workspace_identity",
            None,
        ),
        run_fault_sink=_ensure_run_fault_memo(),
    )
    st.tool_authorities.add_terminal_listener(
        lambda authority: _ensure_run_route_state_memo().release(authority.run_id)
    )
    # 事件 X-3：原生按站点内存探针。默认关闭 —— `build_memory_probe` 在
    # `SIMPLEHARNESS_MEMORY_PROBE` 没显式打开时返回 None，这里连监听器都不注册，
    # 也不会启动 tracemalloc / import psutil / 碰 gc.get_objects()。

    st._memory_probe = _install_memory_probe(
        registry=st.tool_authorities,
        snapshot_dir=_paths.user_data_dir() / "memory-probe",
        emit=lambda fields: logger.info("memory.probe", **dict(fields)),
    )
    if st._memory_probe is not None:
        logger.info(
            "memory.probe.started",
            every=st._memory_probe.every,
            snapshot_dir=str(st._memory_probe.snapshot_dir),
        )
    st.project_bindings = service_context.get("project_binding_service")
    if st.project_bindings is not None:
        st.sdk_execution_db = ProductRuntimePathsAdapter(_paths.user_data_dir()).execution_database

        async def _has_durable_sdk_run_start(run_id: str) -> bool:
            return await asyncio.to_thread(
                durable_sdk_run_start_exists, st.sdk_execution_db, run_id
            )

        st._has_durable_sdk_run_start = _has_durable_sdk_run_start

        st.reconciled_project_claims = await st.project_bindings.reconcile_orphan_run_claims(
            st._has_durable_sdk_run_start
        )
        if any(st.reconciled_project_claims.values()):
            logger.warning("project_run_claims_reconciled %s", st.reconciled_project_claims)

        def _release_project_run(authority: Any) -> None:
            asyncio.create_task(st.project_bindings.release_run(authority.run_id))

        st._release_project_run = _release_project_run

        st.tool_authorities.add_terminal_listener(st._release_project_run)

    _skill_tools.bind(
        skill_resolver=SdkThenLegacyFrozenSkillResolver(
            authorities=st.tool_authorities,
            snapshot_resolver=service_context.get("frozen_skill_instruction_resolver"),
            legacy_resolver=service_context.get("legacy_frozen_skill_instruction_resolver"),
        )
    )
    deskpet_tool_registry_v2.set_dynamic_capability_admission_provider(
        st.tool_authorities.validate_runtime_tool_admission
    )


def _build_drivers_and_dependencies(st: _SdkRuntimeStackBuild) -> None:
    """建 ReAct 驱动与启动模式路由、执行上下文、能力桥、委派服务与工具目录依赖。"""

    # C10-13 缺陷：这里不显式给 max_wall_seconds 就静默回落 SDK 默认 900s，
    # 恰好等于语料批次的外部 SIGTERM deadline，外层先赢 → 连失败终态回执都
    # 没有（NO_PACKET）。墙钟限额必须显式、可配置，并严格早于任何外层看门狗。
    # 完整的层级不变式见 deskpet/execution/termination_budget.py。
    st._max_wall_seconds = resolve_max_wall_seconds(config.raw)
    logger.info(
        "react_termination_limits",
        max_turns=25,
        max_tool_calls=50,
        max_consecutive_same_tool=10,
        max_wall_seconds=st._max_wall_seconds,
    )
    st.driver = build_react_driver(
        clock=st.clock,
        # SDK H079 的重复键是工具名 + canonical arguments hash；10 限制的是
        # 同名同参的连续调用。改变参数会重置 streak，不是同一工具名总计10次。
        # 25轮/50工具的持久累计计数跨授权恢复保留；外部测试限时另行记录。
        limits=TerminationLimits(
            max_turns=25,
            max_tool_calls=50,
            max_consecutive_same_tool=10,
            max_wall_seconds=st._max_wall_seconds,
        ),
        budget_policy=st.budget_policy,
        estimator=None,
        tool_exposure_resolver=st.tool_authorities.resolve_exposure,
    )

    st.verification_driver_factory = service_context.get(
        "skill_install_verification_driver_factory"
    )
    if st.verification_driver_factory is None:
        raise RuntimeError("Skill verification driver factory is unavailable")

    st.host_control_driver = ProductRootDriverRouter(
        react_driver=st.driver,
        attempt_resolver=SkillInstallVerificationAttemptResolver(st.capability_store),
        verification_driver_factory=st.verification_driver_factory,
    )
    st.driver = StartModeDriverRouter(ordinary=st.driver, host_control=st.host_control_driver)

    def execution_context_getter():

        tool_context = active_product_tool_context()
        run_id = tool_context.run_id.value
        effect = getattr(tool_context, "effect_id", None)
        effect_id = str(getattr(effect, "value", effect) or "")
        if not effect_id:
            raise RuntimeError("SDK product Tool effect identity is unavailable")

        base = st.tool_authorities.resolve(run_id).execution_context(
            call_id=active_product_tool_call_id().value,
            effect_id=effect_id,
        )
        return project_tool_execution_context(base, tool_context)

    st.execution_context_getter = execution_context_getter

    st.capability_bridge = SdkRuntimeCapabilityBridgeAdapter(
        st.tool_authorities,
        st.execution_context_getter,
        route_state_memo=_ensure_run_route_state_memo(),
    )

    # 五个委派工具跑在真实 SDK 子运行上（DETACHED，
    # 授权 = 父目录只读子集，每个子运行一个监视器负责收尾与取消）。执行账本在
    # ports_factory 里才拿得到，这里先放一个占位。

    st.delegation_uow: dict[str, Any] = {}
    st.delegation_service = ProductDelegationService(
        uow_getter=lambda: st.delegation_uow.get("uow"),
        runtime_getter=lambda: (
            host_state.sdk_ingress.require_ready().runtime
            if host_state.sdk_ingress is not None
            else None
        ),
        tool_authorities=st.tool_authorities,
        binding_resolver=st.provider_binding_resolver,
        context_port_getter=lambda: host_state.sdk_context_port,
        on_parent_woken=lambda run_id: _ensure_sdk_recovery_watcher(run_id),
        # 同步等待不能拖过父运行自己的墙钟上限。
        wait_seconds=min(900.0, float(st._max_wall_seconds) * 0.8),
        clock=st.clock,
    )

    st.dependencies = ToolCatalogDependencies(
        todo_session_db=st.todo_session_db,
        # 等子代理工具（字节钉在冻结工具清单里，不能改）按 ``provider().execution_uow`` 取账本，
        # 这里给它一个只带账本的对象。
        workflow_service_provider=lambda: (
            None if st.execution_uow is None else SimpleNamespace(execution_uow=st.execution_uow)
        ),
        context_page_store=st.context_page_store,
        execution_context_getter=st.execution_context_getter,
        capability_bridge_service=st.capability_bridge,
        search_gateway=st.search_gateway,
        delegation_service_provider=lambda: st.delegation_service,
    )


def _register_skill_install_tool(st: _SdkRuntimeStackBuild) -> None:
    """建产品状态库与授权记录，登记技能安装工具与管理类工具投影。"""

    st.sdk_product_state_db_path = _paths.user_data_dir() / "data" / "sdk-product-state.db"
    st.sdk_product_state_db_path.parent.mkdir(parents=True, exist_ok=True)
    st.product_state_db = ProductStateDatabase(st.sdk_product_state_db_path)
    st.product_state_db.initialize()
    st.repository = AuthorizationSagaRepository(
        st.product_state_db, owner_id=f"sdk-runtime-g{st.generation}"
    )
    st.project_skill_install_service = service_context.get("project_skill_install_service")
    if st.project_skill_install_service is None:
        raise RuntimeError("SDK Runtime requires ProjectSkillInstallService")
    st.skill_install_receipts = AuthorizedPreflightReceiptResolver(
        st.capability_store, st.repository
    )

    async def skill_install_handler(_arguments, _context):

        trusted = st.execution_context_getter()
        authority = st.tool_authorities.resolve(trusted.run_id)
        try:
            receipt = await st.skill_install_receipts.resolve_chat(
                context=trusted, principal_id=authority.principal_id
            )
            return await st.project_skill_install_service.confirm_authorized(receipt)
        except ProjectSkillInstallError as exc:
            code = str(getattr(exc, "code", "skill_install_failed"))
            retryable = bool(getattr(exc, "retryable", False))
            public_messages = {
                "skill_install_intent_not_found": "The Skill install request is no longer available.",
                "skill_install_runtime_verifier_unavailable": "Skill verification is temporarily unavailable.",
                "skill_pack_manifest_invalid": "The repository does not contain a valid Skill package.",
            }
            public_message = public_messages.get(
                code, "Skill installation could not be completed safely."
            )
            failure_receipt_ref = hashlib.sha256(
                json.dumps(
                    {
                        "schema": "skill-install-handler-failure-v1",
                        "code": code,
                        "principal_id": authority.principal_id,
                        "tool_name": "skill_install",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            allowed_actions = (
                ["retry", "change_source", "cancel"] if retryable else ["change_source", "cancel"]
            )
            return ToolResult.failed(
                _context.call_id,
                code,
                json.dumps(
                    {
                        "schema": "skill-install-failure-v1",
                        "code": code,
                        "public_message": public_message,
                        "retryable": retryable,
                        "failure_receipt_ref": failure_receipt_ref,
                        "attempt_generation": 1,
                        "allowed_actions": allowed_actions,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                retryable=retryable,
            )

    st.skill_install_handler = skill_install_handler

    st.catalog = build_explicit_product_tool_catalog(st.dependencies)

    st.projected_registrations = tuple(
        dataclass_replace(
            item,
            description=(
                "Administrative memory override only. Ordinary facts, preferences, "
                "and requests such as 'remember X' are recorded automatically after "
                "the Turn completes; do not call this tool for them. Call only when "
                "the user explicitly requests pinned/non-decaying retention or a "
                "specific memory tier."
            ),
        )
        if item.name == "memory_write"
        else item
        for item in st.catalog.registrations
    )
    st.projected_registrations = (
        *st.projected_registrations,
        ProductToolRegistration(
            name="skill_install",
            description="Install Skills from one public GitHub repository for all Sessions of the current user.",
            input_schema={
                "type": "object",
                # The SDK intentionally supports a bounded JSON-Schema subset and
                # rejects the optional `format` keyword. Source resolution performs
                # the authoritative public-GitHub URL validation.
                "properties": {
                    "url": {"type": "string"},
                    "retry_failure_receipt_ref": {"type": "string"},
                    "retry_attempt_generation": {"type": "integer", "minimum": 1},
                    "retry_command_id": {"type": "string"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=st.skill_install_handler,
            dispatch_kind="async",
            permission_category="skill_install",
            metadata={
                "source": "product-skill-install",
                "version": "2",
                "stable_handler_id": "core.skill_install.v2",
            },
        ),
    )


async def _prepare_context_route_service(st: _SdkRuntimeStackBuild) -> None:
    """建上下文路由服务：页面读取、收据账本、范围披露与路由处理器。"""

    def _context_route_binding_store():

        return WorkspaceBindingAuthorityStore(
            _state_db_path, configured_workspace_root=st.configured_workspace_root
        )

    st._context_route_binding_store = _context_route_binding_store

    # 2026-09-10：认知记忆 SDK 移除，``HumanMemoryV7Runtime``（分析批 / 类型化召回 /
    # Procedure / Prospective 的宿主运行时）随之下线，``human_memory_v7_runtime``
    # 槽位不再注册。

    st.context_page_store.primary_reader = PrimaryContextPageReader(
        _state_db_path,
        stack_getter=lambda: host_state.sdk_runtime_stack,
        policy_factory=_primary_history_policy,
    )
    # G1: durable payload-free page-in issue/consume receipts in the audit sidecar.

    st.context_page_store.receipt_ledger = ContextPageInReceiptLedger(
        _state_db_path.with_name("operation-audit.db"),
        clock=st.clock,
    )
    # 2026-09-10：``procedure_use`` / ``procedure_discover`` 两个程序性记忆工具
    # 及其 recovery schema 随认知记忆 SDK 一并移除。
    # 2026-09-08 HM-TO-A6 F-K1: v55 side record of assistant tool-call arguments.
    # v56 chains onto v55; 事件 AI 的 v57 把 context_use_recollection 收进
    # harness_evidence_reservations 的种类 CHECK。初始化链头即安装前面每一步。

    await initialize_evidence_kind_state_db(_state_db_path)
    # 2026-09-10：类型化 Context-use 权威（``memory_standalone`` 路由背后的
    # typed recall 记账）与 Prospective occurrence 协调器都直接建在认知记忆
    # 运行时上，随其一并移除。两个槽位在下游本来就是 Optional（旧库 user_version
    # < 35 时同样是 None），此处恒为 None。
    st._typed_use_authority = None
    service_context.register("sdk_typed_context_use_authority", None)
    st._occurrence_coordinator = None
    service_context.register("prospective_occurrence_coordinator", None)

    st.scope_disclosure = ScopeDisclosureReader(
        _state_db_path,
        stack_getter=lambda: host_state.sdk_runtime_stack,
        policy_factory=_primary_history_policy,
    )
    st._context_route_service = ContextRouteToolService(
        service_factory_getter=lambda: service_context.get("human_memory_host_service_factory"),
        binding_store_factory=st._context_route_binding_store,
        binding_append_getter=lambda: service_context.get("human_memory_binding_append_authority"),
        ledger=_ContextRouteLedgerStore(
            _state_db_path, evidence_ingress=_ensure_evidence_ingress()
        ),
        tool_context_getter=active_product_tool_context,
        recall_executor=None,
        typed_use_authority=None,
        scope_disclosure_reader=st.scope_disclosure.read,
        producer_dependencies_reader=st.scope_disclosure.producer_dependencies,
        # Event V: the contested probe asks with the model's own words AND this
        # turn's admitted user text, so neither wording alone decides whether
        # the Host gets to see an open conflict group.
        current_turn_text_reader=(
            lambda sdk_run_id: _read_current_turn_text(_state_db_path, sdk_run_id)
        ),
        # 事件 AL: an accepted route into a task publishes this turn's own
        # admitted evidence id, so ``task_scope_update`` can cite it on its
        # first attempt instead of paying for a rejected payload first.
        current_turn_evidence_reader=(
            lambda sdk_run_id, task_scope_id: _read_current_turn_evidence_ref(
                _state_db_path, sdk_run_id, task_scope_id
            )
        ),
        # A person's "allow" on this exact create_new call is also the manual
        # directory-binding decision (one confirmation, no failing retry loop).
        user_confirmation_reader=(
            lambda sdk_run_id, effect_id: st.authorization_policy.user_confirmed(
                sdk_run_id, effect_id
            )
        ),
    )

    async def context_route_handler(arguments, _context):
        return await st._context_route_service.handle_context_route(arguments)

    st.context_route_handler = context_route_handler

    async def task_scope_search_handler(arguments, _context):
        return await st._context_route_service.handle_task_scope_search(arguments)

    st.task_scope_search_handler = task_scope_search_handler


def _register_context_route_tools(st: _SdkRuntimeStackBuild) -> None:
    """登记上下文路由、任务范围搜索与范围更新工具。"""

    st.projected_registrations = (
        *st.projected_registrations,
        ProductToolRegistration(
            name="context_route",
            description=(
                "Commit the Context route for this Run: direct_standalone (no "
                "memory needed), memory_standalone (typed recall), "
                "continue_active (exact current task), resume_existing (exact "
                "task_scope_id from a confirmed task_scope_search candidate; "
                "returns the bounded ResumePackage), or create_new (new "
                "multi-step task; requires title). To continue editing files of a "
                "completed task, use create_new with reuse_workspace_of and its "
                "exact expected_source_hash: a new active Scope receives an explicit "
                "binding to the original single root under current policy. Without "
                "reuse_workspace_of, create_new uses a separate new directory. Search hits never "
                "authorize; only this tool commits a route. When the user names a task that is not "
                "the active one (a search hit with is_active=false), route resume_existing with its "
                "exact task_scope_id: continue_active would bind this Run to the active task, and a "
                "Run binds one scope and can never rebind."
            ),
            input_schema=CONTEXT_ROUTE_SCHEMA,
            handler=st.context_route_handler,
            dispatch_kind="async",
            permission_category="context_route",
            projectless_admission="safe",
            metadata={
                "source": "product-context-route",
                "version": "1",
                "stable_handler_id": "core.context_route.v1",
            },
        ),
        ProductToolRegistration(
            name="task_scope_search",
            description=(
                "Permission-first search over the caller's own archived task "
                "scopes. Returns read-only candidates (title, goal, snippet, "
                "rank); candidates grant no authority and never change the "
                "active task. To continue the Run's current active task you do "
                "not need this tool at all: call "
                "context_route(route=continue_active) directly. Do not repeat a "
                "query that returned zero candidates. "
                "Each hit carries is_active: only the is_active=true hit is what "
                "continue_active would select; a task the user named that comes "
                "back is_active=false must be routed with resume_existing. "
                "For an active task, pass its exact task_scope_id to "
                "context_route(route=resume_existing). For completed candidates, "
                "use create_new with reuse_workspace_of "
                "and expected_source_hash as the first task route in a new Run "
                "to request a new active Scope bound to the original workspace; "
                "do not resume the completed task first when you intend to edit."
            ),
            input_schema=TASK_SCOPE_SEARCH_SCHEMA,
            handler=st.task_scope_search_handler,
            dispatch_kind="async",
            permission_category="task_scope_search",
            projectless_admission="safe",
            metadata={
                "source": "product-context-route",
                "version": "1",
                "stable_handler_id": "core.task_scope_search.v1",
            },
        ),
    )
    # semantic closure Tool (host-composed, direct kernel, always
    # exposed; the Host handler gates scope_unbound / nothing_to_close).

    st._closure_tool_service = _ensure_task_scope_update_service()

    async def task_scope_update_handler(arguments, _context):
        return await st._closure_tool_service.handle_task_scope_update(arguments)

    st.task_scope_update_handler = task_scope_update_handler

    st.projected_registrations = (
        *st.projected_registrations,
        ProductToolRegistration(
            name="task_scope_update",
            description=TASK_SCOPE_UPDATE_DESCRIPTION,
            input_schema=TASK_SCOPE_UPDATE_SCHEMA,
            handler=st.task_scope_update_handler,
            dispatch_kind="async",
            permission_category="task_scope_update",
            projectless_admission="safe",
            metadata={
                "source": "product-task-scope-closure",
                "version": "1",
                "stable_handler_id": "core.task_scope_update.v1",
            },
        ),
    )


def _freeze_catalog_and_authorization(st: _SdkRuntimeStackBuild) -> None:
    """建工具注册与清单，冻结工具目录，建授权策略、授权适配与委派观察者。"""

    st.tools_adapter, st.tool_inventory = build_product_tool_registry(st.projected_registrations)

    st.tool_inventory = extend_product_registry_with_mcp(
        st.tools_adapter,
        st.tool_inventory,
        legacy_registry=live_tool_registry,
        execution_context_getter=st.execution_context_getter,
    )
    st.tools_adapter.bind_run_authorities(st.tool_authorities)
    st.frozen_catalog = _freeze_sdk_catalog(
        st.tools_adapter,
        st.generation,
        visibility_registry=live_tool_registry,
    )
    # Projected MCP handlers still execute through the physical registry.
    # Freeze its exact policy fingerprint into the SDK Run authority so the
    # strict execution-time stale check compares the same policy snapshot.
    st.frozen_catalog["policy_fingerprint"] = deskpet_tool_registry_v2.read_policy_snapshot(
        strict=True
    ).fingerprint
    # memory_recall/memory_search remain registered for explicit product
    # surfaces, but automatic Memory is injected through AgentMemoryPort and
    # those tools are deliberately absent from the model-visible SDK catalog.
    # The per-Run authority must therefore receive the same visible projection,
    # not the larger executor inventory.
    st.visible_tool_names = frozenset(st.frozen_catalog["tool_names"])
    st.sdk_tool_inventory = tuple(
        item for item in st.tool_inventory if str(item.name) in st.visible_tool_names
    )
    if {str(item.name) for item in st.sdk_tool_inventory} != st.visible_tool_names:
        raise RuntimeError("SDK catalog is missing product inventory entries")

    # Build authorization system

    st.authorization_policy = SdkPreparedAuthorizationPolicy(
        st.authorization_runtime,
        st.tool_authorities,
        clock=st.clock,
        initial_policy_generation=st.initial_authorization_policy.generation,
        skill_install_preflight=st.project_skill_install_service,
    )

    # Build authorization adapter
    st.authorization_adapter = ProductAuthorizationAdapter(
        st.repository,
        policy=st.authorization_policy,
        identity_factory=st.authorization_policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            st.product_state_db,
            policy_generation_provider=(st.authorization_policy.current_policy_generation),
        ),
        grant_factory=st.authorization_policy.grant_factory,
        clock=st.clock,
        terminal_lifecycle=st.project_skill_install_service,
    )

    # Build reconciliation adapter
    async def _delegation_observer(effect: Any, _saga: Any) -> Any:

        observed = await st.delegation_service.observe(effect)
        if observed is not None:
            return observed
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN,
            f"product-saga:unknown:{effect.effect_id.value}",
        )

    st._delegation_observer = _delegation_observer


def _prepare_reconciliation_and_gates(st: _SdkRuntimeStackBuild) -> None:
    """建对账、投递、投影泵关闭、上下文来源与效果/读取两道门。"""

    st.reconciliation_adapter = ProductReconciliationAdapter(
        st.repository, observer=st._delegation_observer
    )

    # Build delivery sink: ProductDeliveryAdapter is per-run (needs session/request/run
    # presentation context), so the stack-level delivery port routes through the global
    # _DeliverySink, which dispatches to the adapter registered for each run by
    # _execute_sdk_run via _delivery_adapters.

    st.delivery_adapter = _DeliverySink()

    # F06（2026-09-07）：provider 传输超时后 SDK 把调用记 UNKNOWN、Run 进 waiting 并
    # 挂 provider wait-blocker，等 Host 通过 ProviderReconciliationPort 给可判定结论；
    # 之前这里是 Noop（永远 STILL_UNKNOWN）且无人调用 reconcile_incomplete → Run 永久
    # 停摆。现在：RuntimeReconciliationPort 在 SDK 启动与 runtime.reconcile() 时调
    # provider coordinator 的 reconcile_incomplete，provider 侧用 retry-once 策略。

    st.provider_reconciliation = _ensure_provider_reconciliation()
    st.runtime_reconciliation = ProductRuntimeReconciliation(
        lambda: st.production_ports.get("ports"), st.provider_reconciliation
    )
    host_state.runtime_reconciliation = st.runtime_reconciliation

    st.projection_pump = None

    # Context source/binding tables are owned by the product SessionDB migration
    # chain (023_official_memory_integration_v31.sql).  Keep them in state.db;
    # sdk-product-state.db is a separate authority for authorization sagas and
    # task grants and intentionally does not carry the SessionDB schema.
    st.context_source_repository = ProductContextSourceRepository(st.session_state_db_path)
    st.context_provider = ProductConversationContextProvider(st.context_source_repository)
    # 2026-09-10：Harness SDK 0.7.10 把 ``memory: AgentMemoryPort`` 列为生产组装
    # 必填项。认知记忆 SDK 移除后 Host 交一个**诚实的空记忆端口**：召回返回合法
    # 空结果（不伪造 write_fence / 权威），release 无操作，record 返回合法回执但
    # 不持久化任何记忆。见 ``deskpet/sdk_adapters/null_memory_port.py``。

    st.agent_memory_port = NoMemoryAgentPort()
    service_context.register("sdk_context_source_repository", st.context_source_repository)

    async def close_projection_pump() -> None:
        if st.projection_pump is not None:
            await st.projection_pump.close()

    st.close_projection_pump = close_projection_pump

    # per-effect workspace EffectGate in front of every physical
    # PROJECT_EFFECT dispatch (envelope echo → frozen Run authority → S4
    # verify_task_execution_envelope over the durable v45 route receipt →
    # strict head revision → TaskScope active).  Constructed at stack build so
    # a missing piece fails startup instead of degrading to "no gate".

    if st.tool_authorities is None:
        raise RuntimeError("sdk_effect_gate_composition_missing:tool_authority_registry")
    st.effect_gate = EffectGate(
        binding_store=st._context_route_binding_store(),
        route_ledger=_ContextRouteLedgerStore(_state_db_path),
        scope_store=_GateScopeStore(_state_db_path),
        authority_resolver=st.tool_authorities.resolve,
        exposure_resolver=st.tool_authorities.resolve_exposure,
    )
    service_context.register("sdk_effect_gate", st.effect_gate)

    # F-Z1 (2026-09-09): the read-class file Tools are exposed in a
    # route-capable projectless Run only because every *call* is admitted here
    # — durable ``context_route`` task decision → verified S4 binding root →
    # path containment.  Same composition discipline as the EffectGate: a
    # missing piece fails startup instead of degrading to "no gate".

    # F-Z1b (2026-09-09): a read whose path is outside the task's bound roots
    # but still inside the configured workspace no longer dead-ends — the gate
    # enters the same S4 binding-proposal authority the create_new route uses
    # (manual → the 『项目目录授权』 card, auto → a policy:auto grant), then makes
    # the model re-route once so the write path stays consistent too.
    st.read_gate = WorkspaceReadGate(
        binding_store=st._context_route_binding_store(),
        route_ledger=_ContextRouteLedgerStore(_state_db_path),
        scope_store=_GateScopeStore(_state_db_path),
        authority_resolver=st.tool_authorities.resolve,
        service_factory_getter=lambda: service_context.get("human_memory_host_service_factory"),
        binding_append_getter=lambda: service_context.get("human_memory_binding_append_authority"),
    )
    service_context.register("sdk_workspace_read_gate", st.read_gate)


def _define_production_runtime(st: _SdkRuntimeStackBuild) -> None:
    """定义生产端口工厂与目录、投影泵代理，组装生产运行时与 SDK 运行栈。"""

    def ports_factory(database, uow):
        return _build_production_ports(st, database, uow)

    st.ports_factory = ports_factory

    class _ProductionToolCatalogProxy:
        def _target(self):
            ports = st.production_ports.get("ports")
            if ports is None:
                raise RuntimeError("production tool catalog is not bound")
            return ports.tool_catalog

        def current_generation(self):
            return self._target().current_generation()

        def resolve(self, generation, content_fingerprint):
            return self._target().resolve(generation, content_fingerprint)

    st._ProductionToolCatalogProxy = _ProductionToolCatalogProxy

    class _ProductionProjectionPumpProxy:
        async def start(self):
            if st.projection_pump is None:
                raise RuntimeError("production provider projection pump is not bound")
            started = st.projection_pump.start()
            if inspect.isawaitable(started):
                await started

        async def close(self):
            if st.projection_pump is not None:
                await st.projection_pump.close()

    st._ProductionProjectionPumpProxy = _ProductionProjectionPumpProxy

    st.production_ports: dict[str, Any] = {}
    st.production_tool_catalog = st._ProductionToolCatalogProxy()
    st.production_projection_pump = st._ProductionProjectionPumpProxy()

    def _production_ports_for(uow):
        cached = st.production_ports.get("ports")
        if cached is None:
            cached = st.ports_factory(uow.database, uow)
            st.production_ports["ports"] = cached
        elif cached.react_checkpoint is not uow:
            raise RuntimeError("production runtime transaction owner changed")
        return cached

    st._production_ports_for = _production_ports_for

    def production_runtime_factory(execution_path):

        st.production_ports.clear()
        profiles = {"agent.general": RuntimeProfile("agent.general", "react")}
        runtime_config_kwargs = dict(
            clock=st.clock,
            execution_path=execution_path,
            provider_builder=lambda uow: st._production_ports_for(uow).provider,
            tools_builder=lambda uow: st._production_ports_for(uow).tools,
            delivery_builder=lambda uow: st._production_ports_for(uow).delivery,
            context_builder=lambda _database: st.production_ports["ports"].context,
            context_staging_builder=lambda _database: st.production_ports["ports"].context_staging,
            authorization=st.authorization_adapter,
            tool_reconciliation=st.reconciliation_adapter,
            reconciliation=st.runtime_reconciliation,
            provider_reconciliation=st.provider_reconciliation,
            tool_catalog=st.production_tool_catalog,
            driver=st.driver,
            profiles=profiles,
            memory=st.agent_memory_port,
            memory_ownership=ResourceOwnership.BORROWED,
            context_provider=st.context_provider,
            provider_budget_resolver=st.provider_binding_resolver,
            provider_projection_pump=st.production_projection_pump,
            run_binding=st.tool_authorities,
            structured_message_services=ProductContextAdapter(),
            **_sdk_runtime_authority_bindings(),
            owner_id=f"deskpet-product-sdk-g{st.generation}",
        )
        if "observability_sink" in inspect.signature(ProductionRuntimeConfig).parameters:
            runtime_config_kwargs["observability_sink"] = _sdk_observability.sink
        config = ProductionRuntimeConfig(**runtime_config_kwargs)
        runtime = build_production_runtime(config)
        runtime_snapshot = getattr(runtime, "diagnostics_snapshot", None)
        if callable(runtime_snapshot):
            _sdk_observability.register_snapshot_source("harness", runtime_snapshot)
        return ProductionRuntimeBuild(
            runtime=runtime,
            transaction_owner=st.production_ports["ports"].react_checkpoint,
        )

    st.production_runtime_factory = production_runtime_factory

    # Default pinned wheel, or an explicitly attested development source identity.
    st.stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(_paths.user_data_dir()),
        candidate_identity=build_runtime_identity(),
        dependency_loader=lambda: SdkRuntimeBuildInputs(
            profiles={"agent.general": RuntimeProfile("agent.general", "react")},
            drivers={"react": st.driver},
            ports_factory=st.ports_factory,
            workflow_catalog_digest=st.frozen_catalog["content_fingerprint"],
            owned_resources=(OwnedResourceCloser("provider-http", st.client.aclose),),
            runtime_factory=st.production_runtime_factory,
        ),
        ready_publisher=lambda ready: service_context.register("sdk_runtime_ready", ready),
    )


def _register_runtime_services(st: _SdkRuntimeStackBuild) -> None:
    """登记运行时目录、绑定与授权等服务，核对上下文路由账本并登记运行上下文、决策与任务执行权威。"""

    service_context.register("sdk_runtime_catalog", st.frozen_catalog)
    service_context.register("sdk_provider_binding_resolver", st.provider_binding_resolver)
    service_context.register("sdk_tool_authority_registry", st.tool_authorities)
    service_context.register("sdk_runtime_tool_inventory", st.sdk_tool_inventory)
    service_context.register("sdk_prepared_authorization_policy", st.authorization_policy)

    # S5a — register the three concrete SDK 0.7 authorities.  Missing any
    # prerequisite must fail the stack build (no Noop/fake fallback); the
    # proxies in _sdk_runtime_authority_bindings stay fail-closed otherwise.

    st._state_version = ContextRouteLedgerStore(_state_db_path).user_version()
    if st._state_version < 35:
        # Legacy (pre human-memory epoch) composition keeps the SDK bare
        # context path; the v45 route/snapshot ledger does not exist there and
        # the fail-closed proxies stay unregistered exactly as before S5a.
        logger.info(
            "sdk_context_authorities_skipped_legacy_epoch version=%s",
            st._state_version,
        )
        st.result = st.stack
        st.flow = "stop"
        return
    if st.tool_authorities is None:
        raise RuntimeError("sdk_context_authority_composition_missing:tool_authority_registry")
    st.context_route_ledger = ContextRouteLedgerStore(
        _state_db_path, evidence_ingress=_ensure_evidence_ingress()
    )
    st.context_route_ledger.verify_schema()

    def _authority_ports():
        ports = st.production_ports.get("ports")
        if ports is None:
            raise RuntimeError("sdk_run_context_authority_ports_unbound")
        return ports

    st._authority_ports = _authority_ports

    # 2026-09-10：``reconcile`` 原本是认知记忆运行时的 pending-occurrence 对账，
    # 随 Prospective 一并移除；该参数在下游本就是 Optional。
    st._occurrence_reconcile = None

    # the protected "closure required" instruction of the
    # next Run (pending merge / dirty admission scope) is a production fact, not
    # a test-harness one — the reader is a composition slot (missing → startup fail).
    service_context.register(
        "sdk_run_context_authority",
        _build_run_context_authority(
            state_db_path=_state_db_path,
            ports_resolver=st._authority_ports,
            exposure_resolver=st.tool_authorities.resolve_exposure,
            ledger=st.context_route_ledger,
            reconcile=st._occurrence_reconcile,
            typed_use_authority=st._typed_use_authority,
            occurrence_coordinator=st._occurrence_coordinator,
            configured_workspace_root=st.configured_workspace_root,
        ),
    )
    service_context.register(
        "sdk_runtime_decision_sink",
        ProductRuntimeDecisionSink(
            ledger=st.context_route_ledger, reconcile=st._occurrence_reconcile
        ),
    )
    # PROJECT_EFFECT envelopes resolve their exact single root
    # from the route receipt's immutable binding-set receipt (never the live
    # head, never a default root); zero/multi root is a whole-Run fault whose
    # stable code is memoised for the terminal evidence.
    service_context.register(
        "sdk_task_execution_authority",
        ProductTaskExecutionAuthority(
            root_resolver=BindingRootResolver(st._context_route_binding_store()),
            fault_sink=_ensure_run_fault_memo(),
        ),
    )


@dataclass
class _ProductionPortsBuild:
    """_build_production_ports 的运行状态：原函数的局部变量与参数（切段用）。"""

    catalog_snapshot: Any = None
    child_run_id: Any = None
    context: Any = None
    context_staging: Any = None
    database: Any = None
    delegated_children: Any = None
    durable_catalog_resolver: Any = None
    effects: Any = None
    metadata: Any = None
    parent_run_id: Any = None
    projection_context: Any = None
    provider_port: Any = None
    raw_binding: Any = None
    raw_tool_authority: Any = None
    record: Any = None
    record_state: Any = None
    recoverable: Any = None
    restored: Any = None
    restored_binding: Any = None
    st: Any = None
    start: Any = None
    start_input: Any = None
    uow: Any = None


def _build_production_ports(st, database, uow):
    """_build_product_sdk_runtime_stack.ports_factory 闭包的函数体（闭包读的外层变量作为参数传入）。"""
    pp = _ProductionPortsBuild(st=st, database=database, uow=uow)
    pp.context = SqliteContextPort(pp.database, clock=pp.st.clock)
    host_state.sdk_context_port = pp.context
    pp.st.delegation_uow["uow"] = pp.uow

    pp.recoverable = (
        *pp.uow.list_recoverable_root_runs(),
        *pp.uow.list_recoverable_child_runs(),
    )
    # F06（2026-09-07）：SDK 的 recoverable 列表不含 waiting；但被 UNKNOWN provider
    # 调用挂住的 waiting Run 会在 SDK 启动 reconcile 后立刻重驱，届时 ReAct driver
    # 需要 resolve_exposure——不先恢复其工具授权就会 sdk_runtime_tool_exposure_unavailable
    # → FAILED。这里把这类 waiting Run 一并按历史授权恢复（lease_state=waiting）。
    pp.recoverable = (
        *pp.recoverable,
        *waiting_runs_blocked_on_provider(
            pp.uow, exclude={str(record.run_id) for record in pp.recoverable}
        ),
    )
    # 停在委派调用上的等待运行也要先恢复绑定与授权（启动后会被唤醒）。
    pp.recoverable = (
        *pp.recoverable,
        *waiting_runs_blocked_on_delegation(
            pp.uow, exclude={str(record.run_id) for record in pp.recoverable}
        ),
    )
    pp.delegated_children: list[tuple[str, str]] = []
    for pp.record in pp.recoverable:
        pp.start = pp.uow.read_start_snapshot(str(pp.record.run_id))
        pp.start_input = pp.start.get("input") if isinstance(pp.start, Mapping) else None
        pp.metadata = (
            pp.start_input.get("context_metadata") if isinstance(pp.start_input, Mapping) else None
        )
        pp.raw_binding = (
            pp.metadata.get("run_binding") if isinstance(pp.metadata, Mapping) else None
        )
        if isinstance(pp.raw_binding, Mapping):
            pp.restored = SdkRunBindingV1.from_record(pp.raw_binding)
            pp.st.provider_binding_resolver.register(pp.restored)
            pp.record_state = str(
                getattr(getattr(pp.record, "state", None), "value", None)
                or getattr(pp.record, "state", "")
            ).lower()
            if pp.record_state == "waiting":
                pp.st.provider_binding_resolver.mark_waiting(pp.restored.run_id)
            if is_delegated_child_start(pp.start):
                # 委派子运行不是主对话运行：不建界面投递、不占根映射、不挂
                # 恢复监视器（否则子运行回答会写进主会话、"停止"会取消到它）。
                # 授权照常按历史恢复（下面），由委派服务重新接管监视。
                pp.delegated_children.append(
                    (str(pp.record.run_id), str(pp.metadata.get("parent_run_id") or ""))
                )
            elif not _restore_sdk_delivery_route(pp.record, pp.start, pp.st.session_db):
                logger.warning(
                    "sdk_recovery_delivery_route_missing",
                    sdk_run_id=str(pp.record.run_id),
                    state=pp.record_state,
                )
    pp.effects = ProductEffectExecutor(
        clock=pp.st.clock,
        uow=pp.uow,
        registry=pp.st.tools_adapter,  # tools_adapter is already a ToolRegistry
        authorization=pp.st.authorization_adapter,
        reconciliation=pp.st.reconciliation_adapter,
        foreground_admission=_ensure_foreground_effect_gate(),
        effect_gate=pp.st.effect_gate,
        read_gate=pp.st.read_gate,
        evidence_ingress=_ensure_evidence_ingress(),
    )

    pp.durable_catalog_resolver = DurableToolCatalogResolver(pp.uow)
    pp.catalog_snapshot = pp.uow.put_tool_catalog_snapshot(
        pp.effects.provider_tool_specs(tuple(pp.st.frozen_catalog["tool_names"]))
    )
    pp.st.frozen_catalog["generation"] = pp.catalog_snapshot.generation
    pp.st.frozen_catalog["content_fingerprint"] = pp.catalog_snapshot.content_fingerprint
    _restore_recoverable_tool_authorities(pp)
    for pp.child_run_id, pp.parent_run_id in pp.delegated_children:
        if pp.parent_run_id:
            pp.st.delegation_service.adopt_recovered(pp.child_run_id, pp.parent_run_id)
    # 重启前停在委派调用上的父运行：运行时就绪后写对账结论并唤醒。
    pp.st.delegation_service.start_recovery()
    pp.provider_port = ProductProviderInvocationCoordinator(
        uow=pp.uow,
        resolver=pp.st.provider_binding_resolver,
        evidence_ingress=_ensure_evidence_ingress(),
        context_use_authority=pp.st._typed_use_authority,
        typed_terminal=pp.st._typed_use_authority,
        clock=pp.st.clock,
    )
    if pp.st.projection_pump is None:

        def projection_context(receipt: Any) -> ProviderProjectionContextV1:
            start = pp.uow.read_start_snapshot(str(receipt.run_id))
            if not isinstance(start, Mapping):
                raise RuntimeError("sdk_provider_projection_start_snapshot_missing")
            start_input = start.get("input")
            metadata = (
                start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
            )
            if not isinstance(metadata, Mapping):
                raise RuntimeError("sdk_provider_projection_context_missing")
            return ProviderProjectionContextV1.from_value(metadata)

        pp.projection_context = projection_context

        pp.st.projection_pump = SdkProviderProjectionPump(
            pp.uow,
            pp.st.session_db,
            context_resolver=pp.projection_context,
            on_committed=lambda context: _broadcast_context_usage_snapshot(
                pp.st.session_db, context.session_id
            ),
        )
        pp.st.projection_pump.trigger()

    pp.context_staging = ContextStagingRepository(pp.database)
    service_context.register("sdk_context_staging", pp.context_staging)
    return RuntimePorts(
        provider=pp.provider_port,
        tools=pp.effects,
        authorization=pp.st.authorization_adapter,
        context=pp.context,
        delivery=DeliveryDispatcher(pp.uow, {"product": pp.st.delivery_adapter}, clock=pp.st.clock),
        tool_reconciliation=pp.st.reconciliation_adapter,
        reconciliation=pp.st.runtime_reconciliation,
        provider_reconciliation=pp.st.provider_reconciliation,
        react_checkpoint=pp.uow,
        tool_catalog=pp.durable_catalog_resolver,
        owner_id=f"deskpet-product-sdk-g{pp.st.generation}",
        agent_memory=pp.st.agent_memory_port,
        context_provider=pp.st.context_provider,
        context_staging=pp.context_staging,
    )


def _restore_recoverable_tool_authorities(pp: _ProductionPortsBuild) -> None:
    """逐个可恢复运行：从启动快照恢复运行绑定与工具授权。"""

    for pp.record in pp.recoverable:
        pp.start = pp.uow.read_start_snapshot(str(pp.record.run_id))
        pp.start_input = pp.start.get("input") if isinstance(pp.start, Mapping) else None
        pp.metadata = (
            pp.start_input.get("context_metadata") if isinstance(pp.start_input, Mapping) else None
        )
        if not isinstance(pp.metadata, Mapping):
            continue
        pp.raw_binding = pp.metadata.get("run_binding")
        pp.raw_tool_authority = pp.metadata.get("tool_authority")
        if not isinstance(pp.raw_binding, Mapping) or not isinstance(
            pp.raw_tool_authority, Mapping
        ):
            logger.warning(
                "sdk_recovery_tool_authority_missing",
                sdk_run_id=str(pp.record.run_id),
            )
            continue
        pp.restored_binding = SdkRunBindingV1.from_record(pp.raw_binding)
        pp.record_state = str(
            getattr(getattr(pp.record, "state", None), "value", None)
            or getattr(pp.record, "state", "")
        ).lower()
        try:
            if pp.record_state == "waiting":
                pp.st.tool_authorities.restore_waiting_run(
                    run_start_record=pp.raw_tool_authority,
                    run_binding=pp.restored_binding,
                    catalog_resolver=pp.durable_catalog_resolver,
                )
            else:
                pp.st.tool_authorities.restore_run(
                    run_start_record=pp.raw_tool_authority,
                    run_binding=pp.restored_binding,
                    catalog_resolver=pp.durable_catalog_resolver,
                    lease_state="active",
                )
        except SdkToolAuthorityMigrationUnavailable as exc:
            _isolate_unrestorable_sdk_tool_authority(
                sdk_run_id=str(pp.record.run_id),
                metadata=pp.metadata,
                provider_binding_resolver=pp.st.provider_binding_resolver,
                tool_authorities=pp.st.tool_authorities,
                error=exc,
            )


# S5b AC-6①: every authority the SDK runtime composition must carry; a missing
# one is a startup failure (raised from `_activate_product_sdk_runtime`), never
# a Noop/fake fallback.  Kept as data so the composition test can construct the
# same check against a real service_context instead of grepping this file.
SDK_COMPOSITION_SLOTS: tuple[str, ...] = (
    "sdk_provider_binding_resolver",
    "sdk_tool_authority_registry",
    "sdk_run_context_authority",
    # 2026-09-10：``sdk_typed_context_use_authority``（类型化 Context-use 记账）
    # 随认知记忆 SDK 移除，恒为 None，不再是必备槽位。
    "sdk_runtime_decision_sink",
    "sdk_task_execution_authority",
    "sdk_effect_gate",
    "sdk_closure_instruction_reader",
)


def _assert_sdk_composition_slots(context=None) -> None:  # type: ignore[no-untyped-def]
    registry = service_context if context is None else context
    for slot in SDK_COMPOSITION_SLOTS:
        if registry.get(slot) is None:
            raise RuntimeError(f"sdk_context_authority_composition_missing:{slot}")
