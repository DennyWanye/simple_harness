#!/usr/bin/env python3
"""代码质量整治阶段 6E：把模块里的若干顶层定义原样搬到另一个模块，全仓导入方改到新位置（解环用）。

用法：
    python3 scripts/code_quality/move_defs.py <源模块> <目标模块> 名字1,名字2,... [--doc 新模块文档] [--dry-run]
        # 模块写成点分名（如 deskpet.execution.primary_dependencies），目标模块不存在就新建

两种方向（自动判断，二者只能居其一，否则拒绝——那样搬了也成环）：
- 下沉：搬走的定义不用源模块里留下的定义；源模块其余部分还用它们 → 源模块从目标模块导入。
- 挪出：搬走的定义要用源模块里留下的定义 → 目标模块从源模块导入；源模块其余部分不能再用搬走的名字。
搬走的定义用到的源模块导入，按原样复制到目标模块（相对导入改成绝对写法）。
全仓（backend 下全部 .py，含测试与脚本）：`from 源模块 import 搬走的名字`（绝对或相对写法）改成从目标模块导入；
`"源模块.名字"` 形式的打桩字符串改成 `"目标模块.名字"`；`源模块别名.名字` 形式的属性访问与打桩列出来并拒绝。
核对：搬后每个定义的语法树与搬前逐字相同。收尾只给改前导入排序就合规的文件重排导入（--force-exclude）。
"""

from __future__ import annotations

import argparse
import ast
import builtins
import re
import subprocess
import symtable
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
RUFF = str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")
CFG = str(REPO / "scripts/code_quality/ruff.toml")
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__builtins__"}


def mod_path(mod: str) -> Path:
    p = BACKEND / Path(*mod.split("."))
    return p / "__init__.py" if p.is_dir() else p.with_suffix(".py")


