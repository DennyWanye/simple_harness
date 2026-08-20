# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit reasoning-protocol capabilities for Provider model bindings.

The resolver is deliberately an exact-id declaration table.  Unknown models
receive no private wire fields; endpoints and model-name substrings are never
used as protocol detection signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


ReasoningBehavior = Literal["toggleable", "always_on", "unsupported", "unknown"]
ReasoningRequestStyle = Literal["thinking_object", "effort_only", "none"]


@dataclass(frozen=True, slots=True)
class ProviderReasoningCapability:
    behavior: ReasoningBehavior
    request_style: ReasoningRequestStyle
    efforts: tuple[str, ...] = ()
    preserve_reasoning: Literal["tool_loop", "all_turns", "not_required"] = (
        "not_required"
    )


UNKNOWN_REASONING_CAPABILITY = ProviderReasoningCapability("unknown", "none")

_DECLARED: Mapping[str, ProviderReasoningCapability] = MappingProxyType(
    {
        "deepseek-v4-pro": ProviderReasoningCapability(
            "toggleable", "thinking_object", ("high", "max"), "tool_loop"
        ),
        "deepseek-v4-flash": ProviderReasoningCapability(
            "toggleable", "thinking_object", ("high", "max"), "tool_loop"
        ),
        "kimi-k3": ProviderReasoningCapability(
            "always_on", "effort_only", ("low", "high", "max"), "all_turns"
        ),
        "kimi-k2.7-code": ProviderReasoningCapability(
            "always_on", "effort_only", ("low", "high", "max"), "all_turns"
        ),
        "kimi-k2.6": ProviderReasoningCapability(
            "toggleable", "thinking_object", ("low", "high", "max"), "all_turns"
        ),
        "kimi-k2.5": ProviderReasoningCapability(
            "toggleable", "thinking_object", ("low", "high", "max"), "not_required"
        ),
    }
)


def declared_reasoning_capability(model_id: str) -> ProviderReasoningCapability:
    """Return the immutable declared profile for an exact model id."""

    return _DECLARED.get(str(model_id).strip().lower(), UNKNOWN_REASONING_CAPABILITY)


def reasoning_wire_fields(
    capability: ProviderReasoningCapability,
    model_params: object,
) -> dict[str, object]:
    """Map Session-neutral mode/effort onto one declared wire protocol.

    ``model_params`` is accepted as an object so corrupted legacy values fail
    closed.  Default omits all private fields and lets the declared model use
    its own default.
    """

    if not isinstance(model_params, Mapping):
        return {}
    raw_mode = model_params.get("reasoning_mode")
    if raw_mode is None:
        if model_params.get("thinking") is True:
            raw_mode = "thinking"
        elif model_params.get("fast") is True or model_params.get("thinking") is False:
            raw_mode = "fast"
        else:
            raw_mode = "default"
    mode = str(raw_mode).strip().lower()
    if mode not in {"default", "thinking", "fast"} or mode == "default":
        return {}
    if capability.behavior in {"unknown", "unsupported"}:
        return {}

    raw_effort = str(
        model_params.get("reasoning_effort") or model_params.get("effort") or ""
    ).strip().lower()
    default_effort = "low" if mode == "fast" else "high"
    effort = raw_effort if raw_effort in capability.efforts else default_effort
    if effort not in capability.efforts:
        effort = ""

    fields: dict[str, object] = {}
    if capability.request_style == "thinking_object":
        fields["thinking"] = {
            "type": "disabled" if mode == "fast" else "enabled"
        }
    elif capability.request_style == "effort_only" and capability.behavior == "always_on":
        # Fast on an always-thinking model means lower effort, never disabled.
        pass
    if effort:
        fields["reasoning_effort"] = effort
    return fields


__all__ = (
    "ProviderReasoningCapability",
    "UNKNOWN_REASONING_CAPABILITY",
    "declared_reasoning_capability",
    "reasoning_wire_fields",
)
