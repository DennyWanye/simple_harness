"""全后端（产品、测试、脚本）从指定模块 from-import 的名字，与模块顶层现有名字比对。用法：ext_names.py 模块名...（在 backend 下跑）"""
import ast, pathlib, sys, collections
targets = set(sys.argv[1:])
def modname(p):
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__": parts = parts[:-1]
    return ".".join(parts)
def resolve(mod, node, ispkg):
    if node.level == 0: return node.module or ""
    base = mod.split(".") if ispkg else mod.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))
wanted = collections.defaultdict(set)
attr_use = collections.defaultdict(set)
for p in pathlib.Path(".").rglob("*.py"):
    s = str(p)
    if s.startswith((".venv", "vendor")) or "site-packages" in s: continue
    try: t = ast.parse(p.read_text())
    except Exception: continue
    m = modname(p)
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom):
            b = resolve(m, n, p.name == "__init__.py")
            if b in targets:
                for a in n.names: wanted[b].add((a.name, s))
bad = 0
for tmod in sorted(targets):
    path = pathlib.Path(*tmod.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text())
    have = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): have.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)): have |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(tg, ast.Name): have.add(tg.id)
        elif isinstance(n, ast.If):
            for x in ast.walk(n):
                if isinstance(x, (ast.Import, ast.ImportFrom)) and "TYPE_CHECKING" not in ast.unparse(n.test):
                    have |= {(a.asname or a.name) for a in x.names}
    missing = sorted({(nm, f) for nm, f in wanted[tmod] if nm not in have and nm != "*"})
    print(f"{tmod}: 外部导入名字 {len({nm for nm,_ in wanted[tmod]})} 个，缺 {len({nm for nm,_ in missing})} 个")
    for nm, f in missing[:30]: print("   缺", nm, "←", f)
    bad += len(missing)
sys.exit(1 if bad else 0)
