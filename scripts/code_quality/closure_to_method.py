#!/usr/bin/env python3
"""代码质量整治阶段 6D：状态对象改名之后，把方法里只读 self 与状态对象的内层函数提成同类的方法。

用法：
    python3 scripts/code_quality/closure_to_method.py <文件> <类名.方法名> <内层函数名> --state st --cls _XxxState

前提（不满足就拒绝）：
- 已用 state_rename.py 把外层方法的局部变量改成 `st.x`，内层函数定义后紧跟一句 `st.名字 = 名字`；
- 内层函数直接定义在外层方法的函数体顶层，没有装饰器；
- 按 symtable 算，内层函数的自由变量只有 self 与状态对象（其余名字都是它自己的、模块级的或内置的）；
- 外层方法里（含更深的内层函数）对它的引用全是 `st.名字(...)` 调用，且不再给 `名字` 或 `st.名字` 赋值。
做法：函数原文搬成同类方法（插在外层方法之后），形参前补 `self, st: 状态类`；删掉定义与那句绑定；
调用处 `st.名字(` 改成 `self.名字(st, `。闭包读 self、st 时拿到的就是外层的同一个对象，改成参数后
指向同一个对象，晚绑定语义不变（字段仍在调用时从 st 读）。
核对：新方法体与原函数体语法树逐字相同。
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
    ap.add_argument("name")
    ap.add_argument("--state", default="st")
    ap.add_argument("--cls", required=True)
    a = ap.parse_args()
    path = Path(a.file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    cls_name, meth_name = a.qual.split(".")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    host = next(n for n in cls.body if isinstance(n, FUNCS) and n.name == meth_name)
    if any(isinstance(n, FUNCS) and n.name == a.name for n in cls.body):
        sys.exit(f"类里已有方法 {a.name}")
    idx = next(
        (i for i, s in enumerate(host.body) if isinstance(s, FUNCS) and s.name == a.name), None
    )
    if idx is None:
        sys.exit(f"{a.name} 不在外层方法的函数体顶层")
    inner = host.body[idx]
    if inner.decorator_list:
        sys.exit("内层函数有装饰器")
    bind = host.body[idx + 1] if idx + 1 < len(host.body) else None
    ok_bind = (
        isinstance(bind, ast.Assign)
        and len(bind.targets) == 1
        and isinstance(bind.targets[0], ast.Attribute)
        and isinstance(bind.targets[0].value, ast.Name)
        and bind.targets[0].value.id == a.state
        and bind.targets[0].attr == a.name
        and isinstance(bind.value, ast.Name)
        and bind.value.id == a.name
    )
    if not ok_bind:
        sys.exit(f"定义后面不是 `{a.state}.{a.name} = {a.name}`")
    table = find_table(symtable.symtable(text, str(path), "exec"), [cls_name, meth_name, a.name])
    frees = set(table.get_frees())
    if not frees <= {"self", a.state}:
        sys.exit(f"内层函数还读外层的 {sorted(frees - {'self', a.state})}")
    # 外层方法里对它的引用
    calls = []
    for n in ast.walk(host):
        if n is inner:
            continue
        if isinstance(n, ast.Name) and n.id == a.name and n is not bind.value:
            if not any(n is d for d in ast.walk(inner)):
                sys.exit(f"第 {n.lineno} 行直接用到名字 {a.name}")
        if (
            isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == a.state
            and n.attr == a.name
            and n is not bind.targets[0]
        ):
            if isinstance(n.ctx, ast.Store):
                sys.exit(f"第 {n.lineno} 行又给 {a.state}.{a.name} 赋值")
            calls.append(n)
    inside = {id(d) for d in ast.walk(inner)}
    if any(id(c) in inside for c in calls):
        sys.exit("内层函数里递归调用自己，不处理")
    call_funcs = {id(c.func) for c in ast.walk(host) if isinstance(c, ast.Call) and c.func in calls}
    if any(id(n) not in call_funcs for n in calls):
        sys.exit(f"{a.state}.{a.name} 有不是直接调用的用法")
    # 新方法原文
    interior = string_interior_lines(text)
    seg = "".join(lines[inner.lineno - 1 : inner.end_lineno])
    host_indent = len(lines[host.lineno - 1]) - len(lines[host.lineno - 1].lstrip())
    method = reindent(seg, " " * host_indent, inner.lineno, interior)
    head_end = method.index("(") + 1
    method = method[:head_end] + f"self, {a.state}: {a.cls}, " + method[head_end:]
    out = list(lines)
    # 先改调用处（从后往前，同一行可能有多处）
    edits = sorted(((c.lineno, c.col_offset, c.end_col_offset) for c in calls), reverse=True)
    for ln, c0, c1 in edits:
        b = out[ln - 1].encode("utf-8")
        tail = b[c1:]
        if not tail.startswith(b"("):
            sys.exit(f"第 {ln} 行调用格式不认")
        rest = tail[1:]
        sep = b"" if rest.lstrip().startswith(b")") else b", "
        new = b[:c0] + f"self.{a.name}({a.state}".encode() + sep + rest
        out[ln - 1] = new.decode("utf-8")
    # 删定义与绑定（连带其后的空行）
    end = bind.end_lineno
    if end < len(out) and not out[end].strip():
        end += 1
    start = inner.lineno
    while start > 1 and out[start - 2].strip().startswith("#"):
        start -= 1
    del out[start - 1 : end]
    removed = end - start + 1
    insert_at = host.end_lineno - removed
    out[insert_at:insert_at] = ["\n" + method]
    new_text = "".join(out)
    new_tree = ast.parse(new_text)
    new_cls = next(n for n in new_tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    new_m = next(n for n in new_cls.body if isinstance(n, FUNCS) and n.name == a.name)
    if ast.dump(ast.Module(new_m.body, [])) != ast.dump(ast.Module(inner.body, [])):
        sys.exit("新方法体与原函数体不同")
    path.write_text(new_text, encoding="utf-8")
    ruff = os.environ.get("RUFF") or str(
        REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
    )
    subprocess.run(
        [
            ruff,
            "format",
            "--force-exclude",
            "--config",
            str(REPO / "scripts/code_quality/ruff.toml"),
            "--quiet",
            str(path),
        ],
        check=False,
    )
    print(
        f"{a.name} 提成方法：{inner.end_lineno - inner.lineno + 1} 行，改调用 {len(calls)} 处；方法体逐字相同"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
