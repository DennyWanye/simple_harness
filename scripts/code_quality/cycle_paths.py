#!/usr/bin/env python3
"""代码质量整治阶段 5G：标了"循环依赖"的函数内导入，逐处列出成环路径（目标模块经顶层导入回到本模块的最短链）。

用法：python3 scripts/code_quality/cycle_paths.py [--tree sdk] [过滤子串]
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import deque
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_graph import build_graph, module_name, resolve  # noqa: E402
from inner_imports import BACKEND, SDK_SRC, inner_imports, tree_files  # noqa: E402


def shortest(graph: dict[str, set[str]], start: str, goal: str) -> list[str] | None:
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == goal:
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])  # type: ignore[arg-type]
            return path[::-1]
        for nxt in graph.get(cur, ()):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", choices=["backend", "sdk"], default="sdk")
    ap.add_argument("filter", nargs="?", default="")
    a = ap.parse_args()
    graph = build_graph()
    base = BACKEND if a.tree == "backend" else SDK_SRC
    for path in tree_files(a.tree):
        rel = path.relative_to(base).as_posix()
        if a.filter not in rel:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        try:
            me = module_name(path)
        except KeyError:
            continue
        for node, kind in inner_imports(path):
            if kind != "internal" or "按需导入：循环依赖" not in lines[node.lineno - 2]:
                continue
            for target in sorted(resolve(node, me, path)):
                chain = shortest(graph, target, me)
                if chain:
                    short = [c.replace("agent_orchestrator.", "ao.").replace("simple_harness.", "sh.") for c in chain]
                    print(f"{rel}:{node.lineno} {ast.unparse(node)}\n    {' → '.join(short)}")
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
