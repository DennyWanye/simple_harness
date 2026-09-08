# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Direct, explicit binding of real product handlers to SDK registrations."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence, cast

from simple_harness import FrozenJsonValue, JsonValue, thaw_json
from simple_harness.tools import ToolContext

from deskpet.sdk_adapters.tools import (
    PROJECTLESS_SAFE_TOOL_NAMES,
    ProductToolRegistration,
    active_product_tool_call_id,
)
from deskpet.tools.capabilities import ToolExecutionContext

from .manifest import MANIFEST_SHA256, load_tool_manifest, migrate_tool_schemas


logger = logging.getLogger(__name__)

_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PROVIDER_TOOLS = frozenset(
    {"web_search", "web_fetch", "web_crawl", "web_extract_article", "web_read_sitemap", "scrapling_fetch", "gold_price_lookup", "run_browser_task", "generate_image"}
)
_CONTROL_TOOLS = frozenset(
    {"agent", "agent_parallel", "await_subagents", "capability_build", "capability_repair", "external_action_wait", "project_directory_select", "spawn_subagents", "spawn_team", "tool_activate", "workflow_spawn", "workspace_prepare"}
)
_STAGED_TOOLS = frozenset(
    {"desktop_create_file", "doc_create", "doc_edit", "edit_file", "excel_create", "file_write", "move_file", "pdf_export", "ppt_create", "register_artifacts", "write_file"}
)
_CONTEXT_TOOLS = frozenset({"context_page_in", "skill_invoke", "todo_write"})
_ASYNC_TOOLS = frozenset(
    {
        "app_discover", "app_launch", "download_file", "file_read", "file_write",
        "gold_price_lookup", "memory_forget", "memory_read", "memory_search",
        "memory_write", "process_list", "process_start", "process_stop",
        "process_wait", "scrapling_fetch", "skill_invoke", "web_crawl",
        "web_extract_article", "web_fetch", "web_read_sitemap", "web_search",
        "workspace_recall",
    }
)


def _authoritative_execution_context(
    base: Any, sdk_context: ToolContext
) -> ToolExecutionContext:
    """Attach the SDK call identity to an injected host workspace binding."""

    call_id = active_product_tool_call_id().value
    if isinstance(base, ToolExecutionContext):
        return replace(
            base,
            request_id=sdk_context.request_id.value,
            run_id=sdk_context.run_id.value,
            call_id=call_id,
            effect_id=call_id,
        )
    if base is None:
        raise RuntimeError("authoritative_tool_execution_context_unavailable")
    for field in ("scope_id", "session_id", "request_id"):
        if not str(getattr(base, field, "") or "").strip():
            raise RuntimeError("authoritative_tool_execution_context_incomplete")
    return ToolExecutionContext(
        scope_id=str(base.scope_id),
        session_id=str(base.session_id),
        request_id=sdk_context.request_id.value,
        root_run_id=str(getattr(base, "root_run_id", "") or sdk_context.run_id.value),
        run_id=sdk_context.run_id.value,
        call_id=call_id,
        effect_id=call_id,
        workspace=str(base.workspace) if getattr(base, "workspace", None) else None,
        write_scope_root=(
            str(base.write_scope_root)
            if getattr(base, "write_scope_root", None)
            else None
        ),
        owner_key=str(getattr(base, "owner_key", "") or ""),
        project_id=str(getattr(base, "project_id", "") or ""),
        project_revision=int(getattr(base, "project_revision", 0) or 0),
        project_identity=str(getattr(base, "project_identity", "") or ""),
    )


