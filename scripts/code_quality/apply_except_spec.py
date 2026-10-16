#!/usr/bin/env python3
"""代码质量整治阶段 7：按清单逐处改宽捕获。

清单一行一处，字段用 " | " 分隔：
    文件:行 | log debug|info|warning | 事件名 | 原因
    文件:行 | logtype debug|info|warning | 事件名 | 原因
        （只记异常类型：异常消息可能含隐私数据时用）
    文件:行 | narrow | 异常类型（可写元组 "OSError, ValueError"） | 原因（可空）
    文件:行 | deltry | | 原因（可空，写进实施记录用）
    文件:行 | reason | | 原因          （只给丙类：except 行写原因，处理体不动）
log / logtype 的原因可留空：except 行注释不动（乙类只要求日志）。
"#" 开头的行和空行跳过。"原因"写 "-" 时沿用 pass 行或 except 行上已有的注释文字。

做法：
- log：处理体只有 pass 时换成日志调用；否则在处理体第一句前插入日志调用。日志调用是
  `<记录器>.<级别>("<事件名>", exc_info=True)`。except 行注释换成 `# noqa: BLE001 - <原因>`
  （except 行上别的注释保留）。模块没有记录器时在导入之后加 `logger = logging.getLogger(__name__)`。
- narrow：把捕获类型里的 Exception 换成给定类型；BaseException 与裸 except 拒绝（阶段 7 裁决）。
- deltry：只处理"只有这一个处理块、没有 else/finally"的 try，去掉 try 与处理块，try 体减缩进。
改完要跑 ruff format（--force-exclude）与 handler_diff.py。

用法：
    python3 scripts/code_quality/apply_except_spec.py 清单文件 [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from add_exc_info import module_loggers  # noqa: E402

PREFERRED = ("logger", "log", "_log", "_logger", "LOGGER", "_LOG", "LOG")
NOQA = re.compile(r"#\s*noqa:\s*BLE001\b[^#]*")


def _comment_split(line: str) -> tuple[str, str]:
    """把一行拆成代码与注释（不处理字符串里的 #，except 行与 pass 行用不到）。"""
    i = line.find("#")
    return (line, "") if i < 0 else (line[:i].rstrip(), line[i:])


def _old_reason(comment: str) -> str:
    m = re.match(r"#\s*(?:noqa:\s*BLE001\b)?\s*[-—:：]?\s*(.*)", comment)
    return (m.group(1) if m else "").strip()


def _logger_name(path: Path, tree: ast.Module) -> tuple[str, bool]:
    names = module_loggers(path, tree)
    for p in PREFERRED:
        if p in names:
            return p, False
    if names:
        return sorted(names)[0], False
    defined = {
        t.id
        for s in tree.body
        if isinstance(s, ast.Assign)
        for t in s.targets
        if isinstance(t, ast.Name)
    }
    for p in PREFERRED:
        if p not in defined:
            return p, True
    raise SystemExit(f"{path}: 找不到可用的记录器名字")


def _add_logger(lines: list[str], tree: ast.Module, name: str) -> list[str]:
    has_logging = any(
        isinstance(s, ast.Import) and any(a.name == "logging" and a.asname is None for a in s.names)
        for s in tree.body
    )
    last_import = 0
    for s in tree.body:
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            last_import = s.end_lineno
        elif not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)):
            break
    insert = ["", f"{name} = logging.getLogger(__name__)", ""]
    out = lines[:last_import] + insert + lines[last_import:]
    if not has_logging:
        first = next(
            (
                s.lineno
                for s in tree.body
                if isinstance(s, (ast.Import, ast.ImportFrom))
                and not (isinstance(s, ast.ImportFrom) and s.module == "__future__")
            ),
            last_import + 1,
        )
        out.insert(first - 1, "import logging")
    return out


def _narrow_line(path: Path, h: ast.ExceptHandler, code: str, comment: str, arg: str) -> str:
    if h.type is None or (isinstance(h.type, ast.Name) and h.type.id == "BaseException"):
        raise SystemExit(f"{path}:{h.lineno} 裸 except / BaseException 不许收窄（阶段 7 裁决）")
    if not re.search(r"\bException\b", code):
        raise SystemExit(f"{path}:{h.lineno} except 行里没有 Exception")
    new_t = f"({arg})" if "," in arg else arg
    code = re.sub(r"\(?\bException\b\)?", new_t, code, count=1)
    comment = NOQA.sub("", comment).strip()
    comment = "" if comment == "#" else comment
    return code + (f"  {comment}" if comment else "")


