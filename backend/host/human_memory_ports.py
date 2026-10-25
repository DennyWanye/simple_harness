"""人类记忆宿主端口与终端操作审计激活、未完成提供方对账"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import paths as _paths
from deskpet.execution.closure_request_guard import (
    ClosurePhysicalRequestGuard,
    ClosureRequestAuthority,
)
from deskpet.execution.foreground_queue import EffectBoundary, ForegroundQueueStore
from deskpet.execution.foreground_runtime import (
    ForegroundRuntimeExecutionAuthority,
    SqliteSdkTerminalObserver,
)
from deskpet.execution.foreground_runtime_ports import (
    ProductForegroundProviderPort,
    ProductForegroundToolPort,
)
from deskpet.execution.primary_context import (
    ForegroundConversationEntrypoint,
    PrimaryForegroundContextPort,
)
from deskpet.execution.semantic_closure import ClosureFallback
from deskpet.memory.recovery_fence import build_recovery_lifecycle_port
from deskpet.memory.schema import StartupCompositionMode
from deskpet.operation_audit.composition import compose_terminal_audit
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    DefaultDenySnapshotRedactor,
)
from deskpet.sdk_adapters.post_turn_invoker import RunBoundInvoker
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.task_scope.runtime_binding_authority import WorkspaceBindingRuntimeAuthority
from host.chat_delivery import _broadcast_control
from host.runtime import logger, service_context
from host.sdk_execution import _provider_endpoint_identity_for_binding
from host.sdk_services import (
    _ensure_evidence_ingress,
    _ensure_foreground_effect_gate,
    _ensure_provider_reconciliation,
    _ensure_run_fault_memo,
    _ensure_task_scope_update_service,
    _primary_history_policy,
)
from host.state import host_state
from host.stores_bootstrap import _state_db_path


async def _publish_foreground_context_snapshot(snapshot: Any) -> None:
    """主对话运行冻结的上下文快照脱敏后存为公开快照，供用量明细读取。"""
    session_db = service_context.get("session_db")
    if session_db is None:
        return
    await session_db.put_sdk_context_public_snapshot(DefaultDenySnapshotRedactor().redact(snapshot))


async def _activate_terminal_operation_audit():
    """Default-on audit of actual foreground terminals; no Provider work here."""

    consumer = service_context.get("terminal_operation_audit")
    if consumer is None:
        try:
            consumer = await compose_terminal_audit(
                _state_db_path,
                stack_getter=lambda: host_state.sdk_runtime_stack,
                subject="deskpet-local-owner-v1",
            )
        except (OSError, sqlite3.Error):
            service_context.register("terminal_operation_audit_status", "storage_unavailable")
            logger.warning("terminal_operation_audit_storage_unavailable")
            return None
        service_context.register("terminal_operation_audit", consumer)
        service_context.register("terminal_operation_audit_status", "started")
    return consumer


async def _activate_human_memory_host_ports(
    startup_epoch, *, history_reader=None, configured_workspace_root=None
) -> None:  # type: ignore[no-untyped-def]
    """Publish fresh-HUMAN authorities only after their dependencies are ready."""

    names = (
        "human_memory_binding_append_authority",
        "human_memory_recovery_lifecycle",
        "human_memory_foreground_scheduler_wake",
        "human_memory_foreground_runtime_execution_authority",
    )
    if startup_epoch.composition_mode is not StartupCompositionMode.HUMAN:
        for name in names:
            service_context.register(name, None)
        return
    policy = service_context.get("capability_store")
    if policy is None:
        raise RuntimeError("Human Memory binding requires authorization policy")

    foreground = ForegroundQueueStore(_state_db_path)
    binding = WorkspaceBindingRuntimeAuthority(
        _state_db_path,
        subject="deskpet-local-owner-v1",
        foreground=foreground,
        policy=policy,
        configured_workspace_root=configured_workspace_root,
        display_invalidation=service_context.get("memory_display_invalidation"),
    )
    recovery = build_recovery_lifecycle_port(
        db_path=_state_db_path,
        artifact_dir=Path(_paths.user_data_dir()) / "human-memory-emergency-exports",
    )
    service_context.register("human_memory_binding_append_authority", binding)
    service_context.register("human_memory_recovery_lifecycle", recovery)
    if any(
        item is None
        for item in (
            host_state.sdk_ingress,
            host_state.sdk_runtime_stack,
            host_state.provider_registry,
            host_state.sdk_provider_binding_resolver,
            host_state.sdk_runtime_catalog,
            host_state.sdk_runtime_tool_inventory,
            host_state.sdk_tool_authority_registry,
        )
    ):
        # Fresh installs without a configured Provider keep the Host mutation
        # authorities available, but cannot manufacture an Agent authority.
        service_context.register("human_memory_foreground_scheduler_wake", None)
        service_context.register("human_memory_foreground_runtime_execution_authority", None)
        logger.warning(
            "human_memory_foreground_runtime_unavailable",
            reason="sdk_runtime_authority_unavailable",
        )
        return

    class _AuditSink:
        def record(self, event: str, payload: Mapping[str, object]) -> None:
            logger.info(event, **dict(payload))

    # lease-fenced semantic-closure fallback.  The Provider adapter
    # is rebuilt from the durable Run binding through the same resolver the SDK
    # Run used (no new client); the handler is the same one behind the
    # `task_scope_update` Tool; run facts come from the SDK runtime stack.

    _closure_service = _ensure_task_scope_update_service()
    if host_state.sdk_provider_binding_resolver is None:
        raise RuntimeError(
            "sdk_context_authority_composition_missing:sdk_provider_binding_resolver"
        )

    class _RuntimeLeaseFence:
        """Per-Run lease fence resolved lazily from the driver's (owner, generation)."""

        def __init__(self, owner_id: str) -> None:
            self.owner_id = owner_id
            self.host_run_id = ""
            self.sdk_run_id = ""
            self.generation = 0

        def bind(self, *, host_run_id: str, sdk_run_id: str, generation: int) -> None:
            self.host_run_id = host_run_id
            self.sdk_run_id = sdk_run_id
            self.generation = int(generation)

        async def reserve_attempt(self, row, members):  # type: ignore[no-untyped-def]
            await foreground.reserve_post_turn_attempt(
                host_run_id=self.host_run_id,
                sdk_run_id=self.sdk_run_id,
                owner_id=self.owner_id,
                generation=self.generation,
                attempt=row,
                members=members,
            )

        async def revalidate(self) -> None:

            await foreground.authorize_effect(
                host_run_id=self.host_run_id,
                sdk_run_id=self.sdk_run_id,
                owner_id=self.owner_id,
                generation=self.generation,
                boundary=EffectBoundary.CLOSURE,
            )

        async def reserve_attempt_with_input(self, row, members, observer):
            await foreground.reserve_post_turn_attempt(
                host_run_id=self.host_run_id,
                sdk_run_id=self.sdk_run_id,
                owner_id=self.owner_id,
                generation=self.generation,
                attempt=row,
                members=members,
                input_observer_tx=observer,
            )

    _runtime_owner_id = f"deskpet-foreground:{os.getpid()}:{uuid.uuid4().hex}"
    _closure_fence = _RuntimeLeaseFence(_runtime_owner_id)

    _closure_request_authority = ClosureRequestAuthority(
        _state_db_path,
        stack_getter=lambda: host_state.sdk_runtime_stack,
        policy_factory=_primary_history_policy,
    )

    def _closure_adapter(record):  # type: ignore[no-untyped-def]
        binding = SdkRunBindingV1.from_record(record)
        return host_state.sdk_provider_binding_resolver.build_authority(
            binding,
            request_guard=ClosurePhysicalRequestGuard(_closure_request_authority, binding=binding),
        ).provider

    class _BoundClosureFallback:
        """Binds the fence to the (host_run, sdk_run, generation) of each settle call."""

        def __init__(self) -> None:
            self._inner = ClosureFallback(
                _state_db_path,
                invoker=RunBoundInvoker(
                    _state_db_path, fence=_closure_fence, adapter_factory=_closure_adapter
                ),
                service=_closure_service,
                run_facts_reader=host_state.sdk_runtime_stack,
                request_authority=_closure_request_authority,
            )

        async def settle(self, *, host_run_id, sdk_run_id, owner_id, generation, terminal_state):  # type: ignore[no-untyped-def]
            if owner_id != _closure_fence.owner_id:
                raise RuntimeError("foreground_runtime_closure_owner_mismatch")
            _closure_fence.bind(
                host_run_id=host_run_id, sdk_run_id=sdk_run_id, generation=generation
            )
            return await self._inner.settle(
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                owner_id=owner_id,
                generation=generation,
                terminal_state=terminal_state,
            )

    def _foreground_route_ledger():  # type: ignore[no-untyped-def]
        """前台首轮 HOST_INITIAL 路由回执的落账目标（与 chat 路径同一实现）。"""

        return ContextRouteLedgerStore(_state_db_path, evidence_ingress=_ensure_evidence_ingress())

    _foreground_conversation_entrypoint = ForegroundConversationEntrypoint(
        session_store=service_context.get("session_db"),
        identity_authority=service_context.get("memory_identity_authority"),
        context_sources=service_context.get("sdk_context_source_repository"),
    )

    async def _primary_state_changed() -> None:
        await _broadcast_control({"type": "human_memory_changed", "payload": {}})

    _terminal_audit = await _activate_terminal_operation_audit()

    # 2026-09-10：prospective（未来时点提醒）随认知记忆 SDK 移除，终态提交
    # 不再有 occurrence hook 要跑。
    runtime = ForegroundRuntimeExecutionAuthority(
        store=foreground,
        subject="deskpet-local-owner-v1",
        owner_id=_runtime_owner_id,
        closure_fallback=_BoundClosureFallback(),
        ingress=host_state.sdk_ingress,
        context=PrimaryForegroundContextPort(
            _state_db_path,
            subject="deskpet-local-owner-v1",
            **({"history_reader": history_reader} if history_reader is not None else {}),
            # 受信业务时钟：原先取 ``HumanMemoryV7Runtime.semantic_clock``，
            # 那本身就是组装时注入的 ``time.time``（见删除前的
            # ``compose_human_memory_runtime(clock=clock)``，``clock`` 默认
            # ``time.time``）。记忆运行时移除后直接用同一个物理时钟。
            clock=time.time,
            policy=_primary_history_policy("deskpet-local-owner-v1"),
            stack_getter=lambda: host_state.sdk_runtime_stack,
            route_ledger=_foreground_route_ledger(),
            settled_run_reader=lambda run_id, **kwargs: (
                host_state.sdk_runtime_stack.read_settled_primary_run(run_id, **kwargs)
            ),
            public_snapshot_sink=_publish_foreground_context_snapshot,
        ),
        provider=ProductForegroundProviderPort(
            host_state.provider_registry,
            host_state.sdk_provider_binding_resolver,
        ),
        tools=ProductForegroundToolPort(
            _state_db_path,
            catalog=host_state.sdk_runtime_catalog,
            inventory=host_state.sdk_runtime_tool_inventory,
            registry=host_state.sdk_tool_authority_registry,
        ),
        terminal_observer=SqliteSdkTerminalObserver(
            str(_state_db_path),
            host_state.sdk_ingress,
            host_state.sdk_runtime_stack,
            run_fault_memo=_ensure_run_fault_memo(),
        ),
        audit_sink=_AuditSink(),
        effect_gate=_ensure_foreground_effect_gate(),
        run_binding_reader=lambda run_id: host_state.sdk_runtime_stack.read_closure_run_facts(
            run_id
        ),
        endpoint_identity_resolver=_provider_endpoint_identity_for_binding,
        conversation_entrypoint=_foreground_conversation_entrypoint,
        state_changed=_primary_state_changed,
        terminal_audit_wake=_terminal_audit.wake if _terminal_audit is not None else None,
        provider_reconciliation=_ensure_provider_reconciliation(),
        provider_reconcile=_reconcile_incomplete_providers,
    )
    service_context.register("human_memory_foreground_scheduler_wake", runtime)
    service_context.register("human_memory_foreground_runtime_execution_authority", runtime)


async def _reconcile_incomplete_providers(sdk_run_id: str) -> int:
    """前台运行时在 SDK Run waiting 时调用：只跑 Host provider reconcile 步骤。

    走 ``reconcile_for_run``（在途闸门 + Run 作用域），不是启动路径的全量 ``reconcile()``：
    运行期并发 Run 共享同一个 coordinator，全量调和会把别人在飞行中的 ``HANDED_OFF``
    判成 UNKNOWN → 正常响应被丢弃 + 重复发送。
    """
    if host_state.runtime_reconciliation is None:
        return 0
    return await host_state.runtime_reconciliation.reconcile_for_run(sdk_run_id)
