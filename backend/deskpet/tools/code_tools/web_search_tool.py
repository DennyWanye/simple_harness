# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Async production adapter for the shared in-process SearchGateway."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


async def web_search(args: dict[str, Any], task_id: str = "") -> str:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return json.dumps({"error": "query (string) is required"})

    from deskpet.retrieval.contracts import SearchRequest
    from deskpet.retrieval.runtime import get_default_gateway

    try:
        max_results = max(1, min(int(args.get("max_results", 5)), 10))
        hydrate_top = max(0, min(int(args.get("hydrate_top", 0)), 3))
    except (TypeError, ValueError):
        max_results, hydrate_top = 5, 0
    request_kwargs = {
        "query": query,
        "max_results": max_results,
        "mode": "quick",
        "hydrate_top": hydrate_top,
    }
    if task_id:
        request_kwargs["request_id"] = task_id
    response = await get_default_gateway().search(SearchRequest(**request_kwargs))
    payload = response.to_dict()
    if not payload["results"]:
        payload["error"] = "all configured search providers failed or returned empty"
    return json.dumps(payload, ensure_ascii=False)


def build_web_search_handler(search_gateway):
    """Bind the production SearchGateway explicitly for SDK composition."""

    if not callable(getattr(search_gateway, "search", None)):
        raise TypeError("search_gateway.search must be callable")

    async def handler(args: dict[str, Any], task_id: str = "") -> str:
        query = args.get("query")
        if not query or not isinstance(query, str):
            return json.dumps({"error": "query (string) is required"})
        from deskpet.retrieval.contracts import SearchRequest

        try:
            max_results = max(1, min(int(args.get("max_results", 5)), 10))
            hydrate_top = max(0, min(int(args.get("hydrate_top", 0)), 3))
        except (TypeError, ValueError):
            max_results, hydrate_top = 5, 0
        kwargs = {
            "query": query,
            "max_results": max_results,
            "mode": "quick",
            "hydrate_top": hydrate_top,
        }
        if task_id:
            kwargs["request_id"] = task_id
        response = await search_gateway.search(SearchRequest(**kwargs))
        payload = response.to_dict()
        if not payload["results"]:
            payload["error"] = "all configured search providers failed or returned empty"
        return json.dumps(payload, ensure_ascii=False)

    return handler
