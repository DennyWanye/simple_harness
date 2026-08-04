# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-only context metadata carried alongside OpenAI-style messages.

``__deskpet_context`` is deliberately a message sidecar rather than part of
the provider protocol.  Producers can keep metadata aligned through normal
list slicing/deepcopy/retry operations, while every provider must call
``split_wire_messages`` before serialization so the reserved key never leaves
the process.
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping, MutableSequence, Sequence


CONTEXT_MESSAGE_META_KEY = "__deskpet_context"

Placement = Literal["prefix", "transcript", "control"]
Lifetime = Literal["platform", "stable", "task", "retrieved", "history", "current"]
TrimPolicy = Literal["never", "truncate", "drop", "summarize", "page_in"]

_PLACEMENTS = frozenset({"prefix", "transcript", "control"})
_LIFETIMES = frozenset(
    {"platform", "stable", "task", "retrieved", "history", "current"}
)
_TRIM_POLICIES = frozenset({"never", "truncate", "drop", "summarize", "page_in"})


@dataclass(frozen=True)
class ContextMessageMeta:
    """Canonical metadata aligned one-to-one with a context message."""

    placement: Placement
    lifetime: Lifetime
    source: str
    protected: bool = False
    trim_policy: TrimPolicy = "drop"
    causal_group_id: str | None = None
    anchor_after: str | None = None
    fragment_id: str | None = None
    priority: int = 50
    reason: str = ""
    cache_scope: str | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.placement not in _PLACEMENTS:
            raise ValueError(f"unsupported context placement: {self.placement!r}")
        if self.lifetime not in _LIFETIMES:
            raise ValueError(f"unsupported context lifetime: {self.lifetime!r}")
        if self.trim_policy not in _TRIM_POLICIES:
            raise ValueError(f"unsupported context trim policy: {self.trim_policy!r}")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("context metadata source must be non-empty")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("context metadata priority must be an integer")

    def to_mapping(self) -> dict[str, Any]:
        """Return a stable, JSON-friendly sidecar mapping."""

        return {
            "placement": self.placement,
            "lifetime": self.lifetime,
            "source": self.source,
            "protected": self.protected,
            "trim_policy": self.trim_policy,
            "causal_group_id": self.causal_group_id,
            "anchor_after": self.anchor_after,
            "fragment_id": self.fragment_id,
            "priority": self.priority,
            "reason": self.reason,
            "cache_scope": self.cache_scope,
            "meta": copy.deepcopy(dict(self.meta)),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContextMessageMeta":
        if not isinstance(value, Mapping):
            raise TypeError("context message metadata must be a mapping")
        return cls(
            placement=value.get("placement"),
            lifetime=value.get("lifetime"),
            source=value.get("source"),
            protected=bool(value.get("protected", False)),
            trim_policy=value.get("trim_policy", "drop"),
            causal_group_id=value.get("causal_group_id"),
            anchor_after=value.get("anchor_after"),
            fragment_id=value.get("fragment_id"),
            priority=value.get("priority", 50),
            reason=str(value.get("reason", "")),
            cache_scope=value.get("cache_scope"),
            meta=value.get("meta") or {},
        )


@dataclass(frozen=True)
class ProviderAttemptOptions:
    """Host-only per-attempt options transported through a ContextVar.

    ``cache_boundary`` is a zero-based *inclusive* message index.  Public
    provider method signatures intentionally stay unchanged; fake and
    third-party providers can ignore this context entirely.
    """

    cache_boundary: int | None = None
    cache_fingerprint: str | None = None
    purpose: str | None = None
    session_id: str | None = None
    root_run_id: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None
    workload_class: str | None = None
    callsite_id: str | None = None
    detached: bool = False

    def __post_init__(self) -> None:
        if self.cache_boundary is not None:
            if isinstance(self.cache_boundary, bool) or not isinstance(
                self.cache_boundary, int
            ):
                raise TypeError("cache_boundary must be an integer or None")
            if self.cache_boundary < 0:
                raise ValueError("cache_boundary must be non-negative")
        if self.workload_class == "session-auxiliary" and (
            self.detached or not self.session_id or not self.root_run_id
        ):
            raise ValueError(
                "session-auxiliary attempt requires session_id/root_run_id"
            )
        if self.workload_class == "system-maintenance" and (
            not self.detached or self.session_id is not None or self.root_run_id is not None
        ):
            raise ValueError(
                "system-maintenance attempt must be detached without Session/Root"
            )


_PROVIDER_ATTEMPT_OPTIONS: ContextVar[ProviderAttemptOptions | None] = ContextVar(
    "deskpet_provider_attempt_options", default=None
)

_KNOWN_PROVIDER_PURPOSES = frozenset(
    {
        "agent_response",
        "force_finish",
        "capability_gate",
        "classifier",
        "planner",
        "compressor",
        "context_manager",
        "memory_summarizer",
        "research",
        "supervisor",
        "codifier",
        "workflow",
        "auxiliary_unknown",
    }
)


def normalize_provider_purpose(value: str | None) -> str:
    purpose = str(value or "").strip()
    return purpose if purpose in _KNOWN_PROVIDER_PURPOSES else "auxiliary_unknown"


@contextmanager
def context_attempt_scope(options: ProviderAttemptOptions) -> Iterator[ProviderAttemptOptions]:
    """Install per-attempt options and reliably restore an outer scope."""

    if not isinstance(options, ProviderAttemptOptions):
        raise TypeError("options must be ProviderAttemptOptions")
    token = _PROVIDER_ATTEMPT_OPTIONS.set(options)
    try:
        yield options
    finally:
        _PROVIDER_ATTEMPT_OPTIONS.reset(token)


def current_provider_attempt_options() -> ProviderAttemptOptions | None:
    return _PROVIDER_ATTEMPT_OPTIONS.get()


@contextmanager
def provider_purpose_scope(
    purpose: str,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[ProviderAttemptOptions]:
    """Declare an auxiliary provider purpose without inventing attempt IDs.

    The provider/registry boundary fills missing identities only when Context
    OS is enabled.  A complete outer AgentLoop attempt always wins.
    """

    current = current_provider_attempt_options()
    if current is not None and all(
        (current.session_id, current.request_id, current.attempt_id)
    ):
        yield current
        return
    options = ProviderAttemptOptions(
        cache_boundary=current.cache_boundary if current else None,
        cache_fingerprint=current.cache_fingerprint if current else None,
        purpose=normalize_provider_purpose(purpose),
        session_id=(current.session_id if current and current.session_id else session_id),
        root_run_id=current.root_run_id if current else None,
        request_id=(current.request_id if current and current.request_id else request_id),
        attempt_id=current.attempt_id if current else None,
        workload_class=current.workload_class if current else None,
        callsite_id=current.callsite_id if current else None,
        detached=current.detached if current else False,
    )
    with context_attempt_scope(options):
        yield options


def tag_message(
    message: Mapping[str, Any], metadata: ContextMessageMeta | Mapping[str, Any]
) -> dict[str, Any]:
    """Return a deep-copied message carrying canonical host-only metadata."""

    if not isinstance(message, Mapping):
        raise TypeError("message must be a mapping")
    canonical = (
        metadata
        if isinstance(metadata, ContextMessageMeta)
        else ContextMessageMeta.from_mapping(metadata)
    )
    tagged = copy.deepcopy(dict(message))
    tagged[CONTEXT_MESSAGE_META_KEY] = canonical.to_mapping()
    return tagged


def split_wire_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[ContextMessageMeta | None]]:
    """Deep-copy messages, strip the sidecar, and return aligned metadata."""

    wire: list[dict[str, Any]] = []
    aligned: list[ContextMessageMeta | None] = []
    for message in messages or ():
        if not isinstance(message, Mapping):
            raise TypeError("every context message must be a mapping")
        copied = copy.deepcopy(dict(message))
        raw_meta = copied.pop(CONTEXT_MESSAGE_META_KEY, None)
        wire.append(copied)
        aligned.append(
            None if raw_meta is None else ContextMessageMeta.from_mapping(raw_meta)
        )
    return wire, aligned


def stable_prefix_bytes(
    messages: Sequence[Mapping[str, Any]], cache_boundary: int | None
) -> bytes:
    """Serialize the explicit stable prefix into canonical UTF-8 JSON bytes."""

    wire, _ = split_wire_messages(messages)
    if cache_boundary is None:
        return b""
    if cache_boundary >= len(wire):
        raise ValueError("cache_boundary is outside the message list")
    return json.dumps(
        wire[: cache_boundary + 1],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_prefix_fingerprint(
    messages: Sequence[Mapping[str, Any]], cache_boundary: int | None
) -> str | None:
    canonical = stable_prefix_bytes(messages, cache_boundary)
    return hashlib.sha256(canonical).hexdigest() if canonical else None


def _message_from_value(value: Mapping[str, Any] | str, *, role: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {"role": role, "content": value}
    if not isinstance(value, Mapping):
        raise TypeError("message value must be a mapping or string")
    return dict(value)


def _append_tagged(
    messages: MutableSequence[dict[str, Any]],
    value: Mapping[str, Any] | str,
    *,
    role: str,
    metadata: ContextMessageMeta,
) -> dict[str, Any]:
    tagged = tag_message(_message_from_value(value, role=role), metadata)
    messages.append(tagged)
    return tagged


def append_prefix(
    messages: MutableSequence[dict[str, Any]],
    value: Mapping[str, Any] | str,
    *,
    source: str,
    lifetime: Lifetime = "stable",
    protected: bool = True,
    trim_policy: TrimPolicy = "never",
    **meta: Any,
) -> dict[str, Any]:
    return _append_tagged(
        messages,
        value,
        role="system",
        metadata=ContextMessageMeta(
            placement="prefix",
            lifetime=lifetime,
            source=source,
            protected=protected,
            trim_policy=trim_policy,
            **meta,
        ),
    )


def append_transcript(
    messages: MutableSequence[dict[str, Any]],
    value: Mapping[str, Any] | str,
    *,
    source: str,
    role: str = "user",
    lifetime: Lifetime = "history",
    trim_policy: TrimPolicy = "summarize",
    **meta: Any,
) -> dict[str, Any]:
    return _append_tagged(
        messages,
        value,
        role=role,
        metadata=ContextMessageMeta(
            placement="transcript",
            lifetime=lifetime,
            source=source,
            trim_policy=trim_policy,
            **meta,
        ),
    )


def append_control(
    messages: MutableSequence[dict[str, Any]],
    value: Mapping[str, Any] | str,
    *,
    source: str,
    lifetime: Lifetime = "current",
    protected: bool = False,
    trim_policy: TrimPolicy = "drop",
    **meta: Any,
) -> dict[str, Any]:
    return _append_tagged(
        messages,
        value,
        role="system",
        metadata=ContextMessageMeta(
            placement="control",
            lifetime=lifetime,
            source=source,
            protected=protected,
            trim_policy=trim_policy,
            **meta,
        ),
    )


__all__ = [
    "CONTEXT_MESSAGE_META_KEY",
    "ContextMessageMeta",
    "ProviderAttemptOptions",
    "append_control",
    "append_prefix",
    "append_transcript",
    "context_attempt_scope",
    "current_provider_attempt_options",
    "normalize_provider_purpose",
    "provider_purpose_scope",
    "split_wire_messages",
    "stable_prefix_bytes",
    "stable_prefix_fingerprint",
    "tag_message",
]
