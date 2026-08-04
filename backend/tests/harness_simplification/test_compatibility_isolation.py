# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
from pathlib import Path

from deskpet.agent import run_presenter
from deskpet.agent import session_terminal_delivery
from deskpet.compat.run_presenter import build_legacy_run_presenter
from deskpet.compat.session_projection import SessionTerminalReadThrough


ROOT = Path(__file__).resolve().parents[3]
CURRENT_HARNESS_FILES = (
    "backend/deskpet/harness/bootstrap.py",
    "backend/deskpet/harness/projector.py",
    "backend/deskpet/harness/adapters/product_composition.py",
    "backend/deskpet/harness/adapters/subagent_registry.py",
)
COMPATIBILITY_FACADES = (
    "backend/deskpet/agent/task_work_context.py",
    "backend/deskpet/permissions/task_grants.py",
    "backend/deskpet/workflows/trace/redaction.py",
)


def test_retired_names_live_in_the_compatibility_package() -> None:
    assert (
        build_legacy_run_presenter
        is run_presenter.build_product_run_presenter
    )
    assert (
        SessionTerminalReadThrough
        is session_terminal_delivery.SessionTerminalProjectionConsistencyGate
    )
    assert "build_legacy_run_presenter" not in run_presenter.__all__
    assert "SessionTerminalReadThrough" not in session_terminal_delivery.__all__


def test_old_import_paths_remain_lazy_migration_shims() -> None:
    assert (
        run_presenter.build_legacy_run_presenter
        is run_presenter.build_product_run_presenter
    )
    assert (
        session_terminal_delivery.SessionTerminalReadThrough
        is session_terminal_delivery.SessionTerminalProjectionConsistencyGate
    )


def test_current_harness_does_not_import_compatibility_names_or_aggregate() -> None:
    violations: list[str] = []
    forbidden_modules = {
        "deskpet.compat",
        "deskpet.execution.ports",
        "deskpet.agent.task_work_context",
        "deskpet.permissions.task_grants",
        "deskpet.workflows.trace.redaction",
    }
    forbidden_names = {
        "ExecutionUnitOfWork",
        "SessionTerminalReadThrough",
        "build_legacy_run_presenter",
    }
    for relative in CURRENT_HARNESS_FILES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if any(
                    module == item or module.startswith(f"{item}.")
                    for item in forbidden_modules
                ):
                    violations.append(f"{relative}:{node.lineno}:{module}")
                for alias in node.names:
                    if alias.name in forbidden_names:
                        violations.append(
                            f"{relative}:{node.lineno}:{alias.name}"
                        )
            elif isinstance(node, ast.Name) and node.id in forbidden_names:
                violations.append(f"{relative}:{node.lineno}:{node.id}")
    assert violations == []


def test_current_turn_trace_does_not_advertise_retired_decision_layers() -> None:
    source = (
        ROOT / "backend/deskpet/harness/adapters/product_turn_open.py"
    ).read_text(encoding="utf-8")
    assert "legacy_intent_triage" not in source
    assert "legacy_plan_decision" not in source


def test_leaf_compatibility_facades_contain_aliases_only() -> None:
    violations: list[str] = []
    for relative in COMPATIBILITY_FACADES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(
                node,
                (
                    ast.ClassDef,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                violations.append(f"{relative}:{node.lineno}")
    assert violations == []
