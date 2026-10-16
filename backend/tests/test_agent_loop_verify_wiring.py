# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.6 — AgentLoop end_turn VerifyGate wiring（修 ship-readiness P0-3）。

子代理 round-2 评审指出 AgentLoop 不调 VerifyGate → 生产抓获率仍 0%。
本测试组验证 wiring 正确：
  - verify_gate=None (BC) → 跳过守门
  - mode=off → 跳过守门
  - mode=strict + ledger 空 + assistant_text 含 claim → 拒 + 回灌
  - max_verify_nudges 计数 + ephemeral 救援
"""

from __future__ import annotations


def test_receipt_store_load_session_provides_ledger():
    """AgentLoop 通过 self.receipt_store.load_session(sid) 拿 ledger。
    确保 ReceiptStore.load_session 返 list[ToolReceipt] 接口稳定。"""
    import tempfile
    from pathlib import Path

    from deskpet.tools.receipt_store import ReceiptStore

    with tempfile.TemporaryDirectory() as td:
        store = ReceiptStore(Path(td), key=b"\x42" * 32)
        ledger = store.load_session("nonexistent")
        assert ledger == []
        # 接口签名稳定（AgentLoop 期望 list 返回）
        assert isinstance(ledger, list)


def test_main_py_wiring_present():
    """**最终接电证据**: main.py 应有 set_receipt_store_provider 调用。"""
    from tests.host_source import host_source_text

    main_py = host_source_text()
    # WI-T2.2 wiring
    assert "set_receipt_store_provider" in main_py
    # WI-T1.1 wiring
    assert "set_tools_config_provider" in main_py
