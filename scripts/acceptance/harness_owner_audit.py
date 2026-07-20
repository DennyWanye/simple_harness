#!/usr/bin/env python3
"""Audit legacy mutable harness owners and newly-added equivalents."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = (
    ROOT / "plans/2026-07-20-agent-harness-simplification/baseline.json"
)


@dataclass(frozen=True)
class Owner:
    name: str
    path: str
    scope: str
    symbol: str


BASELINE_OWNERS = (
    Owner("_permission_pending", "backend/main.py", "<module>", "_permission_pending"),
    Owner("_clarify_pending", "backend/main.py", "<module>", "_clarify_pending"),
    Owner("_PLAN_CONFIRM_WAITERS", "backend/main.py", "<module>", "_PLAN_CONFIRM_WAITERS"),
    Owner("_SKILL_CANDIDATE_WAITERS", "backend/main.py", "<module>", "_SKILL_CANDIDATE_WAITERS"),
    Owner("_PPT_OUTLINE_WAITERS", "backend/main.py", "<module>", "_PPT_OUTLINE_WAITERS"),
    Owner("_chat_inflight", "backend/main.py", "<module>", "_chat_inflight"),
    Owner("_auto_resume_redispatchers", "backend/main.py", "<module>", "_auto_resume_redispatchers"),
    Owner("_pipelines", "backend/main.py", "<module>", "_pipelines"),
    Owner("_PPT_PRO_CTX", "backend/deskpet/tools/ppt_tools.py", "<module>", "_PPT_PRO_CTX"),
    Owner("_PPT_PRO_TASKS", "backend/deskpet/tools/ppt_tools.py", "<module>", "_PPT_PRO_TASKS"),
    Owner("_PPT_PRO_RUNNING", "backend/deskpet/tools/ppt_tools.py", "<module>", "_PPT_PRO_RUNNING"),
    Owner("_PPT_PRO_RUNNING_TOPIC", "backend/deskpet/tools/ppt_tools.py", "<module>", "_PPT_PRO_RUNNING_TOPIC"),
    Owner("SubagentRegistry._runs", "backend/deskpet/agent/subagent_registry.py", "SubagentRegistry", "_runs"),
    Owner("SubagentRegistry.completion_queue", "backend/deskpet/agent/subagent_registry.py", "SubagentRegistry", "completion_queue"),
    Owner("PermissionGate._pending", "backend/deskpet/permissions/gate.py", "PermissionGate", "_pending"),
)

OWNER_WORDS = (
    "pending", "waiter", "inflight", "running", "task", "run", "queue",
    "pipeline", "redispatch", "decision", "completion", "active", "effect",
    "subscription", "handle", "wakeup", "supervisor", "grant", "delivery",
)
MUTABLE_CALLS = {
    "dict", "set", "list", "deque", "defaultdict", "Queue", "PriorityQueue",
    "LifoQueue", "WeakSet", "WeakValueDictionary",
}


@dataclass(frozen=True)
class Candidate:
    key: str
    path: str
    scope: str
    symbol: str
    kind: str
    line: int


def _call_tail(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _kind(value: ast.AST | None) -> str | None:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(value, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(value, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(value, ast.Call):
        tail = _call_tail(value.func)
        if tail in MUTABLE_CALLS:
            return tail
        if any(word in tail.casefold() for word in ("waiter", "registry", "queue")):
            return f"object:{tail}"
    return None


class _Assignments(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.class_name: str | None = None
        self.function_name: str | None = None
        self.items: list[tuple[str, str, ast.AST | None, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous = self.class_name
        self.class_name = node.name
        self.generic_visit(node)
        self.class_name = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.function_name
        self.function_name = node.name
        self.generic_visit(node)
        self.function_name = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def _target(self, target: ast.AST) -> tuple[str, str] | None:
        if isinstance(target, ast.Name) and self.function_name is None:
            return "<module>", target.id
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and self.class_name
            and self.function_name == "__init__"
        ):
            return self.class_name, target.attr
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            found = self._target(target)
            if found:
                self.items.append((*found, node.value, node.lineno))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        found = self._target(node.target)
        if found:
            self.items.append((*found, node.value, node.lineno))
        self.generic_visit(node)


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted((ROOT / "backend").rglob("*.py"))
        if "tests" not in path.relative_to(ROOT / "backend").parts
    ]


def _assignments(path: Path) -> list[tuple[str, str, ast.AST | None, int]]:
    relative = path.relative_to(ROOT).as_posix()
    visitor = _Assignments(relative)
    visitor.visit(ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative))
    return visitor.items


def scan_owner_candidates() -> list[Candidate]:
    result: dict[str, Candidate] = {}
    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        for scope, symbol, value, line in _assignments(path):
            kind = _kind(value)
            lowered = symbol.casefold()
            if kind is None or not any(word in lowered for word in OWNER_WORDS):
                continue
            key = f"{relative}::{scope}.{symbol}"
            result.setdefault(key, Candidate(key, relative, scope, symbol, kind, line))
    return [result[key] for key in sorted(result)]


def _definition_index() -> dict[tuple[str, str, str], int]:
    found: dict[tuple[str, str, str], int] = {}
    relevant = {owner.path for owner in BASELINE_OWNERS}
    for relative in relevant:
        path = ROOT / relative
        if not path.is_file():
            continue
        for scope, symbol, _value, line in _assignments(path):
            found.setdefault((relative, scope, symbol), line)
    return found


def _reference_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload.get("owner_audit", {}).get("candidate_keys", ()))


def audit(reference: Path = DEFAULT_BASELINE) -> dict[str, Any]:
    definitions = _definition_index()
    survivors = []
    missing = []
    for owner in BASELINE_OWNERS:
        line = definitions.get((owner.path, owner.scope, owner.symbol))
        item = {**asdict(owner), "line": line}
        (survivors if line is not None else missing).append(item)

    candidates = scan_owner_candidates()
    reference_keys = _reference_keys(reference)
    new = [asdict(item) for item in candidates if item.key not in reference_keys]
    return {
        "schema_version": 1,
        "baseline": len(BASELINE_OWNERS),
        "survivor_count": len(survivors),
        "survivors": survivors,
        "missing": missing,
        "candidate_count": len(candidates),
        "candidate_keys": [item.key for item in candidates],
        "reference": reference.relative_to(ROOT).as_posix() if reference.is_relative_to(ROOT) else str(reference),
        "reference_candidate_count": len(reference_keys),
        "new_equivalent_count": len(new),
        "new_equivalents": new,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit stable machine-readable JSON")
    parser.add_argument("--reference", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--max-owners", type=int)
    args = parser.parse_args()
    result = audit(args.reference.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.max_owners is None:
        return 0
    return 0 if result["survivor_count"] + result["new_equivalent_count"] <= args.max_owners else 1


if __name__ == "__main__":
    raise SystemExit(main())
