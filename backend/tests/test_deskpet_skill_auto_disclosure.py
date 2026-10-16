# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for WI-4.1 二级披露做实（embedding 强匹配 → 自动载 skill 正文）.

Covers:
  T1 — 3 skills (strong/mid/weak match) → only strong body in prelude, weak desc-only.
  T2 — over-budget: high usage_count body retained, low dropped.
  T3 — embedder=None → degrade to desc list (no crash).
  T4 — disable_model_invocation skill → not auto-loaded.
  T5 — flag off → desc-list-only (byte regression vs current behavior).
  T6 — loader.read_body(name) returns raw body without arg substitution.
  T7 — SkillMatcher.match returns sorted (name, sim) list.
  T8 — SkillMatcher caches skill embeddings; query embedding recomputed per call.
  T9 — SkillMatcher.match with embedder=None returns [].
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.skill import SkillComponent
from deskpet.skills.loader import SkillLoader, SkillMeta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta(
    name: str,
    description: str,
    body: str = "",
    *,
    disable_model_invocation: bool = False,
    usage_count: int = 0,
) -> SkillMeta:
    """Build a minimal SkillMeta for testing."""
    return SkillMeta(
        name=name,
        description=description,
        version="0.1.0",
        author="test",
        scope="built-in",
        path=f"/fake/{name}/SKILL.md",
        disable_model_invocation=disable_model_invocation,
    )


def _make_skill_dir(tmp_path: Path, name: str, description: str, body: str) -> Path:
    """Write a real SKILL.md for loader tests."""
    # Do NOT use textwrap.dedent with indented content — it strips 8 spaces
    # and the `---` ends up with leading spaces, making _split_frontmatter miss it.
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "version: 0.1.0",
        "author: test",
        "---",
        body,
        "",
    ]
    fm = "\n".join(lines)
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")
    return skill_dir


def _make_embedder(vectors: dict[str, list[float]]) -> Any:
    """Fake synchronous embedder. encode(text) -> list[float]."""

    class _FakeEmbedder:
        def encode(self, text: str) -> list[float]:
            # Return stored vector for known texts, else zero vector
            for key, vec in vectors.items():
                if key in text:
                    return list(vec)
            # Return a distinct vector per unknown text to avoid accidental matches
            # Use hash-based deterministic vector
            h = hash(text) % 1000
            return [float(h), 0.0, 0.0]

    return _FakeEmbedder()


def _unit_vec(idx: int, dim: int = 3) -> list[float]:
    """Return a unit vector with 1.0 at position idx."""
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v


# ---------------------------------------------------------------------------
# T6 — loader.read_body(name) returns raw body, no arg substitution
# ---------------------------------------------------------------------------


def test_read_body_returns_raw_body_no_substitution(tmp_path: Path) -> None:
    """read_body should return the unsubstituted body text."""
    body_text = "Do ${args[0]} with ${args[1]} items."
    _make_skill_dir(tmp_path, "raw-demo", "A demo skill", body_text)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    body = loader.read_body("raw-demo")
    assert "${args[0]}" in body
    assert "${args[1]}" in body
    assert "Do" in body


def test_read_body_unknown_skill_raises(tmp_path: Path) -> None:
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    with pytest.raises(KeyError):
        loader.read_body("nonexistent")


def test_read_body_strips_frontmatter(tmp_path: Path) -> None:
    """read_body must NOT include the frontmatter block."""
    body_text = "This is the skill body."
    _make_skill_dir(tmp_path, "strip-fm", "Test", body_text)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    body = loader.read_body("strip-fm")
    assert "---" not in body.strip().split("\n")[0]
    assert "This is the skill body." in body


# ---------------------------------------------------------------------------
# FP-5 缺口 5h/5i 回归 (2026-06-06 真机抓 bug)：
# 生产 BGE-M3 embedder 是 ASYNC（``async embed(list[str]) -> list[list[float]]``
# / ``async encode(list[str]) -> ndarray``）。原 matcher 同步调 ``encode(text)``
# → 拿到未 await 的 coroutine → 缓存恒空 → 自动披露真机 top_sim=0.0 永远零匹配。
# 旧单测只用 SYNC mock 所以全绿，掩盖了这条生产死链。本组用 async embedder
# 复现生产契约，确保 match_async 能惰性异步建缓存 + 产出真相似度。
# ---------------------------------------------------------------------------


def _make_async_embedder(vectors: dict[str, list[float]]) -> Any:
    """Production-shaped ASYNC embedder: async embed(list[str]) -> list[list[float]]."""

    class _AsyncEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            out: list[list[float]] = []
            for text in texts:
                vec = None
                for key, v in vectors.items():
                    if key in text:
                        vec = list(v)
                        break
                if vec is None:
                    h = hash(text) % 1000
                    vec = [float(h), 0.0, 0.0]
                out.append(vec)
            return out

        async def encode(self, texts: list[str]):  # also async, list-in
            return await self.embed(texts)

    return _AsyncEmbedder()


# ---------------------------------------------------------------------------
# T1 — SkillComponent 3-tier: strong body in prelude, weak desc-only
# ---------------------------------------------------------------------------


