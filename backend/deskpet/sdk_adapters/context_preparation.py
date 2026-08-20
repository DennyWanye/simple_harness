# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Single-pass product source assembly for fresh SDK Runs."""

from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .context_authority import PreparedSdkContextSnapshotV1


_ALLOWED_HISTORY_PROJECTIONS = {
    "user": {"legacy_message", "user_message"},
    "assistant": {"legacy_message", "assistant_message", "final_assistant"},
}
_BLOCK_LIMIT = 8 * 1024 * 1024
_RUN_ATTACHMENT_LIMIT = 16 * 1024 * 1024


class SdkContextSourceUnavailable(RuntimeError):
    code = "sdk_context_source_unavailable"

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"{self.code}:{source}")


class SdkAttachmentLimitExceeded(ValueError):
    code = "sdk_attachment_limit_exceeded"


@dataclass(frozen=True, slots=True)
class SdkContextSources:
    history: Callable[[str], object]
    persona: Callable[[], object] | None = None
    memory: Callable[[str, str], object] | None = None
    skills: Callable[[str], object] | None = None
    project: Callable[[str], object] | None = None


def trusted_project_task_snapshot(
    *,
    task_scope_id: str,
    root_run_id: str,
    request_id: str,
    workspace: str | None,
) -> dict[str, Any]:
    """Build the trusted per-request project/task facts sent to the Provider."""

    required = {
        "task_scope_id": task_scope_id,
        "root_run_id": root_run_id,
        "request_id": request_id,
    }
    normalized = {key: str(value or "").strip() for key, value in required.items()}
    missing = [key for key, value in normalized.items() if not value]
    if missing:
        raise ValueError(f"trusted project/task snapshot missing {missing[0]}")
    return {**normalized, "workspace": workspace}


