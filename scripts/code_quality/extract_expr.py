#!/usr/bin/env python3
"""代码质量整治阶段 6D：把函数里一个大表达式（例如返回字典里某个键的值）抽成函数，原位置换成调用。

用法：
    python3 scripts/code_quality/extract_expr.py <文件> <函数名> <行:列> <新函数名> [--method]
        # 行:列 是表达式开头的位置（ast 的 lineno、col_offset）
        # --method：生成同类的方法（表达式里用到 self 时必须加）；不加时生成模块级函数

规则：
- 表达式不能在推导式、lambda 里面（那里的名字作用域不同），不能含 yield。
- 参数 = 表达式里读到的、属于原函数作用域的名字（按作用域判断，推导式变量不算），按名字排序；
  这些名字在该语句处必须一定已赋值。参数多于 4 个时第一个之后仅限关键字。
- 表达式里有 await → 新函数是 async，调用处加 await。
- 新函数体是 `return (表达式原文)`：续行原样保留（括号里缩进随意），多行字符串内容不变；
  收尾跑 ruff 格式化。求值时机与顺序不变（就在原位置调用）。
"""

from __future__ import annotations

import argparse
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scope_names import defined_before, names  # noqa: E402

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = (
    *FUNCS,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def own_walk(root):
    yield root
    stack = list(ast.iter_child_nodes(root))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (*FUNCS, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("function")
    ap.add_argument("at")
    ap.add_argument("name")
    ap.add_argument("--method", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    line, col = (int(x) for x in a.at.split(":"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, FUNCS) and n.name == a.function and n.lineno <= line <= n.end_lineno
    )
    if any(isinstance(n, FUNCS) and n.name == a.name for n in ast.walk(tree)):
        sys.exit(f"名字 {a.name} 已经有了")
    # 找表达式与它所在的语句；途中不许穿过推导式、lambda
    target = stmt = None

    def find(node, cur_stmt, blocked):
        nonlocal target, stmt
        for c in ast.iter_child_nodes(node):
            if (
                isinstance(c, ast.expr)
                and c.lineno == line
                and c.col_offset == col
                and target is None
            ):
                if blocked:
                    sys.exit("表达式在推导式或 lambda 里面")
                # 同一位置可能有多层表达式（例如 a.b 与 a），取最外层
                target, stmt = c, cur_stmt
                return
            if isinstance(c, (*FUNCS, ast.ClassDef)) and c is not fn:
                continue
            find(
                c,
                c if isinstance(c, ast.stmt) else cur_stmt,
                blocked or isinstance(c, SCOPES[3:] + (ast.Lambda,)),
            )

    find(fn, fn, False)
    if target is None:
        sys.exit("这个位置没有表达式")
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(target)):
        sys.exit("表达式里有 yield")
    host_locals = names(fn.body, ast.Store) | {
        x.arg
        for x in (
            *fn.args.posonlyargs,
            *fn.args.args,
            *fn.args.kwonlyargs,
            *(y for y in (fn.args.vararg, fn.args.kwarg) if y),
        )
    }
    reads = names([target], ast.Load)
    uses_self = "self" in reads
    if uses_self and not a.method:
        sys.exit("表达式用到 self：加 --method")
    params = sorted((reads & host_locals) - {"self", "cls"})
    missing = sorted(set(params) - defined_before(fn, stmt.lineno)) if stmt is not fn else []
    if missing:
        sys.exit(f"参数 {missing} 在这条语句处不一定已赋值")
    is_async = any(isinstance(n, ast.Await) for n in own_walk(target))
    # 表达式原文：第一行从列号起，续行原样
    seg_lines = lines[target.lineno - 1 : target.end_lineno]
    first_b = seg_lines[0].encode("utf-8")
    if len(seg_lines) == 1:
        seg = first_b[target.col_offset : target.end_col_offset].decode("utf-8")
    else:
        last_b = seg_lines[-1].encode("utf-8")
        seg = (
            first_b[target.col_offset :].decode("utf-8")
            + "".join(seg_lines[1:-1])
            + last_b[: target.end_col_offset].decode("utf-8")
        )
    keyword = len(params) > 4
    shown = params[:1] + (["*"] + params[1:] if keyword else params[1:])
    in_class = a.method
    pad = "    " if in_class else ""
    sig = ", ".join((["self"] if in_class else []) + shown)
    head = f"{pad}{'async ' if is_async else ''}def {a.name}({sig}):\n"
    body = f"{pad}    return (\n{pad}        {seg}\n{pad}    )\n"
    new_fn = ("\n" if in_class else "\n\n") + head + body
    args = ", ".join(params[:1] + ([f"{p}={p}" for p in params[1:]] if keyword else params[1:]))
    call = f"{'await ' if is_async else ''}{'self.' if in_class else ''}{a.name}({args})"
    # 替换表达式区间
    out = list(lines)
    s0 = out[target.lineno - 1].encode("utf-8")
    s1 = out[target.end_lineno - 1].encode("utf-8")
    replaced = (
        s0[: target.col_offset] + call.encode("utf-8") + s1[target.end_col_offset :]
    ).decode("utf-8")
    out[target.lineno - 1 : target.end_lineno] = [replaced]
    # 新函数插在原函数之后；生成模块级函数而原函数在类里时，插在类之后（行号按替换后的位置算）
    shift = target.end_lineno - target.lineno
    owner = next(
        (
            c
            for c in tree.body
            if isinstance(c, ast.ClassDef) and c.lineno <= fn.lineno <= c.end_lineno
        ),
        None,
    )
    anchor_end = owner.end_lineno if (owner is not None and not in_class) else fn.end_lineno
    insert_at = anchor_end - shift
    out[insert_at:insert_at] = [new_fn]
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
    check = [
        ruff,
        "check",
        "--no-cache",
        "--config",
        str(REPO / "scripts/code_quality/ruff.toml"),
        "--select",
        "F821",
        "--output-format",
        "concise",
        "-q",
    ]
    before = subprocess.run([*check, "-"], input=text, capture_output=True, text=True).stdout.count(
        "F821"
    )
    after = subprocess.run([*check, str(path)], capture_output=True, text=True).stdout.count("F821")
    if after > before:
        path.write_text(text, encoding="utf-8")
        sys.exit(f"抽出后多了 {after - before} 处未定义名字，已还原")
    print(f"抽出表达式 {a.name}：{target.end_lineno - target.lineno + 1} 行，参数 {len(params)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
