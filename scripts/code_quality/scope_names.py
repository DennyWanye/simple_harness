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


def _terminates(stmts: list[ast.stmt]) -> bool:
    """语句列表一定不会正常走完（以 return/raise/continue/break 结尾，或两边都这样的 if）。"""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and bool(last.orelse) and _terminates(last.orelse)
    return False


def definitely_assigned(stmts: list[ast.stmt], initial: set[str]) -> set[str]:
    """顺序执行完 stmts（正常走完）后一定已赋值的名字（保守：循环体、try 体里的赋值不算）。"""
    have = set(initial)
    for s in stmts:
        if isinstance(s, (ast.Assign, ast.AnnAssign)):
            if isinstance(s, ast.AnnAssign) and s.value is None:
                continue
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            for t in targets:
                have |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(s, (ast.Import, ast.ImportFrom)):
            have |= {(a.asname or a.name).split(".")[0] for a in s.names}
        elif isinstance(s, DEFS):
            have.add(s.name)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                if item.optional_vars is not None:
                    have |= {n.id for n in ast.walk(item.optional_vars) if isinstance(n, ast.Name)}
            have = definitely_assigned(s.body, have)
        elif isinstance(s, ast.If):
            paths = [
                definitely_assigned(branch, have)
                for branch in (s.body, s.orelse)
                if not _terminates(branch)
            ]
            if paths:
                have = set.intersection(*paths)
        elif isinstance(s, (ast.Try, ast.TryStar)):
            paths = []
            if not _terminates(s.body + s.orelse):
                paths.append(definitely_assigned(s.body + s.orelse, have))
            for h in s.handlers:
                if not _terminates(h.body):
                    paths.append(
                        definitely_assigned(h.body, have | ({h.name} if h.name else set()))
                        - ({h.name} if h.name else set())
                    )
            if paths:
                have = set.intersection(*paths)
            have = definitely_assigned(s.finalbody, have)
        elif isinstance(s, ast.Match):
            paths = [definitely_assigned(c.body, have) for c in s.cases if not _terminates(c.body)]
            wildcard = any(
                isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None
                for c in s.cases
            )
            if not wildcard:
                paths.append(set(have))
            if paths:
                have = set.intersection(*paths)
        # 循环（可能一次都不执行）、表达式语句等：不增加
    return have


def write_first(block: list[ast.stmt], name: str) -> bool:
    """名字在段里是"先无条件赋值、之后才读"：段顶层某条语句无条件给它赋值，
    之前的语句都没提到它，且这条语句的右边不读它。只有这样才能不当参数传入。"""
    for s in block:
        mentioned = name in names([s], ast.Load) or name in names([s], ast.Store)
        if not mentioned:
            continue
        if isinstance(s, (ast.Assign, ast.AnnAssign)) and getattr(s, "value", None) is not None:
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            stored = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            return name in stored and name not in names([s.value], ast.Load)
        if isinstance(s, (ast.Import, ast.ImportFrom)) or (isinstance(s, DEFS) and s.name == name):
            return name not in names([s], ast.Load)
        return False
    return False


def locally_bound(block: list[ast.stmt], name: str) -> bool:
    """名字是段内自己的：段里每一处读之前，沿同一条执行路径一定已经有段内的赋值
    （普通赋值、for 循环变量、with ... as、except ... as、import、def 都算）。
    这样的名字不用（也不该）当参数传入；只要有一处读可能看到段前的值，就必须传入。"""

    def reads(node) -> bool:
        return name in names([node], ast.Load)

    def walk(stmts: list[ast.stmt], bound: bool) -> tuple[bool, bool]:
        """返回（这些语句里的读是否都已被段内赋值覆盖，走完后是否一定已赋值）。"""
        for st in stmts:
            if bound:
                continue
            if (
                isinstance(st, (ast.Assign, ast.AnnAssign))
                and getattr(st, "value", None) is not None
            ):
                targets = st.targets if isinstance(st, ast.Assign) else [st.target]
                if reads(st.value) or any(reads(t) for t in targets):
                    return False, False
                if name in {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}:
                    bound = True
                continue
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                if name in {(a.asname or a.name).split(".")[0] for a in st.names}:
                    bound = True
                continue
            if isinstance(st, DEFS):
                if reads(st):
                    return False, False
                if st.name == name:
                    bound = True
                continue
            if isinstance(st, (ast.For, ast.AsyncFor)):
                if reads(st.iter):
                    return False, False
                binds = name in {n.id for n in ast.walk(st.target) if isinstance(n, ast.Name)}
                ok, _ = walk(st.body, binds)
                ok2, _ = walk(st.orelse, False)
                if not (ok and ok2):
                    return False, False
                continue  # 循环可能一次都不执行：走完不算已赋值
            if isinstance(st, ast.While):
                if reads(st.test):
                    return False, False
                ok, _ = walk(st.body, False)
                ok2, _ = walk(st.orelse, False)
                if not (ok and ok2):
                    return False, False
                continue
            if isinstance(st, (ast.With, ast.AsyncWith)):
                if any(reads(item.context_expr) for item in st.items):
                    return False, False
                binds = any(
                    item.optional_vars is not None
                    and name
                    in {n.id for n in ast.walk(item.optional_vars) if isinstance(n, ast.Name)}
                    for item in st.items
                )
                ok, after = walk(st.body, binds)
                if not ok:
                    return False, False
                bound = after
                continue
            if isinstance(st, ast.If):
                if reads(st.test):
                    return False, False
                ok1, a1 = walk(st.body, False)
                ok2, a2 = walk(st.orelse, False)
                if not (ok1 and ok2):
                    return False, False
                bound = (a1 or _terminates(st.body)) and (
                    a2 or (bool(st.orelse) and _terminates(st.orelse))
                )
                continue
            if isinstance(st, (ast.Try, ast.TryStar)):
                ok, a_body = walk(st.body + st.orelse, False)
                if not ok:
                    return False, False
                after = [a_body or _terminates(st.body + st.orelse)]
                for h in st.handlers:
                    if h.type is not None and reads(h.type):
                        return False, False
                    okh, ah = walk(h.body, h.name == name)
                    if not okh:
                        return False, False
                    after.append(ah or _terminates(h.body))
                okf, af = walk(st.finalbody, False)
                if not okf:
                    return False, False
                bound = all(after) or af
                continue
            if isinstance(st, ast.Match):
                if reads(st.subject):
                    return False, False
                for c in st.cases:
                    caps = {
                        n.name
                        for n in ast.walk(c.pattern)
                        if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name
                    }
                    okc, _ = walk(c.body, name in caps)
                    if not okc or (c.guard is not None and reads(c.guard) and name not in caps):
                        return False, False
                continue
            # 其余语句（表达式、return、raise、增量赋值、del 等）：读到就是没覆盖
            if reads(st) or isinstance(st, ast.AugAssign) and name in names([st], ast.Store):
                return False, False
        return True, bound

    if name not in names(block, ast.Load):
        return name in names(block, ast.Store)
    return walk(block, False)[0]


