#!/usr/bin/env python3
"""代码质量整治阶段 5G：把一个文件里标了"按需导入：循环依赖（…）"、导入某个模块的函数内导入提到模块顶部。

用法：python3 scripts/code_quality/lift_tagged.py <文件> <被导入模块（照写在 from 后面的样子，如 .contracts）>
做法：删掉函数里"标注行 + 紧随的 from 导入"，把导入的名字合并成一条顶层 from 导入（放在第一条顶层导入前，
ruff 随后排序）；被导入模块是否真的不再成环，由调用者先用 cycle_paths.py / 导入冒烟确认。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
TAG = "# 按需导入：循环依赖"


def main() -> int:
    path, module = Path(sys.argv[1]), sys.argv[2]
    level = len(module) - len(module.lstrip("."))
    name = module.lstrip(".") or None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    top_ids = {id(n) for n in tree.body}
    picked: list[ast.ImportFrom] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and id(node) not in top_ids and node.level == level
                and node.module == name and lines[node.lineno - 2].strip().startswith(TAG)):
            picked.append(node)
    if not picked:
        sys.exit(f"{path} 里没有标了循环依赖、导入 {module} 的函数内导入")
    names: list[str] = []
    for node in picked:
        for alias in node.names:
            text_alias = alias.name + (f" as {alias.asname}" if alias.asname else "")
            if text_alias not in names:
                names.append(text_alias)
    for node in sorted(picked, key=lambda n: n.lineno, reverse=True):
        del lines[node.lineno - 2 : node.end_lineno]  # 标注行 + 导入语句
    first = min(n.lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                and not (isinstance(n, ast.ImportFrom) and n.module == "__future__"))
    removed_before = sum(n.end_lineno - n.lineno + 2 for n in picked if n.lineno < first)
    lines.insert(first - 1 - removed_before, f"from {module} import {', '.join(names)}\n")
    path.write_text("".join(lines), encoding="utf-8")
    ruff = os.environ.get("RUFF") or str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")
    config = str(REPO / "scripts/code_quality/ruff.toml")
    subprocess.run([ruff, "check", "--config", config, "--select", "I001,F401", "--fix", "--quiet", str(path)], check=False)
    subprocess.run([ruff, "format", "--config", config, "--quiet", str(path)], check=False)
    print(f"{path.name}：上提 {len(picked)} 处导入 {module}（{', '.join(names)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
