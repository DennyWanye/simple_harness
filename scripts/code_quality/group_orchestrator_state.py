"""代码质量整治 5B.1：编排器可变簿记字段按用途分组进状态数据类（一次性转换，留档）。

用法：python3 scripts/code_quality/group_orchestrator_state.py sdk/simple-harness-sdk          # 改写源码
      python3 scripts/code_quality/group_orchestrator_state.py sdk/simple-harness-sdk --check <改写前副本的根>  # 反向还原比对
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(sys.argv[1])
PKG = ROOT / "src/agent_orchestrator/orchestrator"
CORE = PKG / "orchestrator_domains/core.py"
STATE = PKG / "orchestrator_domains/state.py"
FILES = [PKG / "event_handler.py", *sorted((PKG / "orchestrator_domains").glob("*.py"))]

GROUPS = {
    "_planning": ("PlanningRoundState", "Planner rounds waiting, blocked or stalled, per Mission.", [
        "_deferred_planning", "_planning_evidence_waits", "_service_blocked_since",
        "_handoff_known_waits", "_after_handoff_zero_streak", "_counted_after_handoff_unknowns",
        "_stalled_at", "_stall_carry_ons"]),
    "_attempts": ("AttemptState", "Attempts deferred, collected, released or being verified.", [
        "_deferred", "_port_claims", "_resource_requests", "_client_ids", "_released",
        "_verifying", "_creation_refusals_noted", "cancel_receipts"]),
    "_reviews": ("ReviewState", "Critic verdicts, refusals and overdue review calls.", [
        "_critic_verdicts", "_verdict_refusals", "_carried_errors", "_review_call_marks"]),
    "_rounds": ("MissionRoundState", "Per-Mission round faults, recovery isolation and scheduling marks.", [
        "_round_faults", "_unrecovered", "_recovery_isolated", "_parked_faults", "_startup_faults",
        "_mission_marks", "_rotation", "_contract_checked", "_agent_sweep_at", "_agents_closed",
        "progress_log"]),
    "_routes": ("RoutingCache", "Per-Mission policy and model-router caches.", [
        "_policies", "_routers", "_route_drops", "_route_noted"]),
    "_between": ("CycleHooks", "The deployment's between-cycle duty and its pacing.", [
        "_between_cycles", "_between_every", "_between_last"]),
    "_wiring": ("DeploymentWiring", "What a deployment installs: hierarchical, TaskGraph and Assurance hooks.", [
        "_hierarchical", "_planning_world_factory", "_planning_start_gate", "_mission_dispatches",
        "_taskgraph_dispatch_setup", "_taskgraph_notifications", "_assurance_tick",
        "_assurance_local_checks", "_assurance_reviews", "_assurance_root_gate",
        "_assurance_root_setup", "_assurance_management_only", "_taskgraph_read_apis",
        "_assurance_read_apis", "_taskgraph_operator", "_taskgraph_policy"]),
}
FIELD = {f: (g, f.lstrip("_")) for g, (_, _, fs) in GROUPS.items() for f in fs}
# 原来没写类型注解的四个字段
MISSING_ANN = {
    "_assurance_root_setup": "Callable[[Orchestrator], None] | None",
    "_assurance_management_only": "bool",
    "_between_every": "float",
    "_between_last": "float",
    "_rotation": "int",
}
FACTORY = {"{}": "dict", "set()": "set", "[]": "list", "DeferredPlanning()": "DeferredPlanning"}


def init_of(tree: ast.Module) -> ast.FunctionDef:
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OrchestratorCore")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")


def target(stmt: ast.stmt) -> str | None:
    tg = stmt.target if isinstance(stmt, ast.AnnAssign) else (
        stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else None)
    if isinstance(tg, ast.Attribute) and isinstance(tg.value, ast.Name) and tg.value.id == "self":
        return tg.attr
    return None


def rewrite() -> None:
    src = CORE.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    init = init_of(ast.parse(src))
    picked: dict[str, tuple[ast.stmt, list[str]]] = {}
    for stmt in init.body:
        name = target(stmt)
        if name in FIELD:
            # 紧挨在上方的注释行随字段一起搬
            first = stmt.lineno - 1
            while lines[first - 1].strip().startswith("#"):
                first -= 1
            comments = [ln.strip() for ln in lines[first:stmt.lineno - 1]]
            picked[name] = (stmt, comments)
    missing = set(FIELD) - set(picked)
    assert not missing, missing
    # 1. 状态数据类
    out = ["# SPDX-FileCopyrightText: 2026 DennyWanye\n", "# SPDX-License-Identifier: Apache-2.0\n",
           '"""Mutable scheduling state the orchestrator keeps between cycles, grouped by concern."""\n\n',
           "from __future__ import annotations\n\n",
           "import asyncio\n",
           "from collections.abc import Callable\n",
           "from dataclasses import dataclass, field\n",
           "from typing import TYPE_CHECKING, Any\n\n",
           "from ..deferred_planning import DeferredPlanning\n\n",
           "if TYPE_CHECKING:\n",
           "    from ...runtime.model_router import ModelRouter\n",
           "    from ...runtime.output_blocks import PortClaim\n",
           "    from ...verification.critics import CriticVerdict\n",
           "    from ..event_handler import Orchestrator\n",
           "    from ..hierarchical_dispatch import HierarchicalDispatch\n"]
    for group, (cls, doc, fields) in GROUPS.items():
        out.append(f"\n\n@dataclass(slots=True)\nclass {cls}:\n    \"\"\"{doc}\"\"\"\n")
        for f in fields:
            stmt, comments = picked[f]
            ann = ast.unparse(stmt.annotation) if isinstance(stmt, ast.AnnAssign) else MISSING_ANN[f]
            value = ast.unparse(stmt.value)
            if value in FACTORY:
                default = f" = field(default_factory={FACTORY[value]})"
            elif value in ("None", "0", "0.0", "False"):
                default = f" = {value}"
            else:
                default = " = None"  # 构造参数传入的值，构造时显式给
            out.append("\n" + "".join(f"    {c}\n" for c in comments) + f"    {FIELD[f][1]}: {ann}{default}\n")
    STATE.write_text("".join(out), encoding="utf-8")
    # 2. __init__：删字段赋值（连同注释），在每组第一个字段原位置构造状态对象
    first_of = {}
    for f, (stmt, comments) in picked.items():
        g = FIELD[f][0]
        if g not in first_of or stmt.lineno < first_of[g][0]:
            first_of[g] = (stmt.lineno - len(comments), stmt)
    removals = sorted(((stmt.lineno - len(c), stmt.end_lineno, f) for f, (stmt, c) in picked.items()), reverse=True)
    inserts = {line: g for g, (line, _) in first_of.items()}
    for start, end, f in removals:
        g = FIELD[f][0]
        ind = " " * picked[f][0].col_offset
        new = []
        if inserts.get(start) == g:
            cls = GROUPS[g][0]
            args = "assurance_root_setup=assurance_root_setup" if g == "_wiring" else ""
            new = [f"{ind}self.{g} = {cls}({args})\n"]
        lines[start - 1:end] = new
    CORE.write_text("".join(lines), encoding="utf-8")
    # 3. 编排器全部文件：self._x → self._组.x
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        flines = text.splitlines(keepends=True)
        edits = []
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
                    and n.attr in FIELD):
                g, new = FIELD[n.attr]
                edits.append((n.lineno, n.col_offset, n.end_lineno, n.end_col_offset, f"self.{g}.{new}"))
        for l1, c1, l2, c2, rep in sorted(edits, reverse=True):
            assert l1 == l2
            b = flines[l1 - 1].encode()
            flines[l1 - 1] = (b[:c1] + rep.encode() + b[c2:]).decode()
        if edits:
            path.write_text("".join(flines), encoding="utf-8")
            print(path.name, len(edits))


def check(base: Path) -> None:
    """反向还原：self._组.x → self._x，删状态对象构造，与原文件比语法树（core 的 __init__ 另比）。"""
    rev = {(g, new): f for f, (g, new) in FIELD.items()}

    class Back(ast.NodeTransformer):
        def visit_Attribute(self, n):  # type: ignore[no-untyped-def]
            if (isinstance(n.value, ast.Attribute) and isinstance(n.value.value, ast.Name)
                    and n.value.value.id == "self" and (n.value.attr, n.attr) in rev):
                return ast.Attribute(ast.Name("self", ast.Load()), rev[(n.value.attr, n.attr)], n.ctx)
            self.generic_visit(n)
            return n

    for path in FILES:
        if path.name == "state.py":
            continue
        old_path = base / path.relative_to(ROOT)
        old = ast.parse(old_path.read_text(encoding="utf-8"))
        new = Back().visit(ast.parse(path.read_text(encoding="utf-8")))
        if path == CORE:
            for tree in (old, new):
                init = init_of(tree)
                init.body = [s for s in init.body if target(s) not in FIELD and target(s) not in GROUPS]
        same = ast.dump(old) == ast.dump(new)
        print(("一致 " if same else "不同 ") + path.name)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check(Path(sys.argv[sys.argv.index("--check") + 1]))
    else:
        rewrite()
