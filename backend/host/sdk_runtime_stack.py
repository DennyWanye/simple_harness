"""SDK 运行栈组装（整段搬家，拆函数在后面的阶段）与装配槽位检查"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Mapping
from dataclasses import replace as dataclass_replace
from pathlib import Path

import paths as _paths
from deskpet.capabilities.skill_install import (
    AuthorizedPreflightReceiptResolver,
    ProjectSkillInstallError,
)
from deskpet.execution.primary_context_pages import PrimaryContextPageReader
from deskpet.execution.termination_budget import resolve_max_wall_seconds
from deskpet.memory.evidence_kind_schema import initialize_evidence_kind_state_db
from deskpet.memory.identity import ValidatedLocalMemoryIdentityAuthority
from deskpet.operation_audit.page_in_receipts import ContextPageInReceiptLedger
from deskpet.orchestration.chat_tool import (
    METHOD_LIBRARY_DESCRIPTION,
    METHOD_LIBRARY_SCHEMA,
    METHOD_LIBRARY_TOOL_NAME,
    MISSION_AMEND_DESCRIPTION,
    MISSION_AMEND_SCHEMA,
    MISSION_AMEND_TOOL_NAME,
    MISSION_LIST_DESCRIPTION,
    MISSION_LIST_SCHEMA,
    MISSION_LIST_TOOL_NAME,
    MISSION_SOURCE_UPDATE_DESCRIPTION,
    MISSION_SOURCE_UPDATE_SCHEMA,
    MISSION_SOURCE_UPDATE_TOOL_NAME,
    MISSION_START_DESCRIPTION,
    MISSION_START_SCHEMA,
    MISSION_START_TOOL_NAME,
    MISSION_STATUS_DESCRIPTION,
    MISSION_STATUS_SCHEMA,
    MISSION_STATUS_TOOL_NAME,
    MissionStartRefused,
    amend_mission,
    method_library,
    mission_list,
    mission_status,
    start_mission,
    update_mission_source,
)
from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.provider_extra_headers import install_httpx_extra_headers_hook
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
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
    SdkPreparedAuthorizationPolicy,
    SdkRuntimeCapabilityBridgeAdapter,
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


async def _build_product_sdk_runtime_stack(
    generation: int,
    *,
    clock=time.time,
    configured_workspace_root=None,
):
    """Build SDK Runtime Stack with product adapters (Slice C ingress)."""

    # Verify required services
    session_db = service_context.get("session_db")
    capability_platform = service_context.get("capability_platform")
    if session_db is None or capability_platform is None:
        raise RuntimeError("SDK Runtime requires session_db and capability_platform")
    session_state_db_path = Path(getattr(session_db, "db_path", _state_db_path))
    memory_identity_authority = service_context.get("memory_identity_authority")
    if memory_identity_authority is None:
        memory_identity_authority = ValidatedLocalMemoryIdentityAuthority(
            session_state_db_path,
            user_data_dir=_paths.user_data_dir(),
        )
        service_context.register("memory_identity_authority", memory_identity_authority)
    memory_identity_resolver = memory_identity_authority.resolver
    service_context.register("memory_identity_resolver", memory_identity_resolver)
    # The runtime is stable across Provider mutations.  A physical Provider is
    # constructed only after a Run freezes its own Session binding.
    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        raise RuntimeError("SDK Runtime requires provider_registry")

    import httpx

    # Grok Build lane (2026-09-16): SDK provider builds its own headers, so
    # endpoint-specific ones (llm_runtime.json extra_headers) ride a hook.
    client = install_httpx_extra_headers_hook(httpx.AsyncClient())
    budget_policy = BudgetPolicy()
    provider_binding_resolver = _ProductSdkProviderBindingResolver(provider_registry, client)

    # Build tool adapter

    # Build tool catalog dependencies
    todo_session_db = service_context.get("session_db")
    workflow_service = service_context.get("workflow_service")
    context_page_store = service_context.get("context_page_in_store")
    search_gateway = service_context.get("search_gateway")
    authorization_runtime = service_context.get("authorization_runtime")
    capability_store = service_context.get("capability_store")
    capability_scope_store = service_context.get("tool_capability_scope_store")
    if authorization_runtime is None or capability_store is None or capability_scope_store is None:
        raise RuntimeError("SDK Runtime requires prepared authorization authority")
    initial_authorization_policy = await capability_store.get_policy_state()

    skill_projection = service_context.get("managed_skill_discovery_projection")
    skill_metas = tuple(skill_projection.list_metas()) if skill_projection is not None else ()
    workflow_metadata = build_product_workflow_registrations(
        generation=generation,
        transaction_owner=object(),
    )
    resource_records = ProductCapabilityCatalogSourceAdapter().sdk_resource_records(
        skills=skill_metas,
        workflows=workflow_metadata,
    )
    tool_authorities = SdkRunToolAuthorityRegistry(
        scope_store=capability_scope_store,
        resource_records=resource_records,
        workspace_identity_validator=getattr(
            service_context.get("project_binding_service"),
            "validate_workspace_identity",
            None,
        ),
        run_fault_sink=_ensure_run_fault_memo(),
    )
    tool_authorities.add_terminal_listener(
        lambda authority: _ensure_run_route_state_memo().release(authority.run_id)
    )
    # 事件 X-3：原生按站点内存探针。默认关闭 —— `build_memory_probe` 在
    # `SIMPLEHARNESS_MEMORY_PROBE` 没显式打开时返回 None，这里连监听器都不注册，
    # 也不会启动 tracemalloc / import psutil / 碰 gc.get_objects()。

    _memory_probe = _install_memory_probe(
        registry=tool_authorities,
        snapshot_dir=_paths.user_data_dir() / "memory-probe",
        emit=lambda fields: logger.info("memory.probe", **dict(fields)),
    )
    if _memory_probe is not None:
        logger.info(
            "memory.probe.started",
            every=_memory_probe.every,
            snapshot_dir=str(_memory_probe.snapshot_dir),
        )
    project_bindings = service_context.get("project_binding_service")
    if project_bindings is not None:
        sdk_execution_db = ProductRuntimePathsAdapter(_paths.user_data_dir()).execution_database

        async def _has_durable_sdk_run_start(run_id: str) -> bool:
            return await asyncio.to_thread(durable_sdk_run_start_exists, sdk_execution_db, run_id)

        reconciled_project_claims = await project_bindings.reconcile_orphan_run_claims(
            _has_durable_sdk_run_start
        )
        if any(reconciled_project_claims.values()):
            logger.warning("project_run_claims_reconciled %s", reconciled_project_claims)

        def _release_project_run(authority: Any) -> None:
            asyncio.create_task(project_bindings.release_run(authority.run_id))

        tool_authorities.add_terminal_listener(_release_project_run)

    _skill_tools.bind(
        skill_resolver=SdkThenLegacyFrozenSkillResolver(
            authorities=tool_authorities,
            snapshot_resolver=service_context.get("frozen_skill_instruction_resolver"),
            legacy_resolver=service_context.get("legacy_frozen_skill_instruction_resolver"),
        )
    )
    deskpet_tool_registry_v2.set_dynamic_capability_admission_provider(
        tool_authorities.validate_runtime_tool_admission
    )

    # C10-13 缺陷：这里不显式给 max_wall_seconds 就静默回落 SDK 默认 900s，
    # 恰好等于语料批次的外部 SIGTERM deadline，外层先赢 → 连失败终态回执都
    # 没有（NO_PACKET）。墙钟限额必须显式、可配置，并严格早于任何外层看门狗。
    # 完整的层级不变式见 deskpet/execution/termination_budget.py。
    _max_wall_seconds = resolve_max_wall_seconds(config.raw)
    logger.info(
        "react_termination_limits",
        max_turns=25,
        max_tool_calls=50,
        max_consecutive_same_tool=10,
        max_wall_seconds=_max_wall_seconds,
    )
    driver = build_react_driver(
        clock=clock,
        # SDK H079 的重复键是工具名 + canonical arguments hash；10 限制的是
        # 同名同参的连续调用。改变参数会重置 streak，不是同一工具名总计10次。
        # 25轮/50工具的持久累计计数跨授权恢复保留；外部测试限时另行记录。
        limits=TerminationLimits(
            max_turns=25,
            max_tool_calls=50,
            max_consecutive_same_tool=10,
            max_wall_seconds=_max_wall_seconds,
        ),
        budget_policy=budget_policy,
        estimator=None,
        tool_exposure_resolver=tool_authorities.resolve_exposure,
    )

    verification_driver_factory = service_context.get("skill_install_verification_driver_factory")
    if verification_driver_factory is None:
        raise RuntimeError("Skill verification driver factory is unavailable")

    host_control_driver = ProductRootDriverRouter(
        react_driver=driver,
        attempt_resolver=SkillInstallVerificationAttemptResolver(capability_store),
        verification_driver_factory=verification_driver_factory,
    )
    driver = StartModeDriverRouter(ordinary=driver, host_control=host_control_driver)

    def execution_context_getter():

        tool_context = active_product_tool_context()
        run_id = tool_context.run_id.value
        effect = getattr(tool_context, "effect_id", None)
        effect_id = str(getattr(effect, "value", effect) or "")
        if not effect_id:
            raise RuntimeError("SDK product Tool effect identity is unavailable")

        base = tool_authorities.resolve(run_id).execution_context(
            call_id=active_product_tool_call_id().value,
            effect_id=effect_id,
        )
        return project_tool_execution_context(base, tool_context)

    capability_bridge = SdkRuntimeCapabilityBridgeAdapter(
        tool_authorities,
        execution_context_getter,
        route_state_memo=_ensure_run_route_state_memo(),
    )

    # NEXT-TG-1.0 §9 收口第 4 项：五个委派工具跑在真实 SDK 子运行上（DETACHED，
    # 授权 = 父目录只读子集，每个子运行一个监视器负责收尾与取消）。执行账本在
    # ports_factory 里才拿得到，这里先放一个占位。

    delegation_uow: dict[str, Any] = {}
    delegation_service = ProductDelegationService(
        uow_getter=lambda: delegation_uow.get("uow"),
        runtime_getter=lambda: (
            host_state.sdk_ingress.require_ready().runtime
            if host_state.sdk_ingress is not None
            else None
        ),
        tool_authorities=tool_authorities,
        binding_resolver=provider_binding_resolver,
        context_port_getter=lambda: host_state.sdk_context_port,
        on_parent_woken=lambda run_id: _ensure_sdk_recovery_watcher(run_id),
        # 同步等待不能拖过父运行自己的墙钟上限。
        wait_seconds=min(900.0, float(_max_wall_seconds) * 0.8),
        clock=clock,
    )

    dependencies = ToolCatalogDependencies(
        todo_session_db=todo_session_db,
        workflow_service_provider=lambda: workflow_service,
        context_page_store=context_page_store,
        execution_context_getter=execution_context_getter,
        capability_bridge_service=capability_bridge,
        search_gateway=search_gateway,
        delegation_service_provider=lambda: delegation_service,
    )

    # Authorization state must exist before the Skill install registration is
    # frozen, because its physical handler resolves only durable Product saga
    # receipts and never accepts a model-provided approval token.

    sdk_product_state_db_path = _paths.user_data_dir() / "data" / "sdk-product-state.db"
    sdk_product_state_db_path.parent.mkdir(parents=True, exist_ok=True)
    product_state_db = ProductStateDatabase(sdk_product_state_db_path)
    product_state_db.initialize()
    repository = AuthorizationSagaRepository(
        product_state_db, owner_id=f"sdk-runtime-g{generation}"
    )
    project_skill_install_service = service_context.get("project_skill_install_service")
    if project_skill_install_service is None:
        raise RuntimeError("SDK Runtime requires ProjectSkillInstallService")
    skill_install_receipts = AuthorizedPreflightReceiptResolver(capability_store, repository)

    async def skill_install_handler(_arguments, _context):

        trusted = execution_context_getter()
        authority = tool_authorities.resolve(trusted.run_id)
        try:
            receipt = await skill_install_receipts.resolve_chat(
                context=trusted, principal_id=authority.principal_id
            )
            return await project_skill_install_service.confirm_authorized(receipt)
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

    catalog = build_explicit_product_tool_catalog(dependencies)

    projected_registrations = tuple(
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
        for item in catalog.registrations
    )
    projected_registrations = (
        *projected_registrations,
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
            handler=skill_install_handler,
            dispatch_kind="async",
            permission_category="skill_install",
            metadata={
                "source": "product-skill-install",
                "version": "2",
                "stable_handler_id": "core.skill_install.v2",
            },
        ),
    )
    # S5a — five-route Context authority tools (host-composed like skill_install).

    def _context_route_binding_store():

        return WorkspaceBindingAuthorityStore(
            _state_db_path, configured_workspace_root=configured_workspace_root
        )

    # 2026-09-10：认知记忆 SDK 移除，``HumanMemoryV7Runtime``（分析批 / 类型化召回 /
    # Procedure / Prospective 的宿主运行时）随之下线，``human_memory_v7_runtime``
    # 槽位不再注册。

    context_page_store.primary_reader = PrimaryContextPageReader(
        _state_db_path,
        stack_getter=lambda: host_state.sdk_runtime_stack,
        policy_factory=_primary_history_policy,
    )
    # G1: durable payload-free page-in issue/consume receipts in the audit sidecar.

    context_page_store.receipt_ledger = ContextPageInReceiptLedger(
        _state_db_path.with_name("operation-audit.db"),
        clock=clock,
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
    _typed_use_authority = None
    service_context.register("sdk_typed_context_use_authority", None)
    _occurrence_coordinator = None
    service_context.register("prospective_occurrence_coordinator", None)

    scope_disclosure = ScopeDisclosureReader(
        _state_db_path,
        stack_getter=lambda: host_state.sdk_runtime_stack,
        policy_factory=_primary_history_policy,
    )
    _context_route_service = ContextRouteToolService(
        service_factory_getter=lambda: service_context.get("human_memory_host_service_factory"),
        binding_store_factory=_context_route_binding_store,
        binding_append_getter=lambda: service_context.get("human_memory_binding_append_authority"),
        ledger=_ContextRouteLedgerStore(
            _state_db_path, evidence_ingress=_ensure_evidence_ingress()
        ),
        tool_context_getter=active_product_tool_context,
        recall_executor=None,
        typed_use_authority=None,
        scope_disclosure_reader=scope_disclosure.read,
        producer_dependencies_reader=scope_disclosure.producer_dependencies,
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
            lambda sdk_run_id, effect_id: authorization_policy.user_confirmed(sdk_run_id, effect_id)
        ),
    )

    async def context_route_handler(arguments, _context):
        return await _context_route_service.handle_context_route(arguments)

    async def task_scope_search_handler(arguments, _context):
        return await _context_route_service.handle_task_scope_search(arguments)

    projected_registrations = (
        *projected_registrations,
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
            handler=context_route_handler,
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
            handler=task_scope_search_handler,
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
    # S5b Task 3 — semantic closure Tool (host-composed, direct kernel, always
    # exposed; the Host handler gates scope_unbound / nothing_to_close).

    _closure_tool_service = _ensure_task_scope_update_service()

    async def task_scope_update_handler(arguments, _context):
        return await _closure_tool_service.handle_task_scope_update(arguments)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name="task_scope_update",
            description=TASK_SCOPE_UPDATE_DESCRIPTION,
            input_schema=TASK_SCOPE_UPDATE_SCHEMA,
            handler=task_scope_update_handler,
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
    # NEXT-TG-1.0 §9：主 Agent 在聊天里发起后台任务——与任务页同一个 create_mission。

    async def mission_start_handler(arguments, _context):

        try:
            value = start_mission(
                lambda: service_context.get("orchestration"),
                arguments,
                run_id=str(_context.run_id),
                call_id=str(_context.call_id or _context.request_id),
            )
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=MISSION_START_TOOL_NAME,
            description=MISSION_START_DESCRIPTION,
            input_schema=MISSION_START_SCHEMA,
            handler=mission_start_handler,
            dispatch_kind="async",
            permission_category="mission_start",
            projectless_admission="safe",
            metadata={
                "source": "product-mission-start",
                "version": "1",
                "stable_handler_id": "core.mission_start.v1",
            },
        ),
    )
    # 2026-09-29：主 Agent 读后台任务进度（只读；确认与批准只能由人在卡片/任务页上点）。

    async def mission_status_handler(arguments, _context):

        try:
            value = mission_status(lambda: service_context.get("orchestration"), arguments)
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=MISSION_STATUS_TOOL_NAME,
            description=MISSION_STATUS_DESCRIPTION,
            input_schema=MISSION_STATUS_SCHEMA,
            handler=mission_status_handler,
            dispatch_kind="async",
            permission_category="mission_status",
            projectless_admission="safe",
            metadata={
                "source": "product-mission-status",
                "version": "1",
                "stable_handler_id": "core.mission_status.v1",
            },
        ),
    )
    # 阶段 E：主 Agent 替用户改后台任务的要求（与建任务同一条把关规矩）。

    async def mission_amend_handler(arguments, _context):

        try:
            value = amend_mission(
                lambda: service_context.get("orchestration"),
                arguments,
                run_id=str(_context.run_id),
                call_id=str(_context.call_id or _context.request_id),
                permission_mode=str(getattr(_context, "permission_mode", "") or ""),
            )
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=MISSION_AMEND_TOOL_NAME,
            description=MISSION_AMEND_DESCRIPTION,
            input_schema=MISSION_AMEND_SCHEMA,
            handler=mission_amend_handler,
            dispatch_kind="async",
            permission_category="mission_amend",
            projectless_admission="safe",
            metadata={
                "source": "product-mission-amend",
                "version": "1",
                "stable_handler_id": "core.mission_amend.v1",
            },
        ),
    )
    # 2026-10-05：主 Agent 查有哪些后台任务（任务页建的它也要找得到）。

    async def mission_list_handler(arguments, _context):

        try:
            value = mission_list(lambda: service_context.get("orchestration"), arguments)
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=MISSION_LIST_TOOL_NAME,
            description=MISSION_LIST_DESCRIPTION,
            input_schema=MISSION_LIST_SCHEMA,
            handler=mission_list_handler,
            dispatch_kind="async",
            permission_category="mission_list",
            projectless_admission="safe",
            metadata={
                "source": "product-mission-list",
                "version": "1",
                "stable_handler_id": "core.mission_list.v1",
            },
        ),
    )
    # 2026-10-05：主 Agent 替用户把任务的资料换成新版本（变更仍等人批准）。

    async def mission_source_update_handler(arguments, _context):

        try:
            value = update_mission_source(
                lambda: service_context.get("orchestration"),
                arguments,
                run_id=str(_context.run_id),
                call_id=str(_context.call_id or _context.request_id),
            )
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=MISSION_SOURCE_UPDATE_TOOL_NAME,
            description=MISSION_SOURCE_UPDATE_DESCRIPTION,
            input_schema=MISSION_SOURCE_UPDATE_SCHEMA,
            handler=mission_source_update_handler,
            dispatch_kind="async",
            permission_category="mission_source_update",
            projectless_admission="safe",
            metadata={
                "source": "product-mission-source-update",
                "version": "1",
                "stable_handler_id": "core.mission_source_update.v1",
            },
        ),
    )
    # 阶段 C3：主 Agent 替用户查看全库做法、按用户的话退役一条。

    async def method_library_handler(arguments, _context):

        try:
            value = method_library(
                lambda: service_context.get("orchestration"),
                arguments,
                run_id=str(_context.run_id),
                call_id=str(_context.call_id or _context.request_id),
            )
        except MissionStartRefused as refused:
            return ToolResult.failed(
                _context.call_id, refused.code, str(refused), retryable=refused.retryable
            )
        return ToolResult.succeeded(_context.call_id, value)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name=METHOD_LIBRARY_TOOL_NAME,
            description=METHOD_LIBRARY_DESCRIPTION,
            input_schema=METHOD_LIBRARY_SCHEMA,
            handler=method_library_handler,
            dispatch_kind="async",
            permission_category="method_library",
            projectless_admission="safe",
            metadata={
                "source": "product-method-library",
                "version": "1",
                "stable_handler_id": "core.method_library.v1",
            },
        ),
    )
    tools_adapter, tool_inventory = build_product_tool_registry(projected_registrations)

    tool_inventory = extend_product_registry_with_mcp(
        tools_adapter,
        tool_inventory,
        legacy_registry=live_tool_registry,
        execution_context_getter=execution_context_getter,
    )
    tools_adapter.bind_run_authorities(tool_authorities)
    frozen_catalog = _freeze_sdk_catalog(
        tools_adapter,
        generation,
        visibility_registry=live_tool_registry,
    )
    # Projected MCP handlers still execute through the physical registry.
    # Freeze its exact policy fingerprint into the SDK Run authority so the
    # strict execution-time stale check compares the same policy snapshot.
    frozen_catalog["policy_fingerprint"] = deskpet_tool_registry_v2.read_policy_snapshot(
        strict=True
    ).fingerprint
    # memory_recall/memory_search remain registered for explicit product
    # surfaces, but automatic Memory is injected through AgentMemoryPort and
    # those tools are deliberately absent from the model-visible SDK catalog.
    # The per-Run authority must therefore receive the same visible projection,
    # not the larger executor inventory.
    visible_tool_names = frozenset(frozen_catalog["tool_names"])
    sdk_tool_inventory = tuple(
        item for item in tool_inventory if str(item.name) in visible_tool_names
    )
    if {str(item.name) for item in sdk_tool_inventory} != visible_tool_names:
        raise RuntimeError("SDK catalog is missing product inventory entries")

    # Build authorization system

    authorization_policy = SdkPreparedAuthorizationPolicy(
        authorization_runtime,
        tool_authorities,
        clock=clock,
        initial_policy_generation=initial_authorization_policy.generation,
        skill_install_preflight=project_skill_install_service,
    )

    # Build authorization adapter
    authorization_adapter = ProductAuthorizationAdapter(
        repository,
        policy=authorization_policy,
        identity_factory=authorization_policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            product_state_db,
            policy_generation_provider=(authorization_policy.current_policy_generation),
        ),
        grant_factory=authorization_policy.grant_factory,
        clock=clock,
        terminal_lifecycle=project_skill_install_service,
    )

    # Build reconciliation adapter
    async def _delegation_observer(effect: Any, _saga: Any) -> Any:

        observed = await delegation_service.observe(effect)
        if observed is not None:
            return observed
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN,
            f"product-saga:unknown:{effect.effect_id.value}",
        )

    reconciliation_adapter = ProductReconciliationAdapter(repository, observer=_delegation_observer)

    # Build delivery sink: ProductDeliveryAdapter is per-run (needs session/request/run
    # presentation context), so the stack-level delivery port routes through the global
    # _DeliverySink, which dispatches to the adapter registered for each run by
    # _execute_sdk_run via _delivery_adapters.

    delivery_adapter = _DeliverySink()

    # F06（2026-09-07）：provider 传输超时后 SDK 把调用记 UNKNOWN、Run 进 waiting 并
    # 挂 provider wait-blocker，等 Host 通过 ProviderReconciliationPort 给可判定结论；
    # 之前这里是 Noop（永远 STILL_UNKNOWN）且无人调用 reconcile_incomplete → Run 永久
    # 停摆。现在：RuntimeReconciliationPort 在 SDK 启动与 runtime.reconcile() 时调
    # provider coordinator 的 reconcile_incomplete，provider 侧用 retry-once 策略。

    provider_reconciliation = _ensure_provider_reconciliation()
    runtime_reconciliation = ProductRuntimeReconciliation(
        lambda: production_ports.get("ports"), provider_reconciliation
    )
    host_state.runtime_reconciliation = runtime_reconciliation

    projection_pump = None

    # Context source/binding tables are owned by the product SessionDB migration
    # chain (023_official_memory_integration_v31.sql).  Keep them in state.db;
    # sdk-product-state.db is a separate authority for authorization sagas and
    # task grants and intentionally does not carry the SessionDB schema.
    context_source_repository = ProductContextSourceRepository(session_state_db_path)
    context_provider = ProductConversationContextProvider(context_source_repository)
    # 2026-09-10：Harness SDK 0.7.10 把 ``memory: AgentMemoryPort`` 列为生产组装
    # 必填项。认知记忆 SDK 移除后 Host 交一个**诚实的空记忆端口**：召回返回合法
    # 空结果（不伪造 write_fence / 权威），release 无操作，record 返回合法回执但
    # 不持久化任何记忆。见 ``deskpet/sdk_adapters/null_memory_port.py``。

    agent_memory_port = NoMemoryAgentPort()
    service_context.register("sdk_context_source_repository", context_source_repository)

    async def close_projection_pump() -> None:
        if projection_pump is not None:
            await projection_pump.close()

    # S5b Task 1: per-effect workspace EffectGate in front of every physical
    # PROJECT_EFFECT dispatch (envelope echo → frozen Run authority → S4
    # verify_task_execution_envelope over the durable v45 route receipt →
    # strict head revision → TaskScope active).  Constructed at stack build so
    # a missing piece fails startup instead of degrading to "no gate".

    if tool_authorities is None:
        raise RuntimeError("sdk_effect_gate_composition_missing:tool_authority_registry")
    effect_gate = EffectGate(
        binding_store=_context_route_binding_store(),
        route_ledger=_ContextRouteLedgerStore(_state_db_path),
        scope_store=_GateScopeStore(_state_db_path),
        authority_resolver=tool_authorities.resolve,
        exposure_resolver=tool_authorities.resolve_exposure,
    )
    service_context.register("sdk_effect_gate", effect_gate)

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
    read_gate = WorkspaceReadGate(
        binding_store=_context_route_binding_store(),
        route_ledger=_ContextRouteLedgerStore(_state_db_path),
        scope_store=_GateScopeStore(_state_db_path),
        authority_resolver=tool_authorities.resolve,
        service_factory_getter=lambda: service_context.get("human_memory_host_service_factory"),
        binding_append_getter=lambda: service_context.get("human_memory_binding_append_authority"),
    )
    service_context.register("sdk_workspace_read_gate", read_gate)

    def ports_factory(database, uow):
        nonlocal projection_pump
        context = SqliteContextPort(database, clock=clock)
        host_state.sdk_context_port = context
        delegation_uow["uow"] = uow

        recoverable = (
            *uow.list_recoverable_root_runs(),
            *uow.list_recoverable_child_runs(),
        )
        # F06（2026-09-07）：SDK 的 recoverable 列表不含 waiting；但被 UNKNOWN provider
        # 调用挂住的 waiting Run 会在 SDK 启动 reconcile 后立刻重驱，届时 ReAct driver
        # 需要 resolve_exposure——不先恢复其工具授权就会 sdk_runtime_tool_exposure_unavailable
        # → FAILED。这里把这类 waiting Run 一并按历史授权恢复（lease_state=waiting）。
        recoverable = (
            *recoverable,
            *waiting_runs_blocked_on_provider(
                uow, exclude={str(record.run_id) for record in recoverable}
            ),
        )
        # 收口第 4 项：停在委派调用上的等待运行也要先恢复绑定与授权（启动后会被唤醒）。
        recoverable = (
            *recoverable,
            *waiting_runs_blocked_on_delegation(
                uow, exclude={str(record.run_id) for record in recoverable}
            ),
        )
        delegated_children: list[tuple[str, str]] = []
        for record in recoverable:
            start = uow.read_start_snapshot(str(record.run_id))
            start_input = start.get("input") if isinstance(start, Mapping) else None
            metadata = (
                start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
            )
            raw_binding = metadata.get("run_binding") if isinstance(metadata, Mapping) else None
            if isinstance(raw_binding, Mapping):
                restored = SdkRunBindingV1.from_record(raw_binding)
                provider_binding_resolver.register(restored)
                record_state = str(
                    getattr(getattr(record, "state", None), "value", None)
                    or getattr(record, "state", "")
                ).lower()
                if record_state == "waiting":
                    provider_binding_resolver.mark_waiting(restored.run_id)
                if is_delegated_child_start(start):
                    # 委派子运行不是主对话运行：不建界面投递、不占根映射、不挂
                    # 恢复监视器（否则子运行回答会写进主会话、"停止"会取消到它）。
                    # 授权照常按历史恢复（下面），由委派服务重新接管监视。
                    delegated_children.append(
                        (str(record.run_id), str(metadata.get("parent_run_id") or ""))
                    )
                elif not _restore_sdk_delivery_route(record, start, session_db):
                    logger.warning(
                        "sdk_recovery_delivery_route_missing",
                        sdk_run_id=str(record.run_id),
                        state=record_state,
                    )
        effects = ProductEffectExecutor(
            clock=clock,
            uow=uow,
            registry=tools_adapter,  # tools_adapter is already a ToolRegistry
            authorization=authorization_adapter,
            reconciliation=reconciliation_adapter,
            foreground_admission=_ensure_foreground_effect_gate(),
            effect_gate=effect_gate,
            read_gate=read_gate,
            evidence_ingress=_ensure_evidence_ingress(),
        )

        durable_catalog_resolver = DurableToolCatalogResolver(uow)
        catalog_snapshot = uow.put_tool_catalog_snapshot(
            effects.provider_tool_specs(tuple(frozen_catalog["tool_names"]))
        )
        frozen_catalog["generation"] = catalog_snapshot.generation
        frozen_catalog["content_fingerprint"] = catalog_snapshot.content_fingerprint
        for record in recoverable:
            start = uow.read_start_snapshot(str(record.run_id))
            start_input = start.get("input") if isinstance(start, Mapping) else None
            metadata = (
                start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
            )
            if not isinstance(metadata, Mapping):
                continue
            raw_binding = metadata.get("run_binding")
            raw_tool_authority = metadata.get("tool_authority")
            if not isinstance(raw_binding, Mapping) or not isinstance(raw_tool_authority, Mapping):
                logger.warning(
                    "sdk_recovery_tool_authority_missing",
                    sdk_run_id=str(record.run_id),
                )
                continue
            restored_binding = SdkRunBindingV1.from_record(raw_binding)
            record_state = str(
                getattr(getattr(record, "state", None), "value", None)
                or getattr(record, "state", "")
            ).lower()
            try:
                if record_state == "waiting":
                    tool_authorities.restore_waiting_run(
                        run_start_record=raw_tool_authority,
                        run_binding=restored_binding,
                        catalog_resolver=durable_catalog_resolver,
                    )
                else:
                    tool_authorities.restore_run(
                        run_start_record=raw_tool_authority,
                        run_binding=restored_binding,
                        catalog_resolver=durable_catalog_resolver,
                        lease_state="active",
                    )
            except SdkToolAuthorityMigrationUnavailable as exc:
                _isolate_unrestorable_sdk_tool_authority(
                    sdk_run_id=str(record.run_id),
                    metadata=metadata,
                    provider_binding_resolver=provider_binding_resolver,
                    tool_authorities=tool_authorities,
                    error=exc,
                )
        for child_run_id, parent_run_id in delegated_children:
            if parent_run_id:
                delegation_service.adopt_recovered(child_run_id, parent_run_id)
        # 重启前停在委派调用上的父运行：运行时就绪后写对账结论并唤醒。
        delegation_service.start_recovery()
        provider_port = ProductProviderInvocationCoordinator(
            uow=uow,
            resolver=provider_binding_resolver,
            evidence_ingress=_ensure_evidence_ingress(),
            context_use_authority=_typed_use_authority,
            typed_terminal=_typed_use_authority,
            clock=clock,
        )
        if projection_pump is None:

            def projection_context(receipt: Any) -> ProviderProjectionContextV1:
                start = uow.read_start_snapshot(str(receipt.run_id))
                if not isinstance(start, Mapping):
                    raise RuntimeError("sdk_provider_projection_start_snapshot_missing")
                start_input = start.get("input")
                metadata = (
                    start_input.get("context_metadata")
                    if isinstance(start_input, Mapping)
                    else None
                )
                if not isinstance(metadata, Mapping):
                    raise RuntimeError("sdk_provider_projection_context_missing")
                return ProviderProjectionContextV1.from_value(metadata)

            projection_pump = SdkProviderProjectionPump(
                uow,
                session_db,
                context_resolver=projection_context,
                on_committed=lambda context: _broadcast_context_usage_snapshot(
                    session_db, context.session_id
                ),
            )
            projection_pump.trigger()

        context_staging = ContextStagingRepository(database)
        service_context.register("sdk_context_staging", context_staging)
        return RuntimePorts(
            provider=provider_port,
            tools=effects,
            authorization=authorization_adapter,
            context=context,
            delivery=DeliveryDispatcher(uow, {"product": delivery_adapter}, clock=clock),
            tool_reconciliation=reconciliation_adapter,
            reconciliation=runtime_reconciliation,
            provider_reconciliation=provider_reconciliation,
            react_checkpoint=uow,
            tool_catalog=durable_catalog_resolver,
            owner_id=f"deskpet-product-sdk-g{generation}",
            agent_memory=agent_memory_port,
            context_provider=context_provider,
            context_staging=context_staging,
        )

    class _ProductionToolCatalogProxy:
        def _target(self):
            ports = production_ports.get("ports")
            if ports is None:
                raise RuntimeError("production tool catalog is not bound")
            return ports.tool_catalog

        def current_generation(self):
            return self._target().current_generation()

        def resolve(self, generation, content_fingerprint):
            return self._target().resolve(generation, content_fingerprint)

    class _ProductionProjectionPumpProxy:
        async def start(self):
            if projection_pump is None:
                raise RuntimeError("production provider projection pump is not bound")
            started = projection_pump.start()
            if inspect.isawaitable(started):
                await started

        async def close(self):
            if projection_pump is not None:
                await projection_pump.close()

    production_ports: dict[str, Any] = {}
    production_tool_catalog = _ProductionToolCatalogProxy()
    production_projection_pump = _ProductionProjectionPumpProxy()

    def _production_ports_for(uow):
        cached = production_ports.get("ports")
        if cached is None:
            cached = ports_factory(uow.database, uow)
            production_ports["ports"] = cached
        elif cached.react_checkpoint is not uow:
            raise RuntimeError("production runtime transaction owner changed")
        return cached

    def production_runtime_factory(execution_path):

        production_ports.clear()
        profiles = {"agent.general": RuntimeProfile("agent.general", "react")}
        runtime_config_kwargs = dict(
            clock=clock,
            execution_path=execution_path,
            provider_builder=lambda uow: _production_ports_for(uow).provider,
            tools_builder=lambda uow: _production_ports_for(uow).tools,
            delivery_builder=lambda uow: _production_ports_for(uow).delivery,
            context_builder=lambda _database: production_ports["ports"].context,
            context_staging_builder=lambda _database: production_ports["ports"].context_staging,
            authorization=authorization_adapter,
            tool_reconciliation=reconciliation_adapter,
            reconciliation=runtime_reconciliation,
            provider_reconciliation=provider_reconciliation,
            tool_catalog=production_tool_catalog,
            driver=driver,
            profiles=profiles,
            memory=agent_memory_port,
            memory_ownership=ResourceOwnership.BORROWED,
            context_provider=context_provider,
            provider_budget_resolver=provider_binding_resolver,
            provider_projection_pump=production_projection_pump,
            run_binding=tool_authorities,
            structured_message_services=ProductContextAdapter(),
            **_sdk_runtime_authority_bindings(),
            owner_id=f"deskpet-product-sdk-g{generation}",
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
            transaction_owner=production_ports["ports"].react_checkpoint,
        )

    # Default pinned wheel, or an explicitly attested development source identity.
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(_paths.user_data_dir()),
        candidate_identity=build_runtime_identity(),
        dependency_loader=lambda: SdkRuntimeBuildInputs(
            profiles={"agent.general": RuntimeProfile("agent.general", "react")},
            drivers={"react": driver},
            ports_factory=ports_factory,
            workflow_catalog_digest=frozen_catalog["content_fingerprint"],
            owned_resources=(OwnedResourceCloser("provider-http", client.aclose),),
            runtime_factory=production_runtime_factory,
        ),
        ready_publisher=lambda ready: service_context.register("sdk_runtime_ready", ready),
    )

    service_context.register("sdk_runtime_catalog", frozen_catalog)
    service_context.register("sdk_provider_binding_resolver", provider_binding_resolver)
    service_context.register("sdk_tool_authority_registry", tool_authorities)
    service_context.register("sdk_runtime_tool_inventory", sdk_tool_inventory)
    service_context.register("sdk_prepared_authorization_policy", authorization_policy)

    # S5a — register the three concrete SDK 0.7 authorities.  Missing any
    # prerequisite must fail the stack build (no Noop/fake fallback); the
    # proxies in _sdk_runtime_authority_bindings stay fail-closed otherwise.

    _state_version = ContextRouteLedgerStore(_state_db_path).user_version()
    if _state_version < 35:
        # Legacy (pre human-memory epoch) composition keeps the SDK bare
        # context path; the v45 route/snapshot ledger does not exist there and
        # the fail-closed proxies stay unregistered exactly as before S5a.
        logger.info(
            "sdk_context_authorities_skipped_legacy_epoch version=%s",
            _state_version,
        )
        return stack
    if tool_authorities is None:
        raise RuntimeError("sdk_context_authority_composition_missing:tool_authority_registry")
    context_route_ledger = ContextRouteLedgerStore(
        _state_db_path, evidence_ingress=_ensure_evidence_ingress()
    )
    context_route_ledger.verify_schema()

    def _authority_ports():
        ports = production_ports.get("ports")
        if ports is None:
            raise RuntimeError("sdk_run_context_authority_ports_unbound")
        return ports

    # 2026-09-10：``reconcile`` 原本是认知记忆运行时的 pending-occurrence 对账，
    # 随 Prospective 一并移除；该参数在下游本就是 Optional。
    _occurrence_reconcile = None

    # S5b Task 3 review F-2: the protected "closure required" instruction of the
    # next Run (pending merge / dirty admission scope) is a production fact, not
    # a test-harness one — the reader is a composition slot (missing → startup fail).
    service_context.register(
        "sdk_run_context_authority",
        _build_run_context_authority(
            state_db_path=_state_db_path,
            ports_resolver=_authority_ports,
            exposure_resolver=tool_authorities.resolve_exposure,
            ledger=context_route_ledger,
            reconcile=_occurrence_reconcile,
            typed_use_authority=_typed_use_authority,
            occurrence_coordinator=_occurrence_coordinator,
            configured_workspace_root=configured_workspace_root,
        ),
    )
    service_context.register(
        "sdk_runtime_decision_sink",
        ProductRuntimeDecisionSink(ledger=context_route_ledger, reconcile=_occurrence_reconcile),
    )
    # S5b Task 1: PROJECT_EFFECT envelopes resolve their exact single root
    # from the route receipt's immutable binding-set receipt (never the live
    # head, never a default root); zero/multi root is a whole-Run fault whose
    # stable code is memoised for the terminal evidence.
    service_context.register(
        "sdk_task_execution_authority",
        ProductTaskExecutionAuthority(
            root_resolver=BindingRootResolver(_context_route_binding_store()),
            fault_sink=_ensure_run_fault_memo(),
        ),
    )
    _assert_sdk_composition_slots()
    return stack


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
