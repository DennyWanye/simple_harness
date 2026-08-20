# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product-owned immutable Context snapshot contracts.

The full snapshot is private and may be persisted only in the SDK execution
store.  ``DefaultDenySnapshotRedactor`` produces the bounded projection that
SessionDB and the Inspector may retain.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from simple_harness import freeze_json, thaw_json


class SnapshotContractConflict(RuntimeError):
    code = "sdk_context_snapshot_conflict"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


def canonical_json(value: object) -> str:
    """Return deterministic UTF-8 JSON for hashing and CAS comparisons."""

    return json.dumps(
        thaw_json(freeze_json(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


@dataclass(frozen=True, slots=True)
class PreparedSdkContextSnapshotV1:
    snapshot_id: str
    snapshot_version: int
    snapshot_fingerprint: str
    session_id: str
    request_id: str
    root_run_id: str
    sdk_run_id: str
    turn_id: str
    provider_binding: object
    provider_messages: tuple[object, ...]
    catalog: object
    attachments: tuple[object, ...]
    sections: object
    budget: object

    @classmethod
    def build(
        cls,
        *,
        session_id: str,
        request_id: str,
        root_run_id: str,
        sdk_run_id: str,
        turn_id: str,
        provider_binding: Mapping[str, Any],
        provider_messages: Sequence[Mapping[str, Any]],
        catalog: Mapping[str, Any],
        attachments: Sequence[Mapping[str, Any]] = (),
        sections: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        snapshot_version: int = 1,
    ) -> "PreparedSdkContextSnapshotV1":
        if snapshot_version != 1:
            raise ValueError("PreparedSdkContextSnapshotV1 requires version 1")
        identity = {
            "snapshot_version": snapshot_version,
            "session_id": _required_text(session_id, "session_id"),
            "request_id": _required_text(request_id, "request_id"),
            "root_run_id": _required_text(root_run_id, "root_run_id"),
            "sdk_run_id": _required_text(sdk_run_id, "sdk_run_id"),
            "turn_id": _required_text(turn_id, "turn_id"),
            "provider_binding": provider_binding,
            "provider_messages": list(provider_messages),
            "catalog": catalog,
            "attachments": list(attachments),
            "sections": sections or {},
            "budget": budget or {},
        }
        fingerprint = canonical_sha256(identity)
        return cls(
            snapshot_id=f"sdk-context:{fingerprint}",
            snapshot_version=1,
            snapshot_fingerprint=fingerprint,
            session_id=identity["session_id"],
            request_id=identity["request_id"],
            root_run_id=identity["root_run_id"],
            sdk_run_id=identity["sdk_run_id"],
            turn_id=identity["turn_id"],
            provider_binding=freeze_json(provider_binding),
            provider_messages=tuple(freeze_json(item) for item in provider_messages),
            catalog=freeze_json(catalog),
            attachments=tuple(freeze_json(item) for item in attachments),
            sections=freeze_json(sections or {}),
            budget=freeze_json(budget or {}),
        )

    def private_record(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_version": self.snapshot_version,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "root_run_id": self.root_run_id,
            "sdk_run_id": self.sdk_run_id,
            "turn_id": self.turn_id,
            "provider_binding": thaw_json(self.provider_binding),
            "provider_messages": [thaw_json(item) for item in self.provider_messages],
            "catalog": thaw_json(self.catalog),
            "attachments": [thaw_json(item) for item in self.attachments],
            "sections": thaw_json(self.sections),
            "budget": thaw_json(self.budget),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.private_record())


_SENSITIVE_TEXT = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[a-z0-9._-]+|api[_ -]?key|cookie\s*:|"
    r"(?:^|\s)(?:/Users/|/home/|[A-Za-z]:\\)|reasoning_content|private[_ -]?reasoning)"
)


class DefaultDenySnapshotRedactor:
    """Build a public snapshot from a small explicit allowlist.

    No provider messages, attachment body/path, headers, secret fields or
    reasoning fields are ever traversed into the result.
    """

    _SECTION_FIELDS = frozenset(
        {"kind", "label", "count", "estimated_tokens", "availability", "ref"}
    )

    @staticmethod
    def _bounded_text(value: object, limit: int = 160) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or _SENSITIVE_TEXT.search(text):
            return None
        return text[:limit]

    def redact(self, snapshot: PreparedSdkContextSnapshotV1) -> dict[str, Any]:
        binding = thaw_json(snapshot.provider_binding)
        catalog = thaw_json(snapshot.catalog)
        sections = thaw_json(snapshot.sections)
        budget = thaw_json(snapshot.budget)
        attachments = [thaw_json(item) for item in snapshot.attachments]

        public_sections: list[dict[str, Any]] = []
        if isinstance(sections, Mapping):
            iterable = (
                {"kind": kind, **(value if isinstance(value, Mapping) else {})}
                for kind, value in sections.items()
            )
        elif isinstance(sections, list):
            iterable = (item for item in sections if isinstance(item, Mapping))
        else:
            iterable = ()
        for raw in iterable:
            item: dict[str, Any] = {}
            for key in self._SECTION_FIELDS:
                value = raw.get(key)
                if key in {"kind", "label", "availability", "ref"}:
                    bounded = self._bounded_text(value)
                    if bounded is not None:
                        item[key] = bounded
                elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    item[key] = value
            if item.get("kind"):
                public_sections.append(item)

        public_attachments: list[dict[str, Any]] = []
        for raw in attachments:
            if not isinstance(raw, Mapping):
                continue
            item: dict[str, Any] = {}
            kind = self._bounded_text(raw.get("kind"), 40)
            size = raw.get("size")
            if kind:
                item["kind"] = kind
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                item["size"] = size
            if item:
                public_attachments.append(item)

        public_binding: dict[str, Any] = {}
        if isinstance(binding, Mapping):
            for key in ("provider_id", "model_id", "reasoning_mode", "reasoning_effort"):
                value = self._bounded_text(binding.get(key))
                if value is not None:
                    public_binding[key] = value
            for key in ("binding_epoch", "context_window"):
                value = binding.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_binding[key] = value

        public_catalog: dict[str, Any] = {}
        if isinstance(catalog, Mapping):
            for key in ("content_fingerprint",):
                value = self._bounded_text(catalog.get(key))
                if value is not None:
                    public_catalog[key] = value
            for key in ("generation", "schema_token_count", "tool_count"):
                value = catalog.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_catalog[key] = value
            names = catalog.get("tool_names")
            if isinstance(names, (list, tuple)):
                public_catalog["tool_names"] = [
                    bounded for raw in names
                    if (bounded := self._bounded_text(raw, 80)) is not None
                ][:512]

        public_budget: dict[str, Any] = {}
        if isinstance(budget, Mapping):
            for key in (
                "estimated_prompt_tokens", "context_window", "effective_ceiling",
                "compact_at",
            ):
                value = budget.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_budget[key] = value
            if isinstance(budget.get("truncated"), bool):
                public_budget["truncated"] = budget["truncated"]
        return {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_version": snapshot.snapshot_version,
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "session_id": snapshot.session_id,
            "request_id": snapshot.request_id,
            "root_run_id": snapshot.root_run_id,
            "sdk_run_id": snapshot.sdk_run_id,
            "turn_id": snapshot.turn_id,
            "provider_binding": public_binding,
            "catalog": public_catalog,
            "attachments": public_attachments,
            "sections": public_sections,
            "budget": public_budget,
        }


__all__ = [
    "DefaultDenySnapshotRedactor",
    "PreparedSdkContextSnapshotV1",
    "SnapshotContractConflict",
    "canonical_json",
    "canonical_sha256",
]
