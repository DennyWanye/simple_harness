#!/usr/bin/env python3
"""代码质量整治阶段 3.5：给留在函数里的导入补固定口径的原因注释。

口径（2.4 修订）：可选依赖 / 重型包 / 平台专用 / 测试替身 / 导入隔离 / 按需声明 / 循环依赖 / 死代码待删。
注释写在导入上方单独一行：`# 按需导入：<类别>（细节）`；同一块里相邻、类别相同的导入共用一行。
已有按需说明（本行或上方注释、整个文件声明）的算"按需声明"，不再加。禁区与脚本目录不动。

用法：
  python3 scripts/code_quality/tag_inner_imports.py --tree backend|sdk [--dry-run]   # 补注释
  python3 scripts/code_quality/tag_inner_imports.py --check                           # 统计；有没注释的退出码 1
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lift_imports as L  # noqa: E402
from import_graph import build_graph, module_name, reachable, resolve  # noqa: E402
from inner_imports import BACKEND, SDK_SRC, tree_files  # noqa: E402

TAG = "# 按需导入："
TAGGED = re.compile(r"#\s*按需导入：(\S+?)(（|$)")
KINDS = {"stdlib", "internal", "third_party"}


def category(reason: str) -> tuple[str, str]:
    """keep 原因 → (类别, 细节)。"""
    if reason in ("optional",) or reason.startswith("第三方在 try"):
        return "可选依赖", ""
    if reason.startswith(("第三方", "可选或重型标准库")):
        return "重型包", ""
    if reason.startswith("会提前加载重型包"):
        return "重型包", reason.removeprefix("会提前加载重型包").strip()
    if reason.startswith("平台"):
        return "平台专用", ""
    if reason.startswith("测试换掉了模块"):
        return "测试替身", reason.removeprefix("测试换掉了模块").strip()
    if reason.startswith("测试对"):
        return "测试替身", reason.removeprefix("测试对").split(" 打桩", 1)[0].strip()
    if reason.startswith("会破坏导入隔离"):
        return "导入隔离", ""
    if reason.startswith(("本行注释", "上方注释", "文件声明")):
        return "按需声明", ""
    if reason == "会成环":
        return "循环依赖", ""
    if reason.startswith("目标不存在"):
        return "死代码待删", reason.removeprefix("目标不存在：").split("（", 1)[0]
    if reason == "type_checking":
        return "仅类型检查", ""
    return "其他", reason


def _short(mod: str) -> str:
    return mod.rsplit(".", 1)[-1]


def file_plan(path: Path, me: str, graph, patched, contracts, stubbed):
    """返回 [(导入语句, 类别, 细节)]，只含留在函数里的导入。"""
    src, tree, decisions, _plans = L.analyse_file(path, me, graph, patched, KINDS, contracts, stubbed)
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def in_try(st) -> bool:
        node = st
        while id(node) in parents:
            par = parents[id(node)]
            if isinstance(par, ast.Try) and node in par.body:
                return True
            if isinstance(par, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return False
            node = par
        return False

    out = []
    for st, verdict, reason, _funcs in decisions:
        if verdict != "keep":
            out.append((st, "可上提", reason))
            continue
        cat, detail = category(reason)
        if cat == "其他" and in_try(st):  # 防御性导入：失败也不许打断调用方
            cat, detail = "可选依赖", ""
        if cat == "循环依赖":
            back = [t for t in resolve(st, me, path) if reachable(graph, t, me)]
            detail = f"{_short(me)} ↔ {_short(max(back, key=len))}" if back else ""
        out.append((st, cat, detail))
    return src, tree, out


def already_tagged(lines: list[str], st) -> bool:
    if L.LAZY_LINE.search(lines[st.lineno - 1]) or L.lazy_comment_above(lines, st.lineno - 2):
        return True
    i = st.lineno - 2
    while i >= 0:
        text = lines[i].strip()
        if text.startswith("#"):
            if TAGGED.search(text):
                return True
            i -= 1
            continue
        if text.startswith(("import ", "from ")) or text.endswith((",", "(", ")")) or text == "":
            i -= 1
            continue
        return False
    return False


def iter_files(tree: str):
    base = BACKEND if tree == "backend" else SDK_SRC
    for path in tree_files(tree):
        rel = path.relative_to(base)
        if L.is_forbidden(str(path.relative_to(L.REPO))) or rel.parts[0] == "scripts" or "/scripts/" in f"/{rel}":
            continue
        try:
            me = module_name(path)
        except KeyError:
            continue
        yield path, me


def tag_tree(tree: str, dry_run: bool) -> Counter:
    graph = build_graph()
    patched, contracts, stubbed = L.patched_targets(), L.import_contracts(tree), L.stubbed_modules()
    stats: Counter = Counter()
    for path, me in iter_files(tree):
        text = path.read_text(encoding="utf-8")
        if L.LAZY_DECLARED.search(L.without_tags(text)):
            continue
        src, _tree, plan = file_plan(path, me, graph, patched, contracts, stubbed)
        lines = src.splitlines(keepends=True)
        plain = [ln.rstrip("\n") for ln in lines]
        inserts: list[tuple[int, str]] = []
        prev_end, prev_cat = None, None
        for st, cat, detail in sorted(plan, key=lambda x: x[0].lineno):
            if already_tagged(plain, st):
                stats["已有说明"] += 1
                prev_end, prev_cat = st.end_lineno, None
                continue
            stats[cat] += 1
            # 与上一条紧挨着、类别和细节都相同：共用上一行注释（循环依赖逐条标，便于计数）
            if prev_end is not None and st.lineno == prev_end + 1 and (cat, detail) == prev_cat \
                    and cat != "循环依赖":
                prev_end = st.end_lineno
                continue
            indent = " " * st.col_offset
            comment = f"{indent}{TAG}{cat}" + (f"（{detail}）" if detail else "")
            if len(comment) > 100:
                comment = f"{indent}{TAG}{cat}"
            inserts.append((st.lineno - 1, comment + "\n"))
            prev_end, prev_cat = st.end_lineno, (cat, detail)
        if inserts and not dry_run:
            for i, c in sorted(inserts, reverse=True):
                lines.insert(i, c)
            path.write_text("".join(lines), encoding="utf-8")
    return stats


def check() -> int:
    """统计每类数量；没有注释的列出来。"""
    total: Counter = Counter()
    missing = []
    for tree in ("backend", "sdk"):
        for path, _me in iter_files(tree):
            text = path.read_text(encoding="utf-8")
            plain = text.splitlines()
            t = ast.parse(text)
            declared = bool(L.LAZY_DECLARED.search(L.without_tags(text)))

            def walk(node, in_func):
                for ch in ast.iter_child_nodes(node):
                    if in_func and isinstance(ch, (ast.Import, ast.ImportFrom)):
                        yield ch
                    yield from walk(ch, in_func or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)))

            for st in walk(t, False):
                i, cat = st.lineno - 2, None
                while i >= 0 and cat is None:
                    s = plain[i].strip()
                    m = TAGGED.search(s)
                    if m:
                        cat = m.group(1)
                    elif s.startswith("#") or s.startswith(("import ", "from ")) or s.endswith((",", "(", ")")) or not s:
                        i -= 1
                    else:
                        break
                if cat is None and (declared or L.LAZY_LINE.search(plain[st.lineno - 1])
                                    or L.lazy_comment_above(plain, st.lineno - 2)):
                    cat = "按需声明"
                if cat is None:
                    missing.append(f"{path.relative_to(L.REPO)}:{st.lineno}")
                total[cat or "无注释"] += 1
    for k, v in total.most_common():
        print(f"{v:5d} {k}")
    for m in missing[:50]:
        print("无注释", m)
    print(f"函数内导入（不含禁区与脚本）：{sum(total.values())}；无注释 {len(missing)}")
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", choices=["backend", "sdk"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--strip", action="store_true", help="删掉本工具加的注释行（重标前用）")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.strip:
        for path, _me in iter_files(a.tree):
            text = path.read_text(encoding="utf-8")
            new = re.sub(r"^[ \t]*# 按需导入：[^\n]*\n", "", text, flags=re.M)
            if new != text:
                path.write_text(new, encoding="utf-8")
        return 0
    stats = tag_tree(a.tree, a.dry_run)
    for k, v in stats.most_common():
        print(f"{v:5d} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
