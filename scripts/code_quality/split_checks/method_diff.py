"""列出搬前搬后某些方法的源码差异（只看类体里的直接方法，不看类型检查存根）。用法：method_diff.py 原文件 类名 子包目录 方法名..."""
import ast, pathlib, sys, difflib
orig, cname, pkg, *names = sys.argv[1:]
def methods(path, only=None):
    out = {}
    for n in ast.walk(ast.parse(pathlib.Path(path).read_text())):
        if isinstance(n, ast.ClassDef) and (only is None or n.name == only):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)): out[m.name] = ast.unparse(m)
    return out
old = methods(orig, cname); new = {}
for p in sorted(pathlib.Path(pkg).glob("*.py")): new.update(methods(p))
for name in names:
    for l in difflib.unified_diff(old[name].splitlines(), new.get(name, "").splitlines(), lineterm="", n=0):
        if l.startswith(("+", "-")) and not l.startswith(("+++", "---")): print(name, l.strip()[:150])
