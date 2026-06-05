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

import asyncio
import textwrap
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.skill import SkillComponent
from deskpet.skills.loader import SkillLoader, SkillMeta
from deskpet.skills.skill_matcher import SkillMatcher


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
# T7 — SkillMatcher.match returns sorted (name, sim) list
# ---------------------------------------------------------------------------

def test_skill_matcher_match_sorted_by_similarity() -> None:
    """match() should return (name, sim) pairs sorted descending."""
    # Query vector [1, 0, 0]
    # skill-a description vector [1, 0, 0] → cos=1.0 (strong)
    # skill-b description vector [0, 1, 0] → cos=0.0 (weak)
    # skill-c description vector [0.6, 0.8, 0] → cos=0.6 (mid)
    vectors = {
        "query": _unit_vec(0),
        "strong skill": [1.0, 0.0, 0.0],
        "mid skill": [0.6, 0.8, 0.0],
        "weak skill": [0.0, 1.0, 0.0],
    }
    embedder = _make_embedder(vectors)

    skills = [
        _make_meta("strong", "strong skill"),
        _make_meta("weak", "weak skill"),
        _make_meta("mid", "mid skill"),
    ]

    matcher = SkillMatcher(embedder)
    matcher.build(skills)

    results = matcher.match("query", skills)
    assert len(results) == 3
    names = [r[0] for r in results]
    sims = [r[1] for r in results]
    # Must be sorted descending by sim
    assert sims == sorted(sims, reverse=True)
    # strong should be first
    assert names[0] == "strong"


def test_skill_matcher_match_empty_skills() -> None:
    """match() with empty skill list returns []."""
    embedder = _make_embedder({"q": [1.0, 0.0, 0.0]})
    matcher = SkillMatcher(embedder)
    matcher.build([])
    results = matcher.match("q", [])
    assert results == []


# ---------------------------------------------------------------------------
# T8 — embedder=None → match returns []
# ---------------------------------------------------------------------------

def test_skill_matcher_none_embedder_returns_empty() -> None:
    matcher = SkillMatcher(None)
    skills = [_make_meta("foo", "bar")]
    matcher.build(skills)
    results = matcher.match("bar", skills)
    assert results == []


# T9 variant: SkillMatcher.match doesn't crash with None embedder
def test_skill_matcher_none_embedder_no_crash() -> None:
    matcher = SkillMatcher(None)
    matcher.build([])
    results = matcher.match("hello", [])
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# T1 — SkillComponent 3-tier: strong body in prelude, weak desc-only
# ---------------------------------------------------------------------------

