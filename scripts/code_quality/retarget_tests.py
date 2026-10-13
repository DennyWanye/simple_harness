#!/usr/bin/env python3
"""代码质量整治：把测试里对 main 的取用与打桩改到名字现在所属的 host 模块（只改导入路径与打桩位置）。

用法：
    python3 scripts/code_quality/retarget_tests.py <host 模块名> 名字1,名字2,... 测试文件...
        [--patch-also 名字A,名字B]
- `main.X`（X 在名字清单里）改成 `<模块>.X`；
- `setattr(main, "X", ...)`、`patch.object(main, "X")` 改成对 `<模块>`；
- --patch-also 里的名字只改打桩位置（被搬走的函数内部调用它们，打桩要打在新模块上）；
- 文件顶部补 `from host import <模块>`。
改完请跑这些测试，并按计划 4.4 把改动列入本片记录（属"改导入路径"类，不改断言）。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("names")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--patch-also", default="")
    a = ap.parse_args()
    names = {n for n in a.names.split(",") if n}
    patch_also = {n for n in a.patch_also.split(",") if n}
    for f in a.files:
        p = Path(f)
        s = p.read_text(encoding="utf-8")
        before = s
        aliases = set(re.findall(r"^\s*import main as (\w+)", s, re.M))
        if re.search(r"^\s*import main\b(?! as)", s, re.M):
            aliases.add("main")
        for al in aliases:
            for nm in names:
                s = re.sub(rf"\b{al}\.{re.escape(nm)}\b", f"{a.module}.{nm}", s)
            for nm in names | patch_also:
                s = re.sub(rf"((?:setattr|patch\.object)\(\s*){al}(\s*,\s*[\"']{re.escape(nm)}[\"'])",
                           rf"\g<1>{a.module}\g<2>", s)
        if s != before and not re.search(rf"^from host import .*\b{a.module}\b", s, re.M):
            tree = ast.parse(s)
            last = max((n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
            lines = s.splitlines(keepends=True)
            lines.insert(last, f"from host import {a.module}\n")
            s = "".join(lines)
        if s != before:
            p.write_text(s, encoding="utf-8")
            print(f"改写 {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
