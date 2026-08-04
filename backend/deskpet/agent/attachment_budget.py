# SPDX-License-Identifier: BUSL-1.1

"""Body-free accounting for provider-normalized message attachments."""
from __future__ import annotations

import base64
import copy
import math
from typing import Any, Mapping, Sequence

from deskpet.agent.assembler.bundle import AttachmentRef

_TEXT_TYPES = {"text", "input_text", "output_text"}
_UNKNOWN_MEDIA_TOKENS = 4_096


def normalize_user_attachment_blocks(value: Any) -> list[dict[str, Any]]:
    """Return defensive copies of provider-shaped, non-text content blocks."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("type") or "").strip()
        if not kind or kind in _TEXT_TYPES:
            continue
        blocks.append(copy.deepcopy(dict(raw)))
    return blocks


def append_user_attachment_blocks(
    messages: list[dict[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach normalized blocks to the final user turn without persisting bodies."""

    normalized = normalize_user_attachment_blocks(blocks)
    if not normalized:
        return messages
    result = [dict(message) for message in messages]
    for index in range(len(result) - 1, -1, -1):
        if result[index].get("role") != "user":
            continue
        content = result[index].get("content")
        if isinstance(content, list):
            parts = copy.deepcopy(content)
        else:
            parts = [{"type": "text", "text": str(content or "")}]
        result[index]["content"] = [*parts, *normalized]
        return result
    return result


def collect_attachment_budget(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[tuple[AttachmentRef, ...], int]:
    """Describe non-text blocks using indexes and estimates only, never bodies."""

    refs: list[AttachmentRef] = []
    total = 0
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for content_index, raw in enumerate(content):
            if not isinstance(raw, Mapping):
                continue
            media_type = str(raw.get("type") or "").strip()
            if not media_type or media_type in _TEXT_TYPES:
                continue
            byte_size = _content_block_size(raw)
            explicit = raw.get("estimated_tokens")
            if isinstance(explicit, (int, float)) and explicit >= 0:
                tokens = int(explicit)
                method = "content_block_estimate"
            elif byte_size > 0:
                # Conservative provider-agnostic upper bound until an adapter
                # exposes its tokenizer/media estimator. The body is not copied
                # into the ref or diagnostic report.
                tokens = max(1, math.ceil(byte_size / 3))
                method = "encoded_bytes_upper_bound"
            else:
                tokens = _UNKNOWN_MEDIA_TOKENS
                method = "unknown_media_upper_bound"
            refs.append(
                AttachmentRef(
                    fragment_id=f"attachment:{message_index}:{content_index}",
                    message_index=message_index,
                    content_index=content_index,
                    media_type=media_type,
                    byte_size=byte_size,
                    estimated_tokens=tokens,
                    estimate_method=method,
                )
            )
            total += tokens
    return tuple(refs), total


def _content_block_size(block: Mapping[str, Any]) -> int:
    for candidate in _candidate_payloads(block):
        if isinstance(candidate, (bytes, bytearray)):
            return len(candidate)
        if not isinstance(candidate, str) or not candidate:
            continue
        encoded = candidate.split(",", 1)[1] if candidate.startswith("data:") and "," in candidate else candidate
        try:
            return len(base64.b64decode(encoded, validate=True))
        except Exception:
            if candidate.startswith(("http://", "https://")):
                continue
            return len(candidate.encode("utf-8"))
    return 0


def _candidate_payloads(block: Mapping[str, Any]):
    for key in ("data", "file_data", "source"):
        value = block.get(key)
        if isinstance(value, Mapping):
            for nested in ("data", "url", "file_data"):
                if value.get(nested) is not None:
                    yield value[nested]
        elif value is not None:
            yield value
    for key in ("image_url", "input_audio", "audio_url", "file"):
        value = block.get(key)
        if isinstance(value, Mapping):
            for nested in ("data", "url", "file_data"):
                if value.get(nested) is not None:
                    yield value[nested]
        elif value is not None:
            yield value


__all__ = [
    "append_user_attachment_blocks",
    "collect_attachment_budget",
    "normalize_user_attachment_blocks",
]
