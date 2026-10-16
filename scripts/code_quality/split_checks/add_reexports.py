"""给门面补"拆分前被外部导入、现在缺的名字"的转出。用法（backend 下）：add_reexports.py 源文件 子包目录 名字..."""
import ast, pathlib, sys
src, pkg, *names = sys.argv[1:]
pkgname = pathlib.Path(pkg).name
where = {}
for p in sorted(pathlib.Path(pkg).glob("*.py")):
    for n in ast.parse(p.read_text()).body:
        defined = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): defined = [n.name]
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            defined = [t.id for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)]
        elif isinstance(n, ast.ImportFrom):  # 子模块转手导入的外部名字（如原文件从别处导入再被外部引用）
            defined = [a.asname or a.name for a in n.names]
        for d in defined:
            if not isinstance(n, ast.ImportFrom) or d not in where: where[d] = p.stem if not isinstance(n, ast.ImportFrom) else where.get(d, p.stem)
lines = []
for nm in names:
    if nm not in where: sys.exit(f"子包里找不到 {nm}")
    lines.append(f"from .{pkgname}.{where[nm]} import {nm} as {nm}\n")
f = pathlib.Path(src); t = f.read_text()
first = next(l for l in t.splitlines(keepends=True) if l.startswith(f"from .{pkgname}"))
f.write_text(t.replace(first, "# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）\n" + "".join(lines) + first, 1))
print("补转出：", ", ".join(f"{n}←{where[n]}" for n in names))