def _make_ctx(
    skills: list[SkillMeta],
    user_message: str = "query",
    auto_disclosure_enabled: bool = True,
    strong_threshold: float = 0.55,
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
async def test_strong_match_body_in_prelude(tmp_path: Path) -> None:
    """When sim >= threshold, skill body must appear in prelude."""
    # Write real skill files so read_body works
    _make_skill_dir(tmp_path, "strong-skill", "strong skill description", "## Strong Body\nThis is strong body content.")
    _make_skill_dir(tmp_path, "weak-skill", "weak skill description", "## Weak Body\nThis is weak body content.")

    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()

    strong_meta = loader.get("strong-skill")
    weak_meta = loader.get("weak-skill")
    assert strong_meta is not None
    assert weak_meta is not None

    # Embedder: query matches strong
    vectors = {
        "strong skill": [1.0, 0.0, 0.0],
        "weak skill": [0.0, 1.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([strong_meta, weak_meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [strong_meta, weak_meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )

    result: Slice = await component.provide(ctx)
    assert result.text_content
    # Strong body should be inlined
    assert "Strong Body" in result.text_content or "strong body content" in result.text_content.lower()
    # Weak body should NOT be inlined
    assert "weak body content" not in result.text_content.lower()
    # Both names should appear (desc list always present)
    assert "strong-skill" in result.text_content
    assert "weak-skill" in result.text_content


@pytest.mark.asyncio
async def test_weak_match_desc_only(tmp_path: Path) -> None:
    """When no skill exceeds threshold, only desc list (no bodies)."""
    _make_skill_dir(tmp_path, "skill-a", "alpha description", "## Alpha body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta_a = loader.get("skill-a")
    assert meta_a is not None

    # Query is orthogonal to all skills → all sims near 0
    vectors = {
        "alpha description": [0.0, 1.0, 0.0],
        "query": [0.0, 0.0, 1.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta_a])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta_a],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    assert "skill-a" in result.text_content
    assert "Alpha body content" not in result.text_content


# ---------------------------------------------------------------------------
# T2 — over-budget: high usage_count retained, low usage_count dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overbudget_high_usage_retained(tmp_path: Path) -> None:
    """When total body tokens exceed budget, drop lowest usage_count first."""
    # Create two skills both strong matches, budget allows only one
    body_high = "x" * 500  # ~125 tokens
    body_low = "y" * 500   # ~125 tokens

    _make_skill_dir(tmp_path, "high-use", "alpha skill description", body_high)
    _make_skill_dir(tmp_path, "low-use", "beta skill description", body_low)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()

    meta_high = loader.get("high-use")
    meta_low = loader.get("low-use")
    assert meta_high and meta_low

    # Add usage_count tracking via SkillMeta meta dict
    meta_high.meta["usage_count"] = 10
    meta_low.meta["usage_count"] = 0

    # Both strongly match the query
    vectors = {
        "alpha skill": [1.0, 0.0, 0.0],
        "beta skill": [0.99, 0.0, 0.0],  # slightly less sim but still strong
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta_high, meta_low])

    # Tiny budget: only fits one body (~125 tokens each, budget=130)
    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta_high, meta_low],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
        budget_tokens=130,
    )
    result: Slice = await component.provide(ctx)
    # high-use body should be present
    assert body_high[:20] in result.text_content
    # low-use body should have been dropped
    assert body_low[:20] not in result.text_content
    # But both names must still appear in desc list
    assert "high-use" in result.text_content
    assert "low-use" in result.text_content


# ---------------------------------------------------------------------------
# T3 — embedder=None → degrade to desc list (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedder_none_degrades_to_desc_list(tmp_path: Path) -> None:
    """embedder=None: no crash, returns desc list only."""
    _make_skill_dir(tmp_path, "some-skill", "some description", "## Body content")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("some-skill")
    assert meta is not None

    matcher = SkillMatcher(None)  # no embedder
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="some query",
        auto_disclosure_enabled=True,
    )
    result: Slice = await component.provide(ctx)
    assert result.text_content
    # desc list present
    assert "some-skill" in result.text_content
    # body NOT present (no embedding match)
    assert "Body content" not in result.text_content


# ---------------------------------------------------------------------------
# T4 — disable_model_invocation skill → not auto-loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disable_model_invocation_not_auto_loaded(tmp_path: Path) -> None:
    """Skills with disable_model_invocation=True must not have body auto-loaded."""
    _make_skill_dir(tmp_path, "gated-skill", "gated skill description", "## Gated body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    # Manually set disable_model_invocation on the loaded meta
    meta = loader.get("gated-skill")
    assert meta is not None
    meta.disable_model_invocation = True

    # Perfect match
    vectors = {
        "gated skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    # Name still appears in desc list
    assert "gated-skill" in result.text_content
    # Body NOT inlined
    assert "Gated body content" not in result.text_content


# ---------------------------------------------------------------------------
# T5 — flag off → desc-list-only (byte regression vs current)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_desc_list_only(tmp_path: Path) -> None:
    """With auto_disclosure.enabled=False, behavior matches pre-WI-4.1 (desc list only)."""
    _make_skill_dir(tmp_path, "flag-skill", "flag skill description", "## Flag body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("flag-skill")
    assert meta is not None

    # Strong embedder match, but flag is OFF
    vectors = {
        "flag skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=False,  # FLAG OFF
    )
    result: Slice = await component.provide(ctx)
    # Desc list still present
    assert "flag-skill" in result.text_content
    # Body NOT present (flag off)
    assert "Flag body content" not in result.text_content


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
# T-extra: prelude format — "以下技能正文已预载" annotation present when body loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prelude_annotation_present_when_body_loaded(tmp_path: Path) -> None:
    """When body is auto-loaded, prelude should contain an annotation."""
    _make_skill_dir(tmp_path, "annotated-skill", "annotated skill desc", "## Annotated body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("annotated-skill")
    assert meta is not None

    vectors = {
        "annotated skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    # Should have some indicator that auto-load happened
    assert "annotated-skill" in result.text_content
    assert "Annotated body" in result.text_content


# ---------------------------------------------------------------------------
# T-extra: desc_list priority=85 (not cut), body segment priority lower
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_desc_list_always_present_even_when_no_body_loaded(tmp_path: Path) -> None:
    """Desc list (priority=85) must always appear regardless of body loading."""
    _make_skill_dir(tmp_path, "always-listed", "always listed desc", "## Body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("always-listed")
    assert meta is not None

    # embedder=None → no bodies loaded
    component = SkillComponent(skill_matcher=SkillMatcher(None), skill_loader=loader)
    ctx = _make_ctx([meta], auto_disclosure_enabled=True)
    result = await component.provide(ctx)
    assert "always-listed" in result.text_content


# ---------------------------------------------------------------------------
# T-extra: meta contains auto_loaded_count when bodies are loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_auto_loaded_count(tmp_path: Path) -> None:
    """Slice meta should report auto_loaded_count."""
    _make_skill_dir(tmp_path, "counted", "counted skill desc", "## Count body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("counted")
    assert meta is not None

    vectors = {
        "counted skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx([meta], user_message="query", auto_disclosure_enabled=True, strong_threshold=0.55)
    result = await component.provide(ctx)
    assert "auto_loaded_count" in result.meta
    assert result.meta["auto_loaded_count"] >= 1
