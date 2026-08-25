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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from simple_harness import CallId, JsonValue, thaw_json
from simple_harness.tools import (
    FunctionTool,
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from simple_harness.tools.executor import EffectExecutor

logger = logging.getLogger(__name__)

PRODUCT_TOOL_NAMES: tuple[str, ...] = tuple(
    """agent agent_parallel agent_reach_doctor agent_reach_read app_discover
app_launch await_subagents capability_build capability_repair context_page_in
desktop_create_file doc_create doc_edit doc_read download_file edit_file excel_create
external_action_wait fetch_tool_result file_glob file_grep file_organize file_read
file_write generate_image glob gold_price_lookup grep image_ocr list_directory
memory_forget memory_read memory_recall memory_search memory_write move_file
office_pick_file pdf_export ppt_create process_list process_start process_stop
process_wait project_directory_select project_group_send read_file register_artifacts
run_browser_task run_shell scrapling_fetch screen_capture screen_click screen_key
screen_move screen_scroll screen_type skill_invoke spawn_subagents spawn_team
todo_complete todo_write tool_activate tool_describe tool_search web_crawl
web_extract_article web_fetch web_read_sitemap web_search window_capture window_focus
window_key window_list workflow_spawn workspace_prepare workspace_recall write_file""".split()
)

DispatchKind = Literal["sync", "async", "context", "staged", "control", "provider"]
ProductHandler = Callable[[Mapping[str, JsonValue], ToolContext], Any]


@dataclass(frozen=True, slots=True)
class ProductToolRegistration:
    name: str
    description: str
    input_schema: Mapping[str, JsonValue]
    handler: ProductHandler
    dispatch_kind: DispatchKind
    permission_category: str
    metadata: Mapping[str, JsonValue]

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

    def bind_run_authorities(self, authorities: object) -> None:
        if (
            self._run_authorities is not None
            and self._run_authorities is not authorities
        ):
            raise RuntimeError("product Tool authority registry is already bound")
        self._run_authorities = authorities

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
        token = _current_call_id.set(call.call_id)
        context_token = _current_tool_context.set(context)
        try:
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


class ProductEffectExecutor(EffectExecutor):
    """Bind SDK validation and dispatch to the immutable Run authority."""

    def __init__(self, *, registry: ProductToolsAdapter, **kwargs: Any) -> None:
        super().__init__(registry=registry, **kwargs)

    async def execute(self, **kwargs: Any):
        context = kwargs.get("context")
        if not isinstance(context, ToolContext):
            raise TypeError("ProductEffectExecutor requires ToolContext")
        token = _validation_run_id.set(context.run_id.value)
        try:
            return await super().execute(**kwargs)
        finally:
            _validation_run_id.reset(token)

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
        message = "Tool execution failed."
        if isinstance(error, Mapping):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
        return ToolResult.failed(call_id, code, message)
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


def _sdk_tool(registration: ProductToolRegistration) -> FunctionTool:
    async def invoke(arguments, context):
        raw = registration.handler(arguments, context)
        if inspect.isawaitable(raw):
            raw = await raw
        return _result(raw)

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
        required = input_schema.get("required")
        if isinstance(required, list):
            input_schema["required"] = [
                item for item in required if item != "deskpet_public_progress"
            ]
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
    missing = tuple(name for name in PRODUCT_TOOL_NAMES if name not in by_name)
    extra = tuple(sorted(set(by_name) - set(PRODUCT_TOOL_NAMES)))
    if missing or extra:
        raise ValueError(f"product Tool inventory mismatch: missing={missing}, extra={extra}")
    ordered = tuple(by_name[name] for name in PRODUCT_TOOL_NAMES)
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
            )
        )
        existing.add(name)
    return tuple(extended)


__all__ = (
    "PRODUCT_TOOL_NAMES",
    "ProductToolInventoryEntry",
    "ProductEffectExecutor",
    "ProductToolRegistration",
    "ProductToolsAdapter",
    "SdkToolExecutorCatalogUnavailable",
    "active_product_tool_call_id",
    "active_product_tool_context",
    "build_product_tool_registry",
    "extend_product_registry_with_mcp",
)
