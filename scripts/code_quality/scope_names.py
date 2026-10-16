"""代码质量整治：按作用域取一段语句读写了哪些名字（plan_extract 与 extract_method 共用）。

规则：
- 推导式的循环变量只在推导式里有效，不算外层的读写；推导式第一个 for 的可迭代对象在外层求值，照常算；
- lambda 的形参同理；默认值在外层求值；
- 内层函数与类：名字本身是外层的写；装饰器与默认值在外层求值；函数体里读到、又不是它自己的
  形参或局部变量的名字，算外层的读（闭包）；
- except ... as 名字、函数里的 import 都算写。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def name_events(nodes, ctx) -> Iterator[tuple[str, tuple[int, int]]]:
    """按作用域产出 (名字, 位置)。位置用于判断段里先读还是先写。"""

    def visit(node, hidden: frozenset[str]):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ctx) and node.id not in hidden:
                yield node.id, (node.lineno, node.col_offset)
            return
        if ctx is ast.Store and isinstance(node, ast.ExceptHandler) and node.name:
            yield node.name, (node.lineno, node.col_offset)
        if isinstance(node, COMPS):
            bound = frozenset(
                t.id for g in node.generators for t in ast.walk(g.target) if isinstance(t, ast.Name)
            )
            yield from visit(node.generators[0].iter, hidden)
            inner = hidden | bound
            for i, g in enumerate(node.generators):
                if i:
                    yield from visit(g.iter, inner)
                for cond in g.ifs:
                    yield from visit(cond, inner)
            for part in ("elt", "key", "value"):
                if getattr(node, part, None) is not None:
                    yield from visit(getattr(node, part), inner)
            return
        if isinstance(node, ast.Lambda):
            bound = frozenset(
                a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            )
            for d in (*node.args.defaults, *[d for d in node.args.kw_defaults if d]):
                yield from visit(d, hidden)
            yield from visit(node.body, hidden | bound)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if ctx is ast.Store:
                for a in node.names:
                    yield (a.asname or a.name).split(".")[0], (node.lineno, node.col_offset)
            return
        if isinstance(node, DEFS):
            if ctx is ast.Store:
                yield node.name, (node.lineno, node.col_offset)
                return
            for d in node.decorator_list:
                yield from visit(d, hidden)
            args = getattr(node, "args", None)
            own: set[str] = set()
            if args is not None:
                own = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
                own |= {a.arg for a in (args.vararg, args.kwarg) if a}
                for d in (*args.defaults, *[d for d in args.kw_defaults if d]):
                    yield from visit(d, hidden)
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    own.add(n.id)
            for n in ast.walk(node):
                if (
                    isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load)
                    and n.id not in hidden | own
                ):
                    yield n.id, (n.lineno, n.col_offset)
            return
        for c in ast.iter_child_nodes(node):
            yield from visit(c, hidden)

    for node in nodes:
        yield from visit(node, frozenset())


def names(nodes, ctx) -> set[str]:
    return {name for name, _ in name_events(nodes, ctx)}


def first_seen(block: list[ast.stmt], name: str, ctx: type) -> tuple[int, int]:
    """名字在段里第一次读（或写）的位置。赋值语句先求右边再写左边：左边目标的写入时刻
    算在整条语句末尾。"""
    target_end: dict[tuple[int, int], tuple[int, int]] = {}
    for s in block:
        for st in ast.walk(s):
            if isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                for tg in targets:
                    for n in ast.walk(tg):
                        if isinstance(n, ast.Name):
                            target_end[(n.lineno, n.col_offset)] = (st.end_lineno, 10**6)
    spots = [
        target_end.get(pos, pos) if ctx is ast.Store else pos
        for n, pos in name_events(block, ctx)
        if n == name
    ]
    return min(spots) if spots else (10**9, 0)
