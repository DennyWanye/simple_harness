#!/usr/bin/env python3
"""代码质量整治阶段 4：把后端的一个模块整个搬到新位置，并改写全部引用（不留转发）。

用法：python3 scripts/code_quality/move_module.py <旧模块> <新模块> [--dry-run]
例：  python3 scripts/code_quality/move_module.py deskpet.execution.dispatch providers.dispatch_handoff

改写范围（backend 下全部 .py，含测试与脚本）：
- `from 旧模块 import …`、相对导入解析到旧模块的；
- `from 旧父包 import 旧名[ as x]`（按模块导入的写法）：改成 `from 新父包 import 新名[ as x]`，
  本地名字保持不变（新旧名不同时加别名）；
- `import 旧模块 as x`；不带别名的 `import 旧模块` 会用点号全名访问，工具拒绝，手工处理；
- 测试里的字符串路径 "旧模块" 与 "旧模块.名字"（打桩目标）。
搬家用 git mv；被搬模块自己的相对导入也会拒绝（叶子模块不该有）。收尾跑 ruff 排序与格式化。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    import os

    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
RUFF = REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
SKIP = {".venv", "vendor", "__pycache__", "build", "dist", "dist-1", "userdata"}


def module_path(mod: str) -> Path:
    return BACKEND / (mod.replace(".", "/") + ".py")


def module_of(path: Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def absolute(node: ast.ImportFrom, path: Path) -> str:
    if not node.level:
        return node.module or ""
    me = module_of(path)
    pkg = me if path.name == "__init__.py" else me.rpartition(".")[0]
    for _ in range(node.level - 1):
        pkg = pkg.rpartition(".")[0]
    return f"{pkg}.{node.module}" if node.module else pkg


def rewrite_file(path: Path, old: str, new: str) -> list[str]:
    src = path.read_text(encoding="utf-8")
    if old.rsplit(".", 1)[-1] not in src:
        return []
    tree = ast.parse(src)
    old_parent, _, old_leaf = old.rpartition(".")
    new_parent, _, new_leaf = new.rpartition(".")
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    for n in ast.walk(tree):
        ind = " " * getattr(n, "col_offset", 0)
        if isinstance(n, ast.ImportFrom):
            base = absolute(n, path)
            if base == old:
                text = ast.unparse(ast.ImportFrom(new, n.names, 0))
                edits.append((n.lineno, n.end_lineno, ind + text))
            elif base == old_parent and any(a.name == old_leaf for a in n.names):
                keep = [a for a in n.names if a.name != old_leaf]
                moved = next(a for a in n.names if a.name == old_leaf)
                local = moved.asname or old_leaf
                alias = ast.alias(new_leaf, None if local == new_leaf else local)
                lines = []
                if keep:
                    lines.append(ind + ast.unparse(ast.ImportFrom(n.module, keep, n.level)))
                lines.append(ind + ast.unparse(ast.ImportFrom(new_parent, [alias], 0)))
                edits.append((n.lineno, n.end_lineno, "\n".join(lines)))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name == old or a.name.startswith(old + "."):
                    if not a.asname:
                        raise SystemExit(f"{path}:{n.lineno} 用 `import {a.name}` 不带别名，请手工改")
            if any(a.name == old for a in n.names):
                names = [ast.alias(new if a.name == old else a.name, a.asname) for a in n.names]
                edits.append((n.lineno, n.end_lineno, ind + ast.unparse(ast.Import(names))))
    lines = src.splitlines(keepends=True)
    for a, b, text in sorted(edits, reverse=True):
        lines[a - 1 : b] = [text + "\n"]
        notes.append(f"{path.relative_to(REPO)}:{a}")
    out = "".join(lines)
    # 字符串路径（打桩目标、动态导入）
    pat = re.compile(r"""(["'])""" + re.escape(old) + r"""(?=[."'])""")
    out2 = pat.sub(lambda m: m.group(1) + new, out)
    if out2 != out:
        notes.append(f"{path.relative_to(REPO)}: 字符串路径")
    if out2 != src:
        path.write_text(out2, encoding="utf-8")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src, dst = module_path(a.old), module_path(a.new)
    if not src.exists() or dst.exists():
        raise SystemExit(f"源不存在或目标已存在：{src} → {dst}")
    moved_tree = ast.parse(src.read_text(encoding="utf-8"))
    if any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(moved_tree)):
        raise SystemExit("被搬模块里有相对导入，请先改成绝对导入")
    if a.dry_run:
        print(f"将搬 {src.relative_to(REPO)} → {dst.relative_to(REPO)}")
        return 0
    subprocess.run(["git", "mv", str(src), str(dst)], cwd=REPO, check=True)
    changed: list[str] = []
    for path in BACKEND.rglob("*.py"):
        if set(path.relative_to(BACKEND).parts) & SKIP:
            continue
        changed += rewrite_file(path, a.old, a.new)
    files = sorted({str(REPO / c.split(":")[0]) for c in changed})
    if files:
        cfg = str(REPO / "scripts/code_quality/ruff.toml")
        subprocess.run([str(RUFF), "check", "--config", cfg, "--select", "I001", "--fix", "-q", *files], check=False)
        subprocess.run([str(RUFF), "format", "--force-exclude", "--config", cfg, "-q", *files], check=False)
    for c in changed:
        print("改写", c)
    print(f"搬家完成：{a.old} → {a.new}，改写 {len(files)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
