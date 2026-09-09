# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit product Tool inventory projected into the SDK public registry."""

from __future__ import annotations

import contextvars
import copy
import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from simple_harness import CallId, JsonValue, thaw_json
from simple_harness.tools import (
    FunctionTool,
    ToolCall,
    ToolCallState,
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from simple_harness.tools.executor import EffectExecution, EffectExecutor

from deskpet.sdk_adapters.effect_gate import EffectGateRejected
from deskpet.security.sensitive_text import redact_sensitive_text

logger = logging.getLogger(__name__)

# 事件 X：`ProductToolsAdapter._run_calls` 的兜底上限（见 _trim_run_calls）。
# 前台是 FIFO 单 Run，这个值只对不触发 Tool authority 终态的历史入口起作用。
_MAX_TRACKED_RUNS = 64

PRODUCT_TOOL_NAMES: tuple[str, ...] = tuple(
    ["agent", "agent_parallel", "agent_reach_doctor", "agent_reach_read", "app_discover", "app_launch", "await_subagents", "capability_build", "capability_repair", "context_page_in", "desktop_create_file", "doc_create", "doc_edit", "doc_read", "download_file", "edit_file", "excel_create", "external_action_wait", "fetch_tool_result", "file_glob", "file_grep", "file_organize", "file_read", "file_write", "generate_image", "glob", "gold_price_lookup", "grep", "image_ocr", "list_directory", "context_route", "prospective_ack", "procedure_use", "procedure_discover", "memory_forget", "memory_read", "memory_recall", "memory_search", "memory_write", "move_file", "office_pick_file", "pdf_export", "ppt_create", "process_list", "process_start", "process_stop", "process_wait", "project_directory_select", "project_group_send", "read_file", "register_artifacts", "run_browser_task", "run_shell", "scrapling_fetch", "screen_capture", "screen_click", "screen_key", "screen_move", "screen_scroll", "screen_type", "skill_invoke", "spawn_subagents", "spawn_team", "skill_install", "task_scope_search", "task_scope_update", "todo_complete", "todo_write", "tool_activate", "tool_describe", "tool_search", "web_crawl", "web_extract_article", "web_fetch", "web_read_sitemap", "web_search", "window_capture", "window_focus", "window_key", "window_list", "workspace_prepare", "workspace_recall", "write_file"]
)

# Host-composed administrative tools are registered only after their durable
# authorization/runtime services exist.  The static catalog can therefore be
# built without them for conformance and recovery, while production wiring is
# still checked against PRODUCT_TOOL_NAMES once the registration is appended.
HOST_COMPOSED_TOOL_NAMES = frozenset(
    {"prospective_ack", "procedure_use", "procedure_discover", "skill_install", "context_route", "task_scope_search", "task_scope_update"}
)

DispatchKind = Literal["sync", "async", "context", "staged", "control", "provider"]
ProjectlessAdmission = Literal["safe", "requires_project"]
ProductHandler = Callable[[Mapping[str, JsonValue], ToolContext], Any]


# Projectless Sessions are ordinary chat.  Keep only tools whose physical
# handlers do not discover, read, mutate, launch, delegate into, or prepare a
# local workspace.  Every other built-in is fail-closed by the dataclass
# default below; dynamic MCP tools use the same default.
PROJECTLESS_SAFE_TOOL_NAMES = frozenset(
    {
        "context_page_in",
        "context_route",
        "task_scope_search",
        # S5b Task 3: semantic closure never touches the workspace.
        "task_scope_update",
        "procedure_use",
        "procedure_discover",
        "gold_price_lookup",
        "memory_forget",
        "memory_read",
        "memory_recall",
        "memory_search",
        "memory_write",
        "todo_complete",
        "todo_write",
        "tool_activate",
        "tool_describe",
        "tool_search",
        "web_crawl",
        "web_extract_article",
        "web_fetch",
        "web_read_sitemap",
        "web_search",
    }
)

# The process-wide filesystem MCP is rooted at the application's userdata
# workspace.  It cannot be rebound to an immutable per-Session execution root,
# so exposing it to a project-bound Run would create a second, unrelated file
# authority.  Project Runs use the product file/terminal tools whose handlers
# consume the frozen Run workspace instead.
PROJECT_BOUND_UNSCOPED_MCP_SOURCES = frozenset({"mcp:filesystem"})


@dataclass(frozen=True, slots=True)
class ProductToolRegistration:
    name: str
    description: str
    input_schema: Mapping[str, JsonValue]
    handler: ProductHandler
    dispatch_kind: DispatchKind
    permission_category: str
    metadata: Mapping[str, JsonValue]
    projectless_admission: ProjectlessAdmission = "requires_project"

    def __post_init__(self) -> None:
        if self.name not in PRODUCT_TOOL_NAMES:
            raise ValueError(f"unknown product Tool identity: {self.name}")
        if self.dispatch_kind not in {
            "sync", "async", "context", "staged", "control", "provider"
        }:
            raise ValueError("unsupported product Tool dispatch kind")
        if not self.permission_category.strip():
            raise ValueError("permission_category is required")
        if not callable(self.handler):
            raise TypeError("handler must be callable")
        if self.projectless_admission not in {"safe", "requires_project"}:
            raise ValueError("projectless_admission must be total and fail-closed")
        if not self.metadata.get("source") or not self.metadata.get("version"):
            raise ValueError("Tool source and version metadata are required")


@dataclass(frozen=True, slots=True)
class ProductToolInventoryEntry:
    name: str
    dispatch_kind: DispatchKind
    permission_category: str
    source: str
    version: str
    execution_identity: str
    projectless_admission: ProjectlessAdmission = "requires_project"
    availability_reason: str | None = None

    @property
    def executable_eligible(self) -> bool:
        return self.availability_reason is None


_current_call_id: contextvars.ContextVar[CallId | None] = contextvars.ContextVar(
    "product_sdk_tool_call_id", default=None
)
_current_tool_context: contextvars.ContextVar[ToolContext | None] = (
    contextvars.ContextVar("product_sdk_tool_context", default=None)
)
_validation_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "product_sdk_tool_validation_run_id", default=None
)


class SdkToolExecutorCatalogUnavailable(RuntimeError):
    code = "sdk_tool_executor_catalog_unavailable"

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"{self.code}:{tool_name}")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _product_tool_execution_identity(
    registration: ProductToolRegistration,
    tool: FunctionTool,
) -> str:
    """Hash only the stable executable contract of one Tool registration.

    Global manifest/catalog digests are deliberately excluded: changing an
    unrelated Tool must not invalidate a WAITING Run that never leased it.
    """

    metadata = registration.metadata
    source = str(metadata["source"])
    version = str(metadata["version"])
    handler_identity = str(
        metadata.get("handler_id")
        or metadata.get("stable_handler_id")
        or f"{source}:{registration.name}:{version}"
    )
    return _canonical_sha256(
        {
            "name": registration.name,
            "source": source,
            "version": version,
            "dispatch_kind": registration.dispatch_kind,
            "permission_category": registration.permission_category,
            "handler_identity": handler_identity,
            "description": tool.spec.description,
            "input_schema": thaw_json(tool.spec.input_schema),
        }
    )


