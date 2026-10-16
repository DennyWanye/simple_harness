# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction, providers, shared settings, and the aliased schema and inventory methods."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall

from ..capabilities import ToolCapabilityScopeStore, ToolExecutionContext, ToolPolicySnapshot
from .support import _WRITE_PERMISSION_CATEGORIES, ToolSpec

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.tools.registry")


class ToolRegistryCore:
    """Construction, providers, shared settings, and the aliased schema and inventory methods."""

    if TYPE_CHECKING:

        async def _build_circuit_open_envelope(
            self, name: str, session_id: str, spec: ToolSpec
        ) -> dict[str, Any]: ...
        def _normalize_result(self, spec: ToolSpec, raw: Any) -> NormalizedToolOutcome: ...
        def _retire_spec_if_leased_locked(self, spec: ToolSpec) -> None: ...
        async def execute_tool(
            self,
            name: str,
            params: dict[str, Any],
            session_id: str,
            task_id: str = ...,
            *,
            execution_context: ToolExecutionContext | None = ...,
        ) -> dict[str, Any]: ...
        def is_concurrency_safe(self, tool: str | PreparedToolCall) -> bool: ...

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._retired_specs: dict[str, ToolSpec] = {}
        self._snapshot_spec_leases: dict[tuple[str, str], frozenset[str]] = {}
        self._lock = threading.RLock()
        self._catalog_revision = 0
        # The durable GrowthAuthorityRouter owns when this value advances.
        # Registry only provides the atomic, manifest-checked projection.
        self._authority_phase = "legacy"
        self._capability_scope_store: ToolCapabilityScopeStore | None = None
        self._dynamic_capability_admission_provider: (
            Callable[[str, ToolExecutionContext, ToolSpec], bool] | None
        ) = None
        self._context_os_enabled_provider: Callable[[], bool] = lambda: False
        self._context_os_e2e_hooks: Any | None = None
        self._mcp_catalog_stale_callback: Callable[[str], None] | None = None
        # P4-S20: optional permission gate. When set, ``execute_tool``
        # awaits ``gate.check(...)`` before running the handler. Tests
        # and legacy ``dispatch()`` paths leave it unset (no gating).
        self._gate = None  # type: Optional[Any]  # PermissionGate
        # P4-S22: per-session tool-arg context. ``execute_tool`` merges
        # this dict into the LLM-supplied params before invoking the
        # handler, so tools like ``glob`` / ``grep`` can read
        # ``_project_root`` without the LLM having to repeat it every
        # call. Keys conventionally start with underscore so they don't
        # collide with LLM-supplied ones.
        self._session_context: dict[str, dict[str, Any]] = {}
        # P5-S2 Phase 3: optional circuit breaker. When set, every
        # ``execute_tool`` call first asks the breaker whether the tool
        # is currently allowed for this session, and records the outcome
        # afterwards. Defaults to None for backward compat with tests
        # and legacy callers that don't want breaker semantics.
        self._breaker = None  # type: Optional[Any]  # ToolCircuitBreaker
        # WI-T1.1 (last-mile): optional ToolsConfig provider for D1 信封包装。
        # 默认 None → execute_tool 不加 artifacts 键（BC + 字节级一致硬保证）。
        # 启动期 main.py 调 set_tools_config_provider(lambda: cfg.tools)。
        self._tools_config_provider: Callable[[], Any] | None = None
        # WI-T2.2 (last-mile P0 修): optional ReceiptStore provider 真正插电。
        # 默认 None → 不产 receipt（BC）；main.py 启动时按 cfg.verifier.emit_receipts
        # 决定是否构造 ReceiptStore 并注入。
        self._receipt_store_provider: Callable[[], Any] | None = None
        # WI-T2.3 session iteration 计数 (per session_id) — receipt.iteration 字段
        self._session_iteration: dict[str, int] = {}
        # WI-CC-2 (plan mode 物理只读): per-session 规划期只读开关。
        # main.py 在 plan-confirm 硬门挂起前置 True、用户点[执行]/取消/超时后置
        # False。为 True 期间 execute_tool 拦下所有 _WRITE_PERMISSION_CATEGORIES
        # 工具。默认空集 = 无 session 处于只读 = 字节级 BC（features.plan_read_only
        # OFF 时 main.py 永不置位，此集恒空）。
        self._plan_read_only_sessions: set[str] = set()
        # PreparedToolCall intentionally contains only durable JSON fields.
        # These caches retain same-process session/effect-policy context; a
        # resumed workflow supplies the same facts through its durable grant.
        self._prepared_session_ids: dict[tuple[str, str, str], str] = {}
        self._prepared_execution_metadata: dict[str, dict[str, Any]] = {}
        self._prepared_execution_outcomes: dict[str, NormalizedToolOutcome] = {}
        self._late_prepared_calls: dict[
            str,
            tuple[
                asyncio.Future[Any],
                ToolSpec,
                PreparedToolCall,
                ToolExecutionContext | None,
                str,
                bool,
            ],
        ] = {}
        self._prepared_ready_callback: Callable[[str], None] | None = None
        self._tool_completion_latch: Any | None = None

    def set_tool_completion_latch(self, latch: Any | None) -> None:
        self._tool_completion_latch = latch

    def _set_prepared_ready_callback(self, callback: Callable[[str], None] | None) -> None:
        self._prepared_ready_callback = callback

    def set_permission_gate(self, gate) -> None:  # type: ignore[no-untyped-def]
        """Wire a PermissionGate. Called once at backend startup."""
        self._gate = gate

    def set_tools_config_provider(self, provider) -> None:  # type: ignore[no-untyped-def]
        """WI-T1.1 wire a callable returning current ``ToolsConfig``.

        Provider 模式（不直传 cfg）允许 runtime 切换 flag 而无需重启 registry。
        provider 返回值需有 ``.last_mile.artifact_envelope`` 属性（ducktype）；
        返回 None 时按 BC 路径（不包装 envelope）。
        """
        self._tools_config_provider = provider

    def set_capability_scope_store(self, store: ToolCapabilityScopeStore | None) -> None:
        self._capability_scope_store = store

    def set_dynamic_capability_admission_provider(
        self,
        provider: Callable[[str, ToolExecutionContext, ToolSpec], bool] | None,
    ) -> None:
        """Wire the Run-local authority for post-start capability activation."""

        self._dynamic_capability_admission_provider = provider

    @property
    def capability_scope_store(self) -> ToolCapabilityScopeStore | None:
        return self._capability_scope_store

    def set_context_os_enabled_provider(self, provider: Callable[[], bool]) -> None:
        self._context_os_enabled_provider = provider

    def set_context_os_e2e_hooks(self, hooks: Any | None) -> None:
        self._context_os_e2e_hooks = hooks

    def set_mcp_catalog_stale_callback(self, callback: Callable[[str], None] | None) -> None:
        self._mcp_catalog_stale_callback = callback

    def invalidate_mcp_catalog_sources(self, sources: Iterable[str]) -> None:
        """Atomically invalidate whole MCP server catalogs and reconnect them."""
        source_set = {str(source) for source in sources if str(source).startswith("mcp:")}
        if not source_set:
            return
        with self._lock:
            stale_names = [name for name, spec in self._tools.items() if spec.source in source_set]
            for name in stale_names:
                removed = self._tools.pop(name, None)
                if removed is not None:
                    self._retire_spec_if_leased_locked(removed)
            if stale_names:
                self._catalog_revision += 1
        callback = self._mcp_catalog_stale_callback
        if callback is not None:
            for source in sorted(source_set):
                try:
                    callback(source)
                except Exception:
                    logger.warning("mcp_catalog_stale_callback_failed", exc_info=True)

    @property
    def context_os_e2e_hooks(self) -> Any | None:
        return self._context_os_e2e_hooks

    def read_policy_snapshot(self, *, strict: bool) -> ToolPolicySnapshot:
        if self._tools_config_provider is None:
            return ToolPolicySnapshot.from_config(None)
        try:
            return ToolPolicySnapshot.from_config(self._tools_config_provider())
        except Exception:
            if strict:
                raise RuntimeError("tool_policy_unavailable")
            logger.warning("tools_config_provider read failed", exc_info=True)
            return ToolPolicySnapshot.from_config(None)

    def set_receipt_store_provider(self, provider) -> None:  # type: ignore[no-untyped-def]
        """WI-T2.2 P0 修：wire a callable returning current ``ReceiptStore`` (or None).

        与 set_tools_config_provider 同模式：provider 返回 None → BC 路径
        不产 receipt；ReceiptStore 已构造时每次 execute_tool 都 emit。
        """
        self._receipt_store_provider = provider

    def set_circuit_breaker(self, breaker) -> None:  # type: ignore[no-untyped-def]
        """P5-S2 Phase 3: wire a :class:`agent.circuit_breaker.ToolCircuitBreaker`.

        After this is set, ``execute_tool`` consults it before invoking
        each handler and records every outcome. Pass ``None`` to detach
        (mostly useful in tests).
        """
        self._breaker = breaker

    def set_session_context(
        self,
        session_id: str,
        context: dict[str, Any] | None,
    ) -> None:
        """P4-S22 — bind extra args injected into every tool call for
        this session. Pass None to clear. Typical use: chat handler
        sets ``{"_project_root": str(project_root)}`` when entering
        Code mode; clears it on exit.
        """
        if context is None:
            self._session_context.pop(session_id, None)
        else:
            self._session_context[session_id] = dict(context)

    def set_plan_read_only(self, session_id: str, enabled: bool) -> None:
        """WI-CC-2 — toggle 规划期物理只读 for one session.

        ``enabled=True`` 期间，``execute_tool`` 对该 session 上任何
        ``permission_category`` 属 ``_WRITE_PERMISSION_CATEGORIES`` 的工具
        返回「规划期只读」deny（不执行 handler）；只读工具照常放行。
        main.py 在 plan-confirm 硬门挂起前置 True、go/cancel/timeout 后置 False。
        幂等：重复 True/False 无副作用。
        """
        if enabled:
            self._plan_read_only_sessions.add(session_id)
        else:
            self._plan_read_only_sessions.discard(session_id)

    def is_plan_read_only(self, session_id: str) -> bool:
        """True iff ``session_id`` is currently in 规划期物理只读 mode."""
        return session_id in self._plan_read_only_sessions

    # ------------------------------------------------------------------
    # P4-S20 v2: tool_use protocol schema generation + gated execution
    # ------------------------------------------------------------------
    def to_openai_schema(
        self,
        names: list[str] | None = None,
        filter_categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """OpenAI function-calling schema list.

        Equivalent to ``schemas()`` but with v2 filters:
        * ``names`` — explicit allowlist by tool name
        * ``filter_categories`` — restrict to tools whose
          ``permission_category`` is in this list (used by safe-mode)
        """
        with self._lock:
            specs = list(self._tools.values())
        out: list[dict[str, Any]] = []
        name_set = set(names) if names is not None else None
        cat_set = set(filter_categories) if filter_categories is not None else None
        for spec in specs:
            if not spec.env_satisfied():
                continue
            if name_set is not None and spec.name not in name_set:
                continue
            if cat_set is not None and spec.permission_category not in cat_set:
                continue
            out.append({"type": "function", "function": dict(spec.schema)})
        return out

    # Ollama uses the OpenAI-compatible shape; alias for clarity.
    to_ollama_schema = to_openai_schema

    def tool_inventory(self, names: Sequence[str] | None = None) -> list[Any]:
        """Return one versioned workflow inventory row per selected ToolSpec."""

        # 按需导入：测试替身（deskpet.workflows.contracts）
        from deskpet.workflows.contracts import ToolAccess, ToolInventoryEntry

        selected = set(names) if names is not None else None
        with self._lock:
            specs = list(self._tools.values())
        entries: list[Any] = []
        for spec in sorted(specs, key=lambda item: item.name):
            if selected is not None and spec.name not in selected:
                continue
            access = (
                ToolAccess.WRITE
                if spec.permission_category in _WRITE_PERMISSION_CATEGORIES or spec.dangerous
                else ToolAccess.READ
            )
            entries.append(
                ToolInventoryEntry(
                    name=spec.name,
                    access=access,
                    spec_version=spec.spec_version,
                    schema_hash=spec.schema_hash,
                    effect_policy=spec.effect_policy,
                    outcome_parser_id=spec.outcome_parser_id,
                    outcome_parser_version=spec.outcome_parser_version,
                    outcome_parser_hash=spec.outcome_parser_hash,
                )
            )
        if selected is not None:
            missing = sorted(selected - {entry.name for entry in entries})
            if missing:
                raise KeyError(f"unknown tools in inventory: {', '.join(missing)}")
        return entries

    workflow_tool_inventory = tool_inventory
