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
import logging
import re
from typing import Any

from .capabilities import ToolCapabilityBridgeService

logger = logging.getLogger(__name__)

_legacy_registry = None

# Stable, model-facing rejection codes may only use this alphabet so a handler
# exception can never smuggle a path, stack frame or secret into
# ``ToolResult.error_code``.  Anything else collapses to the opaque default.
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9_:.\-]{1,200}$")

_ACTIVATION_NEXT_ACTIONS: dict[str, str] = {
    "activation_schema_hash_stale": (
        "Call tool_describe again for this capability_id and copy the "
        "top-level schema_hash and describe_nonce exactly into tool_activate."
    ),
    "catalog_describe_nonce_invalid": (
        "The describe_nonce is stale or wrong. Call tool_describe again and "
        "copy the top-level describe_nonce exactly into tool_activate."
    ),
    "catalog_capability_not_found": (
        "Do not retry guessed capability ids. Call tool_search with a short "
        "query, then copy one returned full capability_id exactly."
    ),
    "capability_denied": (
        "Do not retry guessed capability ids. Call tool_search with a short "
        "query, then copy one returned full capability_id exactly."
    ),
    "catalog_capability_already_direct": (
        "This tool is already available directly; call it now without "
        "tool_activate."
    ),
    "activation_scope_stale": (
        "The activation scope moved. Call tool_describe again and retry "
        "tool_activate once with the fresh describe_nonce."
    ),
}
_DEFAULT_ACTIVATION_NEXT_ACTION = (
    "Do not retry the same tool_activate arguments. Call tool_search again "
    "and pick an activatable match, or continue with the tools already "
    "exposed."
)


def _classify_bridge_error(exc: BaseException) -> tuple[str, str | None]:
    """Map a bridge exception onto (stable_code, optional_reason).

    Accepts SDK ``RuntimeToolCatalogError.code`` and the Host's own stable
    ``RuntimeError("code")`` / ``RuntimeError("code:reason")`` messages; every
    other message collapses to ``tool_activation_failed``.
    """

    raw = str(getattr(exc, "code", None) or exc or "").strip()
    code, _sep, reason = raw.partition(":")
    if not _SAFE_ERROR_CODE.fullmatch(code):
        return "tool_activation_failed", None
    if reason and not _SAFE_ERROR_CODE.fullmatch(reason):
        reason = "unspecified"
    return code, reason or None


def _activation_rejection(
    exc: BaseException, requested_capability_id: str
) -> dict[str, Any]:
    code, reason = _classify_bridge_error(exc)
    capability_id = (
        requested_capability_id
        if _SAFE_CAPABILITY_ID.fullmatch(requested_capability_id)
        else "<invalid capability_id>"
    )
    if code == "tool_unavailable":
        from deskpet.sdk_adapters.tool_authority import (
            unavailable_capability_next_action,
        )

        next_action = unavailable_capability_next_action(reason or "")
        message = (
            f"tool_activate rejected for {capability_id}: not activatable in "
            f"this Run (availability_reason={reason or 'unspecified'}). "
            f"Do not retry tool_activate for it. {next_action}"
        )
    else:
        next_action = _ACTIVATION_NEXT_ACTIONS.get(
            code, _DEFAULT_ACTIVATION_NEXT_ACTION
        )
        message = f"tool_activate rejected for {capability_id}: {code}. {next_action}"
    logger.warning(
        "tool_activate.rejected",
        extra={
            "tool": "tool_activate",
            "code": code,
            "reason": reason,
            "capability_id": capability_id,
        },
    )
    payload: dict[str, Any] = {
        "error": code if reason is None else f"{code}:{reason}",
        "error_code": code,
        "public_message": message,
        "retriable": False,
        "replan_required": True,
        "requested_capability_id": capability_id,
        "next_action": next_action,
    }
    if reason is not None:
        payload["availability_reason"] = reason
    return payload

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
    if _legacy_registry is None:
        raise RuntimeError("legacy Tool search registry is not bound")
    for spec in _legacy_registry.all_specs():
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


def register_static_tools(registry) -> None:
    global _legacy_registry
    _legacy_registry = registry
    registry.register(
        name="tool_search", toolset="control", schema=_SCHEMA,
        handler=_handle_tool_search,
    )


_DESCRIBE_SCHEMA: dict[str, Any] = {
    "name": "tool_describe",
    "description": (
        "Return the exact schema for one deferred capability in this request. "
        "Copy capability_search.matches[].capability_id exactly. A bare tool "
        "name is accepted only when it has one unique match. To activate, copy "
        "the returned top-level capability_id, schema_hash, and describe_nonce "
        "exactly into tool_activate."
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
    "description": (
        "Activate a previously described capability for the next model iteration. "
        "Copy the three top-level activation fields returned by tool_describe "
        "exactly; never derive or substitute a hash from the nested projection."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability_id": {
                "type": "string",
                "description": "Exact top-level capability_id from tool_describe.",
            },
            "schema_hash": {
                "type": "string",
                "description": "Exact top-level schema_hash from tool_describe.",
            },
            "describe_nonce": {
                "type": "string",
                "description": "Exact top-level describe_nonce from tool_describe.",
            },
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
            code, _reason = _classify_bridge_error(exc)
            if code != "tool_activation_failed":
                # Stable code survives the SDK ``_result`` mapping instead of
                # collapsing into an opaque ``tool_failed``.
                payload["error_code"] = code
            if code in {"capability_denied", "catalog_capability_not_found"}:
                try:
                    suggestions = service.suggestions(requested)
                except Exception:  # noqa: BLE001
                    suggestions = []
                next_action = (
                    "Do not retry guessed capability ids. Call tool_search "
                    "with a short query, then copy one returned full "
                    "capability_id exactly."
                )
                payload.update(
                    {
                        "requested_capability_id": requested,
                        "suggestions": suggestions,
                        "replan_required": True,
                        "next_action": next_action,
                        "public_message": (
                            f"tool_describe rejected: {code}. {next_action}"
                        ),
                    }
                )
            return json.dumps(payload, ensure_ascii=False)

    def activate_handler(args: dict[str, Any], _task_id: str) -> str:
        requested = str(args.get("capability_id", ""))
        try:
            proposal = service.activate(
                requested,
                str(args.get("schema_hash", "")),
                str(args.get("describe_nonce", "")),
            )
            receipt_json = getattr(proposal, "to_json", None)
            if callable(receipt_json):
                receipt = receipt_json()
                if receipt.get("schema") == "runtime_tool_activation_receipt/v1":
                    return json.dumps(receipt, ensure_ascii=False)
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
            # UI-B (S5b phase-4): the raw ``str(exc)`` used to be flattened
            # into an opaque ``tool_failed`` by the SDK result mapping with no
            # Host log, so the model retried the identical call until the
            # repeated-tool limit failed the whole Run.  Return a stable code
            # plus one actionable next step and log the rejection.
            return json.dumps(
                _activation_rejection(exc, requested), ensure_ascii=False
            )

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