@dataclass(frozen=True, slots=True)
class ToolCatalogDependencies:
    """Host-owned dependencies required by the eleven closure handlers."""

    todo_session_db: Any
    workflow_service_provider: Callable[[], Any]
    context_page_store: Any
    execution_context_getter: Callable[[], Any]
    memory_query: Any
    memory_scope_resolver: Any
    capability_bridge_service: Any
    search_gateway: Any
    memory_manager: Any | None = None
    memory_identity_resolver: Any | None = None

    def __post_init__(self) -> None:
        required = {
            "todo_session_db": self.todo_session_db,
            "workflow_service_provider": self.workflow_service_provider,
            "context_page_store": self.context_page_store,
            "execution_context_getter": self.execution_context_getter,
            "memory_query": self.memory_query,
            "memory_scope_resolver": self.memory_scope_resolver,
            "capability_bridge_service": self.capability_bridge_service,
            "search_gateway": self.search_gateway,
        }
        missing = tuple(name for name, value in required.items() if value is None)
        if missing:
            raise RuntimeError(f"required Tool providers unavailable: {missing}")
        if not callable(self.workflow_service_provider) or not callable(
            self.execution_context_getter
        ):
            raise TypeError("Tool provider callbacks must be callable")
        if not callable(getattr(self.search_gateway, "search", None)):
            raise TypeError("search_gateway.search must be callable")
        required_methods = {
            "todo_session_db": (self.todo_session_db, ("replace_session_todos",)),
            "context_page_store": (self.context_page_store, ("get", "mark_active")),
            "memory_query": (self.memory_query, ("recall_readonly",)),
            "memory_scope_resolver": (self.memory_scope_resolver, ("resolve_for_run",)),
            "capability_bridge_service": (
                self.capability_bridge_service,
                ("search", "describe", "suggestions", "activate"),
            ),
        }
        for provider_name, (provider, methods) in required_methods.items():
            absent = tuple(
                method for method in methods
                if not callable(getattr(provider, method, None))
            )
            if absent:
                raise TypeError(f"{provider_name} missing callable(s): {absent}")


@dataclass(frozen=True, slots=True)
class ExplicitProductToolCatalog:
    registrations: tuple[ProductToolRegistration, ...]
    workflows: Mapping[str, Mapping[str, str]]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _StaticHandlerBinding:
    handler_id: str
    context_handler_id: str | None

    def resolve(self) -> tuple[Callable[..., Any], str]:
        identity = self.context_handler_id or self.handler_id
        if "<locals>" in identity:
            identity = self.handler_id
        handler = _import_callable(identity)
        parameters = inspect.signature(handler).parameters
        mode = (
            "keyword_context"
            if "execution_context" in parameters
            else (
                "context"
                if self.context_handler_id == identity
                and self.context_handler_id != self.handler_id
                else "standard"
            )
        )
        return handler, mode


def _callable_id(value: Callable[..., Any]) -> str:
    return f"{value.__module__}:{value.__qualname__}"


def _import_callable(identity: str) -> Callable[..., Any]:
    module_name, separator, qualname = identity.partition(":")
    if not separator or "<locals>" in qualname:
        raise RuntimeError(f"handler identity is not directly importable: {identity}")
    value: Any = importlib.import_module(module_name)
    for component in qualname.split("."):
        value = getattr(value, component)
    if not callable(value) or _callable_id(value) != identity:
        raise RuntimeError(f"handler identity drift: {identity}")
    return value


