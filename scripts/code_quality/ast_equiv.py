#!/usr/bin/env python3
"""代码质量整治：AST 等价检查（阶段 1 自动修复与格式化的证明工具）。

把每个改动过的 .py 文件，在某个提交与工作区之间做规范化后比 AST：
- 去掉文档字符串（格式化会调整其缩进）；
- 抹掉全部类型注解（新语法改写只动注解）；
- 相邻的导入语句按内容排序（导入排序只动顺序）。
规范化后仍不同的文件，逐个列出不同的顶层语句，交人工确认。

用法：python3 scripts/code_quality/ast_equiv.py [--ref HEAD] [--detail N]
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import os
import sys
from pathlib import Path

if sys.version_info < (3, 11):  # 系统自带 3.9 解析不了新语法，换用后端虚拟环境的解释器
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]


class Normalize(ast.NodeTransformer):
    def _strip_doc(self, node):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
        return node

    def _sort_imports(self, body):
        out, run = [], []
        for st in body:
            if isinstance(st, (ast.Import, ast.ImportFrom)) and not (
                    isinstance(st, ast.ImportFrom) and st.module == "__future__"):
                run.append(st)
                continue
            if run:
                out += sorted(run, key=ast.dump)
                run = []
            out.append(st)
        return out + sorted(run, key=ast.dump)

    def generic_visit(self, node):
        node = super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._sort_imports(getattr(node, field)))
        return node

    def visit_Module(self, node):
        return self.generic_visit(self._strip_doc(node))

    def visit_ClassDef(self, node):
        return self.generic_visit(self._strip_doc(node))

    def visit_FunctionDef(self, node):
        node = self._strip_doc(node)
        node.returns = None
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_AnnAssign(self, node):
        node.annotation = ast.Constant("<ann>")
        return self.generic_visit(node)


def norm(src: bytes) -> ast.Module | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    return Normalize().visit(tree)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--detail", type=int, default=3, help="每个不等价文件最多列几处")
    a = ap.parse_args()
    files = subprocess.run(["git", "diff", "--name-only", "--diff-filter=M", a.ref, "--", "*.py"],
                           cwd=REPO, capture_output=True, text=True).stdout.splitlines()
    same = 0
    diff_files: list[str] = []
    for f in files:
        old = subprocess.run(["git", "show", f"{a.ref}:{f}"], cwd=REPO, capture_output=True).stdout
        new = (REPO / f).read_bytes()
        n0, n1 = norm(old), norm(new)
        if n0 is None or n1 is None:
            diff_files.append(f)
            print(f"语法错误：{f}")
            continue
        if ast.dump(n0) == ast.dump(n1):
            same += 1
            continue
        diff_files.append(f)
        print(f"== 不等价：{f}")
        shown = 0
        b0, b1 = n0.body, n1.body
        for i in range(max(len(b0), len(b1))):
            s0 = ast.unparse(b0[i]) if i < len(b0) else "<无>"
            s1 = ast.unparse(b1[i]) if i < len(b1) else "<无>"
            if s0 != s1:
                import difflib
                for line in difflib.unified_diff(s0.splitlines(), s1.splitlines(), lineterm="", n=0):
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                        print("   ", line[:200])
                shown += 1
                if shown >= a.detail:
                    break
    print(f"AST 等价：{same} 个文件等价，{len(diff_files)} 个需人工确认（共改动 {len(files)} 个）")
    return 1 if diff_files else 0


if __name__ == "__main__":
    sys.exit(main())
