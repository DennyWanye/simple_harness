"""Direct, explicit binding of real product handlers to SDK registrations."""

from __future__ import annotations

import importlib
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from simple_harness import JsonValue, thaw_json
from simple_harness.tools import ToolContext

from deskpet.sdk_adapters.tools import (
    ProductToolRegistration,
    active_product_tool_call_id,
)
from deskpet.tools.capabilities import ToolExecutionContext

from .manifest import MANIFEST_SHA256, load_tool_manifest, migrate_tool_schemas


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

    adapted = dict(arguments)
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


def _execution_context(context: ToolContext) -> ToolExecutionContext:
    metadata = thaw_json(context.metadata)
    call_id = active_product_tool_call_id().value
    workspace = str(metadata["workspace"]) if metadata.get("workspace") else None
    write_scope_root = (
        str(metadata["write_scope_root"])
        if metadata.get("write_scope_root")
        else None
    )
    if workspace is None and write_scope_root is None:
        # SDK 0.1.4's ReAct driver currently constructs ToolContext without
        # propagating host workspace metadata. Keep product file tools bound
        # to the same app-owned default workspace instead of falling through
        # to the backend process working directory.
        from agent.write_scope import resolve_workspace_root

        workspace = str(resolve_workspace_root())
        write_scope_root = workspace
    return ToolExecutionContext(
        scope_id=str(metadata.get("scope_id") or context.run_id.value),
        session_id=str(metadata.get("session_id") or context.request_id.value),
        request_id=context.request_id.value,
        root_run_id=str(metadata.get("root_run_id") or context.run_id.value),
        run_id=context.run_id.value,
        call_id=call_id,
        effect_id=call_id,
        workspace=workspace,
        write_scope_root=write_scope_root,
        owner_key=str(metadata.get("owner_key") or ""),
    )


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
    todo_handler, _ = build_todo_write_tool(deps.todo_session_db)
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
            if arguments.get("query") and arguments.get("fact_id") is None:
                return {"ok": False, "error": "natural_language_forget_disabled"}
            _, root_run_id, call_id = trusted_memory_execution(context)
            source_event_id = (
                f"explicit-memory-action/v1/{root_run_id}/{call_id}"
            )
            forgotten = bool(await deps.memory_manager.forget_fact(
                int(arguments["fact_id"]),
                reason="",
                principal=await trusted_principal(context),
                source_event_id=source_event_id,
                payload_hash=None,
            ))
            return {
                "ok": True,
                "forgotten": forgotten,
                "receipt": "forgotten" if forgotten else "already_forgotten",
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
            old_context = _execution_context(context)
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
        migration = migration_by_name.get(name)
        registrations.append(
            ProductToolRegistration(
                name=name,
                description=str(schema.get("description") or name),
                input_schema=schema["parameters"],
                handler=invoke,
                dispatch_kind=_dispatch_kind(name, handler),  # type: ignore[arg-type]
                permission_category=str(item["permission_category"]),
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
