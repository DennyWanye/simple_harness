# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from deskpet.execution.uow_ports import (
    AdmissionUnitOfWork,
    ChildRunUnitOfWork,
    DelegateFactoryUnitOfWork,
    DriverRuntimeUnitOfWork,
    HarnessBootstrapUnitOfWork,
    HarnessDeliveryUnitOfWork,
    KernelUnitOfWork,
    ProductProjectionUnitOfWork,
    ReActUnitOfWork,
    ReconciliationUnitOfWork,
    TeamMigrationUnitOfWork,
    ToolExecutionUnitOfWork,
    UserContinuationUnitOfWork,
    WorkflowDriverUnitOfWork,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


ROOT = Path(__file__).resolve().parents[3]
CORE_MODULES = (
    ("backend/deskpet/harness/kernel.py", "_uow", KernelUnitOfWork),
    (
        "backend/deskpet/harness/runtime.py",
        "_uow",
        DriverRuntimeUnitOfWork,
    ),
    (
        "backend/deskpet/harness/tool_executor.py",
        "_uow",
        ToolExecutionUnitOfWork,
    ),
    (
        "backend/deskpet/harness/drivers/react.py",
        "_uow",
        ReActUnitOfWork,
    ),
    (
        "backend/deskpet/harness/drivers/workflow.py",
        "_unit_of_work",
        WorkflowDriverUnitOfWork,
    ),
    (
        "backend/deskpet/harness/child_runs.py",
        "_store",
        ChildRunUnitOfWork,
    ),
    (
        "backend/deskpet/harness/user_continuations.py",
        "_uow",
        UserContinuationUnitOfWork,
    ),
    (
        "backend/deskpet/harness/reconciler.py",
        "_uow",
        ReconciliationUnitOfWork,
    ),
    (
        "backend/deskpet/harness/admission_launch.py",
        "uow",
        AdmissionUnitOfWork,
    ),
    (
        "backend/deskpet/harness/projector.py",
        "_store",
        HarnessDeliveryUnitOfWork,
    ),
    (
        "backend/deskpet/harness/adapters/subagent_registry.py",
        "_uow",
        DelegateFactoryUnitOfWork,
    ),
    (
        "backend/deskpet/harness/adapters/team.py",
        "_uow",
        TeamMigrationUnitOfWork,
    ),
)
PORT_VIEWS = (
    AdmissionUnitOfWork,
    ChildRunUnitOfWork,
    DelegateFactoryUnitOfWork,
    DriverRuntimeUnitOfWork,
    HarnessBootstrapUnitOfWork,
    HarnessDeliveryUnitOfWork,
    KernelUnitOfWork,
    ProductProjectionUnitOfWork,
    ReActUnitOfWork,
    ReconciliationUnitOfWork,
    TeamMigrationUnitOfWork,
    ToolExecutionUnitOfWork,
    UserContinuationUnitOfWork,
    WorkflowDriverUnitOfWork,
)


def _declared_methods(port: type) -> set[str]:
    return {
        name
        for name, value in port.__dict__.items()
        if not name.startswith("_") and inspect.isfunction(value)
    }


def test_all_small_uow_views_are_implemented_by_the_same_sqlite_uow() -> None:
    for port in PORT_VIEWS:
        missing = sorted(
            name
            for name in _declared_methods(port)
            if not callable(getattr(SqliteExecutionUnitOfWork, name, None))
        )
        assert missing == [], f"{port.__name__} missing: {missing}"


def _call_shape(method: object) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (parameter.name, parameter.kind, parameter.default)
        for parameter in inspect.signature(method).parameters.values()
    )


def test_small_uow_views_match_the_real_sqlite_call_shapes() -> None:
    for port in PORT_VIEWS:
        for name in _declared_methods(port):
            view_method = getattr(port, name)
            implementation = getattr(SqliteExecutionUnitOfWork, name)
            assert _call_shape(view_method) == _call_shape(
                implementation
            ), f"{port.__name__}.{name}"


def test_uow_views_stay_small() -> None:
    for port in PORT_VIEWS:
        methods = _declared_methods(port)
        assert len(methods) <= 25, (port.__name__, sorted(methods))


def test_harness_core_does_not_import_the_compatibility_aggregate() -> None:
    violations: list[str] = []
    for relative, _attribute, _port in CORE_MODULES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "deskpet.execution.ports":
                continue
            if any(alias.name == "ExecutionUnitOfWork" for alias in node.names):
                violations.append(relative)
    assert violations == []


def test_each_harness_component_declares_every_uow_method_it_calls() -> None:
    violations: list[str] = []
    for relative, attribute, port in CORE_MODULES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared = _declared_methods(port)
        used: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            if not isinstance(owner, ast.Attribute):
                continue
            if (
                isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == attribute
            ):
                used.add(node.attr)
        missing = sorted(used - declared)
        if missing:
            violations.append(f"{relative}: {missing}")
    assert violations == []
