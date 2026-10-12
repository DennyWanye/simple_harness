#!/usr/bin/env python3
"""代码质量整治：把模块里的顶层代码块原样搬到新模块（阶段 2 拆主入口的搬家工具）。

用法：
    python3 scripts/code_quality/move_block.py <源文件> <目标文件> <起始行-结束行>[,<起始行-结束行>...]
        [--doc "目标模块文档字符串"] [--dry-run]

规则：
- 行范围必须恰好覆盖若干条完整的顶层语句（含紧挨在上方的注释行）。
- 搬走的代码用到的名字：来自源文件导入的，按原导入语句复制到目标文件；
  来自源文件里其他顶层定义的，视为循环依赖，拒绝执行并列出名字（先搬它们，或一起搬）。
- 源文件在第一个范围的位置插入 `from <目标模块> import ...`，导入搬走代码定义、且源文件
  其余部分仍在用的名字。导入期副作用的先后顺序保持不变。
- 搬走后源文件里不再使用的导入名字，从源文件删除。
- 收尾请跑 ruff format；导入烟雾、启动快照、禁区比对验证。
"""
from __future__ import annotations

import argparse
import ast
import builtins
import os
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__builtins__"}


def bound_names(node: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            out.add((a.asname or a.name).split(".")[0])
        return out
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            out.add(x.id)
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(x.name)
        if isinstance(x, (ast.Import, ast.ImportFrom)):
            for a in x.names:
                out.add((a.asname or a.name).split(".")[0])
    return out


def free_loads(nodes: list[ast.stmt]) -> set[str]:
    """块里读取、但块内没有绑定的名字（粗略：不区分函数局部，靠"块内绑定"排除）。"""
    loads: set[str] = set()
    local: set[str] = set()
    for n in nodes:
        local |= bound_names(n)
        for x in ast.walk(n):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                loads.add(x.id)
            if isinstance(x, ast.arg):
                local.add(x.arg)
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for y in ast.walk(x):
                    if isinstance(y, ast.Name) and isinstance(y.ctx, ast.Store):
                        local.add(y.id)
            if isinstance(x, ast.comprehension):
                for y in ast.walk(x.target):
                    if isinstance(y, ast.Name):
                        local.add(y.id)
            if isinstance(x, ast.ExceptHandler) and x.name:
                local.add(x.name)
    return loads - local - BUILTINS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("target")
    ap.add_argument("ranges")
    ap.add_argument("--doc", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src_path, dst_path = Path(a.source), Path(a.target)
    src = src_path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    ranges = [tuple(int(x) for x in r.split("-")) for r in a.ranges.split(",")]

    moved: list[ast.stmt] = []
    for st in tree.body:
        start = st.lineno
        if hasattr(st, "decorator_list") and st.decorator_list:
            start = min(d.lineno for d in st.decorator_list)
        inside = any(r0 <= start and st.end_lineno <= r1 for r0, r1 in ranges)
        overlap = any(not (st.end_lineno < r0 or start > r1) for r0, r1 in ranges)
        if overlap and not inside:
            raise SystemExit(f"范围切开了第 {start}-{st.end_lineno} 行的语句")
        if inside:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                raise SystemExit(f"范围里有导入语句（第 {st.lineno} 行），请把范围调到导入之后")
            moved.append(st)
    if not moved:
        raise SystemExit("范围里没有完整的顶层语句")
    moved_ids = {id(x) for x in moved}
    rest = [st for st in tree.body if id(st) not in moved_ids]

    # 源文件里各名字的来源
    import_of: dict[str, ast.stmt] = {}
    defined_in_rest: dict[str, int] = {}
    for st in rest:
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            for al in st.names:
                import_of[(al.asname or al.name).split(".")[0]] = st
        else:
            for nm in bound_names(st):
                defined_in_rest.setdefault(nm, st.lineno)
        # try/if 里的导入也算
        if isinstance(st, (ast.Try, ast.If)):
            for x in ast.walk(st):
                if isinstance(x, (ast.Import, ast.ImportFrom)):
                    for al in x.names:
                        import_of.setdefault((al.asname or al.name).split(".")[0], x)

    needed = free_loads(moved)
    cyc = sorted(n for n in needed if n not in import_of and n in defined_in_rest)
    if cyc:
        print("搬走的代码依赖源文件里留下的定义（会循环导入）：")
        for n in cyc:
            print(f"  {n}（第 {defined_in_rest[n]} 行）")
        return 2
    unknown = sorted(n for n in needed if n not in import_of and n not in defined_in_rest)
    if unknown:
        print("警告：这些名字既不是导入也不是源文件定义（可能是运行期动态名字）：", ", ".join(unknown))

    # 目标文件的导入：逐个名字复制原导入
    imp_lines: list[str] = []
    for n in sorted(needed & set(import_of)):
        st = import_of[n]
        for al in st.names:
            if (al.asname or al.name).split(".")[0] != n:
                continue
            alias = f" as {al.asname}" if al.asname else ""
            if isinstance(st, ast.Import):
                imp_lines.append(f"import {al.name}{alias}")
            else:
                mod = "." * st.level + (st.module or "")
                imp_lines.append(f"from {mod} import {al.name}{alias}")
    imp_lines = sorted(set(imp_lines))

    # 搬走的文本（连同紧挨在上方的注释）
    def span(st: ast.stmt) -> tuple[int, int]:
        start = st.lineno
        if hasattr(st, "decorator_list") and st.decorator_list:
            start = min(d.lineno for d in st.decorator_list)
        while start > 1 and lines[start - 2].lstrip().startswith("#"):
            start -= 1
        return start, st.end_lineno

    spans = [span(st) for st in moved]
    # 相邻语句之间只隔空行的，按原文整段复制（保留原有空行）
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and all(not lines[i - 1].strip() for i in range(merged[-1][1] + 1, s)):
            merged[-1][1] = e
        else:
            merged.append([s, e])
    body_text = "\n\n".join("".join(lines[s - 1:e]).rstrip("\n") + "\n" for s, e in merged)

    exported = sorted({nm for st in moved for nm in bound_names(st)})
    rest_loads = set()
    for st in rest:
        for x in ast.walk(st):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                rest_loads.add(x.id)
    test_refs = tests_referencing(src_path, exported)
    still_used = [n for n in exported if n in rest_loads or n in test_refs]
    if test_refs - rest_loads:
        print("只被测试通过源模块取用的名字（暂留在源文件导入里，阶段收尾改测试）：",
              ", ".join(sorted(test_refs - rest_loads)))

    module = ".".join(dst_path.with_suffix("").relative_to(src_path.parent).parts)
    doc = a.doc or f"从 {src_path.name} 搬出的代码。"
    dst_text = f'"""{doc}"""\n\nfrom __future__ import annotations\n\n' + "\n".join(imp_lines) + "\n\n\n" + body_text
    import_stmt = ""
    if still_used:
        # isort: split 让导入排序不把这条并进上方导入块，导入期副作用的先后保持原样
        import_stmt = ("# isort: split\n" + f"from {module} import (\n"
                       + "".join(f"    {n},\n" for n in still_used) + ")\n# isort: split\n")

    out: list[str] = []
    first = spans[0][0]
    drop = {i for s, e in merged for i in range(s, e + 1)}
    for i, ln in enumerate(lines, 1):
        if i == first:
            out.append(import_stmt)
        if i in drop:
            continue
        out.append(ln)
    new_src = "".join(out)

    # 源文件里搬走后不再使用的导入名字
    new_tree = ast.parse(new_src)
    used_after = {x.id for x in ast.walk(new_tree) if isinstance(x, ast.Name)}
    removable = sorted(n for n in (needed & set(import_of)) if n not in used_after)

    print(f"搬走 {len(moved)} 条顶层语句、{sum(e - s + 1 for s, e in spans)} 行 → {dst_path}")
    print(f"目标文件导入 {len(imp_lines)} 条；源文件新增导入 {len(still_used)} 个名字")
    if removable:
        print("源文件里只被搬走代码用到的导入（已删除）：", ", ".join(removable))
    if a.dry_run:
        return 0
    if dst_path.exists():
        raise SystemExit(f"目标文件已存在：{dst_path}")
    dst_path.write_text(dst_text, encoding="utf-8")
    src_path.write_text(remove_import_names(new_src, set(removable)), encoding="utf-8")
    return 0


def tests_referencing(src_path: Path, names: list[str]) -> set[str]:
    import re
    mod = src_path.stem
    found: set[str] = set()
    tests = src_path.parent / "tests"
    if not tests.exists():
        return found
    pat = re.compile(rf"\b{mod}\.(\w+)|from {mod} import ([^\n]+)")
    wanted = set(names)
    for p in tests.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in pat.finditer(text):
            if m.group(1) in wanted:
                found.add(m.group(1))
            if m.group(2):
                for part in m.group(2).replace("(", "").replace(")", "").split(","):
                    nm = part.strip().split(" as ")[0]
                    if nm in wanted:
                        found.add(nm)
    return found


def remove_import_names(src: str, names: set[str]) -> str:
    if not names:
        return src
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []  # (起始行, 结束行, 新文本)
    for st in tree.body:
        if not isinstance(st, (ast.Import, ast.ImportFrom)):
            continue
        keep = [al for al in st.names if (al.asname or al.name).split(".")[0] not in names]
        if len(keep) == len(st.names):
            continue
        indent = lines[st.lineno - 1][: st.col_offset]
        if not keep:
            new = ""
        elif isinstance(st, ast.Import):
            new = indent + "import " + ", ".join(
                al.name + (f" as {al.asname}" if al.asname else "") for al in keep) + "\n"
        else:
            mod = "." * st.level + (st.module or "")
            new = indent + f"from {mod} import (\n" + "".join(
                f"{indent}    {al.name}{' as ' + al.asname if al.asname else ''},\n" for al in keep) + indent + ")\n"
        edits.append((st.lineno, st.end_lineno, new))
    for s, e, new in sorted(edits, reverse=True):
        lines[s - 1:e] = [new]
    return "".join(lines)


if __name__ == "__main__":
    sys.exit(main())
