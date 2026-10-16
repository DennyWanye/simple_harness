#!/usr/bin/env python3
"""代码质量整治阶段 6B：把（已改成运行状态对象的）超长方法里一段连续语句原样切成新方法。

用法：
    python3 scripts/code_quality/cut_phase.py <文件> <类名.方法名> <起始行-结束行> <新方法名> [--doc "说明"]

前提：先用 state_rename.py 把局部变量收进 `state`，段里不再有要传进传出的局部变量（异常名字只在处理块里）。
规则：
- 行范围必须恰好是同一层的若干条完整语句（方法体顶层，或方法里某个 for/while 循环体的顶层）。
- 段里的 `return` → `state.flow = "stop"` 加 `return`；指向外层循环的 `continue` / `break`
  → `state.flow = "continue"` / `"break"` 加 `return`（段内自己的循环里的不动）。
- 新方法：段里有 yield → 异步生成器，调用处 `async with aclosing(self.新方法(state)) as _phase: async for _event in _phase: yield _event`（文件要自己导入 aclosing 与 AsyncGenerator）；
  只有 await → `async def`，调用处 `await`；都没有 → 普通方法。
- 调用处之前把 `state.flow` 清成 None，之后按段里出现过的流程补检查（stop → return、
  continue → continue、break → break）。
- 新方法插在原方法后面；段内代码只改缩进与上述流程语句。收尾跑 ruff 格式化。
- `--nested`：从已切出的阶段方法里再切一段。段里的 `return` 必须紧跟在 `state.flow = "..."`
  之后（都是上一次切出时改写的），原样保留；调用处之后 `if state.flow is not None: return`。
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

LOOPS = (ast.For, ast.AsyncFor, ast.While)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reindent import string_interior_lines  # noqa: E402


def find_func(tree: ast.Module, qual: str):
    cls_name, _, fn_name = qual.partition(".")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    fn = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name
    )
    return cls, fn


def container(fn, start: int, end: int):
    """找到恰好包含 [start, end] 这些完整语句的语句列表，以及它是不是循环体。"""

    def search(stmts, in_loop_body):
        picked = [s for s in stmts if s.lineno >= start and s.end_lineno <= end]
        if picked and picked[0].lineno == start and picked[-1].end_lineno == end:
            first = stmts.index(picked[0])
            if stmts[first : first + len(picked)] == picked:
                return picked, in_loop_body
        for s in stmts:
            if s.lineno <= start and s.end_lineno >= end:
                for field in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(s, field, None)
                    if not inner:
                        continue
                    if field == "handlers":
                        for h in inner:
                            r = search(h.body, False)
                            if r:
                                return r
                        continue
                    r = search(inner, isinstance(s, LOOPS) and field == "body")
                    if r:
                        return r
        return None

    found = search(fn.body, False)
    if not found:
        sys.exit("行范围不是同一层的完整语句")
    return found


def flow_edits(stmts, in_loop_body: bool):
    """段内需要改写的流程语句：[(行号, 种类)]，种类为 stop / continue / break。"""
    edits = []

    def walk(node, loop_depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                continue
            if isinstance(child, ast.Return):
                if child.value is not None:
                    sys.exit(f"第 {child.lineno} 行 return 带值，异步生成器里不该出现")
                edits.append((child.lineno, "stop"))
            elif isinstance(child, (ast.Continue, ast.Break)) and loop_depth == 0:
                if not in_loop_body:
                    sys.exit(
                        f"第 {child.lineno} 行 {type(child).__name__} 指向段外的循环，但段不在循环体里"
                    )
                edits.append(
                    (child.lineno, "continue" if isinstance(child, ast.Continue) else "break")
                )
            elif isinstance(child, LOOPS):
                walk_loop(child, loop_depth)
                continue
            else:
                walk(child, loop_depth)

    def walk_loop(loop, loop_depth):
        # 循环自己的 body 里的 continue/break 属于这个循环；orelse 里的属于外层
        for field in ("target", "iter", "test"):
            if getattr(loop, field, None) is not None:
                walk(ast.Expr(getattr(loop, field)), loop_depth)
        for s in loop.body:
            walk(ast.Module(body=[s], type_ignores=[]), loop_depth + 1)
        for s in loop.orelse:
            walk(ast.Module(body=[s], type_ignores=[]), loop_depth)

    for s in stmts:
        walk(ast.Module(body=[s], type_ignores=[]), 0)
    return edits


def kinds(stmts):
    has_yield = has_await = False
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, SCOPES):
                continue
            if isinstance(n, (ast.Yield, ast.YieldFrom)):
                has_yield = True
            if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
                has_await = True
    return has_yield, has_await


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("qual")
    ap.add_argument("span")
    ap.add_argument("name")
    ap.add_argument("--doc", default="")
    ap.add_argument("--state", default="state")
    ap.add_argument("--nested", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    start, end = (int(x) for x in a.span.split("-"))
    src = path.read_text()
    tree = ast.parse(src)
    cls, fn = find_func(tree, a.qual)
    stmts, in_loop_body = container(fn, start, end)
    # 段的真实起点包括紧贴在第一条语句上方的注释
    lines = src.splitlines(keepends=True)
    top = start
    while top > 1 and lines[top - 2].strip().startswith("#"):
        top -= 1
    indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    edits = dict(flow_edits(stmts, in_loop_body))
    if a.nested:
        for ln, kind in edits.items():
            prev = lines[ln - 2].strip()
            if kind != "stop" or not re.fullmatch(
                rf'{a.state}\.flow = "(stop|continue|break)"', prev
            ):
                sys.exit(f"--nested：第 {ln} 行的 {kind} 前面没有 state.flow 赋值")
        nested_returns = bool(edits)
        edits = {}
    block = lines[top - 1 : end]
    # 多行字符串内部的行原样保留（只挪代码行的缩进，字面量内容不变）
    interior = string_interior_lines(src)
    out: list[tuple[str, bool]] = []
    for i, line in enumerate(block, start=top):
        if i in edits:
            body = line.strip()
            if body not in ("return", "continue", "break"):
                sys.exit(f"第 {i} 行流程语句不是单独一行：{body}")
            pad = line[: len(line) - len(line.lstrip())]
            out.append((f'{pad}{a.state}.flow = "{edits[i]}"\n', False))
            out.append((f"{pad}return\n", False))
        else:
            out.append((line, i in interior))
    common = min(
        (len(ln) - len(ln.lstrip(" ")) for ln, inner in out if not inner and ln.strip()), default=0
    )
    body_text = "".join(
        ln if inner else (" " * 8 + ln[common:] if ln.strip() else "\n") for ln, inner in out
    )
    has_yield, has_await = kinds(stmts)
    flows = sorted(set(edits.values()))
    if a.nested and nested_returns:
        flows = ["nested"]
    if has_yield:
        head = f"    async def {a.name}(self, {a.state}: _RunState) -> AsyncGenerator[AgentEvent, None]:\n"
        # aclosing：调用方中途关掉外层生成器时，段内的 finally 立刻跑（与切之前一致）
        call = (
            f"async with aclosing(self.{a.name}({a.state})) as _phase:\n"
            + " " * 4
            + "async for _event in _phase:\n"
            + " " * 8
            + "yield _event\n"
        )
    elif has_await:
        head = f"    async def {a.name}(self, {a.state}: _RunState) -> None:\n"
        call = f"await self.{a.name}({a.state})\n"
    else:
        head = f"    def {a.name}(self, {a.state}: _RunState) -> None:\n"
        call = f"self.{a.name}({a.state})\n"
    doc = f'        """{a.doc}"""\n\n' if a.doc else ""
    method = "\n" + head + doc + body_text
    pad = " " * indent
    call_lines = [f"{pad}{a.state}.flow = None\n"] if flows else []
    call_lines += [pad + c + "\n" for c in call.rstrip("\n").split("\n")]
    action = {"stop": "return", "continue": "continue", "break": "break"}
    for f in flows:
        if f == "nested":
            call_lines.append(f"{pad}if {a.state}.flow is not None:\n{pad}    return\n")
            continue
        call_lines.append(f'{pad}if {a.state}.flow == "{f}":\n{pad}    {action[f]}\n')
    new_lines = lines[: top - 1] + call_lines + lines[end:]
    # 新方法插在原方法后面
    fn_end = fn.end_lineno - (end - top + 1) + len(call_lines)
    new_lines.insert(fn_end, method)
    text = "".join(new_lines)
    if "flow: Any = None" not in text and flows:
        text = re.sub(
            r"(class _RunState:\n    \"\"\".*?\"\"\"\n\n)",
            r"\1    flow: Any = None\n",
            text,
            count=1,
            flags=re.S,
        )
    ast.parse(text)
    path.write_text(text)
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
    print(
        f"切出 {a.name}：{end - top + 1} 行，流程 {flows or '无'}，{'异步生成器' if has_yield else '异步' if has_await else '普通'}方法"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
