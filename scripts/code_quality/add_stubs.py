#!/usr/bin/env python3
"""代码质量整治阶段 5E：给混入类补"由组合类提供"的类型存根（只在 TYPE_CHECKING 下生效）。

用法：python3 scripts/code_quality/add_stubs.py <目标文件> <目标类> <定义文件>:<类>.<方法或属性> ...
- 方法：照抄定义处的签名（装饰器里只保留 staticmethod/classmethod/property），函数体写 `...`；
- 属性：写 `<名字>: <类型>`，类型取定义处 `self.<名字>: T = …` 的注解，没有注解时写 Any；
- 存根写进目标类开头的 `if TYPE_CHECKING:` 块（没有就新建）；签名里用到、目标模块又没有的名字，
  按定义模块的导入方式补进目标模块顶层的 `if TYPE_CHECKING:` 块（相对导入换算成目标模块的写法）。
运行期 AST 不变：`ast_equiv.py --typing-only` 会去掉全部 TYPE_CHECKING 块再比较。
"""

from __future__ import annotations

import ast
import builtins
import os
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

SRC = Path(__file__).resolve().parents[2] / "sdk/simple-harness-sdk/src"
KEEP_DECORATORS = {"staticmethod", "classmethod", "property"}


def module_of(path: Path) -> str:
    rel = path.resolve().relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def absolute(module: str, node: ast.ImportFrom, is_pkg: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def import_map(path: Path) -> dict[str, tuple[str, str]]:
    """名字 -> (绝对模块, 原名)；只收 from 导入与模块级定义。"""
    tree = ast.parse(path.read_text())
    mod = module_of(path)
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            src = absolute(mod, node, path.name == "__init__.py")
            for a in node.names:
                out[a.asname or a.name] = (src, a.name)
    for node in tree.body:
        name = getattr(node, "name", None)
        if name:
            out.setdefault(name, (mod, name))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, (mod, t.id))
    return out


def relative(target_mod: str, mod: str) -> str:
    """同一顶层包里换成相对导入（与 SDK 的写法一致）。"""
    t, m = target_mod.split("."), mod.split(".")
    if t[0] != m[0]:
        return mod
    pkg = t[:-1]
    common = 0
    while common < min(len(pkg), len(m)) and pkg[common] == m[common]:
        common += 1
    return "." * (len(pkg) - common + 1) + ".".join(m[common:])


def names_in(node: ast.AST) -> set[str]:
    found = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):  # 字符串注解
            try:
                found |= names_in(ast.parse(n.value, mode="eval"))
            except SyntaxError:
                pass
    return found


def find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"找不到类 {name}")


def stub_for(def_file: Path, cls: str, member: str) -> tuple[str, set[str]]:
    tree = ast.parse(def_file.read_text())
    klass = find_class(tree, cls)
    for node in klass.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == member:
            stub = ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=[ast.Expr(ast.Constant(...))],
                decorator_list=[d for d in node.decorator_list if ast.unparse(d) in KEEP_DECORATORS],
                returns=node.returns,
                type_params=[],
            ) if isinstance(node, ast.FunctionDef) else ast.AsyncFunctionDef(
                name=node.name, args=node.args, body=[ast.Expr(ast.Constant(...))],
                decorator_list=[d for d in node.decorator_list if ast.unparse(d) in KEEP_DECORATORS],
                returns=node.returns, type_params=[])
            # 默认值换成 ...，存根不需要也不该拖进默认值表达式里的名字
            args = stub.args
            args.defaults = [ast.Constant(...) for _ in args.defaults]
            args.kw_defaults = [None if d is None else ast.Constant(...) for d in args.kw_defaults]
            text = ast.unparse(ast.fix_missing_locations(stub))
            return text, names_in(stub) - {member}
    for node in ast.walk(klass):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute) \
                and node.target.attr == member:
            ann = ast.unparse(node.annotation)
            return f"{member}: {ann}", names_in(node.annotation)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == member:
            ann = ast.unparse(node.annotation)
            return f"{member}: {ann}", names_in(node.annotation)
    return f"{member}: Any", {"Any"}


def main() -> int:
    target, cls, *specs = sys.argv[1:]
    target_path = Path(target)
    stubs: list[str] = []
    wanted: dict[str, tuple[str, str]] = {}
    for spec in specs:
        def_file, _, qual = spec.partition(":")
        def_cls, _, member = qual.partition(".")
        text, used = stub_for(Path(def_file), def_cls, member)
        stubs.append(text)
        src_map = import_map(Path(def_file))
        for name in used:
            if hasattr(builtins, name) or name in {"self", "cls"}:
                continue
            if name in src_map:
                wanted[name] = src_map[name]
    source = target_path.read_text()
    tree = ast.parse(source)
    have = import_map(target_path)
    lines = source.splitlines(keepends=True)
    klass = find_class(tree, cls)
    first = klass.body[0]
    # 类说明字符串之后
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        anchor = klass.body[1] if len(klass.body) > 1 else None
    else:
        anchor = first
    indent = " " * (klass.col_offset + 4)
    block_body = "".join(
        "".join(f"{indent}    {ln}\n" for ln in s.splitlines()) + "\n" for s in stubs
    )
    existing = next((n for n in klass.body if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test)), None)
    if existing is not None:
        at = existing.end_lineno
        lines.insert(at, "\n" + block_body.rstrip("\n") + "\n")
    else:
        at = anchor.lineno - 1 if anchor is not None else klass.end_lineno
        while at > 0 and lines[at - 1].lstrip().startswith(("@", "#")) and anchor is not None:
            at -= 1
        lines.insert(at, f"{indent}if TYPE_CHECKING:  # pragma: no cover - provided by the composed class\n"
                     + block_body + "\n")
    text = "".join(lines)
    # 顶层 TYPE_CHECKING 导入
    target_mod = module_of(target_path)
    missing = {n: v for n, v in wanted.items() if n not in have}
    need_any = "Any" in missing
    missing.pop("Any", None)
    imports = sorted({f"    from {relative(target_mod, mod)} import {orig}" + (f" as {name}" if name != orig else "")
                      for name, (mod, orig) in missing.items() if mod != target_mod})
    tree = ast.parse(text)
    tlines = text.splitlines(keepends=True)
    typing_names = {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "typing" for a in n.names}
    if "TYPE_CHECKING" not in typing_names or (need_any and "Any" not in typing_names):
        add = [x for x in ("TYPE_CHECKING", "Any" if need_any else None) if x and x not in typing_names]
        tlines.insert(next(n.end_lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"),
                      f"from typing import {', '.join(add)}\n")
        text = "".join(tlines)
        tree = ast.parse(text)
        tlines = text.splitlines(keepends=True)
    if imports:
        top_tc = next((n for n in tree.body if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test)), None)
        if top_tc is not None:
            tlines.insert(top_tc.end_lineno, "\n".join(imports) + "\n")
        else:
            last = max(n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
            tlines.insert(last, "\nif TYPE_CHECKING:\n" + "\n".join(imports) + "\n")
        text = "".join(tlines)
    target_path.write_text(text)
    print(f"{target_path.name}:{cls} 补存根 {len(stubs)} 个，类型导入 {len(imports)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
