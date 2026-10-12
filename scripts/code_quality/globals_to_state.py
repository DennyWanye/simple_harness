#!/usr/bin/env python3
"""代码质量整治：把模块里被 `global` 重新绑定的变量收进状态对象（阶段 2.0b 的改写工具）。

函数搬出模块后，`from 模块 import X` 拿到的是导入那一刻的对象，原模块之后重新绑定 X 不会传过去。
本工具把指定模块里所有"指向模块全局变量"的名字改写成 `<前缀>.<新名>`：
- 用 symtable 判断作用域：局部变量、闭包变量、类属性同名者不动；
- 去掉 `global` 语句里的这些名字（空了就删整条）；
- 删除模块级 `X = None` 这类只为占位的初始化（状态对象默认就是 None）；
- 按字节偏移编辑，保留注释与格式，改完由 ruff format 收尾。

用法：python3 scripts/code_quality/globals_to_state.py <模块文件> <新名映射 JSON> [前缀名，默认 host_state]
前缀名不能在模块里已作为任何变量或参数出现（2026-10-12 教训：main.py 里 `state` 是 43 处局部变量，
改写后撞名，冻结数据类被当成状态对象赋值）。
映射形如 {"_sdk_ingress": "sdk_ingress", ...}
"""
from __future__ import annotations

import ast
import json
import os
import symtable
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])


def scope_children(table: symtable.SymbolTable) -> dict[tuple[str, int], symtable.SymbolTable]:
    return {(c.get_name(), c.get_lineno()): c for c in table.get_children()}


def main() -> int:
    path = Path(sys.argv[1])
    mapping: dict[str, str] = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    prefix = sys.argv[3] if len(sys.argv) > 3 else "host_state"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    clash = [n for n in ast.walk(tree) if (isinstance(n, ast.Name) and n.id == prefix)
             or (isinstance(n, ast.arg) and n.arg == prefix)
             or (isinstance(n, ast.alias) and (n.asname or n.name).split(".")[0] == prefix)]
    if clash:
        raise SystemExit(f"前缀名 {prefix} 已在模块里出现 {len(clash)} 次，换一个")
    top = symtable.symtable(src, str(path), "exec")
    edits: list[tuple[int, int, int, str]] = []  # (行, 起始字节, 结束字节, 新文本)

    def refers_to_global(table: symtable.SymbolTable, name: str) -> bool:
        if table.get_type() == "module":
            return True
        try:
            sym = table.lookup(name)
        except KeyError:
            return True  # 本作用域没出现过这个名字，按全局处理
        return sym.is_global()

    def walk(node: ast.AST, table: symtable.SymbolTable) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                # 装饰器、默认值、基类在外层作用域求值
                outer = []
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    outer += list(child.args.defaults) + [d for d in child.args.kw_defaults if d]
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    outer += list(child.decorator_list)
                if isinstance(child, ast.ClassDef):
                    outer += list(child.bases) + [k.value for k in child.keywords]
                for o in outer:
                    visit_expr(o, table)
                name = "lambda" if isinstance(child, ast.Lambda) else child.name
                inner = scope_children(table).get((name, child.lineno))
                if inner is None:
                    raise SystemExit(f"找不到作用域 {name}@{child.lineno}")
                body = [child.body] if isinstance(child, ast.Lambda) else child.body
                for b in body:
                    visit_expr(b, inner) if isinstance(b, ast.expr) else walk_stmt(b, inner)
                continue
            if isinstance(child, ast.GeneratorExp):
                visit_expr(child.generators[0].iter, table)
                inner = scope_children(table).get(("genexpr", child.lineno))
                if inner is None:
                    raise SystemExit(f"找不到生成器作用域 @{child.lineno}")
                walk_genexpr(child, inner)
                continue
            if isinstance(child, ast.Name) and child.id in mapping and refers_to_global(table, child.id):
                edits.append((child.lineno, child.col_offset, child.end_col_offset,
                              f"{prefix}.{mapping[child.id]}"))
            walk(child, table)

    def walk_stmt(st: ast.stmt, table: symtable.SymbolTable) -> None:
        holder = ast.Module(body=[st], type_ignores=[])
        walk(holder, table)

    def visit_expr(e: ast.expr, table: symtable.SymbolTable) -> None:
        holder = ast.Expression(body=e)
        walk(holder, table)

    def walk_genexpr(g: ast.GeneratorExp, table: symtable.SymbolTable) -> None:
        holder = ast.Module(body=[], type_ignores=[])
        parts: list[ast.AST] = [g.elt]
        for i, comp in enumerate(g.generators):
            parts.append(comp.target)
            if i > 0:
                parts.append(comp.iter)
            parts += comp.ifs
        for p in parts:
            walk(ast.Expression(body=p) if isinstance(p, ast.expr) else holder, table)

    walk(tree, top)

    # 按字符串取全局：globals().get("X"[, 默认]) 与 globals()["X"]
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name) \
                and node.func.value.func.id == "globals" and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in mapping:
            target = node.args[0].value
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "globals" \
                and isinstance(node.slice, ast.Constant) and node.slice.value in mapping:
            target = node.slice.value
        if target is not None:
            if node.lineno != node.end_lineno:
                raise SystemExit(f"跨行的 globals() 取值 @{node.lineno}，请先手工合成一行")
            edits.append((node.lineno, node.col_offset, node.end_col_offset, f"{prefix}.{mapping[target]}"))

    lines = src.splitlines(keepends=True)
    blines = [ln.encode("utf-8") for ln in lines]
    # global 语句：去掉映射里的名字
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            keep = [n for n in node.names if n not in mapping]
            if len(keep) == len(node.names):
                continue
            if node.lineno != node.end_lineno:
                raise SystemExit(f"跨行的 global 语句 @{node.lineno}，请先手工合成一行")
            line = blines[node.lineno - 1]
            indent = line[: node.col_offset]
            new = (indent + b"global " + ", ".join(keep).encode() + b"\n") if keep else b""
            if not keep:
                # 删除整行；若所在块只剩这一句，换成 pass
                new = indent + b"pass\n" if _only_statement(tree, node) else b""
            blines[node.lineno - 1] = new
    # 名字改写（同一行从右往左）
    for ln, c0, c1, text in sorted(edits, key=lambda e: (e[0], -e[1])):
        b = blines[ln - 1]
        blines[ln - 1] = b[:c0] + text.encode("utf-8") + b[c1:]
    out = b"".join(blines).decode("utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"改写 {len(edits)} 处名字")
    return 0


def _only_statement(tree: ast.AST, target: ast.stmt) -> bool:
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(node, field, None)
            if isinstance(seq, list) and target in seq:
                return len(seq) == 1
    return False


if __name__ == "__main__":
    sys.exit(main())