def adapt_model_arguments(name: str, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
    """Translate the 0.1.1-safe wire schemas back to legacy handler values."""

    # SDK ToolCall values are recursively frozen (mappingproxy/tuple).  Legacy
    # Host handlers intentionally accept ordinary dict/list JSON containers,
    # so detach the complete tree at this single admission boundary.
    adapted = thaw_json(cast(FrozenJsonValue, arguments))
    if not isinstance(adapted, dict):
        raise TypeError("model Tool arguments must be a JSON object")
    if name in {"app_launch", "process_start"} and "environment" in adapted:
        entries = adapted["environment"]
        if not isinstance(entries, list) or len(entries) > 64:
            raise ValueError("environment must be a bounded key/value array")
        environment: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"key", "value"}:
                raise ValueError("environment entries require only key and value")
            key, value = entry["key"], entry["value"]
            if (
                not isinstance(key, str)
                or _ENVIRONMENT_KEY.fullmatch(key) is None
                or key in environment
            ):
                raise ValueError("environment keys must be portable and unique")
            if not isinstance(value, str) or len(value) > 8192:
                raise ValueError("environment values must be bounded strings")
            environment[key] = value
        adapted["environment"] = environment
    if name == "capability_build" and "original_args" in adapted:
        entries = adapted["original_args"]
        if not isinstance(entries, list) or len(entries) > 128:
            raise ValueError("original_args must be a bounded key/value array")
        original: dict[str, Any] = {}
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"key", "value_json"}:
                raise ValueError("original_args entries require key and value_json")
            key = entry["key"]
            if not isinstance(key, str) or not key or len(key) > 128 or key in original:
                raise ValueError("original_args keys must be non-empty and unique")
            value_json = entry["value_json"]
            if not isinstance(value_json, str) or len(value_json) > 8192:
                raise ValueError("original_args value_json is invalid")
            original[key] = json.loads(value_json)
        adapted["original_args"] = original
    for field in ("failure_receipt_ref", "expected_sha256", "expected_source_hash"):
        value = adapted.get(field)
        if value is not None and (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)
        ):
            raise ValueError(f"{field} must be exactly 64 hexadecimal characters")
    for field in ("spec", "ops", "outline"):
        if name in {"doc_create", "doc_edit", "excel_create", "ppt_create"} and field in adapted:
            value = adapted[field]
            if not isinstance(value, str) or len(value) > 32768:
                raise ValueError(f"{field} must be bounded JSON text")
            json.loads(value)
    if name in {"window_capture", "window_focus", "window_key"}:
        creation_time = adapted.get("creation_time")
        if isinstance(creation_time, bool) or not isinstance(creation_time, (int, float)) or creation_time <= 0:
            raise ValueError("creation_time must be greater than zero")
    return adapted


# 事故 J（HM-TO-A6 turn 23）：``memory_forget`` 的处理器直接 ``int(arguments["fact_id"])``，
# 模型不带 ``fact_id`` 调用时抛 ``KeyError('fact_id')``，被 SDK 的处理器边界吞成
# ``tool_handler_failed`` + "Tool execution failed."，模型看不到任何可行动信息，连打 18 次
# 直到 ``react_repeated_tool_exceeded`` 打掉整个 Run。
#
# 这里的稳定码全部落在 ``deskpet/sdk_adapters/tools.py`` 的
# ``_SAFE_HANDLER_ERROR_CODE``（``^[a-z][a-z0-9_]{0,63}$``）字母表内，
# 因此会连同 ``public_message`` 一起原样呈现给模型；不含路径、栈帧、密钥。
MEMORY_FORGET_TARGET_REQUIRED = "memory_forget_target_required"
MEMORY_FORGET_INVALID_FACT_ID = "memory_forget_invalid_fact_id"
MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED = "memory_forget_natural_language_disabled"
MEMORY_FORGET_UNKNOWN_FACT_ID = "memory_forget_unknown_fact_id"
MEMORY_FORGET_STORE_UNAVAILABLE = "memory_forget_store_unavailable"
MEMORY_FORGET_IDENTITY_UNAVAILABLE = "memory_forget_identity_unavailable"

_MEMORY_FORGET_CANDIDATE_LIMIT = 20
_MEMORY_FORGET_LABEL_LIMIT = 48
# 与 ``deskpet/sdk_adapters/tools.py`` 的 ``_MAX_HANDLER_PUBLIC_MESSAGE`` 一致。
_MEMORY_FORGET_MESSAGE_LIMIT = 2048

