#!/usr/bin/env python3
"""代码质量整治阶段 6D：去掉"把同模块的模块级函数当参数原样传进来"的多余参数。

用法：python3 scripts/code_quality/drop_global_params.py <文件> <名字,名字,...>

对文件里每个函数：形参里有这些名字之一 X，且 X 是本模块的模块级函数，并且文件里每一处对该函数的
调用都把 X 原样传给它（位置实参 `X` 或关键字 `X=X`）——就删掉这个形参与所有调用处的实参。
函数体里的 X 改由模块级解析，指向同一个函数对象，行为不变。函数在别的文件里也被调用、或有调用处
传的不是 X 本身时，不改（报出来）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def callee_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if (
        isinstance(f, ast.Attribute)
        and isinstance(f.value, ast.Name)
        and f.value.id in ("self", "cls")
    ):
        return f.attr
    return None


def main() -> int:
    path = Path(sys.argv[1])
    wanted = {x.strip() for x in sys.argv[2].split(",") if x.strip()}
    for _round in range(50):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        top_funcs = {n.name for n in tree.body if isinstance(n, FUNCS)}
        globals_ok = wanted & top_funcs
        all_funcs = [n for n in ast.walk(tree) if isinstance(n, FUNCS)]
        by_name: dict[str, list] = {}
        for f in all_funcs:
            by_name.setdefault(f.name, []).append(f)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        edit = None
        for f in all_funcs:
            if len(by_name[f.name]) != 1:
                continue
            a = f.args
            params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
            for p in params:
                if p.arg not in globals_ok:
                    continue
                if any(
                    isinstance(n, ast.Name) and n.id == p.arg and isinstance(n.ctx, ast.Store)
                    for n in ast.walk(f)
                ):
                    continue
                sites = [c for c in calls if callee_name(c) == f.name]
                if not sites:
                    continue
                positional = [*a.posonlyargs, *a.args]
                pos_index = None
                if p in positional:
                    pos_index = positional.index(p) - (
                        1 if positional and positional[0].arg in ("self", "cls") else 0
                    )
                ok = True
                plan = []
                for c in sites:
                    kw = [k for k in c.keywords if k.arg == p.arg]
                    if kw and isinstance(kw[0].value, ast.Name) and kw[0].value.id == p.arg:
                        plan.append(kw[0])
                    elif (
                        pos_index is not None
                        and len(c.args) > pos_index
                        and isinstance(c.args[pos_index], ast.Name)
                        and c.args[pos_index].id == p.arg
                    ):
                        plan.append(c.args[pos_index])
                    else:
                        ok = False
                        break
                if ok:
                    edit = (f, p, plan)
                    break
            if edit:
                break
        if edit is None:
            break
        f, p, plan = edit
        # 从后往前删：调用处的实参、函数签名里的形参（连同逗号）
        spans = [(n.lineno, n.col_offset, n.end_lineno, n.end_col_offset) for n in plan]
        spans.append((p.lineno, p.col_offset, p.end_lineno, p.end_col_offset))
        lines = text.splitlines(keepends=True)
        for l0, c0, l1, c1 in sorted(spans, reverse=True):
            # 区间连同其后的逗号与空白一起删
            s0 = lines[l0 - 1].encode("utf-8")
            s1 = lines[l1 - 1].encode("utf-8")
            rest = s1[c1:]
            stripped = rest.lstrip(b" ")
            if stripped.startswith(b","):
                rest = stripped[1:].lstrip(b" ")
            merged = (s0[:c0] + rest).decode("utf-8")
            lines[l0 - 1 : l1] = [merged]
        new_text = "".join(lines)
        # 删掉形参后若只剩光秃的 *，一并去掉
        tree2 = ast.parse(new_text)
        f2 = next(n for n in ast.walk(tree2) if isinstance(n, FUNCS) and n.name == f.name)
        if not f2.args.kwonlyargs and f2.args.vararg is None:
            seg_lines = new_text.splitlines(keepends=True)
            head = "".join(seg_lines[f2.lineno - 1 : f2.body[0].lineno - 1])
            import re

            fixed = re.sub(r",\s*\*\s*(?=[,)])", "", head, count=1)
            fixed = re.sub(r"\(\s*\*\s*,\s*", "(", fixed, count=1)
            seg_lines[f2.lineno - 1 : f2.body[0].lineno - 1] = [fixed]
            new_text = "".join(seg_lines)
        ast.parse(new_text)
        path.write_text(new_text, encoding="utf-8")
        print(f"{f.name}：去掉参数 {p.arg}（{len(plan)} 处调用）")
    ruff = os.environ.get("RUFF") or str(
        REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
    )
    subprocess.run(
        [
            ruff,
            "format",
            "--force-exclude",
            "--config",
            str(REPO / "scripts/code_quality/ruff.toml"),
            "--quiet",
            str(path),
        ],
        check=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
