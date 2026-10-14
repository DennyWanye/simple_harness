"""后端产品代码只用 SDK 声明的公开接口（代码质量整治阶段 4.2）。

在装好的 SDK 上检查：产品代码（不含测试与脚本）每一处 `from <SDK 模块> import 名字`，模块在 SDK 的
public_api.PUBLIC_MODULES 里，名字在该模块的 `__all__` 里（或是同样公开的子模块）。
静态的模块清单检查另见 scripts/code_quality/sdk_boundary.py（进 CI）。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

from agent_orchestrator.public_api import PUBLIC_MODULES as ORCHESTRATOR_PUBLIC
from simple_harness.public_api import PUBLIC_MODULES as HARNESS_PUBLIC

BACKEND = Path(__file__).resolve().parents[1]
PACKAGES = ("simple_harness", "agent_orchestrator")
SKIP = {"tests", "scripts", ".venv", "vendor", "__pycache__", "build", "dist", "dist-1", "userdata"}
PUBLIC = set(HARNESS_PUBLIC) | set(ORCHESTRATOR_PUBLIC)


def _sdk_imports():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if set(rel.parts) & SKIP:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module.split(".")[0] in PACKAGES:
                    for alias in node.names:
                        yield f"{rel}:{node.lineno}", node.module, alias.name


def _is_submodule(module: str, name: str) -> bool:
    if not hasattr(importlib.import_module(module), "__path__"):
        return False
    return importlib.util.find_spec(f"{module}.{name}") is not None


def test_product_code_imports_only_public_sdk_names() -> None:
    bad = []
    for where, module, name in _sdk_imports():
        if _is_submodule(module, name):
            if f"{module}.{name}" not in PUBLIC:
                bad.append(f"{where} 子模块 {module}.{name} 不公开")
            continue
        if module not in PUBLIC:
            bad.append(f"{where} 模块 {module} 不公开")
            continue
        exported = getattr(importlib.import_module(module), "__all__", ())
        if name not in exported:
            bad.append(f"{where} {module}.{name} 不在 __all__ 里")
    assert bad == []