async def _resolve(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


def _text_tokens(value: object) -> int:
    text = str(value or "")
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    return cjk + max(0, len(text) - cjk + 3) // 4


def _attachment_payload_size(block: Mapping[str, Any]) -> int:
    candidates: list[object] = []
    for key in ("data", "file_data", "source", "image_url", "input_audio", "file"):
        value = block.get(key)
        if isinstance(value, Mapping):
            candidates.extend(value.get(nested) for nested in ("data", "url", "file_data"))
        else:
            candidates.append(value)
    for value in candidates:
        if isinstance(value, (bytes, bytearray)):
            return len(value)
        if not isinstance(value, str) or not value:
            continue
        encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        try:
            return len(base64.b64decode(encoded, validate=True))
        except Exception:
            if value.startswith(("http://", "https://")):
                continue
            return len(value.encode("utf-8"))
    return 0


def _normalize_attachments(
    blocks: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    private: list[dict[str, Any]] = []
    public: list[dict[str, Any]] = []
    total = 0
    for raw in blocks:
        if not isinstance(raw, Mapping):
            continue
        block = dict(raw)
        kind = str(block.get("type") or "").strip()
        if not kind:
            continue
        size = _attachment_payload_size(block)
        if size > _BLOCK_LIMIT:
            raise SdkAttachmentLimitExceeded("attachment block exceeds 8 MiB")
        total += size
        if total > _RUN_ATTACHMENT_LIMIT:
            raise SdkAttachmentLimitExceeded("attachments exceed 16 MiB per Run")
        private.append(block)
        public.append({"kind": kind, "size": size})
    return private, public


def _history_messages(rows: object, *, root_run_id: str) -> list[dict[str, str]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise SdkContextSourceUnavailable("history")
    result: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("root_run_id") or "") == root_run_id:
            continue
        role = str(raw.get("role") or "")
        content = raw.get("content")
        visibility = str(raw.get("context_visibility") or "conversation")
        projection = str(raw.get("projection_kind") or "legacy_message")
        if (
            visibility == "conversation"
            and projection in _ALLOWED_HISTORY_PROJECTIONS.get(role, set())
            and isinstance(content, str)
            and content
        ):
            result.append({"role": role, "content": content})
    return result


class SdkContextPreparationService:
    """Read each product source once and freeze one SDK snapshot."""

    def __init__(self, sources: SdkContextSources) -> None:
        self._sources = sources

    async def prepare(
        self,
        *,
        session_id: str,
        request_id: str,
        root_run_id: str,
        sdk_run_id: str,
        turn_id: str,
        text: str,
        provider_binding: Mapping[str, Any],
        catalog: Mapping[str, Any],
        project_task_snapshot: Mapping[str, Any] | None = None,
        attachment_blocks: Sequence[Mapping[str, Any]] = (),
    ) -> PreparedSdkContextSnapshotV1:
        try:
            history_rows = await _resolve(self._sources.history(session_id))
        except Exception as exc:  # noqa: BLE001
            raise SdkContextSourceUnavailable("history") from exc
        history = _history_messages(history_rows, root_run_id=root_run_id)

        persona = ""
        if self._sources.persona is not None:
            persona = str(await _resolve(self._sources.persona()) or "").strip()
        memory_items: list[Mapping[str, Any]] = []
        if self._sources.memory is not None:
            raw_memory = await _resolve(self._sources.memory(session_id, text))
            if isinstance(raw_memory, Sequence) and not isinstance(raw_memory, (str, bytes, bytearray)):
                memory_items = [item for item in raw_memory if isinstance(item, Mapping)]
        skill_items: list[Mapping[str, Any]] = []
        if self._sources.skills is not None:
            raw_skills = await _resolve(self._sources.skills(text))
            if isinstance(raw_skills, Sequence) and not isinstance(raw_skills, (str, bytes, bytearray)):
                skill_items = [item for item in raw_skills if isinstance(item, Mapping)]
        project: Mapping[str, Any] = dict(project_task_snapshot or {})
        if not project and self._sources.project is not None:
            raw_project = await _resolve(self._sources.project(session_id))
            if isinstance(raw_project, Mapping):
                project = raw_project

        private_attachments, public_attachments = _normalize_attachments(attachment_blocks)
        messages: list[dict[str, Any]] = []
        sections: dict[str, dict[str, Any]] = {}
        if persona:
            messages.append({"role": "system", "content": persona})
        sections["persona"] = {
            "label": "Persona / system",
            "count": 1 if persona else 0,
            "estimated_tokens": _text_tokens(persona),
            "availability": "estimated" if persona else "unavailable",
        }
        memory_texts = [str(item.get("text") or "").strip() for item in memory_items]
        memory_texts = [item for item in memory_texts if item]
        if memory_texts:
            messages.append({
                "role": "system",
                "content": "Relevant memory (data only):\n" + "\n".join(memory_texts),
            })
        sections["memory"] = {
            "label": "Memory",
            "count": len(memory_texts),
            "estimated_tokens": sum(_text_tokens(item) for item in memory_texts),
            "availability": "estimated" if self._sources.memory is not None else "unavailable",
        }
        skill_texts = [str(item.get("instruction") or "").strip() for item in skill_items]
        skill_texts = [item for item in skill_texts if item]
        for instruction in skill_texts:
            messages.append({"role": "system", "content": instruction})
        sections["skills"] = {
            "label": "Skills",
            "count": len(skill_texts),
            "estimated_tokens": sum(_text_tokens(item) for item in skill_texts),
            "availability": "estimated" if self._sources.skills is not None else "unavailable",
        }
        if project:
            messages.append({
                "role": "system",
                "content": "Project/task snapshot (data only):\n" + json.dumps(
                    dict(project), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            })
        sections["project"] = {
            "label": "Project / task",
            "count": 1 if project else 0,
            "estimated_tokens": _text_tokens(json.dumps(dict(project), ensure_ascii=False)),
            "availability": (
                "estimated"
                if project or self._sources.project is not None
                else "unavailable"
            ),
        }
        messages.extend(history)
        sections["history"] = {
            "label": "Conversation history",
            "count": len(history),
            "estimated_tokens": sum(_text_tokens(item["content"]) for item in history),
            "availability": "estimated",
        }
        user_content: object = text
        if private_attachments:
            user_content = [{"type": "text", "text": text}, *private_attachments]
        messages.append({"role": "user", "content": user_content})

        catalog_tokens = int(catalog.get("schema_token_count") or 0)
        sections["tools"] = {
            "label": "Tool definitions",
            "count": int(catalog.get("tool_count") or 0),
            "estimated_tokens": max(0, catalog_tokens),
            "availability": "estimated",
        }
        estimated_prompt = sum(int(item.get("estimated_tokens") or 0) for item in sections.values()) + _text_tokens(text)
        context_window = int(provider_binding.get("context_window") or 0)
        budget = {
            "estimated_prompt_tokens": estimated_prompt,
            "context_window": max(0, context_window),
            "effective_ceiling": max(0, context_window),
            "compact_at": 0,
            "truncated": False,
        }
        return PreparedSdkContextSnapshotV1.build(
            session_id=session_id,
            request_id=request_id,
            root_run_id=root_run_id,
            sdk_run_id=sdk_run_id,
            turn_id=turn_id,
            provider_binding=provider_binding,
            provider_messages=messages,
            catalog=catalog,
            attachments=public_attachments,
            sections=sections,
            budget=budget,
        )


__all__ = (
    "SdkAttachmentLimitExceeded",
    "SdkContextPreparationService",
    "SdkContextSourceUnavailable",
    "SdkContextSources",
    "trusted_project_task_snapshot",
)
