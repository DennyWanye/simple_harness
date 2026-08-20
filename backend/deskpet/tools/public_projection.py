"""Server-owned public projections for tool activity shown in desktop UI."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any


_WEB_FETCH_TOOLS = frozenset({
    "web_fetch",
    "web_extract_article",
    "web_extract_page",
    "web_crawl",
    "web_read_sitemap",
    "scrapling_fetch",
})
_WEB_SEARCH_TOOLS = frozenset({
    "web_search",
    "web_search_brave",
})
_WEB_DIRECT_TOOLS = frozenset({
    # Scrapling-backed lookup whose executor result contains source_urls.
    # It is a web tool even though its public name does not use a web_* prefix.
    "gold_price_lookup",
})
_PUBLIC_OUTCOME_STATUSES = frozenset(
    {"succeeded", "failed", "partial", "rejected", "unknown"}
)


def project_public_tool_arguments(
    tool_name: object,
    arguments: object,
) -> dict[str, Any]:
    """Return the only arguments allowed to cross the tool-event UI boundary.

    Web URLs and search text remain available to the executor and internal
    history, but are replaced by stable semantic labels in desktop activity
    cards. Other tool arguments retain their existing public behavior.
    """

    name = str(tool_name or "")
    if name in _WEB_FETCH_TOOLS:
        return {"target_kind": "web_page"}
    if name in _WEB_SEARCH_TOOLS:
        return {"search_scope": "web"}
    if isinstance(arguments, Mapping):
        return copy.deepcopy(dict(arguments))
    return {}


def project_public_tool_calls(tool_calls: object) -> object:
    """Sanitize persisted assistant tool calls before history reaches UI."""

    if not isinstance(tool_calls, list):
        return tool_calls
    projected: list[object] = []
    for raw_call in tool_calls:
        if not isinstance(raw_call, Mapping):
            projected.append(raw_call)
            continue
        call = copy.deepcopy(dict(raw_call))
        raw_function = call.get("function")
        if not isinstance(raw_function, Mapping):
            projected.append(call)
            continue
        function = dict(raw_function)
        raw_arguments = function.get("arguments")
        parsed_arguments: object = raw_arguments
        if isinstance(raw_arguments, str):
            try:
                parsed_arguments = json.loads(raw_arguments)
            except (TypeError, ValueError):
                parsed_arguments = {}
        public_arguments = project_public_tool_arguments(
            function.get("name"), parsed_arguments
        )
        function["arguments"] = (
            json.dumps(public_arguments, ensure_ascii=False)
            if isinstance(raw_arguments, str)
            else public_arguments
        )
        call["function"] = function
        projected.append(call)
    return projected


def _bounded_public_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def project_public_tool_result(
    tool_name: object,
    result: object,
    *,
    outcome_status: object | None = None,
    outcome_error: object | None = None,
) -> object:
    """Return a bounded, idempotent web-result summary for live/history UI.

    The executor payload is deliberately not consulted for arbitrary error
    detail.  Only the canonical outcome and its explicitly public error fields
    may cross this boundary.  Reading an already projected history row is also
    supported so hydration cannot turn a failed web call green.
    """

    name = str(tool_name or "")
    if (
        name not in _WEB_FETCH_TOOLS
        and name not in _WEB_SEARCH_TOOLS
        and name not in _WEB_DIRECT_TOOLS
    ):
        return copy.deepcopy(result)
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError):
            parsed = None
    parsed_mapping = parsed if isinstance(parsed, Mapping) else {}
    summary_payload = parsed_mapping.get("value")
    if not isinstance(summary_payload, Mapping):
        summary_payload = parsed_mapping

    item_count = 0
    persisted_item_count = summary_payload.get("item_count")
    if (
        isinstance(persisted_item_count, int)
        and not isinstance(persisted_item_count, bool)
        and persisted_item_count >= 0
    ):
        # History hydration can project an already-bounded public summary a
        # second time. Preserve its count instead of recomputing zero from the
        # intentionally removed raw results. Keep the public scalar bounded.
        item_count = min(persisted_item_count, 10_000)
    if isinstance(summary_payload, Mapping):
        for key in ("results", "pages", "links", "items"):
            value = summary_payload.get(key)
            if isinstance(value, list):
                item_count = max(item_count, len(value))
    item_count = min(item_count, 10_000)

    # Explicit presenter data wins.  Otherwise retain a previously projected
    # status during SessionDB hydration.  The namespaced branch reads the
    # short-lived v1 envelope written by older builds without exposing its raw
    # error object.
    persisted_outcome = parsed_mapping.get("status")
    legacy_outcome = parsed_mapping.get("_deskpet_tool_outcome_v1")
    if outcome_status is None and persisted_outcome not in _PUBLIC_OUTCOME_STATUSES:
        if isinstance(legacy_outcome, Mapping):
            persisted_outcome = legacy_outcome.get("outcome")
    canonical_status = (
        outcome_status
        if outcome_status in _PUBLIC_OUTCOME_STATUSES
        else persisted_outcome
        if persisted_outcome in _PUBLIC_OUTCOME_STATUSES
        else "succeeded"
    )

    public_error = outcome_error if isinstance(outcome_error, Mapping) else None
    if public_error is None and canonical_status != "succeeded":
        public_error = parsed_mapping
        if isinstance(legacy_outcome, Mapping) and not (
            parsed_mapping.get("error_code") or parsed_mapping.get("public_message")
        ):
            legacy_error = legacy_outcome.get("error")
            if isinstance(legacy_error, Mapping):
                public_error = legacy_error

    projected: dict[str, Any] = {
        "result_kind": "web_search" if name in _WEB_SEARCH_TOOLS else "web_content",
        "status": canonical_status,
        "item_count": item_count,
    }
    if canonical_status != "succeeded" and isinstance(public_error, Mapping):
        error_code = _bounded_public_text(
            public_error.get("code") or public_error.get("error_code"),
            limit=120,
        )
        public_message = _bounded_public_text(
            public_error.get("message") or public_error.get("public_message"),
            limit=500,
        )
        if error_code is not None:
            projected["error_code"] = error_code
        if public_message is not None:
            projected["public_message"] = public_message
    return projected


__all__ = [
    "project_public_tool_arguments",
    "project_public_tool_calls",
    "project_public_tool_result",
]
