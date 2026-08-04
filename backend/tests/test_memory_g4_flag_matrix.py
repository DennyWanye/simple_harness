# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G4 — memory-v2 flag 矩阵 / 契约测试（记忆系统严测 Phase 3）。

## 背景
盘点发现 flag 覆盖只到"二值开关"，缺：
- dataclass 默认全 False 的**字节级契约根基**没有直接断言
- F4 把 config.toml 的 workspace_memory 改 true，但 dataclass 默认仍 False
  —— 这个"出厂开但契约保 False"的关键不变量没测
- flag 依赖关系（enhanced_retriever / entity_path 依赖 facts_extract）没断言

## 本文件验什么
- G4.1 dataclass 默认：MemoryV2Config 全 flag 默认 False（字节级契约根基）
- G4.2 F4 不变量：config.toml workspace_memory=true，但 dataclass 默认仍 False
  （F4 出厂开 ≠ 破坏字节级契约）
- G4.3 config.toml 其余 flag 仍 False（F4 只动 workspace_memory）
- G4.4 flag 依赖关系自洽：enhanced_retriever / entity_path 标注依赖 facts_extract

不测 main.py lifespan 的运行时 warn 逻辑（内联在 lifespan，需起整个 app，
归 e2e/启动测试；本文件聚焦可单测的 config 契约层）。
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from config import MemoryV2Config

# 记忆 v2 的全部 flag（与 MemoryV2Config 字段对应）
_ALL_FLAGS = [
    "feedback_loop", "facts_extract", "rerank", "enhanced_retriever",
    "chunking", "query_rewrite", "workspace_memory", "reflection",
    "cross_key_merge", "memory_forget", "entity_path", "episodic_to_semantic",
]

# 仓库 config.toml（出厂模板）
_REPO_CONFIG = Path(__file__).resolve().parent.parent.parent / "config.toml"


# 2026-06-27 测试阶段全量点亮（CLAUDE.md §测试阶段不灰度）：A 表语义记忆 flag 的
# dataclass 默认翻 True；仅 B 表（code 专属，主线不开）保持默认 False。原"全 flag
# 默认 False = 字节级 BC"契约对 A 表已**有意废弃**。
_B_TABLE_DEFAULT_FALSE = {"workspace_memory"}  # WI-M1.6 code 工作记忆，归 code 模式


# ----------------------------------------------------------------------
# G4.1 — dataclass 默认符合测试阶段点亮（A 表 True / B 表 False）
# ----------------------------------------------------------------------
def test_g4_1_dataclass_defaults_match_testing_phase() -> None:
    """MemoryV2Config flag 的 **dataclass 默认值**：A 表语义 flag 全 True
    （测试阶段点亮、不灰度），B 表（workspace_memory）仍 False。

    2026-06-27 测试阶段定调：开发完成的能力立即默认 ON。原 Strangler-Fig
    "全 flag 默认 False = 字节级等同 gen-1" 契约对 A 表已有意废弃（见
    CLAUDE.md §🚀 测试阶段：能力即开即用）。test_byte_level_consistency 仍靠
    **显式 False** 构造校验回退路径，不受本默认变更影响。
    """
    cfg = MemoryV2Config()
    for flag in _ALL_FLAGS:
        assert hasattr(cfg, flag), f"MemoryV2Config 缺 flag 字段: {flag}"
        expected = flag not in _B_TABLE_DEFAULT_FALSE
        assert getattr(cfg, flag) is expected, (
            f"dataclass 默认 {flag} 应为 {expected}（A 表测试阶段点亮 / B 表保 False），"
            f"实际 = {getattr(cfg, flag)}"
        )


def test_g4_1b_only_b_table_flags_default_false() -> None:
    """防御反转：除 B 表（workspace_memory）外，MemoryV2Config 的 bool flag
    默认都应 True（测试阶段全量点亮）。防"A 表能力漏点亮 / 被误关"。"""
    for f in dataclasses.fields(MemoryV2Config):
        if isinstance(f.default, bool):
            if f.name in _B_TABLE_DEFAULT_FALSE:
                assert f.default is False, (
                    f"B 表 flag {f.name} 应默认 False（code 专属，主线不开）"
                )
            else:
                assert f.default is True, (
                    f"flag {f.name} dataclass 默认 False —— 测试阶段应点亮（不灰度）。"
                    f"若属有意保 OFF，请加入 _B_TABLE_DEFAULT_FALSE 并注明原因。"
                )


