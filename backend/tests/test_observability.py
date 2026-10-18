# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for stage_timer + VRAM detection."""

from __future__ import annotations

import asyncio

import pytest
from observability.metrics import stage_timer

# --- stage_timer ---


def _contains_log_field(text: str, key: str, value: str) -> bool:
    """Accept structlog JSON, console key/value, and stdlib dict rendering."""
    return any(
        candidate in text
        for candidate in (
            f'{key}="{value}"',
            f"{key}='{value}'",
            f"{key}={value}",
            f'"{key}": "{value}"',
            f"'{key}': '{value}'",
        )
    )


@pytest.mark.asyncio
async def test_stage_timer_success_emits_stage_complete(caplog, capsys):
    """Runs clean, logs stage_complete with elapsed_ms and context.

    P2-2-M3 (2026-04-20): main.py now configures structlog to route
    through stdlib logging (so logs/backend.log gets populated). That
    means the event lands in caplog, not stdout — swap capsys→caplog.
    """
    import logging

    with caplog.at_level(logging.INFO):
        async with stage_timer("asr", conn="c1"):
            await asyncio.sleep(0.01)
    text = caplog.text or capsys.readouterr().out
    assert "stage_complete" in text
    assert _contains_log_field(text, "stage", "asr")
    assert _contains_log_field(text, "conn", "c1")
    assert any(marker in text for marker in ("elapsed_ms=", '"elapsed_ms":', "'elapsed_ms':"))


@pytest.mark.asyncio
async def test_stage_timer_raises_but_still_emits(caplog, capsys):
    """Exception propagates AND stage_error is logged with error field."""
    import logging

    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError):
            async with stage_timer("asr"):
                raise ValueError("boom")
    text = caplog.text or capsys.readouterr().out
    assert "stage_error" in text
    assert "boom" in text
    assert _contains_log_field(text, "stage", "asr")


@pytest.mark.asyncio
async def test_stage_timer_context_fields_emitted(caplog, capsys):
    """Additional kwargs pass through to the log record."""
    import logging

    with caplog.at_level(logging.INFO):
        async with stage_timer("tool_invoke", tool_name="get_time", session="s1"):
            pass
    text = caplog.text or capsys.readouterr().out
    assert _contains_log_field(text, "tool_name", "get_time")
    assert _contains_log_field(text, "session", "s1")
