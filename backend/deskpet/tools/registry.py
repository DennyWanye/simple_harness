# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: the ToolRegistry singleton (tool-framework spec).

Design notes
------------

* **Auto-discovery**: ``deskpet/tools/__init__.py`` walks the package's
  submodules with ``pkgutil.iter_modules`` and imports each one so their
  top-level ``registry.register(...)`` calls land in the singleton. Tool
  authors never touch ``__init__.py`` — drop a new ``foo_tool.py`` and
  call ``register`` at module scope.

* **OpenAI function-calling format**: ``schemas()`` emits the exact
  ``{type: "function", function: {name, description, parameters}}``
  shape that anthropic/openai/gemini adapters all normalize against
  (see spec "OpenAI-Format Tool Schemas").

* **Toolset gating**: every tool belongs to a ``toolset`` string
  (e.g. ``"file"``, ``"web"``, ``"memory"``, ``"control"``). The
  ContextAssembler passes ``enabled_toolsets=[...]`` at turn start so
  only the task-relevant slice shows up in the LLM prompt.

* **Env + check gating**: ``requires_env=["BRAVE_API_KEY"]`` hides the
  tool entirely when any var is missing. ``check_fn`` runs just before
  dispatch and, on False, short-circuits with a retriable error JSON —
  used by ``memory_search`` while the BGE-M3 embedder is still warming.

* **Error contract**: every dispatch path — whether the handler succeeds,
  returns an error dict, raises, or is gated by check_fn — MUST return a
  JSON **string**. Callers never need to unwrap Python exceptions; they
  feed the string straight back to the LLM tool-result turn.

* **Thread safety**: registration may happen during import on any thread
  (e.g. a background prefetch that imports ``deskpet.tools``), and
  ``dispatch`` is called from the agent loop. A ``threading.Lock``
  serializes registration and the registry-read portion of dispatch.
  Handler execution runs **outside** the lock so slow tools never block
  other dispatches.
"""
from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .capabilities import (
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolPolicySnapshot,
    current_tool_execution_context,
    reset_tool_execution_context,
    set_tool_execution_context,
)

from .error_classifier import classify as _classify_retriable

logger = logging.getLogger(__name__)


# A tool handler receives the JSON-decoded args dict + a correlation
# ``task_id`` (used for tracing / observability; "" when the caller
# doesn't have one handy) and MUST return a JSON-encodable string.
ToolHandler = Callable[[dict[str, Any], str], str]
CheckFn = Callable[[], bool]
# Dynamic visibility predicate. Returns True iff the tool should be exposed
# to the LLM *this turn*. Unlike ``requires_env`` (static env check) this is
# re-evaluated on every ``schemas()`` call, so it can depend on per-session
# runtime state (e.g. "only show goal_task_* tools when a /goal is active").
# ``None`` (the default) means "always visible".
VisibilityFn = Callable[..., bool]
OutcomeParser = Callable[[Any], Any]
PrepareFn = Callable[
    [dict[str, Any], str, str],
    tuple[Mapping[str, Any], Sequence[Any]],
]
LifecycleFn = Callable[..., Any]


_OUTCOME_PARSER_VERSION = "v1"
_GRAPH_STAGED_FILE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "file_write",
        "desktop_create_file",
        "doc_create",
        "doc_edit",
        "excel_create",
        "pdf_export",
    }
)
_GRAPH_EXCLUDED_WRITE_TOOLS: frozenset[str] = frozenset(
    {"file_organize", "memory_write", "memory_forget", "ppt_create"}
)
_GRAPH_OPAQUE_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "run_browser_task",
        "screen_click",
        "screen_move",
        "screen_type",
        "screen_key",
        "screen_scroll",
    }
)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_hash(schema: Mapping[str, Any]) -> str:
    return _canonical_hash(dict(schema))


def _parser_hash(parser_id: str, version: str) -> str:
    return _canonical_hash({"parser_id": parser_id, "version": version})


def _schema_defaults(schema: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """Apply top-level JSON-schema defaults without mutating caller input."""

    out = dict(params)
    parameters = schema.get("parameters", {})
    properties = parameters.get("properties", {}) if isinstance(parameters, Mapping) else {}
    if isinstance(properties, Mapping):
        for key, definition in properties.items():
            if key not in out and isinstance(definition, Mapping) and "default" in definition:
                out[str(key)] = definition["default"]
    return out


def _result_payload(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _failure_message(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("code") or "tool failed")
    return str(error or payload.get("message") or "tool failed")


def _normalize_with_parser(parser_id: str, raw: Any) -> Any:
    """Parse a handler result into the durable workflow outcome contract."""

    from deskpet.workflows.effects import NormalizedToolOutcome

    payload = _result_payload(raw)
    if parser_id == "opaque_manual_v1":
        return NormalizedToolOutcome.malformed(
            "opaque tool outcome requires manual reconciliation"
        )
    if parser_id == "mcp_explicit_v1":
        if not isinstance(payload, Mapping) or "isError" not in payload:
            return NormalizedToolOutcome.malformed(
                "dynamic tool did not provide an explicit isError outcome"
            )
        if bool(payload.get("isError")):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        return NormalizedToolOutcome.success(dict(payload))
    if parser_id == "shell_exit_v1":
        if not isinstance(payload, Mapping) or "exit_code" not in payload:
            return NormalizedToolOutcome.malformed("shell outcome has no exit_code")
        if payload.get("exit_code") == 0:
            return NormalizedToolOutcome.success(dict(payload))
        return NormalizedToolOutcome.failure(
            "nonzero_exit", _failure_message(payload), value=dict(payload)
        )
    if parser_id == "accepted_run_v1":
        if isinstance(payload, Mapping) and payload.get("accepted") is True and payload.get("run_id"):
            return NormalizedToolOutcome.success(dict(payload))
        if isinstance(payload, Mapping) and (payload.get("ok") is False or payload.get("error")):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        return NormalizedToolOutcome.malformed("accepted outcome needs accepted=true and run_id")
    if parser_id == "artifact_envelope_v1":
        if isinstance(payload, Mapping) and (payload.get("ok") is False or payload.get("error")):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        if isinstance(payload, Mapping) and any(
            payload.get(key) for key in ("path", "output_path", "artifact", "artifacts")
        ):
            return NormalizedToolOutcome.success(dict(payload))
        return NormalizedToolOutcome.malformed("artifact outcome has no artifact or path")
    if parser_id == "code_array_or_error_v1":
        if isinstance(payload, list):
            return NormalizedToolOutcome.success(payload)
        if isinstance(payload, Mapping):
            if payload.get("ok") is False or payload.get("error"):
                return NormalizedToolOutcome.failure(
                    "tool_failed", _failure_message(payload), value=dict(payload)
                )
            return NormalizedToolOutcome.success(dict(payload))
        return NormalizedToolOutcome.malformed("code search outcome is not an array or object")
    if parser_id == "json_error_envelope_v1":
        if not isinstance(payload, Mapping):
            return NormalizedToolOutcome.malformed("tool outcome is not a JSON object")
        if payload.get("ok") is False or payload.get("error"):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        return NormalizedToolOutcome.success(dict(payload))
    return NormalizedToolOutcome.malformed(f"unknown outcome parser: {parser_id}")


def _default_parser_id(name: str, source: str, permission_category: str) -> str:
    if source.startswith(("plugin:", "mcp:")):
        return "opaque_manual_v1"
    if name == "run_shell":
        return "shell_exit_v1"
    if name in {"glob", "grep"}:
        return "code_array_or_error_v1"
    if name in {"deepresearch", "ppt_pro"}:
        return "accepted_run_v1"
    if name in _GRAPH_STAGED_FILE_TOOLS:
        return "artifact_envelope_v1"
    if permission_category in _WRITE_PERMISSION_CATEGORIES:
        return "opaque_manual_v1"
    return "json_error_envelope_v1"


# WI-CC-2 (plan mode 物理只读): 写/执行类 permission_category 集中维护。
# 与 deskpet.types.skill_platform.PermissionCategory 的 8 个合法值对齐 ——
# 写产物/有副作用的 4 类列入；只读类（read_file / read_file_sensitive /
# network / mcp_call）不在内，规划期照常放行。按 *permission_category* 判而
# 非硬编码工具名，所以自动覆盖所有写产物工具（os/ppt/excel/doc/pdf/memory/
# computer_use 等 —— 它们均以这 4 类之一注册），新增写工具无需改这里。
#   write_file    — write_file / edit_file / 各产物写工具 (ppt/excel/doc/pdf/memory…)
#   desktop_write — desktop_create_file 等桌面落盘
#   shell         — run_shell / computer_use GUI 控制（点击/输入/拖拽副作用）
#   skill_install — 安装 skill（写盘 + 改变工具面）
_WRITE_PERMISSION_CATEGORIES: frozenset[str] = frozenset(
    {"write_file", "desktop_write", "shell", "skill_install"}
)


# WI-T4.1 v3 D11: 显式 conflict 错误（registry spec gap 修复）。
# 历史 ``registry.register replaces on duplicate`` 是反模式 — late-loaded
# stubs 会无声覆盖真实现，错误极难调试（last-mile / stage2 都踩过）。
# v3 行为：name 冲突时若双方都未 opt-in `replace_allowed=True` → 抛此异常；
# 任一方 opt-in 则允许覆盖（warn）。
class ToolNameConflictError(RuntimeError):
    """Raised when registering a tool name that already exists and neither
    the existing spec nor the new registration opt-in `replace_allowed=True`.

    Typical fix: pass `replace_allowed=True` explicitly (for test fixtures,
    MCP hot-replace, or stubs.py guard-mode), or rename one of the tools.
    """


def _envelope_indicates_success(handler_result: str) -> bool:
    """P5-S2 Phase 3: was the handler call a "real" success for breaker
    accounting?

    Handlers return JSON strings. By Phase 0 convention, the structured
    payload uses ``{"ok": false, "error": "..."}`` or an error-only
    envelope for known failure modes (missing param / would_overwrite /
    not_found / etc) even when no Python exception was raised. We treat those as breaker failures
    — otherwise an LLM that keeps invoking ``write_file`` with no
    ``path`` parameter would never trip the breaker.

    Anything that doesn't parse as JSON / isn't a dict / has no ``ok``
    field defaults to True (success) — handlers that pre-date Phase 0
    just return raw strings and we don't want to false-trip on them.
    """
    if not isinstance(handler_result, str):
        return True
    try:
        payload = json.loads(handler_result)
    except (ValueError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    ok = payload.get("ok")
    if ok is False or payload.get("error") not in (None, "", False):
        return False
    return True


def _default_effect_policy(name: str, source: str, permission_category: str) -> Any:
    from deskpet.workflows.contracts import EffectKind, EffectPolicy

    if name in _GRAPH_STAGED_FILE_TOOLS:
        kind = EffectKind.STAGED_FILE
    elif (
        source.startswith(("plugin:", "mcp:"))
        or permission_category in _WRITE_PERMISSION_CATEGORIES
        or name in _GRAPH_OPAQUE_WRITE_TOOLS
    ):
        kind = EffectKind.OPAQUE_MANUAL
    else:
        kind = EffectKind.IDEMPOTENT_READ
    return EffectPolicy(
        policy_id=f"deskpet:{name}:{kind.value}",
        version="v1",
        kind=kind,
        max_attempts=1,
    )


def _default_completion_semantics(name: str) -> str:
    return "accepted_async" if name in {"deepresearch", "ppt_pro"} else "sync"


def _target_path_for_call(
    name: str,
    params: dict[str, Any],
    *,
    session_id: str,
    stable_call_id: str,
) -> tuple[str | None, str | None]:
    """Resolve a graph-staged tool's output path and parameter key."""

    key_by_tool = {
        "write_file": "path",
        "edit_file": "path",
        "file_write": "path",
        "doc_create": "output_path",
        "doc_edit": "file_path",
        "excel_create": "output_path",
        "pdf_export": "output_path",
    }
    key = key_by_tool.get(name)
    if name == "desktop_create_file":
        desktop_name = str(params.get("name") or "")
        return (str(Path.home() / "Desktop" / desktop_name), None) if desktop_name else (None, None)
    if key is None:
        return None, None
    value = params.get(key)
    if value:
        return str(value), key
    suffix_by_tool = {
        "doc_create": ".docx",
        "excel_create": ".xlsx",
        "pdf_export": ".pdf",
    }
    suffix = suffix_by_tool.get(name)
    if suffix is None:
        return None, key
    safe_session = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
    safe_call = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stable_call_id)
    generated = Path.cwd() / "outputs" / f"{name}-{safe_session}-{safe_call}{suffix}"
    params[key] = str(generated)
    return str(generated), key


