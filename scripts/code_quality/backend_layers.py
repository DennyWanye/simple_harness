#!/usr/bin/env python3
"""代码质量整治阶段 4.3：后端单文件模块的分层检查（补 import-linter 收不了的部分）。

import-linter 的 root_packages 只收目录包，main、p4_ipc、provider_test_connection、config、
context、paths 这六个单文件模块由这里检查，层次与 backend/.importlinter 一致：
  0 main → 1 host → 2 上层适配（pipeline、router、billing、p4_ipc、provider_test_connection）
  → 3 deskpet、agent → 4 llm、providers、memory、tools → 5 observability、config、context → 6 paths
下层不许引用上层（含函数内导入，不含只在类型检查时生效的导入）。只报牵涉单文件模块的违例，
目录包之间的由 import-linter 管。

另外锁住 backend/.importlinter 的例外清单（ignore_imports）：只许删、不许加（裁决：交阶段 6 解开）。

用法：python3 scripts/code_quality/backend_layers.py   # 有违例退出码 1
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
LAYERS = [
    {"main"},
    {"host"},
    {"pipeline", "router", "billing", "p4_ipc", "provider_test_connection"},
    {"deskpet", "agent"},
    {"llm", "providers", "memory", "tools"},
    {"observability", "config", "context"},
    {"paths"},
]
LEVEL = {name: i for i, group in enumerate(LAYERS) for name in group}
SINGLE = {"main", "p4_ipc", "provider_test_connection", "config", "context", "paths"}
# 分层契约的登记例外：已清空，不许再加
ALLOWED_EXCEPTIONS: set[str] = set()
SKIP = {"tests", "scripts", ".venv", "vendor", "__pycache__", "build", "dist", "dist-1", "userdata"}


def top_of(path: Path) -> str:
    parts = path.relative_to(BACKEND).parts
    return parts[0][:-3] if len(parts) == 1 else parts[0]


def imports(tree: ast.Module):
    """全部导入（含函数内），跳过 `if TYPE_CHECKING:` 块。"""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        n = stack.pop()
        if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test):
            stack.extend(n.orelse)
            continue
        if isinstance(n, ast.Import):
            for a in n.names:
                yield n.lineno, a.name
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            yield n.lineno, n.module
        stack.extend(ast.iter_child_nodes(n))


def contract_exceptions() -> set[str]:
    """backend/.importlinter 里 ignore_imports 的各行。"""
    lines = (BACKEND / ".importlinter").read_text(encoding="utf-8").splitlines()
    out: set[str] = set()
    inside = False
    for line in lines:
        if line.strip().startswith("ignore_imports"):
            inside = True
            continue
        if inside:
            if line.startswith((" ", "\t")) and line.strip() and not line.strip().startswith("#"):
                out.add(" ".join(line.split()))
            elif line.strip() and not line.startswith((" ", "\t")):
                inside = False
    return out


def main() -> int:
    bad = [
        f"backend/.importlinter 新增了分层例外：{e}"
        for e in sorted(contract_exceptions() - ALLOWED_EXCEPTIONS)
    ]
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if set(rel.parts) & SKIP:
            continue
        src_top = top_of(path)
        if src_top not in LEVEL:
            continue
        for lineno, mod in imports(ast.parse(path.read_text(encoding="utf-8"))):
            dst_top = mod.split(".")[0]
            if dst_top not in LEVEL or dst_top == src_top:
                continue
            if not ({src_top, dst_top} & SINGLE):
                continue
            if LEVEL[src_top] > LEVEL[dst_top]:
                bad.append(f"{rel}:{lineno} {src_top}（第 {LEVEL[src_top]} 层）引用上层 {mod}")
    for b in bad:
        print("违例", b)
    print("后端单文件模块分层：" + ("通过" if not bad else f"{len(bad)} 处违例"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
