#!/usr/bin/env python3
"""代码质量整治阶段 7：宽捕获（except Exception / BaseException / 裸 except）的清单与分类。

用法：
    python3 scripts/code_quality/except_inventory.py [--tree backend|sdk|all]
        [--json 输出文件] [--list 类别]

文件范围与 metrics.py 一致。每处记录：
- 甲 静默：处理体只有 pass / continue / 返回空值（metrics 的 except_broad_silent）。
- 乙 边界层：宿主入口与路由、工具、提供方与网络、检索、MCP；finally 里、清理收尾函数里、
  while 后台循环里。
- 丙 内部层：其余。
- 是否重抛、是否记日志、日志是否带异常信息、except 行是否写了原因（noqa: BLE001 - 原因）。
完成口径（阶段 7 裁决）：产品代码（后端去掉脚本、SDK 源码，都去掉编解码钉死文件）里，
甲、乙必须重抛或记带异常信息的日志；丙还可以在 except 行写原因。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__" and sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import (  # noqa: E402  编解码钉死文件（含工作流定义）不许改，不计入完成口径
    CODEC_PINNED,
    FORBIDDEN_PY,
)

TREES = {"backend": REPO / "backend", "sdk": REPO / "sdk/simple-harness-sdk/src"}
SKIP_DIRS = {
    "vendor",
    "dist",
    "dist-1",
    "build",
    "userdata",
    ".venv",
    "__pycache__",
    "tests",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
}
FORBIDDEN_FILES = {"backend/deskpet/workflows/store/schema.py"}
LOG_NAMES = {"logger", "log", "_log", "_logger", "LOGGER", "logging", "structlog"}
CLEANUP_WORDS = (
    "close",
    "shutdown",
    "cleanup",
    "clean_up",
    "stop",
    "cancel",
    "release",
    "dispose",
    "teardown",
    "aclose",
    "__del__",
    "__exit__",
    "__aexit__",
    "unlink",
    "remove",
)
BOUNDARY_PREFIXES = (
    "backend/host/",
    "backend/main.py",
    "backend/providers/",
    "backend/deskpet/mcp/",
    "backend/deskpet/tools/",
    "backend/router/",
    "backend/llm/",
    "backend/deskpet/retrieval/",
)


def py_files(tree: Path):
    for dp, dn, fn in os.walk(tree):
        dn[:] = [
            d
            for d in dn
            if d not in SKIP_DIRS and not d.endswith(".egg-info") and not d.startswith("dist")
        ]
        for f in fn:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                yield Path(dp) / f


def broad(h: ast.ExceptHandler) -> bool:
    t = h.type
    if t is None:
        return True
    names = [t] if not isinstance(t, ast.Tuple) else list(t.elts)
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names)


def real_body(h: ast.ExceptHandler) -> list[ast.stmt]:
    return [
        s for s in h.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]


def silent(h: ast.ExceptHandler) -> bool:
    body = real_body(h)
    if not body:
        return True
    return all(
        isinstance(s, (ast.Pass, ast.Continue))
        or (
            isinstance(s, ast.Return)
            and (
                s.value is None
                or (isinstance(s.value, ast.Constant) and s.value.value in (None, False, 0, ""))
            )
        )
        for s in body
    )


def shape(h: ast.ExceptHandler) -> str:
    body = real_body(h)
    if not body or all(isinstance(s, ast.Pass) for s in body):
        return "pass"
    if any(isinstance(s, ast.Continue) for s in body):
        return "continue"
    if all(isinstance(s, ast.Return) for s in body):
        return "return"
    return "other"


def logs(h: ast.ExceptHandler) -> bool:
    for n in ast.walk(ast.Module(body=h.body, type_ignores=[])):
        if isinstance(n, ast.Call):
            f = n.func
            root = f
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and (root.id in LOG_NAMES or root.id.endswith("logger")):
                return True
            if isinstance(f, ast.Attribute) and f.attr in ("warn", "print_exc", "exception"):
                return True
    return False


def logs_exc(h: ast.ExceptHandler) -> bool:
    """处理体里的日志调用带异常信息：exc_info=... 关键字、.exception(...)，或 error_type=...
    （只记异常类型，用于异常消息可能含隐私数据的地方）。"""
    for n in ast.walk(ast.Module(body=h.body, type_ignores=[])):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and (
                n.func.attr == "exception"
                or any(k.arg in ("exc_info", "error_type") for k in n.keywords)
            )
        ):
            return True
    return False


REASON = re.compile(r"#\s*(noqa:\s*BLE001\s*[-—:：]\s*\S|宽捕获[：:]\s*\S)")


def reraises(h: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(ast.Module(body=h.body, type_ignores=[])))


def is_product(rel: str) -> bool:
    """完成口径的范围：后端去掉脚本，SDK 源码；都去掉编解码钉死文件与禁区（表结构、迁移、能力包模板）。"""
    return (
        not rel.startswith("backend/scripts/")
        and rel not in CODEC_PINNED
        and not FORBIDDEN_PY.search(rel)
        and "/capabilities/templates/" not in rel  # 已发布能力包模板是执行池准入身份，不许改
    )


def unexplained(row: dict) -> bool:
    """完成口径（阶段 7 裁决）：甲、乙必须重抛或记带异常信息的日志；丙还可以在 except 行写原因。"""
    if row["reraises"] or row["logs_exc"]:
        return False
    return not (row["class"] == "丙" and row["reason"])


PRINT_REASON = re.compile(r"#\s*noqa:\s*T201\s*[-—:：]\s*\S")


def scan(tree_name: str):
    out = []
    for path in py_files(TREES[tree_name]):
        rel = path.relative_to(REPO).as_posix()
        if rel in FORBIDDEN_FILES:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        for row in file_rows(rel, src, ast.parse(src)):
            row["tree"] = tree_name
            out.append(row)
    return out


def file_rows(rel: str, src: str, tree: ast.Module) -> list[dict]:
    """一个文件里每处宽捕获一行。"""
    out: list[dict] = []
    lines = src.splitlines()
    parents: dict[int, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p

    def enclosing(node):
        chain = []
        cur = parents.get(id(node))
        while cur is not None:
            chain.append(cur)
            cur = parents.get(id(cur))
        return chain

    for n in ast.walk(tree):
        if not (isinstance(n, ast.ExceptHandler) and broad(n)):
            continue
        chain = enclosing(n)
        func = next(
            (c for c in chain if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))), None
        )
        try_node = chain[0] if chain and isinstance(chain[0], ast.Try) else None
        in_finally = any(
            isinstance(c, ast.Try) and any(id(x) == id(chain[i - 1]) for x in c.finalbody)
            for i, c in enumerate(chain)
            if i > 0
        )
        in_loop = (
            any(
                isinstance(c, (ast.While, ast.For, ast.AsyncFor))
                for c in chain[: chain.index(func)]
            )
            if func
            else False
        )
        fname = func.name if func else "<module>"
        first = (
            ast.unparse(try_node.body[0]).split("\n")[0][:120] if try_node and try_node.body else ""
        )
        is_silent = silent(n)
        if is_silent:
            cls = "甲"
        elif (
            rel.startswith(BOUNDARY_PREFIXES)
            or in_finally
            or any(w in fname.lower() for w in CLEANUP_WORDS)
            or in_loop
            and any(isinstance(c, ast.While) for c in chain)
        ):
            cls = "乙"
        else:
            cls = "丙"
        reason = bool(REASON.search(lines[n.lineno - 1]))
        explained = reraises(n) or logs_exc(n) or reason  # 旧口径，只作参考
        row = {
            "file": rel,
            "line": n.lineno,
            "func": fname,
            "class": cls,
            "silent": is_silent,
            "shape": shape(n),
            "logs": logs(n),
            "reraises": reraises(n),
            "in_finally": in_finally,
            "in_loop": in_loop,
            "cleanup_func": any(w in fname.lower() for w in CLEANUP_WORDS),
            "try_first": first,
            "logs_exc": logs_exc(n),
            "reason": reason,
            "explained": explained,
            "product": is_product(rel),
        }
        row["unexplained"] = unexplained(row)
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="all")
    ap.add_argument("--json")
    ap.add_argument("--list")
    a = ap.parse_args()
    trees = list(TREES) if a.tree == "all" else [a.tree]
    rows = [r for t in trees for r in scan(t)]
    for t in trees:
        rs = [r for r in rows if r["tree"] == t]
        c = Counter(r["class"] for r in rs)
        nolog = Counter(
            r["class"] for r in rs if not r["logs"] and not r["reraises"] and not r["silent"]
        )
        print(
            f"{t}: 宽捕获 {len(rs)}；甲 静默 {c['甲']}，乙 边界 {c['乙']}，丙 内部 {c['丙']}；"
            f"不静默但既不记日志也不重抛：乙 {nolog['乙']}、丙 {nolog['丙']}"
        )
        un = Counter(r["class"] for r in rs if r["unexplained"] and r["product"])
        only_exc = sum(1 for r in rs if r["unexplained"] and r["product"] and r["logs"])
        print(
            f"   产品代码未达完成口径：甲 {un['甲']}，乙 {un['乙']}，丙 {un['丙']}；"
            f"其中已有日志只差异常信息：{only_exc}"
        )
        sil = [r for r in rs if r["silent"]]
        print(
            "   甲 细分：清理收尾",
            sum(1 for r in sil if r["in_finally"] or r["cleanup_func"]),
            "循环里跳过",
            sum(1 for r in sil if r["shape"] == "continue"),
            "返回空值",
            sum(1 for r in sil if r["shape"] == "return"),
            "其余 pass",
            sum(
                1
                for r in sil
                if r["shape"] == "pass" and not (r["in_finally"] or r["cleanup_func"])
            ),
        )
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.list:
        for r in rows:
            if r["class"] == a.list:
                print(f"{r['file']}:{r['line']} {r['func']} [{r['shape']}] {r['try_first']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
