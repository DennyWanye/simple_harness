#!/usr/bin/env python3
"""代码质量整治阶段 4.2：后端产品代码只许从 SDK 的公开模块导入。

公开模块清单由 SDK 自己声明：sdk/simple-harness-sdk/src/{simple_harness,agent_orchestrator}/public_api.py
的 PUBLIC_MODULES。`from 包 import 子模块` 算作用到子模块。测试与脚本目录不受限（可以用 SDK 的测试夹具）。
导入的名字是否在模块 `__all__` 里，由后端测试 tests/test_sdk_public_boundary.py 在装好的 SDK 上检查。

用法：python3 scripts/code_quality/sdk_boundary.py   # 有违例退出码 1
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SDK_SRC = REPO / "sdk/simple-harness-sdk/src"
PACKAGES = ("simple_harness", "agent_orchestrator")
SKIP = {"tests", "scripts", ".venv", "vendor", "__pycache__", "build", "dist", "dist-1", "userdata"}


def public_modules() -> set[str]:
    out: set[str] = set()
    for pkg in PACKAGES:
        tree = ast.parse((SDK_SRC / pkg / "public_api.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.AnnAssign)
                and getattr(node.target, "id", "") == "PUBLIC_MODULES"
            ):
                out |= set(ast.literal_eval(node.value))
    return out


def is_module(name: str) -> bool:
    """name 是不是 SDK 源码里的模块或包（区分大小写，macOS 文件系统不区分）。"""
    parent, _, leaf = name.rpartition(".")
    folder = SDK_SRC / parent.replace(".", "/")
    if not folder.is_dir():
        return False
    entries = set(os.listdir(folder))
    return leaf in entries or f"{leaf}.py" in entries


def used_modules(tree: ast.Module):
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.ImportFrom)
            and n.module
            and not n.level
            and n.module.split(".")[0] in PACKAGES
        ):
            for a in n.names:
                sub = f"{n.module}.{a.name}"
                yield n.lineno, sub if is_module(sub) else n.module
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in PACKAGES:
                    yield n.lineno, a.name


def main() -> int:
    public = public_modules()
    bad = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if set(rel.parts) & SKIP:
            continue
        for lineno, mod in used_modules(ast.parse(path.read_text(encoding="utf-8"))):
            if mod not in public:
                bad.append(f"{rel}:{lineno} 引用的 {mod} 不在 SDK 公开模块清单里")
    for b in bad:
        print("违例", b)
    print(
        f"SDK 公开边界：公开模块 {len(public)} 个，" + ("通过" if not bad else f"{len(bad)} 处违例")
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
