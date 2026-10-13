#!/usr/bin/env python3
"""代码质量整治：找测试里直接给主入口属性赋值、而该名字的读取方在 host 模块里的地方。

``main.X = v`` 只换主入口里的绑定；X 若是从 host 模块导入的，host 里的读取方看不见。
用法：python3 scripts/code_quality/main_assignments.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _host_readers() -> dict[str, list[str]]:
    readers: dict[str, list[str]] = {}
    for path in sorted((BACKEND / "host").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}:
            readers.setdefault(name, []).append(path.stem)
    return readers


def main() -> int:
    main_tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    main_defined = set()
    for st in main_tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            main_defined.add(st.name)
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            main_defined |= {t.id for t in targets if isinstance(t, ast.Name)}
    readers = _host_readers()
    hits = 0
    for path in sorted((BACKEND / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {"main"}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                aliases |= {a.asname for a in n.names if a.name == "main" and a.asname}
        for n in ast.walk(tree):
            targets = n.targets if isinstance(n, ast.Assign) else ([n.target] if isinstance(n, ast.AugAssign) else [])
            for t in targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id in aliases
                    and t.attr not in main_defined
                    and t.attr in readers
                ):
                    hits += 1
                    rel = path.relative_to(REPO)
                    print(f"{rel}:{n.lineno} 赋值 main.{t.attr}，读取方在：{', '.join(readers[t.attr])}")
    print(f"直接赋值可能打不到：{hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
