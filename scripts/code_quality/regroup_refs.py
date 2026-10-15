#!/usr/bin/env python3
"""代码质量整治 5B.1：编排器之外对已分组字段的读写改到新位置（配合 group_orchestrator_state.py）。

1. `表达式._字段`（表达式指向编排器：loop、world.loop、orch、orchestrator、fake 等）→ `表达式._组.字段`；
2. 测试假对象 `SimpleNamespace(_字段=值, …)` → `SimpleNamespace(_组=状态类(字段=值), …)`，补导入。
用法：python3 scripts/code_quality/regroup_refs.py 文件...
只改给定文件；改完逐个跑相关测试核对。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "group_state", Path(__file__).with_name("group_orchestrator_state.py")
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.argv, _argv = [sys.argv[0], "."], sys.argv
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
sys.argv = _argv
FIELD, GROUPS = _mod.FIELD, _mod.GROUPS
ORCH_EXPR = r"(?:case\.world\.loop|world\.loop|self\._orch|self\.orchestrator|self\._orchestrator|orchestrator|handler|loop|orch|fake)"
PAT = re.compile(rf"\b({ORCH_EXPR})\.({'|'.join(sorted(map(re.escape, FIELD), key=len, reverse=True))})\b")
STATE_MOD = "agent_orchestrator.orchestrator.orchestrator_domains.state"


def rewrite(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    n = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        group, name = FIELD[m.group(2)]
        return f"{m.group(1)}.{group}.{name}"

    text = PAT.sub(sub, text)
    # SimpleNamespace(...) 假对象的关键字参数
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    needed: set[str] = set()
    edits = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and ast.unparse(call.func).endswith("SimpleNamespace")):
            continue
        grouped = [k for k in call.keywords if k.arg in FIELD]
        if not grouped:
            continue
        by_group: dict[str, list[str]] = {}
        for k in grouped:
            group, name = FIELD[k.arg]
            by_group.setdefault(group, []).append(f"{name}={ast.get_source_segment(text, k.value)}")
        kept = [ast.get_source_segment(text, a) for a in call.args] + [
            (f"{k.arg}={ast.get_source_segment(text, k.value)}" if k.arg else f"**{ast.get_source_segment(text, k.value)}")
            for k in call.keywords if k.arg not in FIELD
        ]
        for group, args in by_group.items():
            cls = GROUPS[group][0]
            needed.add(cls)
            kept.append(f"{group}={cls}({', '.join(args)})")
        new = f"{ast.get_source_segment(text, call.func)}({', '.join(kept)})"
        edits.append((call.lineno, call.col_offset, call.end_lineno, call.end_col_offset, new))
        n += 1
    for l1, c1, l2, c2, new in sorted(edits, reverse=True):
        head = lines[l1 - 1].encode()[:c1].decode()
        tail = lines[l2 - 1].encode()[c2:].decode()
        lines[l1 - 1:l2] = [head + new + tail]
    text = "".join(lines)
    if needed:
        imp = f"from {STATE_MOD} import {', '.join(sorted(needed))}\n"
        tree = ast.parse(text)
        imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
        at = imports[-1].end_lineno if imports else 0
        lines = text.splitlines(keepends=True)
        lines.insert(at, imp)
        text = "".join(lines)
    path.write_text(text, encoding="utf-8")
    return n


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(arg, rewrite(Path(arg)))
