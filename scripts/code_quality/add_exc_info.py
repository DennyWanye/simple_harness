#!/usr/bin/env python3
"""代码质量整治阶段 7B：宽捕获处理块里已有日志、但不带异常信息的，给日志调用补 exc_info=True。

只改"未说明"的宽捕获（不重抛、日志不带异常信息、except 行没写原因），且只在产品代码范围内
（口径见 except_inventory.py）。每个处理块只改第一条记录器调用
（直接在处理体里，不进内层函数）。
白名单（阶段 7 裁决）：接收者是由 logging.getLogger / structlog.get_logger 赋值的
模块顶层名字（本模块定义或从别的模块导入），或 logging 模块本身；
方法是 debug/info/warning/error/critical。白名单外的、带 **kwargs 展开的跳过并列出。

用法：
    python3 scripts/code_quality/add_exc_info.py [--dry-run] 路径 ...
改完要跑 ruff format（--force-exclude）与 handler_diff.py。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from except_inventory import broad, file_rows, is_product  # noqa: E402

LEVELS = {"debug", "info", "warning", "error", "critical"}
FACTORIES = {
    ("logging", "getLogger"),
    ("structlog", "get_logger"),
    ("structlog.stdlib", "get_logger"),
}


ROOTS = (REPO / "backend", REPO / "sdk/simple-harness-sdk/src")


def _resolve(path: Path, node: ast.ImportFrom) -> Path | None:
    """把 from X import name 解析成源文件（相对导入按本文件位置，绝对导入按后端与 SDK 根目录）。"""
    if node.level:
        base = path.parent
        for _ in range(node.level - 1):
            base = base.parent
        bases = [base]
    else:
        bases = list(ROOTS)
    parts = (node.module or "").split(".") if node.module else []
    for b in bases:
        target = b.joinpath(*parts) if parts else b
        for cand in (target.with_suffix(".py"), target / "__init__.py"):
            if cand.is_file():
                return cand
    return None


def module_loggers(path: Path, tree: ast.Module, depth: int = 0) -> set[str]:
    """本模块顶层由 logging.getLogger / structlog.get_logger 赋值的名字，以及从别的模块导入、
    在那里这样赋值的名字（白名单，阶段 7 裁决：在本模块里确认记录器来源）。"""
    names: set[str] = set()
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            v = stmt.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute):
                owner = ast.unparse(v.func.value)
                if (owner, v.func.attr) in FACTORIES:
                    names.add(stmt.targets[0].id)
        elif isinstance(stmt, ast.ImportFrom) and depth < 3:
            wanted = {a.name: a.asname or a.name for a in stmt.names}
            if not any("log" in n.lower() for n in wanted):
                continue
            src_path = _resolve(path, stmt)
            if src_path is None:
                continue
            found = module_loggers(
                src_path, ast.parse(src_path.read_text(encoding="utf-8")), depth + 1
            )
            names |= {alias for orig, alias in wanted.items() if orig in found}
    return names


def _is_logger_call(n: ast.AST, loggers: set[str]) -> bool:
    if not (
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LEVELS
    ):
        return False
    recv = n.func.value
    return isinstance(recv, ast.Name) and (recv.id in loggers or recv.id == "logging")


def _first_log_call(h: ast.ExceptHandler, loggers: set[str]) -> ast.Call | None:
    stack = list(reversed(h.body))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if _is_logger_call(n, loggers):
            return n  # type: ignore[return-value]
        stack.extend(reversed(list(ast.iter_child_nodes(n))))
    return None


def _offset(lines: list[str], lineno: int, col: int) -> int:
    return sum(len(x) for x in lines[: lineno - 1]) + len(
        lines[lineno - 1].encode("utf-8")[:col].decode("utf-8")
    )


def patch_file(path: Path, dry: bool) -> tuple[int, list[str]]:
    src = path.read_text(encoding="utf-8")
    rel = path.resolve().relative_to(REPO).as_posix()
    if not is_product(rel):
        return 0, []
    tree = ast.parse(src)
    loggers = module_loggers(path.resolve(), tree)
    keep = src.splitlines(keepends=True)
    edits: list[int] = []
    skipped: list[str] = []
    rows = {r["line"]: r for r in file_rows(rel, src, tree)}
    for h in ast.walk(tree):
        if not (isinstance(h, ast.ExceptHandler) and broad(h)):
            continue
        row = rows[h.lineno]
        if not (row["unexplained"] and row["logs"]):
            continue
        call = _first_log_call(h, loggers)
        if call is None:
            skipped.append(f"{rel}:{h.lineno} 日志调用不在白名单（记录器来源不明）")
            continue
        if any(k.arg is None for k in call.keywords):
            skipped.append(f"{rel}:{call.lineno} 带 **kwargs")
            continue
        edits.append(_offset(keep, call.end_lineno, call.end_col_offset) - 1)  # 指向右括号
    out = src
    for pos in sorted(edits, reverse=True):
        assert out[pos] == ")", (rel, pos, out[pos - 20 : pos + 1])
        j = pos - 1
        while out[j] in " \t\r\n":
            j -= 1
        if out[j] == ",":
            out = out[: j + 1] + " exc_info=True," + out[j + 1 :]
        elif out[j] == "(":
            out = out[:pos] + "exc_info=True" + out[pos:]
        else:
            out = out[: j + 1] + ", exc_info=True" + out[j + 1 :]
    if edits and not dry:
        path.write_text(out, encoding="utf-8")
    return len(edits), skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    total = 0
    files = 0
    for p in a.paths:
        base = Path(p)
        targets = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for f in targets:
            if "/tests/" in f.as_posix() or f.name.startswith("test_") or "/.venv/" in f.as_posix():
                continue
            n, skipped = patch_file(f, a.dry_run)
            for s in skipped:
                print(f"跳过：{s}")
            if n:
                files += 1
                total += n
    print(f"{'预演' if a.dry_run else '已改'}：{files} 个文件，{total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
