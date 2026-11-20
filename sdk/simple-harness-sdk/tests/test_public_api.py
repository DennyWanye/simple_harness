"""The SDK's declared public interface (代码质量整治阶段 4.1).

Every module listed in a package's ``public_api.PUBLIC_MODULES`` must import, declare ``__all__``
and actually provide every name in it; internal modules (sqlite submodules, testing, underscore
modules) must never be listed.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from agent_orchestrator.public_api import PUBLIC_MODULES as ORCHESTRATOR_PUBLIC
from simple_harness.public_api import PUBLIC_MODULES as HARNESS_PUBLIC

ALL_PUBLIC = (*HARNESS_PUBLIC, *ORCHESTRATOR_PUBLIC)


def _internal(module: str) -> bool:
    parts = module.split(".")
    return (
        module.startswith("simple_harness.execution.sqlite.")
        or "testing" in parts
        or any(p.startswith("_") for p in parts)
    )


def test_lists_are_sorted_unique_and_stay_in_their_own_package() -> None:
    for package, modules in (
        ("simple_harness", HARNESS_PUBLIC),
        ("agent_orchestrator", ORCHESTRATOR_PUBLIC),
    ):
        assert list(modules) == sorted(set(modules))
        assert all(m == package or m.startswith(package + ".") for m in modules)


def test_internal_modules_are_never_public() -> None:
    assert [m for m in ALL_PUBLIC if _internal(m)] == []


@pytest.mark.parametrize("module_name", ALL_PUBLIC)
def test_public_module_declares_and_provides_all(module_name: str) -> None:
    module = importlib.import_module(module_name)
    declared = getattr(module, "__all__", None)
    assert declared is not None, f"{module_name} has no __all__"
    assert len(declared) == len(set(declared)), f"{module_name}.__all__ has duplicates"
    missing = [name for name in declared if not hasattr(module, name)]
    assert missing == [], f"{module_name}.__all__ names that do not exist: {missing}"


def test_host_integration_doc_imports_only_public_modules() -> None:
    """接入说明叫宿主导入的模块必须都在公开清单里（issue #42：4b 节的连接器合同曾经不在）。"""

    doc = Path(__file__).resolve().parents[1] / "docs" / "orchestration-host.md"
    imported = set(
        re.findall(
            r"^\s*(?:from|import)\s+(agent_orchestrator[\w.]*)", doc.read_text("utf-8"), re.M
        )
    )
    assert "agent_orchestrator.runtime.connectors" in imported  # 文档确实教了写连接器
    assert sorted(imported - set(ORCHESTRATOR_PUBLIC)) == []
