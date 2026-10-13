#!/usr/bin/env python3
"""代码质量整治 2.24：把控制通道的大 if/elif 分发链拆成处理函数加有序路由表（在 main.py 原位改写）。

做法：
- 分发链上每个分支变成模块级处理函数 ``_cc_<名字>(conn, raw, msg_type)``，函数体原样保留；
  分支顶层的 ``continue`` 改成 ``return``（嵌套循环里的不动）。
- 连接级状态（WebSocket、会话号、伴侣挑战、记忆控制绑定、请求范围）放进 ``_ControlConnection``，
  处理函数开头按需取出成同名局部变量，函数体不用改名。
- 分发链换成按原顺序查 ``_CONTROL_ROUTES``：每项是"消息类型集合"或"判断函数"，与原 if/elif 顺序一致。
- 原来在循环前按条件导入的 assert_not_primary_authority，用到它的处理函数里显式导入。

用法：python3 scripts/code_quality/split_control_channel.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "backend/main.py"
CONN_FIELDS = ["ws", "session_id", "requested_scope", "companion_challenge", "human_memory_control_binding"]


def chain_branches(fn: ast.AsyncFunctionDef) -> tuple[ast.If, list[tuple[ast.expr | None, list[ast.stmt]]]]:
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.While))
    head = next(
        n for n in loop.body
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and any(isinstance(c, ast.Constant) and c.value == "ping" for c in n.test.comparators)
    )
    out: list[tuple[ast.expr | None, list[ast.stmt]]] = []
    node: ast.If | None = head
    while node is not None:
        out.append((node.test, node.body))
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            if node.orelse:
                out.append((None, node.orelse))
            node = None
    return head, out


def literal_types(test: ast.expr) -> list[str] | None:
    """test 若是 msg_type == "x" 或 msg_type in {常量...}，返回类型列表；否则 None。"""
    if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "msg_type"):
        return None
    if len(test.ops) != 1:
        return None
    op, comp = test.ops[0], test.comparators[0]
    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
        return [comp.value]
    if isinstance(op, ast.In) and isinstance(comp, (ast.Set, ast.Tuple, ast.List)):
        vals = [e.value for e in comp.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(vals) == len(comp.elts):
            return vals
    return None


def top_level_continues(body: list[ast.stmt]) -> list[ast.Continue]:
    found: list[ast.Continue] = []

    def visit(node: ast.AST, depth: int) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            depth += 1
        if isinstance(node, ast.Continue) and depth == 0:
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, depth)

    for st in body:
        visit(st, 0)
    return found


def has_disallowed_flow(body: list[ast.stmt]) -> bool:
    """分支顶层（不含内层函数）有 return，或不在内层循环里的 break。"""
    hit = False

    def visit(node: ast.AST, depth: int) -> None:
        nonlocal hit
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            depth += 1
        if isinstance(node, ast.Return) or (isinstance(node, ast.Break) and depth == 0):
            hit = True
        for child in ast.iter_child_nodes(node):
            visit(child, depth)

    for st in body:
        visit(st, 0)
    return hit


def slug(types: list[str] | None, index: int, test_src: str) -> str:
    if types:
        base = types[0]
    elif "startswith((\"mission_\"" in test_src:
        base = "orchestration_routes"
    elif "P4_IPC_MESSAGE_TYPES" in test_src:
        base = "p4_ipc"
    elif "startswith(\"workflow_\")" in test_src:
        base = "workflow_routes"
    elif test_src == "ELSE":
        base = "unknown"
    else:
        base = f"route_{index}"
    return "_cc_" + re.sub(r"[^0-9a-zA-Z]+", "_", base).strip("_")


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "control_channel")
    head, branches = chain_branches(fn)

    handlers: list[str] = []
    routes: list[str] = []
    names_seen: set[str] = set()
    for i, (test, body) in enumerate(branches):
        test_src = ast.get_source_segment(src, test) if test is not None else "ELSE"
        types = literal_types(test) if test is not None else None
        name = slug(types, i, test_src)
        while name in names_seen:
            name += "_"
        names_seen.add(name)
        if has_disallowed_flow(body):
            raise SystemExit(f"分支 {test_src[:60]} 里有 return/break，需要人工处理")
        first, last = body[0].lineno, body[-1].end_lineno
        indent = body[0].col_offset
        conts = {c.lineno: c.col_offset for c in top_level_continues(body)}
        out_body: list[str] = []
        for ln in range(first, last + 1):
            text = lines[ln - 1]
            if ln in conts and text.strip() == "continue":
                text = text.replace("continue", "return", 1)
            out_body.append(text[indent:] if text.strip() else "\n")
        body_text = "".join(out_body)
        used = {n.id for st in body for n in ast.walk(st) if isinstance(n, ast.Name)}
        prologue = [f"    {f} = conn.{f}\n" for f in CONN_FIELDS if f in used]
        if "assert_not_primary_authority" in used:
            prologue.insert(0, "    from deskpet.memory.primary_authority import assert_not_primary_authority\n\n")
        indented = "".join(("    " + l) if l.strip() else "\n" for l in body_text.splitlines(keepends=True))
        if types is not None:
            doc = "、".join(types)
        elif test is None:
            doc = "未知类型"
        else:
            doc = {"_cc_orchestration_routes": "编排服务转发（mission_、orchestration_、taskgraph. 前缀）",
                   "_cc_p4_ipc": "P4 进程间消息", "_cc_workflow_routes": "工作流消息（workflow_ 前缀）"}.get(name, "其它")
        handlers.append(
            f"async def {name}(conn: _ControlConnection, raw: dict, msg_type: str) -> None:\n"
            f'    """控制消息：{doc}。"""\n'
            + "".join(prologue) + indented + "\n\n"
        )
        if types is not None:
            routes.append(f"    (frozenset({sorted(types)!r}), {name}),\n")
        elif test is None:
            unknown = name
        else:
            routes.append(f"    (lambda msg_type: {test_src.strip()}, {name}),\n")

    # 1) 分发链替换为查表
    chain_first = head.lineno
    chain_last = branches[-1][1][-1].end_lineno
    chain_indent = head.col_offset
    pad = " " * chain_indent
    dispatch = (
        f"{pad}await _dispatch_control_message(\n"
        f"{pad}    _ControlConnection(\n"
        f"{pad}        ws=ws,\n"
        f"{pad}        session_id=session_id,\n"
        f"{pad}        requested_scope=requested_scope,\n"
        f"{pad}        companion_challenge=companion_challenge,\n"
        f"{pad}        human_memory_control_binding=human_memory_control_binding,\n"
        f"{pad}    ),\n"
        f"{pad}    raw,\n"
        f"{pad}    msg_type,\n"
        f"{pad})\n"
    )
    new_lines = lines[: chain_first - 1] + [dispatch] + lines[chain_last:]

    # 2) 处理函数、连接对象、路由表放在 control_channel 之前
    support = (
        "@dataclass\n"
        "class _ControlConnection:\n"
        '    """一条控制连接的状态：分发时交给各消息处理函数。"""\n\n'
        "    ws: WebSocket\n"
        "    session_id: str\n"
        "    requested_scope: str\n"
        "    companion_challenge: Any\n"
        "    human_memory_control_binding: Any\n\n\n"
        + "".join(handlers)
        + "# 有序路由表：与原 if/elif 分发链顺序一致。每项是消息类型集合或判断函数。\n"
        + "_CONTROL_ROUTES: list[tuple[Any, Any]] = [\n" + "".join(routes) + "]\n\n\n"
        + "async def _dispatch_control_message(conn: _ControlConnection, raw: dict, msg_type: str) -> None:\n"
        + '    """按路由表把一条控制消息交给处理函数；都不匹配时回"未知类型"。"""\n'
        + "    for matcher, handler in _CONTROL_ROUTES:\n"
        + "        if (msg_type in matcher) if isinstance(matcher, frozenset) else matcher(msg_type):\n"
        + "            await handler(conn, raw, msg_type)\n"
        + "            return\n"
        + f"    await {unknown}(conn, raw, msg_type)\n\n\n"
    )
    fn_start = fn.lineno - 1 - len(fn.decorator_list)
    new_lines = new_lines[:fn_start] + [support] + new_lines[fn_start:]
    MAIN.write_text("".join(new_lines), encoding="utf-8")
    print(f"处理函数 {len(handlers)} 个，路由 {len(routes)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
