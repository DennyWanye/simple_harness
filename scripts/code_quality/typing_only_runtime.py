#!/usr/bin/env python3
"""代码质量整治阶段 5：找"只在 TYPE_CHECKING 块里导入、却在运行期用到"的名字（调用时才 NameError，ruff 查不出）。

用法：python3 scripts/code_quality/typing_only_runtime.py 文件...   有命中时退出码 1。
"""
import ast
import sys

bad = 0
for path in sys.argv[1:]:
    tree=ast.parse(open(path).read())
    tc=set()
    for n in tree.body:
        if isinstance(n,ast.If) and "TYPE_CHECKING" in ast.unparse(n.test):
            for s in ast.walk(n):
                if isinstance(s,(ast.Import,ast.ImportFrom)):
                    tc|={(a.asname or a.name).split(".")[0] for a in s.names}
    runtime_bound=set()
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            runtime_bound|={(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)): runtime_bound.add(n.name)
        elif isinstance(n,(ast.Assign,ast.AnnAssign)):
            for t in (n.targets if isinstance(n,ast.Assign) else [n.target]):
                if isinstance(t,ast.Name): runtime_bound.add(t.id)
    only_tc=tc-runtime_bound
    skip=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.If) and "TYPE_CHECKING" in ast.unparse(n.test): skip|={id(x) for x in ast.walk(n)}
        for ann in ([n.annotation] if isinstance(n,(ast.arg,ast.AnnAssign)) and n.annotation is not None else [])+([n.returns] if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.returns is not None else []):
            skip|={id(x) for x in ast.walk(ann)}
    # 函数里先按需导入了同名（循环依赖的局部导入），函数内的使用不算
    for fn in ast.walk(tree):
        if isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)):
            local={(a.asname or a.name).split(".")[0] for s in ast.walk(fn) if isinstance(s,(ast.Import,ast.ImportFrom)) for a in s.names}
            skip|={id(x) for x in ast.walk(fn) if isinstance(x,ast.Name) and x.id in local}
    hits=sorted({(x.id,x.lineno) for x in ast.walk(tree) if isinstance(x,ast.Name) and x.id in only_tc and id(x) not in skip})
    if hits:
        bad += 1
        print("运行期用到只供类型检查的名字：", path, hits[:10])
print(f"类型检查专用名字检查：{'通过' if not bad else '不通过'}")
sys.exit(1 if bad else 0)
