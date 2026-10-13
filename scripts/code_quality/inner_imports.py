#!/usr/bin/env python3
"""代码质量整治阶段 3：函数内导入清单与分类。

按语句统计函数（含方法、内层函数）里的 import，分类：
- optional：被 try/except ImportError（或 Exception）包着，属可选依赖
- type_checking：在 `if TYPE_CHECKING:` 下
- stdlib：标准库
- internal：项目内部模块（后端顶层包、SDK 包）
- third_party：其它
用法：
  python3 scripts/code_quality/inner_imports.py summary            # 各树各类计数
  python3 scripts/code_quality/inner_imports.py list --tree backend --kind internal
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SDK_SRC = REPO / "sdk/simple-harness-sdk/src"
STDLIB = set(sys.stdlib_module_names)


def _internal_roots() -> dict[str, set[str]]:
    backend = {p.name for p in BACKEND.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    backend |= {p.stem for p in BACKEND.glob("*.py")}
    sdk = {p.name for p in SDK_SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return {"backend": backend, "sdk": sdk}


ROOTS = _internal_roots()
ALL_INTERNAL = ROOTS["backend"] | ROOTS["sdk"]


def tree_files(tree: str) -> list[Path]:
    if tree == "backend":
        out = []
        for p in BACKEND.rglob("*.py"):
            rel = p.relative_to(BACKEND).parts
            if rel[0] in {".venv", "tests", "vendor", "build", "dist"} or "site-packages" in rel:
                continue
            out.append(p)
        return sorted(out)
    return sorted(SDK_SRC.rglob("*.py"))


def classify(node: ast.AST, stack: list[ast.AST]) -> str:
    for anc in reversed(stack):
        if isinstance(anc, ast.Try):
            for h in anc.handlers:
                names = []
                if h.type is None:
                    names = ["*"]
                elif isinstance(h.type, ast.Tuple):
                    names = [getattr(e, "id", getattr(e, "attr", "")) for e in h.type.elts]
                else:
                    names = [getattr(h.type, "id", getattr(h.type, "attr", ""))]
                if any(n in {"ImportError", "ModuleNotFoundError"} for n in names):
                    return "optional"
        if isinstance(anc, ast.If) and "TYPE_CHECKING" in ast.unparse(anc.test):
            return "type_checking"
    if isinstance(node, ast.ImportFrom) and node.level > 0:
        return "internal"
    mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
    root = (mod or "").split(".")[0]
    if root in STDLIB:
        return "stdlib"
    if root in ALL_INTERNAL:
        return "internal"
    return "third_party"


def inner_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []

    def walk(node: ast.AST, stack: list[ast.AST], in_func: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)) and in_func:
                out.append((child, classify(child, stack + [node])))
            inner = in_func or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            walk(child, stack + [node], inner)

    walk(tree, [], False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "list"])
    ap.add_argument("--tree", choices=["backend", "sdk"], default="backend")
    ap.add_argument("--kind", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "summary":
        result = {}
        for tree in ("backend", "sdk"):
            c: Counter = Counter()
            for p in tree_files(tree):
                for _node, kind in inner_imports(p):
                    c[kind] += 1
            result[tree] = dict(c)
            print(f"{tree}: 合计 {sum(c.values())}  " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    base = BACKEND if a.tree == "backend" else SDK_SRC
    for p in tree_files(a.tree):
        for node, kind in inner_imports(p):
            if a.kind and kind != a.kind:
                continue
            print(f"{p.relative_to(base)}:{node.lineno} {kind} {ast.unparse(node)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