def module_of(path: Path) -> str:
    parts = list(path.relative_to(BACKEND).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def absolute(node: ast.ImportFrom, importer: str, is_pkg: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = importer.split(".")
    if not is_pkg:
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def bound(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in node.names}
    return {
        x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)
    }


def global_loads(nodes: list[ast.stmt]) -> set[str]:
    """这些顶层语句按全局名字读取、自己又没在顶层定义的名字（按作用域，用 symtable）。"""
    src = ast.unparse(ast.Module(body=list(nodes), type_ignores=[]))
    top = symtable.symtable(src, "<b>", "exec")
    out = {s.get_name() for s in top.get_symbols() if s.is_referenced()}

    def visit(t):
        for s in t.get_symbols():
            if s.is_global() and s.is_referenced():
                out.add(s.get_name())
        for c in t.get_children():
            visit(c)

    for c in top.get_children():
        visit(c)
    defined = {
        s.get_name()
        for s in top.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace()
    }
    return out - BUILTINS - defined


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("target")
    ap.add_argument("names")
    ap.add_argument("--doc", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = [x.strip() for x in a.names.split(",") if x.strip()]
    spath, tpath = mod_path(a.source), mod_path(a.target)
    stext = spath.read_text(encoding="utf-8")
    slines = stext.splitlines(keepends=True)
    stree = ast.parse(stext)
    moved = [n for n in stree.body if not isinstance(n, (ast.Import, ast.ImportFrom)) and bound(n) & set(names)]
    for n in moved:
        if not bound(n) <= set(names):
            sys.exit(f"第 {n.lineno} 行的语句还绑定了 {sorted(bound(n) - set(names))}，一并列上")
    if {x for n in moved for x in bound(n)} != set(names):
        sys.exit(f"源模块顶层找不到：{sorted(set(names) - {x for n in moved for x in bound(n)})}")
    moved_ids = {id(n) for n in moved}
    rest = [n for n in stree.body if id(n) not in moved_ids]
    import_of: dict[str, ast.stmt] = {}
    defined_rest: set[str] = set()
    for n in rest:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                import_of[(al.asname or al.name).split(".")[0]] = n
        else:
            defined_rest |= bound(n)
    need = global_loads(moved)
    uses_rest = sorted(need & defined_rest - set(import_of))
    rest_uses_moved = sorted(set(names) & global_loads(rest))
    if uses_rest and rest_uses_moved:
        sys.exit(f"双向依赖：搬走的用到 {uses_rest}，留下的用到 {rest_uses_moved}，搬了也成环")
    direction = "挪出" if uses_rest else "下沉"
    # 目标模块要的导入
    imp: list[str] = []
    for nm in sorted(need & set(import_of)):
        st = import_of[nm]
        for al in st.names:
            if (al.asname or al.name).split(".")[0] != nm:
                continue
            alias = f" as {al.asname}" if al.asname else ""
            if isinstance(st, ast.Import):
                imp.append(f"import {al.name}{alias}")
            else:
                src_mod = absolute(st, a.source, spath.name == "__init__.py")
                if src_mod == "__future__":
                    continue
                imp.append(f"from {src_mod} import {al.name}{alias}")
    if uses_rest:
        imp.append(f"from {a.source} import {', '.join(uses_rest)}")

    def span(n):
        s = min([n.lineno, *[d.lineno for d in getattr(n, "decorator_list", [])]])
        while s > 1 and slines[s - 2].lstrip().startswith("#"):
            s -= 1
        return s, n.end_lineno

    spans = [span(n) for n in moved]
    body = "\n\n".join("".join(slines[s - 1 : e]).rstrip("\n") + "\n" for s, e in spans)
    # 全仓导入方
    edits: dict[Path, list[tuple[ast.ImportFrom, list[str]]]] = defaultdict(list)
    problems = []
    for path in BACKEND.rglob("*.py"):
        if ".venv" in path.parts or path in (spath, tpath):
            continue
        text = path.read_text(encoding="utf-8")
        leaf = a.source.rsplit(".", 1)[-1]
        if leaf not in text:
            continue
        try:
            t = ast.parse(text)
        except SyntaxError:
            continue
        me = module_of(path)
        for n in ast.walk(t):
            if isinstance(n, ast.ImportFrom) and absolute(n, me, path.name == "__init__.py") == a.source:
                hit = [al for al in n.names if al.name in names]
                if not hit:
                    continue
                keep = [al for al in n.names if al.name not in names]
                fmt = lambda al: al.name + (f" as {al.asname}" if al.asname else "")  # noqa: E731
                prefix = "." * n.level + (n.module or "") if n.level else a.source
                stmts = []
                if keep:
                    stmts.append(f"from {prefix} import {', '.join(fmt(x) for x in keep)}")
                stmts.append(f"from {a.target} import {', '.join(fmt(x) for x in hit)}")
                edits[path].append((n, stmts))
        for nm in names:
            if re.search(rf"[\"']{re.escape(a.source)}\.{nm}\b", text):
                edits.setdefault(path, [])
        aliases = {
            al.asname or al.name
            for n in ast.walk(t)
            if isinstance(n, ast.Import)
            for al in n.names
            if al.name == a.source
        } | {
            al.asname or al.name
            for n in ast.walk(t)
            if isinstance(n, ast.ImportFrom)
            for al in n.names
            if f"{absolute(n, me, path.name == '__init__.py')}.{al.name}" == a.source
        }
        for n in ast.walk(t):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in aliases
                and n.attr in names
            ):
                problems.append(f"{path.relative_to(BACKEND)}:{n.lineno} {n.value.id}.{n.attr}")
            if (
                isinstance(n, ast.Call)
                and len(n.args) >= 2
                and isinstance(n.args[0], ast.Name)
                and n.args[0].id in aliases
                and isinstance(n.args[1], ast.Constant)
                and n.args[1].value in names
            ):
                problems.append(f"{path.relative_to(BACKEND)}:{n.lineno} 按别名打桩 {n.args[1].value}")
    if problems:
        print("需要先处理（按模块别名取用或打桩搬走的名字）：")
        for p in sorted(set(problems)):
            print("  " + p)
        return 1
    print(f"{direction}：{len(names)} 个名字；目标模块导入 {len(imp)} 条；改写导入方 {len(edits)} 个文件")
    if a.dry_run:
        print("\n".join(imp))
        for p, items in sorted(edits.items()):
            for n, stmts in items:
                print(f"  {p.relative_to(BACKEND)}:{n.lineno} → {' ; '.join(stmts)}")
        return 0
    clean_before = {
        str(p)
        for p in [*edits, spath, tpath]
        if p.exists()
        and subprocess.run(
            [RUFF, "check", "--no-cache", "--force-exclude", "--config", CFG, "--select", "I", "-q", str(p)],
            cwd=REPO,
            capture_output=True,
        ).returncode
        == 0
    }
    # 目标模块
    if tpath.exists():
        ttext = tpath.read_text(encoding="utf-8")
        ttree = ast.parse(ttext)
        tlines = ttext.splitlines(keepends=True)
        last_imp = max(
            (n.end_lineno for n in ttree.body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0
        )
        tlines[last_imp:last_imp] = [x + "\n" for x in imp]
        new_t = "".join(tlines).rstrip("\n") + "\n\n\n" + body
    else:
        doc = a.doc or f"从 {a.source} 搬出的定义（阶段 6E 解环）。"
        new_t = f'"""{doc}"""\n\nfrom __future__ import annotations\n\n' + "\n".join(imp) + "\n\n\n" + body
    # 源模块：删掉搬走的定义；下沉时补一条从目标导入
    out = list(slines)
    for s, e in sorted(spans, reverse=True):
        while e < len(out) and not out[e].strip():
            e += 1
        del out[s - 1 : e]
    if rest_uses_moved:
        last = max((n.end_lineno for n in rest if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
        out[last:last] = [f"from {a.target} import {', '.join(rest_uses_moved)}\n"]
    new_s = "".join(out)
    # 核对
    for n in moved:
        nn = next(x for x in ast.parse(new_t).body if bound(x) == bound(n) and type(x) is type(n))
        if ast.dump(nn) != ast.dump(n):
            sys.exit(f"{bound(n)} 搬后语法树不同")
    ast.parse(new_s)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(new_t, encoding="utf-8")
    spath.write_text(new_s, encoding="utf-8")
    for path, items in edits.items():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for n, stmts in sorted(items, key=lambda x: -x[0].lineno):
            pad = " " * n.col_offset
            lines[n.lineno - 1 : n.end_lineno] = ["".join(f"{pad}{s}\n" for s in stmts)]
        text = "".join(lines)
        for nm in names:
            text = re.sub(rf"([\"']){re.escape(a.source)}\.{nm}\b", rf"\g<1>{a.target}.{nm}", text)
        path.write_text(text, encoding="utf-8")
    files = [str(p) for p in [*edits, spath, tpath]]
    fix = [f for f in files if f in clean_before or f == str(tpath)]
    if fix:
        subprocess.run([RUFF, "check", "--no-cache", "--force-exclude", "--config", CFG, "--select", "I,F401", "--fix", "-q", *fix], cwd=REPO, check=False)
    subprocess.run([RUFF, "format", "--force-exclude", "--config", CFG, "-q", *files], cwd=REPO, check=False)
    print("完成；搬后定义的语法树与搬前逐字相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