def defined_before(fn: ast.AST, start: int) -> set[str]:
    """从函数开头走到第 start 行那条语句之前，一定已赋值的名字（参数、前面无条件的赋值、
    外层 for 的循环变量、with ... as、except ... as）。调用处传的参数必须都在里面，
    否则走到某条路时，调用本身就会报 UnboundLocalError（原代码在那条路上可能根本不读它）。"""
    a = fn.args  # type: ignore[attr-defined]
    have = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    have |= {x.arg for x in (a.vararg, a.kwarg) if x}

    def descend(body: list[ast.stmt], have: set[str]) -> set[str] | None:
        for i, st in enumerate(body):
            if st.lineno == start or (
                getattr(st, "decorator_list", None)
                and st.decorator_list[0].lineno <= start <= st.lineno
            ):
                return definitely_assigned(body[:i], have)
            if st.lineno < start <= st.end_lineno and not isinstance(st, DEFS):
                before = definitely_assigned(body[:i], have)
                parts: list[tuple[list[ast.stmt], set[str]]] = []
                if isinstance(st, (ast.For, ast.AsyncFor)):
                    parts += [
                        (
                            st.body,
                            before | {n.id for n in ast.walk(st.target) if isinstance(n, ast.Name)},
                        ),
                        (st.orelse, before),
                    ]
                elif isinstance(st, (ast.With, ast.AsyncWith)):
                    bound = {
                        n.id
                        for item in st.items
                        if item.optional_vars is not None
                        for n in ast.walk(item.optional_vars)
                        if isinstance(n, ast.Name)
                    }
                    parts.append((st.body, before | bound))
                elif isinstance(st, (ast.Try, ast.TryStar)):
                    parts += [(st.body, before), (st.orelse, definitely_assigned(st.body, before))]
                    parts += [
                        (h.body, before | ({h.name} if h.name else set())) for h in st.handlers
                    ]
                    parts.append((st.finalbody, before))
                elif isinstance(st, ast.Match):
                    parts += [
                        (
                            c.body,
                            before
                            | {
                                n.name
                                for n in ast.walk(c.pattern)
                                if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name
                            },
                        )
                        for c in st.cases
                    ]
                else:  # if / while
                    parts += [
                        (getattr(st, "body", []), before),
                        (getattr(st, "orelse", []), before),
                    ]
                for sub, h in parts:
                    if sub and sub[0].lineno <= start <= sub[-1].end_lineno:
                        got = descend(sub, h)
                        if got is not None:
                            return got
                return None
        return None

    return descend(fn.body, have) or set()  # type: ignore[attr-defined]


def closure_factory_problem(fn: ast.AST, block: list[ast.stmt], end: int) -> str | None:
    """段里定义、段后还要用的内层函数（闭包）抽出后变成"工厂函数返回闭包"，闭包读的外层变量从
    "调用时读外层"变成"工厂调用那一刻的参数值"。只有外层在定义之后不再改写这些变量时才等价。
    有问题时返回原因，没问题返回 None。"""
    # 段里所有最外层的函数、类、lambda（不论在第几层语句里）都可能被带出段外，一并按闭包查
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    defs = []
    stack = list(block)
    while stack:
        n = stack.pop()
        if isinstance(n, kinds):
            defs.append(n)
            continue
        stack.extend(ast.iter_child_nodes(n))
    if not defs:
        return None
    start = block[0].lineno
    for loop in ast.walk(fn):
        if (
            isinstance(loop, (ast.For, ast.AsyncFor, ast.While))
            and loop.lineno < start <= loop.end_lineno
        ):
            return "闭包定义在循环里（每轮一个闭包，晚绑定语义会变）"
    # 注意传 fn.body：names([fn]) 只会得到函数名本身（内层作用域不算），闭包检查就形同虚设
    host_locals = names(fn.body, ast.Store) | {
        a.arg
        for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)  # type: ignore[attr-defined]
    }
    free = names(defs, ast.Load) & host_locals
    after = [s for s in ast.walk(fn) if isinstance(s, ast.stmt) and s.lineno > end]
    rebound = sorted(free & names(after, ast.Store))
    if rebound:
        return f"闭包读的 {rebound} 在定义之后还会被改写"
    later = sorted(
        free
        - defined_before(fn, start)
        - names(block, ast.Store)
        - {d.name for d in defs if not isinstance(d, ast.Lambda)}
    )
    if later:
        return f"闭包读的 {later} 在定义处不一定已赋值"
    return None
