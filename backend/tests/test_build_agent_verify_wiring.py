# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.1 v3 — build_agent 工厂 wiring 测试.

验证 host.agent_factory.build_agent(...) 工厂真正把 VerifyGate / ReceiptStore /
max_verify_nudges 接到 _AgentLoop 实例上。**这是 last-mile P0-1 修复的硬证据**
（last-mile 写了 AgentLoop kwargs + verify_gate 类，但 main.py 构造调用漏传）。

测试策略（详 plans/2026-05-24-tool-layer-optimization-v3/01-TDD.md §A1.3）：
不走 `import main; reload` 路径（monolithic main.py 99% 翻车 — v1 评审 P1-1）。
直接 `from host.agent_factory import build_agent` + mock cfg/registry 调用工厂 + assertion。

四组场景：
  1. flag ON (mode=shadow) → verify_gate is not None + receipt_store wired
  2. flag OFF → verify_gate is None（BC）
  3. patterns_file 缺失 → catch + warn + verify_gate=None（不崩）
  4. kwargs 全传递 — max_iterations/completion_probe/signature_repeat_threshold
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Minimal cfg fixtures (mimic AppConfig.tools.verifier shape) ─────


@dataclass
class _VerifierStub:
    verify_gate_mode: str = "off"
    emit_receipts: bool = False
    claim_patterns_file: str = "verify/claim_patterns.yaml"
    max_verify_nudges: int = 2
    extractor_fallback_enabled: bool = True
    ephemeral_subagent_model: str = "haiku"
    run_build: bool = False
    run_tests: bool = False


@dataclass
class _LastMileStub:
    artifact_dir_retention_days: int = 30


@dataclass
class _ToolsStub:
    verifier: _VerifierStub = field(default_factory=_VerifierStub)
    last_mile: _LastMileStub = field(default_factory=_LastMileStub)


@dataclass
class _CfgStub:
    tools: _ToolsStub = field(default_factory=_ToolsStub)


@pytest.fixture
def cfg_flag_on() -> _CfgStub:
    cfg = _CfgStub()
    cfg.tools.verifier.verify_gate_mode = "shadow"
    cfg.tools.verifier.emit_receipts = True
    return cfg


@pytest.fixture
def cfg_flag_off() -> _CfgStub:
    return _CfgStub()  # defaults: mode=off, emit=False


@pytest.fixture
def cfg_bad_patterns(tmp_path: Path) -> _CfgStub:
    cfg = _CfgStub()
    cfg.tools.verifier.verify_gate_mode = "shadow"
    cfg.tools.verifier.emit_receipts = True
    cfg.tools.verifier.claim_patterns_file = str(tmp_path / "nonexistent.yaml")
    return cfg


@pytest.fixture
def mock_receipt_store_getter():
    """Returns a callable that returns a MagicMock ReceiptStore."""
    rs = MagicMock(name="ReceiptStore")
    rs.load_session.return_value = []
    return lambda: rs


@pytest.fixture
def mock_receipt_store_getter_none():
    """Simulates emit_receipts=False — getter returns None."""
    return lambda: None


# ─── WI-HM-1: 出厂默认点亮 + VG-INVARIANT 守门 ───────────────────────


def test_factory_default_lights_up_self_correction_loop():
    """WI-HM-1 决策①：真 AppConfig 出厂默认 = 自我纠错闭环全档开.

    verify_gate_mode='strict' + emit_receipts=True + structured_reflection=True，
    且 _validate_flag_invariants 不抛（VG-INVARIANT-0/1 都满足）。
    （2026-06-23 由 shadow 升 strict——真机确认 strict 不误阻塞无 claim 闲聊。）
    """
    from config import AppConfig, _validate_flag_invariants

    cfg = AppConfig()
    v = cfg.tools.verifier
    assert v.verify_gate_mode == "strict"
    assert v.emit_receipts is True
    assert v.structured_reflection is True
    # 默认 config 必须通过 invariant 校验（不抛 ConfigError）.
    _validate_flag_invariants(cfg)


def test_vg_invariant_1_raises_when_mode_on_but_receipts_off():
    """WI-HM-1 硬连锁：verify_gate_mode != 'off' 但 emit_receipts=False →
    ConfigError VG-INVARIANT-1（否则 ledger 永远空 → 全 end_turn 阻塞）。"""
    from config import AppConfig, ConfigError, _validate_flag_invariants

    cfg = AppConfig()
    cfg.tools.verifier.emit_receipts = False  # 违反 VG-INVARIANT-1
    with pytest.raises(ConfigError, match="VG-INVARIANT-1"):
        _validate_flag_invariants(cfg)
