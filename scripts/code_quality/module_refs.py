#!/usr/bin/env python3
"""代码质量整治阶段 5：列出外部代码对某个模块的全部引用，拆模块前后各跑一次比对。

扫描 SDK 与后端的源码、测试、脚本，找出对模块 M 的：
  from M import 名字 / import M as 别名 后的 别名.名字（读、写）/ setattr、patch.object、delattr(别名, "名字")
  patch("M.名字") 字符串打桩 / 读 M 源文件文本（inspect.getsource(别名)、路径里含模块文件名）
输出每个名字在 M 里是怎么绑定的：本模块定义 / 从别处导入（"转手"：打桩只改 M 的绑定，真正读它的代码
可能在别的模块，打桩会静默失效）/ 不存在（会 AttributeError 或打桩加了没人读的属性）。

用法：python3 scripts/code_quality/module_refs.py agent_orchestrator.orchestrator.event_handler
退出码：有"不存在"或"转手打桩"时为 1。
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
SDK_SRC = REPO / "sdk/simple-harness-sdk/src"
ROOTS = [REPO / "sdk/simple-harness-sdk/src", REPO / "sdk/simple-harness-sdk/tests",
         REPO / "sdk/simple-harness-sdk/scripts", REPO / "backend", REPO / "scripts"]
SKIP = {".venv", "vendor", "__pycache__", "node_modules", "dist", "build"}


def bindings(path: Path) -> tuple[set[str], set[str]]:
    """模块顶层：本模块定义的名字、从别处导入的名字。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    own: set[str] = set()
    imported: set[str] = set()
    for n in tree.body:
        nodes = n.body if isinstance(n, ast.If) else [n]
        for x in nodes:
            if isinstance(x, (ast.Import, ast.ImportFrom)):
                imported |= {(a.asname or a.name).split(".")[0] for a in x.names}
            elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                own.add(x.name)
            elif isinstance(x, (ast.Assign, ast.AnnAssign)):
                targets = x.targets if isinstance(x, ast.Assign) else [x.target]
                own |= {t.id for t in targets if isinstance(t, ast.Name)}
    return own, imported - own


def absolute(path: Path, node: ast.ImportFrom) -> str | None:
    """把相对导入换成绝对模块名（只认 SDK 源码里的相对导入）。"""
    if not node.level:
        return node.module
    try:
        parts = list(path.relative_to(SDK_SRC).with_suffix("").parts)
    except ValueError:
        return None
    base = parts[: len(parts) - node.level]
    return ".".join(base + ([node.module] if node.module else []))


def main() -> int:
    mod = sys.argv[1]
    path = SDK_SRC / (mod.replace(".", "/") + ".py")
    own, imported = bindings(path)
    leaf = path.name
    rows: list[tuple[str, str, int, str]] = []  # (种类, 文件, 行, 名字)
    for root in ROOTS:
        for p in sorted(root.rglob("*.py")):
            if set(p.parts) & SKIP or p == path:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if mod not in text and leaf not in text and mod.rsplit(".", 1)[-1] not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            rel = p.relative_to(REPO).as_posix()
            aliases: set[str] = set()
            parent, _, short = mod.rpartition(".")
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    aliases |= {a.asname for a in n.names if a.name == mod and a.asname}
                elif isinstance(n, ast.ImportFrom) and n.module == parent:
                    aliases |= {a.asname or a.name for a in n.names if a.name == short}
                elif isinstance(n, ast.ImportFrom) and absolute(p, n) == mod:
                    rows += [("导入", rel, n.lineno, a.name) for a in n.names]
                elif isinstance(n, ast.ImportFrom) and absolute(p, n) == parent:
                    aliases |= {a.asname or a.name for a in n.names if a.name == short}
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                    rows.append(("写" if isinstance(n.ctx, ast.Store) else "读", rel, n.lineno, n.attr))
                elif isinstance(n, ast.Call) and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) \
                        and n.args[0].id in aliases and isinstance(n.args[1], ast.Constant):
                    rows.append(("打桩", rel, n.lineno, str(n.args[1].value)))
                elif isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Name) \
                        and n.args[0].id in aliases and isinstance(n.func, ast.Attribute) \
                        and n.func.attr == "getsource":
                    rows.append(("读源码", rel, n.lineno, "-"))
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    if n.value.startswith(mod + "."):
                        rows.append(("字符串打桩", rel, n.lineno, n.value[len(mod) + 1:].split(".")[0]))
                    elif n.value.endswith(leaf) and "/" in n.value:
                        rows.append(("读源码", rel, n.lineno, n.value))
    bad = 0
    for kind, rel, line, name in sorted(set(rows), key=lambda r: (r[1], r[2])):
        if name in own or kind == "读源码" and name == "-":
            state = "本模块定义" if name in own else ""
        elif name in imported:
            state = "转手"
        elif kind == "读源码":
            state = ""
        else:
            state = "不存在"
        flag = state == "不存在" or (state == "转手" and kind in ("打桩", "写", "字符串打桩"))
        bad += flag
        print(f"{'！' if flag else ' '} {kind:5} {rel}:{line} {name} {state}")
    print(f"模块引用：{len(set(rows))} 处，待看 {bad} 处")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
