"""启动步骤：核心服务与记忆纪元、提供方路由、检索网关与语音引擎、桌面测试桥与实时语音"""

from __future__ import annotations

import faulthandler as _faulthandler
import os
import signal as _signal
from pathlib import Path

import paths
import paths as _paths
from config import resolve_cloud_api_key as _resolve_cloud_api_key
from deskpet.memory.display_invalidation import MemoryDisplayInvalidation
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
from deskpet.memory.schema import StartupCompositionMode, dispatch_startup_epoch
from deskpet.orchestration.wiring import activate_orchestration
from deskpet.permissions import protected_paths as _protected_paths
from deskpet.realtime_voice import (
    REALTIME_VOICE_ENABLED,
    RealtimeVoiceService,
    allowed_realtime_origins,
)
from deskpet.retrieval.runtime import build_search_gateway, set_default_gateway
from deskpet.session.project_binding import (
    SessionCreationService as _SessionCreationService,
)
from deskpet.tools.file_tools import rebind_loop as _rebind_ws_loop
from llm.provider_registry import LLMProviderRegistry, _migrate_legacy_provider_config
from llm.resolution import ProviderRoutingReadiness
from observability.startup import registry as startup_errors

from host.capability_runtime import _initialize_capability_runtime
from host.chat_delivery import _broadcast_control
from host.companion_services import (
    _initialize_companion_action_decision_service,
    _initialize_companion_projection_services,
)
from host.connections import _control_connections
from host.growth_authority import _complete_growth_authority_cutover, _initialize_growth_authority
from host.human_memory_ports import _activate_human_memory_host_ports
from host.llm_providers import billing_ledger
from host.routes_cloud_config import _seed_registry_from_runtime_overrides
from host.runtime import _CONFIG_PATH, SHARED_SECRET, config, logger, service_context
from host.sdk_activation import (
    _activate_companion_runtime_adapter_and_open_ingress,
    _activate_product_sdk_runtime,
)
from host.sdk_execution import _initialize_sdk_desktop_test_bridge, _sdk_desktop_test_enabled
from host.state import host_state
from host.stores_bootstrap import (
    _freeze_current_session_owner,
    _project_binding_service,
    _session_db,
    _state_db_path,
)
from host.window_control import _WINDOW_CONTROL_BOOTSTRAP


async def _primary_context_usage_reader(execution_session_id: str) -> Any:
    """主对话读用量：按执行会话号读 SessionDB 的用量状态；会话库还没就绪时返回空。"""
    session_db = service_context.get("session_db")
    if session_db is None:
        return None
    return await session_db.get_context_usage_state(execution_session_id)


async def _startup_core_services() -> tuple[Any, Any]:
    """启动：受保护路径通知、提供方路由就绪状态、人类记忆启动纪元与宿主服务工厂登记。"""
    # 2026-09-26 诊断：`kill -USR1 <pid>` 把所有线程的调用栈打到 stderr（排查 CPU 占满/卡住）
    try:
        _faulthandler.register(_signal.SIGUSR1, all_threads=True)
    except Exception:  # noqa: BLE001 - 只是诊断，Windows 上没有这个信号
        logger.debug("faulthandler_register_failed", exc_info=True)
    logger.info("preloading models...")
    # 2026-09-26 受保护核心文件：会话卡片走控制通道全局广播（不依赖面板是否可见）

    _protected_paths.set_notifier(_broadcast_control)

    _provider_readiness = ProviderRoutingReadiness()
    service_context.register("provider_routing_readiness", _provider_readiness)
    # v33 reset preflight must finish before MemoryManager, workflow,
    # companion, or SDK execution databases are opened.

    approved_fresh_lane = _state_db_path.resolve().is_relative_to(
        Path(_paths.user_data_dir()).resolve()
    )
    startup_epoch = await dispatch_startup_epoch(
        _state_db_path,
        approved_fresh_lane=approved_fresh_lane,
    )

    memory_display_invalidation = MemoryDisplayInvalidation(_broadcast_control)
    # MM-D2（2026-09-09）：Manual 目录授权卡片也要靠这条 content-free 失效广播刷新，
    # 所以它必须在 service_context 里可被 _activate_human_memory_host_ports 取到。
    service_context.register("memory_display_invalidation", memory_display_invalidation)
    service_context.register(
        "human_memory_host_service_factory",
        (
            HumanMemoryHostServiceFactory(
                _state_db_path,
                startup_epoch,
                settled_run_reader=lambda run_id, **kwargs: (
                    host_state.sdk_runtime_stack.read_settled_primary_run(run_id, **kwargs)
                ),
                run_binding_reader=lambda run_id: (
                    host_state.sdk_runtime_stack.read_closure_run_facts(run_id).binding_record
                ),
                context_usage_reader=_primary_context_usage_reader,
                decision_ingress_getter=lambda: host_state.sdk_ingress,
                display_invalidation=memory_display_invalidation,
            )
            if startup_epoch.composition_mode is StartupCompositionMode.HUMAN
            else None
        ),
    )
    return _provider_readiness, startup_epoch


