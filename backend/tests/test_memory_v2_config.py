# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-1 — [memory.v2] config schema（WI-M0.3）。

[memory.v2] / [memory.v2.facts] 是 [memory] 的嵌套子表；_load_section 只做
平铺解析，故 load_config 把 v2 pop 出来单独构建（_load_memory_v2）。
"""
from __future__ import annotations

from config import load_config, MemoryV2Config, MemoryV2FactsConfig


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_t1_1_no_v2_section_defaults_match_testing_phase(tmp_path):
    # 2026-06-27 测试阶段点亮：无 [memory.v2] 段时 A 表语义 flag 落 dataclass
    # 默认 True；仅 workspace_memory（B 表 code 工作记忆）仍 False。
    cfg = load_config(_write(tmp_path, "[memory]\nembedding_model = \"bge-m3\"\n"))
    v2 = cfg.memory.v2
    assert isinstance(v2, MemoryV2Config)
    for flag in ("feedback_loop", "facts_extract", "rerank",
                 "enhanced_retriever", "chunking", "query_rewrite",
                 "reflection"):
        assert getattr(v2, flag) is True, flag
    assert v2.workspace_memory is False  # B 表：code 专属，主线不开
    # nested facts defaults
    assert isinstance(v2.facts, MemoryV2FactsConfig)
    assert v2.facts.min_user_chars == 8
    assert v2.facts.facts_weight == 0.2


def test_t1_2_explicit_flag_true(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2]\nfacts_extract = true\nrerank = true\n"))
    assert cfg.memory.v2.facts_extract is True
    assert cfg.memory.v2.rerank is True
    # untouched flag → dataclass 默认（测试阶段已点亮为 True）；
    # workspace_memory 仍 False 可验"未设即取默认"。
    assert cfg.memory.v2.enhanced_retriever is True
    assert cfg.memory.v2.workspace_memory is False


def test_t1_3_nested_facts_section(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2.facts]\nmin_user_chars = 12\nfacts_weight = 0.35\n"))
    assert cfg.memory.v2.facts.min_user_chars == 12
    assert cfg.memory.v2.facts.facts_weight == 0.35
    # flag 未设 → dataclass 默认（测试阶段点亮为 True）
    assert cfg.memory.v2.facts_extract is True


def test_t1_4_unknown_key_in_v2_does_not_crash(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2]\nfacts_extract = true\nbogus_key = 1\n"))
    # unknown key dropped by _load_section, known flag still parsed
    assert cfg.memory.v2.facts_extract is True


def test_default_appconfig_has_v2():
    """AppConfig() 默认（无 config 文件）也要带可用的 v2。"""
    from config import AppConfig
    assert isinstance(AppConfig().memory.v2, MemoryV2Config)
