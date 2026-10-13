#!/usr/bin/env python3
"""代码质量整治：找出"打桩打在主入口、读取方已搬进 host 模块"的测试。

测试函数调用了 host 模块 M 的函数，同时对 main 打桩名字 Y，而 M 自己也按全局名读 Y，
这个打桩就打不到 M 里的读取方，测试看着能过、实际测的是真对象。
用法：python3 scripts/code_quality/hidden_patches.py <host 模块名> 测试文件...
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    mod, files = sys.argv[1], sys.argv[2:]
    tree = ast.parse((REPO / f"backend/host/{mod}.py").read_text(encoding="utf-8"))
    mod_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    hits = 0
    for f in files:
        test_tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        for fn in ast.walk(test_tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test"):
                continue
            uses_mod = any(
                isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == mod
                for n in ast.walk(fn)
            )
            if not uses_mod:
                continue
            for n in ast.walk(fn):
                if (
                    isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "setattr"
                    and len(n.args) >= 2
                    and isinstance(n.args[0], ast.Name)
                    and n.args[0].id == "main"
                    and isinstance(n.args[1], ast.Constant)
                    and n.args[1].value in mod_names
                ):
                    hits += 1
                    print(f"{f}:{n.lineno} {fn.name} 打桩 main.{n.args[1].value}，{mod} 也读它")
    print(f"可能打不到的打桩：{hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
