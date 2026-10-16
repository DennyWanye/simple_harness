#!/usr/bin/env python3
"""代码质量整治阶段 8：按清单删除死定义（函数、类、方法），顺带删掉因此变成无用的导入名。

清单一行一处：`文件:行 名字`（"#" 开头与空行跳过）。行号是定义所在行（def / class 行）。
只删"删除前在本模块里被用到、删除后一处都不用了"的模块顶层导入名，原本就没用的导入不动。
定义带装饰器的拒绝（阶段 8 裁决：装饰器注册的路由与处理器不能当死代码删）。

用法：
    python3 scripts/code_quality/delete_defs.py 清单 [--dry-run]
删完要跑 ruff format（--force-exclude）。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _used_names(tree: ast.AST, skip: set[int]) -> Counter:
    """模块里（跳过 skip 里的定义节点）用到的名字：Name 读、属性链的根名。"""
    out: Counter = Counter()
    stack = [tree]
    while stack:
        node = stack.pop()
        if id(node) in skip:
            continue
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
            out[node.id] += 1
        stack.extend(ast.iter_child_nodes(node))
    return out


def _imported(tree: ast.Module) -> dict[str, ast.stmt]:
    out: dict[str, ast.stmt] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for a in stmt.names:
                out[(a.asname or a.name).split(".")[0]] = stmt
    return out


def _string_mentions(tree: ast.Module, name: str) -> bool:
    """__all__ 或别的字符串里出现这个名字（再导出），就不删它的导入。"""
    return any(isinstance(n, ast.Constant) and n.value == name for n in ast.walk(tree))


def _module_of(path: Path) -> str | None:
    rel = path.relative_to(REPO).as_posix()
    for root in ("backend/", "sdk/simple-harness-sdk/src/"):
        if rel.startswith(root) and rel.endswith(".py"):
            parts = rel[len(root) : -3].split("/")
            return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    return None


def _reexported(path: Path, name: str) -> bool:
    """别的模块从本模块转手导入这个名字（from 本模块 import 名字 / 本模块.名字）。"""
    mod = _module_of(path)
    if not mod:
        return False
    out = subprocess.run(
        [
            "git",
            "grep",
            "-nE",
            rf"from {re.escape(mod)} import|{re.escape(mod)}\.{re.escape(name)}\b",
            "--",
            "*.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if re.search(rf"\b{re.escape(name)}\b", line.split(":", 2)[-1]):
            return True
    return False


def delete_in_file(path: Path, targets: list[tuple[int, str]], dry: bool) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.split("\n")
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for ln, name in targets:
                if node.lineno == ln and node.name == name:
                    if node.decorator_list:
                        raise SystemExit(f"{path}:{ln} {name} 带装饰器，不删")
                    nodes.append(node)
    found = {(n.lineno, n.name) for n in nodes}
    missing = [t for t in targets if t not in found]
    if missing:
        raise SystemExit(f"{path}: 找不到 {missing}")
    skip = {id(n) for n in nodes}
    before = _used_names(tree, set())
    after = _used_names(tree, skip)
    imported = _imported(tree)
    drop_names = {
        name
        for name, stmt in imported.items()
        if before[name] > 0
        and after[name] == 0
        and not _string_mentions(tree, name)
        and not _reexported(path, name)
    }
    notes = [f"{path.relative_to(REPO)}: 删 {n.name}（{n.lineno}-{n.end_lineno}）" for n in nodes]
    # 从下往上删定义（含紧挨着的上方注释行）
    for node in sorted(nodes, key=lambda n: -n.lineno):
        start = node.lineno - 1
        while start > 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        del lines[start : node.end_lineno]
    out = "\n".join(lines)
    if drop_names:
        tree2 = ast.parse(out)
        lines2 = out.split("\n")
        per_stmt: dict[int, list] = defaultdict(list)
        for stmt in tree2.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                keep = [
                    a for a in stmt.names if (a.asname or a.name).split(".")[0] not in drop_names
                ]
                if len(keep) != len(stmt.names):
                    per_stmt[stmt.lineno] = [stmt, keep]
        for ln in sorted(per_stmt, reverse=True):
            stmt, keep = per_stmt[ln]
            if keep:
                stmt.names = keep
                new = ast.unparse(stmt)
            else:
                new = None
            seg = lines2[stmt.lineno - 1 : stmt.end_lineno]
            indent = seg[0][: len(seg[0]) - len(seg[0].lstrip())]
            lines2[stmt.lineno - 1 : stmt.end_lineno] = [indent + new] if new else []
        out = "\n".join(lines2)
        notes.append(f"{path.relative_to(REPO)}: 删导入 {sorted(drop_names)}")
    ast.parse(out)
    if not dry:
        path.write_text(out, encoding="utf-8")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    by_file: dict[Path, list] = defaultdict(list)
    for raw in Path(a.spec).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        loc, name = raw.split()[:2]
        f, ln = loc.rsplit(":", 1)
        by_file[REPO / f].append((int(ln), name))
    n = 0
    for path, targets in by_file.items():
        for note in delete_in_file(path, targets, a.dry_run):
            print(note)
        n += len(targets)
    print(f"{'预演' if a.dry_run else '已删'}：{len(by_file)} 个文件，{n} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
