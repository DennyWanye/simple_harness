#!/usr/bin/env python3
"""代码质量整治阶段 6D：把内层闭包的函数体原样搬成模块级函数，闭包本身只留一句转发。

用法：
    python3 scripts/code_quality/closure_body_out.py <文件> <外层函数名|类名.方法名> <内层函数名> <新函数名>
        [--pass 名字,名字]   # 闭包读到的外层变量，作为新函数的前几个参数传入（按这里的顺序）

适用：闭包本身要作为值交出去（当回调、工厂），不能改成别的签名；但函数体太长要拆。
条件（不满足就拒绝）：
- 内层函数直接定义在外层函数体顶层（或顶层 try 的主体里），没有装饰器；
- 形参只有普通的位置或关键字参数（无默认值、无 *args/**kwargs、无仅限关键字参数）；
- 按 symtable 算，闭包的自由变量恰好都在 --pass 里，且这些变量在外层里只在闭包定义之前赋值一次、
  之后不再改写（传入参数与闭包当时读到的是同一个对象；先用 state_rename 把会改写的变量收进状态对象）。
做法：模块级新增 `[async] def 新函数(传入..., 原形参...)`，函数体原样（缩进调整）；原闭包体换成
`return [await] 新函数(传入..., 原形参...)`。闭包的名字、签名、所在位置都不变。
核对：新函数体与原闭包体语法树逐字相同。
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import symtable
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reindent import reindent, string_interior_lines  # noqa: E402
from scope_names import names  # noqa: E402

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def find_table(top: symtable.SymbolTable, path: list[str]) -> symtable.SymbolTable:
    cur = top
    for name in path:
        matches = [c for c in cur.get_children() if c.get_name() == name]
        if len(matches) != 1:
            sys.exit(f"符号表里找不到唯一的 {name}")
        cur = matches[0]
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("qual")
    ap.add_argument("inner")
    ap.add_argument("new_name")
    ap.add_argument("--pass", dest="passed", default="")
    a = ap.parse_args()
    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    passed = [x.strip() for x in a.passed.split(",") if x.strip()]
    if any(getattr(n, "name", None) == a.new_name for n in tree.body):
        sys.exit(f"模块里已有 {a.new_name}")
    if "." in a.qual:
        cls_name, fn_name = a.qual.split(".")
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        outer = next(n for n in cls.body if isinstance(n, FUNCS) and n.name == fn_name)
        table_path = [cls_name, fn_name, a.inner]
        top_owner = cls
    else:
        outer = next(n for n in tree.body if isinstance(n, FUNCS) and n.name == a.qual)
        table_path = [a.qual, a.inner]
        top_owner = outer
    bodies = [outer.body] + [s.body for s in outer.body if isinstance(s, ast.Try)]
    inner = next(
        (n for b in bodies for n in b if isinstance(n, FUNCS) and n.name == a.inner), None
    )
    if inner is None:
        sys.exit(f"{a.inner} 不在外层函数体顶层（或顶层 try 主体）")
    if inner.decorator_list:
        sys.exit("闭包有装饰器")
    args = inner.args
    if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults:
        sys.exit("闭包形参不是简单的位置参数")
    frees = set(find_table(symtable.symtable(text, str(path), "exec"), table_path).get_frees())
    if frees != set(passed):
        sys.exit(f"闭包的自由变量是 {sorted(frees)}，--pass 给的是 {passed}")
    # 传入的变量：外层里只在闭包定义前赋值，之后不改写
    for nm in passed:
        stores = [
            x.lineno
            for x in ast.walk(outer)
            if isinstance(x, ast.Name) and x.id == nm and isinstance(x.ctx, ast.Store)
        ]
        params = {p.arg for p in (*outer.args.args, *outer.args.kwonlyargs)}
        late = [ln for ln in stores if ln > inner.lineno and not inner.lineno <= ln <= inner.end_lineno]
        if late or (len(stores) > 1) or (not stores and nm not in params):
            sys.exit(f"{nm} 在外层不是只赋值一次且在闭包定义前（赋值行 {stores}）")
        if nm in names(inner.body, ast.Store):
            sys.exit(f"闭包里给 {nm} 赋值")
    argnames = [p.arg for p in args.args]
    for p in args.args:
        if p.annotation is not None:
            sys.exit("闭包形参带注解，不处理")
    is_async = isinstance(inner, ast.AsyncFunctionDef)
    ret = f" -> {ast.get_source_segment(text, inner.returns)}" if inner.returns else ""
    body_start = inner.body[0].lineno
    # 函数体前紧贴的注释也算函数体
    while body_start - 1 > inner.lineno and lines[body_start - 2].strip().startswith("#"):
        body_start -= 1
    interior = string_interior_lines(text)
    body = reindent("".join(lines[body_start - 1 : inner.end_lineno]), "    ", body_start, interior)
    sig = ", ".join([*passed, *argnames])
    new_fn = (
        f"{'async ' if is_async else ''}def {a.new_name}({sig}){ret}:\n"
        f'    """{a.qual}.{a.inner} 闭包的函数体（闭包读的外层变量作为参数传入）。"""\n' + body
    )
    pad = " " * (inner.col_offset + 4)
    forward = f"{pad}return {'await ' if is_async else ''}{a.new_name}({sig})\n"
    out = list(lines)
    out[body_start - 1 : inner.end_lineno] = [forward]
    insert_at = top_owner.end_lineno - (inner.end_lineno - body_start + 1) + 1
    out[insert_at:insert_at] = ["\n\n" + new_fn]
    new_text = "".join(out)
    nt = ast.parse(new_text)
    nf = next(n for n in nt.body if getattr(n, "name", None) == a.new_name)
    if ast.dump(ast.Module(nf.body[1:], [])) != ast.dump(ast.Module(inner.body, [])):
        sys.exit("新函数体与原闭包体不同")
    path.write_text(new_text, encoding="utf-8")
    ruff = os.environ.get("RUFF") or str(
        REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
    )
    subprocess.run(
        [ruff, "format", "--config", str(REPO / "scripts/code_quality/ruff.toml"), "--quiet", str(path)],
        check=False,
    )
    n = inner.end_lineno - body_start + 1
    print(f"{a.inner} 的函数体 {n} 行搬成 {a.new_name}；函数体逐字相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