_foreground_invocation_origin = contextvars.ContextVar("foreground_invocation_origin", default=None)


def active_product_foreground_origin():
    """Exact admission captured before dispatch; never a lookup of latest Run."""
    return _foreground_invocation_origin.get()


def active_product_tool_call_id() -> CallId:
    """Return the SDK-owned identity of the currently dispatched Tool call."""

    call_id = _current_call_id.get()
    if call_id is None:
        raise RuntimeError("product Tool invoked outside SDK ToolRegistry")
    return call_id


def active_product_tool_context() -> ToolContext:
    """Return the current SDK Tool context without any global fallback."""

    context = _current_tool_context.get()
    if context is None:
        raise RuntimeError("product Tool invoked outside SDK ToolRegistry")
    return context


class ProductToolsAdapter(ToolRegistry):
    """SDK registry that exposes the current call identity to product wrappers."""

    def __init__(
        self,
        tools=(),
        *,
        execution_identities: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(tools)
        self._execution_identities = dict(execution_identities or {})
        self._run_authorities: object | None = None
        # 事件 X：SDK ``ToolRegistry._calls`` 只增不减（见 release_run_calls）。
        # 这里按 Run 记住本进程发过的 call id，Run 终态时据此归还已结算的本地
        # 认领。键是 SDK run id 字符串，值是该 Run 的 CallId 集合。
        self._run_calls: dict[str, set[CallId]] = {}

    def validate(self, call):
        """接住 schema 校验失败，转交产品包装层变成模型可见拒绝。

        冻结 SDK 在进入处理器之前校验参数，失败即整 Run ``driver_failed``。这里
        把失败记在 call 名下并照常返回 Tool；``_sdk_tool`` 的包装层会在调用真实
        处理器之前拦下它（处理器永远拿不到非法参数）。校验失败信息里可能带
        JSON path 与原因，属产品自己的 schema 描述，但仍按稳定码白名单收敛后
        才对模型可见。
        """

        from simple_harness.tools import MalformedToolArgumentsError

        try:
            return super().validate(call)
        except MalformedToolArgumentsError as exc:
            tool = self.get(call.name)
            _record_invalid_arguments(str(call.call_id), str(exc))
            logger.warning(
                "tool_arguments.invalid tool=%s", call.name
            )
            return tool

    def bind_run_authorities(self, authorities: object) -> None:
        if (
            self._run_authorities is not None
            and self._run_authorities is not authorities
        ):
            raise RuntimeError("product Tool authority registry is already bound")
        already_bound = self._run_authorities is authorities
        self._run_authorities = authorities
        if already_bound:
            return
        listen = getattr(authorities, "add_terminal_listener", None)
        if callable(listen):
            listen(lambda authority: self.release_run_calls(authority.run_id))

    def release_run_calls(self, run_id: object) -> int:
        """归还一个终态 Run 已结算的本地 Tool 认领，返回释放条数。

        事件 X（2026-09-09 后端内存增长）：冻结 SDK 的 ``ToolRegistry`` 在
        ``invoke`` 里把每次调用记进 ``_calls``，但只有 ``allow_confirmed_not_started``
        才会删；产品侧这个 registry 是每个 SDK runtime stack 一份的长生命周期
        对象，于是进程活多久、``_calls`` 就攒多久——每条 ``_CallRecord`` 还吊着
        已完成的 ``asyncio.Task``（协程帧、拷贝的 contextvars Context、
        ``ToolContext``/``TaskExecutionEnvelope``）和整份 ``ToolResult`` 载荷
        （tool_search 结果、分页内容、文件读取……）。原生旅程里这就是每回合几百
        MB 的 MALLOC_SMALL 增长。

        Run 到终态时（``SdkRunToolAuthorityRegistry.mark_terminal`` 的监听器）
        释放：此时该 Run 的终态回执已落库，本地认领不再有仲裁价值。仍在
        RUNNING 的记录一律不动——取消由 SDK 自己的 ``close_call`` 负责，
        ``allow_confirmed_not_started`` 也会再拦一道。确定性/回执/重放不受影响：
        ``_calls`` 完全是进程内状态，不参与任何持久化或指纹。
        """

        value = str(getattr(run_id, "value", run_id))
        call_ids = self._run_calls.pop(value, None)
        if not call_ids:
            return 0
        states = self.calls
        released = 0
        for call_id in call_ids:
            if states.get(call_id) in (None, ToolCallState.RUNNING):
                continue
            self.allow_confirmed_not_started(call_id)
            released += 1
        return released

    def _trim_run_calls(self, current_run_id: str) -> None:
        """终态监听器之外的兜底上限，覆盖不经 Tool authority 的历史入口。

        没有 Run authority 的旧聊天入口不会触发 ``mark_terminal``，那条路上
        ``_calls`` 依旧只增不减。这里按插入顺序保留最近 ``_MAX_TRACKED_RUNS``
        个 Run，更早的 Run 只归还其**已结算**的认领（RUNNING 一律不动），所以
        它既不会打断在飞的调用，也不会让任何入口无界增长。
        """

        while len(self._run_calls) > _MAX_TRACKED_RUNS:
            stale = next(iter(self._run_calls))
            if stale == current_run_id:  # 不淘汰正在进行的 Run
                return
            self.release_run_calls(stale)

    def register_dynamic(self, tool: FunctionTool, *, execution_identity: str) -> None:
        if not isinstance(tool, FunctionTool):
            raise TypeError("dynamic product Tool must be FunctionTool")
        identity = str(execution_identity).strip()
        if not identity:
            raise ValueError("dynamic product Tool execution identity is required")
        super().register(tool)
        self._execution_identities[tool.spec.name] = identity

    def _validate_execution_identity(self, name: str, run_id: str) -> None:
        resolve = getattr(self._run_authorities, "resolve", None)
        if not callable(resolve):
            raise SdkToolExecutorCatalogUnavailable(name)
        try:
            authority = resolve(run_id)
            frozen = authority.specs[name]
            current = super().get(name)
        except (KeyError, RuntimeError):
            raise SdkToolExecutorCatalogUnavailable(name) from None
        current_schema_hash = _canonical_sha256(
            thaw_json(current.spec.input_schema)
        )
        if (
            self._execution_identities.get(name) != frozen.execution_identity
            or current_schema_hash != frozen.schema_hash
            or current.spec.description != frozen.schema["description"]
        ):
            raise SdkToolExecutorCatalogUnavailable(name)

    def assert_workspace_current(self, run_id: object) -> None:
        resolve = getattr(self._run_authorities, "resolve", None)
        if not callable(resolve):
            raise RuntimeError("sdk_run_authority_unavailable")
        authority = resolve(run_id)
        check = getattr(authority, "assert_workspace_current", None)
        if not callable(check):
            raise RuntimeError("workspace_identity_check_unavailable")
        check()

    def get(self, name: str):
        run_id = _validation_run_id.get()
        if run_id is not None:
            self._validate_execution_identity(name, run_id)
        return super().get(name)

    async def invoke(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        accepted_result_call_id: CallId | None = None,
    ) -> ToolResult:
        validation_token = (
            _validation_run_id.set(context.run_id.value)
            if self._run_authorities is not None
            else None
        )
        if self._run_authorities is not None:
            resolve = getattr(self._run_authorities, "resolve", None)
            if not callable(resolve):
                raise SdkToolExecutorCatalogUnavailable(call.name)
            authority = resolve(context.run_id)
            if (
                str(getattr(authority, "run_id", "")) != context.run_id.value
                or str(getattr(authority, "request_id", ""))
                != context.request_id.value
            ):
                raise RuntimeError("sdk_tool_context_identity_mismatch")
        # 事件 X：记住 call → Run 归属，Run 终态时才知道该归还哪些本地认领。
        self._run_calls.setdefault(context.run_id.value, set()).add(call.call_id)
        self._trim_run_calls(context.run_id.value)
        token = _current_call_id.set(call.call_id)
        context_token = _current_tool_context.set(context)
        try:
            origin = _foreground_invocation_origin.get()
            if origin is not None:
                from deskpet.execution.primary_effect_index import record_effect

                await record_effect(origin, context, call.name)
            delivery_adapter = None
            try:
                from .desktop_runtime import _delivery_adapters

                delivery_adapter = _delivery_adapters.get(context.run_id.value)
                if delivery_adapter is not None:
                    await delivery_adapter.present_tool_call(call)
            except Exception:
                delivery_adapter = None
            result = await super().invoke(
                call, context, accepted_result_call_id=accepted_result_call_id
            )
            if delivery_adapter is not None:
                await delivery_adapter.present_tool_result(call, result)
            return result
        finally:
            _current_tool_context.reset(context_token)
            _current_call_id.reset(token)
            if validation_token is not None:
                _validation_run_id.reset(validation_token)


class ForegroundEffectAdmissionPort(Protocol):
    """Final generation-bound admission for foreground-owned Runs.

    Implemented by ``deskpet.execution.foreground_runtime.
    ForegroundEffectAdmissionGate``; Runs never registered with the gate pass
    through unchanged.
    """

    async def authorize(self, sdk_run_id: str) -> object | None: ...


class EffectGatePort(Protocol):
    """Per-effect workspace admission for PROJECT_EFFECT Tools (S5b).

    Implemented by ``deskpet.sdk_adapters.effect_gate.EffectGate``: returns
    ``None`` to admit or a ``ToolResult.rejected`` carrying one stable reason.
    """

    async def verify(
        self, context: ToolContext, tool_name: str, *, call_id: CallId | None = None
    ) -> ToolResult | None: ...


class WorkspaceReadGatePort(Protocol):
    """Call-time workspace admission for the read-class file Tools (F-Z1).

    Implemented by ``deskpet.sdk_adapters.read_gate.WorkspaceReadGate``.  Like
    the EffectGate it is binary: ``None`` admits, a ``ToolResult.rejected``
    names one stable reason.  It only ever answers for a Run whose projection
    exposed the read Tools through the ``primary_route_capable`` exemption;
    every other Run is passed through untouched.
    """

    async def verify(
        self,
        context: ToolContext,
        tool_name: str,
        *,
        call_id: CallId | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult | None: ...


class ProductEffectExecutor(EffectExecutor):
    """Bind SDK validation and dispatch to the immutable Run authority."""

    def __init__(
        self,
        *,
        registry: ProductToolsAdapter,
        foreground_admission: ForegroundEffectAdmissionPort | None = None,
        effect_gate: EffectGatePort | None = None,
        read_gate: WorkspaceReadGatePort | None = None,
        evidence_ingress: Any | None = None,
        procedure_runtime: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(registry=registry, **kwargs)
        self._foreground_admission = foreground_admission
        self._effect_gate = effect_gate
        # F-Z1: read-class file Tools exposed in a route-capable projectless
        # Run are admitted per call, never per Run.
        self._read_gate = read_gate
        # S5b Task 2: Harness evidence reservation before the physical
        # dispatch, objective event + evidence row + tool_invocation import in
        # one state.db transaction after the SDK settled the effect.
        self._evidence_ingress = evidence_ingress
        self._procedure_runtime = procedure_runtime

    async def _evidence_scope(self, context: ToolContext) -> tuple[str, str] | None:
        """(task_scope_id, subject) of the Run's admission scope, or ``None``.

        Foreground Runs resolve through ``foreground_run_sdk_bindings``; a Run
        carrying a PROJECT_EFFECT envelope falls back to the envelope scope
        (the gate already proved it equals the frozen admission scope).  A Run
        with neither has no TaskScope watermark and produces no evidence.
        """

        ingress = self._evidence_ingress
        if ingress is None:
            return None
        binding = await ingress.resolve_run_scope(context.run_id.value)
        if binding is not None:
            return binding.task_scope_id, binding.subject
        envelope = context.task_execution_envelope
        scope_id = None if envelope is None else envelope.task_scope_id
        if not scope_id:
            return None
        subject = await ingress.scope_subject(str(scope_id))
        if subject is None:
            return None
        return str(scope_id), subject

    async def _reserve_evidence(
        self,
        context: ToolContext,
        call: ToolCall,
        scope: tuple[str, str],
        *,
        pre_commit: Any | None = None,
    ) -> None:
        effect_id = context.effect_id
        if effect_id is None:
            raise RuntimeError("effect_evidence_identity_missing")
        await self._evidence_ingress.reserve(
            run_id=context.run_id.value,
            task_scope_id=scope[0],
            kind="tool_invocation",
            source_event_id=f"effect:{effect_id.value}",
            tool_name=call.name,
            pre_commit=pre_commit,
        )

    async def _abandon_rejected_reservation(
        self, context: ToolContext, execution: EffectExecution
    ) -> None:
        """Task 6 (Task 2 review F-7): a call the SDK denied after the reservation
        (``effect=None``) never dispatches, so its reservation is resolved right
        away as a ``rejected`` tombstone — no drainer has to find it later and
        the sequence gap never blocks ``run_terminal``."""

        effect_id = context.effect_id
        if effect_id is None:
            return
        await self._evidence_ingress.abandon_reservation(
            f"effect:{effect_id.value}",
            status="rejected",
            reason_code=execution.result.error_code,
        )

    async def _commit_evidence(
        self, context: ToolContext, call: ToolCall, execution: EffectExecution, scope: tuple[str, str]
    ) -> None:
        from deskpet.execution.evidence_ingress import ToolInvocationFact
        from deskpet.sdk_adapters.effect_gate import classify_objective_event

        record = execution.effect
        if record is None or not record.terminal:
            # Rejected before an effect existed, or still HANDED_OFF/UNKNOWN:
            # the reservation stays open for the terminal drain.
            return
        ingress = self._evidence_ingress
        existing = await ingress.reservation(f"effect:{record.effect_id.value}")
        if existing is not None and existing.status != "reserved":
            # Already imported (e.g. the context_route ledger transaction).
            return
        result = execution.result
        objective = classify_objective_event(
            record.tool_name,
            dict(thaw_json(record.arguments)),
            result,
            effect_id=record.effect_id.value,
            call_id=record.call_id.value,
        )
        fact = ToolInvocationFact(
            run_id=record.run_id.value,
            effect_id=record.effect_id.value,
            call_id=record.call_id.value,
            tool_name=record.tool_name,
            effect_state=record.state.value,
            outcome=result.outcome.value,
            error_code=result.error_code,
            objective=objective,
        )
        await ingress.commit_fact(task_scope_id=scope[0], subject=scope[1], fact=fact)

    async def execute(self, **kwargs: Any):
        context = kwargs.get("context")
        if not isinstance(context, ToolContext):
            raise TypeError("ProductEffectExecutor requires ToolContext")
        gated = self._is_first_occurrence(kwargs)
        if self._effect_gate is not None and gated:
            # S5b EffectGate: re-verify the TaskExecutionEnvelope against the
            # frozen Run authority, the durable route receipt, the S4 binding
            # set and the live filesystem identity before ANY physical project
            # effect.  A rejection is returned as the SDK authorization-deny
            # shape (effect=None): no execution_effects row, no Host event.
            #
            # Step 0 (Task 1 review F-1): an effect that already has a durable
            # SDK ledger record (any state) is an exact replay / reconcile,
            # never a new physical admission.  The gate must not pre-empt the
            # SDK's terminal-record replay or HANDED_OFF/UNKNOWN reconcile
            # with a fabricated terminal ``rejected`` — the admission decision
            # was made durably on first occurrence.
            call = kwargs.get("call")
            if not isinstance(call, ToolCall):
                raise TypeError("ProductEffectExecutor requires ToolCall")
            rejection = await self._effect_gate.verify(
                context, call.name, call_id=call.call_id
            )
            if rejection is not None:
                logger.warning(
                    "tool.denied",
                    extra={
                        "tool": call.name,
                        "reason": rejection.error_code or "effect_gate_rejected",
                        "path": "effect_gate",
                    },
                )
                return EffectExecution(effect=None, result=rejection)
        if self._read_gate is not None and gated:
            # F-Z1 read gate.  Same shape and same step-0 discipline as the
            # EffectGate: only a first occurrence is verified, a rejection is
            # the SDK authorization-deny shape (effect=None) so no
            # ``execution_effects`` row and no Host event is produced, and the
            # model sees one stable reason code with its executable next step.
            call = kwargs.get("call")
            if not isinstance(call, ToolCall):
                raise TypeError("ProductEffectExecutor requires ToolCall")
            read_rejection = await self._read_gate.verify(
                context,
                call.name,
                call_id=call.call_id,
                arguments=dict(thaw_json(call.arguments)),
            )
            if read_rejection is not None:
                logger.warning(
                    "tool.denied",
                    extra={
                        "tool": call.name,
                        "reason": read_rejection.error_code or "read_gate_rejected",
                        "path": "workspace_read_gate",
                    },
                )
                return EffectExecution(effect=None, result=read_rejection)
        self._registry.assert_workspace_current(context.run_id)
        origin = None
        if self._foreground_admission is not None:
            # Final current-generation admission immediately before the
            # physical Tool effect.  A foreground lease reclaimed after the
            # authorization decision fails here, so a stale worker's Run
            # cannot produce external Tool side effects.
            origin = await self._foreground_admission.authorize(context.run_id.value)
        evidence_scope = await self._evidence_scope(context)
        if evidence_scope is not None:
            call = kwargs.get("call")
            if not isinstance(call, ToolCall):
                raise TypeError("ProductEffectExecutor requires ToolCall")
            # Reserve the Harness source_sequence BEFORE the physical action
            # (design-freeze §3): a crash after the SDK settles the effect but
            # before the Host commit is closed by the terminal drain, which
            # re-reads the settled effect under this exact sequence.
            #
            # Task 6 (Task 1 review F-2): the reservation's BEGIN IMMEDIATE is
            # the last Host write lock before the physical dispatch, so the
            # gate re-checks the binding head / scope status inside it; a head
            # that moved since the gate snapshot rejects here with no
            # reservation and no dispatch.
            pre_commit = None
            if self._effect_gate is not None and gated:
                pre_commit = self._effect_gate.reservation_check(
                    context, call.name, call_id=call.call_id
                )
            try:
                await self._reserve_evidence(
                    context, call, evidence_scope, pre_commit=pre_commit
                )
            except EffectGateRejected as rejected:
                await self._effect_gate.memoize_rejection(context, rejected.result)
                logger.warning(
                    "tool.denied",
                    extra={
                        "tool": call.name,
                        "reason": rejected.result.error_code or "effect_gate_rejected",
                        "path": "effect_gate_reservation",
                    },
                )
                return EffectExecution(effect=None, result=rejected.result)
        token = _validation_run_id.set(context.run_id.value)
        origin_token = _foreground_invocation_origin.set(origin)
        try:
            from contextlib import AsyncExitStack, nullcontext
            binding_scope = getattr(self._effect_gate, "execution_scope", None)
            scope = binding_scope(context, kwargs["call"].name) if gated and callable(binding_scope) else nullcontext()
            read_scope = getattr(self._read_gate, "execution_scope", None)
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(scope)
                if gated and callable(read_scope):
                    # The verified read root is projected into the execution
                    # context for this dispatch only, so a relative path and a
                    # ``path``-less glob/grep resolve against the bound root
                    # instead of the backend process cwd.
                    await stack.enter_async_context(
                        read_scope(
                            context,
                            kwargs["call"].name,
                            call_id=kwargs["call"].call_id,
                        )
                    )
                # A foreground/binding rejection must not consume a Procedure
                # step. Recheck and reserve only inside the final execution
                # scope, after the existing admission/evidence gates.
                procedure_rejection = None
                if self._procedure_runtime is not None and gated:
                    from deskpet.memory.procedure_applicability import ProcedureUseRejected
                    try:
                        await self._procedure_runtime.before_call(context, kwargs["call"])
                    except ProcedureUseRejected as error:
                        # r14: one opaque string for every pre-call deny left
                        # the model guessing and re-binding until the turn
                        # budget ran out. The code is unchanged; the public
                        # text now names the exact bound call it must issue.
                        from deskpet.memory.procedure_guidance import (
                            call_rejection_public_message)
                        procedure_rejection = ToolResult.rejected(
                            kwargs["call"].call_id, str(error),
                            call_rejection_public_message(error))
                if procedure_rejection is not None:
                    execution = EffectExecution(effect=None, result=procedure_rejection)
                else:
                    execution = await super().execute(**kwargs)
        finally:
            _foreground_invocation_origin.reset(origin_token)
            _validation_run_id.reset(token)
        if evidence_scope is not None:
            if execution.effect is None:
                await self._abandon_rejected_reservation(context, execution)
            else:
                await self._commit_evidence(context, kwargs["call"], execution, evidence_scope)
        if self._procedure_runtime is not None and execution.effect is not None:
            await self._procedure_runtime.store.commit_effect(record=execution.effect)
        return execution

    def _is_first_occurrence(self, kwargs: Mapping[str, Any]) -> bool:
        """Step 0: only an effect with no durable SDK ledger record — or a
        record still PREPARED — is gated.

        ``uow.read_effect`` is the SDK's own replay/reconcile read (sync,
        same store the executor uses); a record past PREPARED means the SDK
        owns the outcome (terminal replay returns the original receipt,
        HANDED_OFF/UNKNOWN goes through ``reconcile``).  A PREPARED row (Task
        2 review F-3) means no physical action happened yet and the SDK will
        re-authorize and dispatch on replay, so the gate must re-verify it.
        """

        from simple_harness.execution.effects import EffectState

        effect_id = kwargs.get("effect_id")
        read_effect = getattr(self._uow, "read_effect", None)
        if effect_id is None or not callable(read_effect):
            return True
        existing = read_effect(effect_id)
        return existing is None or existing.state is EffectState.PREPARED

    async def _prepared(self, *, effect_id, call, context):
        token = _validation_run_id.set(context.run_id.value)
        try:
            return await super()._prepared(
                effect_id=effect_id,
                call=call,
                context=context,
            )
        finally:
            _validation_run_id.reset(token)

    def _prepared_from_decision(self, decision):
        token = _validation_run_id.set(str(decision.run_id))
        try:
            return super()._prepared_from_decision(decision)
        finally:
            _validation_run_id.reset(token)


# A handler may opt into a stable, model-visible failure by returning
# ``{"error": ..., "error_code": "<stable_code>", "public_message": "..."}``.
# The code must use this alphabet (no paths, stack frames, secrets) and the
# message is bounded; anything else keeps the opaque default mapping.
_SAFE_HANDLER_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_HANDLER_PUBLIC_MESSAGE = 2048

# --- 事件 AH：``tool_failed`` 不得再是一句空话 --------------------------------
# HM-TO-A6 第 12 次尝试 turn 11：``grep`` 对一个**文件**路径连挂 4 次，handler
# 返回的是 ``{"error": "path is not a directory: <path>"}``——没有 ``error_code``
# 就走下面的默认分支，压成 ``tool_failed`` + "Tool execution failed."。
# ``ToolResult.failed`` **不带 value**，于是拒因既没到模型（payload 被丢弃），
# 也没进日志（只记 code）。模型无从自纠，改用 1 KiB 分页硬读 40 KB 文件，预算打光。
#
# 因此默认分支也必须产出一条**有界、去路径、去密钥**的原因：异常类名
# （handler 可选给 ``error_type``）+ 净化后的自然语言，同时进 ``public_message``
# 与 ``product_tool.failed`` 的 warning 日志。净化复用仓库既有的
# ``redact_sensitive_text``（与 ``observability/log_redaction`` 同一套规则），
# 路径再额外整体折叠成 ``<path>``——稳定码与日志字段永不含路径是既定口径
# （``tools/file_tools._err`` / ``tests/os_tools/test_file_tools_rejection_codes.py``）。
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:[\\/]|~(?=[\\/])|\.{0,2}/)[^\s'\"()\[\],;]*")
_MAX_FAILURE_REASON = 240
_FAILURE_TEXT_KEYS = ("public_message", "error", "message", "hint", "detail", "reason")
NO_REASON_FAILURE_MESSAGE = (
    "Tool execution failed and the handler reported no reason (tool_failed). "
    "Re-read the tool schema, change at least one argument, and try once more; "
    "do not repeat the identical call."
)


def _sanitized_failure_text(value: Any) -> str:
    """有界、去密钥、去路径的失败原因散文；拿不到就是空串。"""

    text = ""
    if isinstance(value, Mapping):
        for key in _FAILURE_TEXT_KEYS:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                text = item.strip()
                break
            if isinstance(item, Mapping):
                nested = item.get("message")
                if isinstance(nested, str) and nested.strip():
                    text = nested.strip()
                    break
    elif isinstance(value, str):
        text = value.strip()
    if not text:
        return ""
    text = _PATH_LIKE.sub("<path>", redact_sensitive_text(text))
    return " ".join(text.split())[:_MAX_FAILURE_REASON]


def _failure_error_type(value: Any) -> str:
    """handler 自报的异常类名（``OSError`` / ``re.error`` …），无则空串。"""

    if not isinstance(value, Mapping):
        return ""
    declared = value.get("error_type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()[:64]
    error = value.get("error")
    if isinstance(error, Mapping):
        nested = error.get("type")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()[:64]
    return ""


def _failure_reason(value: Any) -> str:
    """``<异常类>: <净化散文>`` —— 可直接进日志字段的一行原因。"""

    parts = [item for item in (_failure_error_type(value), _sanitized_failure_text(value)) if item]
    return ": ".join(parts) if parts else "-"


def failure_log_reason(raw: Any) -> str:
    """``product_tool.failed`` 的 ``reason=`` 字段（永不含路径/密钥）。"""

    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
    return _failure_reason(value)


def _result(raw: Any) -> ToolResult:
    call_id = active_product_tool_call_id()
    if isinstance(raw, ToolResult):
        if raw.call_id != call_id:
            raise ValueError("product ToolResult used a foreign call_id")
        return raw
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
    if isinstance(value, Mapping) and (
        value.get("ok") is False or value.get("success") is False or value.get("error")
    ):
        error = value.get("error")
        code = "tool_failed"
        message = ""
        if isinstance(error, Mapping):
            code = str(error.get("code") or code)
            message = str(error.get("message") or "")
        declared_code = value.get("error_code")
        if isinstance(declared_code, str) and _SAFE_HANDLER_ERROR_CODE.fullmatch(
            declared_code
        ):
            code = declared_code
            declared_message = value.get("public_message")
            if isinstance(declared_message, str) and declared_message.strip():
                message = declared_message.strip()[:_MAX_HANDLER_PUBLIC_MESSAGE]
        if not message.strip():
            # 事件 AH：没有 handler 自报的 public_message 时，也必须给出**有界、
            # 去路径**的原因，而不是那句谁也无法据以自纠的 "Tool execution failed."。
            reason = _failure_reason(value)
            message = (
                f"Tool execution failed ({reason})."
                if reason != "-"
                else NO_REASON_FAILURE_MESSAGE
            )
        return ToolResult.failed(call_id, code, message[:_MAX_HANDLER_PUBLIC_MESSAGE])
    return ToolResult.succeeded(call_id, value)


def _sdk_input_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    """Detach a Host/MCP schema and remove non-semantic dialect markers.

    The SDK intentionally accepts a small executable JSON-Schema subset.  MCP
    producers commonly add ``$schema``/``$id`` annotations; those markers do
    not change argument validation and must not make the whole Runtime fail to
    start.  Every other keyword is retained so the SDK validator can reject
    unsupported semantics rather than silently weakening them.
    """

    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): normalize(child)
                for key, child in item.items()
                if key not in {"$schema", "$id"}
            }
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        return copy.deepcopy(item)

    normalized = normalize(value)
    if not isinstance(normalized, dict):  # pragma: no cover - Mapping guarantees this
        raise TypeError("SDK Tool input schema must normalize to an object")
    return normalized


# S5B-UI-F2（S5b 真实 UI 验收）：冻结 SDK 0.7.1 在 ``ToolRegistry.validate``
# 按 schema 的 ``required`` 校验参数，缺项抛 ``MalformedToolArgumentsError``，
# kernel 据此把**整个 Run** 判 ``driver_failed``——发生在进入处理器之前，模型没有
# 任何自纠机会。真实 gpt-5.6-luna 在两个不同工具（``tool_search {}``、
# ``context_route {}``）上各打掉过一整个 Run。
#
# 因此「必填」由 Host 自己执行：发布给 SDK 的 schema 不再带 ``required``，
# 在这里的产品包装层按同一张必填清单校验，缺项返回稳定码 + 可执行 next_action。
# 语义没有放宽——缺项照样被拒绝，只是拒绝从「杀 Run」变成「模型可见且可重试」。
# 已提供字段的类型校验仍由 SDK 负责，不受影响。
MISSING_ARGUMENT_ERROR_CODE = "missing_required_argument"
INVALID_ARGUMENTS_ERROR_CODE = "invalid_tool_arguments"

# 冻结 SDK 的 ``ToolRegistry.validate`` 对**整个** schema 求值（顶层 required、嵌套
# required、类型、枚举……），任何一条不符就抛 ``MalformedToolArgumentsError``；kernel
# 据此把整个 Run 判 ``driver_failed``。把顶层 required 搬到 Host 只解决了其中一类：
# 真实 gpt-5.6-luna 用 ``agent_parallel {"subagents":[{...}]}``（**嵌套**字段不合规）
# 照样打掉过一整个 Run（证据 20260903T1130-uiA）。
#
# 因此在 Host 自己拥有的 registry 子类里接住校验失败：记下该 call 的失败原因并照常
# 返回 Tool，随后产品包装层在**调用真实处理器之前**把它转成模型可见的稳定拒绝。
# 处理器因此永远拿不到非法参数，语义没有放宽，Run 也不再因为模型的一次参数失误而死。
# 标记用**模块级有界表**而不是 ContextVar：``ToolRegistry.invoke`` 在当前 context 里
# validate，随后 ``asyncio.create_task(dispatch())`` 复制一份 context，在副本里删除
# 不会回写父 context，长 Run 内会单调泄漏（独立审查预警）。call_id 全局唯一，普通
# dict 即可；消费即删，并按上限淘汰最旧，异常路径下也不会无界增长。
_INVALID_ARGUMENTS_MAX = 256
_invalid_arguments: dict[str, str] = {}


def _record_invalid_arguments(call_id: str, detail: str) -> None:
    _invalid_arguments[str(call_id)] = detail
    while len(_invalid_arguments) > _INVALID_ARGUMENTS_MAX:
        _invalid_arguments.pop(next(iter(_invalid_arguments)))


def _take_invalid_arguments(call_id: str) -> str | None:
    return _invalid_arguments.pop(str(call_id), None)


def _missing_required_arguments(
    arguments: Mapping[str, Any], required: Sequence[str]
) -> list[str]:
    """缺失或显式 ``None`` 才算缺参。

    空字符串**不算**：``write_file`` 的 ``content`` 是必填，而写一个空文件是合法
    请求；把空串当缺参会把合法调用误拒。各工具自己对空值的语义（例如
    ``tool_search`` 的空 query 无意义）留在它们的处理器里判断。
    """

    return [
        name
        for name in required
        if name not in arguments or arguments[name] is None
    ]


# HM-TO-A6 incident B（2026-09-08 native run product-sdk-cba43a68… turn 7）：
# 真实 DeepSeek 连发 8 次 ``task_scope_search {}`` 与 6 次 ``task_scope_update {}``，
# 每次都收到同一句「see the tool description for the expected values」，直到
# ``react_max_turns_exceeded`` 打掉整轮。缺参回执点名了字段，却没有回显字段的
# **形状**（类型 / 枚举 / 最小项数），模型无从补齐。参照 procedure_use（bcd3bb15）
# 的做法：稳定码与 schema 都不动，只把已发布 schema 里已有的形状回显出来。
_MAX_ECHOED_ENUM = 6
_MAX_ARGUMENT_SHAPE_CHARS = 600


def _argument_shape(schema: Any) -> str:
    """One compact human-readable shape line for a single JSON-Schema property."""

    if not isinstance(schema, Mapping):
        return "value"
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        kind = "|".join(str(item) for item in raw_type if str(item) != "null")
    else:
        kind = str(raw_type or "value")
    parts: list[str] = [kind or "value"]
    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and enum:
        shown = [json.dumps(item, ensure_ascii=False) for item in enum[:_MAX_ECHOED_ENUM]]
        if len(enum) > _MAX_ECHOED_ENUM:
            shown.append("…")
        parts.append("one of " + "|".join(shown))
    if kind == "array":
        items = schema.get("items")
        item_kind = (
            str(items.get("type") or "object")
            if isinstance(items, Mapping)
            else "object"
        )
        parts.append(f"of {item_kind}")
        required_items = items.get("required") if isinstance(items, Mapping) else None
        if isinstance(required_items, (list, tuple)) and required_items:
            parts.append(
                "each item requires "
                + ", ".join(str(item) for item in required_items)
            )
    for key, label in (
        ("minimum", "min"),
        ("minItems", "min items"),
        ("minLength", "min length"),
    ):
        value = schema.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{label} {value}")
    return ", ".join(parts)


def _expected_arguments_hint(
    input_schema: Mapping[str, Any], missing: Sequence[str]
) -> str:
    """``Expected: name (shape); …`` for exactly the arguments that were missing."""

    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    shapes = [
        f"{name} ({_argument_shape(properties.get(name))})"
        for name in missing
        if isinstance(properties.get(name), Mapping)
    ]
    if not shapes:
        return ""
    hint = "Expected: " + "; ".join(shapes) + "."
    if len(hint) > _MAX_ARGUMENT_SHAPE_CHARS:
        hint = hint[: _MAX_ARGUMENT_SHAPE_CHARS - 1].rstrip() + "…"
    return hint


def _sdk_tool(registration: ProductToolRegistration) -> FunctionTool:
    async def invoke(arguments, context):
        call_id = active_product_tool_call_id()
        invalid = _take_invalid_arguments(str(call_id))
        missing = _missing_required_arguments(arguments, host_required)
        if missing:
            # 缺必填是最常见的一类，给指名道姓的稳定码（比通用 schema 拒绝更可行动）。
            names = ", ".join(missing)
            logger.warning(
                "tool_arguments.missing tool=%s missing=%s",
                registration.name,
                names,
            )
            expected = _expected_arguments_hint(input_schema, missing)
            return ToolResult.failed(
                call_id,
                MISSING_ARGUMENT_ERROR_CODE,
                (
                    f"{registration.name} rejected: missing required "
                    f"argument(s) {names}. Call {registration.name} again with "
                    f"{names} filled in."
                    + (f" {expected}" if expected else "")
                ),
            )
        if invalid is not None:
            # 其余 schema 违规（类型、枚举、范围、多余属性、嵌套必填）统一走这条。
            logger.warning(
                "product_tool.invalid_arguments tool=%s", registration.name
            )
            return ToolResult.failed(
                call_id,
                INVALID_ARGUMENTS_ERROR_CODE,
                (
                    f"{registration.name} rejected: the arguments do not match "
                    "its schema. Re-read the tool schema and call it again with "
                    "every required field present and correctly typed, "
                    "including fields nested inside objects and arrays, and "
                    "without extra properties."
                ),
            )
        raw = registration.handler(arguments, context)
        if inspect.isawaitable(raw):
            raw = await raw
        result = _result(raw)
        if result.outcome is ToolOutcome.FAILED:
            # Host-side trace of every failed product Tool call: stable code
            # only, never the handler payload (it may carry private data).
            # 字段拼进 message：本项目 structlog 的 foreign_pre_chain 没有
            # ExtraAdder，``extra=`` 的字段在渲染阶段会被整体丢弃（独立审查 F-5 实测）。
            logger.warning(
                "product_tool.failed tool=%s code=%s reason=%s",
                registration.name,
                result.error_code,
                failure_log_reason(raw),
            )
        return result

    input_schema = _sdk_input_schema(registration.input_schema)
    if input_schema.get("type") == "object":
        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        properties = dict(properties)
        properties["deskpet_public_progress"] = {
            "type": "string",
            "description": (
                "Optional concise user-facing description of the current "
                "action. Never include private reasoning, secrets, full "
                "arguments, or raw tool output."
            ),
        }
        input_schema["properties"] = properties
    # `required` **保留在发布 schema 里**：模型必须看得见哪些参数是必填的。
    # （独立审查 F-1：先前把它摘掉使 66/77 个工具对模型呈现为"全可选"，反而放大了
    # 漏填概率，且属于已批准的模型可见契约缩水。校验失败现已被 Host 接住，摘除不再必要。）
    # Host 侧另留一份同样的清单，只为在拒绝时能指名道姓说缺了哪个参数。
    declared_required = input_schema.get("required")
    host_required: tuple[str, ...] = (
        tuple(
            str(item)
            for item in declared_required
            if str(item) != "deskpet_public_progress"
        )
        if isinstance(declared_required, (list, tuple))
        else ()
    )
    return FunctionTool(
        ToolSpec(
            registration.name,
            registration.description,
            input_schema,
        ),
        invoke,
    )


def build_product_tool_registry(
    registrations: Sequence[ProductToolRegistration],
) -> tuple[ProductToolsAdapter, tuple[ProductToolInventoryEntry, ...]]:
    by_name: dict[str, ProductToolRegistration] = {}
    for registration in registrations:
        if registration.name in by_name:
            raise ValueError(f"duplicate product Tool: {registration.name}")
        by_name[registration.name] = registration
    missing = tuple(
        name
        for name in PRODUCT_TOOL_NAMES
        if name not in by_name and name not in HOST_COMPOSED_TOOL_NAMES
    )
    extra = tuple(sorted(set(by_name) - set(PRODUCT_TOOL_NAMES)))
    if missing or extra:
        raise ValueError(f"product Tool inventory mismatch: missing={missing}, extra={extra}")
    ordered = tuple(by_name[name] for name in PRODUCT_TOOL_NAMES if name in by_name)
    sdk_tools = tuple(_sdk_tool(item) for item in ordered)
    execution_identities = {
        registration.name: _product_tool_execution_identity(registration, tool)
        for registration, tool in zip(ordered, sdk_tools, strict=True)
    }
    registry = ProductToolsAdapter(
        sdk_tools,
        execution_identities=execution_identities,
    )
    inventory = tuple(
        ProductToolInventoryEntry(
            item.name,
            item.dispatch_kind,
            item.permission_category,
            str(item.metadata["source"]),
            str(item.metadata["version"]),
            execution_identities[item.name],
            item.projectless_admission,
        )
        for item in ordered
    )
    return registry, inventory


def extend_product_registry_with_mcp(
    registry: ProductToolsAdapter,
    inventory: Sequence[ProductToolInventoryEntry],
    *,
    legacy_registry: object,
    execution_context_getter: Callable[[], Any],
) -> tuple[ProductToolInventoryEntry, ...]:
    """Project current healthy MCP handlers into the SDK executor registry."""

    all_specs = getattr(legacy_registry, "all_specs", None)
    execute_tool = getattr(legacy_registry, "execute_tool", None)
    if not callable(all_specs) or not callable(execute_tool):
        raise TypeError("legacy MCP registry contract is unavailable")
    extended = list(inventory)
    existing = {item.name for item in extended}
    for legacy in sorted(all_specs(), key=lambda item: str(item.name)):
        source = str(getattr(legacy, "source", ""))
        name = str(getattr(legacy, "name", ""))
        if not source.startswith("mcp:") or name in existing:
            continue
        schema = getattr(legacy, "schema", None)
        if not isinstance(schema, Mapping):
            raise TypeError(f"MCP Tool schema is invalid: {name}")
        parameters = schema.get("parameters")
        if not isinstance(parameters, Mapping):
            raise TypeError(f"MCP Tool parameters are invalid: {name}")
        description = str(schema.get("description") or "").strip()
        if not description:
            raise ValueError(f"MCP Tool description is required: {name}")
        identity = _canonical_sha256(
            {
                "name": name,
                "source": source,
                "spec_version": str(getattr(legacy, "spec_version", "v1")),
                "schema": dict(schema),
                "runtime_provenance_ref": str(
                    getattr(legacy, "runtime_provenance_ref", "")
                ),
                "fixture_epoch": int(getattr(legacy, "fixture_epoch", 0)),
                "fixture_spec_hash": str(getattr(legacy, "fixture_spec_hash", "")),
                "stable_handler_id": str(getattr(legacy, "stable_handler_id", "")),
            }
        )

        async def invoke(arguments, context, *, _name=name):
            execution_context = execution_context_getter()
            raw = await execute_tool(
                _name,
                dict(arguments),
                execution_context.session_id,
                execution_context.request_id,
                execution_context=execution_context,
            )
            if not bool(raw.get("ok", False)):
                return ToolResult.failed(
                    context.call_id,
                    "mcp_tool_failed",
                    str(raw.get("error") or "MCP Tool failed."),
                )
            value = raw.get("result")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            return ToolResult.succeeded(context.call_id, value)

        try:
            sdk_tool = FunctionTool(
                ToolSpec(name, description, _sdk_input_schema(parameters)), invoke
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "mcp_tool_excluded_from_sdk_catalog name=%s reason=schema_incompatible error=%s",
                name,
                exc,
            )
            continue
        registry.register_dynamic(sdk_tool, execution_identity=identity)
        extended.append(
            ProductToolInventoryEntry(
                name=name,
                dispatch_kind="async",
                permission_category=str(
                    getattr(legacy, "permission_category", "read_file")
                ),
                source=source,
                version=str(getattr(legacy, "spec_version", "v1")),
                execution_identity=identity,
                projectless_admission="requires_project",
            )
        )
        existing.add(name)
    return tuple(extended)


def filter_sdk_catalog_for_workspace(
    catalog: Mapping[str, Any],
    inventory: Sequence[ProductToolInventoryEntry],
    *,
    workspace_resolution_kind: str,
    primary_route_capable: bool = False,
) -> tuple[dict[str, Any], tuple[ProductToolInventoryEntry, ...]]:
    """Return the per-Run model/tool projection for one workspace tag.

    The durable catalog generation/fingerprint continue to identify the full
    source snapshot.  A projectless Run records its filtered inventory, so
    restart can deterministically reapply the same projection before exact
    authority reconstruction.
    """

    from deskpet.sdk_adapters.tool_authority import (
        _PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES,
    )

    kind = str(workspace_resolution_kind).strip()
    if kind == "legacy":
        selected = dict(catalog)
        selected["descriptor_specs"] = list(catalog.get("specs", ()))
        return selected, tuple(inventory)
    if kind == "project_bound":
        selected_inventory = tuple(
            replace(
                item,
                availability_reason=(
                    item.availability_reason or "workspace_unscoped"
                    if item.source in PROJECT_BOUND_UNSCOPED_MCP_SOURCES
                    else item.availability_reason
                ),
            )
            for item in inventory
        )
        allowed = {item.name for item in selected_inventory if item.executable_eligible}
        selected_specs = tuple(
            item
            for item in catalog.get("specs", ())
            if str(item.get("name")) in allowed
        )
        if {str(item.get("name")) for item in selected_specs} != allowed:
            raise RuntimeError("project_tool_projection_incomplete")
        selected = dict(catalog)
        selected["descriptor_specs"] = list(catalog.get("specs", ()))
        selected["specs"] = list(selected_specs)
        selected["tool_names"] = [str(item.get("name")) for item in selected_specs]
        selected["tool_count"] = len(selected_specs)
        from deskpet.sdk_adapters.context_partitions import tool_schema_tokens

        selected["schema_token_count"] = tool_schema_tokens(selected_specs)
        schema_fingerprints = catalog.get("schema_fingerprints")
        if isinstance(schema_fingerprints, Mapping):
            selected["schema_fingerprints"] = {
                name: schema_fingerprints[name] for name in allowed
            }
        return selected, selected_inventory
    if kind == "missing":
        raise RuntimeError("workspace_unavailable")
    if kind != "projectless":
        raise ValueError("unsupported workspace resolution kind")
    # A ``primary_route_capable`` Run is one that was frozen before the model
    # routed (``task_scope_id is None`` at Run start) and can still bind a task
    # from inside the Run.  Both exemptions below expose a Tool whose *call* is
    # gated afterwards, never a Tool that could act unbound:
    #   * PROJECT_EFFECT names — SDK react barrier + TaskExecutionEnvelope +
    #     EffectGate (design-freeze §1/§4);
    #   * F-Z1 read names — ``WorkspaceReadGate`` (durable route decision +
    #     verified binding root + path containment) at call time.
    # Without the second line a Run could write and shell out but never read a
    # workspace file (incident Z), which is the asymmetry F-Z1 closes.
    exempt = _PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES
    selected_inventory = tuple(
        replace(
            item,
            availability_reason=(
                item.availability_reason
                if (item.projectless_admission == "safe"
                    or (primary_route_capable and item.name in exempt))
                else item.availability_reason or "workspace_unscoped"
            ),
        )
        for item in inventory
    )
    allowed = {item.name for item in selected_inventory if item.executable_eligible}
    selected_specs = tuple(
        item for item in catalog.get("specs", ()) if str(item.get("name")) in allowed
    )
    if {str(item.get("name")) for item in selected_specs} != allowed:
        raise RuntimeError("projectless_tool_projection_incomplete")
    selected = dict(catalog)
    selected["descriptor_specs"] = list(catalog.get("specs", ()))
    selected["specs"] = list(selected_specs)
    selected["tool_names"] = sorted(allowed)
    schema_fingerprints = catalog.get("schema_fingerprints")
    if isinstance(schema_fingerprints, Mapping):
        selected["schema_fingerprints"] = {
            name: schema_fingerprints[name] for name in allowed
        }
    return selected, selected_inventory


__all__ = (
    "PRODUCT_TOOL_NAMES",
    "PROJECTLESS_SAFE_TOOL_NAMES",
    "EffectGatePort",
    "ForegroundEffectAdmissionPort",
    "ProductEffectExecutor",
    "ProductToolInventoryEntry",
    "ProductToolRegistration",
    "ProductToolsAdapter",
    "SdkToolExecutorCatalogUnavailable",
    "active_product_tool_call_id",
    "active_product_tool_context",
    "build_product_tool_registry",
    "extend_product_registry_with_mcp",
    "filter_sdk_catalog_for_workspace",
)
