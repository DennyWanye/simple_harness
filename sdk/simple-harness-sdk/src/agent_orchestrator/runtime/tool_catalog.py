# SPDX-License-Identifier: Apache-2.0
"""工具的发现目录；目录版本与执行池身份分开，授权仍由冻结名单决定。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import freeze_json, thaw_json

from .domain_tools import DomainTool

ROLES = ("task", "main", "child")
HISTORY_TOOLS = ("session_history_read", "session_history_search")
MAIN_READ_TOOLS = (
    "workspace_read_file",
    "workspace_list",
    "knowledge_list",
    "knowledge_read",
    *HISTORY_TOOLS,
)
META_SCHEMAS = {
    "tool_search": {
        "type": "object",
        "description": "在当前允许范围内按名字或说明搜索工具。",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 256},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "tool_schema": {
        "type": "object",
        "description": "读取允许工具的完整说明，并加载到本回合。",
        "properties": {"names": {"type": "array", "items": {"type": "string"}, "maxItems": 256}},
        "required": ["names"],
        "additionalProperties": False,
    },
}


def checked_roles(roles: object) -> tuple[str, ...]:
    if (
        not isinstance(roles, tuple)
        or any(role not in ROLES for role in roles)
        or len(set(roles)) != len(roles)
    ):
        raise ValueError("roles must be a distinct tuple drawn from task, main, child")
    return roles


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    description: str
    schema: Mapping[str, Any]
    source: str
    roles: tuple[str, ...] = ROLES
    always_exposed: bool = False

    def __post_init__(self) -> None:
        checked_roles(self.roles)
        object.__setattr__(self, "schema", freeze_json(thaw_json(self.schema)))


class ToolCatalog:
    def __init__(self, entries: Iterable[CatalogEntry]) -> None:
        self._entries = tuple(sorted(entries, key=lambda entry: entry.name))
        if len({entry.name for entry in self._entries}) != len(self._entries):
            raise ValueError("duplicate tool names in catalog")
        version = [
            {
                "name": e.name,
                "schema": thaw_json(e.schema),
                "roles": sorted(e.roles),
                "source": e.source,
            }
            for e in self._entries
        ]
        self.catalog_version = hashlib.sha256(
            json.dumps(version, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    def visible(self, role: str) -> tuple[CatalogEntry, ...]:
        if role not in ROLES:
            raise ValueError("unknown tool role")
        return tuple(
            e for e in self._entries if role in e.roles and (role != "child" or "main" in e.roles)
        )

    def search(self, role: str, query: str, limit: int = 8) -> tuple[CatalogEntry, ...]:
        if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("query must be text and limit must be between 1 and 256")
        needle = query.casefold()
        return tuple(
            e
            for e in self.visible(role)
            if needle in e.name.casefold() or needle in e.description.casefold()
        )[:limit]

    def schemas(self, role: str, names: Iterable[str]) -> list[dict[str, Any]]:
        visible = {e.name: e for e in self.visible(role)}
        requested = tuple(dict.fromkeys(names))
        if any(name not in visible for name in requested):
            raise ValueError("not_found_or_forbidden")
        return [
            {
                "name": name,
                "description": visible[name].description,
                "schema": thaw_json(visible[name].schema),
            }
            for name in requested
        ]

    def narrowed(self, role: str, names: Iterable[str]) -> ToolCatalog:
        allowed = set(names)
        return ToolCatalog(e for e in self.visible(role) if e.name in allowed)


def search_schema(enabled: bool) -> dict[str, Any]:
    schema = deepcopy(META_SCHEMAS["tool_search"])
    schema["title"] = "tool-search:lazy" if enabled else "tool-search:all"
    return schema


def catalog_for(
    tools: Mapping[str, DomainTool], *, commands: Mapping[str, Any] | None = None
) -> ToolCatalog:
    from simple_harness.agents.arp.skill_tools import ArpSkillTools
    from simple_harness.agents.arp.tools import ArpSessionHistoryTools

    from .tool_gateway import read_tool_schemas

    entries = []
    for name, schema in read_tool_schemas(large=True).items():
        roles = ROLES if name in MAIN_READ_TOOLS else ("task",)
        entries.append(
            CatalogEntry(name, str(schema.get("description", "")), schema, "builtin", roles)
        )
    for function in (*ArpSessionHistoryTools().function_tools(), *ArpSkillTools().function_tools()):
        spec = function.spec
        if spec.name == "tool_discover":
            continue
        roles = ROLES if spec.name in HISTORY_TOOLS else ("task",)
        entries.append(
            CatalogEntry(
                spec.name,
                spec.description,
                thaw_json(spec.input_schema),
                "builtin" if spec.name in HISTORY_TOOLS else "skill",
                roles,
                spec.name in HISTORY_TOOLS,
            )
        )
    entries.extend(
        CatalogEntry(
            name,
            str(tool.schema.get("description", "")),
            tool.schema,
            "builtin" if name in {*META_SCHEMAS, "sandbox_run"} else "application",
            tool.roles,
            name in META_SCHEMAS,
        )
        for name, tool in tools.items()
    )
    entries.extend(
        CatalogEntry(name, str(schema.get("description", "")), schema, "builtin", ROLES, True)
        for name, schema in (commands or {}).items()
        if name not in tools
    )
    return ToolCatalog(entries)


def invoke_meta(
    catalog: ToolCatalog, role: str, allowed: Iterable[str], name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    scoped = catalog.narrowed(role, allowed)
    if name == "tool_search":
        return {
            "tools": [
                {"name": e.name, "description": e.description, "source": e.source}
                for e in scoped.search(role, arguments["query"], arguments.get("limit", 8))
            ],
            "catalog_version": catalog.catalog_version,
        }
    if name == "tool_schema":
        return {
            "schemas": scoped.schemas(role, arguments["names"]),
            "catalog_version": catalog.catalog_version,
        }
    raise ValueError("unknown meta tool")


__all__ = ("CatalogEntry", "ToolCatalog")
