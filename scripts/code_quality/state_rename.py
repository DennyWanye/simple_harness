#!/usr/bin/env python3
"""代码质量整治阶段 6B：把一个超长函数的局部变量（含参数）改成一个运行状态对象的属性，为切段做准备。

用法：
    python3 scripts/code_quality/state_rename.py <文件> <类名.方法名> [--state state] [--cls _RunState]
        [--check]   # 只核对：把 state.X 还原成 X、去掉建状态那一句后，语法树与 HEAD 的函数逐字相同

规则：
- 改的是函数自己作用域里的局部变量与参数（self/cls 除外）；lambda 参数、推导式变量、异常名字
  （`except E as e`，Python 退出处理块就删掉它）不改。
- 函数体开头插一句 `state = _RunState(参数=参数, ...)`；模块里在类前面生成 `_RunState`
  数据类，字段是全部被改名的变量（`Any = None`）。
- 函数里不许有 nonlocal / global / 海象赋值 / del / 内部 def（遇到就拒绝）。
- 只按语法树位置替换名字记号，字符串与注释原样不动。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

SCOPES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def find_func(tree: ast.Module, qual: str) -> tuple[ast.ClassDef, ast.AsyncFunctionDef | ast.FunctionDef]:
    cls_name, _, fn_name = qual.partition(".")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    fn = next(
        n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name
    )
    return cls, fn


def scope_bound(node: ast.AST) -> set[str]:
    """嵌套作用域（lambda / 推导式）自己绑定的名字。"""
    if isinstance(node, ast.Lambda):
        a = node.args
        params = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
        return {p.arg for p in params}
    bound: set[str] = set()
    for gen in node.generators:  # type: ignore[attr-defined]
        bound |= {n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
    return bound


def collect(fn: ast.AST) -> tuple[set[str], list[ast.Name]]:
    """返回（要改名的名字，要替换的 Name 节点）。"""
    for n in ast.walk(fn):
        if isinstance(n, (ast.Nonlocal, ast.Global, ast.NamedExpr, ast.Delete)) or (
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn
        ):
            sys.exit(f"拒绝：函数里有 {type(n).__name__}（第 {n.lineno} 行）")
    a = fn.args  # type: ignore[attr-defined]
    params = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]]
    except_names = {n.name for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.name}
    targets: set[str] = set()
    nodes: list[ast.Name] = []

    def visit(node: ast.AST, shadow: frozenset[str], in_nested: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                inner = shadow | frozenset(scope_bound(child))
                # 推导式第一个迭代器在外层作用域求值；lambda 默认值也在外层求值
                if isinstance(child, ast.Lambda):
                    for d in child.args.defaults + [d for d in child.args.kw_defaults if d]:
                        visit_expr(d, shadow, in_nested)
                    visit_expr(child.body, inner, True)
                else:
                    first = child.generators[0]  # type: ignore[attr-defined]
                    visit_expr(first.iter, shadow, in_nested)
                    for i, gen in enumerate(child.generators):  # type: ignore[attr-defined]
                        if i:
                            visit_expr(gen.iter, inner, True)
                        for cond in gen.ifs:
                            visit_expr(cond, inner, True)
                    for part in ("elt", "key", "value"):
                        if getattr(child, part, None) is not None:
                            visit_expr(getattr(child, part), inner, True)
                continue
            if isinstance(child, ast.Name):
                if child.id in shadow:
                    continue
                if isinstance(child.ctx, ast.Store) and in_nested:
                    continue
                handle(child)
                continue
            visit(child, shadow, in_nested)

    def visit_expr(expr: ast.AST, shadow: frozenset[str], in_nested: bool) -> None:
        if isinstance(expr, ast.Name):
            if expr.id not in shadow:
                handle(expr)
            return
        holder = ast.Module(body=[ast.Expr(expr)], type_ignores=[])
        visit(holder, shadow, in_nested)

    def handle(name: ast.Name) -> None:
        nodes.append(name)

    for stmt in fn.body:  # type: ignore[attr-defined]
        visit(ast.Module(body=[stmt], type_ignores=[]), frozenset(), False)
    assigned = {n.id for n in nodes if isinstance(n.ctx, ast.Store)}
    targets = (assigned | set(params)) - except_names - {"self", "cls"}
    nodes = [n for n in nodes if n.id in targets]
    return targets, nodes


def reverse(fn: ast.AST, names: set[str], state: str) -> ast.AST:
    class Back(ast.NodeTransformer):
        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            self.generic_visit(node)
            if isinstance(node.value, ast.Name) and node.value.id == state and node.attr in names:
                return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
            return node

        def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
            self.generic_visit(node)
            if isinstance(node.target, ast.Name):
                node.simple = 1
            return node

    fn = Back().visit(fn)
    body = fn.body  # type: ignore[attr-defined]
    fn.body = [  # type: ignore[attr-defined]
        s
        for s in body
        if not (
            isinstance(s, ast.Assign)
            and isinstance(s.targets[0], ast.Name)
            and s.targets[0].id == state
        )
    ]
    return fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("qual")
    ap.add_argument("--state", default="state")
    ap.add_argument("--cls", default="_RunState")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    if a.check:
        head = subprocess.run(["git", "show", f"HEAD:./{path.name}"], cwd=path.parent, capture_output=True, text=True).stdout
        _, old = find_func(ast.parse(head), a.qual)
        _, new = find_func(ast.parse(path.read_text()), a.qual)
        names, _ = collect(old)
        back = reverse(new, names, a.state)
        same = ast.dump(back, include_attributes=False) == ast.dump(old, include_attributes=False)
        print("改名核对：" + ("逐字相同" if same else "不同"))
        return 0 if same else 1
    src = path.read_text()
    tree = ast.parse(src)
    cls, fn = find_func(tree, a.qual)
    names, nodes = collect(fn)
    if a.state in {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}:
        sys.exit(f"函数里已有名字 {a.state}")
    lines = src.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset), reverse=True):
        assert node.lineno == node.end_lineno
        line = lines[node.lineno - 1]
        b = line.encode("utf-8")
        seg = b[node.col_offset : node.end_col_offset].decode("utf-8")
        assert seg == node.id, (node.lineno, seg, node.id)
        lines[node.lineno - 1] = (
            b[: node.col_offset] + f"{a.state}.{node.id}".encode("utf-8") + b[node.end_col_offset :]
        ).decode("utf-8")
    args = fn.args
    params = [p.arg for p in args.posonlyargs + args.args + args.kwonlyargs if p.arg not in ("self", "cls")]
    params += [x.arg for x in (args.vararg, args.kwarg) if x]
    first = fn.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    anchor = fn.body[1] if has_doc else first
    indent = " " * anchor.col_offset
    init = f"{indent}{a.state} = {a.cls}({', '.join(f'{p}={p}' for p in params)})\n"
    lines.insert(anchor.lineno - 1, init)
    fields = "".join(f"    {n}: Any = None\n" for n in sorted(names))
    cls_text = (
        f"@dataclass\nclass {a.cls}:\n"
        f'    """{cls.name}.{fn.name} 的运行状态：原函数的局部变量与参数（阶段 6B 切段用）。"""\n\n{fields}\n\n'
    )
    lines.insert(cls.lineno - 1 - len(cls.decorator_list), cls_text)
    path.write_text("".join(lines))
    print(f"改名 {len(names)} 个变量、{len(nodes)} 处；插入 {a.cls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
