#!/usr/bin/env python3
"""代码质量整治阶段 6D：核对 extract_method.py 抽出的方法能原样内联回去。

用法：
    python3 scripts/code_quality/extract_check.py <参照提交> <文件>...

参照提交里没有、现在新出现的函数（模块级函数或同一个类里的方法）当作抽出来的。从不再调用别的新函数的
那个开始，把调用处换回函数体：
- `x, y = self.新(...)`：函数体去掉末尾的 `return x, y`；
- `return self.新(...)`：函数体原样（抽出段本来就走到原函数结束）；
- `self.新(...)` 单独一句：函数体原样；函数体里有 return（抽出段带 return）时，去掉调用后补的那句裸 `return`；
- 调用处的实参必须与形参同名（`a` 或 `a=a`），否则不认。
全部内联后，参照提交里的每个函数都要和现在逐字相同（语法树比较）。
"""

from __future__ import annotations

import ast
import copy
import subprocess
import sys
from pathlib import Path

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scope_names import defined_before  # noqa: E402


def functions(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, FUNCS):
            out[n.name] = n
        elif isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, FUNCS):
                    out[f"{n.name}.{m.name}"] = m
    return out


def param_names(fn) -> list[str]:
    a = fn.args
    names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return [n for n in names if n not in ("self", "cls")]


def match_call(expr: ast.AST, new: dict[str, ast.AST]) -> str | None:
    """表达式是不是对某个新函数的调用（可带 await），实参与形参同名。"""
    if isinstance(expr, ast.Await):
        expr = expr.value
    if not isinstance(expr, ast.Call):
        return None
    f = expr.func
    name = None
    if isinstance(f, ast.Name):
        name = f.id
    elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        name = f.attr
    if name is None:
        return None
    key = next((k for k in new if k.rsplit(".", 1)[-1] == name), None)
    if key is None:
        return None
    params = param_names(new[key])
    given = [a.id for a in expr.args if isinstance(a, ast.Name)]
    if len(given) != len(expr.args):
        return None
    for kw in expr.keywords:
        if kw.arg is None or not (isinstance(kw.value, ast.Name) and kw.value.id == kw.arg):
            return None
        given.append(kw.arg)
    return key if sorted(given) == sorted(params) else None


def has_return(stmts) -> bool:
    def walk(n):
        if isinstance(n, (*FUNCS, ast.Lambda, ast.ClassDef)):
            return False
        if isinstance(n, ast.Return):
            return True
        return any(walk(c) for c in ast.iter_child_nodes(n))

    return any(walk(s) for s in stmts)


def inline(stmts: list[ast.stmt], key: str, fn, new) -> tuple[list[ast.stmt], int]:
    body = list(fn.body)
    out: list[ast.stmt] = []
    hits = 0
    i = 0
    while i < len(stmts):
        s = stmts[i]
        done = False
        if isinstance(s, ast.Assign) and len(s.targets) == 1 and match_call(s.value, new) == key:
            last = body[-1]
            want = ast.dump(s.targets[0], include_attributes=False)
            if isinstance(last, ast.Return) and last.value is not None:
                got = last.value
                if isinstance(got, ast.Tuple) and isinstance(s.targets[0], ast.Tuple):
                    same = [ast.dump(x) for x in got.elts] == [
                        ast.dump(ast.Name(t.id, ast.Load()))
                        for t in s.targets[0].elts
                        if isinstance(t, ast.Name)
                    ]
                else:
                    same = isinstance(s.targets[0], ast.Name) and ast.dump(got) == ast.dump(
                        ast.Name(s.targets[0].id, ast.Load())
                    )
                if same:
                    out.extend(copy.deepcopy(body[:-1]))
                    hits += 1
                    done = True
            del want
        elif isinstance(s, ast.Return) and s.value is not None and match_call(s.value, new) == key:
            out.extend(copy.deepcopy(body))
            hits += 1
            done = True
        elif isinstance(s, ast.Expr) and match_call(s.value, new) == key:
            out.extend(copy.deepcopy(body))
            hits += 1
            done = True
            nxt = stmts[i + 1] if i + 1 < len(stmts) else None
            if has_return(body) and isinstance(nxt, ast.Return) and nxt.value is None:
                i += 1  # 调用后补的裸 return
        if done:
            i += 1
            continue
        for field in ("body", "orelse", "finalbody"):
            v = getattr(s, field, None)
            if (
                isinstance(v, list)
                and v
                and isinstance(v[0], ast.stmt)
                and not isinstance(s, (*FUNCS, ast.ClassDef))
            ):
                v2, h = inline(v, key, fn, new)
                setattr(s, field, v2)
                hits += h
        for h_ in getattr(s, "handlers", None) or []:
            h_.body, h = inline(h_.body, key, fn, new)
            hits += h
        if isinstance(s, ast.Match):
            for c in s.cases:
                c.body, h = inline(c.body, key, fn, new)
                hits += h
        out.append(s)
        i += 1
    return out, hits


def check(path: Path, ref: str) -> bool:
    rel = subprocess.run(
        ["git", "ls-files", "--full-name", str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent,
    ).stdout.strip() or str(path)
    old_src = subprocess.run(
        ["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, cwd=path.parent
    ).stdout
    old = functions(ast.parse(old_src))
    cur = functions(ast.parse(path.read_text(encoding="utf-8")))
    new = {k: v for k, v in cur.items() if k not in old}
    hosts = {k: copy.deepcopy(v) for k, v in cur.items()}
    # 调用处的实参必须一定已赋值（否则走到某条路时调用本身报 UnboundLocalError）
    unsafe = []
    for hk, host in cur.items():
        for st in ast.walk(host):
            if not isinstance(st, ast.stmt):
                continue
            value = getattr(st, "value", None)
            key = match_call(value, new) if value is not None else None
            if key is None:
                continue
            call = value.value if isinstance(value, ast.Await) else value
            args = {a.id for a in call.args if isinstance(a, ast.Name)} | {
                kw.arg for kw in call.keywords
            }
            missing = args - defined_before(host, st.lineno)
            if missing:
                unsafe.append(f"{hk} 调 {key.rsplit('.', 1)[-1]}：{sorted(missing)}")
    for u in unsafe:
        print(f"{path}：实参在调用处不一定已赋值 {u}")
    left = set(new)

    def calls_new(fn) -> set[str]:
        found = set()
        for n in ast.walk(fn):
            k = (
                match_call(n, {x: new[x] for x in left})
                if isinstance(n, (ast.Call, ast.Await))
                else None
            )
            if k:
                found.add(k)
        return found

    while left:
        leaf = next((k for k in sorted(left) if not (calls_new(hosts[k]) - {k}) & left), None)
        if leaf is None:
            print(f"{path}：新函数互相调用成环")
            return False
        left.remove(leaf)
        total = 0
        for k in list(hosts):
            if k == leaf or k in new and k not in left and k != leaf and False:
                continue
            if k == leaf:
                continue
            hosts[k].body, h = inline(hosts[k].body, leaf, hosts[leaf], {leaf: hosts[leaf]})
            total += h
        if total != 1:
            print(f"{path}：{leaf} 被内联 {total} 次（应为 1）")
            return False
    bad = [k for k in old if k not in hosts or ast.dump(hosts[k]) != ast.dump(old[k])]
    print(
        f"{path}：新函数 {len(new)} 个全部内联，原有函数 {len(old)} 个，"
        + ("逐字相同" if not bad else f"不同 {bad}")
    )
    return not bad


def main() -> int:
    ref, files = sys.argv[1], sys.argv[2:]
    ok = all([check(Path(f).resolve(), ref) for f in files])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
