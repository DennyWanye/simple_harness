#!/usr/bin/env python3
"""代码质量整治：禁区比对（计划第 3 节、4.1 节）。

比对工作区与某个提交（默认 HEAD），三类检查：
1. 禁区文件：表结构与迁移、包内资源文件（sql/json/md/yaml 等）。字节有变即不通过。
2. 严格字面量：代码里形如标识符的字符串（事件名、状态码、决策类型、哈希前缀、工具名）
   和全部数字常量。多重集有增减即不通过。并发默认值落在这里。
3. 自由文本字面量（含空格的字符串）：工具说明、提示词模板、日志与异常措辞。默认同样
   有增减即不通过（工具说明是执行池准入身份）。只改日志或异常措辞的片可加
   --text-report-only 降为只报告，但必须先确认改动里没有工具说明与提示词。

文档字符串、类型注解里的常量，getattr/setattr 的常量属性名，utf-8 编码参数不计
（这几类会被自动修复改写，都不是数据）。
纯移动、改名、拆函数不会改变字面量多重集。确需改动的（例如删死代码、改日志措辞），
在 --allow 文件里逐行写出允许变化的字面量 repr，并在该片实施记录里说明原因。
偏差单裁决允许改动的禁区文件，在 --allow 文件里写一行 file:<仓库相对路径>。

用法：
    python3 scripts/code_quality/guard.py [--ref HEAD] [--allow FILE] [--show-text]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import os
import sys
from collections import Counter
from pathlib import Path

if sys.version_info < (3, 11):  # 系统自带 3.9 解析不了新语法，换用后端虚拟环境的解释器
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
PY_ROOTS = ("backend/", "sdk/simple-harness-sdk/src/")
TS_ROOT = "tauri-app/src/"
SKIP = re.compile(
    r"(^|/)(vendor|dist[^/]*|build|userdata|\.venv|__pycache__|tests|node_modules|"
    r"[^/]*\.egg-info)(/|$)|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$|\.test\.tsx?$"
)
RESOURCE_EXT = {".sql", ".json", ".md", ".yaml", ".yml", ".jsonl", ".txt", ".j2", ".toml"}
RESOURCE_ROOTS = (
    "sdk/simple-harness-sdk/src/",
    "backend/deskpet/",
    "backend/agent/",
    "backend/llm/",
    "backend/providers/",
    "backend/memory/",
)
# 发版流程会重写的文件，不算禁区（但不许手改）
RELEASE_OWNED = {
    "sdk/simple-harness-sdk/src/agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json",
    # 由 scripts/generate_execution_build_manifest.py 生成，测试强制与工具源码一致
    "backend/deskpet/tools/execution_build_manifest.json",
}
FORBIDDEN_PY = re.compile(
    r"(^|/)(schema|migrator|migration|[a-z_]*_migration|schema_migrations)\.py$|(^|/)migrations/"
    # 阶段 8 裁决：带建表语句或按 SQL 文本哈希核对身份的模块（后端记忆层、SDK 存储与治理层）
    r"|^backend/deskpet/memory/([a-z0-9_]+_schema|schema_chain|schema_v2_migrator)\.py$"
    r"|^sdk/simple-harness-sdk/src/agent_orchestrator/(storage|governance|runtime)/[a-z0-9_]+_schema\.py$"
    r"|^sdk/simple-harness-sdk/src/simple_harness/execution/sqlite/[a-z0-9_]+_schema\.py$"
)
# 编解码器清单写死字节哈希的源文件：字节有变即不通过（与 ruff.toml 的排除清单一致）
CODEC_PINNED = {
    "sdk/simple-harness-sdk/src/" + p
    for p in (
        "agent_orchestrator/contracts/evidence_state.py",
        "agent_orchestrator/contracts/htn.py",
        "agent_orchestrator/contracts/models.py",
        "agent_orchestrator/contracts/semantic_base.py",
        "agent_orchestrator/contracts/state_machines.py",
        "agent_orchestrator/graph/network_codec.py",
        "agent_orchestrator/graph/task_network.py",
        "simple_harness/contracts/__init__.py",
        "simple_harness/contracts/errors.py",
        "simple_harness/contracts/json.py",
        # 测试按字节冻结（tests/agents/test_api_mode_fence.py）
        "simple_harness/runtime/start_snapshot.py",
        "simple_harness/runtime/drivers/start_mode.py",
    )
}
STRICT = re.compile(r"^[A-Za-z0-9_.:/\-\[\]{}<>=+*#@$%^&|~!?]{2,}$")


def git_files(ref: str | None) -> list[str]:
    if ref is None:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout
        return [f for f in out.splitlines() if (REPO / f).is_file()]
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref], cwd=REPO, capture_output=True, text=True
    ).stdout
    return out.splitlines()


def read(ref: str | None, path: str) -> bytes:
    if ref is None:
        return (REPO / path).read_bytes()
    return subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO, capture_output=True).stdout


def docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def annotation_ids(tree: ast.AST) -> set[int]:
    """注解子树与 cast 类型参数里的常量（带引号的类型名）。工具参数结构是字典字面量，不从注解生成。"""
    ids: set[int] = set()
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            roots.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif (
            isinstance(node, ast.Call)
            and len(node.args) == 2
            and not node.keywords
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "cast")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "cast")
            )
        ):
            roots.append(node.args[0])  # cast 的第一个参数是类型表达式（阶段 5E）
    for r in roots:
        ids.update(id(n) for n in ast.walk(r))
    return ids


def attr_name_ids(tree: ast.AST) -> set[int]:
    """getattr/setattr 的常量属性名、encode/decode 的 utf-8 参数：自动修复会改写成属性访问或默认值。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Name)
            and f.id in ("getattr", "setattr")
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            ids.add(id(node.args[1]))
        if isinstance(f, ast.Attribute) and f.attr in ("encode", "decode"):
            for a in node.args[:1]:
                if isinstance(a, ast.Constant) and str(a.value).lower().replace("-", "") == "utf8":
                    ids.add(id(a))
        for k in node.keywords:
            if (
                k.arg == "encoding"
                and isinstance(k.value, ast.Constant)
                and str(k.value.value).lower().replace("-", "") == "utf8"
            ):
                ids.add(id(k.value))
    return ids


