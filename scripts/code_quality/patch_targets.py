#!/usr/bin/env python3
"""代码质量整治：检查测试对某模块的打桩与取值是否还指向正确的位置（拆模块后每片必跑）。

函数搬到新模块后，测试里 `monkeypatch.setattr(main, "X", ...)` 只改了 main 的名字绑定；
新模块里的函数读的是自己模块的 X，打桩静默失效（raising=False 时连报错都没有）。
本脚本扫描 backend/tests 里对 main（含 `import main as 别名`）的：
  monkeypatch.setattr(别名, "X", ...)、patch.object(别名, "X")、patch("main.X")、别名.X 取值
标出两类问题：
  缺失：main 已不再绑定 X（取值会 AttributeError，raising=False 的打桩会静默加一个没人读的属性）
  转手：main 里的 X 是从 host.* 导入的，真正读它的函数可能在那个模块里，打桩要改到那里
用法：python3 scripts/code_quality/patch_targets.py [--module main]
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]


def main_bindings(path: Path) -> tuple[set[str], dict[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined: set[str] = set()
    via_host: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("host.") and n.col_offset == 0:
            for a in n.names:
                via_host[a.asname or a.name] = n.module
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            defined.add(n.id)
    return defined - set(via_host), via_host


def host_readers(name: str) -> list[str]:
    """host/ 下哪些模块在函数或模块级读取这个名字。"""
    out = []
    for f in sorted((REPO / "backend/host").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        if any(isinstance(x, ast.Name) and x.id == name and isinstance(x.ctx, ast.Load) for x in ast.walk(tree)):
            out.append(f.stem)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default="main")
    a = ap.parse_args()
    mod = a.module
    defined, via_host = main_bindings(REPO / "backend" / f"{mod}.py")
    problems: list[str] = []
    for p in sorted((REPO / "backend/tests").rglob("*.py")):
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        aliases: set[str] = set()
        reads: list[tuple[str, int]] = []
        patches: list[tuple[str, int]] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for al in n.names:
                    if al.name == mod:
                        aliases.add(al.asname or mod)
            if isinstance(n, ast.ImportFrom) and n.module == mod and n.level == 0:
                for al in n.names:
                    reads.append((al.name, n.lineno))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) \
                    and n.args[0].id in aliases and isinstance(n.args[1], ast.Constant) \
                    and isinstance(n.args[1].value, str):
                f = n.func
                fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if fname in ("setattr", "object", "delattr"):
                    patches.append((n.args[1].value, n.lineno))
            if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str) and n.args[0].value.startswith(mod + "."):
                f = n.func
                if (isinstance(f, ast.Name) and f.id == "patch") or (isinstance(f, ast.Attribute) and f.attr == "patch"):
                    patches.append((n.args[0].value.split(".", 1)[1].split(".")[0], n.lineno))
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                if isinstance(n.ctx, ast.Store):
                    patches.append((n.attr, n.lineno))
                else:
                    reads.append((n.attr, n.lineno))
        rel = p.relative_to(REPO).as_posix()
        for nm, ln in sorted(set(reads) | set(patches)):
            if nm.startswith("__"):
                continue
            if nm not in defined and nm not in via_host:
                problems.append(f"缺失 {rel}:{ln} {nm}")
        for nm, ln in sorted(set(patches)):
            if nm in via_host:
                rd = host_readers(nm)
                if rd:
                    problems.append(f"转手 {rel}:{ln} 打桩 {nm}，但 host 下这些模块也读它：{', '.join(rd)}")
    for pr in problems:
        print(pr)
    print(f"打桩位置检查：{len(problems)} 处待看")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
