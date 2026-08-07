# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: ``tool_search`` meta-tool (lazy schema loading, CCB pattern).

Spec: "Tool Search for Lazy Schema Loading" (tool-framework/spec.md).

Agent loop starts with a small, curated set of toolsets exposed via
``ContextAssembler``. When the user asks for something the curated
subset can't handle, the LLM calls ``tool_search(query="...")`` and the
registry searches the FULL inventory (including env-hidden tools — we
want to surface "web_search_brave requires BRAVE_API_KEY" so the agent
can ask the user to set it) for name/description matches. Matching
tools come back as a schema list the agent can cite / request activation
for on subsequent turns.

Matching is intentionally simple substring (lowercased, whitespace-split
tokens): if every query token appears in ``name + " " + description``,
the tool is a hit. We sort hits by number of tokens matched
(descending), then by name (ascending) for stable output.
"""
from __future__ import annotations

import json
from typing import Any

from .registry import registry
from .capabilities import ToolCapabilityBridgeService

_SCHEMA: dict[str, Any] = {
    "name": "tool_search",
    "description": (
        "Search the complete Simple Harness tool registry by keyword. Use this when "
        "you need a capability that isn't in your current toolset (e.g. file "
        "ops, web fetch, todo management). Returns matching tools' schemas; "
        "invoke them directly on the next turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Space-separated keywords. Matches against tool name + "
                    "description, case-insensitive. Every token must appear."
                ),
            },
            "toolset": {
                "type": "string",
                "description": (
                    "Optional toolset filter: 'file', 'web', 'memory', "
                    "'todo', 'control'. Omit to search all toolsets."
                ),
            },
        },
        "required": ["query"],
    },
}


def _handle_tool_search(args: dict[str, Any], task_id: str) -> str:
    query = str(args.get("query", "") or "").strip().lower()
    if not query:
        return json.dumps(
            {"error": "query must be non-empty", "retriable": False}
        )
    toolset_filter = args.get("toolset")
    tokens = [t for t in query.split() if t]

    hits: list[tuple[int, str, dict[str, Any]]] = []
    for spec in registry.all_specs():
        if toolset_filter and spec.toolset != toolset_filter:
            continue
        if spec.name == "tool_search":
            # Don't surface the search tool itself — the agent is
            # already calling it, no point advertising it again.
            continue
        haystack = (
            spec.name + " " + str(spec.schema.get("description", ""))
        ).lower()
        matched = sum(1 for tok in tokens if tok in haystack)
        if matched == len(tokens):
            hits.append(
                (
                    matched,
                    spec.name,
                    {"type": "function", "function": dict(spec.schema)},
                )
            )

    # Sort: more tokens matched first, then alphabetical for determinism.
    hits.sort(key=lambda t: (-t[0], t[1]))
    return json.dumps(
        {
            "matches": [h[2] for h in hits],
            "count": len(hits),
            "query": query,
            "toolset_filter": toolset_filter,
        },
        ensure_ascii=False,
    )


registry.register(
    name="tool_search",
    toolset="control",
    schema=_SCHEMA,
    handler=_handle_tool_search,
)


_DESCRIBE_SCHEMA: dict[str, Any] = {
    "name": "tool_describe",
    "description": (
        "Return the exact schema for one deferred capability in this request. "
        "Copy capability_search.matches[].capability_id exactly. A bare tool "
        "name is accepted only when it has one unique match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability_id": {
                "type": "string",
                "description": (
                    "Prefer the complete capability_id returned by "
                    "capability_search, for example builtin:desktop_create_file."
                ),
            }
        },
        "required": ["capability_id"],
    },
}

_ACTIVATE_SCHEMA: dict[str, Any] = {
    "name": "tool_activate",
    "description": "Activate a previously described capability for the next model iteration.",
    "parameters": {
        "type": "object",
        "properties": {
            "capability_id": {"type": "string"},
            "schema_hash": {"type": "string"},
            "describe_nonce": {"type": "string"},
        },
        "required": ["capability_id", "schema_hash", "describe_nonce"],
    },
}


def register_capability_bridge_tools(
    target_registry,
    service: ToolCapabilityBridgeService,
) -> None:
    """Replace legacy search and add describe/activate for Context OS ON."""

    def search_handler(args: dict[str, Any], _task_id: str) -> str:
        try:
            result = service.search(
                str(args.get("query", "")),
                limit=int(args.get("limit", 10) or 10),
                cursor=int(args.get("cursor", 0) or 0),
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "retriable": False})

    def describe_handler(args: dict[str, Any], _task_id: str) -> str:
        requested = str(args.get("capability_id", ""))
        try:
            return json.dumps(
                service.describe(requested),
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            payload: dict[str, Any] = {
                "error": error,
                "retriable": False,
            }
            if error == "capability_denied":
                try:
                    suggestions = service.suggestions(requested)
                except Exception:  # noqa: BLE001
                    suggestions = []
                payload.update(
                    {
                        "requested_capability_id": requested,
                        "suggestions": suggestions,
                        "replan_required": True,
                        "next_action": (
                            "Do not retry guessed capability ids. Call tool_search "
                            "with a short query, then copy one returned full "
                            "capability_id exactly."
                        ),
                    }
                )
            return json.dumps(payload, ensure_ascii=False)

    def activate_handler(args: dict[str, Any], _task_id: str) -> str:
        try:
            proposal = service.activate(
                str(args.get("capability_id", "")),
                str(args.get("schema_hash", "")),
                str(args.get("describe_nonce", "")),
            )
            return json.dumps(
                {
                    "status": "activation_proposed",
                    "__deskpet_control": {
                        "kind": "tool_activation",
                        "base_scope_revision": proposal.base_scope_revision,
                        "nonce": proposal.nonce,
                        "capability_id": proposal.prepared_capability.ref.capability_id,
                        "schema_hash": proposal.prepared_capability.ref.schema_hash,
                        "schema": proposal.prepared_capability.schema_copy(),
                    },
                },
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "retriable": False})

    search_schema = {
        **_SCHEMA,
        "description": "Search only deferred capabilities authorized for this request.",
        "parameters": {
            **_SCHEMA["parameters"],
            "properties": {
                **_SCHEMA["parameters"]["properties"],
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "cursor": {"type": "integer", "minimum": 0},
            },
        },
    }
    for name, schema, handler in (
        ("tool_search", search_schema, search_handler),
        ("tool_describe", _DESCRIBE_SCHEMA, describe_handler),
        ("tool_activate", _ACTIVATE_SCHEMA, activate_handler),
    ):
        target_registry.register(
            name=name,
            toolset="control",
            schema=schema,
            handler=handler,
            source="builtin",
            replace_allowed=True,
            concurrency_safe=False if name == "tool_activate" else True,
            outcome_parser_id=(
                "activation_proposed_v1"
                if name == "tool_activate"
                else None
            ),
        )
