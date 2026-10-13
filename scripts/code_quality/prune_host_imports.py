#!/usr/bin/env python3
"""代码质量整治：删掉 main.py 从 host.* 导入、但 main.py 本身和测试都不再用的名字。

搬家时为了测试暂留的导入，测试改到新模块后就成了转发层；按"不留转发层"原则删除。
用法：python3 scripts/code_quality/prune_host_imports.py [--dry-run]
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parent))
from move_block import global_refs, remove_import_names  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "backend/main.py"


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    host_names: set[str] = set()
    body = []
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module and st.module.startswith("host."):
            host_names |= {a.asname or a.name for a in st.names}
        else:
            body.append(st)
    used = global_refs(ast.Module(body=body, type_ignores=[]))
    test_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                          for p in (REPO / "backend/tests").rglob("*.py"))
    via_tests = {n for n in host_names
                 if re.search(rf"\b(?:main|main_module|main_mod|_main)\.{re.escape(n)}\b", test_text)
                 or re.search(rf"from main import [^\n]*\b{re.escape(n)}\b", test_text)
                 or re.search(rf"setattr\(\s*(?:main|main_module|main_mod|_main)\s*,\s*[\"']{re.escape(n)}[\"']", test_text)}
    drop = sorted(host_names - used - via_tests)
    print("删除：", ", ".join(drop) if drop else "无")
    keep_for_tests = sorted((host_names - used) & via_tests)
    if keep_for_tests:
        print("仍只为测试保留：", ", ".join(keep_for_tests))
    if drop and "--dry-run" not in sys.argv:
        MAIN.write_text(remove_import_names(src, set(drop)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
