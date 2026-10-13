#!/usr/bin/env python3
"""代码质量整治阶段 3：把函数内导入上提到模块顶部（判不清就不动）。

规则见 plans/2026-10-12-code-quality/阶段3-切片设计.md。
用法：
  python3 scripts/code_quality/lift_imports.py --tree backend --paths deskpet/agent --dry-run
  python3 scripts/code_quality/lift_imports.py --tree sdk --paths agent_orchestrator/planning
--paths 是相对树根的目录或文件，可多个；--kinds 默认 stdlib,internal,third_party。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import CODEC_PINNED, FORBIDDEN_PY  # noqa: E402
from import_contracts import contracts as import_contracts  # noqa: E402
from import_contracts import first_forbidden, matches  # noqa: E402
from import_graph import (  # noqa: E402
    INDEX,
    build_graph,
    module_name,
    reachable,
    resolve,
    top_level_imports,
    with_ancestors,
)
from inner_imports import BACKEND, REPO, SDK_SRC, STDLIB, classify, tree_files  # noqa: E402
from startup_profile import HEAVY  # noqa: E402

LIGHT_THIRD_PARTY = {"httpx", "aiosqlite", "tomlkit", "tomli", "websockets", "yaml", "pydantic",
                     "structlog", "fastapi", "starlette", "anyio", "jsonschema"}
PLATFORM_TEST = re.compile(r"sys\.platform|os\.name|platform\.|IS_WINDOWS|IS_MAC|_is_windows|win32|darwin")
# 可选或重型的标准库：有的 Python 环境没装（tkinter），或会拉起图形库，按需加载
OPTIONAL_STDLIB = {"tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "ensurepip", "venv"}
PLATFORM_STDLIB = {"resource", "fcntl", "termios", "tty", "pty", "pwd", "grp", "crypt", "msvcrt",
                   "winreg", "_winapi", "winsound", "posix", "nt", "syslog", "curses", "readline"}
# 文件里明说"不在模块加载时导入"的，整个文件不动
LAZY_DECLARED = re.compile(r"never import\b[^\n]*\bat module load|not import[^\n]*at module (load|import)|"
                           r"import[^\n]*lazily|lazy import|延迟导入|按需导入|不在模块加载时导入")
LAZY_LINE = re.compile(r"#.*(lazy|lazily|PLC0415|延迟|按需|懒|启动耗时)", re.I)
# 导入语句上方紧挨着的注释（中间只隔导入语句）说明这些依赖要按需加载
LAZY_ABOVE = re.compile(r"#.*(lazy|lazily|PLC0415|延迟|按需|懒|启动耗时|heavy|重型|below the switch)", re.I)
DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
TEST_ROOTS = [BACKEND / "tests", REPO / "sdk/simple-harness-sdk/tests"]
RUFF = REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"


# ---------- 测试里的打桩目标 ----------

def stubbed_modules() -> set[str]:
    """测试里整个换掉的项目模块（往 sys.modules 塞替身）：导入它的地方必须留在函数里，替身才生效。"""
    out: set[str] = set()
    for root in TEST_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "sys.modules" not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            out |= {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in INDEX}
    return out


def patched_targets() -> set[str]:
    """测试里被打桩的 "模块.属性"（字符串路径、setattr/patch.object 的模块别名）。"""
    out: set[str] = set()
    for root in TEST_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            # 打桩路径也可能经参数化等方式传进来：打桩的测试文件里凡是"项目模块.属性"形状的字符串都算
            if "setattr" in text or "patch" in text:
                for n in ast.walk(tree):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and DOTTED.match(n.value) \
                            and n.value.rpartition(".")[0] in INDEX:
                        out.add(n.value)
            aliases: dict[str, str] = {}
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        aliases[a.asname or a.name] = a.name if a.asname else a.name
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    for a in n.names:
                        full = f"{n.module}.{a.name}"
                        if full in INDEX:
                            aliases[a.asname or a.name] = full
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                fname = ast.unparse(n.func)
                if not any(fname.endswith(s) for s in ("setattr", "patch", "patch.object", "delattr")):
                    continue
                args = n.args
                if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) \
                        and "." in args[0].value:
                    out.add(args[0].value)
                if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                    target = ast.unparse(args[0])
                    mod = aliases.get(target.split(".")[0])
                    if mod is not None:
                        rest = target.split(".")[1:]
                        full = ".".join([mod, *rest])
                        out.add(f"{full}.{args[1].value}")
    return out


# ---------- 单个文件的分析 ----------

def bound_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.asname or a.name.split(".")[0] for a in node.names]
    return [a.asname or a.name for a in node.names]


def stores_in(func: ast.AST) -> Counter:
    """函数里（不进内层函数）各名字的绑定次数，含参数、导入、赋值、循环变量等。"""
    c: Counter = Counter()
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = func.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None:
                c[arg.arg] += 1
    stack = list(ast.iter_child_nodes(func))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            c[n.name] += 1
            continue
        if isinstance(n, ast.Lambda):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            c[n.id] += 1
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for nm in bound_names(n):
                c[nm] += 1
        elif isinstance(n, ast.ExceptHandler) and n.name:
            c[n.name] += 1
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            for nm in n.names:
                c[nm] += 100
        stack.extend(ast.iter_child_nodes(n))
    return c


def same_import_count(func: ast.AST, key: str, name: str) -> int:
    """函数里（不进内层函数）以完全相同的导入绑定 name 的次数。"""
    count = 0
    stack = list(ast.iter_child_nodes(func))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for nm, a in zip(bound_names(n), n.names):
                if nm == name and _import_key(n, a) == key:
                    count += 1
        stack.extend(ast.iter_child_nodes(n))
    return count


def module_bindings(tree: ast.Module) -> dict[str, list[str]]:
    """模块顶层各名字 → 绑定它的语句文本（导入语句给出标准化文本）。"""
    out: dict[str, list[str]] = defaultdict(list)

    def walk(body):
        for st in body:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                for nm, a in zip(bound_names(st), st.names):
                    out[nm].append(_import_key(st, a))
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[st.name].append("def")
            elif isinstance(st, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(st, field, []) or [])
                for h in getattr(st, "handlers", []) or []:
                    walk(h.body)
            else:
                for n in ast.walk(st):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        out[n.id].append("assign")

    walk(tree.body)
    return out


def _import_key(st: ast.Import | ast.ImportFrom, alias: ast.alias) -> str:
    if isinstance(st, ast.Import):
        return f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
    mod = "." * st.level + (st.module or "")
    return f"from {mod} import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")


def global_declared(tree: ast.Module) -> set[str]:
    return {nm for n in ast.walk(tree) if isinstance(n, ast.Global) for nm in n.names}


_TOP_NAMES: dict[str, set[str]] = {}


def _top_names(mod: str) -> set[str]:
    if mod not in _TOP_NAMES:
        try:
            tree = ast.parse(INDEX[mod].read_text(encoding="utf-8"))
        except (KeyError, SyntaxError):
            _TOP_NAMES[mod] = set()
        else:
            _TOP_NAMES[mod] = set(module_bindings(tree))
    return _TOP_NAMES[mod]


def missing_target(st: ast.Import | ast.ImportFrom, me: str, path: Path) -> str:
    """内部导入的目标模块或名字不存在时返回其描述，否则返回空串。"""
    if isinstance(st, ast.Import):
        for a in st.names:
            if a.name not in INDEX:
                return a.name
        return ""
    if st.level:
        pkg = me if path.name == "__init__.py" else me.rpartition(".")[0]
        for _ in range(st.level - 1):
            pkg = pkg.rpartition(".")[0]
        base = f"{pkg}.{st.module}" if st.module else pkg
    else:
        base = st.module or ""
    if base not in INDEX:
        return base
    names = _top_names(base)
    for a in st.names:
        if a.name not in names and f"{base}.{a.name}" not in INDEX:
            # 包用 __getattr__ 按需提供名字时判不清，也按"不存在"处理，不上提
            return f"{base}.{a.name}" + ("（包靠 __getattr__ 提供）" if "__getattr__" in names else "")
    return ""


_HEAVY_ROOTS: dict[str, set[str]] = {}


def _heavy_roots(mod: str) -> set[str]:
    """模块顶层直接导入的重型第三方包。"""
    if mod not in _HEAVY_ROOTS:
        roots = set()
        for node in top_level_imports(INDEX[mod]):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif not node.level and node.module:
                roots.add(node.module.split(".")[0])
        _HEAVY_ROOTS[mod] = roots & set(HEAVY)
    return _HEAVY_ROOTS[mod]


def heavy_reach(graph, starts) -> set[str]:
    """从 starts 沿顶层依赖会加载的重型包。"""
    seen = set(starts)
    todo = list(starts)
    out: set[str] = set()
    while todo:
        v = todo.pop()
        if v in INDEX:
            out |= _heavy_roots(v)
        for w in graph.get(v, ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return out


def lazy_comment_above(lines: list[str], i: int) -> bool:
    """从第 i 行（0 起）往上，跨过空行与导入语句，看紧挨着的注释是否声明按需导入。"""
    while i >= 0:
        text = lines[i].strip()
        if text.startswith("#"):
            if LAZY_ABOVE.search(text):
                return True
        elif not (text.startswith(("import ", "from ")) or text == "" or text.endswith((",", "(", ")"))
                  or re.match(r"^[\w ,]+,?$", text)):
            return False
        i -= 1
    return False


def stubbed_target(st, me: str, path: Path, stubbed: set[str]) -> str:
    if isinstance(st, ast.Import):
        mods = {a.name for a in st.names}
    else:
        base = absolute_base(st, me, path)
        mods = {base} | {f"{base}.{a.name}" for a in st.names}
    hit = sorted(mods & stubbed)
    return hit[0] if hit else ""


def absolute_base(st: ast.ImportFrom, me: str, path: Path) -> str:
    if not st.level:
        return st.module or ""
    pkg = me if path.name == "__init__.py" else me.rpartition(".")[0]
    for _ in range(st.level - 1):
        pkg = pkg.rpartition(".")[0]
    return f"{pkg}.{st.module}" if st.module else pkg


def identifiers(tree: ast.Module) -> set[str]:
    """文件里出现过的全部名字（变量、参数、定义、导入绑定）。"""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update(bound_names(n))
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
    return out


def _bindings_in(func: ast.AST, name: str, key: str) -> int:
    """函数整棵子树里（含内层作用域）绑定 name 的次数，不算与 key 完全相同的导入。"""
    count = 0
    for n in ast.walk(func):
        if n is func:
            continue
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, (ast.Store, ast.Del)):
            count += 1
        elif isinstance(n, ast.arg) and n.arg == name:
            count += 1
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            count += 1
        elif isinstance(n, ast.ExceptHandler) and n.name == name:
            count += 1
        elif isinstance(n, (ast.Global, ast.Nonlocal)) and name in n.names:
            count += 1
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for nm, a in zip(bound_names(n), n.names):
                if nm == name and _import_key(n, a) != key:
                    count += 1
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = func.args
        count += sum(1 for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
                     if x is not None and x.arg == name)
    return count


def attr_plan(st: ast.ImportFrom, base: str, func: ast.AST, tree: ast.Module,
              file_idents: set[str], top_imports: set[str], lifted_names: dict[str, str]):
    """模块属性方案；做不了时返回原因（字符串）。"""
    if base not in INDEX:
        return "模块不在项目内"
    if any(f"{base}.{a.name}" in INDEX for a in st.names):
        return "导入的是子模块"
    parent, _, leaf = base.rpartition(".")
    text = f"from {parent} import {leaf}" if parent else f"import {leaf}"
    if st.level and st.module:  # 原句是相对导入时，新句也用相对写法
        head, _, tail = st.module.rpartition(".")
        text = f"from {'.' * st.level}{head} import {tail}"
    if leaf in file_idents and text not in top_imports:
        return f"模块别名 {leaf} 在文件里另有用处"
    if lifted_names.get(leaf, text) != text:
        return f"模块别名 {leaf} 被别的导入占用"
    edits = []
    for nm, a in zip(bound_names(st), st.names):
        if _bindings_in(func, nm, _import_key(st, a)):
            return f"名字 {nm} 在函数里另有绑定"
        for n in ast.walk(func):
            if isinstance(n, ast.Name) and n.id == nm and isinstance(n.ctx, ast.Load):
                edits.append((n, f"{leaf}.{a.name}"))
    return text, leaf, edits


def is_forbidden(rel_repo: str) -> bool:
    return bool(FORBIDDEN_PY.search(rel_repo)) or rel_repo in CODEC_PINNED


# ---------- 主流程 ----------

def isolation_broken(st, kind: str, me: str, path: Path, graph, contracts) -> str | None:
    """上提后，哪个入口会在加载时拉进被禁模块；不会则返回 None。"""
    if kind == "internal":
        targets = resolve(st, me, path)
    else:
        targets = {(st.module if isinstance(st, ast.ImportFrom) else a.name) for a in st.names}
    for c in contracts:
        if not (me in c.entries or any(reachable(graph, e, me) for e in with_ancestors(c.entries))):
            continue
        hit = first_forbidden(graph, targets, c.forbidden) if kind == "internal" else \
            next((t for t in targets if matches(t, c.forbidden)), None)
        if hit:
            return f"{c.test} 不许加载 {hit}"
    return None


def analyse_file(path: Path, me: str, graph, patched: set[str], kinds: set[str], contracts=(),
                 stubbed=frozenset()):
    src = path.read_text(encoding="utf-8")
    src_lines = src.splitlines()
    tree = ast.parse(src)
    mod_binds = module_bindings(tree)
    globals_ = global_declared(tree)
    decisions = []  # (node, verdict, reason, func_chain)
    lifted_names: dict[str, str] = {}
    plans: dict[int, tuple] = {}  # 模块属性方案：id(导入语句) → (顶层导入文本, 模块别名, [(名字节点, 替换文本)])
    file_idents = identifiers(tree)
    top_imports = {ast.unparse(x) for x in tree.body if isinstance(x, (ast.Import, ast.ImportFrom))}

    def visit(node: ast.AST, stack: list[ast.AST], funcs: list[ast.AST]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)) and funcs:
                decisions.append(judge(child, stack + [node], funcs))
            new_funcs = funcs + [child] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else funcs
            visit(child, stack + [node], new_funcs)

    def judge(st, ancestors, funcs):
        kind = classify(st, ancestors)
        if kind not in kinds:
            return st, "keep", kind, funcs
        line = src_lines[st.lineno - 1] if st.lineno - 1 < len(src_lines) else ""
        if LAZY_LINE.search(line):
            return st, "keep", "本行注释声明按需导入", funcs
        if lazy_comment_above(src_lines, st.lineno - 2):
            return st, "keep", "上方注释声明按需导入", funcs
        stub = stubbed_target(st, me, path, stubbed)
        if stub:
            return st, "keep", f"测试换掉了模块 {stub}", funcs
        if isinstance(st, ast.ImportFrom) and any(a.name == "*" for a in st.names):
            return st, "keep", "星号导入", funcs
        inner = ancestors[ancestors.index(funcs[0]):]
        conds = [a for a in inner if isinstance(a, ast.If)]
        if any(PLATFORM_TEST.search(ast.unparse(c.test)) for c in conds):
            return st, "keep", "平台条件导入", funcs
        if kind == "stdlib":
            root = (st.module if isinstance(st, ast.ImportFrom) else st.names[0].name).split(".")[0]
            if root in PLATFORM_STDLIB:
                return st, "keep", "平台专用标准库", funcs
            if root in OPTIONAL_STDLIB:
                return st, "keep", "可选或重型标准库", funcs
        if conds and kind == "third_party":
            return st, "keep", "第三方条件导入", funcs
        if kind == "third_party":
            root = (st.module if isinstance(st, ast.ImportFrom) else st.names[0].name).split(".")[0]
            if root not in LIGHT_THIRD_PARTY:
                return st, "keep", "第三方（非轻量清单）", funcs
            if any(isinstance(a, ast.Try) for a in inner):
                return st, "keep", "第三方在 try 里", funcs
        # 打桩目标：上提后名字在导入时就绑定，打桩会静默失效；改成顶层导入模块、调用处用"模块.名字"，
        # 每次调用仍按属性取值，打桩照样生效
        verdict = "lift"
        if isinstance(st, ast.ImportFrom) and st.module is not None:
            base = absolute_base(st, me, path)
            hit = next((a.name for a in st.names if f"{base}.{a.name}" in patched), None)
            if hit is not None:
                plan = attr_plan(st, base, funcs[-1], tree, file_idents, top_imports, lifted_names)
                if isinstance(plan, str):
                    return st, "keep", f"测试对 {base}.{hit} 打桩，{plan}", funcs
                verdict = "attr"
        # 名字冲突
        for nm, a in zip(bound_names(st), st.names) if verdict == "lift" else ():
            key = _import_key(st, a)
            existing = mod_binds.get(nm, [])
            if existing and any(e != key for e in existing):
                return st, "keep", f"名字 {nm} 在模块顶层另有绑定", funcs
            if nm in globals_:
                return st, "keep", f"名字 {nm} 被 global 声明", funcs
            for f in funcs:
                others = stores_in(f)[nm] - same_import_count(f, key, nm)
                if others > 0:
                    return st, "keep", f"名字 {nm} 在函数里另有绑定", funcs
            prev = lifted_names.get(nm)
            if prev is not None and prev != key:
                return st, "keep", f"名字 {nm} 被不同导入共用", funcs
        # 目标必须存在：函数内导入不存在的模块或名字，说明那条路径已是死代码，上提会让整个模块导入失败
        if kind == "internal":
            missing = missing_target(st, me, path)
            if missing:
                return st, "keep", f"目标不存在：{missing}", funcs
        # 重型包：内部模块顶层导入了重型第三方包（语音识别、大模型分词等），上提会让它们提前加载
        if kind == "internal":
            heavy = heavy_reach(graph, resolve(st, me, path)) - heavy_reach(graph, with_ancestors({me}))
            if heavy:
                return st, "keep", f"会提前加载重型包 {', '.join(sorted(heavy))}", funcs
        # 导入隔离：测试钉住某入口加载时不得拉进某些模块，上提不能让入口经本模块走到它们
        broken = isolation_broken(st, kind, me, path, graph, contracts)
        if broken:
            return st, "keep", f"会破坏导入隔离（{broken}）", funcs
        # 循环
        if kind == "internal":
            targets = resolve(st, me, path)
            bad = [t for t in targets if reachable(graph, t, me)]
            if bad:
                return st, "keep", "会成环", funcs
            graph[me] |= targets
        if verdict == "attr":
            plans[id(st)] = plan
            lifted_names[plan[1]] = plan[0]
            return st, "attr", "测试打桩，改成模块属性访问", funcs
        for nm, a in zip(bound_names(st), st.names):
            lifted_names[nm] = _import_key(st, a)
        return st, "lift", kind, funcs

    visit(tree, [], [])
    return src, tree, decisions, plans


def _abs_key(st: ast.Import | ast.ImportFrom, alias: ast.alias, me: str, path: Path) -> str:
    base = absolute_base(st, me, path) if isinstance(st, ast.ImportFrom) else ""
    return f"{base}:{alias.name}:{alias.asname}"


def rewrite(path: Path, me: str, src: str, tree: ast.Module, decisions, plans) -> int:
    lift = [d for d in decisions if d[1] in ("lift", "attr")]
    if not lift:
        return 0
    lines = src.splitlines(keepends=True)
    existing_top = {ast.unparse(st) for st in tree.body if isinstance(st, (ast.Import, ast.ImportFrom))}
    # 模块顶层（不含 TYPE_CHECKING 块）已有的同一导入：函数内重复导入的名字不再新加
    top_keys = {_abs_key(st, a, me, path) for st in tree.body if isinstance(st, (ast.Import, ast.ImportFrom))
                for a in st.names}
    new_imports: list[str] = []
    for st, verdict, *_ in lift:
        if verdict == "attr":
            text = plans[id(st)][0]
        else:
            names = [a for a in st.names if _abs_key(st, a, me, path) not in top_keys]
            if not names:
                continue
            node = ast.ImportFrom(st.module, names, st.level) if isinstance(st, ast.ImportFrom) else ast.Import(names)
            text = ast.unparse(node)
        if text not in existing_top and text not in new_imports:
            new_imports.append(text)
    # 模块属性方案：名字改成"模块.名字"（列号是 UTF-8 字节偏移，从后往前改）
    edits = {id(n): (n, r) for st, verdict, *_ in lift if verdict == "attr" for n, r in plans[id(st)][2]}
    for n, r in sorted(edits.values(), key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True):
        raw = lines[n.lineno - 1].encode("utf-8")
        lines[n.lineno - 1] = (raw[: n.col_offset] + r.encode("utf-8") + raw[n.end_col_offset :]).decode("utf-8")
    # 删除函数内导入（从后往前）；若它是所在块唯一语句，换成 pass
    parents: dict[int, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p
    for st, *_ in sorted(lift, key=lambda d: -d[0].lineno):
        parent = parents[id(st)]
        block = None
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(parent, field, None)
            if isinstance(seq, list) and st in seq:
                block = seq
        if block is None and isinstance(parent, ast.ExceptHandler):
            block = parent.body
        indent = " " * st.col_offset
        lone = block is not None and len([s for s in block if not _lifted(s, lift)]) == 0 and block[0] is st
        replacement = [f"{indent}pass\n"] if lone else []
        lines[st.lineno - 1 : st.end_lineno] = replacement
    # 插到开头的导入块之后
    insert_at = _header_end(tree)
    text = "".join(lines)
    out_lines = text.splitlines(keepends=True)
    out_lines[insert_at:insert_at] = [i + "\n" for i in new_imports]
    path.write_text("".join(out_lines), encoding="utf-8")
    return len(lift)


def _lifted(st, lift) -> bool:
    return any(st is d[0] for d in lift)


def _header_end(tree: ast.Module) -> int:
    """开头导入块的末行号（插入点，0 起）：跳过文档字符串、__future__ 与连续的导入语句。"""
    end = 0
    for i, st in enumerate(tree.body):
        if i == 0 and isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
            end = st.end_lineno
            continue
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            end = st.end_lineno
            continue
        break
    return end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", choices=["backend", "sdk"], required=True)
    ap.add_argument("--paths", nargs="+", required=True)
    ap.add_argument("--kinds", default="stdlib,internal,third_party")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    base = BACKEND if a.tree == "backend" else SDK_SRC
    kinds = set(a.kinds.split(","))
    wanted = [(base / p).resolve() for p in a.paths]
    files = [p for p in tree_files(a.tree)
             if any(p.resolve() == w or w in p.resolve().parents for w in wanted)]
    graph = build_graph()
    patched = patched_targets()
    contracts = import_contracts(a.tree)
    stubbed = stubbed_modules()
    stats: Counter = Counter()
    changed: list[Path] = []
    for path in files:
        rel_repo = str(path.relative_to(REPO))
        if is_forbidden(rel_repo) or "/scripts/" in f"/{path.relative_to(base)}" or path.relative_to(base).parts[0] == "scripts":
            continue
        try:
            me = module_name(path)
        except KeyError:
            continue
        if LAZY_DECLARED.search(path.read_text(encoding="utf-8")):
            stats["keep:文件声明按需导入"] += 1
            continue
        src, tree, decisions, plans = analyse_file(path, me, graph, patched, kinds, contracts, stubbed)
        for st, verdict, reason, _f in decisions:
            stats[f"{verdict}:{reason}"] += 1
            if a.verbose:
                print(f"{verdict} {path.relative_to(base)}:{st.lineno} {reason} {ast.unparse(st)}")
        if not a.dry_run and rewrite(path, me, src, tree, decisions, plans):
            changed.append(path)
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"{v:5d} {k}")
    if changed:
        rels = [str(p) for p in changed]
        # I001 排序；SIM102：去掉导入后外层 if 只剩一个内层 if，合并成一个（安全修复，不改行为）
        subprocess.run([str(RUFF), "check", "--config", str(REPO / "scripts/code_quality/ruff.toml"),
                        "--select", "I001,SIM102", "--fix", "-q", *rels], check=False)
        subprocess.run([str(RUFF), "format", "--config", str(REPO / "scripts/code_quality/ruff.toml"),
                        "-q", *rels], check=False)
        print(f"改写文件 {len(changed)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
