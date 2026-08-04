# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
from pathlib import Path

from deskpet.harness.drivers import react, react_boundary, react_loop


ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / "backend/deskpet/harness/drivers/react.py"
LOOP = ROOT / "backend/deskpet/harness/drivers/react_loop.py"
BOUNDARY = ROOT / "backend/deskpet/harness/drivers/react_boundary.py"


def _top_level_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def test_react_responsibilities_have_one_source_module() -> None:
    driver_classes = _top_level_classes(DRIVER)
    loop_classes = _top_level_classes(LOOP)
    boundary_classes = _top_level_classes(BOUNDARY)

    assert "ReActDriver" in driver_classes
    assert "AgentLoopCollaborator" not in driver_classes
    assert "ReactCommandBoundary" not in driver_classes

    assert "AgentLoopCollaborator" in loop_classes
    assert "ReActDriver" not in loop_classes
    assert "ReactCommandBoundary" not in loop_classes

    assert boundary_classes == {"ReactCommandBoundary"}


def test_historical_reexports_are_the_same_objects() -> None:
    assert react.ReactCommandBoundary is react_boundary.ReactCommandBoundary
    assert react.AgentLoopCollaborator is react_loop.AgentLoopCollaborator
    assert react.ReactToolBatch is react_loop.ReactToolBatch


def test_react_modules_have_bounded_file_size() -> None:
    assert len(DRIVER.read_text(encoding="utf-8").splitlines()) <= 5_200
    assert len(LOOP.read_text(encoding="utf-8").splitlines()) <= 1_200
    assert len(BOUNDARY.read_text(encoding="utf-8").splitlines()) <= 500


def test_split_modules_do_not_reintroduce_product_or_workflow_owners() -> None:
    forbidden = (
        "ppt_tools",
        "research_tools",
        "deep_research",
        "deskpet.workflows.engine",
        "deskpet.workflows.definitions",
        "SessionDB",
        "websocket",
    )
    violations: list[str] = []
    for path in (DRIVER, LOOP, BOUNDARY):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in source:
                violations.append(f"{path.name}: {name}")
    assert violations == []


def test_production_composition_uses_the_new_module_boundaries() -> None:
    composition = (
        ROOT
        / "backend/deskpet/harness/adapters/product_composition.py"
    ).read_text(encoding="utf-8")
    spawn_tool = (
        ROOT
        / "backend/deskpet/tools/code_tools/spawn_subagents_tool.py"
    ).read_text(encoding="utf-8")

    assert (
        "from deskpet.harness.drivers.react_loop import AgentLoopCollaborator"
        in composition
    )
    assert (
        "from deskpet.harness.drivers.react_boundary import "
        "ReactCommandBoundary"
        in spawn_tool
    )
