#!/usr/bin/env python3
"""代码质量整治阶段 5：把一个领域 mixin 的部分方法原样搬进同目录的新 mixin 模块（纯移动）。

用法：
    python3 scripts/code_quality/move_methods.py <源文件> <源类> <新模块名> <新类名> <新类说明> \
        <组合类文件> 方法1 方法2 ... [--dry-run]

规则：
- 方法源码逐字搬走（含紧挨在上方的注释与空行）；新类继承源类的基类（公共基类），`__slots__ = ()`。
- 源文件的模块级名字一律留在源文件；新模块用到时从源文件导入（打桩与测试照旧找源模块）。
- 新模块的导入从源文件导入语句里按用到的名字筛出。
- 两个类之间新出现的跨类调用，按源类所在包的约定把同签名存根写进公共基类的 `if TYPE_CHECKING:` 块；
  存根签名用到、公共基类还没有的名字，从源文件导入语句里筛出，加进公共基类的 TYPE_CHECKING 导入。
- 组合类的基类列表里，新类紧跟在源类后面。
- 收尾跑 ruff 删未用导入、排序、格式化；mypy 与相关测试另行验证。
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_class import RUFF_CONFIG, REPO, Source, bound_names, names_in, render_import  # noqa: E402


def self_calls(code: str) -> set[str]:
    tree = ast.parse(textwrap.dedent(code))
    return {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("self", "cls")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("class_name")
    parser.add_argument("module")
    parser.add_argument("new_class")
    parser.add_argument("doc")
    parser.add_argument("composite")
    parser.add_argument("methods", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.source).resolve()
    src = Source(path, args.class_name)
    body = src.cls.body
    methods = {n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    moving = list(args.methods)
    missing = [m for m in moving if m not in methods]
    if missing:
        sys.exit(f"类里没有方法 {missing}")
    staying = [m for m in methods if m not in moving]
    moved = [n for n in body if getattr(n, "name", None) in moving]
    moved_code = "".join(src.chunk(n, body) for n in moved)
    kept_code = "".join(src.chunk(methods[m], body) for m in staying)

    # 公共基类与它的存根块
    if len(src.cls.bases) != 1:
        sys.exit("源类要恰好一个基类（公共基类）")
    core_name = ast.unparse(src.cls.bases[0])
    core_import = next(
        (s for s, _ in src.imports if any(b == core_name for b, _ in bound_names(s))), None
    )
    assert isinstance(core_import, ast.ImportFrom) and core_import.level == 1, "公共基类要在同一个包里"
    core_path = path.parent / f"{core_import.module}.py"
    core_text = core_path.read_text(encoding="utf-8")
    core_tree = ast.parse(core_text)
    core_cls = next(n for n in core_tree.body if isinstance(n, ast.ClassDef) and n.name == core_name)
    stub_block = next(
        n for n in core_cls.body if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test)
    )
    existing = {n.name for n in stub_block.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    core_methods = {n.name for n in core_cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    need = ((self_calls(moved_code) & set(staying)) | (self_calls(kept_code) & set(moving))) - existing - core_methods

    stubs: list[str] = []
    stub_names: set[str] = set()
    for name in sorted(need):
        node = ast.parse(textwrap.dedent(src.chunk(methods[name], body))).body[0]
        node.body = [ast.Expr(ast.Constant(...))]  # type: ignore[attr-defined]
        text = ast.unparse(node)
        stub_names |= names_in(text)
        stubs.append(text)

    # 新模块
    header = "".join(src.lines[: src.tree.body[0].lineno - 1])
    used = names_in(moved_code)

    def imports_for(names: set[str], *, in_block: bool) -> list[str]:
        out = []
        for stmt, in_checking in src.imports:
            if in_checking != in_block:
                continue
            keep = [alias for bound, alias in bound_names(stmt) if bound in names]
            if keep:
                out.append(render_import(stmt, keep, 0))
        return out

    checking = imports_for(used, in_block=True)
    plain = imports_for(used | {core_name} | ({"TYPE_CHECKING"} if checking else set()), in_block=False)
    helpers = sorted(used & set(src.helpers))
    lines = [*plain]
    if helpers:
        lines.append(f"from .{path.stem} import {', '.join(helpers)}")
    block = "\n".join(lines)
    if checking:
        block += "\n\nif TYPE_CHECKING:\n" + "\n".join("    " + x for x in checking)
    cls_text = f'class {args.new_class}({core_name}):\n    """{args.doc}"""\n\n    __slots__ = ()\n\n' + moved_code
    new_module = path.parent / f"{args.module}.py"
    new_text = header + f'"""{args.doc}"""\n\nfrom __future__ import annotations\n\n' + block + "\n\n\n" + cls_text

    # 源文件删去搬走的方法
    keep_lines = list(src.lines)
    for node in sorted(moved, key=lambda n: n.lineno, reverse=True):
        start, end = src.span(node, body)
        del keep_lines[start:end]
    source_text = "".join(keep_lines)

    # 公共基类加存根与类型检查导入
    core_lines = core_text.splitlines(keepends=True)
    if stubs:
        indent = " " * stub_block.body[0].col_offset
        rendered = "".join(textwrap.indent(s, indent) + "\n" for s in stubs)
        core_lines.insert(stub_block.end_lineno, rendered)  # type: ignore[arg-type]
        core_bound = set()
        for n in ast.walk(core_tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                core_bound |= {b for b, _ in bound_names(n)}
            elif isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n in core_tree.body:
                core_bound.add(n.name)
        extra = imports_for(stub_names - core_bound, in_block=True) + imports_for(
            stub_names - core_bound, in_block=False
        )
        if extra:
            module_checking = next(
                n for n in core_tree.body if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test)
            )
            core_lines.insert(module_checking.end_lineno, "".join(f"    {x}\n" for x in extra))  # type: ignore[arg-type]
    core_out = "".join(core_lines)

    # 组合类：导入并把新类排在源类后面
    comp = Path(args.composite).resolve()
    comp_text = comp.read_text(encoding="utf-8")
    rel = path.parent.relative_to(comp.parent).as_posix().replace("/", ".")
    old_import = f"from .{rel}.{path.stem} import {args.class_name}\n"
    if old_import not in comp_text:
        sys.exit(f"组合类文件里找不到 {old_import!r}")
    comp_text = comp_text.replace(old_import, old_import + f"from .{rel}.{args.module} import {args.new_class}\n", 1)
    comp_text, count = re.subn(
        rf"^(\s+){args.class_name},\n", rf"\g<0>\g<1>{args.new_class},\n", comp_text, count=1, flags=re.M
    )
    if count != 1:
        sys.exit("组合类基类列表里找不到源类")

    print(f"搬走 {len(moving)} 个方法；源类余 {len(staying)} 个；公共基类加存根 {sorted(need)}")
    if args.dry_run:
        return 0
    new_module.write_text(new_text, encoding="utf-8")
    path.write_text(source_text, encoding="utf-8")
    core_path.write_text(core_out, encoding="utf-8")
    comp.write_text(comp_text, encoding="utf-8")
    files = [str(new_module), str(path), str(core_path), str(comp)]
    ruff = [os.environ.get("RUFF") or str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")]
    subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select", "F401,I001", "--fix", "--quiet", *files], check=False)
    subprocess.run([*ruff, "format", "--config", str(RUFF_CONFIG), "--quiet", str(new_module)], check=False)
    res = subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select", "F401,F811,F821,F841", *files],
                         capture_output=True, text=True)
    print(res.stdout[-4000:] or "ruff：无未定义/未使用名字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
