#!/usr/bin/env python3
"""代码质量整治：按改动的源文件找出相关测试文件（测试里出现该模块的点分路径或 from 导入）。

用法：python3 scripts/code_quality/tests_for_changes.py [--ref HEAD] [--tree backend|sdk] > 清单
输出相对测试树根的路径，一行一个。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SDK = REPO / "sdk/simple-harness-sdk"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--tree", choices=["backend", "sdk"], default="backend")
    a = ap.parse_args()
    root, src_root, prefix = (
        (BACKEND, BACKEND, "backend/") if a.tree == "backend" else (SDK, SDK / "src", "sdk/simple-harness-sdk/src/")
    )
    changed = subprocess.run(["git", "diff", "--name-only", a.ref, "--", prefix], cwd=REPO,
                             capture_output=True, text=True).stdout.split()
    mods = set()
    for f in changed:
        if not f.endswith(".py") or "/tests/" in f:
            continue
        rel = Path(f).relative_to(prefix).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mods.add(".".join(parts))
    if not mods:
        return 0
    pats = [re.compile(rf"(?<![\w.]){re.escape(m)}(?![\w])") for m in mods]
    # host 包与主入口经 main 被大量测试间接使用：改到它们时，引用 main 或 host 的测试都算相关
    if any(m == "main" or m.startswith("host.") for m in mods):
        pats.append(re.compile(r"^\s*(import main\b|from main import|from host[ .]|import host\b)|import main as", re.M))
    out = []
    for t in sorted((root / "tests").rglob("test_*.py")):
        text = t.read_text(encoding="utf-8", errors="replace")
        if any(p.search(text) for p in pats):
            out.append(str(t.relative_to(root)))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
