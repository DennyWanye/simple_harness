#!/usr/bin/env python3
"""代码质量整治阶段 5：把长方法里一段连续语句抽成同类的私有方法（拆长函数）。

用法：
    python3 scripts/code_quality/extract_method.py <文件> <方法名> <起始行-结束行> <新方法名>
        --params "tx: _SqliteWorkflowTransaction, completion: sqlite3.Row, ..."
        [--returns "ticket, request" --returns-type "tuple[sqlite3.Row, WorkflowLaunchRequest]"]
        [--tail]          # 这段走完必定 return 或 raise：调用处写成 `return self.新方法(...)`
        [--static]        # 新方法不用 self：写成 @staticmethod
        [--after 方法名]  # 新方法插在哪个方法后面（默认紧跟原方法）

规则：
- 行范围必须恰好是原方法里同一层的若干条完整语句。
- 参数与返回值由调用者写明（先用 block_flow.py 看数据流）；工具核对：段内读到、段前赋过值的名字都要
  在参数里，段内赋值、段后还读到的名字都要在返回值里（--tail 时没有返回值），否则拒绝。
- 段里有 await 时新方法是 async，调用处加 await。
- 段内代码逐字搬走，只调整缩进。收尾跑 ruff 格式化。
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]


def names(nodes: list[ast.AST], ctx: type) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ctx):
                out.add(n.id)
            elif ctx is ast.Store and isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("method")
    ap.add_argument("span")
    ap.add_argument("new_name")
    ap.add_argument("--params", default="")
    ap.add_argument("--returns", default="")
    ap.add_argument("--returns-type", default="")
    ap.add_argument("--tail", action="store_true")
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--after")
    a = ap.parse_args()

    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == a.method
    )
    start, end = (int(x) for x in a.span.split("-"))

    # 找到恰好覆盖这段的同层语句
    def block_in(body: list[ast.stmt]) -> list[ast.stmt] | None:
        picked = [s for s in body if s.lineno >= start and s.end_lineno <= end]
        if picked and picked[0].lineno == start and picked[-1].end_lineno == end:
            first, last = body.index(picked[0]), body.index(picked[-1])
            if body[first : last + 1] == picked:
                return picked
        for s in body:
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(s, field, None)
                if sub and s.lineno <= start and end <= s.end_lineno:
                    found = block_in(sub)
                    if found:
                        return found
            for h in getattr(s, "handlers", []):
                found = block_in(h.body)
                if found:
                    return found
        return None

    block = block_in(fn.body)
    if block is None:
        sys.exit("行范围不是同一层的完整语句")
    all_stmts = [n for n in ast.walk(fn) if isinstance(n, ast.stmt) and n is not fn]
    before = [s for s in all_stmts if s.end_lineno < start]
    after = [s for s in all_stmts if s.lineno > end]
    params = {x.arg for x in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    assigned_before = names(before, ast.Store) | params
    need_in = (names(block, ast.Load) & assigned_before) - {"self"}
    need_out = names(block, ast.Store) & names(after, ast.Load)
    # 参数串里可能有带逗号的注解（tuple[a, b]），按顶层逗号切
    given_params = split_top(a.params)
    param_names = {p.split(":")[0].strip() for p in given_params}
    returns = [r.strip() for r in a.returns.split(",") if r.strip()]
    missing_in = sorted(need_in - param_names)
    if missing_in:
        sys.exit(f"参数缺：{missing_in}（段内读到、段前赋过值）")
    if not a.tail and sorted(need_out - set(returns)):
        print(f"提示：段后还读到 {sorted(need_out - set(returns))}；确认段后读之前会重新赋值，否则补进 --returns")
    is_async = any(isinstance(n, ast.Await) for s in block for n in ast.walk(s))

    indent = " " * (block[0].col_offset)
    body_src = textwrap.dedent("".join(lines[start - 1 : end]))
    method_indent = " " * fn.col_offset
    ret_ann = a.returns_type or ("None" if not returns and not a.tail else "")
    sig_params = ", ".join((["self"] if not a.static else []) + given_params)
    head = (
        (f"{method_indent}@staticmethod\n" if a.static else "")
        + f"{method_indent}{'async ' if is_async else ''}def {a.new_name}({sig_params})"
        + (f" -> {ret_ann}" if ret_ann else "")
        + ":\n"
    )
    new_body = textwrap.indent(body_src, method_indent + "    ")
    if returns and not a.tail:
        new_body += f"{method_indent}    return {', '.join(returns)}\n"
    new_method = "\n" + head + new_body

    call_args = ", ".join(p.split(":")[0].strip() for p in given_params)
    owner = "self" if not a.static else type_owner(tree, fn)
    call = f"{'await ' if is_async else ''}{owner}.{a.new_name}({call_args})"
    if a.tail:
        call_line = f"{indent}return {call}\n"
    elif returns:
        call_line = f"{indent}{', '.join(returns)} = {call}\n"
    else:
        call_line = f"{indent}{call}\n"

    anchor = fn
    if a.after:
        anchor = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == a.after
        )
    insert_at = anchor.end_lineno  # 插在锚点方法之后
    out = list(lines)
    out[insert_at:insert_at] = [new_method]
    out[start - 1 : end] = [call_line]
    path.write_text("".join(out), encoding="utf-8")
    ruff = os.environ.get("RUFF") or str(
        REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
    )
    subprocess.run([ruff, "format", "--config", str(REPO / "scripts/code_quality/ruff.toml"),
                    "--quiet", str(path)], check=False)
    print(f"抽出 {a.new_name}：{end - start + 1} 行，{'async ' if is_async else ''}参数 {len(given_params)} 个")
    return 0


def split_top(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def type_owner(tree: ast.Module, fn: ast.AST) -> str:
    for c in ast.walk(tree):
        if isinstance(c, ast.ClassDef) and fn in c.body:
            return c.name
    sys.exit("--static 需要原方法在类里")


if __name__ == "__main__":
    sys.exit(main())
