#!/usr/bin/env python3
"""代码质量整治阶段 5：把长方法里一段连续语句抽成同类的私有方法（拆长函数）。

用法：
    python3 scripts/code_quality/extract_method.py <文件> <方法名> <起始行-结束行> <新方法名>
        --params "tx: _SqliteWorkflowTransaction, completion: sqlite3.Row, ..."
        [--returns "ticket, request" --returns-type "tuple[sqlite3.Row, WorkflowLaunchRequest]"]
        [--tail]          # 这段走完必定 return 或 raise：调用处写成 `return self.新方法(...)`
                          #（返回类型为 None 时写成调用后单独 `return`）
        [--sentinel 名字] # 这段要么提前 return 一个非空结果、要么走完不返回：新方法走完返回 None，
                          # 调用处写成 `名字 = self.新方法(...)` 加 `if 名字 is not None: return 名字`
        [--static]        # 新方法不用 self：写成 @staticmethod
        [--after 方法名]  # 新方法插在哪个方法后面（默认紧跟原方法）

规则：
- 行范围必须恰好是原方法里同一层的若干条完整语句。
- 参数与返回值由调用者写明（先用 block_flow.py 看数据流）；工具核对：段内读到、段前赋过值的名字都要
  在参数里，段内赋值、段后还读到的名字都要在返回值里（--tail 时没有返回值），否则拒绝。
- 段里有 await 时新方法是 async，调用处加 await。
- 参数多于 4 个时，第一个之后的参数仅限关键字，调用处按名字传。
- 段内代码逐字搬走，只调整缩进。收尾跑 ruff 格式化。
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
from reindent import reindent, string_interior_lines  # noqa: E402


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
                    out |= {
                        x.name
                        for x in ast.walk(case.pattern)
                        if isinstance(x, (ast.MatchAs, ast.MatchStar)) and x.name
                    }
                    out |= {
                        x.rest
                        for x in ast.walk(case.pattern)
                        if isinstance(x, ast.MatchMapping) and x.rest
                    }
    return out


def names(nodes: list[ast.AST], ctx: type) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ctx):
                out.add(n.id)
            elif ctx is ast.Store and isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
            elif ctx is ast.Store and isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                out.add(n.name)  # 函数里定义的内层函数与类：名字是局部绑定
            elif ctx is ast.Store and isinstance(n, (ast.Import, ast.ImportFrom)):
                out |= {
                    (a.asname or a.name).split(".")[0] for a in n.names
                }  # 函数里的导入也是局部绑定
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
    ap.add_argument("--sentinel")
    ap.add_argument("--after")
    a = ap.parse_args()

    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    # 方法名可写成 "外层.内层"：从外层方法里的闭包抽段（同名闭包多时用它定位）
    outer = None
    if "." in a.method:
        outer_name, inner_name = a.method.split(".", 1)
        outer = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == outer_name
        )
        fn = next(
            n
            for n in ast.walk(outer)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == inner_name
            and n is not outer
        )
    else:
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
    assigned_before = names(before, ast.Store) | params | header_bindings(fn, start, end)
    if outer is not None:  # 闭包读到的外层变量：外层参数与闭包定义之前的赋值
        outer_before = [
            s for s in ast.walk(outer) if isinstance(s, ast.stmt) and s.end_lineno < fn.lineno
        ]
        assigned_before |= names(outer_before, ast.Store) | {
            x.arg for x in (*outer.args.posonlyargs, *outer.args.args, *outer.args.kwonlyargs)
        }
    # 段在 `except … as 名字` 里面时，名字由外层的异常处理绑定
    assigned_before |= {
        h.name
        for h in ast.walk(fn)
        if isinstance(h, ast.ExceptHandler) and h.name and h.lineno < start <= h.end_lineno
    }
    need_in = (names(block, ast.Load) & assigned_before) - {"self"}
    # lambda 自己的参数：只在绑定它的 lambda 里读到的名字不是外面传进来的
    inside_lambda: set[int] = set()
    lambda_args: set[str] = set()
    for s in block:
        for lam in ast.walk(s):
            if isinstance(lam, ast.Lambda):
                bound = {
                    x.arg for x in (*lam.args.posonlyargs, *lam.args.args, *lam.args.kwonlyargs)
                }
                lambda_args |= bound
                inside_lambda |= {
                    id(n) for n in ast.walk(lam.body) if isinstance(n, ast.Name) and n.id in bound
                }
    need_in -= {
        name
        for name in lambda_args
        if all(
            id(n) in inside_lambda
            for s in block
            for n in ast.walk(s)
            if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
        )
    }
    need_out = names(block, ast.Store) & names(after, ast.Load)
    # 段在循环里：下一轮循环还会读到段内赋的值（block_flow 只看段后面的语句，看不到下一轮）
    loops = [
        n
        for n in ast.walk(fn)
        if isinstance(n, (ast.While, ast.For, ast.AsyncFor))
        and n.lineno < start
        and end <= n.end_lineno
    ]
    loop_reads: set[str] = set()
    for loop in loops:
        outside = [
            s
            for s in ast.walk(loop)
            if isinstance(s, ast.stmt)
            and s is not loop
            and (s.end_lineno < start or s.lineno > end)
        ]
        loop_reads |= names(outside, ast.Load)
        if isinstance(loop, (ast.For, ast.AsyncFor)):
            loop_reads |= names([loop.iter], ast.Load)
        elif isinstance(loop, ast.While):
            loop_reads |= names([loop.test], ast.Load)
    loop_carried = (names(block, ast.Store) & loop_reads) - need_out
    need_out |= loop_carried

    # 段里作用于外层循环的 break / continue 抽不出去
    def escapes(node: ast.AST, inside_loop: bool) -> bool:
        if isinstance(node, (ast.Break, ast.Continue)) and not inside_loop:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return False
        nested = inside_loop or isinstance(node, (ast.While, ast.For, ast.AsyncFor))
        orelse = getattr(node, "orelse", [])  # 条件表达式的 orelse 是表达式，不是语句列表
        orelse = orelse if isinstance(orelse, list) else []
        return any(
            escapes(c, inside_loop if any(c is o for o in orelse) else nested)
            for c in ast.iter_child_nodes(node)
        )

    if any(escapes(s, False) for s in block):
        sys.exit("段内有作用于外层循环的 break/continue，抽不出去")
    # 参数串里可能有带逗号的注解（tuple[a, b]），按顶层逗号切
    given_params = split_top(a.params)
    param_names = {p.split(":")[0].strip() for p in given_params}
    returns = [r.strip() for r in a.returns.split(",") if r.strip()]
    missing_in = (
        need_in
        - param_names
        - {
            t.id
            for s in block
            for c in ast.walk(s)
            if isinstance(c, ast.comprehension)
            for t in ast.walk(c.target)
            if isinstance(t, ast.Name)
            if not any(
                isinstance(n, ast.Name) and n.id == t.id and isinstance(n.ctx, ast.Store)
                for s2 in block
                for n in ast.walk(s2)
                if n is not t
                and not any(
                    n is x
                    for cc in ast.walk(s2)
                    if isinstance(cc, ast.comprehension)
                    for x in ast.walk(cc.target)
                )
            )
        }
    )
    # 段内自己也赋值的名字，可能是先赋值再读（段前那次赋值与它无关）：只提示，由调用者确认
    shadowed = missing_in & names(block, ast.Store)
    if missing_in - shadowed:
        sys.exit(f"参数缺：{sorted(missing_in - shadowed)}（段内读到、段前赋过值）")
    # 赋值语句先求右边再写左边：左边目标的写入时刻算在整条语句末尾（右边跨行时按行号比会误判）
    target_at: dict[int, tuple[int, int]] = {}
    for s in block:
        for st in ast.walk(s):
            if isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                for tg in targets:
                    for n in ast.walk(tg):
                        target_at[id(n)] = (st.end_lineno, 10**6)  # type: ignore[assignment]

    def first(name: str, ctx: type) -> tuple[int, int]:
        spots = [
            target_at.get(id(n), (n.lineno, n.col_offset))
            for s in block
            for n in ast.walk(s)
            if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ctx)
        ]
        if ctx is ast.Store:  # except ... as 名字
            spots += [
                (h.lineno, h.col_offset)
                for s in block
                for h in ast.walk(s)
                if isinstance(h, ast.ExceptHandler) and h.name == name
            ]
        return min(spots) if spots else (10**9, 0)

    # 推导式里绑定的名字只在推导式内有效，不算段外传入
    comp_bound = {
        t.id
        for s in block
        for c in ast.walk(s)
        if isinstance(c, ast.comprehension)
        for t in ast.walk(c.target)
        if isinstance(t, ast.Name)
    }
    shadowed -= comp_bound

    # 赋值语句右边先求值：同一行里"读在右、写在左"按读在先处理（行号相同比列号不可靠，保守判为读在先）
    read_first = sorted(n for n in shadowed if first(n, ast.Load) <= first(n, ast.Store))
    if read_first:
        sys.exit(f"参数缺：{read_first}（段内先读后写，要从段前传进来）")
    if shadowed:
        print(f"提示：{sorted(shadowed)} 段内先赋值再读，不作参数")
    redundant = sorted(
        n for n in param_names & names(block, ast.Store) if first(n, ast.Store) < first(n, ast.Load)
    )
    if redundant:
        sys.exit(f"多余参数：{redundant}（段内先赋值再读，不用传进来）")
    if not a.tail and sorted(need_out - set(returns)):
        print(
            f"提示：段后还读到 {sorted(need_out - set(returns))}；确认段后读之前会重新赋值，否则补进 --returns"
        )
        if loop_carried - set(returns):
            print(
                f"提示（循环）：{sorted(loop_carried - set(returns))} 下一轮循环还会读，确认每轮读之前都会重新赋值"
            )
    # 闭包按调用时的值读外层变量：段内给被闭包读的变量重新赋值，抽出后闭包就读不到新值。
    # 只有"该变量在返回值里、段内不调用读它的闭包"时才安全（调用处先赋回再调闭包）。
    captured: dict[str, set[str]] = {}
    for inner in ast.walk(fn):
        if inner is fn or not isinstance(
            inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            continue
        if start <= inner.lineno <= end:  # 段内自己的闭包随段一起搬走
            continue
        own = {x.arg for x in (*inner.args.posonlyargs, *inner.args.args, *inner.args.kwonlyargs)}
        if inner.args.vararg:
            own.add(inner.args.vararg.arg)
        if inner.args.kwarg:
            own.add(inner.args.kwarg.arg)
        body_nodes = inner.body if isinstance(inner.body, list) else [inner.body]
        own |= names(body_nodes, ast.Store)
        for name in names(body_nodes, ast.Load) - own:
            captured.setdefault(name, set()).add(getattr(inner, "name", "<lambda>"))
    called = names(block, ast.Load)
    for name in sorted(names(block, ast.Store) & set(captured)):
        readers = captured[name]
        if name not in returns or readers & called:
            sys.exit(f"段内给闭包 {sorted(readers)} 读的 {name} 赋值，抽出后闭包读不到新值")
        print(f"提示：{name} 被闭包 {sorted(readers)} 读；段内不调这些闭包，调用处赋回后再用")
    is_async = any(isinstance(n, ast.Await) for s in block for n in ast.walk(s))
    if a.sentinel:
        if a.tail or returns:
            sys.exit("--sentinel 不和 --tail、--returns 一起用")
        if a.sentinel in names([fn], ast.Store) | names([fn], ast.Load):
            sys.exit(f"--sentinel 名字 {a.sentinel} 已在原方法里用过")

        def own_returns(node: ast.AST) -> list[ast.Return]:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return []
            found = [node] if isinstance(node, ast.Return) else []
            return found + [r for c in ast.iter_child_nodes(node) for r in own_returns(c)]

        rets = [r for s in block for r in own_returns(s)]
        if not rets:
            sys.exit("--sentinel 的段里没有 return")
        if any(
            r.value is None or (isinstance(r.value, ast.Constant) and r.value.value is None)
            for r in rets
        ):
            sys.exit("--sentinel 的段里有返回 None 的 return，和'走完返回 None'分不开")

    indent = " " * (block[0].col_offset)
    # 多行字符串内部的行原样保留（textwrap 会连字符串续行一起挪，字面量就变了）
    interior = string_interior_lines(text)
    method_indent = " " * fn.col_offset
    ret_ann = a.returns_type or ("None" if not returns and not a.tail else "")
    # 参数多于 4 个时，第一个之后都写成仅限关键字，调用处按名字传，免得位置传错
    keyword = len(given_params) > 4
    shown = given_params[:1] + (["*"] + given_params[1:] if keyword else given_params[1:])
    # 原函数是模块级函数（不在类里）时，新函数也是模块级函数：不带 self，调用处直接写名字
    module_level = fn in tree.body
    if outer is not None:  # 从闭包抽出的段作为类的方法，插在外层方法后面
        method_indent = " " * outer.col_offset
    sig_params = ", ".join((["self"] if not (a.static or module_level) else []) + shown)
    head = (
        (f"{method_indent}@staticmethod\n" if a.static and not module_level else "")
        + f"{method_indent}{'async ' if is_async else ''}def {a.new_name}({sig_params})"
        + (f" -> {ret_ann}" if ret_ann else "")
        + ":\n"
    )
    new_body = reindent("".join(lines[start - 1 : end]), method_indent + "    ", start, interior)
    if returns and not a.tail:
        new_body += f"{method_indent}    return {', '.join(returns)}\n"
    if a.sentinel:
        new_body += f"{method_indent}    return None\n"
    new_method = "\n" + head + new_body

    arg_names = [p.split(":")[0].strip() for p in given_params]
    call_args = ", ".join(
        arg_names[:1] + ([f"{n}={n}" for n in arg_names[1:]] if keyword else arg_names[1:])
    )
    owner = "" if module_level else ("self." if not a.static else type_owner(tree, fn) + ".")
    call = f"{'await ' if is_async else ''}{owner}{a.new_name}({call_args})"
    if a.sentinel:
        call_line = (
            f"{indent}{a.sentinel} = {call}\n{indent}if {a.sentinel} is not None:\n"
            f"{indent}    return {a.sentinel}\n"
        )
    elif a.tail and ret_ann == "None":  # 段走完就结束原函数、不带返回值：调用后单独 return
        call_line = f"{indent}{call}\n{indent}return\n"
    elif a.tail:
        call_line = f"{indent}return {call}\n"
    elif returns:
        call_line = f"{indent}{', '.join(returns)} = {call}\n"
    else:
        call_line = f"{indent}{call}\n"

    anchor = fn if outer is None else outer
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
        f"抽出 {a.new_name}：{end - start + 1} 行，{'async ' if is_async else ''}参数 {len(given_params)} 个"
    )
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
