# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 (2026-05-11): malformed tool_call.arguments JSON handling.

Regression: deepseek-v4-pro on the relay occasionally emits broken JSON
for tool_call.arguments when the args contain large markdown content
(unescaped \\n / \\" / \\\\ in long strings). The old code path
silently swallowed the JSONDecodeError and dispatched the tool with
``args = {}``. Tools then reported "missing required parameter", the
LLM retried with the same broken JSON, and after 3 strikes the
circuit breaker fired — burning tokens + popup-ing the user without
ever telling the model what was wrong.

Now: provider stashes the raw buffer + parse error on the assembled
tool_call dict; ChatResponse.ToolCall carries those fields; AgentLoop
short-circuits ``_dispatch_tool`` and returns a structured error
tool_result that tells the LLM exactly what JSON syntax error it made,
so it can regenerate.

Also covers: connect timeout bumped from 5s to 10s after seeing real
ConnectTimeout failures while the relay reported no errors (TLS handshake
on Windows occasionally takes 6-8s on first connect).
"""

from __future__ import annotations

import httpx
from providers.openai_compatible import OpenAICompatibleProvider

# ─────────────── connect timeout (Fix A) ───────────────


def test_default_connect_timeout_is_10s():
    """Regression: 2026-05-11 — the relay never saw the request because our
    5s connect timeout fired before TLS handshake finished. Bumped to 10s.

    P5-S2 F1 (2026-05-12) — read budget bumped 60→120 per the relay's
    integration guide (docs/中转站建议.md): their PR #27 ships an SSE
    keep-alive comment every 15s that resets read timers across all
    hops, so the larger budget covers slow individual reasoning tokens
    (5KB+ chunks) without false-failing on healthy long generations.
    """
    p = OpenAICompatibleProvider(base_url="https://example.test", api_key="k", model="m")
    # `httpx.Timeout` carries connect/read/write/pool fields.
    assert isinstance(p.timeout, httpx.Timeout)
    assert p.timeout.connect == 10.0, (
        f"connect timeout regressed to {p.timeout.connect}s; expected 10.0"
    )
    # F1: read budget covers thinking-mode bursts (the relay keep-alive
    # makes 120s safe even when upstream is silent for 30-60s mid-think).
    assert p.timeout.read == 120.0


def test_local_ollama_read_timeout_covers_slow_first_token():
    """A CPU/GPU-split local model may need minutes before its first byte."""
    p = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama",
        model="qwen3-coder:30b",
    )

    assert isinstance(p.timeout, httpx.Timeout)
    assert p.timeout.connect == 10.0
    assert p.timeout.read == 600.0


# ─────────────── malformed args end-to-end (Fix B) ───────────────


def _stub_response_with_malformed_tool_call(args_raw: str) -> dict:
    """Build the dict shape provider.chat_with_tools would normally
    return when the model emits broken JSON args. Crucially, it
    populates ``_args_raw`` + ``_args_parse_error`` like the real
    assembly path does after a JSONDecodeError on args_buf."""
    return {
        "content": "",
        "reasoning_content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "write_file",
                "arguments": {},  # Empty because parse failed
                "_args_raw": args_raw,
                "_args_parse_error": "Expecting ',' delimiter: line 1 column 50 (char 49)",
            }
        ],
        "stop_reason": "tool_use",
        "model": "deepseek-v4-pro",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
