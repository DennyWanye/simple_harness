#!/usr/bin/env python3
"""比对两次全量回归的失败名单。新增失败 = 回归，退出码 1。

用法：python3 scripts/code_quality/compare_regression.py <基线 summary.json> <新 summary.json>
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    base = json.load(open(sys.argv[1], encoding="utf-8"))
    new = json.load(open(sys.argv[2], encoding="utf-8"))
    bad = False
    for part in ("host", "frontend", "sdk"):
        if part not in base or part not in new:
            continue
        # 超时被杀的文件按"整文件失败"计入
        b = set(base[part]["failed"]) | {f"{x}::<STALL>" for x in base[part].get("stalls", [])}
        n = set(new[part]["failed"]) | {f"{x}::<STALL>" for x in new[part].get("stalls", [])}
        added, fixed = sorted(n - b), sorted(b - n)
        print(f"== {part}: 基线失败 {len(b)}，现在失败 {len(n)}，新增 {len(added)}，转绿 {len(fixed)}")
        print(f"   用例数 {base[part]['tests']} -> {new[part]['tests']}")
        for x in added:
            print(f"   新增失败 {x}")
        if added:
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
