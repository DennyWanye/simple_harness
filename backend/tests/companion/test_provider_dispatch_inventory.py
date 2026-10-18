from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _parse(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _call_name(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.expr = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _inside_dispatch_wrapper(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    node: ast.AST | None = call
    while node is not None:
        parent = parents.get(node)
        if isinstance(parent, ast.Call):
            parent_name = _call_name(parent)
            if parent_name.endswith(("dispatch_with_run_fence", "coordinate_provider_call")):
                return True
        node = parent
    return False


def test_production_provider_and_effect_calls_use_approved_dispatch_seams() -> None:
    inventory = {
        "backend/deskpet/harness/tool_executor.py": {
            "self._registry.execute_prepared",
        },
    }
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relative, targets in inventory.items():
        tree = _parse(relative)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in targets:
                continue
            seen.add((relative, name))
            if not _inside_dispatch_wrapper(node, parents):
                violations.append(f"{relative}:{node.lineno}:{name}")
    expected = {(relative, name) for relative, names in inventory.items() for name in names}
    assert seen == expected
    assert violations == []


def test_retry_authority_is_not_hidden_below_the_coordinator() -> None:
    # 提供方拆成 mixin 后，按门面加 openai_compatible_domains/ 整个包读
    compatible = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "backend/providers/openai_compatible.py",
            *sorted((ROOT / "backend/providers/openai_compatible_domains").glob("*.py")),
        )
    )

    assert "transport_retries = 0" in compatible
    assert 'retry_mode = "at_most_once"' in compatible
