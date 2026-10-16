#!/usr/bin/env python3
"""代码质量整治阶段 6D：拆函数后查"可能未赋值就读"的新问题。

抽段时若把"只在某个分支里赋值、另一分支读的是段前的值"的名字误判成段内局部变量，就会少传参数，
走到那条路时报 UnboundLocalError。对参照提交与工作区的同一文件各跑一次 mypy 的 possibly-undefined
检查（--check-untyped-defs），按"所在函数 + 名字"比对，列出新增的。

用法：python3 scripts/code_quality/unbound_audit.py <参照提交> <文件>...
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MYPY = REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/mypy"
PAT = re.compile(r":(\d+): error: Name \"(\w+)\" may be undefined")


def enclosing(tree: ast.Module, line: int) -> str:
    best, size = "<module>", 10**9
    for n in ast.walk(tree):
        if (
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.lineno <= line <= n.end_lineno
        ):
            if n.end_lineno - n.lineno < size:
                best, size = n.name, n.end_lineno - n.lineno
    return best


def findings(text: str, name: str) -> set[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_text(text, encoding="utf-8")
        res = subprocess.run(
            [
                str(MYPY),
                "--config-file",
                str(REPO / "scripts/code_quality/mypy-backend.ini"),
                "--enable-error-code",
                "possibly-undefined",
                "--check-untyped-defs",
                "--no-incremental",
                "--follow-imports",
                "skip",
                str(p),
            ],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
    tree = ast.parse(text)
    out = set()
    for m in PAT.finditer(res.stdout):
        out.add((enclosing(tree, int(m.group(1))), m.group(2)))
    return out


def main() -> int:
    ref, files = sys.argv[1], sys.argv[2:]
    bad = 0
    for f in files:
        rel = Path(f).resolve().relative_to(REPO).as_posix()
        old = subprocess.run(
            ["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, cwd=REPO
        ).stdout
        new = (REPO / rel).read_text(encoding="utf-8")
        added = findings(new, Path(rel).name) - findings(old, Path(rel).name)
        for func, var in sorted(added):
            print(f"{rel}：{func} 里 {var} 可能未赋值（新增）")
            bad += 1
    print(f"新增可能未赋值 {bad} 处")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
