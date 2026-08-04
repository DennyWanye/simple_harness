"""Trusted owner-scoped, query-only memory recall tool."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Protocol

from deskpet.memory.companion_message_projection import OwnerMemoryReadScopeV1
from deskpet.tools.capabilities import ToolExecutionContext

MEMORY_RECALL_TOOL_NAME = "memory_recall"
MEMORY_READ_SCOPE_EXTENSION_KIND = "deskpet.memory.read_scope.v1"
MEMORY_RECALL_SCHEMA: dict[str, Any] = {
    "name": MEMORY_RECALL_TOOL_NAME,
    "description": "Search the current owner's frozen conversation memory scope.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "Text to search for.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


class OwnerScopedMemoryRecallQueryPort(Protocol):
    async def recall_readonly(
        self,
        query: str,
        limit: int,
        owner_scope: OwnerMemoryReadScopeV1,
    ) -> list[Any]: ...


class OwnerMemoryReadScopeResolverPort(Protocol):
    def resolve_for_run(self, run_id: str) -> OwnerMemoryReadScopeV1: ...


class CompanionRunMemoryScopeResolver:
    """Reopen a scope only through the durable generation-0 run snapshot."""

    def __init__(self, companion_store: Any) -> None:
        self._store = companion_store

    def resolve_for_run(self, run_id: str) -> OwnerMemoryReadScopeV1:
        payload = self._store.resolve_owner_memory_read_scope_for_run(run_id)
        return OwnerMemoryReadScopeV1.from_mapping(payload)


def _validate_model_args(args: Mapping[str, Any]) -> tuple[str, int]:
    if not isinstance(args, Mapping):
        raise ValueError("memory_recall arguments must be an object")
    extras = set(args) - {"query", "limit"}
    if extras:
        raise ValueError("memory_recall accepts only query and limit")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("memory_recall query must be a non-empty string")
    limit = args.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("memory_recall limit must be between 1 and 20")
    return query.strip(), limit


def build_memory_recall_handlers(
    query_port: OwnerScopedMemoryRecallQueryPort,
    scope_resolver: OwnerMemoryReadScopeResolverPort,
):
    async def reject_untrusted_handler(_args: Mapping[str, Any]) -> str:
        raise RuntimeError("memory_recall_requires_trusted_run_context")

    async def trusted_context_handler(
        args: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> str:
        query, limit = _validate_model_args(args)
        run_id = str(context.run_id or context.root_run_id or "").strip()
        if not run_id:
            raise RuntimeError("memory_recall_run_identity_missing")
        scope = scope_resolver.resolve_for_run(run_id)
        if inspect.isawaitable(scope):
            scope = await scope
        if not isinstance(scope, OwnerMemoryReadScopeV1):
            raise RuntimeError("memory_recall_scope_unavailable")
        hits = query_port.recall_readonly(query, limit, scope)
        if inspect.isawaitable(hits):
            hits = await hits
        items = []
        for hit in hits:
            raw = asdict(hit) if is_dataclass(hit) else dict(hit)
            items.append(
                {
                    key: raw[key]
                    for key in ("message_id", "score", "text", "ts", "source")
                    if key in raw
                }
            )
        return json.dumps(
            {"ok": True, "items": items},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return reject_untrusted_handler, trusted_context_handler


def register_memory_recall(
    registry: Any,
    query_port: OwnerScopedMemoryRecallQueryPort,
    scope_resolver: OwnerMemoryReadScopeResolverPort,
) -> None:
    """Composition helper; the production registry owner calls this explicitly."""

    handler, context_handler = build_memory_recall_handlers(
        query_port, scope_resolver
    )
    registry.register(
        MEMORY_RECALL_TOOL_NAME,
        "core",
        MEMORY_RECALL_SCHEMA,
        handler,
        context_handler=context_handler,
        permission_category="read_file",
        source="builtin",
        dangerous=False,
        spec_version="core.memory_recall.v1",
        permission_policy_version="v1",
        completion_semantics="sync",
    )


__all__ = [
    "CompanionRunMemoryScopeResolver",
    "MEMORY_READ_SCOPE_EXTENSION_KIND",
    "MEMORY_RECALL_SCHEMA",
    "MEMORY_RECALL_TOOL_NAME",
    "OwnerMemoryReadScopeResolverPort",
    "OwnerScopedMemoryRecallQueryPort",
    "build_memory_recall_handlers",
    "register_memory_recall",
]
