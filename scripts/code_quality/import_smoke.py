#!/usr/bin/env python3
"""代码质量整治：导入烟雾（计划 4.1）。

逐个导入后端与 SDK 的全部模块，抓漏改的导入、循环依赖、搬家后找不到的名字。

用法：
    python3 scripts/code_quality/import_smoke.py [--tree backend|sdk|all]
        一个进程里按字母序导入全部模块。
    python3 scripts/code_quality/import_smoke.py --fresh-changed REF
        REF 之后改过的模块，每个在全新进程里单独导入（循环依赖只在特定入口顺序暴露）。

后端用 backend/.venv（装的是钉版 SDK wheel）；SDK 用 sdk/simple-harness-sdk/.venv（源码）。
用户数据、缓存、日志目录指到临时目录，导入期副作用不碰真实数据。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TREES = {
    "backend": {
        "python": REPO / "backend/.venv/bin/python",
        "cwd": REPO / "backend",
        "root": REPO / "backend",
        "skip": {"tests", "scripts", "verify", "build", "dist", "dist-1", "vendor", "userdata",
                 ".venv", "__pycache__", "simple_harness_backend.egg-info"},
    },
    "sdk": {
        "python": REPO / "sdk/simple-harness-sdk/.venv/bin/python",
        "cwd": REPO / "sdk/simple-harness-sdk",
        "root": REPO / "sdk/simple-harness-sdk/src",
        "skip": {"__pycache__"},
    },
}

# 已有问题（2026-10-12 录基线时发现），阶段 3 处理循环依赖时清掉。新进程单独导入它们会失败，
# 但按正常顺序导入没问题。这里只报告，不判失败。
KNOWN_FRESH_FAILURES = {
    "sdk simple_harness.runtime.recall_protocol_v4",
}

CHILD = r"""
import importlib, json, sys, traceback, warnings
warnings.simplefilter("ignore")
mods = json.loads(sys.stdin.read())
res = {}
for m in mods:
    try:
        importlib.import_module(m)
        res[m] = "ok"
    except BaseException as e:  # noqa: BLE001 — 烟雾要记录任何导入失败
        tb = traceback.format_exception_only(type(e), e)[-1].strip()
        res[m] = tb[:400]
sys.stdout.write("\n@@RESULT@@" + json.dumps(res))
"""


def modules(tree: str) -> list[str]:
    cfg = TREES[tree]
    root: Path = cfg["root"]
    out: list[str] = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in cfg["skip"] and not d.endswith(".egg-info"))
        here = Path(dp)
        if here != root and not (here / "__init__.py").exists():
            dn[:] = []
            continue
        for f in sorted(fn):
            if not f.endswith(".py") or f.startswith("test_") or f == "conftest.py":
                continue
            relp = (here / f).relative_to(root).with_suffix("")
            parts = list(relp.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and parts[-1] != "__main__":
                out.append(".".join(parts))
    return out


def run(tree: str, mods: list[str]) -> dict[str, str]:
    cfg = TREES[tree]
    with tempfile.TemporaryDirectory(prefix="cq-import-") as raw:
        tmp = os.path.realpath(raw)
        env = dict(os.environ)
        env.update({
            "DESKPET_USER_DATA_DIR": f"{tmp}/data", "DESKPET_USER_CACHE_DIR": f"{tmp}/cache",
            "DESKPET_USER_LOG_DIR": f"{tmp}/log", "PYTHONDONTWRITEBYTECODE": "1",
        })
        env.pop("PYTHONPATH", None)
        # 后端用 backend/ 作导入根；SDK 显式指向本检出的 src（虚拟环境里的可编辑安装指向主检出）
        env["PYTHONPATH"] = str(cfg["cwd"] if tree == "backend" else cfg["root"])
        p = subprocess.run([str(cfg["python"]), "-c", CHILD], input=json.dumps(mods), cwd=cfg["cwd"],
                           capture_output=True, text=True, env=env, timeout=1800)
    if "@@RESULT@@" not in p.stdout:
        return {m: f"进程异常退出 rc={p.returncode}: {p.stderr[-400:]}" for m in mods}
    return json.loads(p.stdout.split("@@RESULT@@", 1)[1])


def changed_modules(ref: str) -> dict[str, list[str]]:
    files = subprocess.run(["git", "diff", "--name-only", ref], cwd=REPO, capture_output=True,
                           text=True).stdout.splitlines()
    files += subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
                            capture_output=True, text=True).stdout.splitlines()
    out: dict[str, list[str]] = {"backend": [], "sdk": []}
    for tree in out:
        root = TREES[tree]["root"].relative_to(REPO).as_posix() + "/"
        known = set(modules(tree))
        for f in files:
            if f.startswith(root) and f.endswith(".py"):
                parts = list(Path(f[len(root):]).with_suffix("").parts)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                m = ".".join(parts)
                if m in known:
                    out[tree].append(m)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="all", choices=["backend", "sdk", "all"])
    ap.add_argument("--fresh-changed", metavar="REF")
    a = ap.parse_args()
    bad: list[str] = []
    if a.fresh_changed:
        for tree, mods in changed_modules(a.fresh_changed).items():
            for m in mods:
                r = run(tree, [m])[m]
                if r != "ok":
                    if f"{tree} {m}" in KNOWN_FRESH_FAILURES:
                        print(f"已知问题（不判失败）{tree} {m}: {r[:160]}")
                    else:
                        bad.append(f"{tree} {m}: {r}")
            print(f"{tree}: 新进程单独导入 {len(mods)} 个改动模块")
    else:
        for tree in (["backend", "sdk"] if a.tree == "all" else [a.tree]):
            mods = modules(tree)
            res = run(tree, mods)
            fails = {m: r for m, r in res.items() if r != "ok"}
            print(f"{tree}: 导入 {len(mods)} 个模块，失败 {len(fails)}")
            bad += [f"{tree} {m}: {r}" for m, r in sorted(fails.items())]
    for b in bad:
        print("失败", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
