#!/usr/bin/env python3
"""代码质量整治阶段 8：死代码候选清单（8.4 vulture + 无人导入的模块），逐个搜全仓引用。

候选来源：
- vulture（置信度 60，去掉测试与脚本）报的未用函数、类、方法；
- 后端与 SDK 里没有任何地方导入的模块（绝对导入、相对导入、点分路径字符串都算导入）。
每个候选统计引用（按词边界搜名字，去掉定义所在行）：
- 产品代码引用（backend、SDK src、前端 src，不含测试）
- 测试引用（backend/tests、SDK tests）
- 字符串引用（引号里出现名字，可能是注册表、getattr、打桩路径）
排除（不能当死代码删，阶段 8 裁决）：SDK 公开接口清单里的名字、带装饰器的函数（路由、IPC、注册）、
pytest 夹具、以 test_ 开头的名字、魔术方法、禁区与编解码钉死文件里的定义。

用法：
    python3 scripts/code_quality/dead_symbols.py --vulture vulture输出 --json 输出 [--md 清单.md]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import CODEC_PINNED, FORBIDDEN_PY  # noqa: E402

VULTURE_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): unused (?P<kind>function|class|method) '(?P<name>[^']+)'"
)
PRODUCT_ROOTS = ("backend/", "sdk/simple-harness-sdk/src/", "tauri-app/src/")
TEST_ROOTS = ("backend/tests/", "sdk/simple-harness-sdk/tests/")
SRC_SUFFIX = (".py", ".ts", ".tsx", ".json", ".toml")


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True
    ).stdout.split()
    return [f for f in out if f.endswith(SRC_SUFFIX) and not f.startswith((".local", "plans/"))]


def is_test(f: str) -> bool:
    return f.startswith(TEST_ROOTS) or "/tests/" in f or f.rsplit("/", 1)[-1].startswith("test_")


def public_api_names() -> set[str]:
    names: set[str] = set()
    for p in (REPO / "sdk/simple-harness-sdk/src").rglob("__init__.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                try:
                    names |= set(ast.literal_eval(node.value))
                except ValueError:
                    pass
    return names


def module_name(f: str) -> str | None:
    if f.startswith("backend/") and f.endswith(".py"):
        rel = f[len("backend/") : -3]
    elif f.startswith("sdk/simple-harness-sdk/src/") and f.endswith(".py"):
        rel = f[len("sdk/simple-harness-sdk/src/") : -3]
    else:
        return None
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def decorated_or_special(f: str, line: int, name: str) -> str | None:
    try:
        tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError):
        return "解析不了"
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.lineno == line
        ):
            if node.decorator_list:
                return "带装饰器"
            if name.startswith("__") and name.endswith("__"):
                return "魔术方法"
            if name.startswith("test_"):
                return "测试名"
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vulture", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()

    files = tracked()
    texts = {f: (REPO / f).read_text(encoding="utf-8", errors="replace") for f in files}
    public = public_api_names()

    cands: list[dict] = []
    for line in Path(a.vulture).read_text(encoding="utf-8").splitlines():
        m = VULTURE_LINE.match(line)
        if m:
            cands.append(
                {"file": m["file"], "line": int(m["line"]), "kind": m["kind"], "name": m["name"]}
            )

    # 无人导入的模块
    mods = {module_name(f): f for f in files if module_name(f)}
    for mod, f in mods.items():
        if not mod or f.endswith("__init__.py") or is_test(f) or f.startswith("backend/scripts/"):
            continue
        if f in ("backend/main.py",) or f.rsplit("/", 1)[-1] in ("conftest.py", "__main__.py"):
            continue
        leaf = mod.rsplit(".", 1)[-1]
        pat = re.compile(r"\b" + re.escape(leaf) + r"\b")
        hit = any(pat.search(t) for g, t in texts.items() if g != f)
        if not hit:
            cands.append({"file": f, "line": 1, "kind": "module", "name": leaf, "module": mod})

    rows = []
    for c in cands:
        f = c["file"]
        reason = None
        if FORBIDDEN_PY.search(f) or f in CODEC_PINNED:
            reason = "禁区"
        elif c["name"] in public:
            reason = "SDK 公开接口"
        elif c["kind"] != "module":
            reason = decorated_or_special(f, c["line"], c["name"])
        pat = re.compile(r"\b" + re.escape(c["name"]) + r"\b")
        refs = defaultdict(int)
        strs = 0
        for g, t in texts.items():
            for mm in pat.finditer(t):
                ln = t.count("\n", 0, mm.start()) + 1
                if g == f and ln == c["line"]:
                    continue
                start = t.rfind("\n", 0, mm.start()) + 1
                end = t.find("\n", mm.end())
                lt = t[start : end if end >= 0 else None]
                if g == f and c["kind"] == "module":
                    continue
                key = "测试" if is_test(g) else "产品"
                refs[key] += 1
                before = lt[: mm.start() - start]
                if before.count('"') % 2 == 1 or before.count("'") % 2 == 1:
                    strs += 1
        rows.append(
            {
                **c,
                "excluded": reason,
                "product_refs": refs["产品"],
                "test_refs": refs["测试"],
                "string_refs": strs,
            }
        )

    Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    zero = [r for r in rows if not r["excluded"] and r["product_refs"] == 0 and r["test_refs"] == 0]
    test_only = [
        r for r in rows if not r["excluded"] and r["product_refs"] == 0 and r["test_refs"] > 0
    ]
    print(
        f"候选 {len(rows)}；排除 {sum(1 for r in rows if r['excluded'])}；"
        f"零引用 {len(zero)}；只剩测试引用 {len(test_only)}；其余有产品引用 "
        f"{len(rows) - len(zero) - len(test_only) - sum(1 for r in rows if r['excluded'])}"
    )
    if a.md:
        out = [
            "| 种类 | 名字 | 位置 | 产品引用 | 测试引用 | 字符串引用 | 排除原因 |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in sorted(
            rows, key=lambda r: (r["excluded"] or "", r["product_refs"], r["file"], r["line"])
        ):
            out.append(
                f"| {r['kind']} | {r['name']} | {r['file']}:{r['line']} | {r['product_refs']} | "
                f"{r['test_refs']} | {r['string_refs']} | {r['excluded'] or ''} |"
            )
        Path(a.md).write_text("\n".join(out) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
