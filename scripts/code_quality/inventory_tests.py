#!/usr/bin/env python3
"""代码质量整治：测试盘点（计划 0.11、4.4）。

把后端与 SDK 的测试文件分三类，重构前知道哪些会因为"形状变了"而变红：
- 字节钉死：断言里有 64 位十六进制摘要，或对源码/产物算 sha256 再比。
- 结构耦合：用 ast 解析产品源码、读 .py 源文本、inspect.getsource，或打桩私有名字
  （monkeypatch/patch 一个下划线开头的属性）。拆文件、改私有名字会让它们变红。
- 行为：其余。
同一文件可同时属于前两类。输出按模块目录汇总的表，和结构耦合最重的文件清单。

用法：python3 scripts/code_quality/inventory_tests.py [--md 输出.md]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = {"后端": REPO / "backend/tests", "SDK": REPO / "sdk/simple-harness-sdk/tests"}
HEX64 = re.compile(r"['\"][0-9a-f]{64}['\"]")
BYTES = re.compile(r"sha256\(.*(read_bytes|read_text|getsource)|hexdigest\(\)\s*==|== *['\"][0-9a-f]{64}")
STRUCT = re.compile(
    r"ast\.parse\(|inspect\.getsource|\.py['\"]\)\.read_text|read_text\([^)]*\)[^\n]*\bdef\b|"
    r"(monkeypatch\.setattr|patch(\.object)?)\([^)\n]*['\"]?[\w.]*\._[a-z]\w*")
PRIVATE_PATCH = re.compile(r"(monkeypatch\.setattr|patch(?:\.object)?)\(\s*[\w.]+,\s*['\"]_[a-z]\w*['\"]|"
                           r"patch\(['\"][\w.]+\._[a-z]\w*['\"]")


def classify(text: str) -> set[str]:
    kinds: set[str] = set()
    if HEX64.search(text) or BYTES.search(text):
        kinds.add("字节钉死")
    n_private = len(PRIVATE_PATCH.findall(text))
    if STRUCT.search(text) or n_private:
        kinds.add("结构耦合")
    return kinds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    a = ap.parse_args()
    lines = ["# 测试盘点（计划 0.11）", "",
             "由 scripts/code_quality/inventory_tests.py 生成。分类是启发式，用于排序重构风险，不是精确判定。", ""]
    heavy: list[tuple[int, str]] = []
    for tree, root in ROOTS.items():
        by_dir: dict[str, Counter] = defaultdict(Counter)
        for p in sorted(root.rglob("test_*.py")):
            text = p.read_text(encoding="utf-8", errors="replace")
            kinds = classify(text)
            top = p.relative_to(root).parts[0] if len(p.relative_to(root).parts) > 1 else "(根目录)"
            by_dir[top]["文件"] += 1
            for k in kinds:
                by_dir[top][k] += 1
            if not kinds:
                by_dir[top]["行为"] += 1
            n_private = len(PRIVATE_PATCH.findall(text))
            if n_private:
                heavy.append((n_private, p.relative_to(REPO).as_posix()))
        total = Counter()
        for c in by_dir.values():
            total.update(c)
        lines += [f"## {tree}", "", f"合计 {total['文件']} 个文件：行为 {total['行为']}，"
                  f"字节钉死 {total['字节钉死']}，结构耦合 {total['结构耦合']}。", "",
                  "| 目录 | 文件 | 行为 | 字节钉死 | 结构耦合 |", "|---|---|---|---|---|"]
        for d, c in sorted(by_dir.items(), key=lambda kv: -kv[1]["文件"]):
            lines.append(f"| {d} | {c['文件']} | {c['行为']} | {c['字节钉死']} | {c['结构耦合']} |")
        lines.append("")
    lines += ["## 打桩私有名字最多的 30 个文件", "",
              "拆函数、改私有名字时这些最先变红。按第 4.4 节：只许改导入路径与构造方式，改期望值要签字。", "",
              "| 次数 | 文件 |", "|---|---|"]
    for n, f in sorted(heavy, reverse=True)[:30]:
        lines.append(f"| {n} | {f} |")
    text = "\n".join(lines) + "\n"
    if a.md:
        Path(a.md).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