# 冻结 manifest 里的描述指向 ``memory_facts_list`` —— 那是一条 UI WebSocket 路由，
# **不是**工具，模型永远拿不到 ``fact_id``，于是只能空手调用或改用自然语言。
# 照 ``tool_search``/``tool_describe`` 的既有做法在构建期投影正确的公开说明，
# 不改写 manifest 的存档字节。
_MEMORY_FORGET_DESCRIPTION = (
    "Forget one memory this assistant previously stored with memory_write, "
    "identified by the exact integer fact_id that memory_write returned. Use "
    "ONLY when the user explicitly asks to forget something. Forgetting by "
    "natural-language description is disabled. If you have no fact_id, call it "
    "once to receive the list of forgettable ids, then either call it again "
    "with one of them or tell the user to remove the memory in the app's "
    "memory panel — do not retry the same call."
)
_MEMORY_FORGET_FACT_ID_DESCRIPTION = (
    "Exact integer fact ID to forget, as returned by memory_write."
)
_MEMORY_FORGET_QUERY_DESCRIPTION = (
    "Deprecated and always rejected: memory_forget never resolves a memory "
    "from free text. Pass fact_id instead."
)


def _memory_forget_candidate_label(fact: Any) -> str:
    key = str(getattr(fact, "key", "") or "").strip()
    value = str(getattr(fact, "value", "") or "").strip()
    label = f"{key}={value}" if key and value else key or value
    label = " ".join(label.split())
    if len(label) > _MEMORY_FORGET_LABEL_LIMIT:
        label = label[: _MEMORY_FORGET_LABEL_LIMIT - 1] + "…"
    return label


