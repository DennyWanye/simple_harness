#!/usr/bin/env python3
"""代码质量整治阶段 3：模块依赖图、循环与"函数内导入能否上提"判断。

图只算模块顶层（不在函数里）的导入，节点是项目内部模块（后端树、SDK 源码树）。
导入 a.b.c 时，a、a.b 两个包也会被导入，同样计为依赖。

用法：
  python3 scripts/code_quality/import_graph.py cycles                 # 列出现有循环（强连通分量）
  python3 scripts/code_quality/import_graph.py liftable [--tree sdk]  # 每处内部函数内导入：可上提 / 会成环
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inner_imports import BACKEND, SDK_SRC, inner_imports, tree_files  # noqa: E402


def module_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for base, tree in ((BACKEND, "backend"), (SDK_SRC, "sdk")):
        for p in tree_files(tree):
            rel = p.relative_to(base).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                idx.setdefault(".".join(parts), p)
    return idx


INDEX = module_index()


def module_name(path: Path) -> str:
    for name, p in INDEX.items():
        if p == path:
            return name
    raise KeyError(path)


def _package_of(mod: str, path: Path) -> str:
    return mod if path.name == "__init__.py" else mod.rpartition(".")[0]


def resolve(node: ast.Import | ast.ImportFrom, importer: str, importer_path: Path) -> set[str]:
    """一条导入语句会让哪些内部模块被导入（含沿途的包）。"""
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        bases = [a.name for a in node.names]
        subs: list[str] = []
    else:
        if node.level:
            pkg = _package_of(importer, importer_path)
            for _ in range(node.level - 1):
                pkg = pkg.rpartition(".")[0]
            base = f"{pkg}.{node.module}" if node.module else pkg
        else:
            base = node.module or ""
        bases = [base]
        subs = [f"{base}.{a.name}" for a in node.names]
    for b in bases + subs:
        parts = b.split(".")
        for i in range(1, len(parts) + 1):
            cand = ".".join(parts[:i])
            if cand in INDEX:
                targets.add(cand)
    targets.discard(importer)
    # 导入方自己的上层包在它之前就已加载，"沿途经过"不算新依赖；直接从上层包取名字才算
    direct = set(bases) if isinstance(node, ast.ImportFrom) and any(s not in INDEX for s in subs) else set()
    return {t for t in targets if not importer.startswith(t + ".") or t in direct}


def with_ancestors(mods) -> set[str]:
    """模块连同它们的上层包（导入模块时上层包先被加载）。"""
    out = set()
    for m in mods:
        parts = m.split(".")
        out |= {".".join(parts[:i]) for i in range(1, len(parts) + 1)} & INDEX.keys()
    return out


def top_level_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []

    def walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.If) and "TYPE_CHECKING" in ast.unparse(child.test):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                out.append(child)
            walk(child)

    walk(tree)
    return out


def build_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for name, path in INDEX.items():
        graph[name]
        for node in top_level_imports(path):
            graph[name] |= resolve(node, name, path)
    return graph


def sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    counter = [0]
    sys.setrecursionlimit(100000)

    def strong(v: str):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in list(graph):
        if v not in index:
            strong(v)
    return out


def reachable(graph: dict[str, set[str]], start: str, goal: str) -> bool:
    seen = {start}
    todo = [start]
    while todo:
        v = todo.pop()
        if v == goal:
            return True
        for w in graph.get(v, ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cycles", "liftable"])
    ap.add_argument("--tree", choices=["backend", "sdk"], default="backend")
    a = ap.parse_args()
    graph = build_graph()
    if a.cmd == "cycles":
        comps = sccs(graph)
        print(f"模块 {len(graph)} 个，循环（强连通分量）{len(comps)} 个")
        for c in sorted(comps, key=len, reverse=True):
            print(f"  [{len(c)}] " + ", ".join(c[:12]) + (" …" if len(c) > 12 else ""))
        return 0
    base = BACKEND if a.tree == "backend" else SDK_SRC
    ok = cyc = 0
    for path in tree_files(a.tree):
        try:
            me = module_name(path)
        except KeyError:
            continue
        for node, kind in inner_imports(path):
            if kind != "internal":
                continue
            targets = resolve(node, me, path)
            bad = sorted(t for t in targets if reachable(graph, t, me))
            if bad:
                cyc += 1
                print(f"成环 {path.relative_to(base)}:{node.lineno} {ast.unparse(node)}  ← {bad[0]} 已依赖本模块")
            else:
                ok += 1
    print(f"内部函数内导入：可上提 {ok}，会成环 {cyc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
