# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tool execution and call preparation."""

from __future__ import annotations

import asyncio as _asyncio
import contextvars
import copy
import inspect as _inspect
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

from paths import output_dir

from deskpet.execution.contracts import thaw_json
from deskpet.tools import receipt_store
from deskpet.tools.artifact import (
    extract_artifacts_from_result,
    maybe_add_artifacts,
    sha256_file_async,
)
from deskpet.types.task_grants import ResourceSelector
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    TargetMode,
)

from ..capabilities import (
    ToolExecutionContext,
    ToolPolicySnapshot,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from ..context_adapter import reject_reserved_model_fields
from .core import ToolRegistryCore
from .support import (
    _GRAPH_STAGED_FILE_TOOLS,
    _WRITE_PERMISSION_CATEGORIES,
    PreparedToolCallStale,
    ToolSpec,
    _result_payload,
    _schema_hash,
    tool_spec_fingerprint,
)

# 与原模块同名的记录器（getLogger 按名字返回同一个对象）；就地定义，ruff 才认得出
# 捕获异常后写日志的写法
logger = logging.getLogger("deskpet.tools.registry")

_PREPARED_FILE_TARGET_TOOLS: frozenset[str] = frozenset(
    {
        *_GRAPH_STAGED_FILE_TOOLS,
        "doc_create",
        "doc_edit",
        "excel_create",
        "pdf_export",
        "ppt_create",
        "ppt_pro",
    }
)


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


def _failure_message(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("code") or "tool failed")
    return str(error or payload.get("message") or "tool failed")


def _normalize_with_parser(parser_id: str, raw: Any) -> Any:
    """Parse a handler result into the durable workflow outcome contract."""

    payload = _result_payload(raw)
    if parser_id == "opaque_manual_v1":
        return NormalizedToolOutcome.malformed("opaque tool outcome requires manual reconciliation")
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
        if not isinstance(payload, Mapping):
            return NormalizedToolOutcome.malformed("shell outcome has no exit_code")
        if "exit_code" not in payload:
            if payload.get("ok") is False and payload.get("error"):
                error = payload.get("error")
                code = (
                    str(error.get("code") or "shell_failed")
                    if isinstance(error, Mapping)
                    else str(error)
                )
                return NormalizedToolOutcome.failure(
                    code,
                    _failure_message(payload),
                    value=dict(payload),
                )
            return NormalizedToolOutcome.malformed("shell outcome has no exit_code")
        if payload.get("exit_code") == 0:
            return NormalizedToolOutcome.success(dict(payload))
        return NormalizedToolOutcome.failure(
            "nonzero_exit", _failure_message(payload), value=dict(payload)
        )
    if parser_id == "accepted_run_v1":
        if (
            isinstance(payload, Mapping)
            and payload.get("accepted") is True
            and payload.get("run_id")
        ):
            return NormalizedToolOutcome.success(dict(payload))
        if isinstance(payload, Mapping) and (payload.get("ok") is False or payload.get("error")):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        return NormalizedToolOutcome.malformed("accepted outcome needs accepted=true and run_id")
    if parser_id == "activation_proposed_v1":
        if not isinstance(payload, Mapping):
            return NormalizedToolOutcome.malformed("tool activation outcome is not a JSON object")
        if (
            payload.get("error")
            or payload.get("ok") is False
            or payload.get("success") is False
            or payload.get("cancelled") is True
            or payload.get("canceled") is True
            or payload.get("timeout") is True
            or payload.get("timed_out") is True
        ):
            return NormalizedToolOutcome.failure(
                "tool_activation_failed",
                _failure_message(payload),
                value=dict(payload),
            )
        control = payload.get("__deskpet_control")
        capability_id = control.get("capability_id") if isinstance(control, Mapping) else None
        schema_hash = control.get("schema_hash") if isinstance(control, Mapping) else None
        schema = control.get("schema") if isinstance(control, Mapping) else None
        nonce = control.get("nonce") if isinstance(control, Mapping) else None
        base_scope_revision = (
            control.get("base_scope_revision") if isinstance(control, Mapping) else None
        )
        function_schema: Mapping[str, Any] | None = None
        if isinstance(schema, Mapping):
            if schema.get("type") == "function" and isinstance(schema.get("function"), Mapping):
                function_schema = schema["function"]
            elif isinstance(schema.get("name"), str) and isinstance(
                schema.get("parameters"), Mapping
            ):
                function_schema = schema
        valid_function_schema = bool(
            function_schema
            and isinstance(function_schema.get("name"), str)
            and function_schema.get("name")
            and isinstance(function_schema.get("parameters"), Mapping)
        )
        valid_schema_hash = bool(
            isinstance(schema_hash, str)
            and len(schema_hash) == 64
            and all(character in "0123456789abcdef" for character in schema_hash)
            and function_schema is not None
            and schema_hash
            in {
                _schema_hash(schema),
                _schema_hash(function_schema),
            }
        )
        if (
            payload.get("status") != "activation_proposed"
            or not isinstance(control, Mapping)
            or control.get("kind") != "tool_activation"
            or not isinstance(capability_id, str)
            or not capability_id
            or not valid_schema_hash
            or not valid_function_schema
            or not isinstance(nonce, str)
            or not nonce
            or not isinstance(base_scope_revision, int)
            or isinstance(base_scope_revision, bool)
            or base_scope_revision < 0
        ):
            return NormalizedToolOutcome.malformed(
                "tool activation outcome has no valid activation proposal"
            )
        return NormalizedToolOutcome.success(dict(payload))
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
        if not payload:
            return NormalizedToolOutcome.malformed("tool outcome is an empty JSON object")
        if "ok" in payload and not isinstance(payload.get("ok"), bool):
            return NormalizedToolOutcome.malformed("tool outcome has a non-boolean ok field")
        if "success" in payload and not isinstance(payload.get("success"), bool):
            return NormalizedToolOutcome.malformed("tool outcome has a non-boolean success field")
        if payload.get("cancelled") is True or payload.get("canceled") is True:
            return NormalizedToolOutcome.failure(
                "tool_cancelled",
                str(payload.get("message") or "tool was cancelled"),
                value=dict(payload),
            )
        if payload.get("timeout") is True or payload.get("timed_out") is True:
            return NormalizedToolOutcome.failure(
                "tool_timeout",
                str(payload.get("message") or "tool timed out"),
                value=dict(payload),
            )
        state = (
            str(payload.get("state") or payload.get("status") or "")
            .strip()
            .casefold()
            .replace("-", "_")
            .replace(" ", "_")
        )
        failed_states = {
            "aborted",
            "cancelled",
            "canceled",
            "error",
            "failed",
            "failure",
            "interrupted",
            "not_executed",
            "timeout",
            "timed_out",
            "unknown",
        }
        if state in failed_states:
            return NormalizedToolOutcome.failure(
                f"tool_{state}",
                str(payload.get("message") or f"tool ended with state {state}"),
                value=dict(payload),
            )
        if state in {"accepted", "in_progress", "pending", "queued", "running"}:
            return NormalizedToolOutcome.malformed(f"tool outcome is not terminal: {state}")
        if state and state not in {
            "complete",
            "completed",
            "done",
            "ok",
            "success",
            "succeeded",
        }:
            return NormalizedToolOutcome.malformed(f"tool outcome has an unknown state: {state}")
        if payload.get("ok") is False or payload.get("success") is False or payload.get("error"):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(payload), value=dict(payload)
            )
        return NormalizedToolOutcome.success(dict(payload))
    return NormalizedToolOutcome.malformed(f"unknown outcome parser: {parser_id}")


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
        "ppt_create": "output_path",
        "ppt_pro": "output_path",
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
        "ppt_create": ".pptx",
        "ppt_pro": ".pptx",
    }
    suffix = suffix_by_tool.get(name)
    if suffix is None:
        return None, key
    safe_session = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
    safe_call = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stable_call_id)
    kind_by_tool = {
        "doc_create": "Doc",
        "excel_create": "Excel",
        "pdf_export": "PDF",
        "ppt_create": "PPT",
        "ppt_pro": "PPT",
    }
    try:
        generated_root = output_dir(kind_by_tool[name])
    except Exception:
        generated_root = Path.cwd() / "outputs"
    generated = generated_root / f"{name}-{safe_session}-{safe_call}{suffix}"
    params[key] = str(generated)
    return str(generated), key