async def _memory_forget_candidates(
    memory_manager: Any, principal: Any
) -> tuple[tuple[int, str], ...]:
    """列出该 principal 名下可按 id 遗忘的记忆（与 UI facts 面板同一条只读面）。

    这条列举只用来把拒绝变得**可行动**；它绝不放宽授权：读的是
    ``list_facts``（identity-safe，personal scope），失败一律降级成空列表，
    不把存储异常泄漏给模型。
    """

    lister = getattr(memory_manager, "list_facts", None)
    if lister is None:
        return ()
    try:
        facts = await lister(principal, limit=_MEMORY_FORGET_CANDIDATE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 列举失败不得升级成工具崩溃
        logger.warning(
            "memory_forget.candidates_unavailable error_type=%s", type(exc).__name__
        )
        return ()
    candidates: list[tuple[int, str]] = []
    for fact in facts or ():
        raw_id = getattr(fact, "id", None)
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            continue
        candidates.append((raw_id, _memory_forget_candidate_label(fact)))
        if len(candidates) >= _MEMORY_FORGET_CANDIDATE_LIMIT:
            break
    return tuple(candidates)


def _memory_forget_rejection(
    code: str, reason: str, candidates: Sequence[tuple[int, str]]
) -> dict[str, Any]:
    if candidates:
        listed = "; ".join(
            f"{fact_id} ({label})" if label else str(fact_id)
            for fact_id, label in candidates
        )
        action = (
            f"Forgettable memory ids for this user: {listed}. Call memory_forget "
            "again with fact_id set to exactly one of those integers."
        )
    else:
        action = (
            "This user currently has no memory that can be forgotten by id — only "
            "a memory this assistant stored earlier with memory_write has one. Do "
            "not call memory_forget again; tell the user you cannot remove it "
            "yourself and that they can delete it in the app's memory panel."
        )
    message = f"memory_forget rejected: {reason} {action}"
    return {
        "ok": False,
        "error_code": code,
        "public_message": message[:_MEMORY_FORGET_MESSAGE_LIMIT],
    }


def _memory_forget_fact_id(value: Any) -> int | None:
    """把模型给的 ``fact_id`` 收敛成整数；无法收敛返回 ``None``（由调用方拒绝）。"""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _dynamic_handlers(deps: ToolCatalogDependencies) -> dict[str, tuple[Callable[..., Any], str]]:
    from deskpet.memory.recall_adapter import (
        build_memory_recall_handlers,
        build_memory_search_handler,
    )
    from deskpet.tools.code_tools.spawn_subagents_tool import (
        build_sdk_await_subagents_tool,
        product_delegation_tool_catalog,
    )
    from deskpet.tools.code_tools.todo_write_tool import build_todo_write_tool
    from deskpet.tools.context_page_in_tools import build_context_page_in_handler
    from deskpet.tools.tool_search import register_capability_bridge_tools
    from deskpet.tools.code_tools.web_search_tool import build_web_search_handler

    delegates = product_delegation_tool_catalog()
    await_handler, _ = build_sdk_await_subagents_tool(
        deps.workflow_service_provider
    )
    sync_todo_handler, _ = build_todo_write_tool(deps.todo_session_db)

    async def todo_handler(
        arguments: dict[str, Any],
        task_id: str = "",
        *,
        execution_context: ToolExecutionContext | None = None,
    ) -> str:
        # The legacy handler bridges its async SessionDB write from a worker
        # thread.  The SDK invokes product handlers on its event loop, so run
        # this one sync bridge off-loop instead of deadlocking that loop with
        # run_coroutine_threadsafe(...).result().
        return await asyncio.to_thread(
            sync_todo_handler,
            arguments,
            task_id,
            execution_context=execution_context,
        )
    page_handler = build_context_page_in_handler(
        deps.context_page_store,
        execution_context_getter=deps.execution_context_getter,
    )
    _reject_memory_recall, trusted_memory_recall = build_memory_recall_handlers(
        deps.memory_query,
        deps.memory_scope_resolver,
    )

    class Capture:
        def __init__(self) -> None:
            self.handlers: dict[str, Callable[..., Any]] = {}

        def register(self, *, name: str, handler: Callable[..., Any], **_kwargs: Any) -> None:
            if name in self.handlers:
                raise RuntimeError(f"duplicate capability bridge Tool: {name}")
            self.handlers[name] = handler

    capture = Capture()
    register_capability_bridge_tools(capture, deps.capability_bridge_service)
    dynamic = {
        name: (handler_schema[0], "standard")
        for name, handler_schema in delegates.items()
    }
    dynamic.update(
        {
            "await_subagents": (await_handler, "keyword_context"),
            "todo_write": (todo_handler, "keyword_context"),
            "context_page_in": (page_handler, "standard"),
            "memory_recall": (trusted_memory_recall, "context"),
            "memory_search": (
                build_memory_search_handler(
                    deps.memory_query,
                    deps.memory_scope_resolver,
                ),
                "context",
            ),
            "web_search": (build_web_search_handler(deps.search_gateway), "standard"),
        }
    )
    if deps.memory_manager is not None and deps.memory_identity_resolver is not None:
        from simple_harness_memory import MemoryPrincipal

        def trusted_memory_execution(context: Any) -> tuple[str, str, str]:
            authority_context = deps.execution_context_getter()
            metadata = thaw_json(getattr(context, "metadata", {}))
            session_id = str(
                getattr(authority_context, "session_id", "")
                or metadata.get("session_id")
                or getattr(context, "session_id", "")
            ).strip()
            if not session_id:
                raise RuntimeError("trusted_memory_session_id_unavailable")
            run = getattr(context, "run_id", None)
            root_run_id = str(
                getattr(authority_context, "root_run_id", "")
                or metadata.get("root_run_id")
                or getattr(context, "root_run_id", "")
                or getattr(run, "value", run)
                or ""
            ).strip()
            call = getattr(context, "call_id", None)
            call_id = str(
                getattr(authority_context, "call_id", "")
                or getattr(call, "value", call)
                or ""
            ).strip()
            if not root_run_id or not call_id:
                raise RuntimeError("trusted_memory_execution_id_unavailable")
            return session_id, root_run_id, call_id

        async def trusted_principal(context: Any) -> MemoryPrincipal:
            session_id, _, _ = trusted_memory_execution(context)
            identity = await deps.memory_identity_resolver.resolve(session_id)
            return MemoryPrincipal(
                identity.deployment_id,
                identity.household_id,
                identity.actor_id,
                identity.session_id,
            )

        async def memory_write(arguments: Mapping[str, Any], context: Any) -> dict[str, Any]:
            _, root_run_id, call_id = trusted_memory_execution(context)
            source_event_id = (
                f"explicit-memory-action/v1/{root_run_id}/{call_id}"
            )
            tier = {
                "auto": "auto",
                "l1": "working",
                "l2": "long_term",
                "l3": "identity",
            }[str(arguments.get("tier", "auto"))]
            fact_id = await deps.memory_manager.remember_fact(
                await trusted_principal(context),
                str(arguments["text"]),
                source_event_id=source_event_id,
                salience=float(arguments.get("salience", 0.5)),
                pinned=bool(arguments.get("pinned", False)),
                tier=tier,
            )
            return {
                "ok": True,
                "memory_id": int(fact_id),
                "source_event_id": source_event_id,
            }

        async def memory_forget(arguments: Mapping[str, Any], context: Any) -> dict[str, Any]:
            # 任何模型可控的参数形状都必须收敛成**确定性、模型可见**的拒绝：
            # 抛异常只会变成不可行动的 "Tool execution failed."（事故 J）。
            raw_fact_id = arguments.get("fact_id")
            fact_id = _memory_forget_fact_id(raw_fact_id)
            if fact_id is None:
                raw_query = arguments.get("query")
                if raw_fact_id is not None:
                    code = MEMORY_FORGET_INVALID_FACT_ID
                    reason = "fact_id must be an integer memory id."
                elif isinstance(raw_query, str) and raw_query.strip():
                    # 自然语言遗忘保持禁用（提示注入面，见 plans/2026-05-23-memory-
                    # system-stage2/03-architect-review-round1.md D-RISK-5）；
                    # 只是把拒绝从静默失败改成可行动。
                    code = MEMORY_FORGET_NATURAL_LANGUAGE_DISABLED
                    reason = (
                        "forgetting by natural-language description is disabled; "
                        "memory_forget resolves an exact integer fact_id only."
                    )
                else:
                    code = MEMORY_FORGET_TARGET_REQUIRED
                    reason = "no fact_id was given."
                # 拒绝路径必须永远返回，绝不改成第二种崩溃：身份解析失败时退化成
                # 「没有可遗忘的 id」这条同样可行动的文案。
                try:
                    principal = await trusted_principal(context)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "memory_forget.principal_unavailable error_type=%s",
                        type(exc).__name__,
                    )
                    return _memory_forget_rejection(code, reason, ())
                return _memory_forget_rejection(
                    code,
                    reason,
                    await _memory_forget_candidates(deps.memory_manager, principal),
                )
            # 授权/抑制路径与既有实现逐字一致：同一个 trusted principal、同一个
            # 由 root_run_id/call_id 派生的 source_event_id、payload_hash=None。
            # 身份/执行标识解析失败依然**不遗忘**（授权语义不变），只是不再以裸异常收场。
            try:
                _, root_run_id, call_id = trusted_memory_execution(context)
                principal = await trusted_principal(context)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "memory_forget.identity_unavailable error_type=%s",
                    type(exc).__name__,
                )
                return {
                    "ok": False,
                    "error_code": MEMORY_FORGET_IDENTITY_UNAVAILABLE,
                    "public_message": (
                        "memory_forget rejected: this Run cannot be tied to a "
                        "memory owner, so nothing was forgotten. Do not retry in "
                        "this turn; tell the user the memory is unchanged."
                    ),
                }
            source_event_id = (
                f"explicit-memory-action/v1/{root_run_id}/{call_id}"
            )
            try:
                forgotten = bool(await deps.memory_manager.forget_fact(
                    fact_id,
                    reason="",
                    principal=principal,
                    source_event_id=source_event_id,
                    payload_hash=None,
                ))
            except Exception as exc:  # noqa: BLE001 - 存储异常不得外泄成裸异常
                # 只记类型名：所有权/幂等冲突与存储故障的细节都是私有诊断。
                logger.warning(
                    "memory_forget.store_failed error_type=%s", type(exc).__name__
                )
                return {
                    "ok": False,
                    "error_code": MEMORY_FORGET_STORE_UNAVAILABLE,
                    "public_message": (
                        "memory_forget rejected: this memory could not be "
                        "forgotten right now. Do not retry in this turn; tell the "
                        "user the memory is unchanged and that they can remove it "
                        "in the app's memory panel."
                    ),
                }
            if not forgotten:
                return _memory_forget_rejection(
                    MEMORY_FORGET_UNKNOWN_FACT_ID,
                    f"fact_id {fact_id} is not an active memory of this user.",
                    await _memory_forget_candidates(deps.memory_manager, principal),
                )
            return {
                "ok": True,
                "forgotten": True,
                "receipt": "forgotten",
                "source_event_id": source_event_id,
            }

        async def memory_read(arguments: Mapping[str, Any], context: Any) -> dict[str, Any]:
            fact_id = int(arguments["memory_id"])
            fact = await deps.memory_manager.read_fact(
                await trusted_principal(context),
                fact_id,
            )
            if fact is None:
                return {"ok": False, "error": "memory_not_found"}
            return {
                "ok": True,
                "memory": {
                    "id": fact_id,
                    "key": str(fact.key),
                    "value": str(fact.value),
                },
            }

        dynamic.update(
            {
                "memory_write": (memory_write, "context"),
                "memory_forget": (memory_forget, "context"),
                "memory_read": (memory_read, "context"),
            }
        )
    dynamic.update({name: (handler, "standard") for name, handler in capture.handlers.items()})
    return dynamic