async def _startup_provider_routing(_provider_readiness: Any) -> None:
    """启动：提供方注册表与路由装配。"""
    try:
        # SessionDB owns the migration/backup recovery path. Registry identity
        # is loaded only after v23 exists, then legacy bindings are reconciled
        # in one SQLite transaction before ingress opens.
        await _session_db.initialize()
        _migrate_legacy_provider_config(_CONFIG_PATH)
        host_state.provider_registry = LLMProviderRegistry(_CONFIG_PATH)
        # 2026-08-09：把 llm_runtime.json 的手填配置同步成一条 registry provider。
        #
        # onboarding 与设置页写的是 llm_runtime.json（用户可直接编辑，明文是有意
        # 为之），而跑 agent 的 harness 只认 registry chain。relay 时代由登录桥
        # 负责填 registry，relay 移除后这条链断了——表现为"填了 provider，聊天却
        # 报 no LLM provider configured"（真机实测）。
        # 放在启动而不只放在 /config/cloud 的原因：存量用户已经填过了，不该被迫
        # 重走一遍 onboarding 才能把配置补进 registry。
        # 只在 registry 为空时补，绝不覆盖用户在设置页管理的多 provider 配置。
        try:
            await _seed_registry_from_runtime_overrides(host_state.provider_registry)
        except Exception as _seed_exc:  # noqa: BLE001 - 补种失败不阻断启动
            logger.warning(
                "provider_registry_seed_failed error=%s", str(_seed_exc)[:200], exc_info=True
            )
        await _session_db.reconcile_provider_bindings(
            {
                entry["id"]: (
                    str(entry["incarnation_id"]),
                    int(entry["config_revision"]),
                )
                for entry in host_state.provider_registry.list_providers()
            },
            registry_digest=host_state.provider_registry.snapshot_digest(),
        )
        service_context.register("provider_registry", host_state.provider_registry)
        if _project_binding_service is not None:

            def _validate_created_session_provider(
                provider_id: str, incarnation: str, revision: int
            ) -> None:
                entry = host_state.provider_registry.get_entry(provider_id)
                if (
                    entry is None
                    or str(entry.incarnation_id) != incarnation
                    or int(entry.config_revision) != revision
                ):
                    raise RuntimeError("provider_binding_stale")

            host_state.session_creation_service = _SessionCreationService(
                _project_binding_service,
                provider_mutation_lock=host_state.provider_registry.mutation_lock,
                session_db=_session_db,
                owner_identity_resolver=_freeze_current_session_owner,
                provider_binding_validator=_validate_created_session_provider,
                documents_root=(
                    _WINDOW_CONTROL_BOOTSTRAP.documents_root
                    if _WINDOW_CONTROL_BOOTSTRAP is not None
                    else None
                ),
                documents_identity=(
                    _WINDOW_CONTROL_BOOTSTRAP.documents_identity
                    if _WINDOW_CONTROL_BOOTSTRAP is not None
                    else None
                ),
                allow_test_projectless=False,
            )
            service_context.register(
                "session_creation_service", host_state.session_creation_service
            )
            await host_state.session_creation_service.reconcile_automatic_workspaces()
        _provider_readiness.mark_ready()
        logger.info(
            "provider_routing_ready providers=%d",
            len(host_state.provider_registry.list_providers()),
        )
    except Exception as _provider_start_exc:  # noqa: BLE001
        _provider_readiness.mark_failed(type(_provider_start_exc).__name__)
        service_context.register("provider_registry", None)
        logger.exception("provider_routing_startup_failed: %s", _provider_start_exc)


