#!/usr/bin/env python3
"""代码质量整治阶段 6B：核对 cut_phase.py 切出的阶段方法能原样内联回去。

用法：
    python3 scripts/code_quality/inline_check.py <文件> <类名.方法名> <参照提交>

做法：参照提交里没有、现在类里新出现的方法，都当作切出来的阶段方法。从不再调用别的阶段方法的
那个开始，逐个把调用处（清 state.flow、调用、调用后的流程检查）换回方法体：
- 调用后检查是 `if state.flow is not None: return`（--nested 切的）→ 方法体原样放回；
- 否则把方法体里成对的 `state.flow = "stop"/"continue"/"break"` + `return` 还原成
  `return` / `continue` / `break`。
全部内联后，目标方法的语法树要和参照提交里的同名方法逐字相同；类里其余方法也要逐字相同。
"""

from __future__ import annotations

import ast
import copy
import subprocess
import sys
from pathlib import Path

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
STATE = "state"  # 状态对象的名字（--state 可改）


def methods(tree: ast.Module, cls_name: str | None) -> dict[str, ast.AST]:
    """类里的方法；cls_name 为 None 时是模块级函数。"""
    if cls_name is None:
        body = tree.body
    else:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name).body
    return {n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def is_result_assign(s: ast.stmt) -> bool:
    return (
        isinstance(s, ast.Assign)
        and len(s.targets) == 1
        and isinstance(s.targets[0], ast.Attribute)
        and isinstance(s.targets[0].value, ast.Name)
        and s.targets[0].value.id == STATE
        and s.targets[0].attr == "result"
    )


def is_flow_assign(s: ast.stmt, value=None) -> str | None:
    if (
        isinstance(s, ast.Assign)
        and len(s.targets) == 1
        and isinstance(s.targets[0], ast.Attribute)
        and isinstance(s.targets[0].value, ast.Name)
        and s.targets[0].value.id == STATE
        and s.targets[0].attr == "flow"
        and isinstance(s.value, ast.Constant)
    ):
        return "none" if s.value.value is None else s.value.value
    return None


def called_phase(s: ast.stmt, phases: set[str]) -> str | None:
    """语句是不是阶段方法的调用块（三种形状之一），是就返回方法名。"""

    def self_call(e: ast.AST) -> str | None:
        if not (
            isinstance(e, ast.Call)
            and [ast.dump(a) for a in e.args] == [ast.dump(ast.Name(STATE, ast.Load()))]
            and not e.keywords
        ):
            return None
        f = e.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
            return f.attr if f.attr in phases else None
        if isinstance(f, ast.Name):  # 模块级函数切出的阶段函数
            return f.id if f.id in phases else None
        return None

    if isinstance(s, ast.Expr):
        e = s.value.value if isinstance(s.value, ast.Await) else s.value
        return self_call(e)
    if isinstance(s, ast.AsyncWith) and len(s.items) == 1:
        ctx = s.items[0].context_expr
        if isinstance(ctx, ast.Call) and getattr(ctx.func, "id", None) == "aclosing":
            name = self_call(ctx.args[0])
            body = ast.unparse(ast.Module(body=s.body, type_ignores=[]))
            if name and body == "async for _event in _phase:\n    yield _event":
                return name
    return None


def flow_check(s: ast.stmt) -> str | None:
    """调用后的流程检查：返回 nested / stop / continue / break。"""
    if not (isinstance(s, ast.If) and not s.orelse):
        return None
    src = ast.unparse(s.test)
    # 再切一次时，前一次留下的 `return state.result` 被改写成了 result 赋值 + flow 赋值 + return
    if len(s.body) == 3 and is_result_assign(s.body[0]) and isinstance(s.body[2], ast.Return):
        if is_flow_assign(s.body[1]) == "stop" and src == f"{STATE}.flow == 'stop'":
            return "stop"
        return None
    # 再切一次时，前一次留下的检查本身被改写成了一对 flow 赋值 + return
    if len(s.body) == 2 and isinstance(s.body[1], ast.Return):
        kind = is_flow_assign(s.body[0])
        if kind in ("stop", "continue", "break") and src == f"{STATE}.flow == '{kind}'":
            return kind
        return None
    if len(s.body) != 1:
        return None
    if src == f"{STATE}.flow is not None" and isinstance(s.body[0], ast.Return):
        return "nested"
    for kind, node in (("stop", ast.Return), ("continue", ast.Continue), ("break", ast.Break)):
        if src == f"{STATE}.flow == '{kind}'" and isinstance(s.body[0], node):
            return kind
    return None


def restore_pairs(stmts: list[ast.stmt]) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    i = 0
    while i < len(stmts):
        s = stmts[i]
        # 带值 return 切出后是三句：state.result = 值；state.flow = "stop"；return
        if (
            is_result_assign(s)
            and i + 2 < len(stmts)
            and is_flow_assign(stmts[i + 1]) == "stop"
            and isinstance(stmts[i + 2], ast.Return)
            and stmts[i + 2].value is None
        ):
            out.append(ast.Return(value=s.value))
            i += 3
            continue
        kind = is_flow_assign(s)
        if (
            kind in ("stop", "continue", "break")
            and i + 1 < len(stmts)
            and isinstance(stmts[i + 1], ast.Return)
            and stmts[i + 1].value is None
        ):
            out.append(
                {"stop": ast.Return(), "continue": ast.Continue(), "break": ast.Break()}[kind]
            )
            i += 2
            continue
        out.append(restore_inside(s))
        i += 1
    return out


def restore_inside(s: ast.stmt) -> ast.stmt:
    if isinstance(s, SCOPES):
        return s
    for field in ("body", "orelse", "finalbody"):
        v = getattr(s, field, None)
        if isinstance(v, list) and v and isinstance(v[0], ast.stmt):
            setattr(s, field, restore_pairs(v))
    for h in getattr(s, "handlers", None) or []:
        h.body = restore_pairs(h.body)
    if isinstance(s, ast.Match):
        for c in s.cases:
            c.body = restore_pairs(c.body)
    return s


def inline_into(stmts: list[ast.stmt], name: str, body: list[ast.stmt]) -> tuple[list, int]:
    out: list[ast.stmt] = []
    hits = 0
    i = 0
    while i < len(stmts):
        s = stmts[i]
        if called_phase(s, {name}):
            if out and is_flow_assign(out[-1]) == "none":
                out.pop()
            j = i + 1
            checks = []
            while j < len(stmts) and flow_check(stmts[j]):
                checks.append(flow_check(stmts[j]))
                j += 1
            new = copy.deepcopy(body)
            if "nested" not in checks:
                new = restore_pairs(new)
            out.extend(new)
            hits += 1
            i = j
            continue
        for field in ("body", "orelse", "finalbody"):
            v = getattr(s, field, None)
            if (
                isinstance(v, list)
                and v
                and isinstance(v[0], ast.stmt)
                and not isinstance(s, SCOPES)
            ):
                v2, h = inline_into(v, name, body)
                setattr(s, field, v2)
                hits += h
        for h_ in getattr(s, "handlers", None) or []:
            h_.body, h = inline_into(h_.body, name, body)
            hits += h
        out.append(s)
        i += 1
    return out, hits


def strip_doc(fn) -> list[ast.stmt]:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def main() -> int:
    global STATE
    path, qual, ref = Path(sys.argv[1]), sys.argv[2], sys.argv[3]  # 可加 --renamed、--state 名字
    if "--state" in sys.argv:
        STATE = sys.argv[sys.argv.index("--state") + 1]
    cls_name, _, target = qual.partition(".") if "." in qual else (None, "", qual)
    cur = methods(ast.parse(path.read_text()), cls_name)
    old_src = subprocess.run(
        ["git", "show", f"{ref}:./{path.name}"], cwd=path.parent, capture_output=True, text=True
    ).stdout
    old = methods(ast.parse(old_src), cls_name)
    phases = set(cur) - set(old)
    pending = {n: cur[n] for n in phases | {target}}

    def calls(fn) -> set[str]:
        found = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.stmt):
                c = called_phase(n, phases)
                if c:
                    found.add(c)
        return found

    order = []
    left = set(phases)
    while left:
        leaf = next((n for n in sorted(left) if not (calls(pending[n]) & left)), None)
        if leaf is None:
            print("阶段方法互相调用成环")
            return 1
        order.append(leaf)
        left.remove(leaf)
        body = strip_doc(pending[leaf])
        total = 0
        for host in pending:
            if host == leaf:
                continue
            fn = pending[host]
            fn.body, h = inline_into(fn.body, leaf, body)
            total += h
        if total != 1:
            print(f"{leaf} 被调用 {total} 次（应为 1）")
            return 1
    if "--renamed" in sys.argv:
        # 参照提交里的原函数还没改成状态对象：内联后再把 state.x 还原成 x、去掉建状态与补绑定的语句
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import state_rename as sr

        rn, _, _, _ = sr.collect(old[target])
        pending[target] = sr.reverse(pending[target], rn, STATE)
        old[target] = sr.strip_nonlocals(old[target], rn)
    same = ast.dump(pending[target]) == ast.dump(old[target])
    others = [n for n in old if n != target and ast.dump(cur[n]) != ast.dump(old[n])]
    missing = [n for n in old if n not in cur]
    print(f"内联 {len(order)} 个阶段方法；{target}：{'逐字相同' if same else '不同'}")
    if others or missing:
        print("其余方法有变化：", others, "缺失：", missing)
    if not same:
        a = ast.unparse(pending[target]).splitlines()
        b = ast.unparse(old[target]).splitlines()
        import difflib

        for line in list(difflib.unified_diff(b, a, "参照", "内联后", lineterm="", n=2))[:80]:
            print(line)
    return 0 if same and not others and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