def _default_prepared_targets(
    name: str,
    params: dict[str, Any],
    *,
    session_id: str,
    stable_call_id: str,
) -> tuple[Any, ...]:
    from deskpet.workflows.effects import PreparedTarget, TargetMode

    if name not in _GRAPH_STAGED_FILE_TOOLS:
        return ()
    raw_path, _ = _target_path_for_call(
        name, params, session_id=session_id, stable_call_id=stable_call_id
    )
    if not raw_path:
        return ()
    final = Path(raw_path).expanduser()
    if not final.is_absolute():
        workspace = params.get("_project_root") or params.get("_write_scope_root")
        if workspace:
            final = Path(str(workspace)).expanduser() / final
    final = final.resolve(strict=False)
    exists = final.is_file()
    if name in {"edit_file", "doc_edit"}:
        mode = TargetMode.EDIT
    elif name == "file_write" and str(params.get("mode", "overwrite")) == "append":
        mode = TargetMode.APPEND
    elif exists:
        mode = TargetMode.REPLACE
    else:
        mode = TargetMode.CREATE
    return (
        PreparedTarget.prepare(
            final,
            run_id=session_id,
            stable_call_id=stable_call_id,
            mode=mode,
            format=final.suffix.lower().lstrip(".") or None,
        ),
    )


def _staged_lifecycle_method(method: str) -> LifecycleFn:
    def invoke(*args: Any, **kwargs: Any) -> Any:
        from deskpet.workflows.effects import StagedFileLifecycle

        return getattr(StagedFileLifecycle(), method)(*args, **kwargs)

    return invoke


@dataclass(frozen=True)
class ToolSpec:
    """Immutable bundle of everything needed to expose + run a tool.

    Kept ``frozen=True`` so a stray ``spec.handler = ...`` typo at call
    site fails loudly instead of silently replacing a registered tool.

    P4-S20 v2 additions (all optional, backward-compatible defaults):

    * ``permission_category`` — one of the 7 categories from the
      permission-gate spec. Defaults to ``"read_file"`` (the safest
      default-allow category) so legacy tools registered without the
      kwarg keep dispatching unchanged.
    * ``source`` — provenance string used by audit + uninstall. Format:
      ``"builtin"`` | ``"plugin:<name>"`` | ``"mcp:<server>"``.
    * ``dangerous`` — UI hint to render the popup in red.
    """

    name: str
    toolset: str
    schema: dict[str, Any]
    handler: ToolHandler
    check_fn: Optional[CheckFn] = None
    requires_env: list[str] = field(default_factory=list)
    permission_category: str = "read_file"
    source: str = "builtin"
    dangerous: bool = False
    # P5-S1: per-tool hard timeout in seconds. ``execute_tool`` enforces
    # via ``asyncio.wait_for``. Defaults to 60s; bash_run / long-running
    # MCP tools should override on registration (e.g. 300.0).
    timeout_seconds: float = 60.0
    # WI-T4.1 v3 D11: explicit consent for "this name may be replaced".
    # ToolNameConflictError 抛 iff 同名重复注册且两边都没 opt-in。
    # 历史 stubs.py "register replaces on duplicate" 行为现改为：
    #   - stubs.py 用 "if not registry.has(name): register"（守卫模式）
    #   - 真实现注册（默认 replace_allowed=False）→ 不会被 stub 覆盖
    #   - mcp_call / 测试热替换等显式 replace_allowed=True 时合法覆盖
    replace_allowed: bool = False
    # G3 (companion-code-v2): tool dispatch partition flag.
    #   True  → handler is safe to run concurrently with other safe tools
    #           in the same partition_dispatch batch (read-only / pure /
    #           idempotent queries)。默认 True 兼容历史注册。
    #   False → handler may mutate shared state (filesystem write, DB
    #           write, shell exec) and MUST run serially within its batch.
    # 用法：partition_dispatch() 把 safe 全 asyncio.gather 并发，unsafe 串行 await。
    # 注册写工具时显式传 ``concurrency_safe=False``。
    concurrency_safe: bool = True
    # Dynamic per-turn visibility predicate (see ``VisibilityFn``). ``None``
    # → always visible. Used to hide a tool from the LLM prompt until some
    # runtime precondition holds (e.g. goal_task_* tools stay hidden until the
    # user runs /goal). Only gates *schema* exposure — ``execute_tool`` still
    # dispatches if the LLM somehow calls a hidden tool (handler guards apply).
    visible_when: Optional[VisibilityFn] = None
    visibility_scope: str = "global"
    fixture_epoch: int = 0
    fixture_spec_hash: str = ""
    fixture_spec_version: str = ""
    fixture_remote_name: str = ""
    # Durable workflow metadata. Legacy registrations receive conservative,
    # versioned defaults in ``register``; explicit plugin/MCP opt-in can
    # replace every field without changing the handler contract.
    spec_version: str = "v1"
    schema_hash: str = ""
    permission_policy_version: str = "v1"
    effect_policy: Any = None
    outcome_parser: Optional[OutcomeParser] = None
    outcome_parser_id: str = ""
    outcome_parser_version: str = ""
    outcome_parser_hash: str = ""
    completion_semantics: str = "sync"
    prepare: Optional[PrepareFn] = None
    stage: Optional[LifecycleFn] = None
    validate: Optional[LifecycleFn] = None
    commit: Optional[LifecycleFn] = None
    rollback: Optional[LifecycleFn] = None
    reconcile: Optional[LifecycleFn] = None

    def env_satisfied(self) -> bool:
        """True iff every ``requires_env`` var is present AND non-empty."""
        return all(os.environ.get(e) for e in self.requires_env)

    def is_visible(self, context: Optional[ToolEligibilityContext] = None) -> bool:
        """True iff this tool should appear in the LLM schema list this turn.

        ``visible_when is None`` → always visible (legacy default). A predicate
        that raises is treated as "hide" (fail-closed): we'd rather omit a
        gated tool than expose it on a transient error, since the only callers
        that set a predicate are opt-in features that prefer invisibility when
        their precondition can't be confirmed.
        """
        if self.visible_when is None:
            return True
        try:
            if self.visibility_scope == "session":
                if context is None:
                    # Explicit legacy adapter: old zero-arg predicates keep
                    # their rollback semantics; new context predicates may
                    # choose to accept None for the same path.
                    try:
                        return bool(self.visible_when())
                    except TypeError:
                        return bool(self.visible_when(None))
                return bool(self.visible_when(context))
            return bool(self.visible_when())
        except Exception as exc:  # noqa: BLE001 — never break schema build
            logger.warning(
                "tool %r visible_when predicate raised, hiding: %s",
                self.name, exc,
            )
            return False

    @property
    def description_for_llm(self) -> str:
        """Convenience accessor — pulls ``description`` out of the OpenAI
        function schema. v2 callers can read this without poking into the
        schema dict."""
        return str(self.schema.get("description", ""))

    @property
    def input_schema_json(self) -> dict[str, Any]:
        """Convenience accessor — pulls ``parameters`` out of the schema."""
        return dict(self.schema.get("parameters", {}))


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    revision: int
    specs: tuple[ToolSpec, ...]


