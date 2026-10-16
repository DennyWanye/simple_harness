"""比较原文件快照与当前模块里某类的 dir()。用法：dir_cmp.py 原文件 模块名 类名"""
import importlib, importlib.util, sys
orig, mod, cname = sys.argv[1:4]
sys.path.insert(0, ".")
new = set(dir(getattr(importlib.import_module(mod), cname)))
name = mod.rsplit(".", 1)[0] + "._orig_snapshot"
spec = importlib.util.spec_from_file_location(name, orig)
m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
old = set(dir(getattr(m, cname)))
print(f"原 {len(old)} 新 {len(new)}；只在新：{sorted(new - old)}；只在原：{sorted(old - new)}")
