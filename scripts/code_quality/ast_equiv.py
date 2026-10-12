#!/usr/bin/env python3
"""代码质量整治：AST 等价检查（阶段 1 自动修复与格式化的证明工具）。

把每个改动过的 .py 文件，在某个提交与工作区之间做规范化后比 AST：
- 去掉文档字符串（格式化会调整其缩进）；
- 抹掉全部类型注解（新语法改写只动注解）；
- 相邻的导入语句按内容排序（导入排序只动顺序）。
- 下列已知安全改写还原成同一形式：
  无占位符的 f 字符串、encode/decode 的默认 utf-8 参数、getattr/setattr 常量名、
  asyncio.TimeoutError 与 IOError 等别名、timezone.utc 与 UTC、typing 与 collections.abc
  之间搬动的名字、一行多个 import 拆开或合并。
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

    TYPING_MODULES = {"typing", "collections.abc", "typing_extensions"}
    TZ_NAMES = {"timezone", "UTC"}

    def _flatten(self, run):
        """一段相邻导入 -> 规范化的单名导入集合（合并、拆分、typing 搬家都还原）。"""
        items = set()
        for st in run:
            if isinstance(st, ast.Import):
                for a in st.names:
                    items.add(("import", a.name, a.asname, 0))
            else:
                mod = "typing" if st.module in self.TYPING_MODULES else st.module
                for a in st.names:
                    if st.module == "datetime" and a.name in self.TZ_NAMES:
                        continue
                    items.add(("from", mod, a.name, a.asname, st.level))
        out = []
        for it in sorted(items, key=repr):
            if it[0] == "import":
                out.append(ast.Import(names=[ast.alias(name=it[1], asname=it[2])]))
            else:
                out.append(ast.ImportFrom(module=it[1], names=[ast.alias(name=it[2], asname=it[3])],
                                          level=it[4]))
        return out

    def _sort_imports(self, body):
        out, run = [], []
        for st in body:
            if isinstance(st, (ast.Import, ast.ImportFrom)) and not (
                    isinstance(st, ast.ImportFrom) and st.module == "__future__"):
                run.append(st)
                continue
            if run:
                out += self._flatten(run)
                run = []
            out.append(st)
        return out + self._flatten(run)

    # —— 已知安全改写的还原 ——
    def visit_JoinedStr(self, node):
        node = super().generic_visit(node)
        if all(isinstance(v, ast.Constant) for v in node.values):
            return ast.Constant("".join(str(v.value) for v in node.values))
        return node

    def visit_Call(self, node):
        node = super().generic_visit(node)
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("encode", "decode"):
            if len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant) \
                    and str(node.args[0].value).lower().replace("-", "").replace("_", "") == "utf8":
                node.args = []
            node.keywords = [k for k in node.keywords if not (
                k.arg == "encoding" and isinstance(k.value, ast.Constant)
                and str(k.value.value).lower().replace("-", "") == "utf8")]
        if isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) == 2 and not node.keywords \
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str) \
                and node.args[1].value.isidentifier():
            return ast.Attribute(value=node.args[0], attr=node.args[1].value, ctx=ast.Load())
        return node

    def visit_Expr(self, node):
        node = super().generic_visit(node)
        c = node.value
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "setattr" \
                and len(c.args) == 3 and isinstance(c.args[1], ast.Constant) \
                and isinstance(c.args[1].value, str) and c.args[1].value.isidentifier():
            return ast.Assign(targets=[ast.Attribute(value=c.args[0], attr=c.args[1].value,
                                                     ctx=ast.Store())], value=c.args[2], lineno=0)
        return node

    ALIASES = {("asyncio", "TimeoutError"): "TimeoutError", ("socket", "timeout"): "TimeoutError",
               ("socket", "error"): "OSError", ("select", "error"): "OSError",
               ("concurrent.futures", "TimeoutError"): "TimeoutError"}

    def visit_Attribute(self, node):
        node = super().generic_visit(node)
        if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in self.ALIASES:
            return ast.Name(id=self.ALIASES[(node.value.id, node.attr)], ctx=node.ctx)
        if node.attr == "UTC" or (node.attr == "utc" and (
                (isinstance(node.value, ast.Attribute) and node.value.attr == "timezone")
                or (isinstance(node.value, ast.Name) and node.value.id == "timezone"))):
            return ast.Name(id="UTC", ctx=node.ctx)
        return node

    def visit_Name(self, node):
        if node.id in ("IOError", "EnvironmentError", "WindowsError"):
            return ast.Name(id="OSError", ctx=node.ctx)
        return node

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
