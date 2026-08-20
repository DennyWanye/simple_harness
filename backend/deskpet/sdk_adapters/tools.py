"""Explicit product Tool inventory projected into the SDK public registry."""

from __future__ import annotations

import contextvars
import copy
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from simple_harness import CallId, JsonValue
from simple_harness.tools import (
    FunctionTool,
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

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


_current_call_id: contextvars.ContextVar[CallId | None] = contextvars.ContextVar(
    "product_sdk_tool_call_id", default=None
)


def active_product_tool_call_id() -> CallId:
    """Return the SDK-owned identity of the currently dispatched Tool call."""

    call_id = _current_call_id.get()
    if call_id is None:
        raise RuntimeError("product Tool invoked outside SDK ToolRegistry")
    return call_id


class ProductToolsAdapter(ToolRegistry):
    """SDK registry that exposes the current call identity to product wrappers."""

    async def invoke(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        accepted_result_call_id: CallId | None = None,
    ) -> ToolResult:
        token = _current_call_id.set(call.call_id)
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
            _current_call_id.reset(token)


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


def _sdk_tool(registration: ProductToolRegistration) -> FunctionTool:
    async def invoke(arguments, context):
        raw = registration.handler(arguments, context)
        if inspect.isawaitable(raw):
            raw = await raw
        return _result(raw)

    input_schema = copy.deepcopy(dict(registration.input_schema))
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
    registry = ProductToolsAdapter(tuple(_sdk_tool(item) for item in ordered))
    inventory = tuple(
        ProductToolInventoryEntry(
            item.name,
            item.dispatch_kind,
            item.permission_category,
            str(item.metadata["source"]),
            str(item.metadata["version"]),
        )
        for item in ordered
    )
    return registry, inventory


__all__ = (
    "PRODUCT_TOOL_NAMES",
    "ProductToolInventoryEntry",
    "ProductToolRegistration",
    "ProductToolsAdapter",
    "active_product_tool_call_id",
    "build_product_tool_registry",
)