def _make_ctx(
    skills: list[SkillMeta],
    user_message: str = "query",
    auto_disclosure_enabled: bool = True,
    strong_threshold: float = 0.70,
    budget_tokens: int = 8000,
) -> ComponentContext:
    """Build a ComponentContext with a fake registry and config."""
    registry = MagicMock()
    registry.all.return_value = skills
    # select returns all (no task type filtering for these tests)
    registry.select.return_value = skills

    ctx = ComponentContext(
        task_type="chat",
        policy=MagicMock(prefer=[]),
        user_message=user_message,
        config={
            "skills": {
                "auto_disclosure": {
                    "enabled": auto_disclosure_enabled,
                    "strong_threshold": strong_threshold,
                    "budget_tokens": budget_tokens,
                }
            }
        },
    )
    ctx.skill_registry = registry
    return ctx


@pytest.mark.asyncio
async def test_default_threshold_rejects_ambiguous_short_query_match(
    tmp_path: Path,
) -> None:
    """An ambiguous BGE-style score must not activate a restrictive Skill."""
    _make_skill_dir(
        tmp_path,
        "recall-yesterday",
        "回忆昨天聊过的亮点",
        "only memory_recall",
    )
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("recall-yesterday")
    assert meta is not None

    matcher = MagicMock()
    matcher.match_async = AsyncMock(return_value=[("recall-yesterday", 0.55)])
    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    result = await component.provide(
        _make_ctx(
            [meta],
            user_message="每周五提醒我整理游戏开发进展",
        )
    )

    assert result.meta["auto_loaded_count"] == 0
    assert result.meta["active_skill_scope_ids"] == []


# ---------------------------------------------------------------------------
# T5 — flag off → desc-list-only (byte regression vs current)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_no_matcher_needed(tmp_path: Path) -> None:
    """With flag off, SkillComponent works even if no matcher/loader injected."""
    _make_skill_dir(tmp_path, "vanilla", "vanilla description", "## Vanilla body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("vanilla")
    assert meta is not None

    # No matcher, no loader — flag off = current behavior
    component = SkillComponent()  # no matcher/loader
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=False,
    )
    result: Slice = await component.provide(ctx)
    assert "vanilla" in result.text_content
    assert "Vanilla body" not in result.text_content


# ---------------------------------------------------------------------------
# TC-5.1 真机回归 (2026-06-11)：total=1 / top_sim=0.000 双根因
#
# ① ``loader.select(task_type)`` 按 task_types frontmatter 过滤，而 builtin
#   claude-code-v1 格式 skill 全是 ``task_types=[]`` → 全被滤掉 → 组件只拿到
#   1 个漏网 skill（真机 log: skill_auto_disclosed total=1）。auto-disclosure
#   的设计是「全集进 desc list + embedding 决定强匹配」→ flag ON 必须用
#   ``registry.all()`` 全集；flag OFF 保持 select 路径（字节级 BC）。
# ② lifespan 的 ``matcher.build(loader.all())`` 同步调 async encode → 静默
#   no-op（unawaited coroutine 被吞）→ 需要 ``build_async`` 预热入口。
# ③ log 的 top_sim 打 strong_matches[0]（空时 0.0）掩盖真实 ranked 分数。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_keeps_select_path(tmp_path: Path) -> None:
    """flag OFF 保持 select() venue（字节级 BC：不泄露全集进 chat prelude）。"""
    _make_skill_dir(tmp_path, "research-skill", "deep research helper", "## Body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    research = loader.get("research-skill")
    other = _make_meta("other", "unrelated description")

    registry = MagicMock()
    registry.select.return_value = [other]
    registry.all.return_value = [other, research]

    component = SkillComponent()
    ctx = _make_ctx([], user_message="query", auto_disclosure_enabled=False)
    ctx.skill_registry = registry

    result: Slice = await component.provide(ctx)
    assert "other" in result.text_content
    assert "research-skill" not in result.text_content


# ---------------------------------------------------------------------------
# TC-5.1 混合匹配 (2026-06-11)：triggers 词法路。
# BGE-M3 int8 对短中文 query 区分度不够(实测 on-target 0.45~0.55 vs
# off-target 0.53+)——显式 triggers 命中直接强匹配,embedding 兜 paraphrase。
# ---------------------------------------------------------------------------


def test_loader_parses_triggers_legacy(tmp_path: Path) -> None:
    """legacy 格式 frontmatter 的 triggers + when_to_use 提升为一等字段。"""
    lines = [
        "---",
        "name: trig-skill",
        "description: a skill",
        "version: 0.1.0",
        "author: test",
        "when_to_use: 用户要深度调研时",
        "triggers: [深度调研, 调研报告]",
        "---",
        "body",
    ]
    d = tmp_path / "trig-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("trig-skill")
    assert meta is not None
    assert meta.triggers == ["深度调研", "调研报告"]
    assert meta.when_to_use == "用户要深度调研时"
    assert meta.to_dict()["triggers"] == ["深度调研", "调研报告"]
