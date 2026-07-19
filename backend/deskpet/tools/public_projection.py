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


def project_public_tool_result(tool_name: object, result: object) -> object:
    """Return a bounded web-result summary suitable for message cards."""

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
    item_count = 0
    if isinstance(parsed, Mapping):
        for key in ("results", "pages", "links", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                item_count = max(item_count, len(value))
    return {
        "result_kind": "web_search" if name in _WEB_SEARCH_TOOLS else "web_content",
        "status": "completed",
        "item_count": item_count,
    }


__all__ = [
    "project_public_tool_arguments",
    "project_public_tool_calls",
    "project_public_tool_result",
]
