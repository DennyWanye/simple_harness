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
from import_graph import INDEX, build_graph, module_name, reachable, resolve  # noqa: E402
from inner_imports import BACKEND, REPO, SDK_SRC, STDLIB, classify, tree_files  # noqa: E402

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
LAZY_LINE = re.compile(r"#.*(lazy|lazily|PLC0415|延迟|按需|懒加载|启动耗时)", re.I)
TEST_ROOTS = [BACKEND / "tests", REPO / "sdk/simple-harness-sdk/tests"]
RUFF = REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"


# ---------- 测试里的打桩目标 ----------

def patched_targets() -> set[str]:
    """测试里被打桩的 "模块.属性"（字符串路径、setattr/patch.object 的模块别名）。"""
    out: set[str] = set()
    for root in TEST_ROOTS:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
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
        if a.name not in names and f"{base}.{a.name}" not in INDEX and "__getattr__" not in names:
            return f"{base}.{a.name}"
    return ""


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
        if not (me in c.entries or any(reachable(graph, e, me) for e in c.entries)):
            continue
        hit = first_forbidden(graph, targets, c.forbidden) if kind == "internal" else \
            next((t for t in targets if matches(t, c.forbidden)), None)
        if hit:
            return f"{c.test} 不许加载 {hit}"
    return None


def analyse_file(path: Path, me: str, graph, patched: set[str], kinds: set[str], contracts=()):
    src = path.read_text(encoding="utf-8")
    src_lines = src.splitlines()
    tree = ast.parse(src)
    mod_binds = module_bindings(tree)
    globals_ = global_declared(tree)
    decisions = []  # (node, verdict, reason, func_chain)
    lifted_names: dict[str, str] = {}

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
        # 打桩目标
        if isinstance(st, ast.ImportFrom) and st.module is not None:
            base = st.module
            if st.level:
                targets = resolve(st, me, path)
                base = max(targets, key=len) if targets else base
            for a in st.names:
                if f"{base}.{a.name}" in patched:
                    return st, "keep", f"测试对 {base}.{a.name} 打桩", funcs
        # 名字冲突
        for nm, a in zip(bound_names(st), st.names):
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
        for nm, a in zip(bound_names(st), st.names):
            lifted_names[nm] = _import_key(st, a)
        return st, "lift", kind, funcs

    visit(tree, [], [])
    return src, tree, decisions


def rewrite(path: Path, src: str, tree: ast.Module, decisions) -> int:
    lift = [d for d in decisions if d[1] == "lift"]
    if not lift:
        return 0
    lines = src.splitlines(keepends=True)
    existing_top = {ast.unparse(st) for st in tree.body if isinstance(st, (ast.Import, ast.ImportFrom))}
    new_imports: list[str] = []
    for st, *_ in lift:
        text = ast.unparse(st)
        if text not in existing_top and text not in new_imports:
            new_imports.append(text)
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
        src, tree, decisions = analyse_file(path, me, graph, patched, kinds, contracts)
        for st, verdict, reason, _f in decisions:
            stats[f"{verdict}:{reason}"] += 1
            if a.verbose:
                print(f"{verdict} {path.relative_to(base)}:{st.lineno} {reason} {ast.unparse(st)}")
        if not a.dry_run and rewrite(path, src, tree, decisions):
            changed.append(path)
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"{v:5d} {k}")
    if changed:
        rels = [str(p) for p in changed]
        subprocess.run([str(RUFF), "check", "--config", str(REPO / "scripts/code_quality/ruff.toml"),
                        "--select", "I001", "--fix", "-q", *rels], check=False)
        subprocess.run([str(RUFF), "format", "--config", str(REPO / "scripts/code_quality/ruff.toml"),
                        "-q", *rels], check=False)
        print(f"改写文件 {len(changed)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
