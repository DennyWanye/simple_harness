#!/usr/bin/env python3
"""代码质量整治阶段 6B/6D：把一个超长函数的局部变量（含参数）改成一个运行状态对象的属性，为切段做准备。

用法：
    python3 scripts/code_quality/state_rename.py <文件> <函数名或 类名.方法名> [--line 起始行]
        [--state state] [--cls _RunState]
        [--check 参照提交]   # 只核对：把 state.X 还原成 X、去掉建状态与补绑定的语句后，
                             # 语法树与参照提交里的同名函数逐字相同

规则：
- 改的是函数自己作用域里的局部变量与参数（self/cls 除外）；异常名字（`except E as e`）不改。
- 闭包（函数里的 def / lambda / 推导式）里读写的、按 Python 作用域规则属于本函数的名字，也改成
  `state.X`：闭包在调用时读 `state.X`，与原来读闭包单元的晚绑定语义一致；闭包里对这些名字的
  `nonlocal` 声明随之去掉。
- 函数里 `def f` / `class C` / `import y` 绑定的名字：语句本身照旧（仍绑定局部名），紧跟一句
  `state.f = f`，其余所有读写都改成 `state.f`。
- 函数体开头插一句 `state = _RunState(参数=参数, ...)`；模块里在函数（或类）前面生成 `_RunState`
  数据类，字段是全部被改名的变量（`Any = None`）。
- 函数里不许有 global / 海象赋值 / del / locals()、vars()、eval、exec、dir（遇到就拒绝）。
- 只按语法树位置替换名字记号，字符串与注释原样不动。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def find_func(tree: ast.Module, qual: str, line: int | None = None):
    """返回（所在类或 None，函数）。"""
    if "." in qual:
        cls_name, _, fn_name = qual.partition(".")
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        cands = [n for n in cls.body if isinstance(n, FUNCS) and n.name == fn_name]
    else:
        cls = None
        cands = [n for n in tree.body if isinstance(n, FUNCS) and n.name == qual]
    if line is not None:
        cands = [n for n in cands if n.lineno == line]
    if len(cands) != 1:
        sys.exit(f"找不到唯一的函数 {qual}")
    return cls, cands[0]


def _args(node) -> list[str]:
    a = node.args
    return [
        x.arg
        for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, *(y for y in (a.vararg, a.kwarg) if y))
    ]


class Scope:
    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        self.locals: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()


def own_nodes(scope_node):
    """作用域自己的节点：不进内层作用域，但产出内层作用域节点本身。"""
    if isinstance(scope_node, ast.Lambda):
        roots = [scope_node.body]
    elif isinstance(scope_node, COMPS):
        # 第一个 for 的可迭代对象在外层求值，不算推导式自己的
        g0 = scope_node.generators[0]
        roots = [g0.target, *g0.ifs]
        for g in scope_node.generators[1:]:
            roots += [g.target, g.iter, *g.ifs]
        roots += [
            getattr(scope_node, p)
            for p in ("elt", "key", "value")
            if getattr(scope_node, p, None) is not None
        ]
    else:
        roots = list(scope_node.body)
    stack = list(reversed(roots))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (*FUNCS, ast.Lambda, ast.ClassDef, *COMPS)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(n))))


def build_scopes(fn) -> tuple[dict[int, Scope], list[tuple[ast.Name, Scope]]]:
    """建作用域树，返回（节点 id → 作用域，全部名字记号及其所在作用域）。"""
    scopes: dict[int, Scope] = {}
    names: list[tuple[ast.Name, Scope]] = []

    def visit(node, parent):
        sc = Scope(node, parent)
        scopes[id(node)] = sc
        if isinstance(node, (*FUNCS, ast.Lambda)):
            sc.locals |= set(_args(node))
        if isinstance(node, COMPS):
            for g in node.generators:
                sc.locals |= {t.id for t in ast.walk(g.target) if isinstance(t, ast.Name)}
        inner = []
        for n in own_nodes(node):
            if isinstance(n, ast.Global):
                sc.globals |= set(n.names)
            elif isinstance(n, ast.Nonlocal):
                sc.nonlocals |= set(n.names)
            elif isinstance(n, ast.Name):
                names.append((n, sc))
                if isinstance(n.ctx, (ast.Store, ast.Del)):
                    sc.locals.add(n.id)
            elif isinstance(n, (*FUNCS, ast.ClassDef)):
                sc.locals.add(n.name)
                inner.append(n)
            elif isinstance(n, (ast.Lambda, *COMPS)):
                inner.append(n)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                sc.locals |= {(a.asname or a.name).split(".")[0] for a in n.names}
            elif isinstance(n, ast.ExceptHandler) and n.name:
                sc.locals.add(n.name)
        # 内层作用域：函数的默认值、装饰器、注解在外层求值
        for n in inner:
            if isinstance(n, (*FUNCS, ast.Lambda)):
                outer_parts = [*n.args.defaults, *[d for d in n.args.kw_defaults if d]]
                if isinstance(n, FUNCS):
                    outer_parts += list(n.decorator_list)
                for part in outer_parts:
                    for x in ast.walk(part):
                        if isinstance(x, ast.Name):
                            names.append((x, sc))
            if isinstance(n, ast.ClassDef):
                # 类的基类、关键字参数、装饰器在外层求值（类体内的名字归类作用域）
                for part in [*n.bases, *[k.value for k in n.keywords], *n.decorator_list]:
                    for x in ast.walk(part):
                        if isinstance(x, ast.Name):
                            names.append((x, sc))
            if isinstance(n, COMPS):
                # 第一个 for 的可迭代对象在外层求值
                for x in ast.walk(n.generators[0].iter):
                    if isinstance(x, ast.Name):
                        names.append((x, sc))
            visit(n, sc)
        sc.locals -= sc.globals | sc.nonlocals

    visit(fn, None)
    return scopes, names


def resolve(name: str, sc: Scope) -> Scope | None:
    if name in sc.globals:
        return None
    cur = sc.parent if name in sc.nonlocals else sc
    while cur is not None:
        if isinstance(cur.node, ast.ClassDef) and cur is not sc:
            cur = cur.parent
            continue
        if name in cur.locals:
            return cur
        cur = cur.parent
    return None


def collect(fn):
    """返回（要改名的名字，要替换的 Name 节点，要补 state.x = x 的绑定语句，nonlocal 语句及其要去掉的名字）。"""
    fn_own = list(own_nodes(fn))
    for n in ast.walk(fn):
        if isinstance(n, ast.Delete):
            sys.exit(f"拒绝：函数里有 del（第 {n.lineno} 行）")
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in ("locals", "vars", "eval", "exec", "dir")
        ):
            sys.exit(f"拒绝：第 {n.lineno} 行用 {n.func.id}() 动态取局部变量，改名后会读不到")
    for n in fn_own:
        if isinstance(n, ast.Global):
            sys.exit(f"拒绝：函数里有 global（第 {n.lineno} 行）")
    scopes, name_nodes = build_scopes(fn)
    root = scopes[id(fn)]
    except_names = {n.name for n in fn_own if isinstance(n, ast.ExceptHandler) and n.name}
    targets = (root.locals - except_names) - {"self", "cls"}
    nodes = [n for n, sc in name_nodes if n.id in targets and resolve(n.id, sc) is root]
    walrus = [n for n in ast.walk(fn) if isinstance(n, ast.NamedExpr)]
    for w in walrus:
        if any(w.target is n for n in nodes):
            sys.exit(f"拒绝：第 {w.lineno} 行海象赋值的目标是本函数的变量（不能写成属性）")
    binders = [
        n
        for n in fn_own
        if (isinstance(n, (*FUNCS, ast.ClassDef)) and n.name in targets)
        or (
            isinstance(n, (ast.Import, ast.ImportFrom))
            and any((a.asname or a.name).split(".")[0] in targets for a in n.names)
        )
    ]
    nonlocal_edits = []
    for sc in scopes.values():
        for st in own_nodes(sc.node) if sc.node is not fn else []:
            if isinstance(st, ast.Nonlocal):
                drop = [nm for nm in st.names if resolve(nm, sc) is root]
                if drop:
                    nonlocal_edits.append((st, drop))
    return targets, nodes, binders, nonlocal_edits


def bound_by(stmt) -> list[str]:
    if isinstance(stmt, (*FUNCS, ast.ClassDef)):
        return [stmt.name]
    return [(a.asname or a.name).split(".")[0] for a in stmt.names]


def reverse(fn, names: set[str], state: str):
    class Back(ast.NodeTransformer):
        def visit_Attribute(self, node):
            self.generic_visit(node)
            if isinstance(node.value, ast.Name) and node.value.id == state and node.attr in names:
                return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
            return node

        def visit_AnnAssign(self, node):
            self.generic_visit(node)
            if isinstance(node.target, ast.Name):
                node.simple = 1
            return node

    fn = Back().visit(fn)

    def clean(body):
        out = []
        for s in body:
            if (
                isinstance(s, ast.Assign)
                and isinstance(s.targets[0], ast.Name)
                and s.targets[0].id == state
            ):
                continue
            # 补的绑定：x = x（还原后）
            if (
                isinstance(s, ast.Assign)
                and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)
                and isinstance(s.value, ast.Name)
                and s.targets[0].id == s.value.id
                and s.value.id in names
            ):
                continue
            out.append(s)
        return out

    for n in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            v = getattr(n, field, None)
            if isinstance(v, list) and v and isinstance(v[0], ast.stmt):
                setattr(n, field, clean(v))
    return fn


def strip_nonlocals(fn, names: set[str]):
    for n in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            v = getattr(n, field, None)
            if isinstance(v, list) and v and isinstance(v[0], ast.stmt):
                kept = []
                for s in v:
                    if isinstance(s, ast.Nonlocal):
                        rest = [x for x in s.names if x not in names]
                        if not rest:
                            continue
                        s.names = rest
                    kept.append(s)
                setattr(n, field, kept)
    return fn


def ensure_imports(text: str) -> str:
    """状态数据类要用 dataclass 与 Any：模块里没导入就补（放在 __future__ 导入之后，ruff 再排序）。"""
    tree = ast.parse(text)
    have: set[str] = set()
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module in ("dataclasses", "typing"):
            have |= {f"{n.module}.{a.name}" for a in n.names if a.asname is None}
    need = []
    if "dataclasses.dataclass" not in have:
        need.append("from dataclasses import dataclass\n")
    if "typing.Any" not in have:
        need.append("from typing import Any\n")
    if not need:
        return text
    lines = text.splitlines(keepends=True)
    future = next(
        (n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"), None
    )
    first_import = next((n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))), None)
    at = (
        future.end_lineno
        if future is not None
        else (first_import.lineno - 1 if first_import else 0)
    )
    lines[at:at] = need
    return "".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("qual")
    ap.add_argument("--line", type=int)
    ap.add_argument("--state", default="state")
    ap.add_argument("--cls", default="_RunState")
    ap.add_argument("--check")
    a = ap.parse_args()
    path = Path(a.file)
    if a.check:
        rel = subprocess.run(
            ["git", "ls-files", "--full-name", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "show", f"{a.check}:{rel}"], cwd=path.parent, capture_output=True, text=True
        ).stdout
        _, old = find_func(ast.parse(head), a.qual)
        _, new = find_func(ast.parse(path.read_text()), a.qual)
        names, _, _, _ = collect(old)
        back = reverse(new, names, a.state)
        old = strip_nonlocals(old, names)
        same = ast.dump(back) == ast.dump(old)
        print("改名核对：" + ("逐字相同" if same else "不同"))
        if not same:
            import difflib

            for ln in list(
                difflib.unified_diff(
                    ast.unparse(old).splitlines(),
                    ast.unparse(back).splitlines(),
                    "参照",
                    "还原后",
                    lineterm="",
                    n=1,
                )
            )[:40]:
                print(ln)
        return 0 if same else 1
    src = path.read_text()
    tree = ast.parse(src)
    cls, fn = find_func(tree, a.qual, a.line)
    names, nodes, binders, nonlocal_edits = collect(fn)
    if a.state in {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}:
        sys.exit(f"函数里已有名字 {a.state}")
    lines = src.splitlines(keepends=True)
    # 编辑按位置从后往前：名字替换、绑定后补一句、nonlocal 清理
    edits: list[tuple[int, int, int, str]] = []  # (行, 起列, 止列, 新文本)；止列 -1 表示整行替换
    for node in nodes:
        assert node.lineno == node.end_lineno
        edits.append((node.lineno, node.col_offset, node.end_col_offset, f"{a.state}.{node.id}"))
    inserts: list[tuple[int, str]] = []  # (在第几行之后插入, 文本)
    for b in binders:
        pad = " " * b.col_offset
        text = "".join(f"{pad}{a.state}.{nm} = {nm}\n" for nm in bound_by(b) if nm in names)
        inserts.append((b.end_lineno, text))
    for st, drop in nonlocal_edits:
        rest = [x for x in st.names if x not in drop]
        pad = " " * st.col_offset
        new_line = f"{pad}nonlocal {', '.join(rest)}\n" if rest else ""
        edits.append((st.lineno, 0, -1, new_line))
    for ln, c0, c1, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if c1 == -1:
            lines[ln - 1] = text
            continue
        b = lines[ln - 1].encode("utf-8")
        assert b[c0:c1].decode("utf-8").isidentifier(), (ln, b[c0:c1])
        lines[ln - 1] = (b[:c0] + text.encode("utf-8") + b[c1:]).decode("utf-8")
    for after, text in sorted(inserts, reverse=True):
        lines.insert(after, text)
    params = [p for p in _args(fn) if p not in ("self", "cls")]
    first = fn.body[0]
    has_doc = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    anchor = fn.body[1] if has_doc else first
    indent = " " * anchor.col_offset
    # 插入点在 anchor 原来的行号处；前面的插入都在 anchor 之后，行号不受影响
    init = f"{indent}{a.state} = {a.cls}({', '.join(f'{p}={p}' for p in params)})\n"
    lines.insert(anchor.lineno - 1, init)
    fields = "".join(f"    {n}: Any = None\n" for n in sorted(names))
    owner = cls if cls is not None else fn
    doc = f"{(cls.name + '.') if cls else ''}{fn.name}"
    cls_text = (
        f"@dataclass\nclass {a.cls}:\n"
        f'    """{doc} 的运行状态：原函数的局部变量与参数（切段用）。"""\n\n{fields}\n\n'
    )
    deco = owner.decorator_list[0].lineno if owner.decorator_list else owner.lineno
    lines.insert(deco - 1, cls_text)
    new_text = "".join(lines)
    new_text = ensure_imports(new_text)
    path.write_text(new_text)
    print(f"改名 {len(names)} 个变量、{len(nodes)} 处，补绑定 {len(binders)} 处；插入 {a.cls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
