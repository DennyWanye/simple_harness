#!/usr/bin/env python3
"""代码质量整治阶段 5：拆长函数前看数据流——函数体里一段连续语句读了哪些前面的局部变量、写出哪些后面还要用的变量。

用法：python3 scripts/code_quality/block_flow.py <文件> <函数名> <起始行-结束行> [...]
每段打印：输入（段内读到、段前已赋值的局部名与参数）、输出（段内赋值、段后还读到的名字）、
段内有没有 return / yield / await（决定抽出的辅助函数要不要 async、要不要返回哨兵）。
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


def header_bindings(fn: ast.AST, start: int, end: int) -> set[str]:
    """包住这段的外层语句在头部绑定的名字：with ... as x、for x in ...、match 的 case 模式。
    这些语句本身跨过段，按"段前结束的语句"取不到它们绑定的名字。"""
    out: set[str] = set()
    for n in ast.walk(fn):
        if not (isinstance(n, ast.stmt) and n.lineno < start and end <= n.end_lineno):
            continue
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    out |= {x.id for x in ast.walk(item.optional_vars) if isinstance(x, ast.Name)}
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            out |= {x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)}
        elif isinstance(n, ast.Match):
            for case in n.cases:
                if case.body and case.body[0].lineno <= start and end <= case.body[-1].end_lineno:
                    out |= {x.name for x in ast.walk(case.pattern) if isinstance(x, (ast.MatchAs, ast.MatchStar)) and x.name}
                    out |= {x.rest for x in ast.walk(case.pattern) if isinstance(x, ast.MatchMapping) and x.rest}
    return out


def names(nodes: list[ast.AST], ctx: type) -> set[str]:
    out = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ctx):
                out.add(n.id)
            elif ctx is ast.Store and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(n.name)
            elif ctx is ast.Store and isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
    return out


def main() -> int:
    path, func, *ranges = sys.argv[1:]
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    )
    params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    stmts = [n for n in ast.walk(fn) if isinstance(n, ast.stmt) and n is not fn]
    for spec in ranges:
        start, end = (int(x) for x in spec.split("-"))
        inside = [s for s in stmts if start <= s.lineno and s.end_lineno <= end]
        top = [s for s in inside if not any(o is not s and o.lineno <= s.lineno and s.end_lineno <= o.end_lineno for o in inside)]
        before = [s for s in stmts if s.end_lineno < start]
        after = [s for s in stmts if s.lineno > end]
        assigned_before = names(before, ast.Store) | params | header_bindings(fn, start, end)
        # 段在 `except … as 名字` 里面时，名字由外层的异常处理绑定
        assigned_before |= {
            h.name
            for h in ast.walk(fn)
            if isinstance(h, ast.ExceptHandler) and h.name and h.lineno < start <= h.end_lineno
        }
        reads = names(top, ast.Load)
        writes = names(top, ast.Store)
        inputs = sorted(reads & assigned_before)
        outputs = sorted(writes & names(after, ast.Load))
        flags = sorted(
            {
                type(n).__name__
                for s in top
                for n in ast.walk(s)
                if isinstance(n, (ast.Return, ast.Yield, ast.Await))
            }
        )
        print(f"{spec}：{len(top)} 条顶层语句")
        print("  输入", inputs)
        print("  输出", outputs)
        print("  含", flags or "无 return/await")
    return 0


if __name__ == "__main__":
    sys.exit(main())
