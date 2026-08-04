"""Architecture gates for workflow.db -> state.db product visibility."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _branch_for_message_type(tree: ast.AST, message_type: str) -> ast.If:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
            continue
        comparator = test.comparators[0]
        if (
            isinstance(comparator, ast.Constant)
            and comparator.value == message_type
        ):
            return node
    raise AssertionError(f"message branch not found: {message_type}")


def _method_lines(node: ast.AST, method: str) -> list[int]:
    return sorted(
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == method
    )


def _body_method_lines(node: ast.If, method: str) -> list[int]:
    return _method_lines(ast.Module(body=node.body, type_ignores=[]), method)


def test_every_product_history_read_crosses_consistency_gate_first() -> None:
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    history = _branch_for_message_type(tree, "session_messages_load")
    ensure_lines = _body_method_lines(history, "ensure_current")
    history_reads = _body_method_lines(history, "get_messages")
    assert len(ensure_lines) == 1
    assert history_reads
    assert ensure_lines[0] < history_reads[0]

    session_list = _branch_for_message_type(tree, "sessions_list")
    ensure_lines = _body_method_lines(session_list, "ensure_current")
    preview_reads = _body_method_lines(
        session_list, "list_sessions_with_preview"
    )
    assert len(ensure_lines) == 1
    assert len(preview_reads) == 2
    assert preview_reads[0] < ensure_lines[0] < preview_reads[1]


def test_production_registration_uses_gate_not_best_effort_readthrough() -> None:
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    assert '"session_terminal_projection_gate"' in source
    assert '"session_terminal_read_through"' not in source

    from context import ServiceContext, _VALID_SERVICES

    gate = object()
    services = ServiceContext()
    services.register("session_terminal_projection_gate", gate)
    assert services.get("session_terminal_projection_gate") is gate
    assert "session_terminal_projection_gate" in _VALID_SERVICES
    assert "session_terminal_read_through" not in _VALID_SERVICES


def test_product_context_gate_is_fail_closed_and_precedes_assembly() -> None:
    path = ROOT / "backend" / "deskpet" / "agent" / "turn_preparer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prepare = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "prepare_context"
    )
    ensure_lines = _method_lines(prepare, "ensure_current")
    assemble_lines = _method_lines(prepare, "assemble")
    assert len(ensure_lines) == 1
    assert assemble_lines
    assert ensure_lines[0] < assemble_lines[0]

    caught_lines = {
        child.lineno
        for child in ast.walk(prepare)
        if isinstance(child, ast.ExceptHandler)
    }
    assert not any(
        handler_line < ensure_lines[0] < assemble_lines[0]
        for handler_line in caught_lines
    )
