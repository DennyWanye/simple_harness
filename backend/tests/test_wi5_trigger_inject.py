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
from deskpet.agent.assembler.policy import AssemblyPolicy
from deskpet.skills.loader import SkillMeta

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


def _write_regular_skill(
    root: Path,
    name: str,
    *,
    triggers: list[str],
    body: str,
    allowed_tools: list[str] | None = None,
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    trigger_text = ", ".join(triggers)
    allowed_text = ", ".join(allowed_tools or [])
    (skill_dir / "SKILL.md").write_text(
        dedent(
            f"""
            ---
            name: {name}
            description: Regular skill {name}
            triggers: [{trigger_text}]
            allowed-tools: [{allowed_text}]
            ---
            {body}
            """
        ).strip(),
        encoding="utf-8",
    )


def _ctx_real_registry(
    registry: object,
    *,
    user_message: str,
    knowledge_enabled: bool,
) -> ComponentContext:
    """Like _ctx but wires the *real* loader as the registry, so the component
    sees exactly what ``loader.all()`` returns — the path that broke in prod
    (loader constructed with knowledge_enabled=False → fragments never loaded)."""
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


@pytest.mark.parametrize(
    "skills",
    [
        [
            SimpleNamespace(name="same", allowed_tools=["read_file"]),
            SimpleNamespace(name="same", allowed_tools=["PPT_PRO"]),
        ],
        [
            SimpleNamespace(name="same", allowed_tools=["Ppt_Create"]),
            SimpleNamespace(name="same", allowed_tools=["read_file"]),
        ],
    ],
)
def test_profile_owned_compat_detection_checks_every_same_name_entry(
    skills: list[SimpleNamespace],
) -> None:
    from deskpet.agent.assembler.components.skill import (
        _is_profile_owned_compat_skill,
    )

    assert _is_profile_owned_compat_skill("same", skills) is True


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


def test_load_config_skills_knowledge_enabled_defaults_on(tmp_path: Path) -> None:
    # 2026-06-27 测试阶段点亮：触发式知识注入出厂默认 ON。
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("schema_version = 1\n", encoding="utf-8")

    cfg = load_config(cfg_path)

    assert cfg.skills.knowledge_enabled is True


def test_load_config_skills_subtable_keeps_knowledge_default_on(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [skills.codify]
            enabled = true
            """
        ).strip(),
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.skills.knowledge_enabled is True
    assert cfg.skills.codify.enabled is True
