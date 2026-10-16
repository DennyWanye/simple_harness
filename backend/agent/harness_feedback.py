"""Compatibility observations for AgentLoop outcomes executed by Harness."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any


_VISUAL_CAPTURE_TOOLS = frozenset({"screen_capture", "window_capture"})
_SUPPORTED_IMAGE_MIME = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
_MAX_TOOL_IMAGE_BYTES = 10 * 1024 * 1024


def _extract_visual_attachment(
    *, tool_name: str, content: str, call_id: str
) -> tuple[str, list[dict[str, Any]]]:
    """Move capture bytes from tool JSON into a provider-shaped media block."""

    if tool_name not in _VISUAL_CAPTURE_TOOLS:
        return content, []
    try:
        payload = json.loads(content)
        outcome = payload.get("outcome")
        value = outcome.get("value") if isinstance(outcome, Mapping) else None
        if not isinstance(value, dict):
            return content, []
        encoded = value.get("image_base64")
        mime = str(value.get("image_mime") or "").strip().lower()
        if not isinstance(encoded, str) or mime not in _SUPPORTED_IMAGE_MIME:
            return content, []
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > _MAX_TOOL_IMAGE_BYTES:
            return content, []
    except (TypeError, ValueError, json.JSONDecodeError):
        return content, []

    value.pop("image_base64", None)
    digest = hashlib.sha256(raw).hexdigest()
    value["media_attachment"] = {
        "attached": True,
        "bytes": len(raw),
        "mime": mime,
        "sha256": digest,
    }
    bounded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    blocks = [
        {
            "type": "text",
            "text": (
                f"Visual evidence from {tool_name} (call_id={call_id}). "
                "Inspect this image before choosing the next action."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{encoded}",
                "detail": "low",
            },
            "estimated_tokens": max(1, (len(raw) + 2) // 3),
        },
    ]
    return bounded, blocks