def _default_prepared_targets(
    name: str,
    params: dict[str, Any],
    *,
    session_id: str,
    stable_call_id: str,
    workspace: str | os.PathLike[str] | None = None,
) -> tuple[Any, ...]:

    if name not in _PREPARED_FILE_TARGET_TOOLS:
        return ()
    raw_path, key = _target_path_for_call(
        name, params, session_id=session_id, stable_call_id=stable_call_id
    )
    if not raw_path:
        return ()
    final = Path(raw_path).expanduser()
    if not final.is_absolute():
        trusted_workspace = (
            workspace or params.get("_project_root") or params.get("_write_scope_root")
        )
        if trusted_workspace:
            final = Path(str(trusted_workspace)).expanduser() / final
    final = final.resolve(strict=False)
    if key is not None:
        params[key] = str(final)
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


class ExecutionMixin(ToolRegistryCore):
    """Tool execution and call preparation."""

    __slots__ = ()

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
        *,
        execution_context: ToolExecutionContext | None = None,
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
        active_policy: ToolPolicySnapshot | None = None
        if context_os_on:
            if execution_context is None or execution_context.origin != "agent":
                return {
                    "ok": False,
                    "result": None,
                    "error": "capability_denied: missing execution context",
                }
            if execution_context.session_id != session_id or not execution_context.scope_id:
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
                provider = self._dynamic_capability_admission_provider
                try:
                    dynamically_admitted = (
                        bool(provider(name, execution_context, spec))
                        if provider is not None
                        else False
                    )
                except Exception as exc:  # noqa: BLE001 - admission is fail-closed
                    logger.warning(
                        "dynamic_capability_admission_failed tool=%s error_type=%s",
                        name,
                        type(exc).__name__,
                    )
                    dynamically_admitted = False
                if not dynamically_admitted:
                    logger.warning(
                        "dynamic_capability_admission_denied tool=%s provider_bound=%s",
                        name,
                        provider is not None,
                    )
                    return {"ok": False, "result": None, "error": "capability_denied"}
            if active_policy.fingerprint != record.prepared.policy_fingerprint:
                logger.warning(
                    "tool_capability_stale tool=%s reason=policy_fingerprint "
                    "active_policy_ref=%s prepared_policy_ref=%s",
                    name,
                    active_policy.fingerprint[:12],
                    record.prepared.policy_fingerprint[:12],
                )
                return {"ok": False, "result": None, "error": "capability_stale"}
            if (
                (
                    prepared is not None
                    and (
                        prepared.ref.schema_hash != spec.schema_hash
                        or prepared.ref.spec_version != spec.spec_version
                        or prepared.ref.permission_policy_version != spec.permission_policy_version
                    )
                )
                or not spec.env_satisfied()
                or not spec.is_visible(record.eligibility)
                or spec.toolset in active_policy.disabled_toolsets
                or (
                    active_policy.dangerous_allowlist
                    and spec.dangerous
                    and spec.name not in active_policy.dangerous_allowlist
                )
            ):
                logger.warning(
                    "tool_capability_stale tool=%s reason=spec_or_eligibility",
                    name,
                )
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
                    "tools_config_provider read failed in execute_tool: %s",
                    _exc,
                )

        # WI-CC-2 (plan mode 物理只读): 规划期对写/执行类工具硬拦。
        # set_plan_read_only(sid, True) 期间（main.py 在 plan-confirm 硬门挂起
        # 时置位），任何 permission_category 属写类集合的工具直接 deny，handler
        # **不执行** —— 把「流程提示」升级为「物理只读」。只读工具放行。
        # 默认无 session 处于只读（features.plan_read_only OFF → main.py 永不置
        # 位）→ 此分支 short-circuit → 字节级 BC。
        return await self._execute_tool_checked(
            active_policy,
            execution_context=execution_context,
            name=name,
            params=params,
            session_id=session_id,
            spec=spec,
            task_id=task_id,
        )

    async def _execute_tool_checked(
        self,
        active_policy: ToolPolicySnapshot | None,
        *,
        execution_context: ToolExecutionContext | None,
        name: str,
        params: dict[str, Any],
        session_id: str,
        spec,
        task_id: str,
    ) -> dict[str, Any]:
        if (
            session_id in self._plan_read_only_sessions
            and spec.permission_category in _WRITE_PERMISSION_CATEGORIES
        ):
            logger.info(
                "plan_read_only_deny sid=%s tool=%s category=%s",
                session_id,
                name,
                spec.permission_category,
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
        _started_at = self._sanitize_mcp_arguments(merged_params, params, session_id, spec)
        try:
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
            timeout_s = (
                float(getattr(spec, "timeout_seconds", cfg_default_timeout)) or cfg_default_timeout
            )

            async def _run_handler() -> Any:
                host_context = execution_context
                if host_context is not None and active_policy is not None:
                    host_context = replace(host_context, policy_snapshot=active_policy)
                token = (
                    set_tool_execution_context(host_context) if host_context is not None else None
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
            except TimeoutError:
                logger.warning("execute_tool %r timed out after %.1fs", name, timeout_s)
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

        return await self._finish_tool_result(
            _started_at,
            merged_params=merged_params,
            name=name,
            result=result,
            session_id=session_id,
        )

    def _sanitize_mcp_arguments(
        self, merged_params: dict[str, Any], params: dict[str, Any], session_id: str, spec
    ):
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

        _started_at = _dt.now(UTC)
        return _started_at

    async def _finish_tool_result(
        self, _started_at, *, merged_params: dict[str, Any], name: str, result, session_id: str
    ) -> dict[str, Any]:
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
                    getattr(getattr(cfg, "last_mile", None), "artifact_envelope", False)
                )
                if envelope_on:
                    envelope = maybe_add_artifacts(
                        envelope=envelope,
                        tool_name=name,
                        enable=True,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "tools_config_provider raised in execute_tool %r: %s",
                    name,
                    _exc,
                )

        # WI-T2.2 P0 修：emit receipt（PRD §3 D5 + 二轮 P0-1 接电）。
        # ReceiptStore 在 main.py 启动期按 cfg.verifier.emit_receipts 构造并
        # set_receipt_store_provider 注入；未注入则 BC 路径不产 receipt。
        if self._receipt_store_provider is not None:
            try:
                store = self._receipt_store_provider()
                if store is not None:
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
                        k: v
                        for k, v in dict(merged_params or {}).items()
                        if not (isinstance(k, str) and k.startswith("_"))
                    }
                    try:
                        receipt_args = json.loads(
                            json.dumps(receipt_args, ensure_ascii=False, default=str)
                        )
                    except (TypeError, ValueError):
                        receipt_args = {str(k): str(v) for k, v in receipt_args.items()}
                    _artifact_shas: list[str] | None = None
                    _env_arts = envelope.get("artifacts")
                    if not (isinstance(_env_arts, list) and _env_arts) and envelope_ok:
                        _env_arts = [
                            artifact.to_dict()
                            for artifact in extract_artifacts_from_result(
                                tool_name=name,
                                result_json=result,
                            )
                        ]
                    if isinstance(_env_arts, list) and _env_arts:
                        _shas: list[str] = []
                        _workspace = merged_params.get("_project_root") or merged_params.get(
                            "_write_scope_root"
                        )
                        _workspace_root = Path(str(_workspace)).resolve() if _workspace else None
                        for _a in _env_arts:
                            _p = _a.get("path") if isinstance(_a, dict) else None
                            if not _p:
                                continue
                            try:
                                _artifact_path = Path(str(_p))
                                if not _artifact_path.is_absolute():
                                    if _workspace_root is None:
                                        continue
                                    _artifact_path = _workspace_root / _artifact_path
                                _artifact_path = _artifact_path.resolve(strict=True)
                                if _workspace_root is not None:
                                    _artifact_path.relative_to(_workspace_root)
                                _digest = await sha256_file_async(_artifact_path)
                                if _digest and _digest not in _shas:
                                    _shas.append(_digest)
                            except (OSError, ValueError):
                                continue
                        _artifact_shas = _shas or None
                    receipt_store.emit_receipt(
                        store,
                        tool_name=name,
                        args=receipt_args,
                        started_at=_started_at,
                        ended_at=_dt.now(UTC),
                        ok=envelope_ok,
                        session_id=session_id,
                        iteration=iteration,
                        artifact_shas=_artifact_shas,
                    )
            except Exception as _exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "receipt_store_provider raised in execute_tool %r: %s",
                    name,
                    _exc,
                )

        # P5-S2 Phase 3: record success/failure to the breaker. The
        # handler returned a JSON string (typically an envelope itself)
        # — if THAT envelope says ``ok=false`` we count it as a failure
        # even though the Python call didn't raise.
        if self._breaker is not None:
            outcome_ok = _envelope_indicates_success(result)
            await self._breaker.record_call(session_id, name, ok=outcome_ok)
        return envelope

    async def execute_tool_outcome(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
        *,
        execution_context: ToolExecutionContext | None = None,
    ) -> NormalizedToolOutcome:
        """Run the permission-aware path and normalize its durable outcome.

        ``execute_prepared`` deliberately refuses calls that still need the
        interactive PermissionGate. Durable workflow adapters use this method
        as that gate-aware continuation, then journal the normalized result.
        """

        raw = await self.execute_tool(
            name,
            params,
            session_id,
            task_id,
            execution_context=execution_context,
        )
        with self._lock:
            spec = self._tools.get(name)
        if spec is None:
            return NormalizedToolOutcome.failure(
                "prepared_tool_missing", "tool is no longer registered"
            )
        if not bool(raw.get("ok", False)):
            return NormalizedToolOutcome.failure(
                "tool_failed", _failure_message(raw), value=copy.deepcopy(raw)
            )
        return self._normalize_result(spec, raw.get("result"))

    # ------------------------------------------------------------------
    # Durable workflow adapter APIs
    # ------------------------------------------------------------------
    def prepare_call(
        self,
        tool_name: str,
        raw_params: Mapping[str, Any],
        session_id: str,
        stable_call_id: str,
        *,
        execution_context: ToolExecutionContext | None = None,
        catalog_snapshot_ref: str = "",
    ) -> PreparedToolCall:
        """Freeze one tool call after context/default/path resolution.

        The legacy handler contract remains ``handler(dict, task_id)``. This
        method only snapshots the exact arguments and capability versions that
        a durable workflow will later recheck before dispatch.
        """

        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        if execution_context is not None:
            if not isinstance(execution_context, ToolExecutionContext):
                raise TypeError("execution_context must be ToolExecutionContext")
            if execution_context.session_id != session_id:
                raise ValueError("trusted context session does not match preparation session")
            if execution_context.call_id != stable_call_id:
                raise ValueError("trusted context call does not match stable_call_id")
            reject_reserved_model_fields(raw_params)
        with self._lock:
            spec = self._tools.get(tool_name)
        if spec is None:
            raise KeyError(f"unknown tool: {tool_name}")
        return self._prepare_call_with_spec(
            spec,
            raw_params,
            session_id,
            stable_call_id,
            execution_context=execution_context,
            catalog_snapshot_ref=catalog_snapshot_ref,
        )

    def prepare_frozen_call(
        self,
        tool_name: str,
        raw_params: Mapping[str, Any],
        session_id: str,
        stable_call_id: str,
        *,
        expected_tool_spec_fingerprint: str,
        catalog_snapshot_ref: str,
        execution_context: ToolExecutionContext | None = None,
    ) -> PreparedToolCall:
        """Prepare against the exact active-or-leased ToolSpec in a Run snapshot."""

        if not expected_tool_spec_fingerprint or not catalog_snapshot_ref:
            raise PreparedToolCallStale(
                "frozen preparation requires ToolSpec and catalog snapshot refs"
            )
        with self._lock:
            active = self._tools.get(tool_name)
            if (
                active is not None
                and tool_spec_fingerprint(active) == expected_tool_spec_fingerprint
            ):
                spec = active
            else:
                run_id = "" if execution_context is None else execution_context.run_id
                leased = bool(
                    run_id
                ) and expected_tool_spec_fingerprint in self._snapshot_spec_leases.get(
                    (catalog_snapshot_ref, run_id), frozenset()
                )
                spec = self._retired_specs.get(expected_tool_spec_fingerprint) if leased else None
        if spec is None or spec.name != tool_name:
            raise PreparedToolCallStale("frozen ToolSpec is no longer active or leased")
        return self._prepare_call_with_spec(
            spec,
            raw_params,
            session_id,
            stable_call_id,
            execution_context=execution_context,
            catalog_snapshot_ref=catalog_snapshot_ref,
        )

    def _prepare_call_with_spec(
        self,
        spec: ToolSpec,
        raw_params: Mapping[str, Any],
        session_id: str,
        stable_call_id: str,
        *,
        execution_context: ToolExecutionContext | None,
        catalog_snapshot_ref: str,
    ) -> PreparedToolCall:
        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        if execution_context is not None:
            if not isinstance(execution_context, ToolExecutionContext):
                raise TypeError("execution_context must be ToolExecutionContext")
            if execution_context.session_id != session_id:
                raise ValueError("trusted context session does not match preparation session")
            if execution_context.call_id != stable_call_id:
                raise ValueError("trusted context call does not match stable_call_id")
            reject_reserved_model_fields(raw_params)
        tool_name = spec.name
        spec_fingerprint = tool_spec_fingerprint(spec)

        params: dict[str, Any] = {}
        if execution_context is None:
            params.update(self._session_context.get(session_id, {}))
        # Provider contracts freeze JSON recursively with MappingProxyType.
        # A shallow dict() leaves nested objects frozen, and later hashing or
        # PreparedToolCall freezing then fails with "cannot pickle
        # 'mappingproxy' object". Thaw the complete JSON tree at admission.
        thawed_params = thaw_json(raw_params or {})
        if not isinstance(thawed_params, Mapping):
            raise TypeError("tool params must be a JSON object")
        params.update(dict(thawed_params))
        params = _schema_defaults(spec.schema, params)
        input_blob_hashes = tuple(str(item) for item in params.pop("_input_blob_hashes", ()) or ())
        effect_type = str(
            getattr(getattr(spec.effect_policy, "kind", None), "value", None) or "opaque_manual"
        )
        targets: Sequence[Any]
        if spec.prepare is not None:
            prepared_result = spec.prepare(dict(params), session_id, stable_call_id)
            if isinstance(prepared_result, PreparedToolCall):
                prepared = prepared_result
                if prepared.tool_name != tool_name or prepared.stable_call_id != stable_call_id:
                    raise ValueError("custom prepare returned a mismatched call identity")
                resource_selectors = self._resolve_resource_scope(
                    spec,
                    prepared.final_params,
                    execution_context,
                )
                prepared = replace(
                    prepared,
                    tool_spec_version=spec.spec_version,
                    schema_hash=spec.schema_hash,
                    permission_policy_version=spec.permission_policy_version,
                    effect_type=effect_type,
                    effect_policy_version=str(getattr(spec.effect_policy, "version", "")),
                    tool_spec_fingerprint=spec_fingerprint,
                    runtime_provenance_ref=spec.runtime_provenance_ref,
                    catalog_snapshot_ref=catalog_snapshot_ref,
                    resource_selectors=resource_selectors,
                )
                cache_key = (tool_name, stable_call_id, prepared.args_hash)
                self._prepared_session_ids[cache_key] = session_id
                return prepared
            final_params, targets = prepared_result
            params = dict(final_params)
        else:
            targets = _default_prepared_targets(
                tool_name,
                params,
                session_id=session_id,
                stable_call_id=stable_call_id,
                workspace=(
                    execution_context.write_scope_root or execution_context.workspace
                    if execution_context is not None
                    else None
                ),
            )

        resource_selectors = self._resolve_resource_scope(
            spec,
            params,
            execution_context,
        )
        prepared = PreparedToolCall.prepare(
            tool_name=tool_name,
            stable_call_id=stable_call_id,
            final_params=params,
            prepared_targets=tuple(targets),
            tool_spec_version=spec.spec_version,
            schema_hash=spec.schema_hash,
            permission_policy_version=spec.permission_policy_version,
            effect_type=effect_type,
            effect_policy_version=str(getattr(spec.effect_policy, "version", "")),
            input_blob_hashes=input_blob_hashes,
            tool_spec_fingerprint=spec_fingerprint,
            runtime_provenance_ref=spec.runtime_provenance_ref,
            catalog_snapshot_ref=catalog_snapshot_ref,
            resource_selectors=resource_selectors,
        )
        cache_key = (tool_name, stable_call_id, prepared.args_hash)
        self._prepared_session_ids[cache_key] = session_id
        return prepared

    @staticmethod
    def _resolve_resource_scope(
        spec: ToolSpec,
        params: Mapping[str, Any],
        execution_context: ToolExecutionContext | None,
    ) -> tuple[ResourceSelector, ...]:
        resolver = spec.resource_scope_resolver
        if resolver is None:
            return ()
        if execution_context is None:
            raise ValueError(f"tool {spec.name} requires trusted context for resource resolution")
        resolved = tuple(resolver(dict(thaw_json(params)), execution_context))
        if not resolved:
            raise ValueError(f"tool {spec.name} resource resolver returned no selectors")
        if not all(isinstance(item, ResourceSelector) for item in resolved):
            raise TypeError(f"tool {spec.name} resource resolver returned an invalid selector")
        identities = {(item.kind, item.canonical_value, item.access) for item in resolved}
        if len(identities) != len(resolved):
            raise ValueError(f"tool {spec.name} resource resolver returned duplicate selectors")
        return resolved

    def _normalize_result(self, spec: ToolSpec, raw: Any) -> NormalizedToolOutcome:
        payload = _result_payload(raw)
        if (
            spec.name == "generate_image"
            and spec.outcome_parser_id == "json_error_envelope_v1"
            and isinstance(payload, Mapping)
            and payload.get("ok") is True
            and payload.get("status") == "generating"
        ):
            # Image generation has an established queued-success envelope.
            # Keep this exception tool-scoped; "generating" from any other
            # synchronous builtin remains non-terminal and fail-closed.
            return NormalizedToolOutcome.success(dict(payload))
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
