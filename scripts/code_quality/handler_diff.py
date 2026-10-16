#!/usr/bin/env python3
"""代码质量整治阶段 7：证明改动只落在 except 处理块里，并生成禁区比对的放行清单。

对每个改动过的 .py 文件，把新旧两版的语法树做同样的归一化后比较：
- 每个 except 处理块的捕获类型和处理体换成占位；
- 模块顶层的记录器定义（X = logging.getLogger(...) / structlog.get_logger(...)）
  和 `import logging` / `import structlog` 去掉。
归一化后必须完全相同。另外列出：
- 收窄：旧版是宽捕获、新版不是的处理块（每处要有失败路径用例）；
- 删 try：处理块个数变少的文件（需人工确认 try 体不会抛异常）。
放行清单：处理块里新增的字面量（日志事件名、exc_info=True 等），写成 guard.py --allow 的格式。

用法：
    python3 scripts/code_quality/handler_diff.py [--ref HEAD] [--allow-out 文件] [文件 ...]
不给文件时取 `git diff --name-only <ref>` 里的 .py。退出码：归一化后不同为 1。
"""

from __future__ import annotations

import argparse
import ast
import copy
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from except_inventory import broad  # noqa: E402

ROOT = REPO
LOGGER_FACTORIES = {"getLogger", "get_logger"}
LOG_MODULES = {"logging", "structlog"}


def _is_logger_def(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Import):
        return all(a.name in LOG_MODULES and a.asname is None for a in stmt.names)
    value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in LOGGER_FACTORIES
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id in LOG_MODULES
    )


def _handler_literals(h: ast.ExceptHandler) -> Counter:
    out: Counter = Counter()
    for n in ast.walk(ast.Module(body=h.body, type_ignores=[])):
        if isinstance(n, ast.Constant) and not isinstance(n.value, type(Ellipsis)):
            out[repr(n.value)] += 1
    return out


def normalize(src: str) -> tuple[str, list[ast.ExceptHandler]]:
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    norm = copy.deepcopy(tree)
    norm.body = [s for s in norm.body if not _is_logger_def(s)]
    for n in ast.walk(norm):
        if isinstance(n, ast.ExceptHandler):
            n.type = None
            n.name = None
            n.body = [ast.Pass()]
    return ast.dump(norm, include_attributes=False), handlers


class _Flatten(ast.NodeTransformer):
    """把 try 换成 try 体 + else + finally 的语句序列（用于认出"删 try、内容只减缩进"）。"""

    def visit_Try(self, node: ast.Try):  # noqa: N802
        self.generic_visit(node)
        return [*node.body, *node.orelse, *node.finalbody]

    visit_TryStar = visit_Try  # noqa: N815


def flattened(dump_src: str) -> str:
    tree = ast.parse(dump_src)
    tree.body = [s for s in tree.body if not _is_logger_def(s)]
    tree = _Flatten().visit(tree)
    for n in ast.walk(tree):  # 删 try 后留下的空 pass 也去掉（不改行为）
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(n, field, None)
            if isinstance(stmts, list) and len(stmts) > 1:
                kept = [x for x in stmts if not isinstance(x, ast.Pass)]
                setattr(n, field, kept or stmts[:1])
    return ast.dump(tree, include_attributes=False)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--allow-out")
    ap.add_argument("--repo", default=str(REPO), help="仓库根目录（改坏试验用）")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    global ROOT
    ROOT = Path(a.repo)
    files = a.files or [f for f in git("diff", "--name-only", a.ref).split() if f.endswith(".py")]
    fail = False
    added: Counter = Counter()
    narrowed: list[str] = []
    for f in files:
        new_src = (ROOT / f).read_text(encoding="utf-8")
        old_src = git("show", f"{a.ref}:{f}")
        if not old_src:
            print(f"新文件，跳过：{f}")
            continue
        old_dump, old_h = normalize(old_src)
        new_dump, new_h = normalize(new_src)
        added += sum(map(_handler_literals, new_h), Counter()) - sum(
            map(_handler_literals, old_h), Counter()
        )
        if old_dump != new_dump:
            if len(new_h) < len(old_h) and flattened(old_src) == flattened(new_src):
                print(f"删 try（处理块 {len(old_h)}→{len(new_h)}，其余只减缩进）：{f}")
                print("  须确认 try 体不会抛异常")
                n_old = sum(map(broad, old_h)) - (len(old_h) - len(new_h))
                if sum(map(broad, new_h)) < n_old:
                    print(f"  同一文件还有收窄，须人工核对失败路径用例：{f}")
            else:
                print(f"不通过：except 处理块以外有改动：{f}")
                fail = True
            continue
        for oh, nh in zip(old_h, new_h, strict=True):
            if broad(oh) and not broad(nh):
                narrowed.append(f"{f}:{nh.lineno} {ast.unparse(nh.type)}")
    for n in narrowed:
        print(f"收窄：{n}")
    print(
        f"文件 {len(files)} 个；收窄 {len(narrowed)} 处；"
        f"处理块里新增字面量 {sum(added.values())} 个（{len(added)} 种）"
    )
    if a.allow_out:
        Path(a.allow_out).write_text("".join(f"{k}\n" for k in sorted(added)), encoding="utf-8")
    print("处理块比对：" + ("不通过" if fail else "通过"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
