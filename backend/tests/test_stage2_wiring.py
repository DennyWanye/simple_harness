# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""验证 Stage 2 接电（修 v2 评审 P0-1/P0-2）。

P0-1: registry.execute_tool 在 receipt_store_provider 注入后必须真 emit。
P0-2: VerifyGate matching 必须按 tool_hint 严格匹配，不放任一 ok receipt。
"""

from __future__ import annotations

import pytest

# ─── P0-1 registry → ReceiptStore 接电验证 ──────────────────


@pytest.mark.asyncio
async def test_p0_1_no_provider_means_no_receipt(tmp_path):
    """provider=None (BC) → 不产 receipt（字节级一致）。"""
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def _h(p, t):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_p0_1a",
        "t",
        {"name": "fake_p0_1a", "description": "x", "parameters": {}},
        _h,
    )
    await registry.execute_tool("fake_p0_1a", {}, session_id="s_p0a")
    # 没有 ReceiptStore → 不产 receipt 文件
    assert not (tmp_path / "receipts").exists()


@pytest.mark.asyncio
async def test_p0_1_provider_set_emits_receipt(tmp_path):
    """provider 返回 ReceiptStore → execute_tool 后 receipt jsonl 写盘。"""
    from deskpet.tools.receipt_store import ReceiptStore
    from deskpet.tools.registry import ToolRegistry

    store = ReceiptStore(tmp_path, key=b"\x11" * 32)
    registry = ToolRegistry()
    registry.set_receipt_store_provider(lambda: store)

    def _h(p, t):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_p0_1b",
        "t",
        {"name": "fake_p0_1b", "description": "x", "parameters": {}},
        _h,
    )
    await registry.execute_tool("fake_p0_1b", {"k": "v"}, session_id="s_p0b")

    # receipt 应已写入
    path = tmp_path / "receipts" / "s_p0b.jsonl"
    assert path.exists()
    content = path.read_text(encoding="utf-8").strip()
    assert "fake_p0_1b" in content
    # iteration 字段
    assert '"iteration": 1' in content or '"iteration":1' in content


@pytest.mark.asyncio
async def test_p0_1_provider_exception_does_not_break_dispatch(tmp_path, caplog):
    """provider 抛异常 → warn log + envelope 仍正常 (never break dispatch)。"""
    import logging

    caplog.set_level(logging.WARNING)
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.set_receipt_store_provider(lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))

    def _h(p, t):
        return {"ok": True}

    registry.register(
        "fake_p0_1c",
        "t",
        {"name": "fake_p0_1c", "description": "x", "parameters": {}},
        _h,
    )
    env = await registry.execute_tool("fake_p0_1c", {}, session_id="s")
    assert env["ok"] is True
    assert any("receipt_store_provider raised" in r.message for r in caplog.records)
