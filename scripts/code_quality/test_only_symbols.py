#!/usr/bin/env python3
"""代码质量整治阶段 8C-3：给"产品零引用、测试有同名词"的未用符号重新分类（语法树解析，不按词边界）。

输入：<目录>/dead.json（dead_symbols.py 的输出）。方法按测试里的属性名 `.名字` 与同名字符串计引用；
函数、类按 `from 模块 import 名字`、`模块别名.名字`、打桩字符串 "模块.名字" 计引用。
输出：<目录>/testonly_ast.json，每个符号带引用它的测试函数与测试函数外引用。

用法：python3 scripts/code_quality/test_only_symbols.py <目录>
"""

import json, re, ast, subprocess, sys, collections

S = sys.argv[1]
rows = json.load(open(f"{S}/dead.json"))
cands = [
    r
    for r in rows
    if not r["excluded"] and r["product_refs"] == 0 and r["test_refs"] > 0 and r["kind"] != "module"
]


def modname(f):
    for pre in ("backend/", "sdk/simple-harness-sdk/src/"):
        if f.startswith(pre):
            p = f[len(pre) : -3].split("/")
            if p[-1] == "__init__":
                p = p[:-1]
            return ".".join(p)


files = [
    f
    for f in subprocess.run(
        ["git", "ls-files", "backend/tests", "sdk/simple-harness-sdk/tests"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if f.endswith(".py")
]
info = {}
for f in files:
    src = open(f, encoding="utf-8").read()
    try:
        t = ast.parse(src)
    except SyntaxError:
        continue
    tests = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    info[f] = (src, t, tests)


def encl(tests, ln):
    best = None
    for n in tests:
        if n.lineno <= ln <= n.end_lineno and (best is None or n.lineno > best.lineno):
            best = n
    return best


def testfn(tests, ln):
    b = None
    for n in tests:
        if n.name.startswith("test") and n.lineno <= ln <= n.end_lineno:
            b = n
    return b


out = []
for r in cands:
    src = open(r["file"]).read()
    if not re.search(rf"\b(def|class)\s+{re.escape(r['name'])}\b", src):
        continue
    mod = modname(r["file"])
    nm = r["name"]
    hits = []
    for f, (tx, t, tests) in info.items():
        if nm not in tx:
            continue
        if r["kind"] == "method":
            for n in ast.walk(t):
                if isinstance(n, ast.Attribute) and n.attr == nm:
                    hits.append((f, n.lineno, "属性"))
                if isinstance(n, ast.Constant) and n.value == nm:
                    hits.append((f, n.lineno, "字符串"))
        else:
            aliases = set()
            direct = set()
            for n in ast.walk(t):
                if isinstance(n, ast.ImportFrom) and n.module:
                    for a in n.names:
                        if n.module == mod and a.name == nm:
                            direct.add(a.asname or a.name)
                            hits.append((f, n.lineno, "导入"))
                        if f"{n.module}.{a.name}" == mod:
                            aliases.add(a.asname or a.name)
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name == mod:
                            aliases.add(
                                a.asname or a.name.split(".")[0] if not a.asname else a.asname
                            )
            for n in ast.walk(t):
                if isinstance(n, ast.Attribute) and n.attr == nm:
                    base = n.value
                    while isinstance(base, ast.Attribute):
                        base = base.value
                    if isinstance(base, ast.Name) and base.id in aliases:
                        hits.append((f, n.lineno, "模块属性"))
                if (
                    isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and n.value in (f"{mod}.{nm}", nm)
                    and n.value != nm
                ):
                    hits.append((f, n.lineno, "打桩字符串"))
                if isinstance(n, ast.Name) and n.id in direct and isinstance(n.ctx, ast.Load):
                    hits.append((f, n.lineno, "使用"))
    tfs = set()
    outside = []
    for f, ln, k in hits:
        tx, t, tests = info[f]
        tf = testfn(tests, ln)
        if tf is None and k != "导入":
            outside.append(f"{f}:{ln}")
        elif tf is not None:
            tfs.add((f, tf.name, tf.lineno, tf.end_lineno))
    r2 = dict(r)
    r2["hits"] = len(hits)
    r2["tests"] = sorted(tfs)
    r2["outside"] = outside
    out.append(r2)
json.dump(out, open(f"{S}/testonly_ast.json", "w"), ensure_ascii=False, indent=1)
zero = [r for r in out if r["hits"] == 0]
inonly = [r for r in out if r["hits"] and not r["outside"]]
print(
    "候选",
    len(out),
    "零引用",
    len(zero),
    "只在测试函数内",
    len(inonly),
    "有函数外引用",
    len(out) - len(zero) - len(inonly),
)
print("只在测试函数内涉及测试函数", len({x for r in inonly for x in r["tests"]}))
for r in sorted(inonly, key=lambda r: len(r["tests"]))[:200]:
    pass
