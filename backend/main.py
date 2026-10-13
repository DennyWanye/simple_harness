# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import sys

# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from deskpet.frozen_worker_dispatch import dispatch_frozen_worker_if_requested

dispatch_frozen_worker_if_requested()

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# isort: split
from host.window_control import (
    _WINDOW_CONTROL_BOOTSTRAP,
)

# isort: split

from pathlib import Path as _Path

import paths as _paths
import uvicorn

# isort: split

# isort: split
from pathlib import Path

import p4_ipc  # P4-S11 MemoryPanel + ContextTrace IPC handlers
from config import (
    effective_llm_model,
    resolve_deep_research_workflow_version,
)
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
)
from deskpet.agent.turn_preparer import (
    ProductTurnPreparer,
)
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
)
from host.state import host_state
from observability.startup import registry as startup_errors  # P3-S2
from pydantic import (
    model_validator,
)

# isort: split
from host.runtime import (
    _CONFIG_PATH,
    SHARED_SECRET,
    config,
    logger,
    service_context,
)

# isort: split


# isort: split
from host.settings_persistence import (
    _persist_supervisor_enabled,
    _restart_orchestration,
)

# isort: split


# isort: split
from config import resolve_cloud_api_key as _resolve_cloud_api_key
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_VERSION,
)
from deskpet.tools.public_projection import project_public_tool_calls, project_public_tool_result
from llm.provider_registry import (
    LLMProviderRegistry,
    ProviderMutationConflict,
    _migrate_legacy_provider_config,
)
from providers.openai_compatible import OpenAICompatibleProvider
from tools.registry import ToolRegistry

# isort: split
from host.llm_providers import (
    _context_os_e2e_boot_hooks,
    _load_llm_runtime_overrides,
    _sanitize_cot_dsml,
    _save_llm_runtime_overrides,
    billing_ledger,
    cloud_llm,
)

# isort: split


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at
# user request. Single-endpoint mode — errors surface directly
# instead of auto-swapping to a different model with a different
# voice mid-turn.


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at user
# request — they don't want auto-swap to gemma4:e4b mid-turn because
# the model-style mismatch produces ugly mixed responses. Errors from
# the configured LLM endpoint now surface directly to the user.


# isort: split
from host.tools_bootstrap import (
    _get_receipt_store,
    deskpet_tool_registry_v2,
    permission_gate_v2,
    plugin_manager,
    skill_registry_client,
)

# isort: split
# 语音装配模块在导入期登记语音引擎占位；保持原来的导入位置，登记顺序不变。
import host.voice_bootstrap  # noqa: F401

# isort: split


# isort: split
from host.llm_calls import (
    _refresh_research_live_llm,
)

# isort: split


# ─── WI-4.3 技能自创闭环 hook（方案 B 抽 helper）─────────────────────
# 2026-06-06：原 codify hook 只 inline 在 chat handler 的 FinalEvent 分支 →
# turn 经 ErrorEvent（relay ReadError）/ 迭代上限 / 被 stop 中止结束时整段不跑
# → 多工具 turn 跑了 ≥5 工具但卡不弹（真机诊断）。抽成模块级 helper，在
# FinalEvent + ErrorEvent 两处都调，让 turn 任意路径结束都能触发技能自创。
# safe-fail：任何异常只 debug log，不中断正常 turn。flag OFF → 整段跳过（BC）。
_companion_ingress_tasks: set[asyncio.Task] = set()


# isort: split
from host.growth_authority import (
    _complete_growth_authority_cutover,
    _initialize_growth_authority,
)

# isort: split


# isort: split
from host.companion_services import (
    _dispatch_companion_action_command,
    _initialize_companion_action_decision_service,
    _initialize_companion_projection_services,
)

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split
from host.stores_bootstrap import (
    _episodic_background_tasks,
    _fact_extractor,
    _facts_store,
    _freeze_current_session_owner,
    _legacy_reflection_tasks,
    _message_chunker,
    _project_binding_service,
    _provider_workload_maintenance_tasks,
    _session_db,
    _SessionCreationService,
    _state_db_path,
    _summarizer_state_db_path,
    _workflow_ipc_background_tasks,
)

# isort: split


# isort: split
from host.capability_runtime import (
    _authorization_policy_snapshot,
    _initialize_capability_runtime,
    _set_authorization_auto_mode,
)

# isort: split


# isort: split
from host.human_memory_ports import (
    _activate_human_memory_host_ports,
)