def _dispatch_kind(name: str, handler: Callable[..., Any]) -> str:
    if name in _CONTROL_TOOLS:
        return "control"
    if name in _STAGED_TOOLS:
        return "staged"
    if name in _CONTEXT_TOOLS:
        return "context"
    if name in _PROVIDER_TOOLS:
        return "provider"
    if name in _ASYNC_TOOLS:
        return "async"
    return "async" if inspect.iscoroutinefunction(handler) else "sync"


def build_explicit_product_tool_catalog(
    dependencies: ToolCatalogDependencies,
) -> ExplicitProductToolCatalog:
    """Build all 77 registrations locally; publish nothing until validation ends."""

    manifest = load_tool_manifest()
    schemas, migration_records = migrate_tool_schemas(manifest)
    dynamic = _dynamic_handlers(dependencies)
    registrations: list[ProductToolRegistration] = []
    migration_by_name = {record.name: record for record in migration_records}
    for item in manifest.tools:
        name = str(item["name"])
        if name in dynamic:
            handler, mode = dynamic[name]
        else:
            handler = _StaticHandlerBinding(
                str(item["handler_id"]),
                (
                    str(item["context_handler_id"])
                    if item.get("context_handler_id")
                    else None
                ),
            )
            mode = "lazy"

        async def invoke(arguments, context, *, _name=name, _handler=handler, _mode=mode):
            adapted = adapt_model_arguments(_name, arguments)
            old_context = _authoritative_execution_context(
                dependencies.execution_context_getter(), context
            )
            if (
                _name not in PROJECTLESS_SAFE_TOOL_NAMES
                and (
                    old_context.workspace is None
                    or old_context.write_scope_root is None
                )
            ):
                raise RuntimeError("project_workspace_binding_required")
            if _name == "skill_invoke" and not isinstance(
                old_context, ToolExecutionContext
            ):
                raise TypeError("skill_invoke requires authoritative ToolExecutionContext")
            call_id = active_product_tool_call_id().value
            resolved_handler, resolved_mode = (
                _handler.resolve() if _mode == "lazy" else (_handler, _mode)
            )
            if resolved_mode == "context":
                result = resolved_handler(adapted, old_context)
            elif resolved_mode == "keyword_context":
                result = resolved_handler(
                    adapted,
                    call_id,
                    execution_context=old_context,
                )
            else:
                result = resolved_handler(adapted, call_id)
            return await result if inspect.isawaitable(result) else result

        schema = schemas[name]
        # The frozen manifest describes the older bridge. Project the current
        # public discovery instructions without rewriting its archived bytes.
        if name == "tool_search":
            schema = {**schema, "description": (
                "Search deferred capabilities authorized for this request. "
                "Returns ranked descriptors, not executable schemas. Copy a returned "
                "capability_id into tool_describe, then use its exact activation "
                "fields with tool_activate before calling the target tool."
            ), "parameters": {**schema["parameters"], "properties": {
                **schema["parameters"]["properties"],
                "query": {**schema["parameters"]["properties"]["query"], "description": (
                    "Space-separated keywords. Any token can match capability metadata "
                    "or schema text; results are ranked by token occurrences."
                )},
                "toolset": {**schema["parameters"]["properties"]["toolset"], "description": (
                    "Legacy compatibility field; the current SDK catalog ignores this filter."
                )},
            }}}
        elif name == "memory_forget":
            properties = schema["parameters"].get("properties", {})
            schema = {**schema, "description": _MEMORY_FORGET_DESCRIPTION, "parameters": {
                **schema["parameters"], "properties": {
                    **properties,
                    "fact_id": {
                        **properties.get("fact_id", {"type": "integer"}),
                        "description": _MEMORY_FORGET_FACT_ID_DESCRIPTION,
                    },
                    "query": {
                        **properties.get("query", {"type": "string"}),
                        "description": _MEMORY_FORGET_QUERY_DESCRIPTION,
                    },
                },
            }}
        elif name == "tool_describe":
            schema = {**schema, "description": schema["description"].replace(
                "capability_search", "tool_search"), "parameters": {
                **schema["parameters"], "properties": {
                    **schema["parameters"]["properties"], "capability_id": {
                        **schema["parameters"]["properties"]["capability_id"],
                        "description": "Copy the complete capability_id returned by tool_search.",
                    },
                },
            }}
        migration = migration_by_name.get(name)
        registrations.append(
            ProductToolRegistration(
                name=name,
                description=str(schema.get("description") or name),
                input_schema=schema["parameters"],
                handler=invoke,
                dispatch_kind=_dispatch_kind(name, handler),  # type: ignore[arg-type]
                permission_category=str(item["permission_category"]),
                projectless_admission=(
                    "safe" if name in PROJECTLESS_SAFE_TOOL_NAMES else "requires_project"
                ),
                metadata={
                    **thaw_json(item),
                    "source": "real-tool-manifest",
                    "version": str(migration.spec_version if migration else item["spec_version"]),
                    "manifest_sha256": MANIFEST_SHA256,
                    "source_schema_hash": str(item["schema_hash"]),
                    "handler_id": str(item.get("stable_handler_id") or item["handler_id"]),
                    "schema_migration": migration.name if migration else None,
                },
            )
        )
    names = tuple(item.name for item in registrations)
    if len(names) != 77 or len(set(names)) != 77 or set(names) != set(manifest.tool_names):
        raise RuntimeError("explicit Tool registration inventory differs from manifest")
    return ExplicitProductToolCatalog(
        registrations=tuple(registrations),
        workflows=manifest.workflows,
        manifest_sha256=MANIFEST_SHA256,
    )


__all__ = (
    "ExplicitProductToolCatalog",
    "ToolCatalogDependencies",
    "adapt_model_arguments",
    "build_explicit_product_tool_catalog",
)
