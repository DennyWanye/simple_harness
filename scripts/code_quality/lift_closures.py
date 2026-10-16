#!/usr/bin/env python3
"""代码质量整治阶段 6D：把函数里不读外层变量的内层辅助函数（或类）原样提成模块级私有定义。

用法：
    python3 scripts/code_quality/lift_closures.py <文件> <函数名> <内层函数名,内层函数名,...>

条件（不满足就拒绝）：
- 内层函数直接定义在该函数的函数体顶层（不在 if/try/循环里），没有装饰器；
- 它读到的名字只能是模块级名字、内置名字、同批一起提出去的内层函数，或它自己的参数与局部变量
  （即不读外层函数的任何局部变量，也没有 nonlocal/global）；
- 模块里没有同名的顶层定义；外层函数里除了这句 def 以外不再给这个名字赋值。
做法：函数体原文搬到模块级（放在外层函数所在的顶层语句之前），缩进左移，多行字符串内容不变；
外层函数里删掉这句 def。外层函数里对它的引用照旧，名字改由模块级解析，指向同一份代码。
"""

from __future__ import annotations

import argparse
import ast
import builtins
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reindent import reindent, string_interior_lines  # noqa: E402
from scope_names import names  # noqa: E402

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def module_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for n in tree.body:
        if isinstance(n, (*FUNCS, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            out |= {x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)}
        elif isinstance(n, (ast.If, ast.Try)):
            for x in ast.walk(n):
                if isinstance(x, (ast.Import, ast.ImportFrom)):
                    out |= {(a.asname or a.name).split(".")[0] for a in x.names}
                elif isinstance(x, (*FUNCS, ast.ClassDef)):
                    out.add(x.name)
                elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    out.add(x.id)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("function")
    ap.add_argument("names")
    a = ap.parse_args()
    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    fn = max(
        (n for n in ast.walk(tree) if isinstance(n, FUNCS) and n.name == a.function),
        key=lambda n: n.end_lineno - n.lineno,
    )
    wanted = [x.strip() for x in a.names.split(",") if x.strip()]
    defs = {
        n.name: n
        for n in fn.body
        if isinstance(n, (*FUNCS, ast.ClassDef)) and n.name in wanted
    }
    if set(defs) != set(wanted):
        sys.exit(f"不在函数体顶层的：{sorted(set(wanted) - set(defs))}")
    top_names = module_names(tree)
    clash = sorted(set(wanted) & top_names)
    if clash:
        sys.exit(f"模块里已有同名定义：{clash}")
    host_locals = names(fn.body, ast.Store) | {
        x.arg
        for x in (
            *fn.args.posonlyargs,
            *fn.args.args,
            *fn.args.kwonlyargs,
            *(y for y in (fn.args.vararg, fn.args.kwarg) if y),
        )
    }
    allowed = top_names | set(dir(builtins)) | set(wanted)
    for name, d in defs.items():
        if d.decorator_list:
            sys.exit(f"{name} 有装饰器")
        if any(isinstance(x, (ast.Nonlocal, ast.Global)) for x in ast.walk(d)):
            sys.exit(f"{name} 里有 nonlocal/global")
        free = names([d], ast.Load)
        bad = sorted((free & (host_locals - set(wanted))) | (free - allowed - host_locals))
        if bad:
            sys.exit(f"{name} 读了外层变量或未知名字：{bad}")
        rebinds = [
            x
            for x in ast.walk(fn)
            if isinstance(x, ast.Name) and x.id == name and isinstance(x.ctx, ast.Store)
        ]
        if rebinds:
            sys.exit(f"外层函数里还给 {name} 赋值")
    interior = string_interior_lines(text)
    owner = next(n for n in tree.body if n.lineno <= fn.lineno <= n.end_lineno)
    owner_start = (
        owner.decorator_list[0].lineno if getattr(owner, "decorator_list", None) else owner.lineno
    )
    lifted = []
    for d in sorted(defs.values(), key=lambda n: n.lineno):
        seg = "".join(lines[d.lineno - 1 : d.end_lineno])
        lifted.append(reindent(seg, "", d.lineno, interior))
    out = list(lines)
    for d in sorted(defs.values(), key=lambda n: -n.lineno):
        end = d.end_lineno
        if end < len(out) and not out[end].strip():
            end += 1  # 连带后面的空行
        del out[d.lineno - 1 : end]
    block = "".join(x + "\n\n" for x in lifted)
    out[owner_start - 1 : owner_start - 1] = [block]
    new_text = "".join(out)
    ast.parse(new_text)
    path.write_text(new_text, encoding="utf-8")
    ruff = os.environ.get("RUFF") or str(
        REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
    )
    subprocess.run(
        [
            ruff,
            "format",
            "--config",
            str(REPO / "scripts/code_quality/ruff.toml"),
            "--quiet",
            str(path),
        ],
        check=False,
    )
    print(f"提出 {len(defs)} 个内层函数到模块级")
    return 0


if __name__ == "__main__":
    sys.exit(main())
