# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""代码质量整治阶段 10：只改了注释的核对（阶段10-切片设计.md 裁决执行条件 3）。

用法（在仓库根目录运行）：
    python3 scripts/code_quality/comment_only_check.py [--ref HEAD] [--module-docstring] [文件...]
不给文件时取工作区相对 ref 改过的 .py/.ts/.tsx。

Python：两版用 tokenize 切词，去掉 COMMENT 与 NL 后（类型, 原文）序列逐个相同才通过；
  不做任何规范化（文档字符串、类型注解、字面量写法都算）。
  --module-docstring：另外允许模块文档字符串（文件第一条语句）的文字不同（10C 只改模块文件头）。
TS/TSX：交给 ts_comment_only_check.mjs（语法树全部词法叶子逐个相同）。
"""
from __future__ import annotations

import argparse
import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.ENCODING}


def module_docstring_line(src: str) -> int | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first.lineno
    return None


def py_tokens(src: str, allow_doc: bool) -> list[tuple[int, str, int]]:
    doc_line = module_docstring_line(src) if allow_doc else None
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in SKIP:
            continue
        text = tok.string
        if doc_line is not None and tok.type == tokenize.STRING and tok.start[0] == doc_line:
            text = "<模块文档字符串>"
            doc_line = None
        out.append((tok.type, text, tok.start[0]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--module-docstring", action="store_true")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    files = a.files or subprocess.run(["git", "diff", "--name-only", a.ref], cwd=REPO, capture_output=True, text=True,
                                      check=True).stdout.split()
    py = [f for f in files if f.endswith(".py")]
    tsf = [f for f in files if f.endswith((".ts", ".tsx"))]
    other = [f for f in files if f not in py and f not in tsf]
    bad = 0
    for f in py:
        old = subprocess.run(["git", "show", f"{a.ref}:{f}"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        new = (REPO / f).read_text(encoding="utf-8")
        x, y = py_tokens(old, a.module_docstring), py_tokens(new, a.module_docstring)
        i = 0
        while i < len(x) and i < len(y) and x[i][:2] == y[i][:2]:
            i += 1
        if i == len(x) == len(y):
            continue
        bad += 1
        print(f"  {f}：注释之外有变化（原第 {x[i][2] if i < len(x) else '?'} 行 {x[i][1] if i < len(x) else None!r}"
              f" → 现第 {y[i][2] if i < len(y) else '?'} 行 {y[i][1] if i < len(y) else None!r}）")
    print(f"Python 只改注释核对：{len(py)} 个文件，{'不通过' if bad else '通过'}")
    if other:
        print(f"  另有非源码文件改动（不在本核对内）：{other}")
    rc = 1 if bad else 0
    if tsf:
        rc |= subprocess.run(["node", str(REPO / "scripts/code_quality/ts_comment_only_check.mjs"), a.ref, *tsf], cwd=REPO).returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
