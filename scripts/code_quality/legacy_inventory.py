#!/usr/bin/env python3
"""代码质量整治阶段 8D：旧版字样（legacy / deprecated）分类清单。

范围：backend 与 SDK src 的产品代码（不含测试）。每处出现按所在位置分三类：
- 注释或文档串
- 标识符（函数、类、变量、参数、属性、导入名里含旧版字样）
- 字符串字面量（多为协议值、落库值、错误码，不能随手改）
标识符再按名字汇总，统计全仓引用（产品、测试，按词边界），并标出所在区域：
- 禁区：禁区与编解码钉死文件
- 数据：读写数据库或会话数据的模块（记忆层、存储、迁移、会话账本），本阶段不删（裁决第 2 条）
- 其余：可进一步判断的兼容分支候选

用法：
    python3 scripts/code_quality/legacy_inventory.py --md 清单.md [--json 输出]
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import CODEC_PINNED, FORBIDDEN_PY  # noqa: E402

WORD = re.compile(r"legacy|deprecated", re.I)
ROOTS = ("backend/", "sdk/simple-harness-sdk/src/")
DATA_HINT = re.compile(
    r"(/memory/|/storage/|/store|_store|schema|migrat|session_db|/sqlite/|ledger|/persistence/)"
)


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout
    return [f for f in out.split() if f.endswith(".py")]


def is_test(f: str) -> bool:
    return "/tests/" in f or f.rsplit("/", 1)[-1].startswith(("test_", "conftest"))


def area(f: str) -> str:
    if FORBIDDEN_PY.search(f) or f in CODEC_PINNED:
        return "禁区"
    if DATA_HINT.search(f):
        return "数据"
    return "其余"


def classify(src: str) -> tuple[dict[int, str], set[str]]:
    """逐个 token 判类：每行取最"硬"的一类（标识符 > 字符串 > 注释）。返回行→类与含旧版字样的标识符。"""
    kinds: dict[int, str] = {}
    idents: set[str] = set()
    rank = {"注释或文档串": 0, "字符串字面量": 1, "标识符": 2}
    doc_lines: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return kinds, idents
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc_lines |= set(range(body[0].lineno, body[0].end_lineno + 1))
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if not WORD.search(tok.string):
            continue
        if tok.type == tokenize.COMMENT:
            k = "注释或文档串"
        elif tok.type == tokenize.STRING:
            k = "注释或文档串" if tok.start[0] in doc_lines else "字符串字面量"
        elif tok.type == tokenize.NAME:
            k = "标识符"
            idents.add(tok.string)
        else:
            continue
        for ln in range(tok.start[0], tok.end[0] + 1):
            if WORD.search(src.splitlines()[ln - 1]) and rank[k] >= rank.get(kinds.get(ln, ""), -1):
                kinds[ln] = k
    return kinds, idents


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    files = tracked()
    texts = {f: (REPO / f).read_text(encoding="utf-8", errors="replace") for f in files}
    prod = [f for f in files if f.startswith(ROOTS) and not is_test(f)]
    per_file: dict[str, dict[str, int]] = {}
    ident_home: dict[str, set[str]] = defaultdict(set)
    for f in prod:
        if not WORD.search(texts[f]):
            continue
        kinds, idents = classify(texts[f])
        c: dict[str, int] = defaultdict(int)
        for k in kinds.values():
            c[k] += 1
        per_file[f] = dict(c)
        for i in idents:
            ident_home[i].add(f)
    rows = []
    for name, homes in sorted(ident_home.items()):
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        p = t = 0
        for f, tx in texts.items():
            n = len(pat.findall(tx))
            if not n:
                continue
            if is_test(f):
                t += n
            elif f.startswith(ROOTS) or f.startswith("tauri-app/"):
                p += n
        areas = sorted({area(h) for h in homes})
        rows.append({"name": name, "files": sorted(homes), "product": p, "test": t, "areas": areas})
    tot: dict[str, int] = defaultdict(int)
    for c in per_file.values():
        for k, n in c.items():
            tot[k] += n
    by_area: dict[str, int] = defaultdict(int)
    for f, c in per_file.items():
        by_area[area(f)] += sum(c.values())
    out = [
        "# 阶段 8D 旧版字样分类清单",
        "",
        "由 `scripts/code_quality/legacy_inventory.py` 生成。范围：backend 与 SDK src 的产品代码，不含测试。",
        "",
        f"- 含旧版字样的文件：{len(per_file)} 个；行数：{sum(tot.values())}",
        "- 按类：" + "；".join(f"{k} {tot[k]}" for k in ("注释或文档串", "字符串字面量", "标识符")),
        "- 按区域：" + "；".join(f"{k} {by_area[k]}" for k in ("禁区", "数据", "其余")),
        f"- 含旧版字样的标识符：{len(rows)} 个；其中产品代码只出现在定义处一次的 "
        f"{sum(1 for r in rows if r['product'] <= 1)} 个",
        "",
        "## 标识符（按区域、产品引用数排序）",
        "",
        "| 标识符 | 产品引用 | 测试引用 | 区域 | 所在文件 |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["areas"], r["product"], r["name"])):
        fl = "、".join(r["files"][:3]) + (
            f" 等 {len(r['files'])} 个" if len(r["files"]) > 3 else ""
        )
        out.append(
            f"| {r['name']} | {r['product']} | {r['test']} | {'、'.join(r['areas'])} | {fl} |"
        )
    out += [
        "",
        "## 按文件",
        "",
        "| 文件 | 区域 | 注释或文档串 | 字符串字面量 | 标识符 |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(per_file, key=lambda f: -sum(per_file[f].values())):
        c = per_file[f]
        out.append(
            f"| {f} | {area(f)} | {c.get('注释或文档串', 0)} | {c.get('字符串字面量', 0)} | {c.get('标识符', 0)} |"
        )
    Path(a.md).write_text("\n".join(out) + "\n", encoding="utf-8")
    if a.json:
        Path(a.json).write_text(
            json.dumps({"files": per_file, "idents": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    print(out[4], out[5], out[6], out[7], sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
