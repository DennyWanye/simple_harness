# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Host 侧记忆召回适配层：把 simple-harness-memory-sdk 的认知记忆能力
暴露为 product SDK memory tool 所需的 owner-scoped recall port。

边界（见 ARCHITECTURE/MEMORY_SDK_BOUNDARY.md）：
- 进 SDK：MemoryBackend（L1/L2 消息、L3 facts、digital twin、RRF recall、decay、summarize）。
- 留 host：SessionDB、ContextSnapshotStore、CompanionMessageProjection，以及这里的
  recall adapter —— 它只做「product SDK tool 契约 <-> MemoryBackend」的翻译，不自己实现记忆。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from deskpet.companion.companion_message_projection import OwnerMemoryReadScopeV1
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


class OwnerMemoryRecallQueryAdapter:
    """把 owner-scoped 只读召回转发给 MemoryBackend.recall。

    MemoryBackend.recall 的 ``session_id`` 是跨 session 亲和降权提示，不是硬过滤。
    单机桌宠场景下 owner scope 通常覆盖全部会话；这里：
    - 单 session scope → 传该 session_id 作为亲和提示；
    - 多 session / 空 scope → 传 None 走全局认知召回。
    """

    def __init__(self, memory_backend: Any) -> None:
        self._backend = memory_backend

    async def recall_readonly(
        self,
        query: str,
        limit: int,
        owner_scope: OwnerMemoryReadScopeV1,
    ) -> list[dict[str, Any]]:
        if self._backend is None:
            return []
        session_ids = tuple(getattr(owner_scope, "session_ids", ()) or ())
        session_id = session_ids[0] if len(session_ids) == 1 else None
        hits = await self._backend.recall(query, session_id=session_id, limit=limit)

        items: list[dict[str, Any]] = []
        for hit in hits:
            raw = asdict(hit) if is_dataclass(hit) else dict(hit)
            items.append(
                {
                    key: raw.get(key)
                    for key in (
                        "message_id",
                        "score",
                        "text",
                        "source",
                        "session_id",
                        "role",
                    )
                }
            )
            # 旧契约里时间戳字段名是 ts，SDK Hit 用 created_at。
            items[-1]["ts"] = raw.get("created_at")
        return items


class CompanionRunMemoryScopeResolver:
    """通过 durable generation-0 run snapshot 重新打开 owner memory scope。"""

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
    query_port: Any,
    scope_resolver: Any,
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
    query_port: Any,
    scope_resolver: Any,
) -> None:
    """在 host ToolRegistry V2 中注册 memory_recall tool（替代已删除的旧模块）。"""

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
    "OwnerMemoryRecallQueryAdapter",
    "build_memory_recall_handlers",
    "register_memory_recall",
]