# isort: split


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    # 2026-09-26 诊断：`kill -USR1 <pid>` 把所有线程的调用栈打到 stderr（排查 CPU 占满/卡住）
    try:
        import faulthandler as _faulthandler
        import signal as _signal

        _faulthandler.register(_signal.SIGUSR1, all_threads=True)
    except Exception:  # noqa: BLE001 - diagnostics only (not on Windows)
        pass
    logger.info("preloading models...")
    # 2026-09-26 受保护核心文件：会话卡片走控制通道全局广播（不依赖面板是否可见）
    from deskpet.permissions import protected_paths as _protected_paths

    _protected_paths.set_notifier(_broadcast_control)
    from llm.resolution import ProviderRoutingReadiness

    _provider_readiness = ProviderRoutingReadiness()
    service_context.register("provider_routing_readiness", _provider_readiness)
    # v33 reset preflight must finish before MemoryManager, workflow,
    # companion, or SDK execution databases are opened.
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.schema import (
        StartupCompositionMode,
        dispatch_startup_epoch,
    )

    approved_fresh_lane = _state_db_path.resolve().is_relative_to(
        Path(_paths.user_data_dir()).resolve()
    )
    startup_epoch = await dispatch_startup_epoch(
        _state_db_path,
        approved_fresh_lane=approved_fresh_lane,
    )
    from deskpet.memory.display_invalidation import MemoryDisplayInvalidation

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
                decision_ingress_getter=lambda: host_state.sdk_ingress,
                display_invalidation=memory_display_invalidation,
            )
            if startup_epoch.composition_mode is StartupCompositionMode.HUMAN
            else None
        ),
    )
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
            logger.warning("provider_registry_seed_failed error=%s", str(_seed_exc)[:200])
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
    from deskpet.retrieval.runtime import build_search_gateway, set_default_gateway

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
        from deskpet.tools.file_tools import rebind_loop as _rebind_ws_loop

        ok = _rebind_ws_loop()
        logger.info("p4_workspace_memory_loop_rebound", ok=ok)
    except Exception as _exc:  # noqa: BLE001
        logger.debug("workspace loop rebind failed: %s", _exc)
    # P2-1-S8: billing DB must exist before the first chat call. Failure
    # here is logged but doesn't block startup — the ledger simply won't
    # record anything until the DB is reachable on a future boot.
    try:
        await billing_ledger.init()
        logger.info("billing_ledger_ready", db_path=str(config.billing.db_path))
    except Exception as exc:
        logger.warning("billing_ledger_init_failed", error=str(exc))
    for name in ("vad_engine", "asr_engine", "tts_engine"):
        engine = service_context.get(name)
        if engine and hasattr(engine, "load"):
            try:
                await engine.load()
                logger.info("loaded", engine=name)
            except Exception as exc:
                logger.warning("failed_to_load", engine=name, error=str(exc))
                # P3-S2: persist structured error so /health + WS startup_status
                # can surface "degraded" state instead of silently accepting
                # requests that will later 500.
                startup_errors.record(name, exc)
    # P4-S13: async initialisers for the P4 read-only stack. Each failure is
    # isolated — MemoryManager needing a base dir doesn't prevent SkillLoader
    # from scanning the built-in dir, etc.
    _sdb = service_context.get("session_db")
    if _sdb is not None:
        try:
            await _sdb.initialize()
            logger.info("p4_session_db_ready", path=str(_sdb._db_path))
        except Exception as exc:
            logger.warning("p4_session_db_init_failed", error=str(exc))
    try:
        from deskpet.workflows.bootstrap import build_workflow_service
        from deskpet.workflows.retention import RetentionPolicy
        from deskpet.workflows.store import RegisteredBlobStore

        _workflow_delivery_blobs = RegisteredBlobStore(
            _paths.user_data_dir() / "workflows" / "blobs",
            _paths.user_data_dir() / "data" / "workflow.db",
        )

        async def _resolve_workflow_content(ref: str) -> bytes:
            wire_ref = str(ref or "").strip()
            digest = wire_ref[7:] if wire_ref.startswith("sha256:") else wire_ref
            return await _workflow_delivery_blobs.get(digest)

        async def _workflow_delivery_epoch(event, delivery) -> int | None:
            if _sdb is None or _workflow_service is None:
                return None
            run_id = str(event.get("run_id") or "")
            target_sid = str(delivery.get("target_id") or "default")
            ref = await _workflow_service.run_store.get_session_ref(run_id, "delivery")
            if ref is None or str(ref["session_id"]) != target_sid:
                return None
            state = await _sdb.get_session_delivery_state(target_sid)
            if state.get("deleted_at") is not None:
                return None
            expected_epoch = int(ref["session_epoch"])
            if int(state.get("epoch", 0)) != expected_epoch:
                return None
            return expected_epoch

        async def _workflow_session_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            target_sid = str(delivery.get("target_id") or "default")
            expected_epoch = await _workflow_delivery_epoch(event, delivery)
            if expected_epoch is None:
                return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
            payload = dict(event.get("payload") or {})
            nested = payload.get("payload")
            nested = dict(nested) if isinstance(nested, dict) else {}
            text = ""
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
                workflow_message_projection,
            )

            content_payload = nested if nested.get("content_ref") else payload
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                text = resolved
            if event.get("event_type") == "workflow.progress":
                v2_payload = payload if payload.get("schema_version") == 2 else nested
                if v2_payload.get("schema_version") == 2:
                    from deskpet.workflows.progress import validated_v2_stage_text

                    text = validated_v2_stage_text(v2_payload).strip()
            if not text:
                text = str(nested.get("text") or payload.get("text") or "").strip()
            if not text:
                if event.get("event_type") == "workflow.accepted":
                    text = f"{payload.get('workflow_name', '任务')} 已开始"
                elif event.get("event_type") == "workflow.decision":
                    text = "Workflow decision is waiting for your confirmation."
                elif event.get("event_type") == "workflow.final":
                    text = f"任务已结束：{payload.get('status', 'unknown')}"
                else:
                    return DeliveryAttemptResultV1.retryable_failure("projection_content_missing")
            if _sdb is not None:
                projection = workflow_message_projection(str(event.get("event_type") or ""))
                message_id = await _sdb.append_message_if_epoch(
                    target_sid,
                    projection.role,
                    text,
                    expected_epoch=expected_epoch,
                    workflow_event_id=str(event["event_id"]),
                    projection_kind=projection.projection_kind,
                    context_visibility=projection.context_visibility,
                    skip_embed=projection.skip_embed,
                    root_run_id=(str(event.get("root_run_id") or "").strip() or None),
                )
                if message_id is None:
                    return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
                return DeliveryAttemptResultV1.delivered()
            return DeliveryAttemptResultV1.retryable_failure("session_db_unavailable")

        async def _workflow_websocket_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            if await _workflow_delivery_epoch(event, delivery) is None:
                return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
            )

            outbound_event = dict(event)
            outbound_payload = dict(event.get("payload") or {})
            nested = outbound_payload.get("payload")
            content_payload = (
                dict(nested)
                if isinstance(nested, dict) and nested.get("content_ref")
                else outbound_payload
            )
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                # Resolved bytes are transport-only.  The immutable outbox row
                # remains ref-only and therefore replay-safe.
                outbound_payload["text"] = resolved
            outbound_event["payload"] = outbound_payload
            envelope_type = (
                "workflow_final"
                if event.get("event_type") == "workflow.final"
                else "workflow_event"
            )
            envelope = {
                "type": envelope_type,
                "payload": {
                    **outbound_event,
                    "session_id": str(delivery.get("target_id") or "default"),
                },
            }
            from deskpet.workflows.delivery import broadcast_websocket_best_effort

            return await broadcast_websocket_best_effort(
                envelope, list(_control_connections.values())
            )

        async def _workflow_artifact_publisher(payload):
            envelope = {"type": "tool_result", "payload": dict(payload)}
            seen: set[int] = set()
            for target_ws in list(_control_connections.values()):
                marker = id(target_ws)
                if marker in seen:
                    continue
                seen.add(marker)
                await asyncio.wait_for(target_ws.send_json(envelope), timeout=1.0)

        product_handlers = {}
        if _sdb is not None:
            from deskpet.workflows.adapters.product_delivery import ProductDeliveryAdapter
            from deskpet.workflows.store import WorkflowRunStore

            product_delivery = ProductDeliveryAdapter(
                session_db=_sdb,
                receipt_store=_get_receipt_store(),
                workflow_store=WorkflowRunStore(_paths.user_data_dir() / "data" / "workflow.db"),
                artifact_publisher=_workflow_artifact_publisher,
                content_resolver=_resolve_workflow_content,
            )
            product_handlers = product_delivery.handlers()

        _workflow_service = await build_workflow_service(
            _paths.user_data_dir(),
            delivery_handlers={
                "session_message": _workflow_session_delivery,
                "websocket": _workflow_websocket_delivery,
                **product_handlers,
            },
            retention_policy=RetentionPolicy.from_days(
                terminal_days=config.workflows.terminal_retention_days,
                evaluation_tombstone_days=config.workflows.evaluation_retention_days,
                orphan_grace_hours=config.workflows.orphan_grace_hours,
            ),
            session_delivery_state_reader=(
                _sdb.get_session_delivery_state if _sdb is not None else None
            ),
            activate=False,
        )
        from deskpet.tools import research_tools as _workflow_research_tools
        from deskpet.workflows.contracts import WorkflowContext
        from deskpet.workflows.definitions.research_core import (
            FetchPort,
            ResearchArtifactPort,
            ResearchSearchPort,
            ResearchSearchResults,
            legacy_ports,
        )
        from deskpet.workflows.definitions.v1 import deep_research_initial_state
        from deskpet.workflows.definitions.v2 import (
            deep_research_initial_state as deep_research_v2_initial_state,
        )
        from deskpet.workflows.definitions.v3 import (
            deep_research_initial_state as deep_research_v3_initial_state,
        )
        from deskpet.workflows.definitions.v4 import (
            deep_research_initial_state as deep_research_v4_initial_state,
        )
        from deskpet.workflows.definitions.v5 import (
            deep_research_initial_state as deep_research_v5_initial_state,
        )
        from deskpet.workflows.definitions.v6 import (
            build_continuation_start_payload as build_deep_research_v6_continuation_payload,
        )
        from deskpet.workflows.definitions.v6 import (
            decode_continuation_snapshot as decode_deep_research_v6_continuation_snapshot,
        )
        from deskpet.workflows.definitions.v6 import (
            deep_research_initial_state as deep_research_v6_initial_state,
        )
        from deskpet.workflows.definitions.v7 import (
            deep_research_initial_state as deep_research_v7_initial_state,
        )
        from deskpet.workflows.launcher import WorkflowLauncher
        from deskpet.workflows.native import NativeExecutionPolicy
        from deskpet.workflows.runtime_adapters import (
            DEEP_RESEARCH_EXTENSION,
            DeepResearchContinuationAdapter,
            DeepResearchRuntimeExtension,
        )
        from deskpet.workflows.terminal_projection import TERMINAL_COMMIT_CAPABILITY

        _workflow_launcher = WorkflowLauncher(_workflow_service)
        _workflow_service.launcher = _workflow_launcher
        # Runtime adapters must all be registered before the production
        # registry is sealed and any durable recovery is allowed to run.
        service_context.register("workflow_service", _workflow_service)
        from deskpet.execution.harness_public_read_service import (
            HarnessPublicReadService,
        )

        _harness_public_read_service = HarnessPublicReadService(
            workflow_db_path=_paths.user_data_dir() / "data" / "workflow.db",
            state_db_path=_state_db_path,
            cursor_secret=hashlib.sha256(f"harness-inspector-v3:{SHARED_SECRET}".encode()).digest(),
        )
        service_context.register("harness_public_read_service", _harness_public_read_service)
        from deskpet.execution.fences import UnboundRunExecutionFence
        from deskpet.execution.provider_fault_script import ProviderFaultScriptV1
        from deskpet.execution.provider_invocations import (
            ProviderInvocationCoordinator,
        )

        _execution_fence = UnboundRunExecutionFence()
        _provider_fault_script = ProviderFaultScriptV1.from_environment()
        from deskpet.execution.tool_completion_latch_script import (
            ToolCompletionLatchScriptV1,
        )
        from deskpet.tools import registry as _runtime_tool_registry

        _runtime_tool_registry.set_tool_completion_latch(
            ToolCompletionLatchScriptV1.from_environment()
        )
        _provider_invocation_coordinator = ProviderInvocationCoordinator(
            _workflow_service.execution_uow,
            fence_reacquirer=_execution_fence.acquire,
            fault_script=_provider_fault_script,
        )
        service_context.register("run_execution_fence_acquirer", _execution_fence.acquire)
        service_context.register(
            "provider_invocation_coordinator",
            _provider_invocation_coordinator,
        )
        from deskpet.execution.provider_workload_audit import (
            ProviderWorkloadAuditStore,
        )
        from deskpet.execution.provider_workloads import (
            BackgroundModelPolicy,
            ProviderWorkloadBreaker,
            ProviderWorkloadRoutePolicy,
            ProviderWorkloadRouter,
            SessionProviderWorkloadTargetResolver,
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
            start_snapshot_reader=_workflow_service.execution_uow,
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
            from deskpet.execution.provider_fault_scenario_runner import (
                ProviderFaultScenarioRunner,
            )

            _provider_fault_scenario_runner = ProviderFaultScenarioRunner(
                _provider_fault_script,
                _provider_workload_router,
                task_owner=_provider_workload_maintenance_tasks,
            )
            await _provider_fault_scenario_runner.start()

        async def _deep_context_factory(row, start_payload) -> WorkflowContext:
            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
            return WorkflowContext(
                ports={"llm": ports.llm, "search": ports.search, "fetch": ports.fetch},
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        def _deep_state_factory(**values):
            return deep_research_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        async def _deep_v2_context_factory(row, start_payload) -> WorkflowContext:
            from deskpet.retrieval.contracts import FetchRequest, SearchRequest
            from deskpet.retrieval.query_terms import extract_query_terms
            from deskpet.retrieval.runtime import get_default_gateway

            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            legacy = legacy_ports(llm_call=llm_call, search=None, extract=None)
            gateway = get_default_gateway()
            run_id = str(row.get("run_id") or "")

            async def _search(query: str, *, max_results: int) -> list[dict]:
                response = await gateway.search(
                    SearchRequest(
                        query=query,
                        max_results=max_results,
                        mode="research",
                        run_id=run_id,
                    )
                )
                payload = response.to_dict()
                attempts = list(payload.get("attempts") or [])
                public_codes = [
                    str(value.get("public_error_code"))
                    for value in attempts
                    if isinstance(value, dict) and value.get("public_error_code")
                ]
                coverage = dict(response.coverage())
                safe_attempts = [
                    {
                        "provider": str(value.get("provider") or ""),
                        "status": str(value.get("status") or ""),
                        "permit": str(value.get("permit") or "closed"),
                        "probe_outcome": (
                            str(value.get("probe_outcome")) if value.get("probe_outcome") else None
                        ),
                        "upstream_called": bool(value.get("upstream_called", False)),
                        "is_rescue": bool(value.get("is_rescue", False)),
                    }
                    for value in attempts
                    if isinstance(value, dict)
                ]
                return ResearchSearchResults(
                    list(payload["results"]),
                    observation={
                        "request_id": str(payload.get("request_id") or ""),
                        "run_id": str(payload.get("run_id") or run_id),
                        "degraded": bool(response.degraded),
                        "reason_code": public_codes[-1] if public_codes else None,
                        "engines_tried": list(response.engines_tried),
                        "engines_hit": list(response.engines_hit),
                        "provider_attempt_count": int(coverage["actual_requests"]),
                        "provider_probe_count": int(coverage["probes"]),
                        "provider_attempts": safe_attempts,
                        "rescue_status": str(response.rescue_status),
                        "rescue_error_code": (
                            response.rescue_error_code.value
                            if response.rescue_error_code is not None
                            else None
                        ),
                        "rescue_upstream_called": bool(response.rescue_upstream_called),
                        "coverage": coverage,
                    },
                )

            async def _extract(url: str) -> dict:
                if gateway.fetch_service is None:
                    return await legacy.fetch.extract(url)
                document = await gateway.fetch_service.fetch(
                    FetchRequest(url=url, timeout=15.0, render_policy="auto")
                )
                return document.to_dict()

            async def _save_artifact(
                *, topic: str, report_md: str, report_hash: str, run_id: str
            ) -> dict[str, object]:
                return await asyncio.to_thread(
                    _workflow_research_tools.save_workflow_report,
                    topic=topic,
                    report_md=report_md,
                    report_hash=report_hash,
                    run_id=run_id,
                )

            blob_root = _paths.user_data_dir() / "workflows" / "blobs"
            return WorkflowContext(
                ports={
                    "llm": legacy.llm,
                    "search": ResearchSearchPort(
                        search_call=_search,
                        direct_call=legacy.search.direct_call,
                        reset_runtime=legacy.search.reset_runtime,
                    ),
                    "fetch": FetchPort(_extract),
                    "artifact": ResearchArtifactPort(_save_artifact),
                    "blob": RegisteredBlobStore(
                        blob_root,
                        _workflow_service.run_store.path,
                    ),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(
                            2,
                            min(
                                6,
                                int(config.workflows.deep_research_max_parallel_tasks),
                            ),
                        )
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v7_context_factory(row, start_payload) -> WorkflowContext:
            base = await _deep_v2_context_factory(row, start_payload)
            scheduler = service_context.get("subagent_scheduler")
            if scheduler is None:
                raise RuntimeError("subagent_scheduler_unavailable")
            return WorkflowContext(
                ports={**base.ports, "subagent_scheduler": scheduler},
                request_id=base.request_id,
                turn_id=base.turn_id,
            )

        async def _deep_v5_context_factory(row, start_payload) -> WorkflowContext:
            import hashlib as _hashlib
            import json as _json
            import time as _time
            from datetime import datetime as _datetime
            from datetime import timezone as _timezone

            from deskpet.retrieval.contracts import (
                DimensionSearchRequest,
                FetchRequest,
                SearchRequest,
            )
            from deskpet.retrieval.query_terms import extract_query_terms
            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.deep_research_v5_evidence_runtime import (
                candidate_from_document,
                candidate_from_json,
                evaluate_runtime_evidence,
                select_dimension_fair_rows,
            )
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                DurableResearchCallEffectAdapter,
                DurableResearchLLMStagePort,
                DurableResearchReadEffectAdapter,
                DurableResearchReadStagePort,
                DurableResearchSnapshotPort,
                DurableV5ControlPort,
                MonotonicResearchClock,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.contracts import canonical_json
            from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchBrief
            from deskpet.workflows.definitions.research_core import ResearchLLMPortV2
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            session_id = str(row.get("session_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(_paths.user_data_dir() / "workflows" / "blobs", db_path)
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)

            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v5 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v5 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode()
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v5-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v5",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            llm_effect = DurableResearchCallEffectAdapter(
                journal=journal,
                blobs=blobs,
                llm=ResearchLLMPortV2(llm_call),
                resolve_effect_context=_effect_context,
                reserve_cost_micros=lambda _role, _input, output: max(0, output * 20),
                actual_cost_micros=lambda result: max(
                    0, int((result.input_tokens or 0) * 3 + (result.output_tokens or 0) * 15)
                ),
                control_signals=signals,
            )

            gateway = get_default_gateway()

            async def _search_transport(payload, identity):
                if payload.get("_stage") == "direct":
                    return {"results": [], "executed_query_fingerprints": []}
                brief_raw = payload.get("research_brief")
                brief = ResearchBrief.from_json(brief_raw) if isinstance(brief_raw, dict) else None
                dimensions = (
                    {item.dimension_id: item for item in brief.dimensions}
                    if brief is not None
                    else {}
                )
                raw_queries = (
                    payload.get("initial_queries") or payload.get("query_strategy_queries") or []
                )
                query_rows = (
                    [item for item in raw_queries if isinstance(item, dict)]
                    if isinstance(raw_queries, list)
                    else []
                )
                work = payload.get("work_item")
                if isinstance(work, dict) and work.get("query"):
                    query_rows.append(
                        {
                            "dimension_id": work.get("dimension_id"),
                            "query": work.get("query"),
                            "source_target": work.get("source_target"),
                            "fingerprint": payload.get("query_fingerprint"),
                        }
                    )
                requests = []
                metadata = {}
                for index, item in enumerate(query_rows):
                    query = str(item.get("query") or "").strip()
                    dimension_id = str(item.get("dimension_id") or "").strip()
                    if not query or not dimension_id or dimension_id not in dimensions:
                        continue
                    fingerprint = (
                        str(item.get("fingerprint") or "").strip()
                        or _hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
                    )
                    dimension = dimensions[dimension_id]
                    semantic_context = " ".join(
                        (
                            brief.user_question if brief is not None else "",
                            dimension.question,
                            *(brief.subjects if brief is not None else ()),
                            *dimension.query_targets,
                            query,
                        )
                    )
                    semantic_terms = extract_query_terms(semantic_context)
                    request = SearchRequest(
                        query=query,
                        max_results=10,
                        mode="research",
                        run_id=identity.run_id,
                        dimension_id=dimension_id,
                        query_terms=semantic_terms,
                    )
                    requests.append(
                        DimensionSearchRequest(
                            dimension_id=dimension_id,
                            request=request,
                            core=dimension.importance == "core",
                            priority=100 - index,
                            query_terms=semantic_terms,
                            query_fingerprint=fingerprint,
                        )
                    )
                    metadata[(dimension_id, query)] = {
                        "query": query,
                        "query_fingerprint": fingerprint,
                        "source_target": str(item.get("source_target") or ""),
                    }
                batch = await gateway.search_dimension_batch(
                    requests,
                    min_results_per_core=1,
                )
                results = []
                for dimension_result in batch.results:
                    response = dimension_result.response
                    meta = metadata.get(
                        (dimension_result.dimension_id, response.query),
                        {"query": response.query, "query_fingerprint": "", "source_target": ""},
                    )
                    for candidate in response.to_dict().get("results") or []:
                        if isinstance(candidate, dict):
                            results.append(
                                {
                                    **candidate,
                                    "dimension_id": dimension_result.dimension_id,
                                    **meta,
                                }
                            )
                output = {
                    "results": results,
                    "executed_query_fingerprints": sorted(
                        {request.budget_query_key for request in requests}
                    ),
                    # The allocator keeps immutable tuples internally, while
                    # durable effect outcomes require strict JSON.  Reuse the
                    # retrieval contract's canonical projection instead of
                    # leaking the internal budget snapshot into workflow state.
                    "budget": batch.to_dict()["budget"],
                }
                # A gap unit is atomic search+fetch+admit under this durable
                # effect fence.  Returning only URLs would make gap_join claim
                # progress without adding any usable evidence.
                if payload.get("_stage") == "gap_work":
                    return await _fetch_transport({**payload, "gap_work_result": output}, identity)
                return output

            search_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_search_transport,
                control_signals=signals,
            )

            async def _fetch_transport(payload, identity):
                rows = []
                for key in ("search_result", "direct_result", "gap_work_result"):
                    value = payload.get(key)
                    if isinstance(value, dict) and isinstance(value.get("results"), list):
                        rows.extend(item for item in value["results"] if isinstance(item, dict))
                brief_raw = payload.get("research_brief")
                if not isinstance(brief_raw, dict):
                    raise ValueError("v5 evidence fetch requires research_brief")
                brief = ResearchBrief.from_json(brief_raw)
                dimension_by_id = {item.dimension_id: item for item in brief.dimensions}
                documents = []
                current_candidates = []
                fetch_rows = select_dimension_fair_rows(
                    rows,
                    tuple(dimension_by_id),
                    limit=24,
                )
                for raw in fetch_rows:
                    url = str(raw.get("url") or "")
                    dimension_id = str(raw.get("dimension_id") or "")
                    if not url or dimension_id not in dimension_by_id:
                        continue
                    try:
                        document = (
                            await gateway.fetch_service.fetch(
                                FetchRequest(
                                    url=url,
                                    timeout=20.0,
                                    render_policy="auto",
                                    run_id=identity.run_id,
                                )
                            )
                            if gateway.fetch_service is not None
                            else None
                        )
                    except Exception:
                        continue
                    if document is not None:
                        value = document.to_dict()
                        documents.append(value)
                        dimension = dimension_by_id[dimension_id]
                        year_anchors = (
                            tuple(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", brief.user_question))
                            if "official_statistics" in dimension.expected_source_types
                            else ()
                        )
                        current_candidates.append(
                            candidate_from_document(
                                dimension_id=dimension_id,
                                dimension_text=" ".join(
                                    (
                                        dimension.question,
                                        *dimension.query_targets,
                                    )
                                ),
                                query=str(raw.get("query") or ""),
                                document=value,
                                subjects=brief.subjects,
                                required_anchors=year_anchors,
                            )
                        )
                prior_candidates = []
                for value in payload.get("evidence_candidates", []):
                    if isinstance(value, dict):
                        prior_candidates.append(candidate_from_json(value))
                evaluated = evaluate_runtime_evidence(
                    brief=brief,
                    candidates=(*prior_candidates, *current_candidates),
                )
                ref_by_candidate = {}
                current_by_id = {item.candidate_id: item for item in current_candidates}
                for candidate_value in evaluated["evidence_candidates"]:
                    if not candidate_value.get("admitted"):
                        continue
                    candidate_id = str(candidate_value["candidate_id"])
                    candidate = current_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    document = next(
                        (
                            item
                            for item in documents
                            if str(item.get("canonical_url") or item.get("url") or "")
                            == str(candidate.canonical_url or candidate.url)
                        ),
                        None,
                    )
                    if document is None:
                        continue
                    encoded = canonical_json(document).encode("utf-8")
                    ref = await blobs.put(
                        encoded,
                        identity,
                        media_type="application/vnd.deskpet.research-evidence+json",
                    )
                    ref_by_candidate[candidate_id] = ref.sha256
                passage_refs = sorted(
                    set(
                        [str(item) for item in payload.get("passage_blob_refs", [])]
                        + list(ref_by_candidate.values())
                    )
                )
                executed = sorted(
                    set(
                        [str(item) for item in payload.get("executed_query_fingerprints", [])]
                        + [
                            str(item)
                            for key in ("search_result", "gap_work_result")
                            for item in (
                                payload.get(key, {}).get("executed_query_fingerprints", [])
                                if isinstance(payload.get(key), dict)
                                else []
                            )
                        ]
                    )
                )
                return {
                    "documents": documents,
                    **evaluated,
                    "passage_blob_refs": passage_refs,
                    "executed_query_fingerprints": executed,
                    "budget_summary": {"io_documents": len(documents)},
                }

            fetch_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_fetch_transport,
                control_signals=signals,
            )

            class _ArtifactStagePort:
                async def execute(self, *, stage, payload, identity):
                    synthesis = payload.get("synthesis_result")
                    synthesis = synthesis if isinstance(synthesis, dict) else {}
                    report_md = str(synthesis.get("report_md") or synthesis.get("summary") or "")
                    if not report_md:
                        raise ValueError("v5 report synthesis produced no report markdown")
                    report_hash = _hashlib.sha256(report_md.encode("utf-8")).hexdigest()
                    saved = await asyncio.to_thread(
                        _workflow_research_tools.save_workflow_report,
                        topic=str(payload.get("topic") or "research"),
                        report_md=report_md,
                        report_hash=report_hash,
                        run_id=run_id,
                    )
                    return {**saved, "report_ref": f"sha256:{saved['sha256']}"}

            return WorkflowContext(
                ports={
                    "llm": DurableResearchLLMStagePort(blobs=blobs, effect=llm_effect),
                    "search": DurableResearchReadStagePort(
                        effect=search_effect, effect_name="search"
                    ),
                    "fetch": DurableResearchReadStagePort(
                        effect=fetch_effect, effect_name="static_fetch"
                    ),
                    "artifact": _ArtifactStagePort(),
                    "control": DurableV5ControlPort(repository, signal_hub=signals),
                    "snapshot": DurableResearchSnapshotPort(blobs=blobs, repository=repository),
                    "clock": MonotonicResearchClock(),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(
                            2, min(6, int(config.workflows.deep_research_max_parallel_tasks))
                        )
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v6_context_factory(row, start_payload) -> WorkflowContext:
            import hashlib as _hashlib

            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.deep_research_v6_bootstrap import (
                build_deep_research_v6_context,
            )
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(
                _paths.user_data_dir() / "workflows" / "blobs",
                db_path,
            )
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)
            await repository.ensure_snapshot_lineage(
                run_id=run_id,
                operation_id=f"research:{run_id}",
                budget_lease_id=_hashlib.sha256(
                    f"deep-research-v6-root-budget|{run_id}".encode()
                ).hexdigest(),
            )

            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v6 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v6 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode()
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v6-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v6",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            return build_deep_research_v6_context(
                blobs=blobs,
                journal=journal,
                resolve_effect_context=_effect_context,
                search_gateway=get_default_gateway(),
                llm_call=llm_call,
                control_signals=signals,
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
                max_parallel_tasks=int(config.workflows.deep_research_max_parallel_tasks),
                dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                provider_invocation_coordinator=service_context.get(
                    "provider_invocation_coordinator"
                ),
            )

        def _deep_v2_state_factory(**values):
            return deep_research_v2_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v3_state_factory(**values):
            return deep_research_v3_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v4_state_factory(**values):
            return deep_research_v4_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v5_state_factory(**values):
            runtime = {
                **dict(values.get("research_config") or {}),
                "soft_checkpoint_seconds": config.research_v5.soft_checkpoint_seconds,
                "lease_seconds": config.research_v5.lease_seconds,
                "auto_cap_seconds": config.research_v5.auto_cap_seconds,
                "plateau_rounds": config.research_v5.plateau_rounds,
            }
            return deep_research_v5_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=runtime,
                blob_root=str(values.get("blob_root") or ""),
                operation_id=str(values.get("operation_id") or values["run_id"]),
                continuation_snapshot=(
                    dict(values["continuation_snapshot"])
                    if isinstance(values.get("continuation_snapshot"), dict)
                    else None
                ),
                parent_operation_id=(
                    str(values["parent_operation_id"])
                    if values.get("parent_operation_id") is not None
                    else None
                ),
                only_gaps=bool(values.get("only_gaps", False)),
            )

        async def _deep_v6_state_factory(**values):
            continuation_snapshot = None
            topic = str(values.get("topic") or "")
            answer_locale = str(values.get("answer_locale") or "zh-CN")
            if values.get("source_snapshot_hash") is not None:
                loader = getattr(
                    _workflow_service.research_repository,
                    "load_continuation_snapshot_v6",
                    None,
                )
                if not callable(loader):
                    raise RuntimeError("v6 continuation snapshot loader is unavailable")
                continuation_snapshot = decode_deep_research_v6_continuation_snapshot(
                    await loader(str(values["run_id"]))
                )
                blob_loader = getattr(_workflow_service, "_research_snapshot_loader", None)
                if not callable(blob_loader):
                    raise RuntimeError("v6 continuation registered blob loader is unavailable")
                spec_bytes = await blob_loader(str(continuation_snapshot["spec_ref"])[7:])
                spec_value = (
                    json.loads(spec_bytes.decode("utf-8"))
                    if isinstance(spec_bytes, bytes)
                    else spec_bytes
                )
                if not isinstance(spec_value, dict):
                    raise RuntimeError("v6 continuation spec blob is invalid")
                topic = str(spec_value.get("normalized_question") or "")
                answer_locale = str(spec_value.get("answer_locale") or "zh-CN")
            return deep_research_v6_initial_state(
                topic=topic,
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                answer_locale=answer_locale,
                schema_version=(
                    int(values["schema_version"])
                    if values.get("schema_version") is not None
                    else None
                ),
                parent_run_id=(
                    str(values["parent_run_id"])
                    if values.get("parent_run_id") is not None
                    else None
                ),
                source_snapshot_hash=(
                    str(values["source_snapshot_hash"])
                    if values.get("source_snapshot_hash") is not None
                    else None
                ),
                continuation_snapshot=continuation_snapshot,
            )

        def _deep_v7_state_factory(**values):
            return deep_research_v7_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        _workflow_launcher.register_adapter(
            "deep_research",
            "v1",
            state_factory=_deep_state_factory,
            context_factory=_deep_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v2",
            state_factory=_deep_v2_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v3",
            state_factory=_deep_v3_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v4",
            state_factory=_deep_v4_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v5",
            state_factory=_deep_v5_state_factory,
            context_factory=_deep_v5_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v6",
            state_factory=_deep_v6_state_factory,
            context_factory=_deep_v6_context_factory,
            extensions={
                DEEP_RESEARCH_EXTENSION: DeepResearchRuntimeExtension(
                    new_runs_enabled=True,
                    action_ids=("generate_now", "continue_research", "cancel_settle"),
                    continuation=DeepResearchContinuationAdapter(
                        decode_snapshot=decode_deep_research_v6_continuation_snapshot,
                        build_child_payload=build_deep_research_v6_continuation_payload,
                    ),
                    terminal_commit_capability=TERMINAL_COMMIT_CAPABILITY,
                )
            },
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v7",
            state_factory=_deep_v7_state_factory,
            context_factory=_deep_v7_context_factory,
        )

        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _RecoveryShim
        from deskpet.workflows.adapters.code_runtime import (
            DurableToolRuntimeState as _RecoveryDurableToolRuntimeState,
        )
        from deskpet.workflows.adapters.code_runtime import (
            ProposalPort as _RecoveryProposalPort,
        )
        from deskpet.workflows.adapters.code_runtime import (
            ToolDispatchPort as _RecoveryToolDispatchPort,
        )
        from deskpet.workflows.definitions.v1 import (
            code_complex_initial_state as _code_recovery_initial_state,
        )
        from deskpet.workflows.definitions.v1 import (
            durable_task_initial_state as _durable_task_initial_state,
        )
        from deskpet.workflows.output_contract import (
            TaskOutputContractPort as _TaskOutputContractPort,
        )
        from deskpet.workflows.output_contract import (
            TaskOutputContractV1 as _TaskOutputContractV1,
        )

        def _code_recovery_state_factory(**values):
            return _code_recovery_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
                output_contract=dict(values.get("output_contract") or {}),
            )

        def _durable_task_state_factory(**values):
            return _durable_task_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
                output_contract=dict(values.get("output_contract") or {}),
            )

        async def _code_recovery_context_factory(row, start_payload):
            provider_snapshot = dict(start_payload.get("provider_snapshot") or {})
            model_snapshot = dict(start_payload.get("model_snapshot") or {})
            provider = _resolve_code_recovery_provider(
                provider_snapshot=provider_snapshot,
                model_snapshot=model_snapshot,
                registry=service_context.get("provider_registry"),
                fallback=host_state.local_llm or cloud_llm,
            )
            shim = _RecoveryShim(provider=provider)
            session_id = str(row.get("session_id") or "default")
            workflow_name = str(row.get("workflow_name") or "code_complex")
            from deskpet.harness.adapters.legacy_execution_migration import (
                resolve_frozen_legacy_workspace,
            )

            workspace = await resolve_frozen_legacy_workspace(
                row=row,
                start_payload=start_payload,
                session_db=service_context.get("session_db"),
            )
            provider_name = str(
                provider_snapshot.get("provider") or provider_snapshot.get("provider_id") or ""
            )
            model_name = str(model_snapshot.get("model") or "")
            allowed_tools = tuple(
                str(item.get("tool_name") or "")
                for item in (start_payload.get("capability_snapshot") or ())
                if isinstance(item, dict) and item.get("tool_name")
            )
            raw_context_os = start_payload.get("context_os")
            prepared_tool_set = None
            eligibility = None
            execution_context = None
            context_os_enabled = bool(
                getattr(getattr(config, "features", None), "context_os_v1", False)
            )
            if context_os_enabled:
                if not isinstance(raw_context_os, dict):
                    raise RuntimeError("code recovery Context OS snapshot is unavailable")
                from deskpet.tools.capabilities import ToolExecutionContext
                from deskpet.tools.prepared_snapshot import load_context_os_snapshot

                prepared_tool_set, eligibility = load_context_os_snapshot(raw_context_os)
                request_id = str(start_payload.get("request_id") or row.get("request_id") or "")
                turn_id = str(start_payload.get("turn_id") or row.get("turn_id") or "")
                if eligibility.session_id != session_id or eligibility.request_id != request_id:
                    raise RuntimeError("code recovery Context OS identity mismatch")
                deskpet_tool_registry_v2.validate_prepared_tool_set(
                    prepared_tool_set, eligibility=eligibility
                )
                prepared_names = {
                    capability.ref.name
                    for capability in (
                        *prepared_tool_set.direct,
                        *prepared_tool_set.activated,
                    )
                }
                _validate_code_recovery_capability_subset(
                    prepared_names=prepared_names,
                    allowed_tools=allowed_tools,
                )
                scope_store = service_context.get("tool_capability_scope_store")
                if scope_store is None:
                    raise RuntimeError("code recovery capability scope store unavailable")
                if (
                    scope_store.get(
                        prepared_tool_set.scope_id,
                        session_id=session_id,
                        request_id=request_id,
                    )
                    is None
                ):
                    try:
                        scope_store.open(prepared_tool_set, eligibility)
                    except ValueError:
                        if (
                            scope_store.get(
                                prepared_tool_set.scope_id,
                                session_id=session_id,
                                request_id=request_id,
                            )
                            is None
                        ):
                            raise
                from deskpet.tools.capabilities import canonical_hash

                scope_hash = canonical_hash(
                    {
                        "session_id": session_id,
                        "workspace": workspace,
                        "write_scope_root": workspace,
                        "venue": ("code" if workflow_name == "code_complex" else "text"),
                    }
                )
                execution_snapshot = await _workflow_service.execution_uow.read_run_start_snapshot(
                    str(row.get("run_id") or "")
                )
                frozen_run_context = (
                    json.loads(execution_snapshot.run_context_json)
                    if execution_snapshot is not None
                    else {}
                )
                root_run_id = str(
                    frozen_run_context.get("root_run_id")
                    or row.get("parent_run_id")
                    or row.get("run_id")
                    or ""
                )
                execution_context = ToolExecutionContext(
                    scope_id=prepared_tool_set.scope_id,
                    session_id=session_id,
                    request_id=request_id,
                    origin="agent",
                    root_run_id=root_run_id,
                    parent_run_id=(str(row.get("parent_run_id") or "").strip() or None),
                    turn_id=turn_id,
                    venue=("code" if workflow_name == "code_complex" else "text"),
                    workspace=workspace,
                    write_scope_root=workspace,
                    capability_hash=prepared_tool_set.schema_fingerprint,
                    scope_hash=scope_hash,
                    provider_plan=tuple(item for item in (provider_name, model_name) if item),
                    run_id=str(row.get("run_id") or ""),
                    trace_id=str(row.get("trace_id") or ""),
                )
            runtime_tool_state = (
                _RecoveryDurableToolRuntimeState(prepared_tool_set)
                if prepared_tool_set is not None
                else None
            )
            output_contract = None
            output_contract_port = None
            if workflow_name == "durable_task":
                raw_output_contract = start_payload.get("output_contract")
                if isinstance(raw_output_contract, dict):
                    output_contract = _TaskOutputContractV1.from_dict(raw_output_contract)
                    if Path(output_contract.workspace_root) != Path(workspace):
                        raise RuntimeError("durable task output contract workspace mismatch")
                    output_contract_port = _TaskOutputContractPort(output_contract)
                elif start_payload.get("capability_builder") is None:
                    # Compatibility read for Runs created before
                    # task-output-contract-v1 shipped. New durable-task launches
                    # are rejected by the host unless they carry a frozen
                    # contract, but making an old checkpoint unrecoverable would
                    # turn a safety upgrade into data loss.
                    logger.warning(
                        "durable_task_legacy_output_contract_missing",
                        run_id=str(row.get("run_id") or ""),
                        root_run_id=str(row.get("root_run_id") or ""),
                    )
            ports = {
                "llm": _RecoveryProposalPort(
                    shim,
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    profile_key=f"workflow.{workflow_name}",
                    allowed_tools=allowed_tools,
                    context_os_v1=context_os_enabled,
                    prepared_tool_set=prepared_tool_set,
                    eligibility=eligibility,
                    execution_context=execution_context,
                    capability_scope_store=service_context.get("tool_capability_scope_store"),
                    runtime_tool_state=runtime_tool_state,
                    dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                    provider_invocation_coordinator=service_context.get(
                        "provider_invocation_coordinator"
                    ),
                    context_compressor=service_context.get("context_compressor"),
                    output_contract=output_contract,
                ),
                "tool": _RecoveryToolDispatchPort(
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    execution_context=execution_context,
                    runtime_tool_state=runtime_tool_state,
                    workflow_name=workflow_name,
                    dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                ),
            }
            if output_contract_port is not None:
                ports["output_contract"] = output_contract_port
            return WorkflowContext(
                ports=ports,
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        _workflow_launcher.register_adapter(
            "code_complex",
            "v1",
            state_factory=_code_recovery_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        _workflow_launcher.register_adapter(
            "durable_task",
            "v1",
            state_factory=_durable_task_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        # Import the actual ToolRegistry instance for personal_runtime_adapter
        from deskpet.tools.registry import registry as _actual_tool_registry
        from deskpet.workflows.adapters.personal_runtime import (
            build_personal_runtime_adapter,
        )
        from deskpet.workflows.effects import EffectJournal as _PersonalEffectJournal

        _workflow_service.runtime_adapters.register(
            build_personal_runtime_adapter(
                journal=_PersonalEffectJournal(_workflow_service.run_store.path),
                tool_registry=_actual_tool_registry,
                run_start_snapshot_reader=(_workflow_service.execution_uow.read_run_start_snapshot),
                dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
            )
        )

        # New product workflow starts are owned exclusively by RunKernel.
        # The legacy tool handlers remain library-compatible but are not wired
        # as production launch ingress.
        _workflow_research_tools.set_deepresearch_workflow_starter(None)
        try:
            _configure_ppt_production(recover=False)
            logger.info("ppt_pro_services_wired")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_pro_services_wire_failed", error=str(exc))
        from deskpet.workflows.startup_recovery import activate_and_recover_workflows

        _workflow_recovery = await activate_and_recover_workflows(
            project_bindings=_project_binding_service,
            workflow_service=_workflow_service,
            workflow_launcher=_workflow_launcher,
            required_runtime_identities=_workflow_service.runtime_adapters.identities(),
        )
        _delete_reconcile = _workflow_recovery["delete_reconcile"]
        startup_recoveries = _workflow_recovery["startup_recoveries"]
        recovered_decisions = _workflow_recovery["recovered_decisions"]
        recovered_deliveries = _workflow_recovery["recovered_deliveries"]
        recovered_runs = _workflow_recovery["recovered_runs"]
        if _delete_reconcile["failed"]:
            logger.warning(
                "session_delete_cancel_reconcile_incomplete",
                **_delete_reconcile,
            )
        logger.info(
            "workflow_service_ready",
            db_path=str(_workflow_service.run_store.path),
            versions=_workflow_service.runner.registry.versions(),
            recovered_runs=len(recovered_runs),
            startup_recoveries=len(startup_recoveries),
            recovered_decisions=len(recovered_decisions),
            recovered_deliveries=len(recovered_deliveries),
        )
    except Exception as exc:  # noqa: BLE001 - old ReAct stays available
        service_context.register("workflow_service", None)
        service_context.register("run_execution_fence_acquirer", None)
        service_context.register("provider_invocation_coordinator", None)
        try:
            from deskpet.tools import research_tools as _workflow_research_tools

            _workflow_research_tools.set_deepresearch_workflow_starter(None)
        except Exception:
            pass
        logger.warning("workflow_service_init_failed", error=str(exc))
    _expire_ppt_outline_dangling_for_startup()
    # R-T1：goal_store 启动恢复（重启仍在的核心路径）。
    _gs_for_restore = service_context.get("session_goal_store")
    if _gs_for_restore is not None:
        try:
            _n = await _gs_for_restore.load_persisted()
            logger.info("goal_store_load_persisted restored=%d", _n)
        except Exception as _lp_exc:  # noqa: BLE001
            logger.warning("goal_store_load_persisted_failed: %s", _lp_exc)
    # FP-4 WI-3.3 ★ FIX BUG: FactsStore.daily_decay() was never called in prod.
    # Apply once at startup (mirrors retriever's "run once at startup" convention).
    # Pinned facts are skipped (WI-3.3). Failure is non-fatal — only logs.
    _fs_for_decay = service_context.get("facts_store")
    if _fs_for_decay is not None:
        try:
            _decay_n = await _fs_for_decay.daily_decay()
            logger.info("p4_facts_daily_decay_startup", mutated=_decay_n)
        except Exception as _dd_exc:  # noqa: BLE001
            logger.warning("p4_facts_daily_decay_failed", error=str(_dd_exc))
    _legacy_sl = service_context.get("skill_loader")
    _managed_sl = service_context.get("managed_skill_discovery_projection")
    for _skill_surface, _skill_surface_name in (
        (_legacy_sl, "legacy_user"),
        (_managed_sl, "managed_first_party"),
    ):
        if _skill_surface is None:
            continue
        try:
            await _skill_surface.start()
            logger.info(
                "p4_skill_surface_ready",
                surface=_skill_surface_name,
                count=len(_skill_surface.list_skills()),
            )
        except Exception as exc:
            logger.warning(
                "p4_skill_surface_start_failed",
                surface=_skill_surface_name,
                error=str(exc),
            )
    # TC-5.1 修复 (2026-06-11)：SkillMatcher 缓存预热。module-level 的 sync
    # build() 对 async 生产 embedder 是静默 no-op → 这里 loader 启动后台跑
    # build_async（encode 内部自动 warmup embedder，不阻塞启动）。失败无害——
    # match_async 仍会逐 turn 惰性建缓存。
    _sm_for_prewarm = service_context.get("skill_matcher")
    _managed_skills_for_prewarm = service_context.get("managed_skill_discovery_projection")
    if _sm_for_prewarm is not None and _managed_skills_for_prewarm is not None:

        async def _skill_matcher_prewarm_bg() -> None:
            try:
                _n = await _sm_for_prewarm.build_async(_managed_skills_for_prewarm.all())
                logger.info("fp5_skill_matcher_prewarmed", cached=_n)
            except Exception as _pw_exc:  # noqa: BLE001
                logger.warning("fp5_skill_matcher_prewarm_failed", error=str(_pw_exc))

        # fire-and-forget; we deliberately don't await (same as embedder warmup)
        asyncio.create_task(_skill_matcher_prewarm_bg())
    # Preload the registered production embedder in the background. Reopening
    # an existing short generation reuses vectors and does not load the encoder.
    _emb = service_context.get("embedder")
    if _emb is not None and callable(getattr(_emb, "warmup", None)):

        async def _embedder_warmup_bg() -> None:
            try:
                await _emb.warmup()
                logger.info("p4_embedder_ready", embedder_kind=getattr(_emb, "kind", None))
            except Exception as exc:
                logger.warning("p4_embedder_warmup_failed", error=str(exc))

        # fire-and-forget; we deliberately don't await
        asyncio.create_task(_embedder_warmup_bg())
    # Option A (2026-06-05): 首启模型下载。瘦包(DESKPET_BUNDLE_MODELS=0)不内嵌
    # 模型 → 后台 daemon 线程从 hf-mirror 拉缺失的 bge-m3 / faster-whisper。
    # 注册到 service_context 供 p4_ipc 的 model_provision_status 探针读取；
    # start_background 幂等。失败不阻塞启动（ASR/记忆各自有降级）。
    try:
        from deskpet.model_provisioner import ModelProvisioner

        _provisioner = ModelProvisioner()
        service_context.register("model_provisioner", _provisioner)
        _provisioner.start_background()
        logger.info("model_provisioner_started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_provisioner_start_failed", error=str(exc))
    # Task 13 retires the JSON PreferenceMemory writer. The existing
    # preference_memory.json file is imported once by the authority cutover
    # reader and remains read-only upgrade residue.
    # P4-S15: VectorWorker — starts after SessionDB is initialised so the
    # vec0 schema is in place. After start, wire its enqueue() onto the
    # SessionDB write-hook so new chat turns auto-embed.
    _vw = service_context.get("vector_worker")
    _provider_workload_audit = service_context.get("provider_workload_audit")
    if _provider_workload_audit is not None:
        from deskpet.execution.provider_workload_audit import (
            provider_workload_audit_retention_loop,
        )

        _provider_audit_retention_task = asyncio.create_task(
            provider_workload_audit_retention_loop(_provider_workload_audit),
            name="provider-workload-audit-retention",
        )
        _provider_workload_maintenance_tasks.add(_provider_audit_retention_task)
        _provider_audit_retention_task.add_done_callback(
            _provider_workload_maintenance_tasks.discard
        )
    if _vw is not None and _sdb is not None:
        try:
            await _vw.start()

            # 记忆系统升级 WI-M1.2: _on_message_written 从「只触发
            # VectorWorker」升级为组合 fanout —— VectorWorker 永远跑，
            # facts 抽取按 config.memory.v2.facts_extract 决定。hook 仍是
            # 2 参 (mid, text)（不动既有调用点）；facts 需要的 role 由
            # SessionDB.get_message_role 按 mid 反查。facts 抽取走
            # asyncio.create_task → 不阻塞 append_message（shadow 模式）。
            async def _on_message_fanout(mid: int, text: str) -> None:
                _routing = await _sdb.get_message_routing_context(mid)
                if _routing is None:
                    logger.warning(
                        "memory_fanout_missing_message_routing_context",
                        message_id=mid,
                    )
                    return
                _role = _routing["role"]
                if _role not in {"user", "assistant"}:
                    logger.debug(
                        "memory_fanout_skipped_non_conversation_role",
                        message_id=mid,
                        role=_role,
                    )
                    return
                await _vw.enqueue(mid, text)
                if config.memory.v2.facts_extract and _fact_extractor is not None:
                    _root_run_id = str(_routing.get("root_run_id") or "")
                    if not _root_run_id:
                        logger.info(
                            "facts_extract_skipped_unscoped_message",
                            message_id=mid,
                        )
                    else:
                        from deskpet.execution.provider_workloads import (
                            workload_context as _workload_context,
                        )

                        _facts_workload_context = _workload_context(
                            "memory.fact_extract",
                            request_id=f"message:{mid}",
                            session_id=str(_routing["session_id"]),
                            root_run_id=_root_run_id,
                            task_scope_id=_routing.get("task_scope_id"),
                        )

                    async def _extract_facts_bg(
                        _mid: int,
                        _text: str,
                        _workload=None,
                    ) -> None:
                        try:
                            await _fact_extractor.process_message(
                                message_id=_mid,
                                content=_text,
                                role=_role,
                                workload_context=_workload,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "facts_extract_bg_failed mid=%s err=%s",
                                _mid,
                                _ex,
                            )

                    if _root_run_id:
                        asyncio.create_task(_extract_facts_bg(mid, text, _facts_workload_context))
                # WI-M1.5: chunking —— 长消息切块 + embed 进
                # messages_chunks。异步不阻塞 append_message（shadow）。
                if config.memory.v2.chunking and _message_chunker is not None:

                    async def _chunk_bg(_mid: int, _text: str) -> None:
                        try:
                            await _message_chunker.chunk_message(
                                message_id=_mid,
                                content=_text,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "chunk_bg_failed mid=%s err=%s",
                                _mid,
                                _ex,
                            )

                    asyncio.create_task(_chunk_bg(mid, text))

            _sdb._on_message_written = _on_message_fanout  # type: ignore[attr-defined]
            logger.info(
                "p4_vector_worker_ready",
                facts_extract=config.memory.v2.facts_extract,
            )

            # 2026-06-02 记忆审计 FATAL-A 修复：自动 backfill 安全网。
            # 在此之前 backfill_missing() 只在手动脚本（scripts/backfill_vectors）
            # 里调，lifespan 从不调用 —— 任何 embedding 缺口（worker 关机丢最后
            # 一批 / encode 失败 / 子进程崩）都会让 messages.embedding 永久 IS
            # NULL 且无声，向量召回永远漏掉这些消息（"刚说的话下次不记得"）。
            # 桌宠用户永远不会手动跑脚本。这里在 worker 起来后 fire-and-forget
            # 跑一次回填：backfill_missing 内部自带 embedder warmup + 分批 sleep
            # 礼让实时 enqueue（vector_worker.py:198-208），不阻塞冷启动。
            async def _vector_backfill_bg() -> None:
                try:
                    # 等 embedder 暖机 + 冷启动峰值过去再回填，别和首轮抢资源。
                    await asyncio.sleep(60.0)
                    _n = await _vw.backfill_missing()
                    if _n:
                        logger.info("p4_vector_backfill_done", processed=_n)
                except Exception as _bf_exc:  # noqa: BLE001
                    logger.warning("p4_vector_backfill_failed", error=str(_bf_exc))

            asyncio.create_task(_vector_backfill_bg())
        except Exception as exc:
            logger.warning("p4_vector_worker_start_failed", error=str(exc))
    # 记忆系统 reflection 低频任务已随旧记忆系统移除（等待 simple-harness-memory-sdk）。

    # WI-OH-4: 记忆 self-curation nudge — flag memory.v2.curation_nudge 开 +
    # facts store + LLM 可用 → 构造 MemoryCurator 存进 service_context，由
    # build_agent 接电到 agent_loop（每 N 回合 fire-and-forget nudge）。
    # flag 关 / 无 facts / 无 LLM → curator=None → agent_loop 不调 nudge（BC）。
    # 2026-06-23 修生产死链：旧代码 flag=False 时**整个 if 不进、零日志** → 真测时
    # 无法区分「flag 没开」vs「facts_store 没接上」vs「LLM 缺失」，曾误诊为接线 bug。
    # 改成显式三分支 skip-log（reason=flag_off / no_facts_store / no_llm_provider），
    # 任何「curator 没接上」都在启动日志留痕，杜绝静默死链复发。facts_store 用
    # module-global 优先、service_context 兜底（两者在 startup 均已就绪，见
    # p4_services_registered + memory_tools.bind 日志）。
    _cur_facts = _facts_store if _facts_store is not None else service_context.get("facts_store")
    if not config.memory.v2.curation_nudge:
        logger.info("oh4_curation_skipped", reason="flag_off")
    elif _cur_facts is None:
        logger.info("oh4_curation_skipped", reason="no_facts_store")
    # OpenSpec 2026-05-16-async-image-gen: start the ImageGenerationWorker
    # unless async disabled (then generate_image runs legacy sync).
    _iw = service_context.get("image_worker")
    _img_async = bool(
        ((config.raw.get("image") if hasattr(config, "raw") else None) or {}).get(
            "async_enabled", True
        )
    )
    if _iw is not None and _img_async:
        try:
            await _iw.start()
            logger.info("image_worker_ready")
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_start_failed", error=str(exc))
    # P4-S15: MCPManager — bootstrap from raw [mcp] section. start() is
    # tolerant: missing section / disabled servers / spawn failures are all
    # logged but don't raise. Only the manager handle is registered; the
    # actual server states are inspectable via manager.server_state().
    try:
        # P4-S18: ensure workspace dir exists before spawning filesystem MCP
        # server. Without this, npx @modelcontextprotocol/server-filesystem
        # spawns OK but its first stat() fails with ENOENT, MCP transport
        # closes, and our manager spins in reconnect loop forever (logged
        # every few seconds, polluting startup output). Touching the dir
        # is idempotent and cheap; agents are still scoped to it.
        try:
            _ws_dir = _paths.user_data_dir() / "workspace"
            _ws_dir.mkdir(parents=True, exist_ok=True)
        except Exception as _ws_exc:  # pragma: no cover — best-effort
            logger.warning("workspace_mkdir_failed", error=str(_ws_exc))

        from deskpet.mcp.bootstrap import create_and_start_from_config as _mcp_bootstrap

        # P6 bugfix 2026-05-13: MCP manager 用的是 deskpet.tools.registry.ToolRegistry
        # 的 keyword API (register(name=..., toolset=..., schema=..., handler=...))，
        # 不是老的 tools.registry.ToolRegistry (register(tool) 单参)。传错 registry
        # 会导致每个 MCP 工具都 TypeError("unexpected keyword 'name'") 注册失败。
        _mcp_app_config = config.raw
        if (
            _context_os_e2e_boot_hooks is not None
            and os.environ.get("DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG", "1").strip() != "0"
        ):
            import copy as _copy_e2e_mcp

            _mcp_app_config = _copy_e2e_mcp.deepcopy(config.raw)
            _mcp_app_config["mcp"] = {
                "enabled": True,
                "servers": [
                    {
                        "name": "context-os-e2e",
                        "enabled": True,
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [
                            str(
                                Path(__file__).resolve().parents[1]
                                / "scripts"
                                / "e2e"
                                / "context_os_mcp_fixture.py"
                            )
                        ],
                        "env": {
                            "DESKPET_DEV_MODE": "1",
                            "DESKPET_CONTEXT_OS_E2E_DAEMON_URL": (
                                _context_os_e2e_boot_hooks.daemon_url
                            ),
                        },
                    }
                ],
            }
        _mcp_manager = await _mcp_bootstrap(
            app_config=_mcp_app_config,
            tool_registry=deskpet_tool_registry_v2,
        )
        deskpet_tool_registry_v2.set_mcp_catalog_stale_callback(_mcp_manager.handle_catalog_stale)
        service_context.register("mcp_manager", _mcp_manager)
        logger.info("p4_mcp_manager_ready", states=_mcp_manager.server_state())
    except Exception as exc:
        logger.warning("p4_mcp_manager_bootstrap_failed", error=str(exc))

    # P4-S20-D 启动时后台总结老对话任务已随旧记忆系统移除（等待 SDK）。

    # NEXT-TG-1.0 §9：工具熔断与自动续跑不再挂在监工开关下（用户关了监工 Agent，
    # 但这两项是确定性的自我修复，应当照常工作）。开关在 [self_healing]，默认开；
    # 阈值等旧参数仍从 [supervisor] 读，写在 [self_healing] 的优先。
    _sup_cfg_all = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
    _heal_cfg = {
        **_sup_cfg_all,
        **((config.raw.get("self_healing") if hasattr(config, "raw") else None) or {}),
    }
    if bool(_heal_cfg.get("circuit_breaker_enabled", True)):
        try:
            from agent.circuit_breaker import ToolCircuitBreaker as _ToolBreaker

            if deskpet_tool_registry_v2 is not None:
                _breaker = _ToolBreaker(
                    threshold=int(_heal_cfg.get("circuit_breaker_threshold", 3)),
                    cooldown_seconds=float(_heal_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                )
                deskpet_tool_registry_v2.set_circuit_breaker(_breaker)
                service_context.register("tool_circuit_breaker", _breaker)
                logger.info(
                    "p5s2_circuit_breaker_wired threshold=%d cooldown=%.0fs",
                    int(_heal_cfg.get("circuit_breaker_threshold", 3)),
                    float(_heal_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("p5s2_circuit_breaker_wire_failed err=%s", _exc)

    # 会话活动记录：自动续跑按会话计次（上限 max_auto_resume_attempts）、
    # 监工看门狗读会话状态都靠它；监工开关与它无关，总是注册。
    if service_context.get("session_activity") is None:
        from agent.session_activity import default_store as _sa_default_store

        service_context.register("session_activity", _sa_default_store())

    # P5-S1/S2: supervisor watchdog + LLM agent. Starts after the rest of
    # startup is done so the 30s grace can run while normal startup races
    # finish. Disabled when [supervisor].enabled = false; in that case we
    # skip construction entirely so the asyncio.Task isn't even created.
    try:
        _sup_cfg = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
        if bool(_sup_cfg.get("enabled", False)):
            from agent.snapshot import build_snapshot as _build_snap_func
            from agent.supervisor import (
                SupervisorAgent as _SupAgent,
            )
            from agent.supervisor import (
                build_supervisor_hook as _build_sup_hook,
            )
            from agent.watchdog import WatchdogLoop as _WatchdogLoop

            # Snapshot builder closure — pulls services lazily so each
            # tick reads fresh state.
            async def _snap_builder(sid: str):
                _ctx_window = int(
                    (config.raw.get("agent") or {}).get("context_window_tokens", 200_000)
                )
                return await _build_snap_func(
                    sid,
                    session_activity=service_context.get("session_activity"),
                    session_db=service_context.get("session_db"),
                    context_window_tokens=_ctx_window,
                )

            # Audit closure — persists every non-wait SupervisorAction
            async def _audit_action(action, sid):
                _sdb_for_audit = service_context.get("session_db")
                if _sdb_for_audit is None:
                    return
                hint_text = action.hint_for_main_agent or action.user_message or action.diagnosis
                await _sdb_for_audit.append_supervisor_hint(
                    session_id=sid,
                    alert_id=action.alert_id,
                    hint_text=hint_text,
                    action="cancel_coerced" if action.raw_action == "cancel" else action.action,
                    severity=action.severity,
                    diagnosis=action.diagnosis,
                )

            # Nudge push closure — wraps SupervisorAction → Hint and
            # forwards to the queue.
            async def _push_hint(sid: str, action):
                from agent.nudge_queue import Hint as _Hint

                _nq = service_context.get("nudge_queue")
                if _nq is None:
                    return
                await _nq.push(
                    sid,
                    _Hint(
                        text=action.hint_for_main_agent or action.user_message or "",
                        alert_id=action.alert_id,
                        severity=action.severity,
                    ),
                )

            # Broadcast closure — sends supervisor_alert to all control WS.
            async def _broadcast_supervisor_alert(typ: str, payload: dict):
                # Use the same multi-WS fan-out the todo broadcaster uses.
                # _control_connections is the canonical mapping (sid → ws).
                if not _control_connections:
                    return
                msg = {"type": typ, "payload": payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug(
                            "supervisor_alert_broadcast_failed sid=%s error=%s", _sid_key, _bex
                        )

            # Build agent. Provider resolution (multi-provider-management):
            # 1) `[supervisor].provider_id = "<id>"` in config.toml — explicit pin
            # 2) Else first enabled provider from LLMProviderRegistry.get_chain()
            # 3) Else legacy `local_llm` (single-provider mode, pre-multi-provider)
            #
            # Previously hardcoded `local_llm` which on a multi-provider deploy
            # without Ollama running produced "supervisor_unavailable: ConnectError"
            # — confusing because the main chat works fine via the relay. Now
            # supervisor follows the user's chain by default.
            _sup_provider = None
            try:
                _reg_for_sup = service_context.get("provider_registry")
                if _reg_for_sup is not None:
                    _sup_pinned_id = (_sup_cfg.get("provider_id") or "").strip()
                    _sup_entry = None
                    if _sup_pinned_id:
                        _sup_entry = _reg_for_sup.get_entry(_sup_pinned_id)
                        if _sup_entry is None or not getattr(_sup_entry, "enabled", True):
                            logger.warning(
                                "supervisor_pinned_provider_missing pid=%s — falling back to chain",
                                _sup_pinned_id,
                            )
                            _sup_entry = None
                    if _sup_entry is None:
                        _chain = []
                        try:
                            _chain = _reg_for_sup.get_chain()
                        except Exception:
                            _chain = []
                        if _chain:
                            _sup_entry = _reg_for_sup.get_entry(_chain[0]["id"])
                    if _sup_entry is not None:
                        _sup_api_key = _reg_for_sup.resolve_api_key(_sup_entry.id) or "ollama"
                        # Optional [supervisor].model override (defaults to entry's default_model)
                        _sup_model_override = (_sup_cfg.get("model") or "").strip()
                        _sup_model = _sup_model_override or _sup_entry.model
                        _sup_provider = OpenAICompatibleProvider(
                            base_url=_sup_entry.base_url,
                            api_key=_sup_api_key,
                            model=_sup_model,
                            temperature=0.1,
                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                        )
                        logger.info(
                            "supervisor_provider_resolved id=%s base_url=%s model=%s",
                            _sup_entry.id,
                            _sup_entry.base_url,
                            _sup_model,
                        )
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "supervisor_provider_resolve_failed err=%s — falling back to local_llm",
                    str(_exc)[:200],
                )
            if _sup_provider is None:
                # Last-resort fallback: legacy single-provider mode.
                _sup_provider = host_state.local_llm
                logger.info(
                    "supervisor_provider_resolved id=legacy base_url=%s — "
                    "registry empty/unavailable; consider adding a provider via Settings",
                    getattr(host_state.local_llm, "base_url", "?"),
                )

            # P6 bugfix 2026-05-14 (live-test): auto-mode bypass for
            # supervisor. When permission_gate.auto_mode is ON, the user
            # has delegated "decide for me" semantics — supervisor must
            # NOT block on UI buttons (ask_user). Below callbacks let
            # SupervisorAgent self-drive: check auto state + spawn a
            # follow-up chat task with "<<supervisor_followup>>" trigger.
            def _supervisor_auto_mode_check() -> bool:
                try:
                    return bool(
                        permission_gate_v2 is not None
                        and getattr(permission_gate_v2, "auto_mode", False)
                    )
                except Exception:
                    return False

            async def _supervisor_auto_followup(sid: str, trigger_text: str) -> None:
                # Spawn a chat task with the synthetic trigger text. We
                # look up the most recent control WS for this sid and
                # use the registered re-dispatcher closure (set by chat
                # handler each user turn through the durable Harness ingress).
                # If no dispatcher is registered yet, we silently no-op
                # — the queued hint will be consumed when the user (or
                # auto_resume) next pokes the agent.
                _target_ws = _control_connections.get(sid)
                if _target_ws is None:
                    # Fall back to ANY control WS — supervisor alerts are
                    # already broadcast to all of them.
                    for _k, _w in _control_connections.items():
                        _target_ws = _w
                        break
                if _target_ws is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_ws",
                        sid,
                    )
                    return
                try:
                    _launch_product_harness_chat(_target_ws, trigger_text, sid)
                except Exception as _ex:
                    logger.warning(
                        "supervisor_auto_followup_dispatch_failed sid=%s err=%s",
                        sid,
                        _ex,
                    )

            _supervisor_agent = _SupAgent(
                provider=_sup_provider,
                snapshot_builder=_snap_builder,
                nudge_queue_push=_push_hint,
                broadcast=_broadcast_supervisor_alert,
                audit=_audit_action,
                auto_mode_check=_supervisor_auto_mode_check,
                auto_followup=_supervisor_auto_followup,
                # P5-S2 G2 (2026-05-12): 30s→120s. supervisor was timing out
                # on deepseek-v4-pro thinking-mode calls — the model takes
                # 30-60s just thinking before emitting the 300-token JSON
                # spec. 120s covers reasoning + transit + the relay 15s SSE
                # keep-alive. config knob name unchanged for backward compat.
                timeout_seconds=float(_sup_cfg.get("llm_timeout_seconds", 120.0)),
            )
            service_context.register("supervisor", _supervisor_agent)

            # P5-S2 Hook B probe: same logic as the agent_loop probe but
            # captured at a different scope (lifespan vs per-chat). Maps
            # session id → SessionDB.get_session_todos → filter incomplete.
            # Returned to the watchdog so the (c) trigger
            # rule (idle with todos) can fire even when there's no
            # active chat task.
            # P6 bugfix 2026-05-14 (用户反馈): 长时间未操作时不要骚扰用户。
            # 即使 todos 未完成、agent 状态 idle，也判断用户是否还在用 deskpet
            # （以最近 user 消息时间为准）。30 分钟无 user 活动 → 视为用户
            # 离开，不再主动弹 "Agent seems stuck" 提醒。
            _user_idle_grace_s = float(_sup_cfg.get("user_idle_grace_seconds", 1800))

            async def _watchdog_incomplete_todos_probe(_base_sid: str) -> list[dict]:
                try:
                    sdb_local = service_context.get("session_db")
                    if sdb_local is None:
                        return []
                    # P6 bugfix 2026-05-14: check user activity first.
                    # 如果用户 > N 分钟没发消息 → 视为离开，不报警。
                    # 即使 agent 自己拆了 todos，用户没主动让它做 →
                    # 我们也不该自动催 agent 干。
                    try:
                        import time as _t

                        last_user_ts = (
                            await sdb_local.last_message_ts(session_id=_base_sid, role="user")
                            if hasattr(sdb_local, "last_message_ts")
                            else None
                        )
                        if last_user_ts is None:
                            # Fallback: scan messages directly
                            _recent = await sdb_local.get_messages(_base_sid, limit=50)
                            _user_ts = [
                                float(r.get("created_at") or 0)
                                for r in _recent
                                if (r.get("role") or "") == "user"
                            ]
                            last_user_ts = max(_user_ts) if _user_ts else 0.0
                        if last_user_ts <= 0:
                            # No user message ever for this sid → never auto-poke
                            logger.debug(
                                "p6_watchdog_skip_no_user_history sid=%s",
                                _base_sid,
                            )
                            return []
                        user_idle_age = _t.time() - last_user_ts
                        if user_idle_age > _user_idle_grace_s:
                            logger.info(
                                "p6_watchdog_skip_user_away sid=%s user_idle_age=%.0fs grace=%.0fs",
                                _base_sid,
                                user_idle_age,
                                _user_idle_grace_s,
                            )
                            return []
                    except Exception as _ue:  # noqa: BLE001
                        # 探测失败保守起见仍按"用户活跃"处理（行为退化到 pre-fix）
                        logger.debug(
                            "p6_watchdog_user_activity_probe_failed sid=%s err=%s",
                            _base_sid,
                            str(_ue)[:200],
                        )
                    rows = await sdb_local.get_session_todos(_base_sid)
                    return [
                        r
                        for r in rows
                        if (r.get("status") or "").lower() not in ("completed", "cancelled")
                    ]
                except Exception as _e:  # noqa: BLE001
                    logger.warning(
                        "p5s2_watchdog_probe_lookup_failed sid=%s err=%s",
                        _base_sid,
                        str(_e)[:200],
                    )
                    return []

            _watchdog = _WatchdogLoop(
                session_activity=service_context.get("session_activity"),
                hook=_build_sup_hook(_supervisor_agent),
                scan_interval_seconds=float(_sup_cfg.get("scan_interval_seconds", 60)),
                stuck_threshold_seconds=float(_sup_cfg.get("stuck_threshold_seconds", 900)),
                dedup_seconds=float(_sup_cfg.get("dedup_seconds", 720)),
                startup_grace_seconds=float(_sup_cfg.get("startup_grace_seconds", 30)),
                idle_with_todos_threshold_seconds=float(
                    _sup_cfg.get("idle_with_todos_threshold_seconds", 60)
                ),
                incomplete_todos_probe=_watchdog_incomplete_todos_probe,
                # P5-S2 Phase 6 rule (d): proactive death-loop trigger.
                tool_signature_repeat_threshold=int(
                    _sup_cfg.get("tool_signature_repeat_threshold", 3)
                ),
            )
            _watchdog.start()
            service_context.register("watchdog", _watchdog)
            logger.info("p5_supervisor_watchdog_started")

        else:
            logger.info("p5_supervisor_disabled_via_config")
    except Exception as exc:  # noqa: BLE001
        logger.warning("p5_supervisor_watchdog_start_failed", error=str(exc))

    # P5-S2 Phase 4: AutoResumeOrchestrator — closes the
    # supervisor → main-agent loop. When the chat handler hits
    # max_iterations / circuit_open / permanent_tool_error /
    # hallucination, it forwards to ``orchestrator.handle_failure``;
    # the orchestrator asks supervisor for a hint and (if action
    # is ``nudge``) automatically spawns a fresh chat task on the
    # same sid via the per-sid re-dispatcher closure populated
    # by the chat handler itself.
    try:
        from agent.auto_resume import AutoResumeOrchestrator as _AROrch

        # Dispatcher closure: orchestrator passes (sid, msgs)
        # where ``msgs`` ends in a system msg with the supervisor
        # hint. Production trampoline:
        #   1. Extract hint text from the injected system msg.
        #   2. Push it to nudge_queue (pop_all picks it up at the
        #      top of the next chat task — uniform with P5-S1
        #      injection path).
        #   3. Call the per-sid re-dispatcher (registered by chat
        #      handler each user turn) with the synthetic trigger
        #      ``<<auto_resume>>`` so a fresh AgentLoop runs.
        async def _resume_through_harness(_sid: str, _msgs: list[dict]) -> None:
            await _enqueue_auto_resume_hint(_sid, _msgs)
            _ws_for_sid = _control_connections.get(_sid) or _control_connections.get("default")
            if _ws_for_sid is None:
                logger.warning("auto_resume_no_transport sid=%s", _sid)
                return
            try:
                _launch_product_harness_chat(_ws_for_sid, "<<auto_resume>>", _sid)
            except Exception as _ex:  # noqa: BLE001
                logger.warning("auto_resume_redispatch_failed sid=%s err=%s", _sid, _ex)

        # ws emitter — broadcast auto_resume_* events to all control conns.
        async def _auto_resume_emit(_typ: str, _payload: dict) -> None:
            if not _control_connections:
                return
            _msg = {"type": _typ, "payload": _payload}
            for _sid_key, _ws_obj in list(_control_connections.items()):
                try:
                    await _ws_obj.send_json(_msg)
                except Exception as _bex:
                    logger.debug("auto_resume_emit_failed sid=%s err=%s", _sid_key, _bex)

        # Audit writer — bridge to SessionDB.append_supervisor_hint.
        async def _auto_resume_audit(_record: dict) -> None:
            _sdb = service_context.get("session_db")
            if _sdb is None:
                return
            try:
                await _sdb.append_supervisor_hint(
                    session_id=_record.get("session_id", ""),
                    alert_id=_record.get("alert_id", ""),
                    hint_text=_record.get("hint_text", ""),
                    action=_record.get("action", "auto_resumed"),
                    severity=_record.get("severity", "yellow"),
                    diagnosis=_record.get("diagnosis", ""),
                )
            except Exception as _ex:  # noqa: BLE001
                logger.debug("auto_resume_audit_failed err=%s", _ex)

        # WI-1.5：resume 注入原 goal_text（窄版）。从 service_context
        # 取活跃 goal store（goal_mode OFF → None → getter 返 None → BC）。
        def _goal_text_getter_for_resume(_sid: str):
            _gs = service_context.get("session_goal_store")
            try:
                return _gs.get_goal_text(_sid) if _gs is not None else None
            except Exception:  # noqa: BLE001 — safe-fail, 不阻 resume
                return None

        # 监工开着就让监工模型给提示；关着用按失败原因的固定提示（不调模型）
        from agent.auto_resume import DeterministicResumePolicy as _DetPolicy

        _resume_policy = service_context.get("supervisor") or _DetPolicy()
        _orch = _AROrch(
            supervisor=_resume_policy,
            chat_dispatcher=_resume_through_harness,
            activity_store=service_context.get("session_activity"),
            max_attempts=int(_heal_cfg.get("max_auto_resume_attempts", 2)),
            enabled=bool(_heal_cfg.get("auto_resume_enabled", True)),
            ws_emitter=_auto_resume_emit,
            audit_writer=_auto_resume_audit,
            goal_text_getter=_goal_text_getter_for_resume,
        )
        service_context.register("auto_resume", _orch)
        logger.info(
            "p5s2_auto_resume_started enabled=%s max_attempts=%d policy=%s",
            _orch._enabled,
            _orch.max_attempts,
            type(_resume_policy).__name__,
        )
    except Exception as _exc:  # noqa: BLE001
        logger.warning("p5s2_auto_resume_start_failed err=%s", _exc)

    # Phase 1.1.5 — 启动落一行 model_context_resolved，让用户/日志一眼
    # 看到当前默认模型解析出的有效窗口 + 来源链。每次 chat 会话另会按
    # session 的实际 model + project_root 再 resolve（resolve() 自带日志）。
    try:
        from llm.model_info import resolve as _resolve_mi

        _startup_v2 = bool(
            ((config.raw.get("context") or {}).get("manager") or {}).get("v2_enabled", True)
        )
        if _startup_v2:
            # resolve() 自身落 model_context_resolved INFO 日志。
            _resolve_mi(config.llm.local.model, project_root=None)
        else:
            logger.info(
                "model_context_resolved model=%s window=legacy source=v1_rollback",
                config.llm.local.model,
            )
    except Exception as _mi_exc:  # noqa: BLE001
        logger.warning("model_context_startup_resolve_failed err=%s", _mi_exc)

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
        from deskpet.orchestration.wiring import activate_orchestration
        from paths import user_data_dir as _orchestration_user_data_dir

        await activate_orchestration(
            service_context,
            config_path=_CONFIG_PATH,
            user_data=_orchestration_user_data_dir(),
            broadcast_targets=lambda: list(_control_connections.values()),
            record_startup_error=startup_errors.record,
        )
        await _activate_human_memory_host_ports(startup_epoch)
        await _complete_growth_authority_cutover()
        await _initialize_companion_projection_services()
        await _activate_companion_runtime_adapter_and_open_ingress()
        _initialize_companion_action_decision_service()
    from deskpet.realtime_voice import (
        REALTIME_VOICE_ENABLED,
        RealtimeVoiceService,
        allowed_realtime_origins,
    )

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
    yield
    if host_state.realtime_voice_service is not None:
        try:
            await host_state.realtime_voice_service.close()
            logger.info("realtime_voice_stopped")
        except Exception:  # noqa: BLE001
            logger.warning("realtime_voice_shutdown_failed")
        finally:
            host_state.realtime_voice_service = None
    from deskpet.retrieval.runtime import shutdown_default_gateway

    await shutdown_default_gateway()
    _foreground_runtime = service_context.get("human_memory_foreground_runtime_execution_authority")
    if _foreground_runtime is not None:
        try:
            await _foreground_runtime.close(timeout=5.0)
            logger.info("human_memory_foreground_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "human_memory_foreground_runtime_shutdown_failed",
                error=str(exc),
            )
    _terminal_audit = service_context.get("terminal_operation_audit")
    if _terminal_audit is not None:
        try:
            await _terminal_audit.close(timeout=5.0)
        except Exception:
            logger.warning("terminal_operation_audit_shutdown_incomplete")
        finally:
            service_context.register("terminal_operation_audit", None)
    # 2026-09-10：记忆分析 lane 已移除；原先嵌在它 finally 里的两次前台调度槽位
    # 复位现在无条件执行（旧写法在没有 lane 时根本不会复位）。
    service_context.register("human_memory_foreground_scheduler_wake", None)
    service_context.register("human_memory_foreground_runtime_execution_authority", None)
    # Close SDK Runtime ingress
    if host_state.sdk_ingress is not None:
        host_state.sdk_ingress.close()
    _pending_sdk_recovery = tuple(_sdk_recovery_watch_tasks.values())
    for _task in _pending_sdk_recovery:
        _task.cancel()
    if _pending_sdk_recovery:
        await asyncio.gather(*_pending_sdk_recovery, return_exceptions=True)
    _sdk_recovery_watch_tasks.clear()
    _pending_ingress = tuple(_companion_ingress_tasks)
    for _task in _pending_ingress:
        _task.cancel()
    if _pending_ingress:
        await asyncio.gather(*_pending_ingress, return_exceptions=True)
    _companion_ingress_tasks.clear()
    _companion_runtime_service = service_context.get("companion_runtime")
    if _companion_runtime_service is not None:
        try:
            await _companion_runtime_service.close(timeout=5.0)
            _runtime_diagnostics = _companion_runtime_service.diagnostics()
            if (
                _runtime_diagnostics["scheduler_count"] != 0
                or _runtime_diagnostics["child_count"] != 0
            ):
                raise RuntimeError("companion_runtime_shutdown_incomplete")
            logger.info(
                "companion_runtime_stopped",
                scheduler_count=0,
                child_count=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_runtime_shutdown_failed", error=str(exc))
    _legacy_reflection_pending = tuple(_legacy_reflection_tasks)
    for _task in _legacy_reflection_pending:
        _task.cancel()
    if _legacy_reflection_pending:
        await asyncio.gather(
            *_legacy_reflection_pending,
            return_exceptions=True,
        )
    _provider_maintenance_pending = tuple(_provider_workload_maintenance_tasks)
    for _task in _provider_maintenance_pending:
        _task.cancel()
    if _provider_maintenance_pending:
        await asyncio.gather(
            *_provider_maintenance_pending,
            return_exceptions=True,
        )
    _provider_workload_maintenance_tasks.clear()
    if host_state.sdk_desktop_bridge is not None:
        try:
            await host_state.sdk_desktop_bridge.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sdk_desktop_test_shutdown_failed", error=str(exc))
        finally:
            host_state.sdk_desktop_bridge = None
            service_context.register("sdk_runtime_ready", None)
    # Agent 编排服务（plans/2026-09-11-orchestrator-host-integration）先于 SDK stack 关闭。
    # 这只是有序关停路径；App 真实退出是 SIGKILL，编排的正确性不依赖这里。
    try:
        from deskpet.orchestration.wiring import deactivate_orchestration

        await deactivate_orchestration(service_context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestration_shutdown_failed", error=str(exc))
    # Close SDK Runtime Stack (Slice C)
    if host_state.sdk_runtime_stack is not None:
        try:
            await host_state.sdk_runtime_stack.close()
            logger.info("product_sdk_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_sdk_runtime_shutdown_failed", error=str(exc))
        finally:
            host_state.sdk_runtime_stack = None
            host_state.sdk_ingress = None
            from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

            for _sdk_run_id, _retained in tuple(_sdk_retained_presentations.items()):
                _delivery_adapters.pop(_sdk_run_id, None)
                _context = _retained[3]
                _root_run_id = str(getattr(_context, "run_id", "") or "")
                if _sdk_run_ids_by_root.get(_root_run_id) == _sdk_run_id:
                    _sdk_run_ids_by_root.pop(_root_run_id, None)
            _sdk_retained_presentations.clear()
            _sdk_unavailable_tool_authority_runs.clear()
            service_context.register("sdk_runtime_ready", None)
    # SessionDB 是纯 Host 会话账本；在每个 Runtime 借用方停止后关闭一次，
    # 带硬超时，避免 shutdown 卡在存储故障上。
    _owned_session_db = service_context.get("session_db")
    if _owned_session_db is not None:
        try:
            await asyncio.wait_for(
                _owned_session_db.close(timeout_seconds=5.0),
                timeout=6.0,
            )
            logger.info("product_memory_owner_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_memory_owner_shutdown_failed", error=str(exc))
        finally:
            service_context.register("session_db", None)
    _capability_platform = service_context.get("capability_platform")
    _capability_center = service_context.get("capability_center")
    if _capability_center is not None:
        try:
            await _capability_center.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_center_shutdown_failed", error=str(exc))
        finally:
            service_context.register("capability_center", None)
    if _capability_platform is not None:
        try:
            cleanup_reports = await _capability_platform.shutdown()
            logger.info(
                "capability_platform_stopped",
                cleanup_reports=len(cleanup_reports),
                remaining_pids=sum(len(report.remaining_pids) for report in cleanup_reports),
                released_private_memory_bytes=sum(
                    report.released_private_memory_bytes for report in cleanup_reports
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_platform_shutdown_failed", error=str(exc))
        finally:
            service_context.register("capability_platform", None)
    _workflow_service = service_context.get("workflow_service")
    if _workflow_service is not None:
        try:
            await asyncio.wait_for(_workflow_service.close(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow_owner_shutdown_failed", error=str(exc))
    # P5-S1: stop the watchdog cleanly so its task doesn't dangle past
    # shutdown and produce "Task was destroyed but it is pending!" noise.
    _wd = service_context.get("watchdog")
    if _wd is not None:
        try:
            await _wd.stop()
        except Exception as exc:
            logger.warning("p5_supervisor_watchdog_stop_failed", error=str(exc))
    # P4-S15: stop in reverse-dependency order — MCP servers first (so they
    # don't keep firing tool_invoke writes), then VectorWorker (drain
    # outstanding embeds), then SkillLoader's watchdog thread.
    _mcp = service_context.get("mcp_manager")
    if _mcp is not None:
        try:
            await _mcp.stop()
        except Exception as exc:
            logger.warning("p4_mcp_manager_stop_failed", error=str(exc))
    _vw = service_context.get("vector_worker")
    if _vw is not None:
        try:
            await _vw.stop()
        except Exception as exc:
            logger.warning("p4_vector_worker_stop_failed", error=str(exc))
    _iw = service_context.get("image_worker")
    if _iw is not None:
        try:
            await _iw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_stop_failed", error=str(exc))
    for _skill_surface in (
        service_context.get("managed_skill_discovery_projection"),
        service_context.get("skill_loader"),
    ):
        if _skill_surface is not None:
            try:
                await _skill_surface.stop()
            except Exception as exc:
                logger.warning("p4_skill_surface_stop_failed", error=str(exc))

    # Stage 2 / WI-S2.4 (D12 v2)：等所有 episodic fact 抽取 task 完成。
    # 防 "Task was destroyed but it is pending" warning + 数据丢失。
    pending = list(_episodic_background_tasks)
    if pending:
        logger.info(
            "episodic_background_tasks_draining count=%d",
            len(pending),
        )
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "episodic_background_tasks_drain_failed: %s",
                exc,
            )

    logger.info("shutting down")


app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)

# CORS: Tauri WebView2 runs on tauri://localhost (or https://tauri.localhost).
# fetch() to http://127.0.0.1:8100 is cross-origin and blocked without this.
# WebSocket connections are NOT subject to CORS, only HTTP (POST /config/cloud).
from fastapi.middleware.cors import CORSMiddleware

# v2 fix: regex 模式支持任意 vite dev port (worktree-aware 隔离场景).
# 默认 5173；v2 worktree 用 5473；其他 worktree 用 5273/5373/5573 等.
# allow_origin_regex 覆盖所有 localhost / 127.0.0.1 + 任意端口 + Tauri.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(tauri://localhost|https://tauri\.localhost|http://(localhost|127\.0\.0\.1):\d+)$",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Shared-Secret"],
)

# isort: split
from host.connections import (
    _chat_peer_groups,
    _control_connections,
)

# isort: split


# isort: split
from host.control_messages import (
    _cancel_harness_inspector_tasks,
    _handle_control_ws_message,
)

# isort: split


# isort: split
from host.chat_delivery import (
    _broadcast_control,
    _broadcast_default_chat_peers,
    _initial_chat_peer_group,
    _register_chat_peer,
    _remap_chat_peer_group,
    _resolve_chat_source_session,
    _resolve_chat_task_scope,
    _send_new_session_origin_user_echo,
)

# isort: split


# isort: split
from host.ppt_outline import (
    _configure_ppt_production,
    _expire_ppt_outline_dangling_for_startup,
)

# isort: split


# ─── WI-1B-4 摘要质量回路 — 纯检测/构造 helper（main.py 闭包 + 单测共用）──────
import re as _re_sql_module

_SUMMARY_CONFUSED_RE = _re_sql_module.compile(r"刚才|之前说的|你忘了|我们在弄|上一个")
_TASK_STATE_SNAPSHOT_MARKER = "[任务态快照"


def _sql_user_is_confused(text: str) -> bool:
    """用户消息是否用"困惑措辞"问起被压掉的上下文（词法匹配）。"""
    return bool(_SUMMARY_CONFUSED_RE.search(text or ""))


def _sql_latest_task_snapshot(entries: list[dict]) -> str | None:
    """从 L1 list_entries 结果里取最近一条 pre-flush 任务态快照（无则 None）。

    任务态快照由 agent_loop wi4b_preflush 在压缩触发时写入 MEMORY.md，
    形如 ``[任务态快照/task-state] 目标: ...; 最近请求: ...``。它的存在
    即"本 session 发生过压缩"的标志（不依赖 ctx_observability flag）。
    """
    snaps = [
        str(e.get("text", ""))
        for e in (entries or [])
        if _TASK_STATE_SNAPSHOT_MARKER in str(e.get("text", ""))
    ]
    return snaps[-1] if snaps else None


def _sql_build_reinject_msg(snapshot: str) -> dict:
    """据任务态快照构造一条回灌 system 消息。"""
    return {
        "role": "system",
        "content": (
            "[任务态回灌/summary-quality-loop] 用户提起了之前的上下文，但相关"
            "历史已被压缩。以下是压缩前保存的任务态快照，请据此续接，不要重新"
            "自我介绍或反问用户想做什么：\n" + snapshot
        ),
        "_is_summary_reinject": True,
    }


# isort: split

# isort: split


# ppt_pro 渲染专用线程池：真机 E2E 发现，若用默认共享 executor(None)，render 任务会
# 排在 BGE-M3 embedder / vector-worker / summarizer 等大量 run_in_executor 工作后面、
# 长时间不执行(表现为 deck 永不落盘的"假 hang")。给 render 独立池根治排队饿死。


# isort: split
from host.provider_chain import (
    _resolve_code_recovery_provider,
    _validate_code_recovery_capability_subset,
)

# isort: split


# SDK Runtime globals (production ingress)
# F06：单例 retry-once provider reconciliation；SDK 端口与前台运行时共用同一份
# ``exhausted_runs``（二次未知的 run 由前台 cancel 收尾）。
# F06：SDK 栈构建时绑定的 Host RuntimeReconciliationPort（coordinator.reconcile_incomplete）。


# isort: split

# isort: split


# Shared final Tool-effect admission gate.  The SDK effect executor consults
# it before every physical dispatch; the foreground runtime registers exact
# lease identities into it.  Created lazily because the SDK stack starts
# before the human-memory foreground ports.


# S5b Task 1: process-local memo of whole-Run fault codes.  The authorities
# that raise a Run fault (TaskExecutionEnvelope authority, run Tool exposure)
# record the stable code; the SDK terminal observer copies it into the
# ``run_terminal`` evidence ``public_payload.error_code`` on durable FAILED.


# HM-TO-A6 incident A: process-local memo of the ContextRouteState each provider
# turn was prepared under.  ``ProductRunContextAuthority`` writes it, the
# capability-discovery bridge reads it so tool_search / tool_describe /
# tool_activate stop offering PROJECT_EFFECT Tools the Run's route can never
# execute.  Advisory only; every authority check downstream is unchanged.


# S5b Task 2: single owner of Harness evidence reservations / same-transaction
# objective events over the human-memory state.db.  Shared by the effect
# executor (tool_invocation + host.file/host.test), the provider coordinator
# (provider_invocation) and the v45 route ledger (snapshot / route facts); the
# terminal observer drains it through the SDK runtime stack's fact reader.


# S5b Task 3: the `task_scope_update` handler shared by the Tool registration and
# the terminal closure fallback (process-wide, like the binding resolver above).


# isort: split
from host.sdk_registry import (
    _sdk_recovery_watch_tasks,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
    _sdk_unavailable_tool_authority_runs,
)

# isort: split


# isort: split
from host.sdk_execution import (
    _assert_sdk_provider_mutation_allowed,
    _initialize_sdk_desktop_test_bridge,
    _sdk_desktop_test_enabled,
    _trigger_harness_recovery_after_identity_bind,
)

# isort: split


# isort: split

# isort: split


# 2026-09-10：S5b Task 4 的 Host↔Memory 异步面（HostEvidenceAuthority /
# HostMemoryAnalysisExecutor / MemoryIngestionOutboxWorker / MemoryAnalysisLane）
# 整条随认知记忆 SDK 移除，不再有可组装的槽位。


# isort: split
from host.sdk_activation import (
    _activate_companion_runtime_adapter_and_open_ingress,
    _activate_product_sdk_runtime,
)

# isort: split


# isort: split
from host.harness_runs import (
    _authoritative_sdk_run_projection,
    _cancel_product_harness_run,
    _signal_product_harness_decision,
    _try_resolve_execution_workflow_decision,
)

# isort: split

# if _sdk_ingress is None or not _sdk_ingress.accepting or not run_id:
#     return False
# from deskpet.execution.contracts import (
#     AttachmentPolicy,
#     RunRef,
#     TERMINAL_RUN_STATUSES,
# )
#
# host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
#     session_id=session_id,
#     workspace=None,
# )
# actor = host.actor(root_run_id=run_id)
# parent_ref = RunRef(run_id, session_id)
# kernel = _sdk_ingress.kernel
# links = await kernel._uow.list_child_links(parent_ref, actor)
# for link in links:
#     if link.attachment_policy is AttachmentPolicy.DETACHED:
#         continue
#     child = await kernel._query(
#         RunRef(link.child_run_id, session_id),
#         actor,
#     )
#     if child.status not in TERMINAL_RUN_STATUSES:
#         return True
# return False


# isort: split
from host.harness_chat import (
    _enqueue_auto_resume_hint,
    _launch_product_harness_chat,
    _resolve_selected_project_directory,
)

# isort: split


# isort: split
from host.history_events import (
    _attach_companion_history_events,
    _attach_workflow_history_events,
    _bind_companion_inbox_route,
    _ensure_companion_inbox_route,
    _settle_companion_bind_projection,
)

# isort: split


# isort: split
from host.context_usage import (
    _handle_context_breakdown_request,
    _read_context_usage_authority,
    _send_context_usage_authority,
)

# isort: split


# isort: split

# isort: split


# isort: split
from host.routes_voice import (
    _voice_transports,
    voice_router,
)

# isort: split


# isort: split
from host.auth import (
    _validate_secret,
)

# isort: split


# isort: split
from host.routes_cloud_config import (
    _seed_registry_from_runtime_overrides,
    cloud_config_router,
)

# isort: split


# isort: split
from host.routes_commands import (
    _current_slash_skill_catalog,
    commands_router,
)

# isort: split


# isort: split
from host.routes_health import (
    health_router,
)

# isort: split


app.include_router(cloud_config_router)
app.include_router(commands_router)
app.include_router(health_router)


# --- S14 memory management dispatch -----------------------------------------
# Handled on the control WS (same auth gate as chat/interrupt) so we don't
# expose a second unauthenticated HTTP surface. The four verbs are the minimum
# needed for the "delete-my-data" affordance V5 §6 requires.


async def _handle_memory_message(
    ws: WebSocket, session_id: str, msg_type: str, payload: dict
) -> None:
    # SessionDB is the current production MemoryStore implementation.  The
    # legacy ``memory_store`` service key disappeared during the P4 migration,
    # but this user-facing IPC handler still looked up only that retired key,
    # leaving MemoryPanel stuck in its loading state forever.  Prefer the
    # compatibility key when present and otherwise use the authoritative DB.
    store = service_context.get("memory_store") or service_context.get("session_db")
    if store is None:
        await ws.send_json(
            {
                "type": "error",
                "payload": {"message": "memory store not registered"},
            }
        )
        return

    try:
        if msg_type == "memory_list":
            # Scope defaults to the current session; ``scope: "all"`` returns
            # every session's turns (export-style). The UI asks per-session.
            scope = payload.get("scope") or "session"
            target_session = None if scope == "all" else payload.get("session_id", session_id)
            limit = payload.get("limit")
            turns = await store.list_turns(target_session, limit)
            await ws.send_json(
                {
                    "type": "memory_list_response",
                    "payload": {
                        "scope": scope,
                        "session_id": target_session,
                        "turns": [
                            {
                                "id": t.id,
                                "session_id": t.session_id,
                                "role": t.role,
                                "content": t.content,
                                "created_at": t.created_at,
                            }
                            for t in turns
                        ],
                    },
                }
            )

        elif msg_type == "memory_delete":
            turn_id = payload.get("id")
            if not isinstance(turn_id, int):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "memory_delete requires integer id"},
                    }
                )
                return
            deleted = await store.delete_turn(turn_id)
            await ws.send_json(
                {
                    "type": "memory_delete_ack",
                    "payload": {"id": turn_id, "deleted": deleted},
                }
            )

        elif msg_type == "memory_clear":
            scope = payload.get("scope") or "session"
            if scope == "all":
                removed = await store.clear_all()
                await ws.send_json(
                    {
                        "type": "memory_clear_ack",
                        "payload": {"scope": "all", "removed": removed},
                    }
                )
            else:
                target_session = payload.get("session_id", session_id)
                await store.clear(target_session)
                await ws.send_json(
                    {
                        "type": "memory_clear_ack",
                        "payload": {"scope": "session", "session_id": target_session},
                    }
                )

        elif msg_type == "memory_export":
            # Dump everything — user asked for their data, they get all of it.
            turns = await store.list_turns(None, None)
            sessions = await store.list_sessions()
            await ws.send_json(
                {
                    "type": "memory_export_response",
                    "payload": {
                        "exported_at": __import__("time").time(),
                        "sessions": [
                            {
                                "session_id": s.session_id,
                                "turn_count": s.turn_count,
                                "last_message_at": s.last_message_at,
                            }
                            for s in sessions
                        ],
                        "turns": [
                            {
                                "id": t.id,
                                "session_id": t.session_id,
                                "role": t.role,
                                "content": t.content,
                                "created_at": t.created_at,
                            }
                            for t in turns
                        ],
                    },
                }
            )
    except AttributeError as exc:
        # Inner store without list_turns/delete_turn/list_sessions/clear_all —
        # surface a clean error instead of a 500 on the wire.
        logger.warning("memory_admin_unsupported", error=str(exc), type=msg_type)
        await ws.send_json(
            {
                "type": "error",
                "payload": {"message": f"{msg_type} not supported by active memory store"},
            }
        )


def _skill_install_error_payload(exc: Exception) -> dict[str, Any]:
    code = str(getattr(exc, "code", "") or "skill_install_failed")
    if isinstance(exc, ValueError) and str(exc).startswith("skill_install_"):
        code = str(exc)
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
        },
        # Compatibility text for older clients. It mirrors the structured
        # message and carries no authority or install state.
        "error_code": code,
        "message": str(exc),
    }


async def _settings_skill_install_adapter(ws: WebSocket):
    from deskpet.capabilities.skill_install_ui import (
        ProjectSkillInstallUIAdapter,
        ProjectSkillInstallUIError,
        TrustedGlobalInstallContext,
    )

    service = service_context.get("project_skill_install_service")
    if service is None:
        raise ProjectSkillInstallUIError(
            "skill_install_service_unavailable",
            "The managed global Skill installer is unavailable.",
            retryable=True,
        )
    authorizer_factory = service_context.get("project_skill_install_settings_authorizer")
    bind_request = getattr(authorizer_factory, "bind_control_request", None)
    if not callable(bind_request):
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot establish trusted Host window authority in this build.",
            retryable=True,
        )
    # The authorizer owns request/window authentication. Query parameters and
    # UI payload fields are deliberately not supplied as identity inputs.
    bound_authorizer = await bind_request(ws)
    global_owner_key = str(getattr(service, "global_owner_key", "") or "").strip()
    if not global_owner_key:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve the trusted global Skill owner.",
            retryable=True,
        )
    principal_id = str(getattr(bound_authorizer, "principal_id", "") or "").strip()
    if not principal_id:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve an authenticated Host principal.",
            retryable=True,
        )
    # This is deliberately a separate Host adapter. The legacy PermissionGate
    # returns only an in-memory decision and cannot mint the durable receipt
    # required by GlobalSkillInstallService.confirm/cancel.
    return (
        ProjectSkillInstallUIAdapter(service=service, authorizer=bound_authorizer),
        TrustedGlobalInstallContext(
            principal_id=principal_id,
            global_owner_key=global_owner_key,
        ),
        principal_id,
    )


