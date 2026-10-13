#!/usr/bin/env python3
"""代码质量整治：给定一组顶层名字，算出要一起搬的定义闭包与行范围（配合 move_block.py）。

用法：python3 scripts/code_quality/group_ranges.py <源文件> 名字1,名字2,... [--no-closure]
输出：闭包里的名字、每个名字依赖的、留在源文件里的其他定义，以及 move_block 可用的行范围串。
只把"函数/类/常量"拉进闭包；被依赖的模块级可变对象会单独列出，需人工决定放哪。
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])


def main() -> int:
    src_path = Path(sys.argv[1])
    seeds = set(sys.argv[2].split(","))
    closure_on = "--no-closure" not in sys.argv
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    owner: dict[str, ast.stmt] = {}
    for st in tree.body:
        names: set[str] = set()
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = {st.name}
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            for x in ast.walk(st):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    names.add(x.id)
        for n in names:
            owner.setdefault(n, st)

    def uses(st: ast.stmt) -> set[str]:
        return {x.id for x in ast.walk(st) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)} & set(owner)

    chosen = set(seeds)
    changed = True
    while changed and closure_on:
        changed = False
        for n in list(chosen):
            for d in uses(owner[n]):
                if d not in chosen and isinstance(owner[d], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    chosen.add(d)
                    changed = True
    stmts = sorted({id(owner[n]): owner[n] for n in chosen}.values(), key=lambda s: s.lineno)
    outside: dict[str, set[str]] = {}
    for st in stmts:
        for d in uses(st):
            if owner[d] not in stmts:
                outside.setdefault(d, set()).add(getattr(st, "name", "?"))
    def start(st: ast.stmt) -> int:
        s = st.lineno
        if getattr(st, "decorator_list", None):
            s = min(d.lineno for d in st.decorator_list)
        return s
    ranges = []
    for st in stmts:
        s, e = start(st), st.end_lineno
        if ranges and s <= ranges[-1][1] + 1 + sum(1 for i in range(ranges[-1][1], s - 1) if not lines[i].strip() or lines[i].lstrip().startswith("#")) and \
                all((not lines[i].strip()) or lines[i].lstrip().startswith("#") for i in range(ranges[-1][1], s - 1)):
            ranges[-1][1] = e
        else:
            ranges.append([s, e])
    print("闭包：", ", ".join(sorted(chosen)))
    print("共", sum(e - s + 1 for s, e in ranges), "行")
    for d, who in sorted(outside.items()):
        kind = type(owner[d]).__name__
        print(f"  依赖留在源文件的 {d}（{kind}，第 {owner[d].lineno} 行）← {', '.join(sorted(who))}")
    print("范围：" + ",".join(f"{s}-{e}" for s, e in ranges))
    return 0


if __name__ == "__main__":
    sys.exit(main())
