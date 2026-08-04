# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Executable index for the cross-module Companion failure matrix.

The detailed fault fixtures live beside the authority they exercise.  This
module deliberately invokes those tests instead of maintaining a second fake
implementation of cutover, provider, runtime, or lease behavior.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = Path(__file__).with_name("fault_matrix.json")
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
CASES = MATRIX["cases"]
SUPPORTED_RUNNERS = {
    "pytest_direct",
    "pytest_source_contract",
    "vitest_source_contract",
}
_MODULES: dict[Path, ModuleType] = {}


def _split_ref(test_ref: str) -> tuple[Path, str]:
    relative_path, separator, test_name = test_ref.partition("::")
    assert separator and relative_path and test_name, test_ref
    path = (REPO_ROOT / relative_path).resolve()
    assert path.is_relative_to(REPO_ROOT)
    assert path.is_file(), test_ref
    return path, test_name


def _load_module(path: Path) -> ModuleType:
    cached = _MODULES.get(path)
    if cached is not None:
        return cached
    module_name = f"_companion_fault_matrix_{path.stem}_{len(_MODULES)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    _MODULES[path] = module
    return module


def _pytest_source(path: Path, test_name: str) -> tuple[Any, str]:
    function = getattr(_load_module(path), test_name, None)
    assert callable(function), f"missing pytest target: {path}::{test_name}"
    return function, inspect.getsource(function)


def _source_contract(path: Path, test_name: str, runner: str) -> str:
    if runner == "pytest_source_contract":
        _, source = _pytest_source(path, test_name)
        return source
    source = path.read_text(encoding="utf-8")
    assert f'it("{test_name}"' in source, (
        f"missing Vitest target: {path}::{test_name}"
    )
    return source


async def _invoke_pytest_target(
    function: Any,
    argument_set: dict[str, Any],
    request: pytest.FixtureRequest,
    invocation_ordinal: int,
) -> None:
    kwargs: dict[str, Any] = {}
    for name in inspect.signature(function).parameters:
        if name in argument_set:
            kwargs[name] = argument_set[name]
        else:
            kwargs[name] = request.getfixturevalue(name)
        if name == "tmp_path":
            kwargs[name] = kwargs[name] / f"invocation-{invocation_ordinal}"
            kwargs[name].mkdir()
    unexpected = set(argument_set) - set(inspect.signature(function).parameters)
    assert not unexpected, f"unexpected matrix arguments: {sorted(unexpected)}"
    result = function(**kwargs)
    if inspect.isawaitable(result):
        await result


def test_fault_matrix_schema_and_required_coverage() -> None:
    assert MATRIX["schema_version"] == 1
    required_items = set(MATRIX["required_plan_items"])
    assert required_items == {1, 2, 6, 7, 9, 10, 11, 13, 14, 15, 16, 18, 19, 20, 21}
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert all(case["runner"] in SUPPORTED_RUNNERS for case in CASES)
    assert all(case["argument_sets"] for case in CASES)
    assert set().union(*(set(case["plan_items"]) for case in CASES)) == required_items
    observed_risks = set().union(*(set(case["risk_tags"]) for case in CASES))
    assert set(MATRIX["required_risk_tags"]) <= observed_risks


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_fault_matrix_case(
    case: dict[str, Any],
    request: pytest.FixtureRequest,
) -> None:
    path, test_name = _split_ref(case["test_ref"])
    assert case["boundary"].strip()
    assert case["plan_items"]
    assert case["risk_tags"]
    assert case["evidence_markers"]

    if case["runner"] == "pytest_direct":
        function, source = _pytest_source(path, test_name)
        assert "assert" in source or "pytest.raises" in source
        for invocation_ordinal, argument_set in enumerate(case["argument_sets"]):
            await _invoke_pytest_target(
                function,
                argument_set,
                request,
                invocation_ordinal,
            )
    else:
        source = _source_contract(path, test_name, case["runner"])
        assert "assert" in source or "expect(" in source

    for marker in case["evidence_markers"]:
        assert marker in source, (
            f"{case['id']} no longer proves evidence marker {marker!r}"
        )