@app.websocket("/ws/control")
async def control_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    session_id = ws.query_params.get("session_id", "default")
    requested_scope = ws.query_params.get("requested_scope", "")
    _cc_sdb = service_context.get("session_db")
    if _cc_sdb is not None:
        try:
            from deskpet.memory.primary_authority import (
                assert_not_primary_authority,
            )

            await assert_not_primary_authority(_cc_sdb._db_path, session_id)
        except Exception as _cc_exc:  # noqa: BLE001
            _cc_code = getattr(_cc_exc, "code", None)
            if _cc_code:
                await ws.send_json(
                    {
                        "type": "session_authority_error",
                        "payload": {"ok": False, "code": _cc_code},
                    }
                )
                await ws.close(code=4003, reason=_cc_code)
                return
            raise
    # The main-window identity bridge must coexist with the message-panel
    # chat socket.  Its requested scope is not mutation authority (the first
    # Rust-signed command still proves the real label/scope), but it is safe to
    # keep this transport in a unique auxiliary slot because it never owns
    # chat delivery.  The message-panel remains the canonical session socket.
    is_companion_aux = requested_scope == "identity_bind"
    if not is_companion_aux:
        _register_chat_peer(session_id)
    connection_registry_key = (
        f"companion-aux:{session_id}:{uuid.uuid4()}" if is_companion_aux else session_id
    )
    # P4-S20: gracefully kick the previous holder of this session_id
    # (e.g. an old E2E client) so its disconnect callback doesn't
    # later pop OUR entry. Don't kill ourselves if we're the holder.
    if not is_companion_aux:
        _prev_ws = _control_connections.get(session_id)
        if _prev_ws is not None and _prev_ws is not ws:
            try:
                await _prev_ws.close(code=4002, reason="session replaced")
            except Exception:
                pass
    _control_connections[connection_registry_key] = ws
    _chat_peer_groups[connection_registry_key] = _initial_chat_peer_group(session_id)
    logger.info("control channel connected", session_id=session_id)
    # P3-S2: first frame after handshake reports startup-error state so the
    # UI can render "CUDA 缺失" / "模型缺失" banners without polling /health.
    try:
        await ws.send_json(
            {
                "type": "startup_status",
                "degraded": startup_errors.is_degraded(),
                "errors": startup_errors.snapshot(),
            }
        )
    except Exception as _e:
        logger.warning("startup_status_send_failed", error=str(_e))
    if _sdk_desktop_test_enabled():
        await ws.send_json(
            {
                "type": "companion_identity_status",
                "payload": {
                    "ready": True,
                    "status": "ready",
                    "profile_id": "sdk-desktop-test",
                    "profile_generation": 1,
                    "binding_epoch": "1",
                    "session_id": "default",
                },
            }
        )
    from deskpet.memory.control_binding import HumanMemoryControlBinding

    human_memory_control_binding = HumanMemoryControlBinding()
    companion_challenge = None
    if host_state.companion_control_ingress is not None:
        try:
            companion_challenge = host_state.companion_control_ingress.open_challenge(
                requested_window_label=ws.query_params.get("requested_window_label", ""),
                requested_scope=ws.query_params.get("requested_scope", ""),
            )
            await ws.send_json(
                {
                    "type": "companion_control_challenge",
                    "payload": companion_challenge.public_payload(),
                }
            )
            if requested_scope == "companion_action":
                try:
                    frozen_identity = host_state.companion_identity_gate.freeze()
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": True,
                                "status": "ready",
                                "profile_id": frozen_identity.owner.profile_id,
                                "profile_generation": (frozen_identity.owner.profile_generation),
                                "binding_epoch": str(frozen_identity.binding_epoch),
                            },
                        }
                    )
                except Exception:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": False,
                                "status": "unready",
                            },
                        }
                    )
        except Exception as _e:  # noqa: BLE001
            logger.warning("companion_control_challenge_failed", error=type(_e).__name__)
    try:
        while True:
            wire_text = await ws.receive_text()
            try:
                raw = json.loads(wire_text)
            except (json.JSONDecodeError, UnicodeError):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "invalid control JSON"},
                    }
                )
                continue
            if not isinstance(raw, dict):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "control frame must be an object"},
                    }
                )
                continue
            msg_type = raw.get("type", "")

            if host_state.companion_control_ingress is not None:
                from deskpet.companion.control_ingress import (
                    CompanionControlIngressError,
                    is_privileged_control_kind,
                )

            if host_state.companion_control_ingress is not None and is_privileged_control_kind(
                msg_type
            ):
                try:
                    strict_raw = host_state.companion_control_ingress.parse_privileged_frame(
                        wire_text
                    )
                    if strict_raw is None:
                        raise CompanionControlIngressError("privileged_control_parse_failed")
                    if companion_challenge is None:
                        raise CompanionControlIngressError("companion_control_challenge_missing")
                    response = await host_state.companion_control_ingress.execute(
                        strict_raw,
                        challenge=companion_challenge,
                    )
                    if msg_type == "companion_profile_bind":
                        human_memory_control_binding.bound(
                            host_state.companion_control_ingress, companion_challenge
                        )
                    elif msg_type == "companion_profile_unbind":
                        human_memory_control_binding.clear()
                    if msg_type in {
                        "companion_growth_evaluation_decision",
                        "companion_growth_activation_decision",
                        "companion_growth_action_decision",
                        "companion_rollback",
                        "companion_forget",
                    }:
                        await _dispatch_companion_action_command(msg_type, strict_raw)
                    if msg_type == "companion_profile_bind":
                        # 事件 AK：这一段必须有界。就绪帧在其后无条件下发，
                        # 失败只降级成稳定码，不再让 UI 无限期停在
                        # 「等待主对话就绪」。
                        try:
                            _bound_identity = host_state.companion_identity_gate.freeze()
                        except Exception as _freeze_exc:  # noqa: BLE001
                            logger.warning(
                                "companion_projection_bind_drain_failed",
                                error=type(_freeze_exc).__name__,
                                code=str(
                                    getattr(_freeze_exc, "code", None) or type(_freeze_exc).__name__
                                ),
                            )
                        else:
                            (
                                _inbox_session_id,
                                _projection_code,
                            ) = await _settle_companion_bind_projection(
                                _bound_identity,
                                ensure_route=_ensure_companion_inbox_route,
                                notification_service=(host_state.companion_notification_service),
                            )
                            _bind_payload = dict(response.get("payload", {}))
                            if _inbox_session_id is not None:
                                _bind_payload["session_id"] = _inbox_session_id
                            if _projection_code is not None:
                                # 稳定码随就绪帧一起到前端，UI 可读地说明
                                # 「历史补读未完成」，而不是静默。
                                _bind_payload["projection_degraded_code"] = _projection_code
                            response = {**response, "payload": _bind_payload}
                        # Harness startup can discover recoverable Runs before
                        # the main window finishes its signed identity bind.
                        # That attempt correctly fails closed; wake the
                        # event-driven reconciler now that the same process
                        # identity gate is ready so waiting work cannot remain
                        # stranded until an unrelated durable event arrives.
                        if _trigger_harness_recovery_after_identity_bind():
                            # 2026-08-05 fork 前既有 bug 修复：此处原引用局部变量
                            # `payload`，但它在本 try 块更下方才赋值——recovery
                            # 触发时即 UnboundLocalError，bind 被拒且前端无限
                            # 重试（真机测试实锤）。改读 response 的 payload。
                            _resp_payload = (
                                response.get("payload", {}) if isinstance(response, Mapping) else {}
                            )
                            logger.info(
                                "harness_recovery_triggered_identity_ready",
                                profile_id=_resp_payload.get("profile_id"),
                                profile_generation=_resp_payload.get("profile_generation"),
                            )
                    elif msg_type == "companion_profile_unbind":
                        if host_state.companion_notification_service is not None:
                            host_state.companion_notification_service.close_history()
                    await ws.send_json(response)
                    payload = response.get("payload", {})
                    companion_challenge = companion_challenge.advance(
                        request_seq=int(
                            payload.get("next_request_seq") or payload["nextRequestSeq"]
                        ),
                        binding_epoch=int(payload.get("binding_epoch") or payload["bindingEpoch"]),
                    )
                    if msg_type in {
                        "companion_profile_bind",
                        "companion_profile_unbind",
                    }:
                        identity_status = {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": (msg_type == "companion_profile_bind"),
                                "status": (
                                    "ready" if msg_type == "companion_profile_bind" else "unready"
                                ),
                                "binding_epoch": str(companion_challenge.binding_epoch),
                                **(
                                    {
                                        "profile_id": payload.get("profile_id"),
                                        "profile_generation": payload.get("profile_generation"),
                                        "session_id": payload.get("session_id"),
                                    }
                                    if msg_type == "companion_profile_bind"
                                    else {}
                                ),
                            },
                        }
                        for status_ws in list(_control_connections.values()):
                            try:
                                await status_ws.send_json(identity_status)
                            except Exception:
                                pass
                except Exception as _e:  # noqa: BLE001
                    code = getattr(_e, "code", str(_e))
                    rechallenge = bool(getattr(_e, "rechallenge", True))
                    logger.warning(
                        "companion_control_rejected",
                        command_kind=msg_type,
                        code=code,
                        rechallenge=rechallenge,
                        connection_id=(
                            companion_challenge.connection_id
                            if companion_challenge is not None
                            else None
                        ),
                    )
                    await ws.send_json(
                        {
                            "type": (
                                "companion_control_rechallenge"
                                if rechallenge
                                else "companion_control_error"
                            ),
                            "payload": {
                                "code": code,
                                "retryable": True,
                            },
                        }
                    )
                    # A rejected mutation never mutates the old active lease.
                    # Rechallenge via a fresh bounded challenged row so stale
                    # seq/epoch clients can recover without backend restart.
                    if rechallenge:
                        try:
                            companion_challenge = (
                                host_state.companion_control_ingress.open_challenge(
                                    requested_window_label=ws.query_params.get(
                                        "requested_window_label", ""
                                    ),
                                    requested_scope=ws.query_params.get("requested_scope", ""),
                                )
                            )
                            await ws.send_json(
                                {
                                    "type": "companion_control_challenge",
                                    "payload": (companion_challenge.public_payload()),
                                }
                            )
                        except Exception as challenge_error:  # noqa: BLE001
                            logger.warning(
                                "companion_rechallenge_failed",
                                error=type(challenge_error).__name__,
                            )
                continue

            if msg_type == "companion_detail_get":
                _detail_request_id = str(raw.get("request_id") or "")
                try:
                    from deskpet.companion.detail_query import (
                        CompanionDetailQueryError,
                    )

                    if (
                        host_state.companion_detail_query is None
                        or companion_challenge is None
                        or requested_scope != "companion_action"
                    ):
                        raise CompanionDetailQueryError("unavailable")
                    _detail_identity = host_state.companion_identity_gate.freeze()
                    if int(companion_challenge.binding_epoch) != int(
                        _detail_identity.binding_epoch
                    ):
                        raise CompanionDetailQueryError("cursor_invalid")
                    _detail_payload = raw.get("payload")
                    if not isinstance(_detail_payload, dict):
                        raise CompanionDetailQueryError("unavailable")
                    _detail_response = await host_state.companion_detail_query.query(
                        frozen_identity=_detail_identity,
                        control_epoch=int(companion_challenge.binding_epoch),
                        request=_detail_payload,
                    )
                    await ws.send_json(
                        {
                            "type": "companion_detail_response",
                            "request_id": _detail_request_id,
                            "payload": _detail_response,
                        }
                    )
                except Exception as _detail_exc:  # noqa: BLE001
                    _detail_code = getattr(_detail_exc, "code", "unavailable")
                    if _detail_code not in {
                        "unavailable",
                        "detail_changed",
                        "cursor_invalid",
                    }:
                        _detail_code = "unavailable"
                    await ws.send_json(
                        {
                            "type": "companion_detail_error",
                            "request_id": _detail_request_id,
                            "payload": {
                                "code": _detail_code,
                                **(
                                    {"message": str(_detail_exc)}
                                    if str(_detail_exc) != _detail_code
                                    else {}
                                ),
                            },
                        }
                    )
                continue

            if await _handle_control_ws_message(raw, session_id=session_id, ws=ws):
                continue

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "memory_summarize_now":
                # P4-S20-D: 手动触发 — 用户可在 SettingsPanel / MemoryPanel
                # 点 "立即总结老对话" 按钮。参数支持 age_days / min_messages
                # / max_per_run override。
                payload = raw.get("payload", {}) or {}
                if _summarizer_state_db_path is None or host_state.local_llm is None:
                    await ws.send_json(
                        {
                            "type": "memory_summarize_response",
                            "payload": {
                                "ok": False,
                                "error": "summarizer not initialized (state_db or local_llm missing)",
                            },
                        }
                    )
                    continue
                # 旧记忆 summarizer 已移除；state_db 恒为 None，上面的 guard 已 continue。

            elif msg_type == "memory_thumbs_up":
                # 记忆系统升级 WI-M1.1：评估反馈回路。前端历史/消息面板
                # 点 👍/👎 → 这里把一行落进 memory_user_feedback，供 eval
                # CLI 离线分析「召回质量差的 query」。
                # payload: { msg_id:int, query:str, helpful:bool }
                # flag feedback_loop 关 → 不落库、不建 v2 表（Strangler-Fig：
                # MR-0 要求 flag 全关时 state.db 无任何 v2 表）。
                payload = raw.get("payload", {}) or {}
                if not config.memory.v2.feedback_loop:
                    await ws.send_json(
                        {
                            "type": "memory_thumbs_up_response",
                            "payload": {"ok": False, "reason": "feedback_loop_disabled"},
                        }
                    )
                    continue
                if _summarizer_state_db_path is None:
                    await ws.send_json(
                        {
                            "type": "memory_thumbs_up_response",
                            "payload": {"ok": False, "reason": "state_db_unavailable"},
                        }
                    )
                    continue
                # 旧记忆 feedback store 已移除；state_db 恒为 None，上面的 guard 已 continue。

            elif msg_type == "memory_archive_list":
                # P4-S20-D: 列出 archive 内容供用户查看 / 决定是否恢复或彻底删
                # 参数：limit (default 100), session_id (optional filter)
                payload = raw.get("payload", {}) or {}
                if _summarizer_state_db_path is None:
                    await ws.send_json(
                        {
                            "type": "memory_archive_list_response",
                            "payload": {"ok": False, "error": "state_db not initialized"},
                        }
                    )
                    continue
                _limit = int(payload.get("limit", 100))
                _filter_sid = payload.get("session_id")
                try:
                    import aiosqlite

                    rows = []
                    async with aiosqlite.connect(_summarizer_state_db_path) as _db:
                        if _filter_sid:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive WHERE session_id = ? "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_filter_sid, _limit),
                            )
                        else:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_limit,),
                            )
                        async for r in cur:
                            rows.append(
                                {
                                    "id": r[0],
                                    "session_id": r[1],
                                    "role": r[2],
                                    "content": r[3],
                                    "created_at": r[4],
                                    "archived_at": r[5],
                                    "archived_into_id": r[6],
                                }
                            )
                        await cur.close()
                    await ws.send_json(
                        {
                            "type": "memory_archive_list_response",
                            "payload": {"ok": True, "rows": rows, "count": len(rows)},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "memory_archive_list_response",
                            "payload": {"ok": False, "error": str(exc)},
                        }
                    )

            elif msg_type in {"session_run_status_query", "session_health_check"}:
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                root_run_id = str(payload.get("root_run_id") or payload.get("run_id") or "").strip()
                projection = _authoritative_sdk_run_projection(
                    root_run_id, str(payload.get("sdk_run_id") or "")
                )
                await ws.send_json(
                    {
                        "type": "session_run_status_ack",
                        "payload": {
                            "query_id": str(payload.get("query_id") or ""),
                            "session_id": target_sid,
                            "root_run_id": root_run_id,
                            **projection,
                        },
                    }
                )

            elif msg_type == "permission_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                decision_id = payload.get("decision_id") or payload.get("request_id")
                try:
                    receipt = await _signal_product_harness_decision(
                        target_sid,
                        payload,
                        {"decision": str(payload.get("decision") or "deny")},
                        authorization=True,
                        projection_ws=ws,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "permission_response_rejected",
                        decision_id=decision_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    await ws.send_json(
                        {
                            "type": "permission_response_applied",
                            "payload": {
                                "ok": False,
                                "request_id": payload.get("request_id"),
                                "decision_id": decision_id,
                                "error": {
                                    "code": getattr(exc, "code", type(exc).__name__),
                                    "message": str(exc),
                                },
                            },
                        }
                    )
                    continue
                logger.info(
                    "permission_response_applied",
                    decision_id=decision_id,
                    duplicate=receipt.duplicate,
                )
                await ws.send_json(
                    {
                        "type": "permission_response_applied",
                        "payload": {
                            "ok": True,
                            "request_id": payload.get("request_id"),
                            "decision_id": decision_id,
                            "duplicate": receipt.duplicate,
                        },
                    }
                )

            elif msg_type == "protected_path_decision":
                # 2026-09-26：用户在会话卡片上对受保护核心文件的决定。
                # 运行从未等待这张卡片；这里只记授权，模型重试时放行。
                from deskpet.permissions import protected_paths as _protected_paths

                payload = raw.get("payload", {}) or {}
                decision = str(payload.get("decision") or "")
                if decision not in {"allow_once", "allow_session", "deny"}:
                    decision = "deny"
                applied = _protected_paths.decide(str(payload.get("request_id") or ""), decision)
                await ws.send_json(
                    {
                        "type": "protected_path_decision_applied",
                        "payload": {
                            "request_id": payload.get("request_id"),
                            "decision": decision,
                            "found": applied is not None,
                        },
                    }
                )

            elif msg_type == "external_wait_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                receipt = await _signal_product_harness_decision(
                    target_sid,
                    payload,
                    {
                        "decision": "complete",
                        "completed": True,
                    },
                )
                logger.info(
                    "external_wait_response_applied",
                    decision_id=(payload.get("decision_id") or payload.get("request_id")),
                    wait_ref=payload.get("wait_ref"),
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "project_directory_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                try:
                    project_root = _resolve_selected_project_directory(
                        payload.get("parent_directory"),
                        payload.get("folder_name"),
                        allow_existing=(payload.get("directory_mode") == "use_existing"),
                    )
                except ValueError as exc:
                    await ws.send_json(
                        {
                            "type": "project_directory_error",
                            "payload": {
                                "decision_id": (
                                    payload.get("decision_id") or payload.get("request_id")
                                ),
                                "code": str(exc),
                                "error": (
                                    "这个位置不能安全创建项目，请重新选择空目录或修改文件夹名。"
                                ),
                            },
                        }
                    )
                    continue
                try:
                    receipt = await _signal_product_harness_decision(
                        target_sid,
                        payload,
                        {
                            "decision": "complete",
                            "completed": True,
                            "project_root": project_root,
                            "parent_directory": str(payload.get("parent_directory") or ""),
                            "folder_name": str(payload.get("folder_name") or ""),
                            "directory_mode": str(payload.get("directory_mode") or "create_new"),
                        },
                    )
                except Exception as exc:  # execution contracts are user-retryable
                    from deskpet.execution.contracts import ExecutionError

                    if not isinstance(exc, ExecutionError):
                        raise
                    logger.warning(
                        "project_directory_response_rejected",
                        session_id=target_sid,
                        decision_id=(payload.get("decision_id") or payload.get("request_id")),
                        code=exc.code,
                        error=str(exc),
                    )
                    await ws.send_json(
                        {
                            "type": "project_directory_error",
                            "payload": {
                                "decision_id": (
                                    payload.get("decision_id") or payload.get("request_id")
                                ),
                                "code": exc.code,
                                "error": (
                                    "项目位置未能应用，请重新选择；若任务状态已经变化，"
                                    "请停止当前任务后重试。"
                                ),
                            },
                        }
                    )
                    continue
                logger.info(
                    "project_directory_response_applied",
                    decision_id=(payload.get("decision_id") or payload.get("request_id")),
                    project_root=project_root,
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "clarification_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                receipt = await _signal_product_harness_decision(
                    target_sid,
                    payload,
                    {"answer": str(payload.get("answer") or "")},
                )
                logger.info(
                    "clarification_response_applied",
                    decision_id=payload.get("decision_id") or payload.get("request_id"),
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "permission_auto_mode_set":
                # P4-S21 #13 / P4-S25: the settings panel sends the target
                # mode explicitly ({enabled: bool}); the CapabilityStore CAS
                # persists it in workflow.db and bumps the generation.
                # MM-D4: never echo the request back — re-read the persisted
                # state so the panel renders the mode+generation that landed.
                payload = raw.get("payload", {}) or {}
                requested = bool(payload.get("enabled", False))
                await _set_authorization_auto_mode(requested)
                snapshot = await _authorization_policy_snapshot()
                logger.info(
                    "permission_auto_mode_set",
                    requested=requested,
                    mode=snapshot["mode"],
                    generation=snapshot["generation"],
                    provenance=snapshot["provenance"],
                )
                await ws.send_json(
                    {
                        "type": "permission_auto_mode_response",
                        "request_id": raw.get("request_id"),
                        "payload": snapshot,
                    }
                )

            elif msg_type in ("orchestration_publish_dir_get", "orchestration_publish_dir_set"):
                # NEXT-TG-1.0 §9：设置页的发布目录入口。校验后写回 config.toml，并重启编排服务
                # （连接器只在启动时按这个值装配）；空路径 = 撤销授权。
                from deskpet.orchestration import publish_settings as _publish_settings
                from deskpet.orchestration.wiring import read_section as _read_orch_section
                from paths import user_data_dir as _publish_user_data_dir

                _rid = raw.get("request_id")
                _body = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
                _reply: dict = {"request_id": _rid, "ok": True}
                try:
                    if msg_type == "orchestration_publish_dir_set":
                        await _publish_settings.set_publish_dir(
                            _body.get("path"),
                            config_path=Path(_CONFIG_PATH),
                            protected=[_publish_user_data_dir()],
                            restart=_restart_orchestration,
                        )
                    _configured = str(_read_orch_section(_CONFIG_PATH).get("publish_dir") or "")
                    _reply["data"] = _publish_settings.current(
                        service_context.get("orchestration"), _configured
                    )
                except _publish_settings.PublishDirRefused as _refused:
                    _reply.update(ok=False, error_code=_refused.code, error=str(_refused))
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("orchestration_publish_dir_failed err=%s", _exc)
                    _reply.update(
                        ok=False, error_code="internal", error="发布目录设置失败，请查看日志"
                    )
                await ws.send_json({"type": msg_type + "_response", "payload": _reply})

            elif msg_type.startswith(("mission_", "orchestration_", "taskgraph.")) or msg_type in (
                "agent_skill_request",
                "agent_skill_evaluation_mission",
                "agent_skill_evaluation_dispatch",
            ):
                # Agent 编排视图的控制通道协议（plan §3.4）：纯分发，不会把异常抛进 socket 循环
                # ``taskgraph.*`` 是任务页执行图面板的四个读接口（handlers.py 已注册），
                # 2026-09-25 之前没放行，面板只会读取超时。``agent_*`` 只放行上面明确列出的
                # 三个技能目录动词（NEXT-TG-1.0 §11：各档位已统一到一个技能目录权威）；
                # 会话销毁等其它 ``agent_*`` 仍不放行。
                from deskpet.orchestration.handlers import handle as _orchestration_handle

                # a payload that is not an object is answered with invalid_request inside
                # handle(), never raised into this shared loop (review P2-6)
                await ws.send_json(
                    await _orchestration_handle(
                        service_context.get("orchestration"),
                        msg_type,
                        raw.get("payload"),
                        request_id=raw.get("request_id"),
                    )
                )

            elif msg_type == "permission_auto_mode_get":
                await ws.send_json(
                    {
                        "type": "permission_auto_mode_response",
                        "request_id": raw.get("request_id"),
                        "payload": await _authorization_policy_snapshot(),
                    }
                )

            elif msg_type == "capability_list":
                center = service_context.get("capability_center")
                if center is None:
                    await ws.send_json(
                        {
                            "type": "capability_list_response",
                            "payload": {
                                "capabilities": [],
                                "error": "capability center is unavailable",
                            },
                        }
                    )
                    continue
                try:
                    payload = raw.get("payload", {}) or {}
                    target_session_id = str(payload.get("session_id") or "").strip()
                    capability_scope = None
                    capability_owner_key = None
                    if target_session_id:
                        project_bindings = service_context.get("project_binding_service")
                        if project_bindings is None:
                            raise RuntimeError("Project binding service is unavailable")
                        workspace = await project_bindings.resolve_session(target_session_id)
                        if workspace.kind == "project":
                            from deskpet.capabilities.contracts import (
                                CapabilityScope,
                            )

                            capability_owner_key = "sdk-runtime"
                            capability_scope = CapabilityScope.for_run(
                                f"capability-center:{target_session_id}",
                                project_id=workspace.project_id,
                                project_revision=workspace.project_revision,
                                project_identity=workspace.project_identity,
                                user_key=capability_owner_key,
                            )
                        elif workspace.kind == "missing":
                            raise RuntimeError(workspace.error_code)
                    capabilities = await center.list_capabilities(
                        capability_scope,
                        owner_key=capability_owner_key,
                    )
                    await ws.send_json(
                        {
                            "type": "capability_list_response",
                            "payload": {"capabilities": capabilities},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("capability_center_list_failed", error=str(exc))
                    await ws.send_json(
                        {
                            "type": "capability_list_response",
                            "payload": {
                                "capabilities": [],
                                "error": str(exc),
                            },
                        }
                    )

            elif msg_type == "capability_operations_list":
                center = service_context.get("capability_center")
                if center is None:
                    await ws.send_json(
                        {
                            "type": "capability_operations_response",
                            "payload": {
                                "operations": [],
                                "error": "capability center is unavailable",
                            },
                        }
                    )
                    continue
                payload = raw.get("payload", {}) or {}
                try:
                    limit = max(1, min(int(payload.get("limit", 100)), 500))
                except (TypeError, ValueError):
                    limit = 100
                try:
                    operations = await center.list_operations(limit=limit)
                    await ws.send_json(
                        {
                            "type": "capability_operations_response",
                            "payload": {"operations": operations},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("capability_center_operations_failed", error=str(exc))
                    await ws.send_json(
                        {
                            "type": "capability_operations_response",
                            "payload": {
                                "operations": [],
                                "error": str(exc),
                            },
                        }
                    )

            elif msg_type in {
                "capability_install",
                "capability_activate",
                "capability_repair",
                "capability_uninstall",
                "capability_rollback",
                "capability_operation_cancel",
                "capability_operation_retry",
            }:
                center = service_context.get("capability_center")
                payload = raw.get("payload", {}) or {}
                action = msg_type.removeprefix("capability_").removeprefix("operation_")
                if center is None:
                    await ws.send_json(
                        {
                            "type": "capability_action_response",
                            "payload": {
                                "ok": False,
                                "action": action,
                                "error_code": "capability_center_unavailable",
                                "error": "capability center is unavailable",
                            },
                        }
                    )
                    continue
                try:
                    if msg_type == "capability_install":
                        (
                            _si_adapter,
                            _si_project,
                            _si_principal,
                        ) = await _settings_skill_install_adapter(ws)
                        _si_result = await _si_adapter.stage(
                            url=str(payload.get("source_url") or payload.get("url") or ""),
                            owner=_si_project,  # the Settings adapter yields the global owner
                            principal_id=_si_principal,
                        )
                        await ws.send_json(
                            {
                                "type": "capability_action_response",
                                "payload": {
                                    "ok": True,
                                    "action": action,
                                    "status": "awaiting_confirmation",
                                    "install": _si_result,
                                },
                            }
                        )
                        continue
                    if msg_type in {
                        "capability_activate",
                        "capability_repair",
                    }:
                        from deskpet.capabilities.ui_service import (
                            CapabilityCenterError,
                        )

                        raise CapabilityCenterError(
                            "model_driven_action_required",
                            "Install, activation, and repair require the main "
                            "agent to resolve a trusted source or failure receipt.",
                        )
                    if msg_type == "capability_uninstall":
                        operation = await center.request_uninstall(
                            capability_id=str(payload.get("capability_id") or ""),
                            source_operation_id=(
                                str(payload["operation_id"])
                                if payload.get("operation_id")
                                else None
                            ),
                        )
                    elif msg_type == "capability_rollback":
                        operation = await center.request_rollback(
                            operation_id=str(payload.get("operation_id") or ""),
                            capability_id=(
                                str(payload["capability_id"])
                                if payload.get("capability_id")
                                else None
                            ),
                        )
                    elif msg_type == "capability_operation_retry":
                        operation = await center.request_retry(
                            operation_id=str(payload.get("operation_id") or "")
                        )
                    else:
                        operation = await center.request_cancel(
                            operation_id=str(payload.get("operation_id") or "")
                        )
                    await ws.send_json(
                        {
                            "type": "capability_action_response",
                            "payload": {
                                "ok": True,
                                "action": action,
                                "operation_id": operation.operation_id,
                                "status": operation.status,
                                "phase": operation.phase,
                            },
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    error_code = str(getattr(exc, "code", "capability_action_failed"))
                    logger.warning(
                        "capability_center_action_failed",
                        action=action,
                        error_code=error_code,
                        error=str(exc),
                    )
                    await ws.send_json(
                        {
                            "type": "capability_action_response",
                            "payload": {
                                "ok": False,
                                "action": action,
                                "error_code": error_code,
                                "error": str(exc),
                            },
                        }
                    )

            elif msg_type == "chat_turn_timeout_set":
                # 设置面板写「对话超时(分钟)」。持久化到 llm_runtime.json,夹 1~60 分钟。
                payload = raw.get("payload", {}) or {}
                try:
                    _mins = int(round(float(payload.get("minutes", 15))))
                except Exception:  # noqa: BLE001
                    _mins = 15
                _mins = max(1, min(60, _mins))
                _ov = _load_llm_runtime_overrides()
                _ov["chat_turn_timeout_minutes"] = _mins
                _save_llm_runtime_overrides(_ov)
                logger.info("chat_turn_timeout_set minutes=%d", _mins)
                await ws.send_json(
                    {
                        "type": "chat_turn_timeout_response",
                        "request_id": raw.get("request_id"),
                        "payload": {"minutes": _mins},
                    }
                )

            elif msg_type == "chat_turn_timeout_get":
                # 设置面板读当前值(展示)。默认 15。
                try:
                    _cur = int(
                        round(
                            float(
                                _load_llm_runtime_overrides().get("chat_turn_timeout_minutes", 15)
                            )
                        )
                    )
                except Exception:  # noqa: BLE001
                    _cur = 15
                await ws.send_json(
                    {
                        "type": "chat_turn_timeout_response",
                        "request_id": raw.get("request_id"),
                        "payload": {"minutes": max(1, min(60, _cur))},
                    }
                )

            elif msg_type == "plugin_list":
                # P4-S20 Stage D — list all discovered plugins with enabled status.
                if plugin_manager is None:
                    await ws.send_json(
                        {
                            "type": "plugin_list_response",
                            "payload": {"plugins": [], "error": "plugin manager not initialized"},
                        }
                    )
                    continue
                await ws.send_json(
                    {
                        "type": "plugin_list_response",
                        "payload": {"plugins": plugin_manager.list_plugins()},
                    }
                )

            elif msg_type == "plugin_enable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None or not plugin_manager.enable(name):
                    await ws.send_json(
                        {
                            "type": "plugin_enable_response",
                            "payload": {"ok": False, "name": name, "error": "unknown plugin"},
                        }
                    )
                    continue
                # Best-effort SkillLoader hot-reload so the plugin's skills appear.
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json(
                    {
                        "type": "plugin_enable_response",
                        "payload": {"ok": True, "name": name},
                    }
                )

            elif msg_type == "plugin_disable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None:
                    await ws.send_json(
                        {
                            "type": "plugin_disable_response",
                            "payload": {
                                "ok": False,
                                "name": name,
                                "error": "plugin manager not initialized",
                            },
                        }
                    )
                    continue
                plugin_manager.disable(name)
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json(
                    {
                        "type": "plugin_disable_response",
                        "payload": {"ok": True, "name": name},
                    }
                )

            elif msg_type == "skill_marketplace_list":
                # P4-S20 Stage C — fetch + cache the official registry.
                if skill_registry_client is None:
                    await ws.send_json(
                        {
                            "type": "skill_marketplace_list_response",
                            "payload": {"skills": [], "error": "marketplace not initialized"},
                        }
                    )
                    continue
                _data = await skill_registry_client.fetch()
                await ws.send_json(
                    {
                        "type": "skill_marketplace_list_response",
                        "payload": _data,
                    }
                )

            elif msg_type == "skill_list_installed":
                # Managed user-global catalog is the only installed-Skill
                # authority.  The legacy userdata/skills directory is not a
                # success or visibility source.
                _capability_platform = service_context.get("capability_platform")
                _global_service = service_context.get("project_skill_install_service")
                if _capability_platform is None or _global_service is None:
                    await ws.send_json(
                        {
                            "type": "skill_list_installed_response",
                            "payload": {"skills": [], "error": "global Skill catalog unavailable"},
                        }
                    )
                    continue
                _installed = []
                from deskpet.capabilities.contracts import CapabilityScope
                from deskpet.capabilities.manifest import load_and_validate_pack

                _global_scope_key = str(_global_service.global_owner_key)
                _catalog_snapshot = await _capability_platform.hub.snapshot(
                    CapabilityScope(user_key=_global_scope_key)
                )
                for _descriptor in _catalog_snapshot.descriptors:
                    # 2026-09-25 UI 全量点击：内置 Skill 以前不算"已安装"，列表为空，
                    # 与能力中心"操作"页的 17 个已装 Skill 对不上。内置的也列出，标 builtin。
                    _binding = next(
                        (
                            item
                            for item in _descriptor.visible_bindings
                            if item.active
                            and (
                                (item.scope == "user" and item.scope_key == _global_scope_key)
                                or item.scope == "builtin"
                            )
                        ),
                        None,
                    )
                    if _binding is None:
                        continue
                    _record = await _capability_platform.store.get_version(
                        _descriptor.version.capability_id,
                        _descriptor.version.version,
                        _descriptor.version.manifest_hash,
                    )
                    if _record is None:
                        continue
                    _manifest = load_and_validate_pack(_record.install_path).manifest
                    for _skill in _manifest.skills:
                        _installed.append(
                            {
                                "name": _skill.id,
                                "description": _manifest.name,
                                "version": _manifest.version,
                                "scope": "builtin" if _binding.scope == "builtin" else "global",
                                "capability_id": _descriptor.version.capability_id,
                                "catalog_generation": _catalog_snapshot.stamp.catalog_generation,
                                "allowed_tools": list(_skill.allowed_tools),
                            }
                        )
                await ws.send_json(
                    {
                        "type": "skill_list_installed_response",
                        "payload": {"skills": _installed},
                    }
                )

            elif msg_type == "skill_install_from_url":
                # Managed user-global install only. The legacy marketplace
                # installer remains a diagnostic inventory, never the runtime
                # authority for URL installs.
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_owner, _si_principal = await _settings_skill_install_adapter(
                        ws
                    )
                    _si_result = await _si_adapter.stage(
                        url=str(payload.get("url") or ""),
                        owner=_si_owner,
                        principal_id=_si_principal,
                    )
                    await ws.send_json(
                        {
                            "type": "skill_install_pending",
                            "request_id": raw.get("request_id"),
                            "payload": {"ok": True, **_si_result},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "skill_install_pending",
                            "request_id": raw.get("request_id"),
                            "payload": _skill_install_error_payload(exc),
                        }
                    )

            elif msg_type == "skill_install_confirm":
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_project, _si_principal = await _settings_skill_install_adapter(
                        ws
                    )
                    _si_decision = str(payload.get("decision") or "").strip()
                    if _si_decision not in {"approve", "deny"}:
                        raise ValueError("skill_install_decision_invalid")
                    _si_result = await _si_adapter.settle(
                        intent_id=str(payload.get("intent_id") or payload.get("staging_id") or ""),
                        digest=str(payload.get("digest") or ""),
                        decision_nonce=str(payload.get("decision_nonce") or ""),
                        decision_version=int(payload.get("decision_version") or 0),
                        decision=_si_decision,
                    )
                    await ws.send_json(
                        {
                            "type": "skill_install_confirm_response",
                            "request_id": raw.get("request_id"),
                            "payload": {"ok": True, **_si_result},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "skill_install_confirm_response",
                            "request_id": raw.get("request_id"),
                            "payload": _skill_install_error_payload(exc),
                        }
                    )

            elif msg_type == "skill_install_status":
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_project, _si_principal = await _settings_skill_install_adapter(
                        ws
                    )
                    _si_result = await _si_adapter.status(
                        intent_id=str(payload.get("intent_id") or ""),
                        project=_si_project,
                    )
                    await ws.send_json(
                        {
                            "type": "skill_install_status_response",
                            "request_id": raw.get("request_id"),
                            "payload": {"ok": True, **_si_result},
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "skill_install_status_response",
                            "request_id": raw.get("request_id"),
                            "payload": _skill_install_error_payload(exc),
                        }
                    )

            elif msg_type == "skill_uninstall":
                # 2026-09-25 UI 全量点击：以前走旧安装器（旧 userdata/skills 目录），
                # 受管安装的 Skill 提示"已卸载"却仍在列表里。卸载与安装同一权威：
                # 能力中心的受管卸载（用户全局作用域），等操作结束再回复。
                payload = raw.get("payload", {}) or {}
                name = str(payload.get("name") or "")
                capability_id = str(payload.get("capability_id") or name)
                center = service_context.get("capability_center")
                _global_service = service_context.get("project_skill_install_service")
                if center is None or _global_service is None:
                    await ws.send_json(
                        {
                            "type": "skill_uninstall_response",
                            "payload": {
                                "ok": False,
                                "name": name,
                                "error": "capability center is unavailable",
                            },
                        }
                    )
                    continue
                try:
                    from deskpet.capabilities.contracts import CapabilityScope

                    operation = await center.request_uninstall(
                        capability_id=capability_id,
                        scope=CapabilityScope(user_key=str(_global_service.global_owner_key)),
                    )
                    for _ in range(60):
                        if operation.status in {"succeeded", "failed", "cancelled", "unknown"}:
                            break
                        await asyncio.sleep(0.5)
                        operation = (
                            await center.store.get_operation(operation.operation_id) or operation
                        )
                    ok = operation.status == "succeeded"
                    await ws.send_json(
                        {
                            "type": "skill_uninstall_response",
                            "payload": {
                                "ok": ok,
                                "name": name,
                                "status": operation.status,
                                **({} if ok else {"error": f"卸载未完成（{operation.status}）"}),
                            },
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "skill_uninstall_response",
                            "payload": {
                                "ok": False,
                                "name": name,
                                "error": f"{getattr(exc, 'code', type(exc).__name__)}: {exc}",
                            },
                        }
                    )

            elif msg_type == "context_usage_request":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                try:
                    authoritative = await _read_context_usage_authority(target_sid)
                    await ws.send_json({"type": "context_usage", "payload": authoritative})
                except Exception as exc:  # noqa: BLE001
                    logger.debug("context_usage_request_failed err=%s", exc)

            elif msg_type == "context_breakdown_request":
                payload = raw.get("payload", {}) or {}
                try:
                    await _handle_context_breakdown_request(
                        ws,
                        payload,
                        owner_session_id=_resolve_chat_source_session(session_id, None),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("context_breakdown_send_failed err=%s", exc)

            elif msg_type == "human_memory_request":
                from deskpet.memory.human_memory_api import (
                    handle_human_memory_command,
                    send_human_memory_response,
                )

                try:
                    _hm_auth = human_memory_control_binding.authenticate(
                        host_state.companion_control_ingress, companion_challenge
                    )
                except Exception as exc:
                    await ws.send_json(
                        {
                            "type": "human_memory_response",
                            "request_id": str(raw.get("request_id") or ""),
                            "payload": {
                                "ok": False,
                                "error": {
                                    "code": getattr(exc, "code", "human_memory_connection_unbound")
                                },
                            },
                        }
                    )
                    continue

                with human_memory_control_binding.request_scope(
                    host_state.companion_control_ingress, companion_challenge
                ):
                    _hm_response = await handle_human_memory_command(
                        raw,
                        factory=service_context.get("human_memory_host_service_factory"),
                        auth=_hm_auth,
                        binding_append=service_context.get("human_memory_binding_append_authority"),
                        recovery=service_context.get("human_memory_recovery_lifecycle"),
                        scheduler_wake=service_context.get(
                            "human_memory_foreground_scheduler_wake"
                        ),
                    )
                    if _hm_response is not None:
                        await send_human_memory_response(
                            _hm_response,
                            factory=service_context.get("human_memory_host_service_factory"),
                            auth=_hm_auth,
                            send=ws.send_json,
                        )

            elif msg_type in {
                "project_preview_register",
                "project_register",
                "session_create",
                "project_catalog_page",
                "project_sessions_page",
                "project_inspect",
                "project_relocate",
            }:
                from deskpet.session.project_protocol import (
                    handle_project_session_command as _handle_project_session_command,
                )

                _project_response = await _handle_project_session_command(
                    raw,
                    bindings=service_context.get("project_binding_service"),
                    creation=service_context.get("session_creation_service"),
                )
                if _project_response is not None:
                    await ws.send_json(_project_response)

            elif msg_type == "sessions_list":
                # The bounded Project catalog and per-scope Session pages are
                # the sole read model.  Returning a second flat projection
                # would let clients regroup paths and recreate split authority.
                await ws.send_json(
                    {
                        "type": "sessions_list_error",
                        "request_id": raw.get("request_id"),
                        "payload": {
                            "code": "protocol_replaced",
                            "error": "use project_catalog_page and project_sessions_page",
                        },
                    }
                )

            elif msg_type == "session_delete":
                # 删除一个会话的全部消息（消息面板下拉里每项的 X）。
                _sd_payload = raw.get("payload", {}) or {}
                _sd_sid = _sd_payload.get("session_id") or ""
                _sd_sdb = service_context.get("session_db")
                _sd_ok = False
                _sd_code = None
                if _sd_sdb is not None and _sd_sid:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(_sd_sdb._db_path, _sd_sid)
                        _sd_workflows = service_context.get("workflow_service")
                        _sd_creation = service_context.get("session_creation_service")
                        if _sd_workflows is not None:
                            async with _sd_workflows.session_lock(_sd_sid):
                                if _sd_creation is not None:
                                    await _sd_creation.mark_deleted(_sd_sid)
                                else:
                                    await _sd_sdb.clear(_sd_sid)
                                await _sd_workflows.cancel_runs_for_session(
                                    _sd_sid,
                                    reason="session_deleted",
                                )
                        else:
                            if _sd_creation is not None:
                                await _sd_creation.mark_deleted(_sd_sid)
                            else:
                                await _sd_sdb.clear(_sd_sid)
                        _sd_attempts = service_context.get("context_attempt_store")
                        if _sd_attempts is not None:
                            _sd_attempts.purge_session(_sd_sid)
                        _sd_ok = True
                        logger.info("session_deleted sid=%s", _sd_sid)
                    except Exception as _sd_exc:  # noqa: BLE001
                        _sd_code = getattr(_sd_exc, "code", None)
                        logger.warning(
                            "session_delete_failed",
                            error=str(_sd_exc),
                            session_id=_sd_sid,
                        )
                await ws.send_json(
                    {
                        "type": "session_deleted",
                        "payload": {
                            "session_id": _sd_sid,
                            "ok": _sd_ok,
                            **({"code": _sd_code} if _sd_code else {}),
                        },
                    }
                )

            elif msg_type == "session_rename":
                # 消息面板「重命名话题」：写自定义标题（空=还原自动预览）。
                _sr_payload = raw.get("payload", {}) or {}
                _sr_sid = _sr_payload.get("session_id") or ""
                _sr_title = _sr_payload.get("title") or ""
                _sr_sdb = service_context.get("session_db")
                _sr_ok = False
                _sr_stored = ""
                _sr_code = None
                if _sr_sdb is not None and _sr_sid:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(_sr_sdb._db_path, _sr_sid)
                        _sr_stored = await _sr_sdb.set_session_title(_sr_sid, _sr_title)
                        _sr_ok = True
                        logger.info("session_renamed sid=%s title_len=%d", _sr_sid, len(_sr_stored))
                    except Exception as _sr_exc:  # noqa: BLE001
                        _sr_code = getattr(_sr_exc, "code", None)
                        logger.warning(
                            "session_rename_failed",
                            error=str(_sr_exc),
                            session_id=_sr_sid,
                        )
                await ws.send_json(
                    {
                        "type": "session_renamed",
                        "payload": {
                            "session_id": _sr_sid,
                            "title": _sr_stored,
                            "ok": _sr_ok,
                            **({"code": _sr_code} if _sr_code else {}),
                        },
                    }
                )

            elif msg_type == "session_messages_load":
                # P4-S23: panel reload (F5) needs to rehydrate chat
                # history from SessionDB. Returns messages for the
                # given session_id (default: ws session_id).
                # Capped at 200 msgs/session to keep payload sane —
                # users can scroll older messages via a future
                # paginated load if needed.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                limit = int(payload.get("limit") or 200)
                _sml_sdb = service_context.get("session_db")
                if _sml_sdb is not None:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(_sml_sdb._db_path, str(target_sid))
                    except Exception as _sml_exc:  # noqa: BLE001
                        _sml_code = getattr(_sml_exc, "code", None)
                        if _sml_code:
                            await ws.send_json(
                                {
                                    "type": "session_messages_error",
                                    "payload": {
                                        "ok": False,
                                        "session_id": target_sid,
                                        "code": _sml_code,
                                    },
                                }
                            )
                            continue
                        raise
                # Loading a transcript is also the product shell's explicit
                # declaration of which conversation this transport currently
                # presents.  Rebind the peer group before the consistency
                # gate: a recovered terminal may be committed while the
                # websocket is reconnecting, and its live notification must
                # route back to this selected history session rather than the
                # transport's startup/default topic.
                _remap_chat_peer_group(session_id, str(target_sid))
                sdb = service_context.get("session_db")
                msgs: list = []
                _companion_history_events: list[dict[str, Any]] = []
                if sdb is not None:
                    try:
                        _history_gate = service_context.get("session_terminal_projection_gate")
                        if _history_gate is not None:
                            await _history_gate.ensure_current(target_sid)
                        try:
                            _history_identity = host_state.companion_identity_gate.freeze()
                            await _bind_companion_inbox_route(
                                target_sid,
                                _history_identity,
                                create_if_absent=False,
                            )
                        except Exception as _history_route_exc:  # noqa: BLE001
                            logger.debug(
                                "companion_history_route_unchanged sid=%s err=%s",
                                target_sid,
                                type(_history_route_exc).__name__,
                            )
                        rows = await sdb.get_messages(target_sid, limit=limit)
                        # P6 bugfix 2026-05-14: 历史还原 — 之前过滤了
                        # role not in (user,assistant) 和 empty content,
                        # 导致 tool_call (role='assistant' content=''
                        # tool_calls=[...]) 和 tool_result (role='tool')
                        # 全部被丢，UI 重启后只剩 user 气泡。现在全部
                        # 返回，前端 ws.ts 决定怎么渲染。
                        msgs = []
                        _history_tool_names: dict[str, str] = {}
                        for _history_row in rows:
                            _history_calls = _history_row.get("tool_calls")
                            if not _history_calls:
                                continue
                            try:
                                _history_calls = (
                                    json.loads(_history_calls)
                                    if isinstance(_history_calls, str)
                                    else _history_calls
                                )
                                if isinstance(_history_calls, list):
                                    for _history_call in _history_calls:
                                        if not isinstance(_history_call, dict):
                                            continue
                                        _history_function = _history_call.get("function")
                                        if isinstance(_history_function, dict):
                                            _history_tool_names[
                                                str(_history_call.get("id") or "")
                                            ] = str(_history_function.get("name") or "")
                            except Exception:
                                continue
                        for r in rows:
                            if str(r.get("projection_kind") or "") == "companion_event":
                                # Companion rows are hydrated through the
                                # owner-fenced visibility overlay below, never
                                # as ordinary assistant text.
                                continue
                            _row_role = r.get("role") or ""
                            if _row_role not in ("user", "assistant", "tool"):
                                continue  # 仍跳过 system 等
                            _entry: dict[str, Any] = {
                                "id": str(r.get("workflow_event_id") or r.get("id") or ""),
                                "role": _row_role,
                                "text": r.get("content") or "",
                                "ts": float(r.get("created_at") or 0) * 1000,
                                "projection_kind": str(r.get("projection_kind") or ""),
                                "workflow_event_id": str(r.get("workflow_event_id") or ""),
                            }
                            if r.get("root_run_id"):
                                _entry["run_id"] = str(r["root_run_id"])
                            if r.get("task_scope_id"):
                                _entry["task_scope_id"] = str(r["task_scope_id"])
                            # tool_calls JSON (assistant 行调工具时)
                            _tcs_raw = r.get("tool_calls")
                            if _tcs_raw:
                                try:
                                    import json as _load_json

                                    _entry["tool_calls"] = project_public_tool_calls(
                                        _load_json.loads(_tcs_raw)
                                        if isinstance(_tcs_raw, str)
                                        else _tcs_raw
                                    )
                                except Exception:
                                    pass
                            # tool_call_id (tool 行的回指)
                            _tcid = r.get("tool_call_id")
                            if _tcid:
                                _entry["tool_call_id"] = _tcid
                                if _row_role == "tool":
                                    _public_history_result = project_public_tool_result(
                                        _history_tool_names.get(str(_tcid), ""),
                                        r.get("content") or "",
                                    )
                                    _entry["text"] = (
                                        json.dumps(_public_history_result, ensure_ascii=False)
                                        if not isinstance(_public_history_result, str)
                                        else _public_history_result
                                    )
                            msgs.append(_entry)

                        _workflow_history = service_context.get("workflow_service")
                        await _attach_workflow_history_events(
                            rows=rows,
                            messages=msgs,
                            session_id=target_sid,
                            session_db=sdb,
                            workflow_service=_workflow_history,
                        )
                        _companion_history_events = await _attach_companion_history_events(
                            rows=rows
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session_messages_consistency_failed",
                            error=str(exc),
                            session_id=target_sid,
                        )
                        await ws.send_json(
                            {
                                "type": "session_messages_error",
                                "payload": {
                                    "session_id": target_sid,
                                    "code": "product_view_not_current",
                                    "error": ("会话记录暂时无法确认是最新状态，请稍后重试。"),
                                },
                            }
                        )
                        continue
                # FEAT-A4: 带回 awaiting plan（不塞进被白名单 3659 过滤的 messages
                # 流，单独挂 payload.plan）。前端 rehydration 据此重建 plan card +
                # [执行]/[取消] 栏。只在 awaiting 时附；否则字段缺省 → 旧行为不变。
                _plan_payload: dict[str, Any] | None = None
                if sdb is not None:
                    try:
                        _sp = await sdb.get_session_plan(target_sid)
                        if _sp is not None and _sp.get("awaiting"):
                            _plan_payload = {
                                "rationale": _sp.get("rationale") or "",
                                "steps": _sp.get("steps") or [],
                                "target_directory": _sp.get("target_directory"),
                                "action_categories": _sp.get("action_categories") or [],
                                "auto_confirmed": bool(_sp.get("auto_confirmed")),
                                "awaiting": True,
                            }
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "session_plan_load_failed sid=%s err=%s",
                            target_sid,
                            exc,
                        )
                _msgs_payload: dict[str, Any] = {
                    "session_id": target_sid,
                    "messages": msgs,
                }
                _msgs_payload["companion_events"] = _companion_history_events
                if _plan_payload is not None:
                    _msgs_payload["plan"] = _plan_payload
                await ws.send_json(
                    {
                        "type": "session_messages_response",
                        "payload": _msgs_payload,
                    }
                )

            elif msg_type == "slash_command":
                # WI-A1 v1 — /command 解析（plans/2026-05-25-companion-code-
                # skill-upgrade）。InputBar 解析 `/<cmd> [args]` → 发本 WS
                # 消息；后端直接路由到 SkillLoader / goal_store / builtins，
                # 不走 AgentLoop。feature flag `[features] slash_commands`
                # 默认 OFF；OFF 时返 disabled 错。
                payload = raw.get("payload", {}) or {}
                cmd_name = (payload.get("command") or "").lstrip("/")
                cmd_args = payload.get("args") or ""
                target_sid = payload.get("session_id") or session_id
                _slash_enabled = bool(
                    getattr(
                        getattr(config, "features", None),
                        "slash_commands",
                        False,
                    )
                )
                if not _slash_enabled:
                    await ws.send_json(
                        {
                            "type": "slash_command_result",
                            "payload": {
                                "command": cmd_name,
                                "session_id": target_sid,
                                "result": {
                                    "type": "error",
                                    "message": "slash_commands feature disabled "
                                    "(set [features] slash_commands = true)",
                                },
                            },
                        }
                    )
                else:
                    try:
                        from deskpet.commands import dispatch_slash_command

                        _slash_skill_loader = await _current_slash_skill_catalog()
                        _slash_skill_catalog = _slash_skill_loader
                        _slash_goal_store = service_context.get("session_goal_store")
                        _slash_pref_mem = service_context.get("preference_memory")
                        _slash_result = await dispatch_slash_command(
                            cmd_name,
                            cmd_args,
                            target_sid,
                            skill_loader=_slash_skill_loader,
                            skill_catalog=_slash_skill_catalog,
                            session_goal_store=_slash_goal_store,
                            session_pref_memory=_slash_pref_mem,
                        )
                    except Exception as _slash_exc:  # noqa: BLE001
                        logger.warning(
                            "slash_command dispatch failed: %s",
                            _slash_exc,
                        )
                        _slash_result = {
                            "type": "error",
                            "message": f"dispatch error: {_slash_exc}",
                        }
                    if _slash_result.get("type") == ("instruction_activation_request"):
                        _slash_text = str(_slash_result.get("user_text") or "").strip()
                        if not _slash_text:
                            raise RuntimeError("slash instruction activation is empty")
                        from deskpet.companion.skills import (
                            PreparedSkillInvocationScopeV1,
                        )

                        _slash_scope = PreparedSkillInvocationScopeV1.from_dict(
                            _slash_result.get("prepared_skill_scope") or {}
                        )
                        _slash_arguments = tuple(
                            str(item) for item in (_slash_result.get("arguments") or ())
                        )
                        _launch_product_harness_chat(
                            ws,
                            _slash_text,
                            target_sid,
                            client_request_id=(
                                str(
                                    payload.get("request_id") or raw.get("request_id") or ""
                                ).strip()
                                or None
                            ),
                            client_turn_id=(str(payload.get("turn_id") or "").strip() or None),
                            prepared_skill_scope=_slash_scope,
                            skill_arguments=_slash_arguments,
                        )
                        _slash_result = {
                            "type": "accepted",
                            "route": "product_harness",
                            "skill_name": cmd_name,
                            "prepared_skill_scope": _slash_result.get("prepared_skill_scope"),
                        }
                    await ws.send_json(
                        {
                            "type": "slash_command_result",
                            "payload": {
                                "command": cmd_name,
                                "session_id": target_sid,
                                "result": _slash_result,
                            },
                        }
                    )

            elif msg_type == "chat_v2_interrupt":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                target_run_id = str(
                    payload.get("root_run_id") or payload.get("run_id") or ""
                ).strip()
                before = _authoritative_sdk_run_projection(
                    target_run_id, str(payload.get("sdk_run_id") or "")
                )
                expected_version = payload.get("expected_run_version")
                stale = expected_version is not None and int(expected_version) != int(
                    before["run_version"]
                )
                terminal_before = before["state"] in {"completed", "failed", "cancelled"}
                cancelled = False
                if not stale and not terminal_before:
                    cancelled = await _cancel_product_harness_run(
                        target_sid,
                        target_run_id,
                        reason=str(payload.get("reason") or "user_interrupt"),
                    )
                after = _authoritative_sdk_run_projection(
                    target_run_id, str(before.get("sdk_run_id") or "")
                )
                _interrupt_evt = {
                    "type": "chat_v2_interrupted",
                    "payload": {
                        "cancel_command_id": str(payload.get("cancel_command_id") or ""),
                        "session_id": target_sid,
                        "run_id": target_run_id,
                        "cancelled": cancelled,
                        "stale_expected_version": stale,
                        **after,
                    },
                }
                await ws.send_json(_interrupt_evt)
                await _broadcast_default_chat_peers(ws, _interrupt_evt)

            elif msg_type == "plan_confirm":
                # superpowers 决策2 — plan-confirm 硬门的用户裁决。
                # 前端 PlanCard 的 [执行]/[取消] 按钮发来 {session_id, decision}。
                # R6 只接受完整 durable decision fence。
                payload = raw.get("payload", {}) or {}
                _csid = payload.get("session_id") or session_id
                _decision = payload.get("decision") or "go"
                if host_state.sdk_ingress is not None and host_state.sdk_ingress.accepting:
                    _run_id = str(payload.get("run_id") or "").strip()
                    _decision_id = str(payload.get("decision_id") or "").strip()
                    _nonce = str(payload.get("nonce") or "").strip()
                    _version = payload.get("version")
                    if not _run_id or not _decision_id or not _nonce or _version is None:
                        raise ValueError("plan confirmation requires the durable decision fence")
                    _receipt = await _signal_product_harness_decision(
                        _csid,
                        payload,
                        {"decision": _decision},
                    )
                    logger.info(
                        "harness_plan_decision sid=%s run_id=%s accepted=%s duplicate=%s",
                        _csid,
                        _run_id,
                        _receipt.accepted,
                        _receipt.duplicate,
                    )
                    _sdb_pc = service_context.get("session_db")
                    if _sdb_pc is not None:
                        await _sdb_pc.clear_session_plan_awaiting(_csid)
                    continue
            elif msg_type == "models_list":
                # The session model picker is data-driven. Pull the
                # relay's live catalog (中转站 GET /models); fall
                # back to the registry endpoint's configured `models`
                # array if the live fetch fails. Each model carries a
                # per-family capability map so the picker only shows the
                # params that model actually supports (gpt-5.x exposes
                # reasoning_effort; claude opus/sonnet exposes thinking;
                # they differ).
                _reg = service_context.get("provider_registry")
                _model_ids: list[str] = []
                _source = "none"
                _base_url = ""
                # 生效的 provider 默认模型：preferred_model 留空(跟随 provider 默认)
                # 时,UI 据此显示「当前真正用的模型」而非「默认模型」占位词。
                _default_model = ""
                if _reg is not None:
                    try:
                        _chain = _reg.get_chain()
                    except Exception:
                        _chain = []
                    if _chain:
                        _first = _chain[0]
                        _pid0 = _first.get("id") if isinstance(_first, dict) else None
                        _entry0 = _reg.get_entry(_pid0) if _pid0 else None
                        if _entry0 is not None:
                            _base_url = str(getattr(_entry0, "base_url", "") or "")
                            # ProviderEntry.model = default_model or models[0]。
                            _default_model = str(getattr(_entry0, "model", "") or "")
                            _cfg_models = list(getattr(_entry0, "models", []) or [])
                            _api_key = _reg.resolve_api_key(_pid0)
                            try:
                                from llm.model_catalog import fetch_models as _fm

                                _live = await _fm(_base_url, _api_key, timeout=8.0)
                            except Exception:
                                _live = []
                            if _live:
                                _model_ids = _live
                                _source = "live"
                                # A successful live catalog is also the
                                # durable fallback for the next restart or a
                                # later /models outage. Persist only if the
                                # Provider still has the exact identity and
                                # endpoint used for this fetch; the registry
                                # method is idempotent and does not invalidate
                                # Session bindings for a cache-only refresh.
                                try:
                                    await _reg.cache_discovered_models(
                                        _pid0,
                                        _live,
                                        expected_incarnation_id=str(
                                            getattr(_entry0, "incarnation_id", "") or ""
                                        ),
                                        expected_config_revision=int(
                                            getattr(_entry0, "config_revision", 0) or 0
                                        ),
                                        expected_base_url=_base_url,
                                    )
                                except Exception as _cache_exc:  # noqa: BLE001
                                    logger.warning(
                                        "provider_catalog_cache_refresh_failed "
                                        "provider=%s error=%s",
                                        _pid0,
                                        str(_cache_exc)[:200],
                                    )
                            elif _cfg_models:
                                _model_ids = _cfg_models
                                _source = "config"
                # Fallback: provider_registry chain 为空时（典型场景：用户登录
                # 中转站后，relay provider 只是前端"虚拟项"、不走 settings_providers_add
                # 注册进 backend registry，见 SettingsProviders.tsx），用当前
                # local_llm（登录后经 update_cloud_config 已切到中转站 base_url+key）
                # 拉中转站 /models。这样登录中转站即可在当前会话换模型，无需手动
                # 再"添加 Provider"。
                if not _model_ids and host_state.local_llm is not None:
                    _ll_base = str(getattr(host_state.local_llm, "base_url", "") or "")
                    _ll_key = getattr(host_state.local_llm, "api_key", None)
                    # 跳过本地 ollama（localhost）—— 它的 /models 是本地小模型，
                    # 不是中转站目录，且 key 通常是占位 "ollama"。
                    if _ll_base and "localhost" not in _ll_base and "127.0.0.1" not in _ll_base:
                        try:
                            from llm.model_catalog import fetch_models as _fm2

                            _live_ll = await _fm2(_ll_base, _ll_key, timeout=8.0)
                        except Exception:
                            _live_ll = []
                        if _live_ll:
                            _model_ids = _live_ll
                            _source = "live-local_llm"
                            _base_url = _ll_base
                # registry chain 为空时,生效默认模型取 local_llm 的运行时 model
                # (onboarding 登录中转站后已写 gpt-5.5)。
                if not _default_model and host_state.local_llm is not None:
                    _default_model = str(getattr(host_state.local_llm, "model", "") or "")
                from llm.model_catalog import build_catalog as _bc

                await ws.send_json(
                    {
                        "type": "models_list_response",
                        "payload": {
                            "models": _bc(_model_ids),
                            "source": _source,
                            "base_url": _base_url,
                            "default_model": _default_model,
                        },
                    }
                )

            elif msg_type == "session_provider_get":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or "").strip()
                session_db = service_context.get("session_db")
                readiness = service_context.get("provider_routing_readiness")
                if not target_sid or session_db is None:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "missing_field",
                                "detail": "session_id required + session_db must be initialized",
                            },
                        }
                    )
                    continue
                try:
                    readiness.require_ready()
                except Exception:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_routing_initializing",
                                "detail": "provider routing is not ready",
                            },
                        }
                    )
                    continue
                binding = await session_db.get_session_provider_binding_authority(target_sid)
                await ws.send_json(
                    {
                        "type": "session_provider_binding",
                        "payload": {
                            "session_id": target_sid,
                            "provider_id": binding.get("provider_id"),
                            "preferred_model": binding.get("preferred_model"),
                            "model_params": binding.get("model_params"),
                            "provider_incarnation_id": binding.get("provider_incarnation_id"),
                            "provider_config_revision": binding.get("provider_config_revision"),
                            "binding_epoch": binding.get("binding_epoch", 0),
                        },
                    }
                )

            elif msg_type in ("session_set_provider", "session_set_model"):
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or "").strip()
                session_db = service_context.get("session_db")
                registry = service_context.get("provider_registry")
                readiness = service_context.get("provider_routing_readiness")
                if not target_sid or session_db is None or registry is None:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "missing_field",
                                "detail": "session_id required + session_db must be initialized",
                            },
                        }
                    )
                    continue
                if "expected_binding_epoch" not in payload:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_binding_conflict",
                                "detail": "expected_binding_epoch is required",
                                "session_id": target_sid,
                            },
                        }
                    )
                    continue
                try:
                    readiness.require_ready()
                except Exception:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_routing_initializing",
                                "detail": "provider routing is not ready",
                            },
                        }
                    )
                    continue
                current = await session_db.get_session_provider_binding_authority(target_sid)
                if msg_type == "session_set_provider":
                    new_provider_id = payload.get("provider_id")
                    new_model = current.get("preferred_model")
                    new_params = current.get("model_params")
                    response_type = "session_provider_set"
                else:
                    new_provider_id = current.get("provider_id")
                    new_model = payload.get("model")
                    raw_params = payload.get("params")
                    new_params = raw_params if isinstance(raw_params, dict) else None
                    response_type = "session_model_set"
                try:
                    if new_provider_id and (
                        "expected_provider_incarnation_id" not in payload
                        or "expected_provider_config_revision" not in payload
                    ):
                        raise ValueError("provider identity expectation is required")
                    binding = await registry.set_session_binding(
                        session_db,
                        session_id=target_sid,
                        provider_id=new_provider_id,
                        preferred_model=new_model,
                        model_params=new_params,
                        expected_binding_epoch=int(payload["expected_binding_epoch"]),
                        expected_incarnation_id=payload.get("expected_provider_incarnation_id"),
                        expected_config_revision=(
                            int(payload["expected_provider_config_revision"])
                            if payload.get("expected_provider_config_revision") is not None
                            else None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    authoritative = await session_db.get_session_provider_binding_authority(
                        target_sid
                    )
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": (
                                    "provider_binding_conflict"
                                    if str(exc) == "provider_binding_conflict"
                                    or isinstance(exc, ValueError)
                                    else "internal_error"
                                ),
                                "detail": str(exc),
                                "session_id": target_sid,
                            },
                        }
                    )
                    await ws.send_json(
                        {
                            "type": "session_provider_binding",
                            "payload": {"session_id": target_sid, **authoritative},
                        }
                    )
                else:
                    await ws.send_json(
                        {
                            "type": response_type,
                            "payload": {
                                "session_id": target_sid,
                                **binding,
                            },
                        }
                    )
                    # The provider/model binding and Context Usage authority
                    # advance in the same durable transaction.  Push the
                    # freshly committed binding-only snapshot immediately so
                    # a new Session does not keep rendering its pre-binding
                    # "no model yet" cache until the first LLM call.
                    try:
                        await _send_context_usage_authority(ws, session_db, target_sid)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "context_usage_binding_push_failed sid=%s err=%s",
                            target_sid,
                            exc,
                        )

            # 2026-06-13: 此处原有一个简版 "model_context_set" 分支(只认
            # {model, context_window}) —— 它在 elif 链里抢在 p4_ipc(6456)
            # 之前,把 ModelContextCard 的完整协议请求({scope, model,
            # fields})劫持并报错,坏了设置卡的保存两天。已删,统一走
            # p4_ipc 完整版(fields 白名单含 context_window/compact_at_pct,
            # scope global/project 深合并 TOML)。ChangeModelModal 的档位
            # 选择也已改发完整协议。

            elif msg_type in (
                "settings_providers_list_request",
                "settings_providers_add",
                "settings_providers_update",
                "settings_providers_remove",
                "settings_providers_reorder",
            ):
                # P5-S2 multi-provider-management Phase 2:
                # CRUD + reorder against LLMProviderRegistry. Mutations
                # broadcast a `providers_changed` event to ALL control
                # connections so multi-window UIs (pet panel + code panel)
                # stay in sync without polling.
                #
                # Spec: openspec/changes/multi-provider-management/specs/
                #       frontend-ipc-surface/spec.md
                _payload = raw.get("payload", {}) or {}
                _reg = service_context.get("provider_registry")
                if _reg is None:
                    await ws.send_json(
                        {
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "registry_unavailable",
                                "detail": "provider_registry not initialized",
                            },
                        }
                    )
                    continue

                async def _broadcast_providers_changed() -> None:
                    """Fan out the new provider list to every open control
                    ws. Mirrors the pattern used by `_todo_broadcaster` /
                    supervisor alert broadcaster."""
                    snapshot = {
                        "type": "providers_changed",
                        "payload": {"providers": _reg.list_providers()},
                    }
                    if not _control_connections:
                        return
                    for _sid_key, _ws_obj in list(_control_connections.items()):
                        try:
                            await _ws_obj.send_json(snapshot)
                        except Exception as _bex:  # noqa: BLE001
                            logger.debug(
                                "providers_changed_broadcast_failed sid=%s err=%s",
                                _sid_key,
                                _bex,
                            )

                if msg_type == "settings_providers_list_request":
                    await ws.send_json(
                        {
                            "type": "settings_providers_list_response",
                            "payload": {"providers": _reg.list_providers()},
                        }
                    )

                elif msg_type == "settings_providers_add":
                    try:
                        entry = await _reg.add_provider(_payload)
                    except ValueError as exc:
                        # Classify error: duplicate vs missing_field vs
                        # invalid_id. The registry raises a single
                        # ValueError for all of these; we sniff the text.
                        _msg = str(exc)
                        if "already exists" in _msg:
                            _reason = "duplicate_id"
                        elif "missing required fields" in _msg or "api_key is required" in _msg:
                            _reason = "missing_field"
                        elif "invalid provider id" in _msg:
                            _reason = "invalid_id"
                        else:
                            _reason = "invalid_payload"
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": _reason, "detail": _msg},
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "internal_error", "detail": str(exc)},
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "type": "settings_providers_added",
                                "payload": {"provider": entry.to_public_dict()},
                            }
                        )
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_update":
                    _pid = _payload.get("id")
                    _patch = _payload.get("patch", {}) or {}
                    if (
                        not _pid
                        or "expected_incarnation_id" not in _payload
                        or "expected_config_revision" not in _payload
                    ):
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "missing_field",
                                    "detail": "id and expected provider identity are required",
                                },
                            }
                        )
                        continue
                    try:
                        _assert_sdk_provider_mutation_allowed((_pid,))
                        entry = await _reg.update_provider(
                            _pid,
                            expected_incarnation_id=_payload.get("expected_incarnation_id"),
                            expected_config_revision=int(_payload["expected_config_revision"]),
                            **_patch,
                        )
                    except KeyError:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "not_found",
                                    "detail": f"provider {_pid!r} not found",
                                },
                            }
                        )
                    except ProviderMutationConflict as exc:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "provider_binding_conflict",
                                    "detail": str(exc),
                                },
                            }
                        )
                    except ValueError as exc:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "invalid_payload", "detail": str(exc)},
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "internal_error", "detail": str(exc)},
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "type": "settings_providers_updated",
                                "payload": {"provider": entry.to_public_dict()},
                            }
                        )
                        _refresh_research_live_llm()
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_remove":
                    _pid = _payload.get("id")
                    if (
                        not _pid
                        or "expected_incarnation_id" not in _payload
                        or "expected_config_revision" not in _payload
                    ):
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "missing_field",
                                    "detail": "id and expected provider identity are required",
                                },
                            }
                        )
                        continue
                    try:
                        _assert_sdk_provider_mutation_allowed((_pid,))
                        await _reg.remove_provider(
                            _pid,
                            expected_incarnation_id=_payload.get("expected_incarnation_id"),
                            expected_config_revision=int(_payload["expected_config_revision"]),
                        )
                    except KeyError:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "not_found",
                                    "detail": f"provider {_pid!r} not found",
                                },
                            }
                        )
                    except ProviderMutationConflict as exc:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "provider_binding_conflict",
                                    "detail": str(exc),
                                },
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "internal_error", "detail": str(exc)},
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "type": "settings_providers_removed",
                                "payload": {"id": _pid},
                            }
                        )
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_reorder":
                    _ordered = _payload.get("ordered_ids") or []
                    _expected_raw = _payload.get("expected_versions")
                    try:
                        _assert_sdk_provider_mutation_allowed(_ordered)
                        if not isinstance(_expected_raw, dict):
                            raise ValueError("expected_versions is required")
                        _expected_versions = {
                            str(pid): (
                                str(value["incarnation_id"]),
                                int(value["config_revision"]),
                            )
                            for pid, value in _expected_raw.items()
                            if isinstance(value, dict)
                        }
                        await _reg.reorder(list(_ordered), expected_versions=_expected_versions)
                    except ProviderMutationConflict as exc:
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {
                                    "reason": "provider_binding_conflict",
                                    "detail": str(exc),
                                },
                            }
                        )
                    except ValueError as exc:
                        _msg = str(exc)
                        # Compute the missing-id detail for nicer UX.
                        _have = {p["id"] for p in _reg.list_providers()}
                        _missing = sorted(_have - set(_ordered))
                        _detail = f"missing: {', '.join(_missing)}" if _missing else _msg
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "incomplete_order", "detail": _detail},
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "settings_providers_error",
                                "payload": {"reason": "internal_error", "detail": str(exc)},
                            }
                        )
                    else:
                        await ws.send_json(
                            {
                                "type": "settings_providers_reordered",
                                "payload": {"providers": _reg.list_providers()},
                            }
                        )
                        await _broadcast_providers_changed()

            elif msg_type == "settings_providers_probe_models":
                # P5-S2 v2: GET <base_url>/models for an OpenAI-compat endpoint
                # to discover available models. Used by AddProviderModal "🔍
                # auto-fetch" so users don't have to type model names by hand.
                # Reply: { ok, models: [string], detail? }
                _payload = raw.get("payload", {}) or {}
                _reg = service_context.get("provider_registry")
                if _reg is None:
                    await ws.send_json(
                        {
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": "provider_registry not initialized",
                            },
                        }
                    )
                    continue
                _base_url = str(_payload.get("base_url") or "").rstrip("/")
                _api_key = str(_payload.get("api_key") or "")
                _provider_id = str(_payload.get("provider_id") or "").strip()
                if not _base_url:
                    await ws.send_json(
                        {
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": "base_url required",
                            },
                        }
                    )
                    continue
                if not _api_key and _provider_id:
                    _entry = _reg.get_entry(_provider_id)
                    if _entry is None:
                        await ws.send_json(
                            {
                                "type": "settings_providers_probe_models_response",
                                "payload": {
                                    "ok": False,
                                    "models": [],
                                    "detail": "provider not found",
                                },
                            }
                        )
                        continue
                    if str(_entry.base_url).rstrip("/") != _base_url:
                        await ws.send_json(
                            {
                                "type": "settings_providers_probe_models_response",
                                "payload": {
                                    "ok": False,
                                    "models": [],
                                    "detail": "base_url differs from saved provider; enter its API key to probe",
                                },
                            }
                        )
                        continue
                    _api_key = str(_reg.resolve_api_key(_provider_id) or "")
                    if not _api_key:
                        await ws.send_json(
                            {
                                "type": "settings_providers_probe_models_response",
                                "payload": {
                                    "ok": False,
                                    "models": [],
                                    "detail": "saved provider API key is unavailable",
                                },
                            }
                        )
                        continue
                try:
                    import httpx as _httpx

                    _models_url = f"{_base_url}/models"
                    _headers = {"Accept": "application/json"}
                    if _api_key and _api_key.lower() != "ollama":
                        _headers["Authorization"] = f"Bearer {_api_key}"
                    async with _httpx.AsyncClient(timeout=15.0) as _hc:
                        _resp = await _hc.get(_models_url, headers=_headers)
                    if _resp.status_code != 200:
                        await ws.send_json(
                            {
                                "type": "settings_providers_probe_models_response",
                                "payload": {
                                    "ok": False,
                                    "models": [],
                                    "detail": f"HTTP {_resp.status_code}: {_resp.text[:200]}",
                                },
                            }
                        )
                    else:
                        _body = _resp.json()
                        # OpenAI-format: {"data": [{"id": "..."}, ...]}
                        # Ollama-format: {"models": [{"name": "..."}, ...]}
                        # tolerate either.
                        _items = _body.get("data") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = _body.get("models") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = []
                        _names: list[str] = []
                        for _it in _items:
                            if isinstance(_it, dict):
                                _n = _it.get("id") or _it.get("name") or _it.get("model")
                                if _n:
                                    _names.append(str(_n))
                            elif isinstance(_it, str):
                                _names.append(_it)
                        await ws.send_json(
                            {
                                "type": "settings_providers_probe_models_response",
                                "payload": {
                                    "ok": True,
                                    "models": _names,
                                },
                            }
                        )
                except Exception as _probe_exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": str(_probe_exc)[:300],
                            },
                        }
                    )

            elif msg_type in ("chat", "chat_v2"):
                # P4-S20-LLM-Unified: 单一对话路径。
                # 永远走 AgentLoop tool_use loop（即原 chat_v2 路径），
                # 同时保留 ContextAssembler 长期记忆召回 + BillingLedger
                # 计费（继承自原 chat 路径）。
                #
                # CRITICAL: run in a background task so the WS recv
                # loop keeps draining permission_response messages —
                # gate's responder Future never gets set otherwise.
                #
                # `chat_v2` 类型保留作为别名，前端无感升级。
                # Payload may carry an explicit task `session_id`; honor it
                # and otherwise fall back to the WS-level main session.
                _payload = raw.get("payload", {}) or {}
                text = _payload.get("text", "")
                from deskpet.agent.attachment_budget import (
                    normalize_user_attachment_blocks as _normalize_attachment_blocks,
                )

                _attachment_blocks = _normalize_attachment_blocks(_payload.get("attachments"))
                _msg_sid = _resolve_chat_source_session(
                    session_id,
                    _payload.get("session_id"),
                )
                _base_msg_sid = _msg_sid
                from deskpet.session.task_scope import (
                    source_session_for_created_conversation as _creation_source_sid,
                )

                _source_session_id = _creation_source_sid(
                    _payload.get("session_id"),
                    _base_msg_sid,
                )
                _scope_decision = _resolve_chat_task_scope(
                    base_sid=_msg_sid,
                    text=text,
                    payload=_payload,
                )
                _msg_sid = _scope_decision.effective_sid
                text = _scope_decision.stripped_text
                _memory_policy_override = (
                    {"l2_page_in": "always"}
                    if getattr(
                        _scope_decision,
                        "force_l2_page_in",
                        None,
                    )
                    == "always"
                    else None
                )
                if _sdk_desktop_test_enabled():
                    if (text or "").strip():
                        _launch_product_harness_chat(
                            ws,
                            text,
                            _msg_sid,
                            None,
                            False,
                            _attachment_blocks,
                            str(_payload.get("request_id") or raw.get("request_id") or "") or None,
                            str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
                        )
                    continue
                if _scope_decision.created:
                    _legacy_creation = service_context.get("session_creation_service")
                    _legacy_request_id = str(
                        _payload.get("request_id") or raw.get("request_id") or uuid.uuid4()
                    )
                    try:
                        _legacy_created = await _legacy_creation.create_conversation_session(
                            request_id=f"legacy-new-session:{_legacy_request_id}",
                            project_id=None,
                            source_session_id=_source_session_id,
                        )
                        _msg_sid = str(_legacy_created["session"]["session_id"])
                    except Exception as _legacy_create_exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": "新会话创建失败，请重试。",
                                    "code": str(
                                        getattr(_legacy_create_exc, "code", "session_create_failed")
                                    ),
                                    "retryable": True,
                                    # Empty-state failures have no Session owner.
                                    # Keep this empty so App's global error banner
                                    # does not filter it as an inactive pseudo sid.
                                    "session_id": _source_session_id or "",
                                    "request_id": _legacy_request_id,
                                },
                            }
                        )
                        continue
                _identity_gate = service_context.get("companion_identity_gate")
                try:
                    if _identity_gate is None:
                        raise RuntimeError("companion_identity_not_ready")
                    # Freeze now, before a background Run is created. Later
                    # owner-aware boundaries consume this immutable snapshot;
                    # account switches never rewrite an in-flight Turn.
                    _frozen_companion_owner = _identity_gate.freeze()
                    _companion_route_bound = await _bind_companion_inbox_route(
                        _msg_sid,
                        _frozen_companion_owner,
                        create_if_absent=True,
                    )
                except Exception as _identity_exc:  # noqa: BLE001
                    # request_id 必须回传：这一轮在 Run 被预约之前就夭折了，
                    # 前端的 pending_root_request_id 只能靠它认领并清除。少了
                    # 它，该会话此后每条消息都会被判成 "等待 root run" 而永远
                    # 不发出去 —— 会话被静默卡死（r4 S18 实测 default 会话）。
                    # 这个 try 覆盖的不只是身份门：_bind_companion_inbox_route
                    # 的属主围栏错误（owner_tombstoned / rebind_forbidden 等）
                    # 也会落到这里。原实现一律报 companion_identity_not_ready，
                    # 把"会话属主坏了"说成"身份没就绪"，排查时会被带偏很远
                    # （r5 真机实测：default 被墓碑化后一直显示身份未就绪）。
                    # 真实原因放进 detail，code 仍保持稳定供前端分支。
                    _identity_detail = str(
                        getattr(_identity_exc, "code", "")
                        or _identity_exc
                        or type(_identity_exc).__name__
                    )
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": "companion_identity_not_ready",
                                "code": "companion_identity_not_ready",
                                "detail": _identity_detail,
                                "retryable": True,
                                "session_id": _msg_sid,
                                "request_id": str(
                                    _payload.get("request_id") or raw.get("request_id") or ""
                                )
                                or None,
                                "turn_id": str(_payload.get("turn_id") or raw.get("turn_id") or "")
                                or None,
                            },
                        }
                    )
                    logger.info(
                        "companion_chat_blocked_identity_not_ready",
                        session_id=_msg_sid,
                        error=type(_identity_exc).__name__,
                        # 只记异常类名会把所有属主围栏错误压成 'RuntimeError'，
                        # 日志里看不出到底是身份没就绪还是会话属主坏了。
                        detail=_identity_detail,
                    )
                    continue
                if not _companion_route_bound:
                    _route_error = (
                        "新会话创建失败，请重试。"
                        if _scope_decision.created
                        else "当前选择的是只读历史会话，请重新发送。"
                    )
                    _route_code = (
                        "companion_session_create_failed"
                        if _scope_decision.created
                        else "companion_session_read_only"
                    )
                    # 同上：Run 预约前夭折的一轮必须带 request_id 回去，
                    # 否则前端 pending_root_request_id 永远挂着。
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": _route_error,
                                "code": _route_code,
                                "retryable": bool(_scope_decision.created),
                                "session_id": _msg_sid,
                                "request_id": str(
                                    _payload.get("request_id") or raw.get("request_id") or ""
                                )
                                or None,
                                "turn_id": str(_payload.get("turn_id") or raw.get("turn_id") or "")
                                or None,
                            },
                        }
                    )
                    logger.info(
                        "companion_chat_blocked_read_only_session",
                        session_id=_msg_sid,
                    )
                    continue
                if _scope_decision.created:
                    try:
                        # 2026-08-09：这里原先直接引用局部名 `session_db`——它只在
                        # `session_provider_get/set` 两个分支里被赋值，而 Python 的
                        # 函数作用域让它在整个 control_channel 里都是局部名。以前不炸
                        # 是因为 ChatView 挂载必发 session_provider_get 把它绑上；
                        # 取消保留会话后空态 activeSid="" ⇒ hydration 直接 return ⇒
                        # 该消息永不发出 ⇒ 空态直发新建会话时 UnboundLocalError
                        # （真机实测）。改为每次从 service_context 取，不依赖执行顺序。
                        _inherit_sdb = service_context.get("session_db")
                        if _inherit_sdb is None:
                            raise RuntimeError("session_db_unavailable")
                        _inherited_model_binding = await _inherit_sdb.get_session_provider_binding(
                            _msg_sid
                        )
                    except Exception as _model_inherit_exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": "新会话模型配置继承失败，请重试。",
                                    "code": "session_model_inherit_failed",
                                    "retryable": True,
                                    "session_id": _base_msg_sid,
                                },
                            }
                        )
                        logger.exception(
                            "session_model_inherit_failed",
                            source_session_id=_base_msg_sid,
                            target_session_id=_msg_sid,
                            error=type(_model_inherit_exc).__name__,
                            detail=str(_model_inherit_exc)[:240],
                        )
                        continue
                    # The control socket keeps a stable transport id while
                    # the visible conversation switches to this fresh,
                    # owner-fenced UUID session.
                    _remap_chat_peer_group(session_id, _msg_sid)
                    _switch_payload = {
                        "old_sid": _base_msg_sid,
                        "new_sid": _msg_sid,
                        "reason": _scope_decision.reason,
                        "provider_id": _inherited_model_binding.get("provider_id"),
                        "preferred_model": _inherited_model_binding.get("preferred_model"),
                        "model_params": _inherited_model_binding.get("model_params"),
                    }
                    for _evt_type in (
                        "session_switched",
                        "task_session_started",
                    ):
                        _switch_evt = {
                            "type": _evt_type,
                            "payload": dict(_switch_payload),
                        }
                        try:
                            await ws.send_json(_switch_evt)
                        except Exception:
                            pass
                        await _broadcast_default_chat_peers(ws, _switch_evt)
                    await _send_new_session_origin_user_echo(ws, _msg_sid, text)
                # Clicking an empty "new topic" still creates and switches
                # session above, but must not launch an empty AgentLoop turn.
                if not (text or "").strip():
                    continue
                if deskpet_tool_registry_v2 is None or permission_gate_v2 is None:
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": "v2 stack not initialized",
                                "session_id": _msg_sid,
                            },
                        }
                    )
                    continue
                # 方案 B（2026-08-20）：continuation 退化为 fresh run。SDK 0.1.4
                # 的 ReAct driver 是单轮语义，多轮对话 = 每条用户消息一个 fresh run、
                # 历史由 SDK context 组装。旧的 projection/boundary + signal 契约是
                # 8/17 迁移遗留的半成品（写投影的 create_task_context 已失联、signal
                # 签名未随 SDK 0.1.3 变更），继续走它只会报 identity mismatch。因此
                # 忽略 target_root_run_id/task_scope_id，直接落入下方 fresh run 分支。

                # P5-S2 Phase 4: a fresh user-initiated chat message means
                # the user has implicitly granted a new auto-resume budget.
                # Reset the attempts counter so subsequent failures may
                # auto-resume up to ``max_attempts`` times again. Skipped
                # for synthetic re-entry texts (`<<supervisor_followup>>`,
                # `<<auto_resume>>`) so they consume the existing budget.
                if not (text or "").startswith("<<"):
                    _sa_for_reset = service_context.get("session_activity")
                    if _sa_for_reset is not None:
                        try:
                            await _sa_for_reset.reset_auto_resume_attempts(_msg_sid)
                        except Exception as _ex:  # noqa: BLE001
                            logger.debug("auto_resume_reset_failed sid=%s err=%s", _msg_sid, _ex)

                _chat_task = _launch_product_harness_chat(
                    ws,
                    text,
                    _msg_sid,
                    _memory_policy_override,
                    bool(_scope_decision.created),
                    _attachment_blocks,
                    str(_payload.get("request_id") or raw.get("request_id") or "") or None,
                    str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
                )

                def _make_followup_cb(target_sid: str, target_ws):
                    def _cb(_t):
                        async def _maybe_followup():
                            try:
                                _nq_check = service_context.get("nudge_queue")
                                if _nq_check is None or not await _nq_check.peek(target_sid):
                                    return
                                new_task = _launch_product_harness_chat(
                                    target_ws, "<<supervisor_followup>>", target_sid
                                )
                                new_task.add_done_callback(_make_followup_cb(target_sid, target_ws))
                                logger.info(
                                    "supervisor_followup_scheduled sid=%s",
                                    target_sid,
                                )
                            except Exception as _exc:  # noqa: BLE001
                                logger.debug(
                                    "supervisor_followup_failed sid=%s error=%s",
                                    target_sid,
                                    _exc,
                                )

                        try:
                            asyncio.create_task(_maybe_followup())
                        except RuntimeError:
                            # No running loop (shutdown); silently skip.
                            pass

                    return _cb

                _chat_task.add_done_callback(_make_followup_cb(_msg_sid, ws))

            elif msg_type == "interrupt":
                payload = raw.get("payload", {}) or {}
                voice_transport = _voice_transports.get(session_id)
                if voice_transport is not None:
                    voice_transport.interrupt()
                await _cancel_product_harness_run(
                    session_id,
                    str(payload.get("run_id") or "").strip(),
                    reason="voice_interrupt",
                )
                await ws.send_json({"type": "interrupt_ack"})

            elif msg_type == "budget_status":
                # P2-1-S8: SettingsPanel "今日使用" pulls from here.
                try:
                    status = await billing_ledger.status()
                    await ws.send_json({"type": "budget_status", "payload": status})
                except Exception as exc:
                    logger.warning("budget_status_failed", error=str(exc))
                    await ws.send_json(
                        {
                            "type": "error",
                            "payload": {"message": f"budget_status failed: {exc}"},
                        }
                    )

            elif msg_type in ("memory_list", "memory_delete", "memory_clear", "memory_export"):
                # S14 (V5 §6 threat 5): user-facing controls over persisted
                # conversation history. All four go through the same memory
                # store the agent reads from, so redaction-on-write still holds.
                await _handle_memory_message(ws, session_id, msg_type, raw.get("payload", {}) or {})

            elif msg_type in p4_ipc.P4_IPC_MESSAGE_TYPES:
                # P4-S11 (§16.8): MemoryPanel + ContextTrace IPC surface.
                # Gracefully degrades when P4 services aren't registered
                # (pre-S12 wire-in) — UI shows empty state instead of error.
                await p4_ipc.handle(
                    ws,
                    session_id,
                    msg_type,
                    raw.get("payload", {}) or {},
                    service_context,
                )

            elif msg_type.startswith("workflow_"):
                # Durable Graph/Trace/Replay/Eval IPC. The service is built
                # during lifespan startup; keeping the dispatcher here avoids
                # coupling the workflow core to FastAPI/WebSocket types.
                from deskpet.workflows.ipc import start_workflow_ipc_dispatch

                workflow_service = service_context.get("workflow_service")
                if workflow_service is None:
                    await ws.send_json(
                        {
                            "type": "workflow_ipc_error",
                            "request_type": msg_type,
                            "request_id": raw.get("request_id"),
                            "ok": False,
                            "error": {
                                "code": "workflow_service_unavailable",
                                "message": "Durable workflow service is unavailable",
                                "retryable": True,
                            },
                        }
                    )
                else:
                    if await _try_resolve_execution_workflow_decision(
                        ws,
                        session_id=session_id,
                        raw=raw,
                        workflow_service=workflow_service,
                    ):
                        continue
                    start_workflow_ipc_dispatch(
                        workflow_service,
                        raw,
                        ws.send_json,
                        _workflow_ipc_background_tasks,
                    )

            elif msg_type == "provider_test_connection":
                # P2-1-S3: SettingsPanel「测试连接」button. The candidate
                # credentials travel through the already-authenticated control
                # channel; nothing is persisted here — the UI saves via the
                # Tauri `set_cloud_api_key` command only on success.
                from provider_test_connection import handle_provider_test_connection

                await handle_provider_test_connection(ws, raw.get("payload", {}) or {})

            elif msg_type == "supervisor_user_choice":
                # P5-S2/S3: user clicked a button on the supervisor bubble.
                # P5-S1 D fix: also act on the choice — "Continue/重试/let it"
                # triggers a follow-up that consumes the queued hint;
                # "Cancel/中断/stop" cancels in-flight + clears queue.
                _payload = raw.get("payload", {}) or {}
                _target_sid = _payload.get("session_id") or session_id
                _alert_id = str(_payload.get("alert_id") or "")
                _btn_idx = int(_payload.get("button_index") or 0)
                _btn_text = str(_payload.get("button_text") or "")
                logger.info(
                    "supervisor_user_choice sid=%s alert=%s button_idx=%d text=%s",
                    _target_sid,
                    _alert_id,
                    _btn_idx,
                    _btn_text,
                )
                _sdb_for_choice = service_context.get("session_db")
                if _sdb_for_choice is not None:
                    try:
                        await _sdb_for_choice.append_supervisor_hint(
                            session_id=_target_sid,
                            alert_id=_alert_id,
                            hint_text=_btn_text,
                            action="user_choice",
                            severity="green",
                            user_button=_btn_text,
                        )
                    except Exception as _exc:
                        logger.debug("supervisor_user_choice_audit_failed error=%s", _exc)

                # Heuristic intent matching by button text. We accept
                # both Chinese and English labels since the LLM picks
                # them dynamically. Stop/cancel takes priority over
                # continue if both keywords appear.
                _btn_lower = _btn_text.lower().strip()
                _is_stop = any(k in _btn_text for k in ("中断", "停止", "取消", "终止")) or any(
                    k in _btn_lower for k in ("cancel", "stop", "abort", "interrupt")
                )
                _is_continue = any(
                    k in _btn_text for k in ("继续", "重试", "再试", "让它", "再等")
                ) or any(k in _btn_lower for k in ("continue", "retry", "let", "wait"))

                if _is_stop:
                    # Cancel any in-flight task + clear the nudge queue
                    await _cancel_product_harness_run(
                        _target_sid,
                        str(_payload.get("run_id") or "").strip(),
                        reason="supervisor_user_choice",
                    )
                    _nq_for_stop = service_context.get("nudge_queue")
                    if _nq_for_stop is not None:
                        try:
                            await _nq_for_stop.clear(_target_sid)
                        except Exception:
                            pass
                elif _is_continue:
                    try:
                        _launch_product_harness_chat(ws, "<<supervisor_followup>>", _target_sid)
                        logger.info(
                            "supervisor_user_choice_followup_scheduled sid=%s",
                            _target_sid,
                        )
                    except Exception as _ex_fu:
                        logger.warning(
                            "supervisor_user_choice_followup_failed sid=%s error=%s",
                            _target_sid,
                            _ex_fu,
                        )

            elif msg_type == "supervisor_toggle":
                # P5-S2: enable / disable the supervisor at runtime.
                # When disabling: cancel watchdog, clear queues, drop activity.
                # When enabling: rebuild watchdog if not present (best-effort —
                # full restart is recommended for reliability).
                _payload = raw.get("payload", {}) or {}
                _enabled = bool(_payload.get("enabled", True))
                _persisted = _persist_supervisor_enabled(_enabled)
                _wd_inst = service_context.get("watchdog")
                if _enabled:
                    if _wd_inst is None or not _wd_inst.is_running():
                        logger.info("supervisor_toggle_enable_requested (restart_recommended)")
                    else:
                        _wd_inst.enable()
                        logger.info("supervisor_toggle_enabled")
                else:
                    if _wd_inst is not None:
                        _wd_inst.disable()
                    _nq_inst = service_context.get("nudge_queue")
                    if _nq_inst is not None:
                        try:
                            await _nq_inst.clear()
                        except Exception:
                            pass
                    logger.info("supervisor_toggle_disabled")
                await ws.send_json(
                    {
                        "type": "supervisor_toggle_ack",
                        "payload": {"enabled": _enabled, "persisted": _persisted},
                    }
                )

            elif msg_type == "__debug_force_error_pending":
                # P5-S1 D — force a session into error_pending so the
                # next watchdog tick triggers supervisor. Used to test
                # fallback alert when supervisor LLM also fails.
                _payload = raw.get("payload", {}) or {}
                target_sid = _payload.get("session_id") or ""
                _sa = service_context.get("session_activity")
                if _sa is not None and target_sid:
                    await _sa.bump(target_sid, event_type="error", snippet="forced for E2E test")
                    await _sa.mark_error_pending(target_sid)
                    # Also reset watchdog dedup so next scan fires
                    _wd = service_context.get("watchdog")
                    if _wd is not None:
                        _wd.reset_dedup(target_sid)
                    await ws.send_json(
                        {
                            "type": "__debug_force_error_pending_ack",
                            "payload": {"sid": target_sid, "ok": True},
                        }
                    )

            elif msg_type == "__debug_dump_session_activity":
                # P5-S1 D — dump current session_activity store so we
                # can verify error_pending got set + watchdog ran.
                _sa = service_context.get("session_activity")
                _wd = service_context.get("watchdog")
                if _sa is not None:
                    _all = await _sa.snapshot_all()
                else:
                    _all = {}
                _wd_state = {
                    "running": _wd.is_running() if _wd else False,
                    "last_scans": dict(_wd._last_scan_ts) if _wd else {},
                }
                await ws.send_json(
                    {
                        "type": "__debug_dump_response",
                        "payload": {"session_activity": _all, "watchdog": _wd_state},
                    }
                )

            elif msg_type == "__debug_inject_supervisor_alert":
                # P5-S1 D — Debug IPC for visual / E2E testing. Already
                # protected by SHARED_SECRET (the ws connection itself
                # requires it), so we don't double-gate on DEV_MODE.
                # Single-user desktop-pet threat model: anyone with the
                # secret already has full backend access.
                _payload = raw.get("payload", {}) or {}
                # Fan out to every control WS, identical path to the
                # real supervisor's broadcast.
                msg = {"type": "supervisor_alert", "payload": _payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug(
                            "debug_supervisor_alert_send_failed sid=%s error=%s",
                            _sid_key,
                            _bex,
                        )
                logger.info(
                    "__debug_inject_supervisor_alert dispatched payload=%s",
                    _payload,
                )
                await ws.send_json(
                    {
                        "type": "__debug_inject_ack",
                        "payload": {"ok": True},
                    }
                )

            else:
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": f"unknown type: {msg_type}"},
                    }
                )

    except WebSocketDisconnect:
        _cancel_harness_inspector_tasks(ws)
        # P4-S20: only clear the dict entry if it's still pointing at
        # OUR ws — a later connection may have replaced us already.
        if _control_connections.get(connection_registry_key) is ws:
            _control_connections.pop(connection_registry_key, None)
        _chat_peer_groups.pop(connection_registry_key, None)
        logger.info("control channel disconnected", session_id=session_id)


app.include_router(voice_router)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(
        app,
        host=config.backend.host,
        port=config.backend.port,
        log_level=config.backend.log_level.lower(),
    )


if __name__ == "__main__":
    main()
