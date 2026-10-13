#!/usr/bin/env python3
"""代码质量整治：找"从 host 模块按名字导入、而该名字在源模块里只在某个分支里定义"的地方。

拆主入口前，这类名字是主入口自己的全局，读不到时只在用到的那一行报错（往往在 try 里被吞掉、
功能降级）；拆开后变成导入期 `from host.x import name`，名字不存在会让整个模块导入失败。
用法：python3 scripts/code_quality/conditional_imports.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _bound(stmt: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names}
    targets = []
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) and getattr(stmt, "value", None) is not None:
        targets = [stmt.target]
    for t in targets:
        for n in ast.walk(t):
            if isinstance(n, ast.Name):
                out.add(n.id)
    return out


def always(body: list[ast.stmt]) -> set[str]:
    """顺序执行这段语句后一定已定义的名字。"""
    out: set[str] = set()
    for st in body:
        if isinstance(st, ast.If):
            out |= always(st.body) & always(st.orelse) if st.orelse else set()
        elif isinstance(st, ast.Try):
            body_names = always(st.body) | always(st.orelse)
            handlers = [always(h.body) for h in st.handlers]
            ok = body_names
            for h in handlers:
                ok &= h
            out |= ok | always(st.finalbody)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            out |= always(st.body)
        else:
            out |= _bound(st)
    return out


def anywhere(body: list[ast.stmt]) -> set[str]:
    out: set[str] = set()
    for st in body:
        out |= _bound(st)
        for field in ("body", "orelse", "finalbody"):
            out |= anywhere(getattr(st, field, []) or [])
        for h in getattr(st, "handlers", []) or []:
            out |= anywhere(h.body)
    return out


def main() -> int:
    conditional: dict[str, set[str]] = {}
    for path in (BACKEND / "host").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        conditional[f"host.{path.stem}"] = anywhere(tree.body) - always(tree.body)
    hits = 0
    for path in [BACKEND / "main.py", *sorted((BACKEND / "host").glob("*.py"))]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in conditional:
                for alias in node.names:
                    if alias.name in conditional[node.module]:
                        hits += 1
                        print(f"{path.relative_to(REPO)}:{node.lineno} 导入期导入 {node.module}.{alias.name}，它只在分支里定义")
    print(f"条件定义名字的导入期导入：{hits}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
