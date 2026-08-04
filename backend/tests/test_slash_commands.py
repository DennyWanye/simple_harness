# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-A2/A4/A5 v1 — slash command dispatcher + REST API 测试.

测试范围:
  - dispatch_slash_command 各路径（help / goal / skill / unknown）
  - FeaturesConfig.slash_commands flag 默认 OFF
  - /api/skills/list REST endpoint OFF/ON 行为
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── dispatch_slash_command 单元测试 ─────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_unknown_command_no_skill_loader():
    from deskpet.commands import dispatch_slash_command
    res = await dispatch_slash_command(
        "notexist", "", "sid", skill_loader=None, session_goal_store=None,
    )
    assert res["type"] == "error"
    assert "unknown command" in res["message"]


@pytest.mark.asyncio
async def test_dispatch_empty_command():
    from deskpet.commands import dispatch_slash_command
    res = await dispatch_slash_command(
        "", "", "sid", skill_loader=None, session_goal_store=None,
    )
    assert res["type"] == "error"
    assert "empty" in res["message"]


@pytest.mark.asyncio
async def test_dispatch_help_with_skill_loader():
    from deskpet.commands import dispatch_slash_command
    mock_loader = MagicMock()
    mock_loader.list_skills.return_value = [
        {"name": "ppt-generate", "description": "Generate PowerPoint."},
        {"name": "deep-research", "description": "Deep research with citations."},
    ]
    res = await dispatch_slash_command(
        "help", "", "sid", skill_loader=mock_loader,
    )
    assert res["type"] == "help"
    assert len(res["builtins"]) >= 3  # help / goal set / goal clear
    assert len(res["skills"]) == 2
    assert res["skills"][0]["name"] == "ppt-generate"


@pytest.mark.asyncio
async def test_dispatch_help_no_skill_loader_safe():
    """skill_loader=None → builtins 仍返但 skills 是空 list，不抛."""
    from deskpet.commands import dispatch_slash_command
    res = await dispatch_slash_command(
        "help", "", "sid", skill_loader=None,
    )
    assert res["type"] == "help"
    assert res["skills"] == []


@pytest.mark.asyncio
async def test_dispatch_goal_set():
    """`/goal write a haiku` → store.set + 返 goal_set."""
    from deskpet.commands import dispatch_slash_command

    mock_goal = MagicMock(text="write a haiku", max_iterations=10)
    mock_store = MagicMock()
    mock_store.set.return_value = mock_goal

    res = await dispatch_slash_command(
        "goal", "write a haiku", "sid-1", session_goal_store=mock_store,
    )
    assert res["type"] == "goal_set"
    assert res["text"] == "write a haiku"
    mock_store.set.assert_called_once_with("sid-1", "write a haiku")


@pytest.mark.asyncio
async def test_dispatch_goal_clear():
    from deskpet.commands import dispatch_slash_command
    mock_store = MagicMock()
    mock_store.clear.return_value = True
    res = await dispatch_slash_command(
        "goal", "clear", "sid-1", session_goal_store=mock_store,
    )
    assert res["type"] == "goal_cleared"
    assert res["ok"] is True
    mock_store.clear.assert_called_once_with("sid-1")


@pytest.mark.asyncio
async def test_dispatch_goal_disabled_no_store():
    from deskpet.commands import dispatch_slash_command
    res = await dispatch_slash_command(
        "goal", "x", "sid", session_goal_store=None,
    )
    assert res["type"] == "error"
    assert "disabled" in res["message"]


@pytest.mark.asyncio
async def test_dispatch_skill_invoke():
    """`/ppt-generate topic` → skill_loader.invoke_script."""
    from deskpet.commands import dispatch_slash_command

    res = await dispatch_slash_command(
        "ppt-generate",
        "test topic",
        "sid",
        skill_catalog={"ppt-generate": object()},
    )
    assert res == {
        "type": "instruction_activation_request",
        "source": "slash_command",
        "skill_name": "ppt-generate",
        "arguments": ["test", "topic"],
        "user_text": "/ppt-generate test topic",
    }


@pytest.mark.asyncio
async def test_dispatch_skill_unknown_returns_error():
    from deskpet.commands import dispatch_slash_command

    res = await dispatch_slash_command(
        "notaskill", "", "sid", skill_catalog={"ppt-generate"},
    )
    assert res["type"] == "error"
    assert "unknown skill" in res["message"]


@pytest.mark.asyncio
async def test_dispatch_skill_exception_safe():
    """skill 跑挂时返 error 不抛."""
    from deskpet.commands import dispatch_slash_command

    class BrokenCatalog:
        def contains(self, _name):
            raise RuntimeError("boom")

    res = await dispatch_slash_command(
        "anyskill", "", "sid", skill_catalog=BrokenCatalog(),
    )
    assert res["type"] == "error"
    assert "unknown skill" in res["message"]


@pytest.mark.asyncio
async def test_dispatch_managed_skill_keeps_typed_frozen_scope():
    from deskpet.commands import dispatch_slash_command

    class Selection:
        def to_dict(self):
            return {
                "schema": "prepared_skill_invocation_scope/v1",
                "pack_id": "skill-ppt-generate",
                "scope_hash": "a" * 64,
            }

    class Catalog:
        def resolve_selection(self, name):
            if name != "ppt-generate":
                raise KeyError(name)
            return Selection()

    res = await dispatch_slash_command(
        "ppt-generate", "topic", "sid", skill_catalog=Catalog()
    )
    assert res["type"] == "instruction_activation_request"
    assert res["prepared_skill_scope"] == Selection().to_dict()


# ─── FeaturesConfig 字段存在性 ─────────────────────────────


def test_features_config_has_3_v1_fields():
    """plans/2026-05-25-... WI-D1：FeaturesConfig 必须有 3 个 flag.

    2026-06-27 测试阶段点亮：三者出厂默认 ON。
    """
    from config import FeaturesConfig
    cfg = FeaturesConfig()
    assert cfg.slash_commands is True
    assert cfg.goal_mode is True
    assert cfg.agent_parallel is True


def test_app_config_includes_features():
    from config import AppConfig
    cfg = AppConfig()
    assert hasattr(cfg, "features")
    assert cfg.features.slash_commands is True  # 测试阶段点亮
