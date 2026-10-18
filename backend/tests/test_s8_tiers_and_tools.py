# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for the clipboard tool."""

from __future__ import annotations

import pytest
from tools.clipboard import ReadClipboardTool

# --- clipboard tool (mocked) ---


@pytest.mark.asyncio
async def test_clipboard_tool_returns_payload_from_platform_impl(monkeypatch):
    """We don't actually touch the OS clipboard — patch the helper."""
    tool = ReadClipboardTool()
    monkeypatch.setattr("tools.clipboard._read_clipboard_windows", lambda: "hello from clip")
    monkeypatch.setattr("tools.clipboard._read_clipboard_tk", lambda: "hello from clip")
    out = await tool.invoke()
    assert out == "hello from clip"


@pytest.mark.asyncio
async def test_clipboard_tool_swallows_exceptions(monkeypatch):
    """Errors must return a string, not raise — keeps LLM stream intact."""
    tool = ReadClipboardTool()

    def boom():
        raise RuntimeError("no clipboard for you")

    monkeypatch.setattr("tools.clipboard._read_clipboard_windows", boom)
    monkeypatch.setattr("tools.clipboard._read_clipboard_tk", boom)

    out = await tool.invoke()
    assert "clipboard read failed" in out
    assert "no clipboard for you" in out


@pytest.mark.asyncio
async def test_clipboard_spec_low_risk():
    assert ReadClipboardTool.spec.requires_confirmation is False
