#!/usr/bin/env python3
"""代码质量整治 2.27：把生命周期（lifespan）按顶层语句分组拆成有序步骤函数（在 main.py 原位改写）。

- 每组顶层语句原样成为一个模块级 async 函数，缩进不变（都是函数体第一层）。
- 跨组共用的局部变量用参数传入、用返回值传出；组内自用的局部变量不动。
- lifespan 只剩：文档字符串、按顺序调用启动步骤、yield、调用关闭步骤。

用法：python3 scripts/code_quality/split_lifespan.py
"""

from __future__ import annotations

import ast
import symtable
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "backend/main.py"

# (步骤函数名, 说明, 起始语句序号, 结束语句序号含)；序号见 lifespan 顶层语句（0 是文档字符串，64 是 yield）
GROUPS = [
    ("_startup_core_services", "启动：受保护路径通知、提供方路由就绪状态、人类记忆启动纪元与宿主服务工厂登记", 1, 15),
    ("_startup_provider_routing", "启动：提供方注册表与路由装配", 16, 16),
    ("_startup_search_and_voice", "启动：检索网关、模型预加载、语音引擎占位", 17, 24),
    ("_startup_workflow_services", "启动：会话库检查与工作流服务接线（单条 1421 行的语句整段搬家，拆分在后面的阶段）", 25, 27),
    ("_startup_restore_and_skill_prewarm", "启动：PPT 大纲清理、目标与事实恢复、技能目录与匹配预热", 28, 38),
    ("_startup_embedder_and_workload_audit", "启动：嵌入模型预热、提供方负载审计维护", 39, 44),
    ("_startup_vector_worker", "启动：向量工作器与消息扇出", 45, 45),
    ("_startup_curation_image_and_mcp", "启动：整理提示、图片工作器、MCP 管理器", 46, 51),
    ("_startup_self_healing", "启动：工具熔断与会话活跃度", 52, 55),
    ("_startup_supervisor", "启动：监督 Agent（单条 292 行的 if，拆分在后面的阶段）", 56, 56),
    ("_startup_auto_resume_and_model_context", "启动：自动续跑编排、默认模型上下文窗口解析", 57, 58),
    ("_startup_desktop_and_realtime_voice", "启动：桌面测试桥、实时语音服务", 59, 63),
]
SHUTDOWN_GROUPS = [
    ("_shutdown_runtimes_and_tasks", "关闭：实时语音、检索网关、前台运行时、审计、SDK 入口与恢复任务、伴侣运行时与各类后台任务", 65, 91),
    ("_shutdown_services", "关闭：桌面测试桥、SDK 运行栈、会话库、能力平台、工作流、看门狗、MCP、向量与图片工作器、技能目录、情节后台任务", 92, 114),
]


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    body = fn.body
    assert isinstance(body[64], ast.Expr) and isinstance(body[64].value, ast.Yield), "第 64 条应是 yield"
    assert len(body) == 115, len(body)

    table = symtable.symtable(src, "main", "exec")
    ls = next(c for c in table.get_children() if c.get_name() == "lifespan")
    locs = {s.get_name() for s in ls.get_symbols() if s.is_local()}

    def walk_top(node: ast.AST):
        """只走生命周期这一层：内层函数只取其名字，不进函数体（内层函数的局部变量不算外层定义）。"""
        stack = [node]
        while stack:
            n = stack.pop()
            yield n
            for c in ast.iter_child_nodes(n):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    yield c
                    continue
                stack.append(c)

    def defs(node: ast.AST) -> set[str]:
        out: set[str] = set()
        for n in walk_top(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out |= {(a.asname or a.name).split(".")[0] for a in n.names}
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
        return out & locs

    scopes = {(c.get_name(), c.get_lineno()): c for c in ls.get_children()}

    def frees(table_: symtable.SymbolTable) -> set[str]:
        out = {s_.get_name() for s_ in table_.get_symbols() if s_.is_free()}
        for child in table_.get_children():
            out |= frees(child)
        return out

    def uses(node: ast.AST) -> set[str]:
        """生命周期这一层的读取，加上内层函数引用外层的自由变量（按符号表判断）。"""
        out: set[str] = set()
        for n in walk_top(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                out.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lineno = n.decorator_list[0].lineno if n.decorator_list else n.lineno
                scope = scopes.get((n.name, n.lineno)) or scopes.get((n.name, lineno))
                if scope is None:
                    raise SystemExit(f"找不到内层函数 {n.name} 第 {n.lineno} 行的符号表")
                out |= frees(scope)
                # 装饰器与默认值在外层求值
                for d in n.decorator_list + list(getattr(n.args, "defaults", [])) + list(getattr(n.args, "kw_defaults", []) or []):
                    if d is not None:
                        out |= {x.id for x in ast.walk(d) if isinstance(x, ast.Name)}
            elif isinstance(n, ast.Lambda):
                scope = scopes.get(("lambda", n.lineno))
                if scope is not None:
                    out |= frees(scope)
                else:
                    out |= {x.id for x in ast.walk(n.body) if isinstance(x, ast.Name)}
        return out & locs

    all_groups = GROUPS + SHUTDOWN_GROUPS
    # 每组需要从外面拿的名字：组内语句用到、但组内此前（含本句）没定义过
    needs: list[set[str]] = []
    provides: list[set[str]] = []
    for name, _desc, a, b in all_groups:
        seen: set[str] = set()
        need: set[str] = set()
        for st in body[a : b + 1]:
            d = defs(st)
            need |= {u for u in uses(st) if u not in seen and u not in d}
            seen |= d
        needs.append(need)
        provides.append(seen)
    # 参数 = 本组需要且由前面某组提供；返回值 = 本组提供且后面某组需要（取最后提供者）
    params: list[list[str]] = []
    returns: list[list[str]] = [[] for _ in all_groups]
    for gi, need in enumerate(needs):
        ps = []
        for n in sorted(need):
            src_groups = [gj for gj in range(gi) if n in provides[gj]]
            if not src_groups:
                raise SystemExit(f"{all_groups[gi][0]} 需要的 {n} 没有前组提供")
            gj = src_groups[-1]
            if n not in returns[gj]:
                returns[gj].append(n)
            ps.append(n)
        params.append(ps)

    def stmt_text(a: int, b: int) -> str:
        first = body[a].lineno
        # 带上语句前的注释行
        while first > 1 and lines[first - 2].strip().startswith("#"):
            first -= 1
        last = body[b].end_lineno
        return "".join(lines[first - 1 : last])

    funcs: list[str] = []
    calls: list[str] = []
    for gi, (name, desc, a, b) in enumerate(all_groups):
        sig = ", ".join(f"{p}: Any" for p in params[gi])
        ret = returns[gi]
        ret_ann = "None" if not ret else ("Any" if len(ret) == 1 else f"tuple[{', '.join('Any' for _ in ret)}]")
        text = stmt_text(a, b)
        tail = "" if not ret else f"    return {', '.join(ret)}\n"
        funcs.append(f"async def {name}({sig}) -> {ret_ann}:\n    \"\"\"{desc}。\"\"\"\n{text}{tail}\n\n")
        call = f"await {name}({', '.join(params[gi])})"
        if ret:
            call = f"{', '.join(ret)} = {call}" if len(ret) > 1 else f"{ret[0]} = {call}"
        calls.append(call)

    # 新的 lifespan 函数体：文档字符串 + 启动步骤 + yield + 关闭步骤
    doc_first, doc_last = body[0].lineno, body[0].end_lineno
    doc = "".join(lines[doc_first - 1 : doc_last])
    n_start = len(GROUPS)
    new_body = (
        doc
        + "".join(f"    {c}\n" for c in calls[:n_start])
        + "    yield\n"
        + "".join(f"    {c}\n" for c in calls[n_start:])
    )
    fn_def_line = fn.lineno - 1  # def 行下标
    head = lines[: fn_def_line - len(fn.decorator_list)]
    decos_and_def = lines[fn_def_line - len(fn.decorator_list) : body[0].lineno - 1]
    tail_lines = lines[fn.end_lineno :]
    out = "".join(head) + "".join(funcs) + "".join(decos_and_def) + new_body + "".join(tail_lines)
    MAIN.write_text(out, encoding="utf-8")
    for gi, (name, *_r) in enumerate(all_groups):
        print(f"{name}: 参数 {params[gi]} 返回 {returns[gi]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
