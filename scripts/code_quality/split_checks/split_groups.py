"""按源码连续区段生成 split_class 分组 JSON。用法：split_groups.py 源文件 类名 区段.json 输出.json"""
import ast, json, pathlib, sys
src, cname, ranges_file, out = sys.argv[1:5]
cfg = json.load(open(ranges_file))
RANGES = cfg["ranges"]  # [起始行, 模块, 类名或 null, 说明或 null]
t = ast.parse(pathlib.Path(src).read_text())
cls = next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == cname)
info = {m: (c, d) for _, m, c, d in RANGES if c}
groups, sizes, order = {}, {}, []
for _, m, _c, _d in RANGES:
    if m not in order: order.append(m)
for n in cls.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        mod = [r for r in RANGES if r[0] <= n.lineno][-1][1]
        groups.setdefault(mod, []).append(n.name)
        sizes[mod] = sizes.get(mod, 0) + n.end_lineno - n.lineno + 1
spec = {"package": cfg["package"], "support": "support", "stubs_in_core": True, "groups": []}
for m in order:
    c, d = info[m]
    g = {"module": m, "class": c, "doc": d, "methods": groups.get(m, [])}
    if m == order[0]: g["core"] = True
    spec["groups"].append(g)
    print(f"{m:24s} {len(groups.get(m, [])):4d} 个方法 {sizes.get(m, 0):6d} 行")
json.dump(spec, open(out, "w"), ensure_ascii=False, indent=1)
