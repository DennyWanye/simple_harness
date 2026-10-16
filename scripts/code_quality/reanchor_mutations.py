#!/usr/bin/env python3
"""代码质量整治：格式化后给改坏清单重新定锚（阶段 1 起，每次全库改写后用）。

改坏清单（sdk/.../acceptance_assets/mutations.json）用源码原文定位改坏点。格式化改了原文，
锚点会对不上。本脚本对每条失效的改坏：
1. 取改写前（--base）的文件，确认原锚点恰好出现一次；
2. 分别把"原文件"和"施加改坏后的文件"走同一条流水线（ruff 安全修复 + ruff format）；
3. 比对两者，得到改坏在新格式下的差异区；向外扩上下文直到它在当前文件里恰好出现一次；
4. 写回新的 original / replacement。改坏的语义不变，只是换了锚定文本。

用法：python3 scripts/code_quality/reanchor_mutations.py --base <改写前提交> [--write]
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 11):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
SDK = REPO / "sdk/simple-harness-sdk"
CATALOGUE = SDK / "tests/orchestrator/acceptance_assets/mutations.json"
RUFF = os.environ.get("CQ_RUFF") or str(REPO / "sdk/simple-harness-sdk/.venv/bin/ruff")
if not Path(RUFF).exists():
    RUFF = "ruff"
CONFIG = REPO / "scripts/code_quality/ruff.toml"
SAFE_RULES = "I001,UP006,UP007,UP045,UP037,UP035,F541,E401,E703,W291,W292,W293,UP012,B009,B010,UP041,UP024,UP017"


def pipeline(text: str, rel: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / Path(rel).name
        p.write_text(text, encoding="utf-8")
        subprocess.run([RUFF, "check", "--config", str(CONFIG), "--select", SAFE_RULES, "--fix", "-q", str(p)],
                       capture_output=True)
        subprocess.run([RUFF, "format", "--force-exclude", "--config", str(CONFIG), "-q", str(p)], capture_output=True)
        return p.read_text(encoding="utf-8")


def reanchor(row: dict, base: str) -> tuple[str, str] | str:
    rel = row["file"]
    old = subprocess.run(["git", "show", f"{base}:sdk/simple-harness-sdk/{rel}"], cwd=REPO,
                         capture_output=True, text=True).stdout
    if old.count(row["original"]) != 1:
        return "改写前文件里原锚点也不是恰好一次"
    current = (SDK / rel).read_text(encoding="utf-8")
    t_old = pipeline(old, rel).splitlines(keepends=True)
    t_mut = pipeline(old.replace(row["original"], row["replacement"]), rel).splitlines(keepends=True)
    ops = [op for op in difflib.SequenceMatcher(a=t_old, b=t_mut, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return "改坏在新格式下没有差异"
    a0, a1 = ops[0][1], ops[-1][2]
    b0, b1 = ops[0][3], ops[-1][4]
    for ctx in range(0, 12):
        lo, hi = max(0, a0 - ctx), min(len(t_old), a1 + ctx)
        new_orig = "".join(t_old[lo:hi])
        new_repl = "".join(t_old[lo:a0]) + "".join(t_mut[b0:b1]) + "".join(t_old[a1:hi])
        if new_orig and current.count(new_orig) == 1:
            return new_orig, new_repl
    return "扩到 12 行上下文仍无法在当前文件里唯一定位"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    raw = CATALOGUE.read_text(encoding="utf-8")
    data = json.loads(raw)
    fixed, failed = 0, []
    for row in data["mutations"]:
        if not row.get("file"):
            continue
        if (SDK / row["file"]).read_text(encoding="utf-8").count(row["original"]) == 1:
            continue
        res = reanchor(row, a.base)
        if isinstance(res, str):
            failed.append(f"{row['id']}: {res}")
            continue
        row["original"], row["replacement"] = res
        fixed += 1
    for f in failed:
        print("未能重锚", f)
    print(f"重锚 {fixed} 条，失败 {len(failed)} 条")
    if a.write and not failed:
        indent = 1 if raw.startswith("{\n ") and not raw.startswith("{\n  ") else 2
        CATALOGUE.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
        print("已写回", CATALOGUE.relative_to(REPO))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
