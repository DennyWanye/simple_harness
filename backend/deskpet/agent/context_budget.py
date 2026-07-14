# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Whole-request budget accounting for Context OS V1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from deskpet.agent.tokens import count_messages_tokens, count_text_tokens
from deskpet.tools.capabilities import PreparedToolPayload, PreparedToolSet, canonical_hash, canonical_json


@dataclass(frozen=True)
class RequestBudget:
    context_window: int
    effective_pct: float
    generation_reserve: int
    messages_tokens: int
    tool_tokens: int
    attachment_tokens: int = 0
    safety_margin: int = 0

    @property
    def effective_input_budget(self) -> int:
        return max(0, int(self.context_window * self.effective_pct) - self.generation_reserve)

    @property
    def planned_input_tokens(self) -> int:
        return self.messages_tokens + self.tool_tokens + self.attachment_tokens + self.safety_margin

    @property
    def fits(self) -> bool:
        return self.planned_input_tokens <= self.effective_input_budget


def prepare_openai_tool_payload(
    prepared: PreparedToolSet,
    *,
    adapter_id: str = "openai-compatible",
    adapter_version: str = "v1",
) -> PreparedToolPayload:
    tools = list(prepared.logical_schemas())
    wire = canonical_json(tools)
    return PreparedToolPayload(
        logical_schema_fingerprint=prepared.schema_fingerprint,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        tools=tools,
        wire_payload_hash=canonical_hash(tools),
        wire_tokens=count_text_tokens(wire),
        estimate_method="canonical_json_cjk_heuristic",
    )


def estimate_request_budget(
    messages: list[dict[str, Any]],
    tool_payload: PreparedToolPayload,
    *,
    context_window: int,
    effective_pct: float,
    generation_reserve: int,
    attachment_tokens: int = 0,
    safety_margin: int = 0,
) -> RequestBudget:
    return RequestBudget(
        context_window=max(1, int(context_window)),
        effective_pct=max(0.01, min(float(effective_pct), 1.0)),
        generation_reserve=max(0, int(generation_reserve)),
        messages_tokens=count_messages_tokens(messages),
        tool_tokens=max(0, int(tool_payload.wire_tokens)),
        attachment_tokens=max(0, int(attachment_tokens)),
        safety_margin=max(0, int(safety_margin)),
    )
