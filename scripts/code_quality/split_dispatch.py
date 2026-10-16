#!/usr/bin/env python3
"""代码质量整治阶段 6D：把分发函数里"if 条件: ...; return X"的分支整段搬成独立函数，
原处换成 return await 新函数(...)。
用法：split_dispatch.py 文件 函数 起始行=新名字[,起始行=新名字...]  [--tail 行=新名字]
条件：分支是函数体顶层 if、无 else、分支体最后一句是 return；
分支读到的外层名字只能是函数参数与前面已赋值的名字（作为参数传入）。
--tail：从该行起到函数末尾的语句整段搬走（最后一句须是 return）。
核对：新函数体与原分支体 AST 逐字相同；搬走后外层函数里不再有对分支局部变量的读取。
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reindent import reindent, string_interior_lines  # noqa: E402
from scope_names import locally_bound, names  # noqa: E402

path = Path(sys.argv[1])
qual = sys.argv[2]
specs = [x.split("=") for x in sys.argv[3].split(",") if x]
tail = None
if "--tail" in sys.argv:
    tail = sys.argv[sys.argv.index("--tail") + 1].split("=")
text = path.read_text()
lines = text.splitlines(keepends=True)
tree = ast.parse(text)
fn = next(
    n
    for n in tree.body
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == qual
)
is_async = isinstance(fn, ast.AsyncFunctionDef)
params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
kwonly = [a.arg for a in fn.args.kwonlyargs]
interior = string_interior_lines(text)
jobs = []  # (start_line, end_line, body_stmts, name, kind)
for ln, name in specs:
    ln = int(ln)
    st = next(s for s in fn.body if s.lineno == ln)
    assert isinstance(st, ast.If) and not st.orelse and isinstance(st.body[-1], ast.Return), ln
    jobs.append((st, st.body, name, "if"))
if tail:
    ln = int(tail[0])
    i = next(i for i, s in enumerate(fn.body) if s.lineno == ln)
    body = fn.body[i:]
    assert isinstance(body[-1], ast.Return)
    jobs.append((body[0], body, tail[1], "tail"))
out = list(lines)
new_defs = []
for st, body, name, kind in sorted(jobs, key=lambda j: -j[0].lineno):
    idx = fn.body.index(st)
    before = fn.body[:idx]
    prior = names(before, ast.Store) | set(params) | set(kwonly)
    reads = names(body, ast.Load)
    extra = sorted(
        n for n in (reads & prior) - set(params) - set(kwonly) if not locally_bound(body, n)
    )
    # 前面赋值的名字必须在分支开头之前一定已赋值：这里只允许无条件顶层赋值
    top_assigned = {
        t.id
        for s in before
        if isinstance(s, ast.Assign)
        for t in s.targets
        if isinstance(t, ast.Name)
    }
    assert set(extra) <= top_assigned, (name, extra)
    used_params = [p for p in params if p in reads]
    used_kw = [k for k in kwonly if k in reads] + extra
    sig_parts = list(used_params) + (["*"] + used_kw if used_kw else [])
    s0 = body[0].lineno
    s1 = body[-1].end_lineno
    seg = "".join(lines[s0 - 1 : s1])
    ind = len(lines[s0 - 1]) - len(lines[s0 - 1].lstrip())
    seg = reindent(seg, "    ", s0, interior)
    ret = fn.returns and ast.get_source_segment(text, fn.returns)
    arrow = f" -> {ret}" if ret else ""
    head = f"{'async ' if is_async else ''}def {name}({', '.join(sig_parts)}){arrow}:\n"
    new_defs.append((name, head + seg, body))
    call_args = ", ".join(used_params + [f"{k}={k}" for k in used_kw])
    call = f"return {'await ' if is_async else ''}{name}({call_args})\n"
    if kind == "if":
        out[s0 - 1 : s1] = [" " * ind + call]
    else:
        out[s0 - 1 : s1] = [" " * ind + call]
end = fn.end_lineno
block = "".join("\n\n" + d for _, d, _ in reversed(new_defs))
out[end:end] = [block]
new_text = "".join(out)
nt = ast.parse(new_text)
for name, _, body in new_defs:
    nf = next(n for n in nt.body if getattr(n, "name", "") == name)
    assert ast.dump(ast.Module(nf.body, [])) == ast.dump(ast.Module(body, [])), f"{name} 体不同"
path.write_text(new_text)
print(f"搬出 {len(new_defs)} 段；新函数体与原分支 AST 逐字相同")
