#!/usr/bin/env python3
"""代码质量整治阶段 5C：把一个没有巨型类、只是定义很多的大模块按族拆进子包（纯移动），原文件留作门面。

用法：
    python3 scripts/code_quality/split_module.py <源文件> <分组.json> [--dry-run]

分组 JSON：
    {"package": "memory_protocol_domains",   # 源文件同目录下新建的子包
     "common": "common",                      # 多个族共用的私有辅助放这里
     "groups": [{"module": "types", "doc": "...", "names": ["LongTermMemoryType", ...]}, ...]}

规则：
- 顶层定义（类、函数、赋值）逐字搬走，含紧挨在上方的注释与空行，各模块里保持源文件的先后顺序。
- 分组里列出的名字进对应模块；没列出的名字按使用方放：只被一个模块用 → 进该模块；多个模块用 → 进 common。
- 依赖只许向前：一个族只能用 common 和排在它前面的族；common 只能用源文件导入的外部名字。违反时报错退出，改分组顺序。
- 每个新模块的导入从源文件导入语句里按用到的名字筛（相对导入多加一个点），再加 common 与前面族的导入。
- 源文件只留模块说明、`__all__` 与转出导入（按 `__all__` 与余下代码用到的名字从各族导入）。收尾跑 ruff 删未用导入、排序、格式化。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_class import REPO, RUFF_CONFIG, bound_names, deepen_relative_imports, names_in, render_import  # noqa: E402


def defined(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("spec")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.source).resolve()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    body = tree.body

    imports: list[tuple[ast.stmt, bool]] = []
    late: list[ast.stmt] = []  # 出现在定义之后的导入（有意放在末尾避循环、改绑名字）：原样留在门面
    defs: dict[str, ast.stmt] = {}
    order: list[ast.stmt] = []
    keep_nodes: list[ast.stmt] = []  # 留在门面：说明、__all__
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and order:
            late.append(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append((node, False))
        elif isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            imports.extend((s, True) for s in node.body)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node is body[0]:
            keep_nodes.append(node)
        elif defined(node) == ["__all__"]:
            keep_nodes.append(node)
        elif defined(node):
            for name in defined(node):
                defs[name] = node
            order.append(node)
        else:
            sys.exit(f"第 {node.lineno} 行的顶层语句不是定义，工具不处理：{ast.unparse(node)[:80]}")

    def span(node: ast.stmt) -> tuple[int, int]:
        index = body.index(node)
        first = node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno
        start = body[index - 1].end_lineno if index else first - 1
        return start, node.end_lineno  # type: ignore[return-value]

    chunk = {id(n): "".join(lines[slice(*span(n))]) for n in order}
    uses = {id(n): names_in(chunk[id(n)]) & set(defs) for n in order}
    common = spec.get("common", "common")
    group_names = [g["module"] for g in spec["groups"]]
    rank = {m: i for i, m in enumerate(group_names)}
    owner: dict[int, str] = {}
    for g in spec["groups"]:
        for name in g["names"]:
            if name not in defs:
                sys.exit(f"源文件里没有顶层名字 {name}")
            owner[id(defs[name])] = g["module"]
    # 没列出的定义按使用方放，直到稳定（辅助之间的依赖做闭包）
    while True:
        changed = False
        for n in order:
            if id(n) in owner:
                continue
            users = {owner[id(m)] for m in order if id(m) in owner and set(defined(n)) & uses[id(m)]}
            if users:
                owner[id(n)] = next(iter(users)) if len(users) == 1 else common
                changed = True
        if not changed:
            break
    loose = [defined(n) for n in order if id(n) not in owner]
    if loose:
        sys.exit(f"没有人用、也没列进分组的定义（先确认是否死代码）：{loose}")
    module_of = {name: owner[id(node)] for name, node in defs.items()}
    # 依赖方向检查
    bad = []
    for n in order:
        here = owner[id(n)]
        for dep in uses[id(n)] - set(defined(n)):
            there = module_of[dep]
            if there == here:
                continue
            if here == common or (there != common and rank[there] > rank[here]):
                bad.append(f"{here} 里的 {defined(n)} 用到 {there} 的 {dep}")
    if bad:
        sys.exit("依赖方向不对（只许向前）：\n" + "\n".join(sorted(set(bad))))

    header = "".join(lines[: body[0].lineno - 1])

    def import_block(used: set[str], extra_level: int) -> str:
        def render(in_block: bool) -> list[str]:
            out = []
            for stmt, in_checking in imports:
                if in_checking != in_block:
                    continue
                keep = [alias for bound, alias in bound_names(stmt) if bound in used]
                if keep:
                    out.append(render_import(stmt, keep, extra_level))
            return out

        checking = render(True)
        plain = render(False)
        if checking and "TYPE_CHECKING" not in used:
            plain.append("from typing import TYPE_CHECKING")
        out = "\n".join(plain)
        if checking:
            out += "\n\nif TYPE_CHECKING:\n" + "\n".join("    " + line for line in checking)
        return out

    package = path.parent / spec["package"]
    outputs: dict[Path, str] = {}
    docs = {g["module"]: g["doc"] for g in spec["groups"]}
    docs[common] = spec.get("common_doc", "Private helpers shared by several families.")
    for module in [common, *group_names]:
        mine = [n for n in order if owner[id(n)] == module]
        if not mine:
            continue
        code = "".join(chunk[id(n)] for n in mine).lstrip("\n")
        used = names_in(code)
        own = {name for n in mine for name in defined(n)}
        local: dict[str, list[str]] = {}
        for name in sorted((used & set(defs)) - own):
            local.setdefault(module_of[name], []).append(name)
        local_imports = "\n".join(f"from .{m} import {', '.join(ns)}" for m, ns in local.items())
        block = import_block(used - {"annotations"}, 1)
        parts = [header + f'"""{docs[module]}"""\n\nfrom __future__ import annotations\n']
        parts.append("\n".join(x for x in (block, local_imports) if x))
        parts.append(deepen_relative_imports(code))
        outputs[package / f"{module}.py"] = "\n\n".join(p.rstrip("\n") for p in parts if p) + "\n"
    outputs[package / "__init__.py"] = header + f'"""Families of definitions re-exported by {path.stem}.py."""\n'

    # 门面：说明 + 转出导入 + __all__
    kept = "".join("".join(lines[slice(*((n.lineno - 1, n.end_lineno)))]) + "\n" for n in keep_nodes)
    all_node = next((n for n in keep_nodes if defined(n) == ["__all__"]), None)
    exported = set(ast.literal_eval(all_node.value)) if all_node is not None else set()  # type: ignore[union-attr]
    reexport: dict[str, list[str]] = {}
    for name in sorted(exported & set(defs)):
        reexport.setdefault(module_of[name], []).append(name)
    doc = "".join(lines[keep_nodes[0].lineno - 1 : keep_nodes[0].end_lineno]) if keep_nodes and keep_nodes[0] is body[0] else ""
    late_text = "".join("".join(lines[slice(*span(n))]) for n in late)
    # __all__ 里从别的模块导入再转出的名字：门面照原样导入（原导入语句按名字筛）
    passthrough = import_block(exported - set(defs), 0)
    facade = header + doc + "\nfrom __future__ import annotations\n\n" + "\n".join(
        x for x in (passthrough, *(f"from .{spec['package']}.{m} import {', '.join(ns)}" for m, ns in reexport.items())) if x
    ) + "\n\n\n" + ("".join(lines[all_node.lineno - 1 : all_node.end_lineno]) if all_node is not None else "")
    # 有意后置的导入放在 __all__ 之后自成一个导入块，它自带的 noqa 才管得到整块
    facade += ("\n" + late_text) if late_text else ""
    outputs[path] = facade
    sizes = {p.name: t.count("\n") for p, t in outputs.items()}
    print("各文件行数：", json.dumps(sizes, ensure_ascii=False))
    print("自动归位的辅助：", json.dumps(
        {m: sorted(name for name, mod in module_of.items() if mod == m and not any(name in g["names"] for g in spec["groups"]))
         for m in [common, *group_names]}, ensure_ascii=False))
    if args.dry_run:
        return 0
    package.mkdir(exist_ok=True)
    for out_path, content in outputs.items():
        out_path.write_text(content, encoding="utf-8")
    files = [str(p) for p in outputs]
    ruff = [os.environ.get("RUFF") or str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")]
    subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select", "F401,I001", "--fix", "--quiet", *files], check=False)
    subprocess.run([*ruff, "format", "--config", str(RUFF_CONFIG), "--quiet", *files], check=False)
    res = subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select", "F401,F811,F821,F841", *files],
                         capture_output=True, text=True)
    print(res.stdout[-4000:] or "ruff：无未定义/未使用名字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
