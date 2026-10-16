#!/usr/bin/env python3
"""代码质量整治阶段 6E：模拟"去掉某条顶层导入边"能让多少处标了循环依赖的函数内导入不再成环。

用法：
    python3 scripts/code_quality/cycle_sim.py [--tree backend] [--top 30]
        # 对出现在成环路径上的每条顶层边，单独去掉后重算，按解掉的处数排序
    python3 scripts/code_quality/cycle_sim.py --drop 甲>乙,丙>丁
        # 同时去掉这几条边后，列出仍成环的各处
    python3 scripts/code_quality/cycle_sim.py --empty-init 包名,包名
        # 把这些包的 __init__ 当成不再导入任何子模块（只去掉包节点自己的出边）
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, deque
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_graph import build_graph, module_name, resolve  # noqa: E402
from inner_imports import BACKEND, SDK_SRC, inner_imports, tree_files  # noqa: E402


def tagged(tree: str):
    base = BACKEND if tree == "backend" else SDK_SRC
    out = []
    for path in tree_files(tree):
        lines = path.read_text(encoding="utf-8").splitlines()
        try:
            me = module_name(path)
        except KeyError:
            continue
        for node, kind in inner_imports(path):
            if kind != "internal" or "按需导入：循环依赖" not in lines[node.lineno - 2]:
                continue
            out.append((f"{path.relative_to(base).as_posix()}:{node.lineno}", me, resolve(node, me, path)))
    return out


def path_between(graph, start, goal):
    prev = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur == goal:
            chain = [cur]
            while prev[chain[-1]] is not None:
                chain.append(prev[chain[-1]])
            return chain[::-1]
        for nxt in graph.get(cur, ()):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    return None


def cyclic(graph, items):
    return [(loc, me, ts) for loc, me, ts in items if any(path_between(graph, t, me) for t in ts)]


def without(graph, edges):
    g = {k: set(v) for k, v in graph.items()}
    for a, b in edges:
        g.get(a, set()).discard(b)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", choices=["backend", "sdk"], default="backend")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--drop", default="")
    ap.add_argument("--empty-init", default="")
    a = ap.parse_args()
    graph = build_graph()
    items = tagged(a.tree)
    drops = [tuple(x.split(">")) for x in a.drop.split(",") if x]
    for pkg in [x for x in a.empty_init.split(",") if x]:
        drops += [(pkg, t) for t in graph.get(pkg, ()) if t.startswith(pkg + ".")]
    base = cyclic(graph, items)
    if drops or a.empty_init:
        g = without(graph, drops)
        left = cyclic(g, base)
        print(f"标注 {len(items)} 处，现成环 {len(base)}；去掉 {len(drops)} 条边后仍成环 {len(left)}")
        for loc, me, ts in left:
            for t in sorted(ts):
                chain = path_between(g, t, me)
                if chain:
                    print(f"  {loc}\n      {' → '.join(chain)}")
                    break
        return 0
    edges = Counter()
    for _loc, me, ts in base:
        for t in ts:
            chain = path_between(graph, t, me)
            if chain:
                edges.update(zip(chain, chain[1:]))
                break
    gains = []
    for e in edges:
        g = without(graph, [e])
        gains.append((len(base) - len(cyclic(g, base)), e))
    gains.sort(reverse=True)
    print(f"标注 {len(items)} 处，现成环 {len(base)}；单独去掉一条边能解掉的处数：")
    for n, (u, v) in gains[: a.top]:
        if n:
            print(f"  {n:3d}  {u} > {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
