#!/usr/bin/env python3
"""代码质量整治：公开接口快照（计划 0.5，改结构的切片必跑）。

记录每个模块的公开名字、每个类的公开属性（含继承来的）与可调用对象的签名。
切片开始前写一份，改完再比。拆类成 mixin 或协作对象时，原类的公开属性与签名必须不变；
模块搬家时，差异里会成对出现"删除/新增"，要在切片说明里逐条对上。

用法：
    python3 scripts/code_quality/api_snapshot.py write --out before.json [--tree backend|sdk|all]
    python3 scripts/code_quality/api_snapshot.py diff before.json [--tree ...]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_smoke  # noqa: E402 — 复用模块清单与解释器配置

CHILD = r"""
import importlib, inspect, json, sys, warnings
warnings.simplefilter("ignore")
mods = json.loads(sys.stdin.read())
out = {}
import re
def sig(obj):
    try:
        return re.sub(r" at 0x[0-9a-f]+", "", str(inspect.signature(obj)))
    except (TypeError, ValueError):
        return "?"
for m in mods:
    try:
        mod = importlib.import_module(m)
    except BaseException as e:  # noqa: BLE001
        out[m] = {"<import-error>": type(e).__name__}
        continue
    entry = {}
    for name, obj in vars(mod).items():
        if name.startswith("_") or inspect.ismodule(obj):
            continue
        if getattr(obj, "__module__", m) != m:
            continue  # 只记本模块定义的名字，导入进来的不算
        if inspect.isclass(obj):
            attrs = {}
            for a in dir(obj):
                if a.startswith("_"):
                    continue
                v = getattr(obj, a, None)
                attrs[a] = sig(v) if callable(v) else type(v).__name__
            entry[name] = {"class": attrs}
        elif callable(obj):
            entry[name] = sig(obj)
        else:
            entry[name] = type(obj).__name__
    out[m] = entry
sys.stdout.write("\n@@RESULT@@" + json.dumps(out, ensure_ascii=False))
"""


def capture(tree: str) -> dict:
    cfg = import_smoke.TREES[tree]
    mods = import_smoke.modules(tree)
    with tempfile.TemporaryDirectory(prefix="cq-api-") as raw:
        tmp = os.path.realpath(raw)
        env = dict(os.environ)
        env.update({"DESKPET_USER_DATA_DIR": f"{tmp}/data", "DESKPET_USER_CACHE_DIR": f"{tmp}/cache",
                    "DESKPET_USER_LOG_DIR": f"{tmp}/log", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0"})
        env.pop("PYTHONPATH", None)
        # 后端用 backend/ 作导入根；SDK 显式指向本检出的 src（虚拟环境里的可编辑安装指向主检出）
        env["PYTHONPATH"] = str(cfg["cwd"] if tree == "backend" else cfg["root"])
        p = subprocess.run([str(cfg["python"]), "-c", CHILD], input=json.dumps(mods), cwd=cfg["cwd"],
                           capture_output=True, text=True, env=env, timeout=1800)
    if "@@RESULT@@" not in p.stdout:
        raise SystemExit(f"{tree} 快照失败：{p.stderr[-2000:]}")
    return json.loads(p.stdout.split("@@RESULT@@", 1)[1])


def flatten(snap: dict) -> dict[str, str]:
    flat: dict[str, str] = {}
    for tree, mods in snap.items():
        for m, entry in mods.items():
            for name, v in entry.items():
                if isinstance(v, dict) and "class" in v:
                    for a, s in v["class"].items():
                        flat[f"{tree}:{m}.{name}.{a}"] = s
                else:
                    flat[f"{tree}:{m}.{name}"] = v
    return flat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["write", "diff"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--tree", default="all", choices=["backend", "sdk", "all"])
    a = ap.parse_args()
    trees = ["backend", "sdk"] if a.tree == "all" else [a.tree]
    snap = {t: capture(t) for t in trees}
    if a.cmd == "write":
        Path(a.out).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"已写 {a.out}：{sum(len(flatten({t: snap[t]})) for t in trees)} 项")
        return 0
    before = json.loads(Path(a.path).read_text(encoding="utf-8"))
    b = flatten({t: before[t] for t in trees if t in before})
    n = flatten(snap)
    removed = sorted(set(b) - set(n))
    added = sorted(set(n) - set(b))
    changed = sorted(k for k in set(b) & set(n) if b[k] != n[k])
    for k in removed:
        print(f"删除 {k}")
    for k in added:
        print(f"新增 {k}")
    for k in changed:
        print(f"签名变化 {k}: {b[k]} -> {n[k]}")
    print(f"公开接口：删除 {len(removed)}，新增 {len(added)}，签名变化 {len(changed)}")
    return 1 if (removed or changed) else 0


if __name__ == "__main__":
    sys.exit(main())