def py_literals(src: bytes) -> list:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip = docstring_ids(tree) | annotation_ids(tree) | attr_name_ids(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in skip:
            v = node.value
            if isinstance(v, bool) or v is None or v is Ellipsis:
                continue
            if isinstance(v, (str, bytes, int, float, complex)):
                out.append(v)
    return out


def ts_literals_all(ref: str | None) -> list:
    """用 TypeScript 编译器提取前端全部字符串与数字字面量（正则在嵌套引号、换引号时会误报）。"""
    import json as _json

    out = subprocess.run(
        ["node", str(REPO / "scripts/code_quality/ts_literals.mjs"), ref or "WORKTREE"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"前端字面量提取失败：{out.stderr[-500:]}")
    return _json.loads(out.stdout)


def snapshot(ref: str | None) -> tuple[dict, Counter, Counter]:
    files = git_files(ref)
    hashes: dict[str, str] = {}
    strict: Counter = Counter()
    text: Counter = Counter()
    for f in files:
        if SKIP.search(f):
            continue
        suffix = Path(f).suffix
        is_res = suffix in RESOURCE_EXT and f.startswith(RESOURCE_ROOTS) and f not in RELEASE_OWNED
        is_forbidden_py = (
            suffix == ".py"
            and f.startswith(PY_ROOTS)
            and (FORBIDDEN_PY.search(f) or f in CODEC_PINNED)
        )
        if is_res or is_forbidden_py:
            hashes[f] = hashlib.sha256(read(ref, f)).hexdigest()
        if suffix == ".py" and f.startswith(PY_ROOTS):
            lits = py_literals(read(ref, f))
        else:
            continue
        for v in lits:
            key = repr(v)
            if isinstance(v, str) and not STRICT.match(v):
                text[key] += 1
            else:
                strict[key] += 1
    for v in ts_literals_all(ref):
        key = repr(v)
        if isinstance(v, str) and not STRICT.match(v):
            text[key] += 1
        else:
            strict[key] += 1
    return hashes, strict, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--allow", help="允许变化的字面量 repr 清单，一行一个")
    ap.add_argument("--show-text", action="store_true", help="列出自由文本字面量的变化")
    ap.add_argument(
        "--text-report-only",
        action="store_true",
        help="自由文本字面量变化只报告不拦（仅限只改日志/异常措辞的片）",
    )
    a = ap.parse_args()
    allow: set[str] = set()
    if a.allow:
        allow = {
            ln.rstrip("\n")
            for ln in Path(a.allow).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }

    h0, s0, t0 = snapshot(a.ref)
    h1, s1, t1 = snapshot(None)
    fail = False

    changed = sorted(f for f in set(h0) | set(h1) if h0.get(f) != h1.get(f))
    for f in changed:
        state = "新增" if f not in h0 else "删除" if f not in h1 else "改动"
        if f"file:{f}" in allow:  # 放行清单里写 file:<路径>（只用于偏差单裁决允许的禁区改动）
            print(f"禁区文件{state}（放行）：{f}")
            continue
        print(f"禁区文件{state}：{f}")
        fail = True

    added = s1 - s0
    removed = s0 - s1
    for k, n in sorted(added.items()):
        if k in allow:
            continue
        print(f"严格字面量增加 {n}×：{k}")
        fail = True
    for k, n in sorted(removed.items()):
        if k in allow:
            continue
        print(f"严格字面量减少 {n}×：{k}")
        fail = True

    ta = Counter({k: n for k, n in (t1 - t0).items() if k not in allow})
    tr = Counter({k: n for k, n in (t0 - t1).items() if k not in allow})
    mode = "只报告" if a.text_report_only else "拦截"
    print(f"自由文本字面量（{mode}）：增加 {sum(ta.values())}，减少 {sum(tr.values())}")
    if (ta or tr) and not a.text_report_only:
        fail = True
    if a.show_text or ((ta or tr) and not a.text_report_only):
        for k, n in sorted(ta.items()):
            print(f"  + {n}× {k[:160]}")
        for k, n in sorted(tr.items()):
            print(f"  - {n}× {k[:160]}")
    print(
        "禁区比对："
        + ("不通过" if fail else "通过")
        + f"（对比 {a.ref}，禁区文件 {len(h1)} 个，严格字面量 {sum(s1.values())} 个）"
    )
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