async def _startup_search_and_billing() -> None:
    """启动：检索网关、工作区记忆回绑主循环、计费账本。"""

    _search_gateway = build_search_gateway(config)
    set_default_gateway(_search_gateway)
    service_context.register("search_gateway", _search_gateway)
    logger.info(
        "search_gateway_ready",
        enabled=config.search_gateway.enabled,
        providers=config.search_gateway.providers,
    )
    # Stage 2 round 2 fix：workspace_memory hook (os_tools/read_file 等
    # sync handler) 依赖 file_tools._workspace_loop 引用主 loop。set_
    # workspace_store 在 module top-level 跑时拿不到 loop —— 这里补绑。
    try:
        ok = _rebind_ws_loop()
        logger.info("p4_workspace_memory_loop_rebound", ok=ok)
    except Exception as _exc:  # noqa: BLE001
        logger.debug("workspace loop rebind failed: %s", _exc, exc_info=True)
    # P2-1-S8: billing DB must exist before the first chat call. Failure
    # here is logged but doesn't block startup — the ledger simply won't
    # record anything until the DB is reachable on a future boot.
    try:
        await billing_ledger.init()
        logger.info("billing_ledger_ready", db_path=str(config.billing.db_path))
    except Exception as exc:
        logger.warning("billing_ledger_init_failed", error=str(exc), exc_info=True)


async def _startup_desktop_and_realtime_voice(startup_epoch: Any) -> None:
    """启动：桌面测试桥、实时语音服务。"""
    if _sdk_desktop_test_enabled():
        # This local acceptance mode exists to validate the new SDK itself.
        # Do not make it depend on the legacy Workflow/Harness cutover gates.
        _initialize_sdk_desktop_test_bridge()
    else:
        # Task 13 startup order is fail-closed: Platform foundation exists before
        # authority composition, while user catalog and Companion ingress remain
        # closed until Harness registers the full core catalog and the durable
        # cutover reconciler has proved every receipt.
        await _initialize_capability_runtime()
        await _initialize_growth_authority()
        # Slice C: Use SDK Runtime instead of legacy harness
        await _activate_product_sdk_runtime()
        # Agent 编排服务（plans/2026-09-11-orchestrator-host-integration，用户 Phase3
        # P3.1）：在 SDK stack 之后启动；失败只让它不可用并记入 startup_errors，
        # 绝不让启动失败。

        await activate_orchestration(
            service_context,
            config_path=_CONFIG_PATH,
            user_data=paths.user_data_dir(),
            broadcast_targets=lambda: list(_control_connections.values()),
            record_startup_error=startup_errors.record,
        )
        await _activate_human_memory_host_ports(startup_epoch)
        await _complete_growth_authority_cutover()
        await _initialize_companion_projection_services()
        await _activate_companion_runtime_adapter_and_open_ingress()
        _initialize_companion_action_decision_service()

    try:
        _realtime_vite_port = int(os.getenv("DESKPET_VITE_PORT", "5173"))
    except ValueError:
        _realtime_vite_port = 5173
    if REALTIME_VOICE_ENABLED:
        host_state.realtime_voice_service = RealtimeVoiceService(
            shared_secret=SHARED_SECRET,
            relay_endpoint_supplier=lambda: str(
                getattr(host_state.local_llm, "base_url", config.llm.local.base_url)
            ),
            api_key_supplier=lambda: host_state.current_cloud_api_key or _resolve_cloud_api_key(),
            allowed_origins=allowed_realtime_origins(_realtime_vite_port),
        )
        logger.info(
            "realtime_voice_ready",
            path="/ws/realtime-voice",
            protocol_version="2026-08-27.1",
        )
    else:
        host_state.realtime_voice_service = None
        logger.info(
            "realtime_voice_disabled",
            path="/ws/realtime-voice",
            reason="temporarily_disabled_by_product",
        )
    logger.info("startup complete")
