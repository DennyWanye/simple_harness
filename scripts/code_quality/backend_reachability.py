#!/usr/bin/env python3
"""代码质量整治阶段 8：后端模块可达性（从给定入口出发；导入、函数内导入、点分字符串常量、
同包短名字符串（按名字加载的模块清单）都算边；构建清单、打包配置等资源文件里按路径或点分名提到的模块算根）。

用法：python3 scripts/code_quality/backend_reachability.py main
输出不可达模块到同目录 reach_out.json（只读分析，不改代码）。
"""
import ast,subprocess,re,sys,json
files=[f for f in subprocess.run(["git","ls-files","backend"],capture_output=True,text=True).stdout.split() if f.endswith(".py") and "/tests/" not in f]
def modname(f):
    p=f[len("backend/"):-3].split("/")
    if p[-1]=="__init__": p=p[:-1]
    return ".".join(p)
mods={modname(f):f for f in files}
edges={}
for m,f in mods.items():
    s=open(f,encoding="utf-8",errors="replace").read()
    try: t=ast.parse(s)
    except SyntaxError: edges[m]=set(); continue
    is_pkg=f.endswith("__init__.py"); out=set()
    def add(x):
        parts=x.split(".")
        for i in range(1,len(parts)+1):
            c=".".join(parts[:i])
            if c in mods: out.add(c)
    for n in ast.walk(t):
        if isinstance(n,ast.Import):
            for a in n.names: add(a.name)
        elif isinstance(n,ast.ImportFrom):
            if n.level:
                base=m.split(".") if is_pkg else m.split(".")[:-1]
                if n.level>1: base=base[:len(base)-(n.level-1)]
                mm=".".join([*base,*([n.module] if n.module else [])])
            else: mm=n.module or ""
            add(mm)
            for a in n.names: add(mm+"."+a.name)
        elif isinstance(n,ast.Constant) and isinstance(n.value,str) and re.fullmatch(r"\.?[a-z_][\w.]*",n.value):
            v=n.value
            pkg=m if is_pkg else m.rsplit(".",1)[0]
            if v.startswith("."): add(pkg+v)
            else:
                add(v)
                if pkg+"."+v in mods: out.add(pkg+"."+v)
    # 包的 __init__ 隐含：导入子模块会先跑父包
    parts=m.split(".")
    for i in range(1,len(parts)):
        c=".".join(parts[:i])
        if c in mods: out.add(c)
    edges[m]=out
roots=[x for x in sys.argv[1:]]
# 非 Python 资源里按路径或点分名提到的模块也算根（构建清单、打包配置、Tauri）
for f in subprocess.run(["git","ls-files"],capture_output=True,text=True).stdout.split():
    if f.endswith((".json",".toml",".spec",".rs",".yaml",".yml",".cfg",".ini")) and not f.startswith(("plans/",".local")):
        try: s2=open(f,encoding="utf-8",errors="replace").read()
        except Exception: continue
        for x in re.findall(r"backend/([\w/]+)\.py",s2):
            mm=x.replace("/",".")
            if mm.endswith(".__init__"): mm=mm[:-9]
            if mm in mods: roots.append(mm)
        for x in re.findall(r"[a-z_][\w]*(?:\.[\w]+)+",s2):
            if x in mods: roots.append(x)
seen=set(); todo=list(roots)
while todo:
    x=todo.pop()
    if x in seen: continue
    seen.add(x); todo+=list(edges.get(x,()))
dead=sorted(m for m in mods if m not in seen)
tot=sum(len(open(mods[m]).read().splitlines()) for m in dead)
import os
out_path=os.environ.get("CQ_REACH_OUT","reach_out.json")
json.dump({"dead":[(m,mods[m]) for m in dead]},open(out_path,"w"),ensure_ascii=False,indent=1)
print("模块",len(mods),"可达",len(seen),"不可达",len(dead),"行",tot)
