# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-5: triggered knowledge injection.

Knowledge snippets are SKILL.md bundles that are not user-invocable.  They are
only loaded/injected when the explicit [skills] knowledge_enabled flag is on.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config import load_config
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.skill import SkillComponent
from deskpet.agent.assembler.policy import AssemblyPolicy
from deskpet.agent.context_compressor import _partition
from deskpet.skills.loader import SkillLoader, SkillMeta
from deskpet.skills.skill_matcher import SkillMatcher


_PPT_BODY = "生成 PPT 的注意事项：先列大纲再渲染，避免长标题竖排。"


def _write_knowledge(root: Path, name: str, *, triggers: list[str], body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    trigger_text = ", ".join(triggers)
    (skill_dir / "SKILL.md").write_text(
        dedent(
            f"""
            ---
            name: {name}
            description: Background knowledge for {name}
            triggers: [{trigger_text}]
            user-invocable: false
            ---
            {body}
            """
        ).strip(),
        encoding="utf-8",
    )


def _ctx(
    skills: list[SkillMeta],
    *,
    user_message: str,
    knowledge_enabled: bool,
) -> ComponentContext:
    registry = MagicMock()
    registry.all.return_value = skills
    registry.select.return_value = skills
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat", prefer=["skill"]),
        user_message=user_message,
        config={
            "skills": {
                "knowledge_enabled": knowledge_enabled,
                "auto_disclosure": {
                    "enabled": True,
                    "strong_threshold": 0.55,
                    "budget_tokens": 8000,
                    "per_skill_max_tokens": 2000,
                },
            }
        },
    )
    ctx.skill_registry = registry
    return ctx


@pytest.mark.asyncio
async def test_trigger_keyword_injects_knowledge(tmp_path: Path) -> None:
    _write_knowledge(tmp_path, "ppt-tips", triggers=["ppt"], body=_PPT_BODY)
    loader = SkillLoader([tmp_path], enable_watch=False, knowledge_enabled=True)
    loader.reload()
    meta = loader.get("ppt-tips")
    assert meta is not None
    assert meta.user_invocable is False

    component = SkillComponent(
        skill_matcher=SkillMatcher(None),
        skill_loader=loader,
    )
    result = await component.provide(
        _ctx([meta], user_message="帮我做一个 ppt", knowledge_enabled=True)
    )

    assert _PPT_BODY in result.text_content
    assert "ppt-tips" not in result.text_content.split("---", 1)[0]
    assert result.meta["auto_loaded_count"] == 1
    assert result.meta["triggered"] is True
    assert result.meta["protected"] is True
    assert result.meta["knowledge_loaded_count"] == 1


@pytest.mark.asyncio
async def test_unrelated_query_no_inject(tmp_path: Path) -> None:
    _write_knowledge(tmp_path, "ppt-tips", triggers=["ppt"], body=_PPT_BODY)
    loader = SkillLoader([tmp_path], enable_watch=False, knowledge_enabled=True)
    loader.reload()
    meta = loader.get("ppt-tips")
    assert meta is not None

    component = SkillComponent(
        skill_matcher=SkillMatcher(None),
        skill_loader=loader,
    )
    result = await component.provide(
        _ctx([meta], user_message="今天天气怎么样", knowledge_enabled=True)
    )

    assert _PPT_BODY not in result.text_content
    assert result.meta["auto_loaded_count"] == 0
    assert result.meta["knowledge_loaded_count"] == 0
    assert result.meta.get("triggered") is not True


@pytest.mark.asyncio
async def test_flag_off_bc(tmp_path: Path) -> None:
    _write_knowledge(tmp_path, "ppt-tips", triggers=["ppt"], body=_PPT_BODY)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()

    assert loader.get("ppt-tips") is None

    # Even if a caller hands a stale/non-invocable meta to the component, flag
    # off must keep it out of the user-visible prelude and body injection path.
    stale_meta = SkillMeta(
        name="ppt-tips",
        description="Background knowledge for ppt-tips",
        version="",
        author="",
        scope="built-in",
        path=str(tmp_path / "ppt-tips" / "SKILL.md"),
        triggers=["ppt"],
        user_invocable=False,
        source_format="claude-code-v1",
    )
    component = SkillComponent(
        skill_matcher=SkillMatcher(None),
        skill_loader=loader,
    )
    result = await component.provide(
        _ctx([stale_meta], user_message="帮我做一个 ppt", knowledge_enabled=False)
    )

    assert "ppt-tips" not in result.text_content
    assert _PPT_BODY not in result.text_content
    assert result.meta["count"] == 0
    assert result.meta["auto_loaded_count"] == 0


def test_load_config_skills_knowledge_enabled_flag(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [skills]
            knowledge_enabled = true
            """
        ).strip(),
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.skills.knowledge_enabled is True


def test_load_config_skills_knowledge_enabled_defaults_off(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("schema_version = 1\n", encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.skills.knowledge_enabled is False


@pytest.mark.asyncio
async def test_triggered_knowledge_protected_from_compaction(tmp_path: Path) -> None:
    _write_knowledge(tmp_path, "ppt-tips", triggers=["ppt"], body=_PPT_BODY)
    loader = SkillLoader([tmp_path], enable_watch=False, knowledge_enabled=True)
    loader.reload()
    meta = loader.get("ppt-tips")
    assert meta is not None

    component = SkillComponent(
        skill_matcher=SkillMatcher(None),
        skill_loader=loader,
    )
    result = await component.provide(
        _ctx([meta], user_message="帮我做一个 ppt", knowledge_enabled=True)
    )
    assert result.meta["protected"] is True

    messages = [
        {"role": "system", "content": result.text_content, "meta": dict(result.meta)},
        {"role": "user", "content": "head"},
        {"role": "assistant", "content": "middle 1"},
        {"role": "user", "content": "middle 2"},
        {"role": "assistant", "content": "tail"},
    ]

    system_msgs, _head, middle, _tail = _partition(messages, first_n=1, last_n=1)

    assert any(_PPT_BODY in (m.get("content") or "") for m in system_msgs)
    assert not any(_PPT_BODY in (m.get("content") or "") for m in middle)

