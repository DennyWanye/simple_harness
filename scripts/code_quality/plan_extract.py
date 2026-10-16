#!/usr/bin/env python3
"""代码质量整治阶段 6D：给超长函数自动挑可抽取的语句段，再调用 extract_method.py 抽取。

用法：
    python3 scripts/code_quality/plan_extract.py plan <文件> <函数名> [--line 起始行] [--target 150]
        # 打印方案：每段的行范围、行数、参数、返回值、开头几行（供起名）
    python3 scripts/code_quality/plan_extract.py apply <文件> <函数名> <方案.json> [--line 起始行]
        # 方案 JSON：[{"span": "起-止", "name": "新名字"}, ...]；从下往上逐段调 extract_method.py

挑段规则（保守，挑不出就留给人工）：
- 段是函数里同一层的若干条完整语句（函数体顶层，或 if/try/with/循环体里），不进内层函数与 lambda；
- 段里没有 yield、nonlocal、global、locals()/vars()/eval/exec，没有作用于段外循环的 break/continue；
- 段里有 return 时，只接受"段走完原函数也就结束"的位置（段是所在语句列表的末尾，往外每一层也都在
  末尾，且外层没有循环、没有 try 的 else 分支），抽出后调用处写 `return 新方法(...)`（--tail）；
- 段内读到、被内层函数用 nonlocal 改写的名字不抽（抽出后读到的是参数副本）；
- 参数不超过 8 个，返回值不超过 3 个；行数在 25～140 之间；多段互不重叠，按"行数减参数与返回值代价"贪心挑，
  挑到函数估计降到目标行数为止。
参数与返回值的最终核对由 extract_method.py 做（拒绝就跳过该段）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extract_method import header_bindings  # noqa: E402
from scope_names import (
    closure_factory_problem,
    defined_before,
    definitely_assigned,
    locally_bound,
    names,
)  # noqa: E402

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
DYNAMIC = {"locals", "vars", "eval", "exec"}


def find_fn(tree: ast.Module, name: str, line: int | None):
    cands = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    if line:
        cands = [
            n
            for n in cands
            if n.lineno == line
            or (n.decorator_list and n.decorator_list[0].lineno <= line <= n.lineno)
        ]
    if not cands:
        sys.exit(f"找不到函数 {name}")
    return max(cands, key=lambda n: n.end_lineno - n.lineno)


def walk_own(node: ast.AST):
    """遍历节点，内层函数与类只产出节点本身、不进内部；lambda 整个跳过。"""
    yield node
    if isinstance(node, SCOPES) and node is not walk_own.root:
        return
    for c in ast.iter_child_nodes(node):
        if isinstance(c, ast.Lambda):
            continue
        yield from walk_own(c)


def escapes(node: ast.AST, inside_loop: bool) -> bool:
    if isinstance(node, (ast.Break, ast.Continue)) and not inside_loop:
        return True
    if isinstance(node, SCOPES):
        return False
    nested = inside_loop or isinstance(node, LOOPS)
    orelse = getattr(node, "orelse", [])
    orelse = orelse if isinstance(orelse, list) else []
    return any(
        escapes(c, inside_loop if any(c is o for o in orelse) else nested)
        for c in ast.iter_child_nodes(node)
    )


def stmt_lists(fn):
    """函数里所有语句列表，附带"从这里落空走完后原函数是否就结束"的判断所需的祖先链。"""
    out = []

    def visit(body, chain):
        out.append((body, chain))
        for s in body:
            if isinstance(s, SCOPES):
                continue
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(s, field, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    visit(sub, chain + [(body, s, field)])
            for h in getattr(s, "handlers", None) or []:
                visit(h.body, chain + [(body, s, "handler")])
            if isinstance(s, ast.Match):
                for c in s.cases:
                    visit(c.body, chain + [(body, s, "case")])

    visit(fn.body, [])
    return out


def ends_function(body, chain) -> bool:
    """语句列表走完后，原函数不再执行别的语句（finally 照样会跑，与 return 一致）。"""
    for parent_body, stmt, field in reversed(chain):
        if isinstance(stmt, LOOPS):
            return False
        if isinstance(stmt, (ast.Try, ast.TryStar)):
            if field == "body" and stmt.orelse:
                return False
            if field == "finalbody":
                return False
        if parent_body[-1] is not stmt:
            return False
    return True


def segment_span(lines, body, i, j):
    first = body[i]
    start = (
        first.decorator_list[0].lineno if getattr(first, "decorator_list", None) else first.lineno
    )
    # 紧贴在上方的注释随段一起走
    top = start
    while top > 1 and lines[top - 2].strip().startswith("#"):
        top -= 1
    return top, start, body[j - 1].end_lineno


def analyse(
    fn,
    lines,
    target: int,
    max_size: int = 140,
    min_size: int = 25,
    max_params: int = 8,
    max_outs: int = 3,
):
    params = {x.arg for x in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    if fn.args.vararg:
        params.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        params.add(fn.args.kwarg.arg)
    walk_own.root = fn
    all_stmts = [n for n in walk_own(fn) if isinstance(n, ast.stmt) and n is not fn]
    nonlocal_written = set()
    for inner in ast.walk(fn):
        if inner is not fn and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(inner):
                if isinstance(n, ast.Nonlocal):
                    nonlocal_written |= set(n.names)
    annotations = {}
    for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs):
        if a.annotation is not None:
            annotations[a.arg] = ast.unparse(a.annotation)
    for n in walk_own(fn):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            annotations.setdefault(n.target.id, ast.unparse(n.annotation))
    cands = []
    for body, chain in stmt_lists(fn):
        tail_ok = ends_function(body, chain)
        doc = (
            body is fn.body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        )
        for i in range(1 if doc else 0, len(body)):
            for j in range(i + 1, len(body) + 1):
                top, start, end = segment_span(lines, body, i, j)
                size = end - top + 1
                if size > max_size:
                    break
                if size < min_size:
                    continue
                block = body[i:j]

                # 只包一两句调用的段（多半是已抽出方法的调用处）再抽一层没有意义
                def bare_call(st):
                    v = getattr(st, "value", None)
                    v = v.value if isinstance(v, ast.Await) else v
                    return isinstance(
                        st, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)
                    ) and isinstance(v, ast.Call)

                if len(block) <= 2 and all(bare_call(st) for st in block):
                    continue
                own = [n for s in block for n in walk_own(s)]
                if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own):
                    continue
                # 段里（含段内的内层函数）有 nonlocal/global：搬走后绑定对象会变
                if any(
                    isinstance(n, (ast.Nonlocal, ast.Global)) for s in block for n in ast.walk(s)
                ):
                    continue
                if any(
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id in DYNAMIC
                    for n in own
                ):
                    continue
                if any(escapes(s, False) for s in block):
                    continue
                has_return = any(isinstance(n, ast.Return) for n in own)
                tail = False
                early = False
                if has_return:
                    # 段走完原函数就结束；或段的最后一句就是 return/raise（段不会落空，后面的语句本来就走不到）
                    closes = isinstance(block[-1], (ast.Return, ast.Raise))
                    if j == len(body) and (tail_ok or closes):
                        tail = True
                    else:
                        # 中途 return：新方法多返回"是否已返回"（裸 return 与带值 return 不混用）
                        rets = [n for n in own if isinstance(n, ast.Return)]
                        if any(r.value is None for r in rets) and not all(
                            r.value is None for r in rets
                        ):
                            continue
                        early = True
                before = [s for s in all_stmts if s.end_lineno < start]
                after = [s for s in all_stmts if s.lineno > end]
                assigned_before = (
                    names(before, ast.Store) | params | header_bindings(fn, start, end)
                )
                assigned_before |= {
                    h.name
                    for h in ast.walk(fn)
                    if isinstance(h, ast.ExceptHandler)
                    and h.name
                    and h.lineno < start <= h.end_lineno
                }
                reads = names(block, ast.Load)
                if reads & nonlocal_written:
                    continue
                need_in = (reads & assigned_before) - {"self", "cls"}
                stores = names(block, ast.Store)

                # 段内先赋值再读的名字不算参数（粗判：段内第一次出现是写）

                need_in = {n for n in need_in if not locally_bound(block, n)}
                # except ... as 名字：出了处理块就解绑，不能当返回值
                handler_names = {
                    h.name
                    for s in block
                    for h in ast.walk(s)
                    if isinstance(h, ast.ExceptHandler) and h.name
                }
                stores_out = stores - handler_names
                need_out = set() if tail else (stores_out & names(after, ast.Load))
                loops = [
                    n
                    for n in walk_own(fn)
                    if isinstance(n, LOOPS) and n.lineno < start and end <= n.end_lineno
                ]
                for loop in loops:
                    outside = [
                        s
                        for s in ast.walk(loop)
                        if isinstance(s, ast.stmt)
                        and s is not loop
                        and (s.end_lineno < start or s.lineno > end)
                    ]
                    need_out |= stores_out & names(outside, ast.Load)
                # 返回值必须走完这段一定已赋值：只在部分分支赋值、段前有值的，作参数传入；段前也没值的，不抽
                if not tail:
                    sure = definitely_assigned(block, set(need_in))
                    for v in sorted(need_out - sure):
                        if v in assigned_before:
                            need_in.add(v)
                    if need_out - definitely_assigned(block, set(need_in)):
                        continue
                # 段里定义的内层函数被段后用到：只接受"段就是这一句 def"（抽成返回闭包的工厂函数），
                # 且外层在定义之后不改写闭包读的变量、定义不在循环里
                def_names = {
                    x.name
                    for top in block
                    for x in ast.walk(top)
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if need_out & def_names:
                    single = len(block) == 1 and isinstance(
                        block[0], (ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    if (
                        not single
                        or need_out != {block[0].name}
                        or closure_factory_problem(fn, block, end)
                    ):
                        continue
                # 参数在调用处必须一定已赋值，否则调用本身可能报 UnboundLocalError
                if need_in - defined_before(fn, start):
                    continue
                if len(need_in) > max_params or len(need_out) > max_outs:
                    continue
                score = size - 6 * len(need_in) - 12 * len(need_out)
                if early:
                    score -= 15  # 中途 return 的段读起来多一层，优先挑别的
                cands.append(
                    dict(
                        top=top,
                        start=start,
                        end=end,
                        size=size,
                        params=sorted(need_in),
                        outs=sorted(need_out),
                        tail=tail,
                        early=early,
                        score=score,
                    )
                )
    cands.sort(key=lambda c: -c["score"])
    chosen = []
    length = fn.end_lineno - fn.lineno + 1
    for c in cands:
        if length <= target:
            break
        if any(not (c["end"] < d["top"] or d["end"] < c["top"]) for d in chosen):
            continue
        chosen.append(c)
        length -= c["size"] - (3 if c["outs"] or c["tail"] else 2)
    chosen.sort(key=lambda c: c["top"])
    analyse.all_candidates = cands
    return chosen, annotations, length


def preview(lines, c) -> str:
    body = [ln.strip() for ln in lines[c["top"] - 1 : c["end"]] if ln.strip()]
    return " | ".join(body[:4])[:220]


def cmd_plan(a) -> int:
    path = Path(a.file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    fn = find_fn(ast.parse("".join(lines)), a.function, a.line)
    chosen, _ann, est = analyse(fn, lines, a.target, a.max, a.min, a.max_params, a.max_outs)
    print(
        f"{a.function}：{fn.end_lineno - fn.lineno + 1} 行 → 估计 {est} 行，挑出 {len(chosen)} 段"
    )
    for c in chosen:
        print(
            json.dumps(
                {
                    "early": c.get("early", False),
                    "span": f"{c['top']}-{c['end']}",
                    "size": c["size"],
                    "params": c["params"],
                    "outs": c["outs"],
                    "tail": c["tail"],
                },
                ensure_ascii=False,
            )
        )
        print("    " + preview(lines, c))
    return 0


def cmd_apply(a) -> int:
    path = Path(a.file)
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    tree = ast.parse("".join(lines))
    fn = find_fn(tree, a.function, a.line)
    chosen, annotations, _ = analyse(fn, lines, 0)
    by_span = {f"{c['top']}-{c['end']}": c for c in chosen}
    # 方案里的段必须是规划器当时挑出的段（重新分析一遍，防止文件改过）
    full, _, _ = analyse_all(fn, lines)
    by_span.update({f"{c['top']}-{c['end']}": c for c in full})
    ret_ann = ast.unparse(fn.returns) if fn.returns is not None else ""
    static = any(ast.unparse(d) == "staticmethod" for d in fn.decorator_list)
    if any(ast.unparse(d) == "classmethod" for d in fn.decorator_list):
        sys.exit("类方法不处理")
    done = 0
    for item in sorted(plan, key=lambda it: -int(it["span"].split("-")[0])):
        c = by_span.get(item["span"])
        if c is None:
            print(f"跳过 {item['span']}：不是规划器认可的段")
            continue
        params = ", ".join(f"{p}: {annotations[p]}" if p in annotations else p for p in c["params"])
        cmd = [
            sys.executable,
            str(HERE / "extract_method.py"),
            str(path),
            a.function,
            f"{c['top'] if c['top'] == c['start'] else c['start']}-{c['end']}",
            item["name"],
            "--params",
            params,
        ]
        if c.get("early"):
            cmd += ["--early-return"]
            if c["outs"]:
                cmd += ["--returns", ", ".join(c["outs"])]
        elif c["tail"]:
            cmd += ["--tail"]
            if ret_ann:
                cmd += ["--returns-type", ret_ann]
        elif c["outs"]:
            cmd += ["--returns", ", ".join(c["outs"])]
            if len(c["outs"]) == 1 and c["outs"][0] in annotations:
                cmd += ["--returns-type", annotations[c["outs"][0]]]
        if static:
            cmd += ["--static"]
        backup = Path(tempfile.mkstemp(suffix=".py")[1])
        shutil.copy(path, backup)
        res = subprocess.run(cmd, capture_output=True, text=True)
        out = (res.stdout + res.stderr).strip()
        if res.returncode != 0 or "确认段后读之前会重新赋值" in out or "下一轮循环还会读" in out:
            shutil.copy(backup, path)
            print(
                f"跳过 {item['span']} {item['name']}：{out.splitlines()[-1] if out else res.returncode}"
            )
        else:
            done += 1
            print(out.splitlines()[-1])
        backup.unlink()
    fn2 = find_fn(ast.parse(path.read_text(encoding="utf-8")), a.function, None)
    print(f"抽出 {done} 段；{a.function} 现在 {fn2.end_lineno - fn2.lineno + 1} 行")
    return 0


def analyse_all(fn, lines):
    analyse(fn, lines, 0, 140, 8, 14, 6)
    return analyse.all_candidates, None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("file")
    p.add_argument("function")
    p.add_argument("--line", type=int)
    p.add_argument("--target", type=int, default=150)
    p.add_argument("--max", type=int, default=140)
    p.add_argument("--min", type=int, default=25)
    p.add_argument("--max-params", type=int, default=8)
    p.add_argument("--max-outs", type=int, default=3)
    q = sub.add_parser("apply")
    q.add_argument("file")
    q.add_argument("function")
    q.add_argument("plan")
    q.add_argument("--line", type=int)
    a = ap.parse_args()
    return cmd_plan(a) if a.cmd == "plan" else cmd_apply(a)


if __name__ == "__main__":
    sys.exit(main())
