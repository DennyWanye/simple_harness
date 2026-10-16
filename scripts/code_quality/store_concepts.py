#!/usr/bin/env python3
"""代码质量整治 8.5：六个存储类的认领、结清、提交三类方法并排列出（只列不合并）。

静态解析每个存储类及其在仓库里定义的基类（mixin），按方法名里的动词归类：
- 认领：claim / lease / acquire / reserve / lock / fence / heartbeat / admit
- 结清：settle / release / finish / finalize / complete / terminal / close / abort / fail / reconcile / resolve / expire / cancel
- 提交：commit / record / write / append / insert / put / save / publish / apply / persist / upsert / store_
用法：python3 scripts/code_quality/store_concepts.py --md 输出.md
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STORES = [
    ("SDK 执行存储", "sdk/simple-harness-sdk/src/simple_harness/execution/sqlite/uow.py", "SqliteExecutionUnitOfWork"),
    ("SDK 编排存储", "sdk/simple-harness-sdk/src/agent_orchestrator/storage/store.py", "Store"),
    ("后端工作流执行存储", "backend/deskpet/workflows/store/execution_uow.py", "SqliteExecutionUnitOfWork"),
    ("伴侣存储", "backend/deskpet/companion/store.py", "CompanionStore"),
    ("会话账本", "backend/deskpet/memory/session_db.py", "SessionDB"),
    ("能力存储", "backend/deskpet/capabilities/store.py", "CapabilityStore"),
]
KINDS = [
    ("认领", re.compile(r"claim|lease|acquire|reserve|lock|fence|heartbeat|admit")),
    ("结清", re.compile(r"settle|release|finish|finaliz|complete|terminal|close|abort|fail|reconcile|resolve|expire|cancel")),
    ("提交", re.compile(r"commit|record|write|append|insert|put|save|publish|apply|persist|upsert|^store_")),
]


def _resolve(path: Path, tree: ast.Module, name: str) -> tuple[Path, ast.ClassDef] | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return path, node
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
            orig = next(a.name for a in node.names if (a.asname or a.name) == name)
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                cands = [base.joinpath(*(node.module or "").split("."))]
            else:
                parts = (node.module or "").split(".")
                cands = [REPO / "backend" / Path(*parts), REPO / "sdk/simple-harness-sdk/src" / Path(*parts)]
            for c in cands:
                for f in (c.with_suffix(".py"), c / "__init__.py"):
                    if f.is_file():
                        got = _resolve(f, ast.parse(f.read_text(encoding="utf-8")), orig)
                        if got:
                            return got
    return None


def methods_of(path: Path, cls: str) -> set[str]:
    seen: set[str] = set()
    out: set[str] = set()
    stack = [(path, cls)]
    while stack:
        p, name = stack.pop()
        if (p, name) in seen:
            continue
        seen.add((p, name))
        got = _resolve(p, ast.parse(p.read_text(encoding="utf-8")), name)
        if not got:
            continue
        fp, node = got
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("__"):
                out.add(n.name)
        for b in node.bases:
            if isinstance(b, ast.Name):
                stack.append((fp, b.id))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    a = ap.parse_args()
    lines = ["| 存储 | 方法总数 | 认领 | 结清 | 提交 |", "|---|---|---|---|---|"]
    detail = []
    for label, rel, cls in STORES:
        ms = methods_of(REPO / rel, cls)
        groups = {k: sorted(m for m in ms if pat.search(m.lstrip("_"))) for k, pat in KINDS}
        lines.append(f"| {label}（{cls}） | {len(ms)} | {len(groups['认领'])} | {len(groups['结清'])} | {len(groups['提交'])} |")
        detail.append(f"\n### {label}（{rel} 的 {cls}）\n")
        for k, _ in KINDS:
            detail.append(f"- **{k}**（{len(groups[k])}）：" + ("、".join(f"`{m}`" for m in groups[k]) or "无"))
    Path(a.md).write_text("\n".join(lines) + "\n" + "\n".join(detail) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
