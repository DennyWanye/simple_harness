#!/usr/bin/env python3
"""Fail-closed architecture census for the R4.5 harness convergence.

The checked-in manifests classify every discovered authority.  Discovery is
semantic (AST + SQL), so moving a writer, making a method private, or renaming
an owner does not make it disappear from the gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLAN_DIR = ROOT / "plans/2026-07-20-agent-harness-simplification"
MANIFEST_PATHS = {
    "dml": PLAN_DIR / "execution_table_dml_authorities.json",
    "uow": PLAN_DIR / "uow_public_write_ops.json",
    "harness": PLAN_DIR / "harness_authorities.json",
}
REQUIRED_FIELDS = frozenset(
    {"path", "symbol", "authority", "tables", "callsites", "source_hash"}
)
EXECUTION_TABLES = frozenset(
    {
        "execution_runs",
        "execution_decisions",
        "execution_grants",
        "execution_continuations",
        "execution_child_commands",
        "execution_child_signal_inbox",
        "execution_effects",
        "execution_effect_links",
        "execution_events",
        "execution_deliveries",
        "execution_runtime_state",
        "execution_legacy_drain_items",
    }
)
SQL_DML_RE = re.compile(
    r"(?is)\b(?:insert(?:\s+or\s+\w+)?\s+into|update|delete\s+from|replace\s+into)"
    r"\s+([a-z_][a-z0-9_]*)"
)
SQL_TABLE_RE = re.compile(r"(?i)\b(execution_[a-z0-9_]+)\b")
GENERIC_WORDS = frozenset({"opcode", "operation", "action"})


class AuthorityInvariantError(RuntimeError):
    """Raised when discovery cannot be reconciled with an approved manifest."""


@dataclass(frozen=True)
class SymbolNode:
    path: str
    symbol: str
    node: ast.AST
    class_name: str | None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@lru_cache(maxsize=None)
def _production_files(repo: Path) -> tuple[Path, ...]:
    base = repo / "backend/deskpet"
    return tuple(
        path
        for path in sorted(base.rglob("*.py"))
        if "tests" not in path.relative_to(base).parts
        and "__pycache__" not in path.parts
    )


@lru_cache(maxsize=None)
def _read_tree(path: Path) -> tuple[str, ast.Module]:
    source = path.read_text(encoding="utf-8-sig")
    return source, ast.parse(source, filename=str(path))


def _source_hash(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise AuthorityInvariantError("cannot hash AST symbol without source segment")
    return _sha256(segment.encode("utf-8"))


def _sql_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_text(node.left)
        right = _sql_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _enclosing_symbols(tree: ast.Module, relative: str) -> list[SymbolNode]:
    result: list[SymbolNode] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.classes: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.classes.append(node.name)
            result.append(SymbolNode(relative, ".".join(self.classes), node, node.name))
            self.generic_visit(node)
            self.classes.pop()

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            prefix = ".".join(self.classes)
            symbol = f"{prefix}.{node.name}" if prefix else node.name
            result.append(
                SymbolNode(relative, symbol, node, self.classes[-1] if self.classes else None)
            )
            self.generic_visit(node)

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

    Visitor().visit(tree)
    return result


@lru_cache(maxsize=None)
def _callsite_index(repo: Path) -> dict[str, tuple[str, ...]]:
    result: dict[str, set[str]] = {}
    for path in _production_files(repo):
        relative = path.relative_to(repo).as_posix()
        _source, tree = _read_tree(path)
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_FunctionDef = _function
            visit_AsyncFunctionDef = _function

            def visit_Call(self, node: ast.Call) -> None:
                called = ""
                if isinstance(node.func, ast.Name):
                    called = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    called = node.func.attr
                if called:
                    owner = ".".join(stack) or "<module>"
                    result.setdefault(called, set()).add(
                        f"{relative}:{owner}:{node.lineno}"
                    )
                self.generic_visit(node)

        Visitor().visit(tree)
    return {key: tuple(sorted(value)) for key, value in result.items()}


def _symbol_callsites(
    repo: Path, *, symbol: str, method_name: str | None = None
) -> list[str]:
    needle = method_name or symbol.rsplit(".", 1)[-1]
    return list(_callsite_index(repo).get(needle, ()))


def _tables_in_node(node: ast.AST) -> set[str]:
    tables: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            tables.update(
                table.lower()
                for table in SQL_TABLE_RE.findall(child.value)
                if table.lower() in EXECUTION_TABLES
            )
    return tables


def _dml_in_node(node: ast.AST) -> set[str]:
    tables: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr not in {"execute", "executemany", "executescript"} or not child.args:
            continue
        sql = _sql_text(child.args[0])
        if sql is None:
            continue
        tables.update(
            match.group(1).lower()
            for match in SQL_DML_RE.finditer(sql)
            if match.group(1).lower() in EXECUTION_TABLES
        )
    return tables


def _generic_sql_violations(repo: Path) -> list[str]:
    """Reject dynamically selected execution DML and generic opcode dispatch."""
    violations: list[str] = []
    for path in _production_files(repo):
        relative = path.relative_to(repo).as_posix()
        if relative == "backend/deskpet/workflows/store/schema.py":
            # Versioned schema migration is classified separately from runtime DML.
            continue
        _source, tree = _read_tree(path)
        for symbol in _enclosing_symbols(tree, relative):
            if not isinstance(symbol.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            arg_names = {
                arg.arg.casefold()
                for arg in (*symbol.node.args.posonlyargs, *symbol.node.args.args, *symbol.node.args.kwonlyargs)
            }
            function_tables = _tables_in_node(symbol.node)
            for child in ast.walk(symbol.node):
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                    continue
                if child.func.attr not in {"execute", "executemany", "executescript"} or not child.args:
                    continue
                sql = _sql_text(child.args[0])
                if sql is None and function_tables:
                    violations.append(f"{relative}:{symbol.symbol}:{child.lineno}:dynamic_sql")
                elif (
                    sql is not None
                    and "{}" in sql
                    and set(table.lower() for table in SQL_TABLE_RE.findall(sql))
                    .intersection(EXECUTION_TABLES)
                    and not _dml_in_node(child)
                    and re.search(r"(?i)\b(insert|update|delete|replace)\b", sql)
                ):
                    violations.append(f"{relative}:{symbol.symbol}:{child.lineno}:dynamic_execution_sql")
            if (
                arg_names & GENERIC_WORDS
                and (_dml_in_node(symbol.node) or "table" in arg_names)
            ):
                violations.append(f"{relative}:{symbol.symbol}:generic_opcode")
    return sorted(set(violations))


def discover_dml_authorities(repo: Path = ROOT) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _production_files(repo):
        relative = path.relative_to(repo).as_posix()
        source, tree = _read_tree(path)
        symbols = _enclosing_symbols(tree, relative)
        classes = [item for item in symbols if isinstance(item.node, ast.ClassDef)]
        class_hits = [(item, _dml_in_node(item.node)) for item in classes]
        covered = {id(node) for item, tables in class_hits if tables for node in ast.walk(item.node)}
        for item, tables in class_hits:
            if not tables:
                continue
            authority = (
                "sqlite_execution_uow"
                if item.symbol.endswith("SqliteExecutionUnitOfWork")
                else "checkpoint_execution_adapter"
                if item.symbol.endswith("SqliteCheckpointExecutionAdapter")
                else "unclassified"
            )
            items.append(
                {
                    "path": relative,
                    "symbol": item.symbol,
                    "authority": authority,
                    "tables": sorted(tables),
                    "callsites": _symbol_callsites(repo, symbol=item.symbol),
                    "source_hash": _source_hash(source, item.node),
                    "kind": "runtime_dml",
                    "counted": authority != "schema_migration",
                }
            )
        for item in symbols:
            if not isinstance(item.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(item.node) in covered:
                continue
            tables = _dml_in_node(item.node)
            if not tables:
                continue
            authority = (
                "schema_migration"
                if relative == "backend/deskpet/workflows/store/schema.py"
                and item.symbol == "_migrate_v6_to_v7"
                else "unclassified"
            )
            items.append(
                {
                    "path": relative,
                    "symbol": item.symbol,
                    "authority": authority,
                    "tables": sorted(tables),
                    "callsites": _symbol_callsites(repo, symbol=item.symbol),
                    "source_hash": _source_hash(source, item.node),
                    "kind": "schema_migration" if authority == "schema_migration" else "runtime_dml",
                    "counted": authority != "schema_migration",
                }
            )
    return sorted(items, key=lambda item: (item["path"], item["symbol"]))


def _class_method_graph(class_node: ast.ClassDef) -> dict[str, set[str]]:
    methods = {
        item.name: item
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    graph: dict[str, set[str]] = {}
    for name, node in methods.items():
        graph[name] = {
            child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "self"
            and child.func.attr in methods
        }
    return graph


def discover_uow_write_ops(repo: Path = ROOT) -> list[dict[str, Any]]:
    relative = "backend/deskpet/workflows/store/execution_uow.py"
    path = repo / relative
    source, tree = _read_tree(path)
    class_node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == "SqliteExecutionUnitOfWork"
    )
    methods = {
        item.name: item
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    graph = _class_method_graph(class_node)
    starters: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in methods.values():
        if node.name == "_write_transaction":
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "self"
            and child.func.attr == "_write_transaction"
            for child in ast.walk(node)
        ):
            starters.append(node)

    items: list[dict[str, Any]] = []
    for node in starters:
        reachable = {node.name}
        pending = [node.name]
        while pending:
            current = pending.pop()
            for called in graph.get(current, ()):
                if called not in reachable:
                    reachable.add(called)
                    pending.append(called)
        tables: set[str] = set()
        for name in reachable:
            tables.update(_tables_in_node(methods[name]))
        arg_names = {
            arg.arg.casefold()
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        }
        items.append(
            {
                "path": relative,
                "symbol": f"SqliteExecutionUnitOfWork.{node.name}",
                "authority": "sqlite_execution_uow",
                "tables": sorted(tables),
                "callsites": _symbol_callsites(
                    repo, symbol=f"SqliteExecutionUnitOfWork.{node.name}", method_name=node.name
                ),
                "source_hash": _source_hash(source, node),
                "kind": "transaction_starter",
                "public": not node.name.startswith("_"),
                "generic_opcode": bool(arg_names & GENERIC_WORDS),
            }
        )
    return sorted(items, key=lambda item: item["symbol"])


def _assignment_target(node: ast.AST, class_name: str | None, function: str | None) -> str | None:
    if isinstance(node, ast.Name) and class_name and function is None:
        return f"{class_name}.{node.id}"
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and class_name
        and function == "__init__"
    ):
        return f"{class_name}.{node.attr}"
    return None


def _annotation_text(node: ast.AST | None) -> str:
    return "" if node is None else ast.unparse(node)


def _is_map_value(value: ast.AST | None, annotation: ast.AST | None) -> bool:
    text = _annotation_text(annotation).casefold()
    if "dict" in text or "mapping" in text:
        return True
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
        return True
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "_runs"
    )


def _field_candidates(repo: Path) -> list[tuple[str, str, str, ast.AST, str]]:
    """Return path, symbol, kind, defining node, source for harness state fields."""
    result: list[tuple[str, str, str, ast.AST, str]] = []
    base = repo / "backend/deskpet/harness"
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(repo).as_posix()
        source, tree = _read_tree(path)
        classes: list[str] = []
        functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                classes.append(node.name)
                self.generic_visit(node)
                classes.pop()

            def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                functions.append(node.name)
                self.generic_visit(node)
                functions.pop()

            visit_FunctionDef = _function
            visit_AsyncFunctionDef = _function

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                symbol = _assignment_target(
                    node.target,
                    classes[-1] if classes else None,
                    functions[-1] if functions else None,
                )
                if symbol:
                    leaf = symbol.rsplit(".", 1)[-1].casefold()
                    annotation = _annotation_text(node.annotation).casefold()
                    if _is_map_value(node.value, node.annotation) and any(
                        word in leaf for word in ("run", "active", "volatile", "live")
                    ):
                        result.append((relative, symbol, "run_map", node, source))
                    if "task" in annotation:
                        result.append((relative, symbol, "task", node, source))
                self.generic_visit(node)

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    symbol = _assignment_target(
                        target,
                        classes[-1] if classes else None,
                        functions[-1] if functions else None,
                    )
                    if not symbol:
                        continue
                    leaf = symbol.rsplit(".", 1)[-1].casefold()
                    if _is_map_value(node.value, None) and any(
                        word in leaf for word in ("run", "active", "volatile", "live")
                    ):
                        result.append((relative, symbol, "run_map", node, source))
                    if (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "create_task"
                    ):
                        result.append((relative, symbol, "task", node, source))
                self.generic_visit(node)

        Visitor().visit(tree)
    unique: dict[tuple[str, str, str], tuple[str, str, str, ast.AST, str]] = {}
    for item in result:
        unique.setdefault(item[:3], item)
    return [unique[key] for key in sorted(unique)]


def _converter_candidates(repo: Path) -> list[tuple[str, str, str, ast.AST, str]]:
    result: list[tuple[str, str, str, ast.AST, str]] = []
    for path in _production_files(repo):
        relative = path.relative_to(repo).as_posix()
        source, tree = _read_tree(path)
        for symbol in _enclosing_symbols(tree, relative):
            node = symbol.node
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            inputs = " ".join(
                _annotation_text(arg.annotation)
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            )
            output = _annotation_text(node.returns)
            if "RunEvent" in inputs and "AgentEvent" in output:
                kind = "converter_contract" if len(node.body) == 1 and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and node.body[0].value.value is Ellipsis else "converter"
                result.append((relative, symbol.symbol, kind, node, source))
    return result


def _legacy_owner_candidates(repo: Path) -> list[tuple[str, str, str, ast.AST, str]]:
    from scripts.acceptance.harness_owner_audit import BASELINE_OWNERS

    result: list[tuple[str, str, str, ast.AST, str]] = []
    by_path: dict[str, list[Any]] = {}
    for owner in BASELINE_OWNERS:
        by_path.setdefault(owner.path, []).append(owner)
    for relative, owners in by_path.items():
        path = repo / relative
        if not path.is_file():
            continue
        source, tree = _read_tree(path)
        classes: list[str] = []
        functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                classes.append(node.name)
                self.generic_visit(node)
                classes.pop()

            def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                functions.append(node.name)
                self.generic_visit(node)
                functions.pop()

            visit_FunctionDef = _function
            visit_AsyncFunctionDef = _function

            def _check(self, target: ast.AST, node: ast.AST) -> None:
                if isinstance(target, ast.Name) and not classes and not functions:
                    scope, name = "<module>", target.id
                elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self" and classes and functions and functions[-1] == "__init__":
                    scope, name = classes[-1], target.attr
                else:
                    return
                for owner in owners:
                    if owner.scope == scope and owner.symbol == name:
                        result.append((relative, f"{scope}.{name}", "legacy_owner", node, source))

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    self._check(target, node)
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                self._check(node.target, node)
                self.generic_visit(node)

        Visitor().visit(tree)
    return result


def discover_harness_authorities(repo: Path = ROOT) -> list[dict[str, Any]]:
    raw = _field_candidates(repo) + _converter_candidates(repo) + _legacy_owner_candidates(repo)
    unique_raw: dict[tuple[str, str, str], tuple[str, str, str, ast.AST, str]] = {}
    for item in raw:
        unique_raw.setdefault(item[:3], item)
    items: list[dict[str, Any]] = []
    for relative, symbol, kind, node, source in unique_raw.values():
        if kind == "run_map":
            authority = "live_run_map"
        elif kind == "task":
            authority = "run_task" if symbol == "LiveRun.task" else "supervisor_task"
        elif kind == "converter":
            authority = "presenter_converter"
        elif kind == "converter_contract":
            authority = "presenter_converter_contract"
        else:
            authority = "legacy_survivor"
        items.append(
            {
                "path": relative,
                "symbol": symbol,
                "authority": authority,
                "tables": [],
                "callsites": _symbol_callsites(
                    repo, symbol=symbol, method_name=symbol.rsplit(".", 1)[-1]
                ),
                "source_hash": _source_hash(source, node),
                "kind": kind,
            }
        )
    return sorted(items, key=lambda item: (item["kind"], item["path"], item["symbol"]))


def build_manifests(repo: Path = ROOT) -> dict[str, dict[str, Any]]:
    dml = discover_dml_authorities(repo)
    uow = discover_uow_write_ops(repo)
    harness = discover_harness_authorities(repo)
    return {
        "dml": {
            "schema_version": 1,
            "baseline_commit": _git_commit(repo),
            "baseline": {"runtime_dml_authority_count": sum(bool(item["counted"]) for item in dml)},
            "target": {"runtime_dml_authority_count": 1},
            "items": dml,
        },
        "uow": {
            "schema_version": 1,
            "baseline_commit": _git_commit(repo),
            "baseline": {"transaction_starter_count": len(uow)},
            "target": {"max_transaction_starter_count": 23},
            "items": uow,
        },
        "harness": {
            "schema_version": 1,
            "baseline_commit": _git_commit(repo),
            "baseline": {
                "legacy_survivor_count": sum(item["kind"] == "legacy_owner" for item in harness),
                "run_map_authority_count": sum(item["kind"] == "run_map" for item in harness),
                "supervisor_task_authority_count": sum(item["authority"] == "supervisor_task" for item in harness),
                "presenter_converter_authority_count": sum(item["authority"] == "presenter_converter" for item in harness),
            },
            "target": {
                "max_legacy_survivor_count": 15,
                "run_map_authority_count": 1,
                "supervisor_task_authority_count": 1,
                "presenter_converter_authority_count": 1,
            },
            "items": harness,
        },
    }


def _item_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return str(item["path"]), str(item["symbol"]), str(item.get("kind") or "")


def _validate_fields(items: Iterable[dict[str, Any]], label: str) -> list[str]:
    failures: list[str] = []
    for index, item in enumerate(items):
        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            failures.append(f"{label}[{index}] missing fields: {sorted(missing)}")
    return failures


def audit(repo: Path = ROOT, *, enforce_target: bool = False) -> dict[str, Any]:
    discovered = build_manifests(repo)
    failures = _generic_sql_violations(repo)
    details: dict[str, Any] = {}
    for label, path in MANIFEST_PATHS.items():
        manifest_path = repo / path.relative_to(ROOT) if repo != ROOT else path
        if not manifest_path.is_file():
            failures.append(f"missing manifest: {manifest_path}")
            continue
        approved = json.loads(manifest_path.read_text(encoding="utf-8"))
        current = discovered[label]
        failures.extend(_validate_fields(approved.get("items", ()), label))
        approved_by_id = {_item_identity(item): item for item in approved.get("items", ())}
        current_by_id = {_item_identity(item): item for item in current["items"]}
        unclassified = sorted(set(current_by_id) - set(approved_by_id))
        missing = sorted(set(approved_by_id) - set(current_by_id))
        changed = sorted(
            identity
            for identity in set(current_by_id) & set(approved_by_id)
            if current_by_id[identity] != approved_by_id[identity]
        )
        if unclassified:
            failures.append(f"{label} unclassified discoveries: {unclassified}")
        if missing:
            failures.append(f"{label} manifest items missing from source: {missing}")
        if changed:
            failures.append(f"{label} manifest/source drift: {changed}")
        if approved.get("baseline") != current.get("baseline"):
            failures.append(
                f"{label} baseline mismatch: approved={approved.get('baseline')} current={current.get('baseline')}"
            )
        details[label] = current["baseline"]

    if enforce_target:
        dml_count = discovered["dml"]["baseline"]["runtime_dml_authority_count"]
        tx_count = discovered["uow"]["baseline"]["transaction_starter_count"]
        harness_counts = discovered["harness"]["baseline"]
        if dml_count != 1:
            failures.append(f"target runtime DML authority count is 1, found {dml_count}")
        if tx_count > 23:
            failures.append(f"target transaction starter count is <=23, found {tx_count}")
        for name in (
            "run_map_authority_count",
            "supervisor_task_authority_count",
            "presenter_converter_authority_count",
        ):
            if harness_counts[name] != 1:
                failures.append(f"target {name} is 1, found {harness_counts[name]}")
        if harness_counts["legacy_survivor_count"] > 15:
            failures.append(
                f"target legacy survivors is <=15, found {harness_counts['legacy_survivor_count']}"
            )
    return {"passed": not failures, "failures": failures, "current": details}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write manifests for the current source")
    parser.add_argument("--check", action="store_true", help="check source against approved manifests")
    parser.add_argument("--enforce-target", action="store_true", help="also enforce R4.5 exit targets")
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    args = parser.parse_args()
    if args.write:
        manifests = build_manifests(ROOT)
        for label, payload in manifests.items():
            MANIFEST_PATHS[label].write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    result = audit(ROOT, enforce_target=args.enforce_target) if args.check or not args.write else {"passed": True, "failures": [], "current": {}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
