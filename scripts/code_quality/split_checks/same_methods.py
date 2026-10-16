"""搬前搬后每个方法（及类属性）语法树逐字相同；类型检查块里的存根不算。用法：same_methods.py 原文件 类名 新包目录 新源文件"""
import ast, pathlib, sys
orig, cname, pkg, newsrc = sys.argv[1:5]
def members(tree, only=None):
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and (only is None or n.name == only):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[m.name] = ast.dump(m)
                elif isinstance(m, (ast.Assign, ast.AnnAssign)) and ast.unparse(m.targets[0] if isinstance(m, ast.Assign) else m.target) != "__slots__":
                    out["attr:" + ast.unparse(m.targets[0] if isinstance(m, ast.Assign) else m.target)] = ast.dump(m)
    return out
old = members(ast.parse(pathlib.Path(orig).read_text()), cname)
new = {}
for p in [pathlib.Path(newsrc), *sorted(pathlib.Path(pkg).glob("*.py"))]:
    for k, v in members(ast.parse(p.read_text())).items():
        new.setdefault(k, []).append(v)
bad = [k for k, v in old.items() if v not in new.get(k, [])]
print(f"方法与类属性 {len(old)} 个，逐字相同 {len(old) - len(bad)} 个")
for k in bad: print("  不同或缺失：", k)
sys.exit(1 if bad else 0)