def _log_or_reason(
    path: Path,
    lines: list[str],
    h: ast.ExceptHandler,
    action: str,
    arg: str,
    reason: str,
    logger: str,
) -> bool:
    """except 行写原因；log / logtype 再在处理体里加日志调用。返回是否用到了记录器。"""
    line = h.lineno
    code, comment = _comment_split(lines[line - 1])
    r = reason
    body0 = h.body[0]
    pass_only = len(h.body) == 1 and isinstance(body0, ast.Pass)
    if r == "-":
        r = _old_reason(comment)
        if not r and pass_only:
            r = _old_reason(_comment_split(lines[body0.lineno - 1])[1])
        if not r:
            raise SystemExit(f"{path}:{line} 原因写了 - 但找不到已有注释")
    if r:  # 原因留空：except 行注释不动（乙类只要求日志）
        rest = NOQA.sub("", comment).strip()
        rest = "" if rest == "#" else rest
        new_comment = (f"{rest}  " if rest else "") + f"# noqa: BLE001 - {r}"
        lines[line - 1] = f"{code}  {new_comment}"
    elif action == "reason":
        raise SystemExit(f"{path}:{line} 只写原因却没给原因")
    if action == "reason":
        return False
    level, event = arg.split()
    indent = " " * body0.col_offset
    extra = "exc_info=True"
    if action == "logtype":
        if h.name is None:  # 补一个异常变量名（不与函数里已有名字冲突）
            lines[line - 1] = re.sub(
                r":(\s*#|\s*$)", r" as swallowed_exc:\1", lines[line - 1], count=1
            )
        extra = f"error_type=type({h.name or 'swallowed_exc'}).__name__"
    call = f'{indent}{logger}.{level}("{event}", {extra})'
    if pass_only:
        lines[body0.lineno - 1] = call
    else:
        lines.insert(body0.lineno - 1, call)
    return True


def _check_module_level_order(path: Path, tree: ast.Module, parents: dict, h, logger: str) -> None:
    """模块顶层的处理块（导入探测）里要用记录器：记录器必须在它前面定义，否则导入时 NameError。"""
    node = h
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
    defs = [
        s.lineno
        for s in tree.body
        if isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == logger for t in s.targets)
    ]
    imported = any(
        isinstance(s, ast.ImportFrom)
        and any((a.asname or a.name) == logger for a in s.names)
        and s.lineno < h.lineno
        for s in tree.body
    )
    if not imported and (not defs or min(defs) > h.lineno):
        raise SystemExit(f"{path}:{h.lineno} 模块顶层处理块在记录器 {logger} 定义之前，需手改")


def apply_file(path: Path, items: list[tuple[int, str, str, str]], dry: bool) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.split("\n")
    handlers = {h.lineno: h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)}
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    logger, need_logger = _logger_name(path, tree)
    used_logger = False
    notes: list[str] = []
    for line, action, arg, reason in sorted(items, key=lambda x: -x[0]):
        h = handlers.get(line)
        if h is None:
            raise SystemExit(f"{path}:{line} 不是 except 行")
        code, comment = _comment_split(lines[line - 1])
        if action == "narrow":
            lines[line - 1] = _narrow_line(path, h, code, comment, arg)
            notes.append(f"{path}:{line} 收窄为 {arg}")
        elif action in ("log", "logtype", "reason"):
            if action != "reason" and not need_logger:
                _check_module_level_order(path, tree, parents, h, logger)
            used_logger |= _log_or_reason(path, lines, h, action, arg, reason, logger)
        elif action == "deltry":
            t = parents[h]
            if not isinstance(t, ast.Try) or len(t.handlers) != 1 or t.orelse or t.finalbody:
                raise SystemExit(f"{path}:{line} 不是单处理块的 try，不能删")
            shift = t.body[0].col_offset - t.col_offset
            span = lines[t.body[0].lineno - 1 : h.lineno - 1]
            body = [ln[shift:] if ln.strip() else ln for ln in span]
            lines[t.lineno - 1 : h.end_lineno] = body
            notes.append(f"{path}:{line} 删 try（{reason or '无原因'}）")
        else:
            raise SystemExit(f"{path}:{line} 未知做法 {action}")
    if used_logger and need_logger:
        lines = _add_logger(lines, tree, logger)
        notes.append(f"{path}: 新加记录器 {logger}")
    if not dry:
        path.write_text("\n".join(lines), encoding="utf-8")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    by_file: dict[Path, list] = defaultdict(list)
    for raw in Path(a.spec).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in raw.split(" | ")]
        parts += [""] * (4 - len(parts))
        loc, action, arg, reason = parts[:4]
        if action.startswith(("log ", "logtype ")):  # "log debug | 事件名" → 级别并进参数
            action, arg = action.split()[0], f"{action.split()[1]} {arg}"
        f, ln = loc.rsplit(":", 1)
        by_file[REPO / f].append((int(ln), action, arg, reason))
    total = 0
    for path, items in by_file.items():
        for n in apply_file(path, items, a.dry_run):
            print(n)
        total += len(items)
    print(f"{'预演' if a.dry_run else '已改'}：{len(by_file)} 个文件，{total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
