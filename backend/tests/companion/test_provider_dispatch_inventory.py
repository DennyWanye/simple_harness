from __future__ import annotations

import ast
import re
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
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _inside_dispatch_wrapper(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    node: ast.AST | None = call
    while node is not None:
        parent = parents.get(node)
        if isinstance(parent, ast.Call):
            parent_name = _call_name(parent)
            if parent_name.endswith(
                ("dispatch_with_run_fence", "coordinate_provider_call")
            ):
                return True
        node = parent
    return False


def test_production_provider_and_effect_calls_use_approved_dispatch_seams() -> None:
    inventory = {
        "backend/deskpet/workflows/adapters/research_runtime.py": {
            "self.llm.complete",
        },
        "backend/deskpet/workflows/adapters/deep_research_v6_evidence_runtime.py": {
            "self.search_gateway.search",
        },
        "backend/deskpet/workflows/adapters/code_runtime.py": {
            "self.llm_registry.chat_with_fallback",
            "execute_prepared",
            "execute_outcome",
        },
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
    expected = {
        (relative, name)
        for relative, names in inventory.items()
        for name in names
    }
    assert seen == expected
    assert violations == []


def test_ppt_image_provider_is_below_the_central_effect_dispatch_fence() -> None:
    tree = _parse("backend/deskpet/workflows/adapters/ppt_runtime.py")
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PptRuntime"
    )
    run_effect = next(
        node
        for node in runtime.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_effect"
    )
    assert any(
        isinstance(node, ast.Call)
        and _call_name(node).endswith("dispatch_with_run_fence")
        for node in ast.walk(run_effect)
    )
    image_calls = [
        node
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call)
        and _call_name(node) == "ppt_tools.generate_images"
    ]
    assert image_calls
    generate_method = next(
        node
        for node in runtime.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "generate_slide_image"
    )
    assert any(
        isinstance(node, ast.Call)
        and _call_name(node).endswith("self._run_effect")
        for node in ast.walk(generate_method)
    )


def test_retry_authority_is_not_hidden_below_the_coordinator() -> None:
    openai_sdk = (
        ROOT / "backend/llm/openai_adapter.py"
    ).read_text(encoding="utf-8")
    anthropic_sdk = (
        ROOT / "backend/llm/anthropic_adapter.py"
    ).read_text(encoding="utf-8")
    gemini_sdk = (
        ROOT / "backend/llm/gemini_adapter.py"
    ).read_text(encoding="utf-8")
    compatible = (
        ROOT / "backend/providers/openai_compatible.py"
    ).read_text(encoding="utf-8")
    shim = (
        ROOT / "backend/agent/tool_use_shim.py"
    ).read_text(encoding="utf-8")

    assert '"max_retries": 0' in openai_sdk
    assert "DispatchAwareAsyncTransport" in openai_sdk
    assert "self._max_retries != 0" in anthropic_sdk
    assert "DispatchAwareAsyncTransport" in anthropic_sdk
    assert "HttpRetryOptions(attempts=1)" in gemini_sdk
    assert "DispatchAwareAsyncTransport" in gemini_sdk
    assert "transport_retries = 0" in compatible
    assert "retry_mode = \"at_most_once\"" in compatible
    assert "chat_with_tools_at_most_once" in shim


def test_workflow_bootstrap_forwards_the_shared_fence_to_stage_ports() -> None:
    source = (
        ROOT
        / "backend/deskpet/workflows/adapters/deep_research_v6_bootstrap.py"
    ).read_text(encoding="utf-8")
    assert source.count(
        "dispatch_fence_acquirer=dispatch_fence_acquirer"
    ) >= 2
    assert (
        "provider_invocation_coordinator="
        "provider_invocation_coordinator"
    ) in source


def test_production_composition_injects_one_coordinator_and_shared_fence() -> None:
    source = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    assert len(re.findall(
        r'service_context\.get\(\s*"provider_invocation_coordinator"\s*\)',
        source,
    )) >= 3
    assert len(re.findall(
        r'service_context\.get\(\s*"run_execution_fence_acquirer"\s*\)',
        source,
    )) >= 5
    composition = (
        ROOT / "backend/deskpet/harness/adapters/product_composition.py"
    ).read_text(encoding="utf-8")
    assert "ProviderInvocationCoordinator(" in composition
    assert "provider_invocation_coordinator=provider_invocation_coordinator" in composition
    assert "provider_fence_acquirer=provider_fence_acquirer" in composition
