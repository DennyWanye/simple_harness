# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Mode-neutral session model-parameter resolution."""
from __future__ import annotations

import asyncio

from llm.resolution import resolve_session_provider_chain


class _Reg:
    def get_chain(self):
        return [
            {
                "id": "p0",
                "base_url": "https://your-llm-relay.example.com/v1",
                "model": "deepseek-v4-pro",
                "temperature": 0.7,
            }
        ]

    def get_entry(self, pid):
        return None


class _SDB:
    def __init__(self, binding):
        self._binding = binding

    async def get_session_provider_binding(self, sid):
        return self._binding


def _resolve(binding):
    return asyncio.run(
        resolve_session_provider_chain(
            "task:project",
            registry=_Reg(),
            session_db=_SDB(binding),
        )
    )


def test_unbound_session_uses_provider_default() -> None:
    entries = _resolve(
        {"provider_id": None, "preferred_model": None, "model_params": None}
    )
    assert entries[0].model == "deepseek-v4-pro"
    assert entries[0].code_params == {}


def test_bound_anthropic_model_strips_reasoning_effort() -> None:
    entries = _resolve(
        {
            "provider_id": None,
            "preferred_model": "opus-4.7",
            "model_params": {
                "thinking": True,
                "effort": "max",
                "context": "1m",
                "fast": True,
            },
        }
    )
    assert entries[0].model == "opus-4.7"
    assert "reasoning_effort" not in entries[0].code_params
    assert entries[0].code_params["extra_body"] == {
        "context_window": 1_000_000,
        "fast": True,
    }


def test_bound_openai_model_keeps_reasoning_effort() -> None:
    entries = _resolve(
        {
            "provider_id": None,
            "preferred_model": "gpt-5.5",
            "model_params": {
                "thinking": True,
                "effort": "max",
                "context": "1m",
                "fast": True,
            },
        }
    )
    assert entries[0].code_params["reasoning_effort"] == "high"
    assert entries[0].code_params["extra_body"] == {
        "context_window": 1_000_000,
        "fast": True,
    }
