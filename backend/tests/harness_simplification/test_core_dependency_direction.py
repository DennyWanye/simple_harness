# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from deskpet.agent import task_work_context as legacy_context
from deskpet.permissions import task_grants as legacy_grants
from deskpet.security.redaction import TraceRedactor
from deskpet.types import task_grants, task_work_context
from deskpet.workflows.trace import redaction as legacy_redaction
from memory.sensitive_filter import redact


ROOT = Path(__file__).resolve().parents[3]
LEAF_MODULES = (
    ROOT / "backend/deskpet/types/task_work_context.py",
    ROOT / "backend/deskpet/types/task_grants.py",
    ROOT / "backend/deskpet/security/redaction.py",
    ROOT / "backend/deskpet/security/sensitive_text.py",
)
HIGHER_PACKAGES = (
    "deskpet.agent",
    "deskpet.permissions",
    "deskpet.workflows",
    "deskpet.capabilities",
    "deskpet.companion",
    "deskpet.harness",
    "memory",
)
LEGACY_CONTRACT_MODULES = (
    "deskpet.agent.task_work_context",
    "deskpet.permissions.task_grants",
    "deskpet.workflows.trace.redaction",
)


def test_shared_contract_modules_are_leaf_dependencies() -> None:
    violations: list[str] = []
    for path in LEAF_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(HIGHER_PACKAGES):
                        violations.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith(HIGHER_PACKAGES):
                    violations.append(f"{path.name}:{node.lineno}")
    assert violations == []


def test_legacy_contract_paths_preserve_object_identity() -> None:
    assert (
        legacy_context.TaskWorkContext
        is task_work_context.TaskWorkContext
    )
    assert (
        legacy_context.TaskWorkContextResolver
        is task_work_context.TaskWorkContextResolver
    )
    assert legacy_context.ProjectionState is task_work_context.ProjectionState
    assert legacy_grants.TaskGrant is task_grants.TaskGrant
    assert (
        legacy_grants.PreparedAuthorizationCommit
        is task_grants.PreparedAuthorizationCommit
    )
    assert legacy_redaction.TraceRedactor is TraceRedactor
    assert set(legacy_context.__all__) == set(task_work_context.__all__)
    assert set(legacy_grants.__all__) == set(task_grants.__all__)


def test_memory_redaction_compatibility_uses_shared_security_utility() -> None:
    assert redact("email me at user@example.com") == (
        "email me at [REDACTED:EMAIL]"
    )


def test_production_code_uses_leaf_contract_paths_directly() -> None:
    violations: list[str] = []
    sources = [
        ROOT / "backend/main.py",
        *(ROOT / "backend/deskpet").rglob("*.py"),
    ]
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT / "backend")
        module_parts = list(relative.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        package = ".".join(package_parts)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(LEGACY_CONTRACT_MODULES):
                        violations.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if node.level:
                    imported = importlib.util.resolve_name(
                        "." * node.level + imported,
                        package,
                    )
                if imported.startswith(LEGACY_CONTRACT_MODULES):
                    violations.append(f"{path}:{node.lineno}")
    assert violations == []
