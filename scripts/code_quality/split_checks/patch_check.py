"""拆模块后测试打桩是否落空。用法（backend 下）：patch_check.py 模块点分名...
扫 tests/ 与 scripts/：monkeypatch.setattr(别名, "X")、patch.object(别名, "X")、patch("模块.X")、别名.X = ...、别名.X 取值。
标出：缺失（模块已不绑定 X）；转手（X 在模块里只是导入来的——打桩改的是门面上的名字，子模块里的读取者看不到）。"""
import ast, pathlib, sys
mods = sys.argv[1:]
def bindings(mod):
    t = ast.parse(pathlib.Path(*mod.split(".")).with_suffix(".py").read_text())
    defined, imported = set(), set()
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): defined.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(tg, ast.Name): defined.add(tg.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return defined, imported - defined
bad = 0
for mod in mods:
    defined, imported = bindings(mod)
    parent, _, leaf = mod.rpartition(".")
    probs = set()
    for p in list(pathlib.Path("tests").rglob("*.py")) + list(pathlib.Path("scripts").rglob("*.py")):
        try: t = ast.parse(p.read_text(errors="replace"))
        except SyntaxError: continue
        aliases = set()
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == mod and a.asname: aliases.add(a.asname)
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == parent:
                for a in n.names:
                    if a.name == leaf: aliases.add(a.asname or leaf)
        uses = []
        for n in ast.walk(t):
            if isinstance(n, ast.Call):
                f = n.func; fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if fname in ("setattr", "object", "delattr") and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) \
                        and n.args[0].id in aliases and isinstance(n.args[1], ast.Constant):
                    uses.append(("打桩", n.args[1].value, n.lineno))
                if fname in ("patch", "setattr") and n.args and isinstance(n.args[0], ast.Constant) \
                        and isinstance(n.args[0].value, str) and n.args[0].value.startswith(mod + "."):
                    uses.append(("打桩", n.args[0].value[len(mod) + 1:].split(".")[0], n.lineno))
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                uses.append(("打桩" if isinstance(n.ctx, ast.Store) else "取值", n.attr, n.lineno))
        for kind, nm, ln in uses:
            if not isinstance(nm, str) or nm.startswith("__"): continue
            if nm not in defined and nm not in imported:
                probs.add(f"缺失 {p}:{ln} {nm}")
            elif kind == "打桩" and nm in imported:
                probs.add(f"转手 {p}:{ln} 打桩 {nm}（门面里只是导入来的）")
    print(f"{mod}：{len(probs)} 处待看")
    for x in sorted(probs)[:40]: print("  ", x)
    bad += len(probs)
sys.exit(1 if bad else 0)
