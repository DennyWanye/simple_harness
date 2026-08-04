# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deskpet.agent.assembler import build_default_assembler
from deskpet.agent.assembler.bundle import AssemblyPolicy
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.project_rules import ProjectRulesComponent


def _context(
    *,
    task_type: str,
    root: Path | None = None,
    active_path: Path | str | None = None,
    rules_config: dict | None = None,
    user_message: str = "work on the project",
) -> ComponentContext:
    config: dict = {}
    if root is not None:
        config["workspace_context"] = {
            "verified": True,
            "root": str(root),
            "active_path": str(active_path or root),
        }
    if rules_config is not None:
        config["project_rules"] = rules_config
    return ComponentContext(
        task_type=task_type,
        policy=AssemblyPolicy(task_type=task_type, prefer=["project_rules"]),
        user_message=user_message,
        config=config,
    )


@pytest.mark.asyncio
async def test_general_task_without_workspace_is_ineligible() -> None:
    result = await ProjectRulesComponent().provide(_context(task_type="chat"))
    assert result.fragments == []
    assert result.meta["reason"] == "workspace_unverified"


@pytest.mark.asyncio
async def test_prompt_path_is_not_treated_as_verified_workspace(monkeypatch, tmp_path) -> None:
    def forbidden(_selection):  # noqa: ANN001
        raise AssertionError("unverified prompt path must not trigger a disk scan")

    monkeypatch.setattr(
        "deskpet.agent.assembler.components.project_rules._discover_rule_files",
        forbidden,
    )
    result = await ProjectRulesComponent().provide(
        _context(
            task_type="chat",
            user_message=f"please inspect {tmp_path}",
        )
    )
    assert result.meta["reason"] == "workspace_unverified"


@pytest.mark.asyncio
async def test_e2e_project_rules_fault_fails_optional_component_closed(monkeypatch, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "AGENTS.md").write_text("must not be loaded", encoding="utf-8")
    monkeypatch.setattr(
        "deskpet.context_os_e2e_hooks.consume_context_os_e2e_fault",
        lambda name: True if name == "project_rules_io_error" else None,
    )

    result = await ProjectRulesComponent().provide(
        _context(task_type="chat", root=root)
    )

    assert result.fragments == []
    assert result.meta["reason"] == "read_failed:FixtureProjectRulesIOError"


@pytest.mark.asyncio
async def test_nested_rules_are_nearest_first_with_full_file_hash(tmp_path) -> None:
    root = tmp_path / "workspace"
    active_dir = root / "pkg" / "src"
    active_dir.mkdir(parents=True)
    active_file = active_dir / "feature.py"
    active_file.write_text("pass\n", encoding="utf-8")
    root_rule = root / "AGENTS.md"
    nested_rule = root / "pkg" / "AGENTS.md"
    root_rule.write_text("root rule", encoding="utf-8")
    nested_rule.write_text("nested rule", encoding="utf-8")

    result = await ProjectRulesComponent().provide(
        _context(task_type="chat", root=root, active_path=active_file)
    )

    assert [fragment.meta["path"] for fragment in result.fragments] == [
        "pkg/AGENTS.md",
        "AGENTS.md",
    ]
    assert "nested rule" in str(result.fragments[0].content)
    assert result.fragments[0].priority > result.fragments[1].priority
    expected_hash = hashlib.sha256(nested_rule.read_bytes()).hexdigest()
    assert result.fragments[0].meta["sha256"] == expected_hash
    assert result.meta["reason"] == "matched_verified_active_path"


@pytest.mark.asyncio
async def test_rules_directory_is_loaded_without_recursive_escape(tmp_path) -> None:
    root = tmp_path / "workspace"
    rules_dir = root / ".deskpet" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "style.md").write_text("use strict typing", encoding="utf-8")

    result = await ProjectRulesComponent().provide(
        _context(task_type="workspace", root=root)
    )
    assert [fragment.meta["path"] for fragment in result.fragments] == [
        ".deskpet/rules/style.md"
    ]


@pytest.mark.asyncio
async def test_active_path_traversal_outside_workspace_is_rejected(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "AGENTS.md").write_text("must not load", encoding="utf-8")

    result = await ProjectRulesComponent().provide(
        _context(task_type="chat", root=root, active_path=root / ".." / "outside")
    )
    assert result.fragments == []
    assert result.meta["reason"] == "active_path_outside_workspace"


@pytest.mark.asyncio
async def test_symlinked_rule_outside_workspace_is_rejected(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside-rules.md"
    outside.write_text("external secret rule", encoding="utf-8")
    link = root / "AGENTS.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")

    result = await ProjectRulesComponent().provide(
        _context(task_type="chat", root=root)
    )
    assert result.fragments == []
    assert result.meta["reason"] == "no_matching_rules"
    assert result.meta["rejected_sources"] == [
        {"path": "AGENTS.md", "reason": "outside_workspace"}
    ]


@pytest.mark.asyncio
async def test_nearest_rule_consumes_budget_before_parent_rule(tmp_path) -> None:
    root = tmp_path / "workspace"
    nested = root / "pkg"
    nested.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rule " * 20, encoding="utf-8")
    (nested / "AGENTS.md").write_text("nested rule " * 40, encoding="utf-8")

    result = await ProjectRulesComponent().provide(
        _context(
            task_type="chat",
            root=root,
            active_path=nested,
            rules_config={"max_chars": 110, "max_tokens": 1_000},
        )
    )
    assert len(result.fragments) == 1
    assert result.fragments[0].meta["path"] == "pkg/AGENTS.md"
    assert result.fragments[0].meta["budget_truncated"] is True
    assert len(str(result.fragments[0].content)) <= 110
    assert "project rules truncated" in str(result.fragments[0].content)
    assert {item["path"] for item in result.meta["omitted"]} == {"AGENTS.md"}


@pytest.mark.asyncio
async def test_rule_changes_are_reread_next_turn(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    rule = root / "AGENTS.md"
    rule.write_text("version one", encoding="utf-8")
    component = ProjectRulesComponent()
    ctx = _context(task_type="chat", root=root)

    first = await component.provide(ctx)
    rule.write_text("version two", encoding="utf-8")
    second = await component.provide(ctx)

    assert first.fragments[0].meta["sha256"] != second.fragments[0].meta["sha256"]
    assert "version two" in str(second.fragments[0].content)


def test_default_factory_registers_rules_only_for_code_policy() -> None:
    assembler = build_default_assembler()
    assert "project_rules" in assembler._registry.names()
    assert "project_rules" in assembler._policies["code"].prefer
    assert "project_rules" not in assembler._policies["chat"].prefer