# ----------------------------------------------------------------------
# G4.2 — F4 不变量：config.toml 出厂开 workspace_memory，dataclass 仍 False
# ----------------------------------------------------------------------
def test_g4_2_f4_invariant_toml_true_dataclass_false() -> None:
    """F4 核心不变量：config.toml workspace_memory=true，但 dataclass 默认 False。

    F4 让 code 工作记忆出厂可用（config.toml），同时**不动 dataclass 默认**
    以保字节级契约。这两者必须同时成立 —— 否则要么功能没开（toml 也 false），
    要么破坏了契约（dataclass true）。
    """
    assert _REPO_CONFIG.is_file(), f"找不到仓库 config.toml: {_REPO_CONFIG}"
    raw = tomllib.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    toml_v2 = raw.get("memory", {}).get("v2", {})

    # config.toml 出厂开
    assert toml_v2.get("workspace_memory") is True, (
        "F4: config.toml [memory.v2] workspace_memory 应为 true（出厂开 code 工作记忆）"
    )
    # dataclass 默认仍 False（契约不破）
    assert MemoryV2Config().workspace_memory is False, (
        "F4: dataclass 默认 workspace_memory 必须仍 False（保字节级契约）"
    )


# ----------------------------------------------------------------------
# G4.3 — config.toml 出厂全量点亮（测试阶段不灰度）
# ----------------------------------------------------------------------
# 出厂运行配置层必须出现的关键语义 flag（防漏配）。测试阶段它们全 True。
_FACTORY_REQUIRED_FLAGS = {
    "facts_extract", "enhanced_retriever", "cross_key_merge",
    "reflection", "feedback_loop", "query_rewrite", "workspace_memory",
}


def test_g4_3_factory_config_fully_lit() -> None:
    """config.toml [memory.v2]：测试阶段全量点亮 —— 所有 bool flag 出厂 True。

    2026-06-27 测试阶段定调（CLAUDE.md §测试阶段不灰度）：已开发完成的记忆能力
    出厂即开。原"仅审计 #4 子集出厂开、其余 False"已被取代。这条改为钉住
    "出厂 config 不留 OFF 的记忆 flag"，防误关 / 漏配。
    """
    raw = tomllib.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    toml_v2 = raw.get("memory", {}).get("v2", {})
    for flag, val in toml_v2.items():
        if not isinstance(val, bool):
            continue
        assert val is True, (
            f"config.toml [memory.v2] {flag}={val} —— 测试阶段应全量点亮（出厂 True）"
        )
    # 反向：关键语义 flag 都得在 toml 真出现（防漏配）。
    for flag in _FACTORY_REQUIRED_FLAGS:
        assert flag in toml_v2, (
            f"语义记忆 flag {flag} 未出现在 config.toml [memory.v2]（漏配）"
        )


# ----------------------------------------------------------------------
# G4.4 — flag 依赖关系自洽（enhanced_retriever / entity_path 依赖 facts_extract）
# ----------------------------------------------------------------------
def test_g4_4_dependency_flags_exist_and_independent() -> None:
    """依赖语义断言：enhanced_retriever / entity_path 与 facts_extract 都存在
    且可独立设置（main.py lifespan 据此 warn 但不挡 boot）。

    这里只验字段独立性（能各自设 True/False 不互相强制），运行时降级 warn
    归 e2e。核心防止：将来重构把这些 flag 合并/删除导致依赖校验失效。
    """
    cfg = MemoryV2Config(enhanced_retriever=True, facts_extract=False)
    # 能构造出"依赖未满足"的组合（main.py 会 warn）—— 字段独立
    assert cfg.enhanced_retriever is True
    assert cfg.facts_extract is False

    cfg2 = MemoryV2Config(entity_path=True, facts_extract=True)
    assert cfg2.entity_path is True and cfg2.facts_extract is True
