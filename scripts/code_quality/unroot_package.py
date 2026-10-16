#!/usr/bin/env python3
"""代码质量整治阶段 6E：包根不再转出子模块的名字，导入方改成从定义所在的子模块导入。

用法：
    python3 scripts/code_quality/unroot_package.py <包名，如 deskpet.execution> [--dry-run]

做法：
1. 读包的 __init__.py：只允许文档字符串、`from .子模块 import 名字[ as 别名]`、`__all__`；
   建"包根名字 → 子模块里的原名字"对照表。别的写法一律拒绝。
2. 扫描 backend 下全部 .py（产品代码、测试、脚本，排除 .venv）：凡是解析到这个包的
   `from 包 import 名字`（绝对或相对写法），按子模块分组改写成 `from 包.子模块 import 原名字 as 本地名`
   （原名与本地名相同时不写 as）；相对写法保持相对。名字本身是子模块的不动。
3. 有 `import 包` / `import 包 as x` 后按属性取名字的、或字符串打桩 "包.名字" 的，列出来并拒绝（先人工处理）。
4. 把 __init__.py 改成只剩文档字符串。
改完跑 ruff 格式化与导入排序（仓库根口径），再用 module_refs.py 复查。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def module_of(path: Path) -> str:
    if not path.is_relative_to(BACKEND):
        return "__script__." + path.stem  # 仓库根脚本：只会用绝对导入
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def absolute(node: ast.ImportFrom, importer: str, is_pkg: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = importer.split(".")
    if not is_pkg:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join([*base, *([node.module] if node.module else [])])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pkg = a.package
    init = BACKEND / Path(*pkg.split(".")) / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"))
    table: dict[str, tuple[str, str]] = {}  # 包根名字 -> (子模块, 原名字)
    doc = None
    for i, n in enumerate(tree.body):
        if i == 0 and isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            doc = ast.get_source_segment(init.read_text(encoding="utf-8"), n)
            continue
        if isinstance(n, ast.ImportFrom) and n.module == "__future__":
            continue
        if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module:
            for al in n.names:
                table[al.asname or al.name] = (n.module, al.name)
            continue
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            # 包根转出的别处名字：导入方改成从那个模块（绝对路径）导入
            for al in n.names:
                table[al.asname or al.name] = ("=" + n.module, al.name)
            continue
        if (
            isinstance(n, (ast.Assign, ast.AnnAssign))
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in ast.walk(n))
        ):
            continue
        sys.exit(f"__init__.py 第 {n.lineno} 行不是转出导入，拒绝")
    submods = {p.stem for p in init.parent.glob("*.py") if p.stem != "__init__"} | {
        p.name for p in init.parent.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    }
    edits: dict[Path, list[tuple[ast.ImportFrom, str]]] = defaultdict(list)
    problems = []
    # 仓库根下的脚本也会从 backend 包导入（如 scripts/bench）
    candidates = [*BACKEND.rglob("*.py"), *(REPO / "scripts").rglob("*.py")]
    for path in candidates:
        if ".venv" in path.parts or path == init:
            continue
        text = path.read_text(encoding="utf-8")
        if pkg not in text and "from ." not in text:
            continue
        try:
            t = ast.parse(text)
        except SyntaxError:
            continue
        me = module_of(path)
        is_pkg = path.name == "__init__.py"
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                for al in n.names:
                    if al.name == pkg:
                        problems.append(f"{path}:{n.lineno} import {pkg}")
            if not isinstance(n, ast.ImportFrom):
                continue
            if absolute(n, me, is_pkg) != pkg:
                continue
            groups: dict[str, list[str]] = defaultdict(list)
            for al in n.names:
                local = al.asname or al.name
                if al.name in submods and al.name in table:
                    # 旧包根把同名函数转出、遮住了子模块：清空后这里会拿到模块而不是函数
                    problems.append(f"{path}:{n.lineno} {al.name} 既是子模块又是旧转出的同名定义")
                    continue
                if al.name in submods:
                    groups[""].append(f"{al.name}" + (f" as {al.asname}" if al.asname else ""))
                    continue
                if al.name not in table:
                    problems.append(f"{path}:{n.lineno} 包根没有转出 {al.name}")
                    continue
                sub, orig = table[al.name]
                groups[sub].append(orig if orig == local else f"{orig} as {local}")
            prefix = "." * n.level + (n.module or "") if n.level else pkg
            stmts = []
            for sub, names in groups.items():
                if sub == "":
                    stmts.append(f"from {prefix} import {', '.join(names)}")
                elif sub.startswith("="):
                    stmts.append(f"from {sub[1:]} import {', '.join(names)}")
                else:
                    joiner = "" if prefix.endswith(".") else "."
                    stmts.append(f"from {prefix}{joiner}{sub} import {', '.join(names)}")
            edits[path].append((n, stmts))
        for m in re.finditer(rf"[\"']{re.escape(pkg)}\.([A-Za-z_]\w*)", text):
            if m.group(1) in table and m.group(1) not in submods:
                problems.append(f"{path}: 字符串引用 {pkg}.{m.group(1)}")
    if problems:
        print("需要先处理：")
        for p in sorted(set(problems)):
            print("  " + p)
        return 1
    total = sum(len(v) for v in edits.values())
    print(f"改写 {total} 条导入，涉及 {len(edits)} 个文件")
    if a.dry_run:
        for path, items in sorted(edits.items()):
            for n, stmts in items:
                print(f"  {path}:{n.lineno} → {' ; '.join(stmts)}")
        return 0
    ruff = str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")
    cfg = str(REPO / "scripts/code_quality/ruff.toml")
    # 只给改前导入排序就合规的文件重排导入（不合规的文件不动它别的导入顺序）
    clean = [
        str(p)
        for p in edits
        if subprocess.run(
            [ruff, "check", "--no-cache", "--force-exclude", "--config", cfg, "--select", "I", "-q", str(p)],
            cwd=REPO,
            capture_output=True,
        ).returncode
        == 0
    ]
    for path, items in edits.items():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for n, stmts in sorted(items, key=lambda x: -x[0].lineno):
            pad = " " * n.col_offset
            new = "".join(f"{pad}{s}\n" for s in stmts)
            lines[n.lineno - 1 : n.end_lineno] = [new]
        path.write_text("".join(lines), encoding="utf-8")
    init.write_text(((doc or f'"""{pkg}"""') + "\n"), encoding="utf-8")
    files = [str(p) for p in edits] + [str(init)]
    if clean:
        subprocess.run(
            [ruff, "check", "--no-cache", "--force-exclude", "--config", cfg, "--select", "I", "--fix", "-q", *clean],
            cwd=REPO,
            check=False,
        )
    subprocess.run([ruff, "format", "--force-exclude", "--config", cfg, "-q", *files], cwd=REPO, check=False)
    print("完成；__init__.py 只剩文档字符串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
