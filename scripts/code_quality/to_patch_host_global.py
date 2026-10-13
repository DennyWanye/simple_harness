#!/usr/bin/env python3
"""代码质量整治：把测试里对主入口的打桩改成 patch_host_global（主入口与 host 模块一起换）。

用法：python3 scripts/code_quality/to_patch_host_global.py 名字1,名字2 测试文件...
只改 ``monkeypatch.setattr(main 或其别名, "名字", ...)``，支持跨行写法；文件顶部补导入。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def main() -> int:
    names = [n for n in sys.argv[1].split(",") if n]
    for f in sys.argv[2:]:
        p = Path(f)
        s = p.read_text(encoding="utf-8")
        aliases = {"main"} | set(re.findall(r"^\s*import main as (\w+)", s, re.M))
        total = 0
        for al in aliases:
            for nm in names:
                s, n = re.subn(
                    rf"monkeypatch\.setattr\(\s*{al}\s*,\s*([\"']){re.escape(nm)}\1\s*,",
                    f'patch_host_global(monkeypatch, "{nm}",',
                    s,
                )
                total += n
        if total and "from tests.host_patch import patch_host_global" not in s:
            tree = ast.parse(s)
            last = max(x.end_lineno for x in tree.body if isinstance(x, (ast.Import, ast.ImportFrom)))
            lines = s.splitlines(keepends=True)
            lines.insert(last, "from tests.host_patch import patch_host_global\n")
            s = "".join(lines)
        if total:
            p.write_text(s, encoding="utf-8")
            print(f"改写 {f}：{total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
