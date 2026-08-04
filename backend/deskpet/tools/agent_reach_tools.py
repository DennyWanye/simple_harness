"""DeskPet tool surface backed directly by Agent-Reach."""

from __future__ import annotations

import json
from typing import Any

from .agent_reach_port import agent_reach_port
from .registry import registry


_DOCTOR_SCHEMA: dict[str, Any] = {
    "name": "agent_reach_doctor",
    "description": (
        "Check Agent-Reach internet channels and active upstream backends. "
        "Use before platform-specific research or when an internet channel fails."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional channel names such as github, web, youtube, rss.",
            }
        },
        "required": [],
    },
}


_READ_SCHEMA: dict[str, Any] = {
    "name": "agent_reach_read",
    "description": (
        "Read a public URL through Agent-Reach channel routing and its active backend. "
        "For GitHub or another platform URL, Agent-Reach selects the platform and "
        "falls back to its Web/Jina Reader backend when needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute public http(s) URL."},
            "max_chars": {"type": "integer", "default": 120000},
        },
        "required": ["url"],
    },
}


def _doctor(args: dict[str, Any], task_id: str) -> str:
    del task_id
    channels = args.get("channels")
    names = [str(value) for value in channels] if isinstance(channels, list) else None
    return json.dumps(
        {"ok": True, "provider": "agent-reach", "channels": agent_reach_port.doctor(names=names)},
        ensure_ascii=False,
    )


def _read(args: dict[str, Any], task_id: str) -> str:
    del task_id
    result = agent_reach_port.read_url(
        str(args.get("url") or ""),
        max_chars=int(args.get("max_chars", 120000) or 120000),
    )
    return json.dumps(
        {
            "ok": result.ok,
            "provider": "agent-reach",
            "channel": result.channel,
            "active_backend": result.active_backend,
            "status": result.status,
            "reason_code": result.reason_code,
            "message": result.message,
            "url": result.url,
            "title": result.title,
            "content": result.text,
        },
        ensure_ascii=False,
    )


registry.register(
    "agent_reach_doctor",
    "web",
    _DOCTOR_SCHEMA,
    _doctor,
    permission_category="network",
    timeout_seconds=30.0,
)

registry.register(
    "agent_reach_read",
    "web",
    _READ_SCHEMA,
    _read,
    permission_category="network",
    timeout_seconds=45.0,
)


__all__ = ["_doctor", "_read"]