def _run_coro_sync(coro: Any) -> Any:
    """把一个 coroutine 在 sync 上下文里跑到底，返回其结果。

    记忆系统升级 WI-M1.6：``dispatch()`` 是 sync 的，但 file 工具 handler
    改成了 async。无 running loop（测试 / smoke / 遗留 sync 调用）→ 直接
    ``asyncio.run``；万一在 running loop 里被调到（不应发生 —— 生产 async
    路走 V2 ``execute_tool``）→ 丢进独立线程各自起 loop 跑，避免
    "loop already running"。
    """
    import asyncio as _asyncio

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)
    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
        return _ex.submit(lambda: _asyncio.run(coro)).result()


class ToolRegistry:
    """Process-wide singleton for tool registration + dispatch.

    Don't instantiate directly in application code — import the module
    level ``registry`` instance instead. Tests do instantiate fresh
    ``ToolRegistry()`` objects to avoid polluting the global one.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._lock = threading.Lock()
        self._catalog_revision = 0
        self._capability_scope_store: Optional[ToolCapabilityScopeStore] = None
        self._context_os_enabled_provider: Callable[[], bool] = lambda: False
        self._context_os_e2e_hooks: Optional[Any] = None
        self._mcp_catalog_stale_callback: Optional[Callable[[str], None]] = None
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
        self._tools_config_provider: Optional[Callable[[], Any]] = None
        # WI-T2.2 (last-mile P0 修): optional ReceiptStore provider 真正插电。
        # 默认 None → 不产 receipt（BC）；main.py 启动时按 cfg.verifier.emit_receipts
        # 决定是否构造 ReceiptStore 并注入。
        self._receipt_store_provider: Optional[Callable[[], Any]] = None
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
        self._prepared_effect_policy_versions: dict[tuple[str, str, str], str] = {}

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

    def set_capability_scope_store(
        self, store: Optional[ToolCapabilityScopeStore]
    ) -> None:
        self._capability_scope_store = store

    @property
    def capability_scope_store(self) -> Optional[ToolCapabilityScopeStore]:
        return self._capability_scope_store

    def set_context_os_enabled_provider(self, provider: Callable[[], bool]) -> None:
        self._context_os_enabled_provider = provider

    def set_context_os_e2e_hooks(self, hooks: Optional[Any]) -> None:
        self._context_os_e2e_hooks = hooks

    def set_mcp_catalog_stale_callback(
        self, callback: Optional[Callable[[str], None]]
    ) -> None:
        self._mcp_catalog_stale_callback = callback

    def invalidate_mcp_catalog_sources(self, sources: Iterable[str]) -> None:
        """Atomically invalidate whole MCP server catalogs and reconnect them."""
        source_set = {str(source) for source in sources if str(source).startswith("mcp:")}
        if not source_set:
            return
        with self._lock:
            stale_names = [
                name for name, spec in self._tools.items() if spec.source in source_set
            ]
            for name in stale_names:
                self._tools.pop(name, None)
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
    def context_os_e2e_hooks(self) -> Optional[Any]:
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

    def get_session_context(self, session_id: str) -> dict[str, Any]:
        """Read-only snapshot of the session's tool-arg context."""
        return dict(self._session_context.get(session_id, {}))

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
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        check_fn: Optional[CheckFn] = None,
        requires_env: Optional[list[str]] = None,
        permission_category: str = "read_file",
        source: str = "builtin",
        dangerous: bool = False,
        timeout_seconds: float = 60.0,
        replace_allowed: bool = False,
        concurrency_safe: bool = True,
        visible_when: Optional[VisibilityFn] = None,
        visibility_scope: str = "global",
        fixture_epoch: int = 0,
        fixture_spec_hash: str = "",
        fixture_spec_version: str = "",
        fixture_remote_name: str = "",
        spec_version: str = "v1",
        permission_policy_version: str = "v1",
        effect_policy: Any = None,
        outcome_parser: Optional[OutcomeParser] = None,
        outcome_parser_id: Optional[str] = None,
        outcome_parser_version: str = _OUTCOME_PARSER_VERSION,
        outcome_parser_hash: Optional[str] = None,
        completion_semantics: Optional[str] = None,
        prepare: Optional[PrepareFn] = None,
        stage: Optional[LifecycleFn] = None,
        validate: Optional[LifecycleFn] = None,
        commit: Optional[LifecycleFn] = None,
        rollback: Optional[LifecycleFn] = None,
        reconcile: Optional[LifecycleFn] = None,
    ) -> None:
        """Register a single tool.

        Name conflict policy (WI-T4.1 v3):
          - 默认 ``replace_allowed=False``: 重复 name 且 existing spec 也未
            opt-in → 抛 :class:`ToolNameConflictError`
          - 任一方 (existing 或 new) ``replace_allowed=True`` → 允许覆盖
            （仅 log.warning）
          - stubs.py 应改用守卫模式 ``if not registry.has(name): register``，
            真实现注册时不再被 stub 覆盖

        The ``schema`` argument is the raw OpenAI ``function`` object
        (``{name, description, parameters}``). ``schemas()`` wraps each
        with the outer ``{type: "function", function: ...}`` envelope,
        so callers don't need to repeat it here.

        Args:
            replace_allowed: 显式声明"这个名字允许被覆盖"。两边都未 opt-in
                时同名注册 raise；仅一边 True 也允许覆盖（用于 MCP 热重连 /
                测试 fixture / stubs.py 守卫模式）。
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"tool name must be non-empty str, got {name!r}")
        if not toolset or not isinstance(toolset, str):
            raise ValueError(f"toolset must be non-empty str, got {toolset!r}")
        if not isinstance(schema, dict):
            raise TypeError(f"schema must be dict, got {type(schema).__name__}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not spec_version or not permission_policy_version:
            raise ValueError("tool spec and permission policy versions are required")
        if visibility_scope not in {"global", "session"}:
            raise ValueError("visibility_scope must be global or session")
        if completion_semantics not in {None, "sync", "accepted_async"}:
            raise ValueError("completion_semantics must be sync or accepted_async")

        # WI-T4.2 v3 spec D3：plugin 工具自动加 ``<plugin>:`` 前缀防 namespace
        # 冲突（两个 plugin 注册同名 tool 时第二个会因 ToolNameConflictError 崩）。
        # 触发条件：source 形如 ``plugin:<name>`` 或 ``mcp:<server>`` 且 name 还
        # 没带前缀。schema["name"] 也同步改，让 LLM 看到的就是 qualified name。
        # 注意：mcp/manager.py 已手动构造 ``mcp_<server>_<tool>``，本逻辑对其是
        # no-op（name 检测已含前缀直接跳过）。
        qualified_name = name
        if isinstance(source, str) and ":" in source:
            prefix_kind, _, prefix_id = source.partition(":")
            if prefix_kind in ("plugin", "mcp") and prefix_id:
                expected_prefix = f"{prefix_kind}_{prefix_id}_"
                # 已有以下任一前缀时跳过（防重复 / 兼容历史命名）：
                #   1. expected_prefix (本逻辑加过)
                #   2. f"{prefix_kind}:"  (理论 caller 已用 dotted 形式)
                #   3. f"{prefix_id}:"    (P4-S20 旧约定 <plugin_id>:<tool>)
                #   4. f"{prefix_id}_"    (mcp/manager.py 真实加的 mcp_<name>_<tool>
                #                          会落到 prefix_kind="mcp"+prefix_id=<name>,
                #                          name 已是 mcp_<name>_<tool> → 含 prefix_id_)
                if (
                    not name.startswith(expected_prefix)
                    and not name.startswith(f"{prefix_kind}:")
                    and not name.startswith(f"{prefix_id}:")
                ):
                    qualified_name = f"{expected_prefix}{name}"
                    # schema 的 name 字段同步（LLM 看到 qualified name 用 dispatch）
                    if isinstance(schema.get("name"), str):
                        schema = {**schema, "name": qualified_name}
                    logger.info(
                        "registry: auto-prefixed plugin tool %r → %r (source=%s)",
                        name, qualified_name, source,
                    )

        resolved_policy = effect_policy or _default_effect_policy(
            qualified_name, source, permission_category
        )
        resolved_parser_id = outcome_parser_id or _default_parser_id(
            qualified_name, source, permission_category
        )
        resolved_parser_hash = outcome_parser_hash or _parser_hash(
            resolved_parser_id, outcome_parser_version
        )
        is_staged = getattr(getattr(resolved_policy, "kind", None), "value", None) == "staged_file"
        if is_staged:
            stage = stage or _staged_lifecycle_method("stage")
            commit = commit or _staged_lifecycle_method("commit")
            rollback = rollback or _staged_lifecycle_method("rollback")
            reconcile = reconcile or _staged_lifecycle_method("reconcile")

        spec = ToolSpec(
            name=qualified_name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=list(requires_env or []),
            permission_category=permission_category,
            source=source,
            dangerous=dangerous,
            timeout_seconds=float(timeout_seconds),
            replace_allowed=replace_allowed,
            concurrency_safe=bool(concurrency_safe),
            visible_when=visible_when,
            visibility_scope=visibility_scope,
            fixture_epoch=int(fixture_epoch or 0),
            fixture_spec_hash=str(fixture_spec_hash or ""),
            fixture_spec_version=str(fixture_spec_version or ""),
            fixture_remote_name=str(fixture_remote_name or ""),
            spec_version=spec_version,
            schema_hash=_schema_hash(schema),
            permission_policy_version=permission_policy_version,
            effect_policy=resolved_policy,
            outcome_parser=outcome_parser,
            outcome_parser_id=resolved_parser_id,
            outcome_parser_version=outcome_parser_version,
            outcome_parser_hash=resolved_parser_hash,
            completion_semantics=(
                completion_semantics or _default_completion_semantics(qualified_name)
            ),
            prepare=prepare,
            stage=stage,
            validate=validate,
            commit=commit,
            rollback=rollback,
            reconcile=reconcile,
        )
        # 后续 dict 查 / 冲突检测都用 qualified_name
        name = qualified_name
        with self._lock:
            existing = self._tools.get(name)
            if existing is not None:
                # WI-T4.1 v3 D11: 同名重注册策略
                if not (existing.replace_allowed or replace_allowed):
                    raise ToolNameConflictError(
                        f"Tool {name!r} already registered "
                        f"(toolset={existing.toolset}, source={existing.source}). "
                        f"Set replace_allowed=True on either registration to "
                        f"allow override (e.g. stubs.py guard-mode), or rename "
                        f"one of the tools."
                    )
                logger.warning(
                    "tool %r re-registered (toolset=%s → %s, source=%s → %s); "
                    "previous definition replaced (replace_allowed opt-in)",
                    name, existing.toolset, toolset, existing.source, source,
                )
            self._tools[name] = spec
            self._catalog_revision += 1

    def has(self, name: str) -> bool:
        """Return True iff a tool with this name is currently registered.

        Stubs.py 守卫模式：``if not registry.has(name): register(...)``
        防止 late-loaded stub 覆盖 already-registered 真实现。
        """
        with self._lock:
            return name in self._tools

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if
        the name was absent. Used by MCPManager to drop a server's
        tools on disconnect (P4-S9 task 14.5 + 14.6).
        """
        with self._lock:
            removed = self._tools.pop(name, None)
            if removed is not None:
                self._catalog_revision += 1
            return removed is not None

    # ------------------------------------------------------------------
    # Schema export
    # ------------------------------------------------------------------
    def catalog_snapshot(self) -> ToolCatalogSnapshot:
        """Copy a single immutable catalog view under the registry lock."""
        with self._lock:
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def eligible_specs(
        self,
        *,
        context: ToolEligibilityContext,
        policy_snapshot: ToolPolicySnapshot,
        catalog: Optional[ToolCatalogSnapshot] = None,
        enabled_toolsets: Optional[Sequence[str]] = None,
    ) -> tuple[ToolSpec, ...]:
        """Single host/session eligibility owner for the Context OS path."""
        snapshot = catalog or self.catalog_snapshot()
        allowed = set(enabled_toolsets) if enabled_toolsets is not None else None
        out: list[ToolSpec] = []
        for spec in snapshot.specs:
            if not spec.env_satisfied() or not spec.is_visible(context):
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            if spec.toolset in policy_snapshot.disabled_toolsets:
                continue
            if spec.toolset in policy_snapshot.schema_only_toolsets:
                continue
            if (
                policy_snapshot.dangerous_allowlist
                and spec.dangerous
                and spec.name not in policy_snapshot.dangerous_allowlist
            ):
                continue
            out.append(spec)
        return tuple(out)

    def validate_prepared_tool_set(
        self,
        prepared: Any,
        *,
        eligibility: ToolEligibilityContext,
    ) -> None:
        policy = self.read_policy_snapshot(strict=True)
        if policy.fingerprint != prepared.policy_fingerprint:
            raise RuntimeError("capability_stale")
        with self._lock:
            current = dict(self._tools)
        fixture_specs: list[ToolSpec] = []
        for capability in (*prepared.direct, *prepared.activated):
            spec = current.get(capability.ref.name)
            stale_reason = ""
            if spec is None:
                stale_reason = "missing"
            elif spec.schema_hash != capability.ref.schema_hash:
                stale_reason = "schema_hash"
            elif spec.spec_version != capability.ref.spec_version:
                stale_reason = "spec_version"
            elif (
                spec.permission_policy_version
                != capability.ref.permission_policy_version
            ):
                stale_reason = "permission_policy_version"
            elif not spec.env_satisfied():
                stale_reason = "environment"
            elif not spec.is_visible(eligibility):
                stale_reason = "visibility"
            elif spec.toolset in policy.disabled_toolsets:
                stale_reason = "disabled_toolset"
            elif (
                policy.dangerous_allowlist
                and spec.dangerous
                and spec.name not in policy.dangerous_allowlist
            ):
                stale_reason = "dangerous_allowlist"
            if stale_reason:
                logger.warning(
                    "prepared_tool_catalog_stale tool=%s reason=%s session_id=%s request_id=%s",
                    capability.ref.name,
                    stale_reason,
                    eligibility.session_id,
                    eligibility.request_id,
                )
                raise RuntimeError("tool_catalog_stale")
            if spec.fixture_spec_hash:
                fixture_specs.append(spec)
        if self._context_os_e2e_hooks is not None and fixture_specs:
            remote_names = [spec.fixture_remote_name or spec.name for spec in fixture_specs]
            try:
                meta = self._context_os_e2e_hooks.catalog(remote_names)
            except RuntimeError as exc:
                self.invalidate_mcp_catalog_sources(spec.source for spec in fixture_specs)
                raise RuntimeError("tool_catalog_stale") from exc
            remote = meta.get("tools") if isinstance(meta, dict) else None
            stale = not isinstance(remote, dict)
            if not stale:
                for spec in fixture_specs:
                    item = remote.get(spec.fixture_remote_name or spec.name)
                    if (
                        not isinstance(item, dict)
                        or int(item.get("fixture_epoch", -1)) != spec.fixture_epoch
                        or str(item.get("fixture_spec_hash", "")) != spec.fixture_spec_hash
                        or str(item.get("fixture_spec_version", "")) != spec.fixture_spec_version
                    ):
                        logger.warning(
                            "fixture_tool_catalog_stale tool=%s remote=%s "
                            "expected_epoch=%s actual_epoch=%s "
                            "expected_hash=%s actual_hash=%s "
                            "expected_version=%s actual_version=%s",
                            spec.name,
                            spec.fixture_remote_name or spec.name,
                            spec.fixture_epoch,
                            item.get("fixture_epoch") if isinstance(item, dict) else None,
                            spec.fixture_spec_hash[:12],
                            str(item.get("fixture_spec_hash", ""))[:12]
                            if isinstance(item, dict) else None,
                            spec.fixture_spec_version,
                            item.get("fixture_spec_version")
                            if isinstance(item, dict) else None,
                        )
                        stale = True
                        break
            if stale:
                self.invalidate_mcp_catalog_sources(spec.source for spec in fixture_specs)
                raise RuntimeError("tool_catalog_stale")

    def schemas(
        self, enabled_toolsets: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """Return OpenAI-format schema list.

        Filtering rules (applied in order):
          1. ``requires_env`` — any missing/empty env var hides the tool
             so the LLM never sees a feature it can't invoke.
          1b. ``visible_when`` — per-turn dynamic predicate; False (or a
             raised exception) hides the tool. Used to keep goal_task_*
             tools out of the prompt until a /goal is active.
          2. ``enabled_toolsets`` — if provided, only tools whose
             ``toolset`` is in the whitelist survive. ``None`` (the
             default) returns everything.
          3. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets`` — 强 strict 模式
             过滤；同时 schema 层 + execute_tool 层都挡（默认双层）。
          4. ★v3 WI-T5.1：``cfg.tools.disabled_toolsets_schema_only`` —
             opt-in 仅 schema 层挡（execute_tool 仍可调）。
          5. ★v3 WI-T5.1：``cfg.tools.dangerous_tools_allowlist`` —
             非空时过 dangerous=True 工具白名单。
        """
        allowed: Optional[set[str]] = (
            set(enabled_toolsets) if enabled_toolsets is not None else None
        )

        # WI-T5.1 v3：cfg.tools 字段（cfg provider 已注入）
        cfg_disabled: set[str] = set()
        cfg_disabled_schema_only: set[str] = set()
        dangerous_allowlist: set[str] = set()
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                cfg_disabled = set(getattr(cfg, "disabled_toolsets", []) or [])
                cfg_disabled_schema_only = set(
                    getattr(cfg, "disabled_toolsets_schema_only", []) or []
                )
                dangerous_allowlist = set(
                    getattr(cfg, "dangerous_tools_allowlist", []) or []
                )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("tools_config_provider read failed in schemas: %s", _exc)

        with self._lock:
            specs = list(self._tools.values())

        out: list[dict[str, Any]] = []
        for spec in specs:
            if not spec.env_satisfied():
                continue
            # Dynamic per-turn visibility (e.g. goal_task_* hidden until a
            # /goal is active). Keeps gated tools out of the LLM prompt so
            # the model isn't tempted to call a feature with no precondition.
            if not spec.is_visible():
                continue
            if allowed is not None and spec.toolset not in allowed:
                continue
            # WI-T5.1 v3：cfg.disabled_toolsets / _schema_only 双层挡
            if spec.toolset in cfg_disabled:
                continue
            if spec.toolset in cfg_disabled_schema_only:
                continue
            # WI-T5.1 v3：dangerous allowlist — 非空时仅 allowlist 中 dangerous 工具
            if dangerous_allowlist and spec.dangerous and spec.name not in dangerous_allowlist:
                continue
            out.append({"type": "function", "function": dict(spec.schema)})
        return out

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def dispatch(
        self, name: str, args: dict[str, Any], task_id: str = ""
    ) -> str:
        """Invoke a tool by name. Always returns a JSON string.

        * Unknown tool → ``{"error":"unknown tool: <name>","retriable":false}``
        * ``check_fn`` returns False → retriable ``tool not ready`` error
        * Handler raises → ``{"error":"<ExcClass>: <msg>","retriable":<classified>}``
        * Handler returns non-string → stringified via ``json.dumps``;
          already-string return passed through verbatim (handlers are
          expected to produce valid JSON, but we don't re-parse it —
          re-serializing a valid JSON string would wrap it in quotes).

        Handler execution runs outside the internal lock so a slow tool
        (e.g. ``web_fetch``) never blocks another dispatch on a different
        thread.
        """
        with self._lock:
            spec = self._tools.get(name)

        if spec is None:
            return json.dumps(
                {"error": f"unknown tool: {name}", "retriable": False}
            )

        if spec.check_fn is not None:
            try:
                ready = bool(spec.check_fn())
            except Exception as exc:  # noqa: BLE001 — check_fn must never break dispatch
                logger.warning(
                    "tool %r check_fn raised %s; treating as not-ready",
                    name,
                    type(exc).__name__,
                )
                ready = False
            if not ready:
                return json.dumps(
                    {
                        "error": f"tool not ready: {name}",
                        "retriable": True,
                    }
                )

        try:
            result = spec.handler(dict(args or {}), task_id)
            # 记忆系统升级 WI-M1.6：file_read/file_write handler 改成
            # async（直接 await record_action）。sync 的 dispatch() 路径
            # （遗留 fallback + 测试 + smoke 脚本）需把 coroutine 跑到底。
            # 生产 code-mode 走 V2 registry.execute_tool（原生 async 分流），
            # 不经此处。
            import inspect as _inspect2
            if _inspect2.iscoroutine(result):
                result = _run_coro_sync(result)
        except Exception as exc:  # noqa: BLE001 — everything caught by design
            retriable = _classify_retriable(exc)
            err = f"{type(exc).__name__}: {exc}"
            logger.info(
                "tool %r raised (retriable=%s): %s", name, retriable, err
            )
            return json.dumps({"error": err, "retriable": retriable})

        if isinstance(result, str):
            return result
        # Handlers are expected to return strings; accept dict/list as
        # a convenience and serialize. Anything not JSON-encodable
        # surfaces as a non-retriable error (it's a programmer bug in
        # the handler).
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {
                    "error": f"handler returned non-JSON value: {exc}",
                    "retriable": False,
                }
            )

    # ------------------------------------------------------------------
    # P4-S20 v2: tool_use protocol schema generation + gated execution
    # ------------------------------------------------------------------
    def to_openai_schema(
        self,
        names: Optional[list[str]] = None,
        filter_categories: Optional[list[str]] = None,
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

    def to_anthropic_schema(
        self,
        names: Optional[list[str]] = None,
        filter_categories: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Anthropic Messages API tool schema list.

        Anthropic uses ``input_schema`` (not OpenAI's ``parameters``).
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
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description_for_llm,
                    "input_schema": spec.input_schema_json,
                }
            )
        return out

    # Ollama uses the OpenAI-compatible shape; alias for clarity.
    to_ollama_schema = to_openai_schema

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
        *,
        execution_context: Optional[ToolExecutionContext] = None,
    ) -> dict[str, Any]:
        """Permission-gated async tool execution.

        Wraps the legacy ``dispatch()`` with three guarantees:
          1. Looks up the spec; unknown tool → ``{ok: False, error: ...}``
          2. Awaits ``PermissionGate.check`` (if a gate is wired). Deny
             → handler is NOT called.
          3. Runs the handler under a try/except so handler exceptions
             surface as ``{ok: False, error: "..."}`` rather than
             propagating.

        Return shape: ``{"ok": bool, "result": str | None, "error": str | None}``.
        ``result`` is whatever the handler returned (typically a JSON string).
        """
        with self._lock:
            spec = self._tools.get(name)
        if spec is None:
            return {"ok": False, "result": None, "error": f"unknown tool: {name}"}

        context_os_on = bool(self._context_os_enabled_provider())
        active_policy: Optional[ToolPolicySnapshot] = None
        if context_os_on:
            if execution_context is None or execution_context.origin != "agent":
                return {
                    "ok": False,
                    "result": None,
                    "error": "capability_denied: missing execution context",
                }
            if (
                execution_context.session_id != session_id
                or not execution_context.scope_id
            ):
                return {"ok": False, "result": None, "error": "capability_denied"}
            if self._capability_scope_store is None:
                return {
                    "ok": False,
                    "result": None,
                    "error": "tool_capability_runtime_unavailable",
                }
            try:
                active_policy = self.read_policy_snapshot(strict=True)
            except RuntimeError:
                return {"ok": False, "result": None, "error": "tool_policy_unavailable"}
            record = self._capability_scope_store.get(
                execution_context.scope_id,
                session_id=execution_context.session_id,
                request_id=execution_context.request_id,
            )
            if record is None:
                return {"ok": False, "result": None, "error": "capability_denied"}
            prepared = record.prepared.capability(name)
            if prepared is None:
                return {"ok": False, "result": None, "error": "capability_denied"}
            if active_policy.fingerprint != record.prepared.policy_fingerprint:
                return {"ok": False, "result": None, "error": "capability_stale"}
            if (
                prepared.ref.schema_hash != spec.schema_hash
                or prepared.ref.spec_version != spec.spec_version
                or prepared.ref.permission_policy_version
                != spec.permission_policy_version
                or not spec.env_satisfied()
                or not spec.is_visible(record.eligibility)
                or spec.toolset in active_policy.disabled_toolsets
                or (
                    active_policy.dangerous_allowlist
                    and spec.dangerous
                    and spec.name not in active_policy.dangerous_allowlist
                )
            ):
                return {"ok": False, "result": None, "error": "capability_stale"}

        # WI-T5.1 v3：disabled_toolsets 双层挡 — strict 模式下 execute_tool
        # 也拒绝（schema_only 仅 schemas() 过滤，execute_tool 仍可调）。
        # Context OS already evaluated the immutable strict snapshot above.
        if not context_os_on and self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                disabled_strict = set(getattr(cfg, "disabled_toolsets", []) or [])
                if spec.toolset in disabled_strict:
                    return {
                        "ok": False,
                        "result": None,
                        "error": (
                            f"tool {name!r} disabled by [tools] "
                            f"disabled_toolsets (toolset={spec.toolset})"
                        ),
                    }
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "tools_config_provider read failed in execute_tool: %s", _exc,
                )

        # WI-CC-2 (plan mode 物理只读): 规划期对写/执行类工具硬拦。
        # set_plan_read_only(sid, True) 期间（main.py 在 plan-confirm 硬门挂起
        # 时置位），任何 permission_category 属写类集合的工具直接 deny，handler
        # **不执行** —— 把「流程提示」升级为「物理只读」。只读工具放行。
        # 默认无 session 处于只读（features.plan_read_only OFF → main.py 永不置
        # 位）→ 此分支 short-circuit → 字节级 BC。
        if (
            session_id in self._plan_read_only_sessions
            and spec.permission_category in _WRITE_PERMISSION_CATEGORIES
        ):
            logger.info(
                "plan_read_only_deny sid=%s tool=%s category=%s",
                session_id, name, spec.permission_category,
            )
            return {
                "ok": False,
                "result": None,
                "error": (
                    f"规划期只读：工具 {name!r}（{spec.permission_category}）"
                    "在计划确认前不可执行。请先批准执行计划（点[执行]）再调用写类工具。"
                ),
            }

        # P5-S2 Phase 3: per-(session, tool) circuit breaker. If the
        # breaker is OPEN we synthesize a structured ``circuit_open``
        # envelope so the LLM sees a real tool_result with a hint and
        # alternatives — much better than silently retrying the broken
        # tool until max_iterations.
        if self._breaker is not None:
            allowed = await self._breaker.can_call(session_id, name)
            if not allowed:
                return await self._build_circuit_open_envelope(name, session_id, spec)

        if self._gate is not None:
            decision = await self._gate.check(
                category=spec.permission_category,
                params=params,
                session_id=session_id,
            )
            if not decision.allow:
                return {
                    "ok": False,
                    "result": None,
                    "error": f"permission denied (source={decision.source})",
                }

        # P4-S22: merge per-session context into params. LLM-supplied
        # values win on key collision so the LLM can override (e.g.
        # explicitly passing ``path`` to glob the user's home dir
        # instead of the project root).
        merged_params: dict[str, Any] = {}
        # MCP is a serialization and trust boundary. Host-only session
        # objects (for example ``_image_worker``) are implementation details
        # for local handlers and must never be forwarded to a remote MCP
        # process. MCP handlers receive only the arguments exposed in their
        # schema and explicitly supplied by the model.
        if not spec.source.startswith("mcp:"):
            merged_params.update(self._session_context.get(session_id, {}))
        merged_params.update(dict(params or {}))

        # P4-S22: run sync handlers in a thread executor. Some new
        # Code-mode tools (todo_write, agent) need to bridge sync→async
        # via ``asyncio.run_coroutine_threadsafe``, which deadlocks when
        # called from the main event-loop thread (the handler blocks
        # waiting for a coro that can't dispatch because the thread is
        # blocked). Running every sync handler in a worker thread is
        # cheap and uniformly safe; handlers that are already async
        # (rare) get awaited directly.
        # P5-S1: tool-level hard timeout. Default 60s; specific tools may
        # override via ``ToolSpec.timeout_seconds`` (e.g. bash_run = 300s).
        # On timeout: return a uniform ``tool_timeout`` error envelope so
        # the agent loop carries on instead of dying with TimeoutError.
        #
        # WI-T2.3 v3 P0 修：dispatch 真实开始时间。原 emit_receipt 处用了两次
        # datetime.now() → duration_ms 永远 ~0μs（last-mile round2 P0-3）。
        # 这里捕真 started_at，emit_receipt 时用它对账 ended_at。
        from datetime import datetime as _dt, timezone as _tz
        _started_at = _dt.now(_tz.utc)
        try:
            import asyncio as _asyncio
            import inspect as _inspect

            # WI-T5.1 v3 default_timeout_seconds：cfg 兜底 ToolSpec 未配 timeout
            # 时的默认值。ToolSpec.timeout_seconds 默认 60.0，cfg 60.0 → 与
            # 现状字节级一致；用户在 [tools] default_timeout_seconds=30 时
            # 全局缩短未显式 override 的工具 timeout。
            cfg_default_timeout = 60.0
            if self._tools_config_provider is not None:
                try:
                    cfg = self._tools_config_provider()
                    cfg_default_timeout = float(
                        getattr(cfg, "default_timeout_seconds", 60.0) or 60.0
                    )
                except Exception:  # noqa: BLE001
                    pass
            timeout_s = float(getattr(spec, "timeout_seconds", cfg_default_timeout)) or cfg_default_timeout

            async def _run_handler() -> Any:
                host_context = execution_context
                if host_context is not None and active_policy is not None:
                    host_context = ToolExecutionContext(
                        scope_id=host_context.scope_id,
                        session_id=host_context.session_id,
                        request_id=host_context.request_id,
                        origin=host_context.origin,
                        policy_snapshot=active_policy,
                    )
                token = (
                    set_tool_execution_context(host_context)
                    if host_context is not None
                    else None
                )
                try:
                    if _inspect.iscoroutinefunction(spec.handler):
                        return await spec.handler(merged_params, task_id)
                    loop = _asyncio.get_running_loop()
                    copied = contextvars.copy_context()
                    return await loop.run_in_executor(
                        None, copied.run, spec.handler, merged_params, task_id
                    )
                finally:
                    if token is not None:
                        reset_tool_execution_context(token)

            try:
                result = await _asyncio.wait_for(_run_handler(), timeout=timeout_s)
            except _asyncio.TimeoutError:
                logger.warning(
                    "execute_tool %r timed out after %.1fs", name, timeout_s
                )
                if self._breaker is not None:
                    await self._breaker.record_call(session_id, name, ok=False)
                return {
                    "ok": False,
                    "result": None,
                    "error": f"tool_timeout: {name} exceeded {timeout_s:.0f}s",
                }
        except Exception as exc:  # noqa: BLE001 — uniform error envelope
            err = f"{type(exc).__name__}: {exc}"
            logger.info("execute_tool %r raised: %s", name, err)
            if self._breaker is not None:
                await self._breaker.record_call(session_id, name, ok=False)
            return {"ok": False, "result": None, "error": err}

        if not isinstance(result, str):
            try:
                result = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                envelope_bad = {
                    "ok": False,
                    "result": None,
                    "error": f"handler returned non-JSON value: {exc}",
                }
                if self._breaker is not None:
                    await self._breaker.record_call(session_id, name, ok=False)
                return envelope_bad
        envelope = {"ok": True, "result": result, "error": None}

        # WI-T1.1 last-mile: 信封包装（PRD §3 D1）。
        # 仅在 provider 已设 + cfg.tools.last_mile.artifact_envelope=True
        # 且 result 含可推断 path/url 时，追加 ``artifacts`` 键。
        # 字节级一致硬保证：flag OFF 时 envelope dict 不含 ``artifacts`` 键
        # （不是空数组，是缺键 —— 见 TG-2 T2-5b）。
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                envelope_on = bool(
                    getattr(getattr(cfg, "last_mile", None),
                            "artifact_envelope", False)
                )
                if envelope_on:
                    from deskpet.tools.artifact import maybe_add_artifacts
                    envelope = maybe_add_artifacts(
                        envelope=envelope, tool_name=name, enable=True,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "tools_config_provider raised in execute_tool %r: %s",
                    name, _exc,
                )

        # WI-T2.2 P0 修：emit receipt（PRD §3 D5 + 二轮 P0-1 接电）。
        # ReceiptStore 在 main.py 启动期按 cfg.verifier.emit_receipts 构造并
        # set_receipt_store_provider 注入；未注入则 BC 路径不产 receipt。
        if self._receipt_store_provider is not None:
            try:
                store = self._receipt_store_provider()
                if store is not None:
                    from deskpet.tools.receipt_store import emit_receipt
                    self._session_iteration[session_id] = (
                        self._session_iteration.get(session_id, 0) + 1
                    )
                    iteration = self._session_iteration[session_id]
                    # The outer registry envelope only says that Python
                    # dispatch completed.  A handler can still return a
                    # structured domain failure without raising, so receipts
                    # use the same outcome classifier as the breaker.
                    envelope_ok = _envelope_indicates_success(result)
                    # WI-T2.3 v3 P0 修：用真实 _started_at（dispatch 开始时记
                    # 录）+ now() 算 duration_ms。原 v2.1 用两次 now() 间隔仅
                    # 微秒，导致 receipt duration_ms ~0 → p95 监控失效。
                    receipt_args = {
                        k: v for k, v in dict(merged_params or {}).items()
                        if not (isinstance(k, str) and k.startswith("_"))
                    }
                    try:
                        receipt_args = json.loads(json.dumps(
                            receipt_args, ensure_ascii=False, default=str
                        ))
                    except (TypeError, ValueError):
                        receipt_args = {
                            str(k): str(v) for k, v in receipt_args.items()
                        }
                    # Bug#3 子问题修 (2026-06-11)：envelope 有 artifacts 时把
                    # 产物 sha256 抄进 receipt（原从不传 → receipt.artifacts
                    # 恒 []，verify gate 无法对账「声称产物 vs 真产物」）。
                    # 仅 file 产物且文件可读才算；失败静默跳过不破 dispatch。
                    _artifact_shas: Optional[list[str]] = None
                    _env_arts = envelope.get("artifacts")
                    if not (isinstance(_env_arts, list) and _env_arts) and envelope_ok:
                        # Receipt authority is not controlled by the public
                        # artifact-envelope compatibility flag.  Infer the
                        # same paths privately so Context OS can preserve a
                        # real artifact identity even when response bytes must
                        # retain the legacy {ok,result,error} shape.
                        from deskpet.tools.artifact import extract_artifacts_from_result

                        _env_arts = [
                            artifact.to_dict()
                            for artifact in extract_artifacts_from_result(
                                tool_name=name,
                                result_json=result,
                            )
                        ]
                    if isinstance(_env_arts, list) and _env_arts:
                        import hashlib as _hashlib
                        from pathlib import Path as _Path

                        _shas: list[str] = []
                        _workspace = (
                            merged_params.get("_project_root")
                            or merged_params.get("_write_scope_root")
                        )
                        _workspace_root = None
                        if _workspace:
                            try:
                                _workspace_root = _Path(str(_workspace)).resolve()
                            except OSError:
                                _workspace_root = None
                        for _a in _env_arts:
                            _p = _a.get("path") if isinstance(_a, dict) else None
                            if not _p:
                                continue
                            try:
                                _artifact_path = _Path(str(_p))
                                if not _artifact_path.is_absolute():
                                    # Relative paths are meaningful only
                                    # against the session's explicit workspace;
                                    # never fall back to the process CWD.
                                    if _workspace_root is None:
                                        continue
                                    _artifact_path = _workspace_root / _artifact_path
                                _artifact_path = _artifact_path.resolve(strict=True)
                                if _workspace_root is not None:
                                    try:
                                        _artifact_path.relative_to(_workspace_root)
                                    except ValueError:
                                        # Reject traversal and symlink escapes.
                                        continue
                                _h = _hashlib.sha256()
                                with _artifact_path.open("rb") as _f:
                                    for _chunk in iter(
                                        lambda: _f.read(65536), b""
                                    ):
                                        _h.update(_chunk)
                                _digest = _h.hexdigest()
                                if _digest not in _shas:
                                    _shas.append(_digest)
                            except OSError:
                                continue
                        _artifact_shas = _shas or None
                    emit_receipt(
                        store,
                        tool_name=name,
                        args=receipt_args,
                        started_at=_started_at,
                        ended_at=_dt.now(_tz.utc),
                        ok=envelope_ok,
                        session_id=session_id,
                        iteration=iteration,
                        artifact_shas=_artifact_shas,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "receipt_store_provider raised in execute_tool %r: %s",
                    name, _exc,
                )

        # P5-S2 Phase 3: record success/failure to the breaker. The
        # handler returned a JSON string (typically an envelope itself)
        # — if THAT envelope says ``ok=false`` we count it as a failure
        # even though the Python call didn't raise.
        if self._breaker is not None:
            outcome_ok = _envelope_indicates_success(result)
            await self._breaker.record_call(session_id, name, ok=outcome_ok)
        return envelope

    # ------------------------------------------------------------------
    # Durable workflow adapter APIs
    # ------------------------------------------------------------------
    def prepare_call(
        self,
        tool_name: str,
        raw_params: Mapping[str, Any],
        session_id: str,
        stable_call_id: str,
    ) -> Any:
        """Freeze one tool call after context/default/path resolution.

        The legacy handler contract remains ``handler(dict, task_id)``. This
        method only snapshots the exact arguments and capability versions that
        a durable workflow will later recheck before dispatch.
        """

        from deskpet.workflows.effects import PreparedToolCall

        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        with self._lock:
            spec = self._tools.get(tool_name)
        if spec is None:
            raise KeyError(f"unknown tool: {tool_name}")

        params: dict[str, Any] = {}
        params.update(self._session_context.get(session_id, {}))
        params.update(dict(raw_params or {}))
        params = _schema_defaults(spec.schema, params)
        input_blob_hashes = tuple(
            str(item) for item in params.pop("_input_blob_hashes", ()) or ()
        )
        targets: Sequence[Any]
        if spec.prepare is not None:
            prepared_result = spec.prepare(dict(params), session_id, stable_call_id)
            if isinstance(prepared_result, PreparedToolCall):
                prepared = prepared_result
                if prepared.tool_name != tool_name or prepared.stable_call_id != stable_call_id:
                    raise ValueError("custom prepare returned a mismatched call identity")
                cache_key = (tool_name, stable_call_id, prepared.args_hash)
                self._prepared_session_ids[cache_key] = session_id
                self._prepared_effect_policy_versions[cache_key] = str(
                    getattr(spec.effect_policy, "version", "")
                )
                return prepared
            final_params, targets = prepared_result
            params = dict(final_params)
        else:
            targets = _default_prepared_targets(
                tool_name,
                params,
                session_id=session_id,
                stable_call_id=stable_call_id,
            )

        effect_type = str(getattr(getattr(spec.effect_policy, "kind", None), "value", None) or "opaque_manual")
        prepared = PreparedToolCall.prepare(
            tool_name=tool_name,
            stable_call_id=stable_call_id,
            final_params=params,
            prepared_targets=tuple(targets),
            tool_spec_version=spec.spec_version,
            schema_hash=spec.schema_hash,
            permission_policy_version=spec.permission_policy_version,
            effect_type=effect_type,
            input_blob_hashes=input_blob_hashes,
        )
        cache_key = (tool_name, stable_call_id, prepared.args_hash)
        self._prepared_session_ids[cache_key] = session_id
        self._prepared_effect_policy_versions[cache_key] = str(
            getattr(spec.effect_policy, "version", "")
        )
        return prepared

    def _normalize_result(self, spec: ToolSpec, raw: Any) -> Any:
        from deskpet.workflows.effects import NormalizedToolOutcome

        try:
            outcome = (
                spec.outcome_parser(raw)
                if spec.outcome_parser is not None
                else _normalize_with_parser(spec.outcome_parser_id, raw)
            )
        except Exception as exc:  # noqa: BLE001 - parser failures fail closed
            return NormalizedToolOutcome.malformed(
                f"outcome parser {spec.outcome_parser_id} raised {type(exc).__name__}"
            )
        if not isinstance(outcome, NormalizedToolOutcome):
            return NormalizedToolOutcome.malformed(
                f"outcome parser {spec.outcome_parser_id} returned an unsupported value"
            )
        return outcome

    async def execute_tool_outcome(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
        *,
        execution_context: Optional[ToolExecutionContext] = None,
    ) -> Any:
        """Compatibility adapter returning a three-state durable outcome."""

        from deskpet.workflows.effects import NormalizedToolOutcome

        with self._lock:
            spec = self._tools.get(name)
        envelope = await self.execute_tool(
            name,
            params,
            session_id,
            task_id,
            execution_context=execution_context,
        )
        if spec is None:
            return NormalizedToolOutcome.failure(
                "unknown_tool", str(envelope.get("error") or f"unknown tool: {name}")
            )
        if envelope.get("ok") is not True:
            return NormalizedToolOutcome.failure(
                "transport_failed", str(envelope.get("error") or "tool dispatch failed")
            )
        return self._normalize_result(spec, envelope.get("result"))

    @staticmethod
    def _grant_value(grant: object, key: str) -> Any:
        if isinstance(grant, Mapping):
            return grant.get(key)
        return getattr(grant, key, None)

    def _validate_authorization(
        self,
        authorization: object,
        *,
        prepared: Any,
        effect_id: str,
    ) -> str | None:
        expected = {
            "effect_id": effect_id,
            "tool_name": prepared.tool_name,
            "args_hash": prepared.args_hash,
            "permission_policy_version": prepared.permission_policy_version,
        }
        for key, value in expected.items():
            if self._grant_value(authorization, key) != value:
                return f"authorization_{key}_mismatch"
        expires_at = self._grant_value(authorization, "expires_at")
        if expires_at is None:
            return "authorization_expiry_missing"
        try:
            if float(expires_at) <= time.time():
                return "authorization_expired"
        except (TypeError, ValueError):
            return "authorization_expiry_invalid"
        return None

    async def execute_prepared(
        self,
        prepared: Any,
        *,
        effect_id: str,
        authorization: object | None = None,
    ) -> Any:
        """Execute exact prepared params after fail-closed capability recheck."""

        import inspect as _inspect

        from deskpet.workflows.effects import NormalizedToolOutcome

        with self._lock:
            spec = self._tools.get(prepared.tool_name)
        if spec is None:
            return NormalizedToolOutcome.failure("prepared_tool_missing", "tool is no longer registered")

        cache_key = (prepared.tool_name, prepared.stable_call_id, prepared.args_hash)
        current_effect_type = str(
            getattr(getattr(spec.effect_policy, "kind", None), "value", None)
            or "opaque_manual"
        )
        snapshots = {
            "tool_spec_version": (prepared.tool_spec_version, spec.spec_version),
            "schema_hash": (prepared.schema_hash, spec.schema_hash),
            "permission_policy_version": (
                prepared.permission_policy_version,
                spec.permission_policy_version,
            ),
            "effect_type": (prepared.effect_type, current_effect_type),
        }
        for field_name, (old, current) in snapshots.items():
            if old != current:
                return NormalizedToolOutcome.failure(
                    "prepared_call_stale", f"{field_name} changed; re-prepare and re-authorize"
                )
        cached_policy_version = self._prepared_effect_policy_versions.get(cache_key)
        current_policy_version = str(getattr(spec.effect_policy, "version", ""))
        if cached_policy_version is not None and cached_policy_version != current_policy_version:
            return NormalizedToolOutcome.failure(
                "prepared_call_stale", "effect policy changed; re-prepare and re-authorize"
            )

        grant_session_id = (
            self._grant_value(authorization, "session_id")
            if authorization is not None
            else None
        )
        session_id = str(
            grant_session_id or self._prepared_session_ids.get(cache_key, "")
        )
        if self._tools_config_provider is not None:
            try:
                cfg = self._tools_config_provider()
                if spec.toolset in set(getattr(cfg, "disabled_toolsets", []) or []):
                    return NormalizedToolOutcome.failure("tool_disabled", "toolset is disabled")
            except Exception as exc:  # noqa: BLE001
                return NormalizedToolOutcome.failure(
                    "policy_recheck_failed", f"tools config unavailable: {type(exc).__name__}"
                )
        if not spec.env_satisfied():
            return NormalizedToolOutcome.failure("tool_disabled", "required environment is unavailable")
        if session_id in self._plan_read_only_sessions and spec.permission_category in _WRITE_PERMISSION_CATEGORIES:
            return NormalizedToolOutcome.failure("plan_read_only", "write tool is disabled during planning")
        if spec.check_fn is not None:
            try:
                if not bool(spec.check_fn()):
                    return NormalizedToolOutcome.failure("tool_not_ready", "dynamic tool check failed")
            except Exception as exc:  # noqa: BLE001
                return NormalizedToolOutcome.failure(
                    "tool_not_ready", f"dynamic tool check raised {type(exc).__name__}"
                )
        if self._breaker is not None and not await self._breaker.can_call(session_id, prepared.tool_name):
            return NormalizedToolOutcome.failure("circuit_open", "tool circuit breaker is open")
        if authorization is not None:
            grant_error = self._validate_authorization(
                authorization, prepared=prepared, effect_id=effect_id
            )
            if grant_error is not None:
                return NormalizedToolOutcome.failure(grant_error, "authorization grant is invalid")
        elif self._gate is not None:
            return NormalizedToolOutcome.failure(
                "authorization_required", "prepared execution requires a durable authorization grant"
            )

        exact_params = dict(prepared.to_dict()["final_params"])
        started_at = None
        raw: Any = None
        try:
            from datetime import datetime, timezone

            started_at = datetime.now(timezone.utc)

            async def invoke() -> Any:
                if _inspect.iscoroutinefunction(spec.handler):
                    return await spec.handler(exact_params, effect_id)
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, spec.handler, exact_params, effect_id)

            raw = await asyncio.wait_for(invoke(), timeout=spec.timeout_seconds)
            outcome = self._normalize_result(spec, raw)
        except asyncio.TimeoutError:
            if current_effect_type in {"staged_file", "opaque_manual"}:
                outcome = NormalizedToolOutcome.malformed(
                    "opaque or write tool timed out; manual reconciliation is required"
                )
            else:
                outcome = NormalizedToolOutcome.failure(
                    "tool_timeout", "read-only tool execution timed out"
                )
        except Exception as exc:  # noqa: BLE001
            outcome = NormalizedToolOutcome.failure(
                "tool_handler_error", f"{type(exc).__name__}: {exc}"
            )

        if self._receipt_store_provider is not None and started_at is not None:
            try:
                from datetime import datetime, timezone
                from deskpet.tools.receipt_store import emit_receipt

                store = self._receipt_store_provider()
                if store is not None:
                    success = outcome.state.value == "success"
                    accepted = spec.completion_semantics == "accepted_async" and success
                    # generate_image can return immediately with a queued job
                    # even though its registry semantics remain sync-compatible
                    # for the worker-unavailable fallback. A queued acknowledgement
                    # is not evidence that an image artifact already exists.
                    if success and prepared.tool_name == "generate_image":
                        try:
                            queued_payload = (
                                json.loads(raw) if isinstance(raw, str) else raw
                            )
                            accepted = bool(
                                isinstance(queued_payload, dict)
                                and queued_payload.get("status") == "generating"
                            )
                        except (TypeError, ValueError, json.JSONDecodeError):
                            accepted = False
                    emit_receipt(
                        store,
                        tool_name=prepared.tool_name,
                        args={k: v for k, v in exact_params.items() if not str(k).startswith("_")},
                        started_at=started_at,
                        ended_at=datetime.now(timezone.utc),
                        ok=success and not accepted,
                        session_id=session_id,
                        phase="accepted" if accepted else "executed",
                        outcome="pending" if accepted else ("success" if success else "failed"),
                        effect_id=effect_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("prepared receipt emission failed for %r: %s", prepared.tool_name, exc)
        if self._breaker is not None:
            await self._breaker.record_call(
                session_id, prepared.tool_name, ok=outcome.state.value == "success"
            )
        return outcome

    def tool_inventory(self, names: Optional[Sequence[str]] = None) -> list[Any]:
        """Return one versioned workflow inventory row per selected ToolSpec."""

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

    def graph_write_inventory(self) -> list[Any]:
        """Committed v1 write classification exposed to graph compilers."""

        names = sorted(
            name
            for name in self.list_tools()
            if name not in _GRAPH_EXCLUDED_WRITE_TOOLS
            and (name in _GRAPH_STAGED_FILE_TOOLS or name in _GRAPH_OPAQUE_WRITE_TOOLS)
        )
        return self.tool_inventory(names)

    # ------------------------------------------------------------------
    # G3 (companion-code-v2): partition_dispatch — safe 并发 / unsafe 串行
    # ------------------------------------------------------------------
    def _is_concurrency_safe(self, name: str) -> bool:
        """Read ``ToolSpec.concurrency_safe`` thread-safely.

        Returns True for unknown tools — they'll fail loudly in
        ``execute_tool`` anyway (``unknown tool: ...``), and treating
        them as safe means a single bogus name doesn't drag the whole
        batch into serial mode.
        """
        with self._lock:
            spec = self._tools.get(name)
        return bool(spec.concurrency_safe) if spec is not None else True

    async def partition_dispatch(
        self,
        calls: list[Any],
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Dispatch a batch of tool calls split by ``concurrency_safe``.

        Args:
            calls: list of objects exposing ``.name`` (str), ``.args``
                (dict | None), and optional ``.task_id`` (str). A
                3-tuple ``(name, args, task_id)`` is also accepted for
                convenience; dicts ``{"name": ..., "args": ..., "task_id": ...}``
                work too. The point is duck-typed access — callers can
                pass their own ToolCall namedtuple.
            session_id: forwarded to ``execute_tool`` for breaker /
                permission gate / per-session context lookup.

        Returns:
            ``list[envelope]`` in **input order** (so caller can pair
            results back to ``calls[i]`` without bookkeeping). Each
            envelope is whatever ``execute_tool`` returned —
            ``{"ok": bool, "result": str | None, "error": str | None}``.

        Strategy:
            * All ``concurrency_safe=True`` calls run concurrently via
              ``asyncio.gather`` (one big batch).
            * All ``concurrency_safe=False`` calls run **serially** in
              their relative input order (e.g. write_file → bash_run
              executes write before bash to preserve causality).
            * Empty ``calls`` → returns ``[]`` immediately.

        Why not just run everything concurrently? Two writes to the
        same path racing in different threads can corrupt files /
        produce non-deterministic results. The partition is the cheapest
        correctness guarantee — pay 10% latency for full safety.
        """
        if not calls:
            return []

        def _extract(c: Any) -> tuple[str, dict[str, Any], str]:
            # Tuple / list: (name, args, task_id?)
            if isinstance(c, (tuple, list)):
                name = c[0]
                args = c[1] if len(c) > 1 else {}
                task_id = c[2] if len(c) > 2 else ""
                return str(name), dict(args or {}), str(task_id or "")
            # Dict: {"name": ..., "args": ..., "task_id": ...}
            if isinstance(c, dict):
                return (
                    str(c.get("name", "")),
                    dict(c.get("args") or {}),
                    str(c.get("task_id") or ""),
                )
            # Object with attributes (namedtuple / dataclass)
            name = getattr(c, "name", "")
            args = getattr(c, "args", None) or {}
            task_id = getattr(c, "task_id", "") or ""
            return str(name), dict(args), str(task_id)

        # Index so we can restore input order after merging.
        indexed: list[tuple[int, str, dict[str, Any], str]] = []
        for i, c in enumerate(calls):
            name, args, task_id = _extract(c)
            indexed.append((i, name, args, task_id))

        safe_batch = [
            (i, n, a, t) for (i, n, a, t) in indexed
            if self._is_concurrency_safe(n)
        ]
        unsafe_batch = [
            (i, n, a, t) for (i, n, a, t) in indexed
            if not self._is_concurrency_safe(n)
        ]

        out: list[Optional[dict[str, Any]]] = [None] * len(calls)

        # Concurrent — safe partition.
        if safe_batch:
            safe_results = await asyncio.gather(*[
                self.execute_tool(n, a, session_id, t)
                for (_, n, a, t) in safe_batch
            ])
            for (orig_i, _, _, _), res in zip(safe_batch, safe_results):
                out[orig_i] = res

        # Serial — unsafe partition (preserves input order within batch).
        for orig_i, n, a, t in unsafe_batch:
            out[orig_i] = await self.execute_tool(n, a, session_id, t)

        # Sanity: every slot filled.
        assert all(o is not None for o in out), (
            "partition_dispatch left a hole in the result list — bug in "
            "safe/unsafe routing logic"
        )
        return [o for o in out if o is not None]  # type: ignore[misc]

    async def _build_circuit_open_envelope(
        self, name: str, session_id: str, spec: ToolSpec
    ) -> dict[str, Any]:
        """Synthesize the dispatch envelope returned when the breaker
        is OPEN. Includes a Chinese hint + the list of sibling tools in
        the same toolset so the LLM has something concrete to fall back
        to.
        """
        # Cooldown remaining (best-effort; breaker may not expose it).
        cooldown_left: Optional[float] = None
        if hasattr(self._breaker, "cooldown_remaining"):
            try:
                cooldown_left = await self._breaker.cooldown_remaining(  # type: ignore[union-attr]
                    session_id, name
                )
            except Exception:  # noqa: BLE001 — never break dispatch over this
                cooldown_left = None

        # Find sibling tools (same toolset, different name). Skip env-
        # gated tools — they wouldn't survive ``schemas()`` either.
        with self._lock:
            siblings = [
                s.name
                for s in self._tools.values()
                if s.toolset == spec.toolset
                and s.name != name
                and s.env_satisfied()
            ]
        siblings.sort()

        if cooldown_left is not None and cooldown_left > 0:
            cooldown_str = f"剩余 {cooldown_left:.0f} 秒"
        else:
            cooldown_str = "请稍后重试"
        hint = (
            f"{name} 连续失败 3 次已熔断 ({cooldown_str})。"
            "检查参数或换个工具。"
        )
        result_payload = {
            "ok": False,
            "error": "circuit_open",
            "hint": hint,
            "available_alternatives": siblings,
        }
        return {
            "ok": False,
            "result": json.dumps(result_payload, ensure_ascii=False),
            "error": "circuit_open",
        }

    # ------------------------------------------------------------------
    # Introspection helpers (tests + tool_search)
    # ------------------------------------------------------------------
    def list_tools(self, source: Optional[str] = None) -> list[str]:
        """All registered tool names (env-hidden tools INCLUDED).

        Distinct from ``schemas()`` which filters — this is the raw
        inventory, used by tests and by the observability dashboard.

        P4-S20: pass ``source="plugin:notion"`` to filter by provenance.
        """
        with self._lock:
            if source is None:
                return sorted(self._tools.keys())
            return sorted(
                n for n, s in self._tools.items() if s.source == source
            )

    def get(self, name: str) -> Optional[ToolSpec]:
        """Return the full spec for one tool, or None if absent.

        ``tool_search`` uses this to grab ``description`` for matching
        without going through the dispatch path.
        """
        with self._lock:
            return self._tools.get(name)

    def all_specs(self) -> list[ToolSpec]:
        """Return every ToolSpec, regardless of env gating. Used by
        ``tool_search`` so a missing ``BRAVE_API_KEY`` still surfaces
        the tool name in search results (agent can then prompt the user
        to set it)."""
        with self._lock:
            return list(self._tools.values())


# Module-level singleton. Import this in tool modules:
#
#     from deskpet.tools.registry import registry
#     registry.register("my_tool", ...)
registry = ToolRegistry()
