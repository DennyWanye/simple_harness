from __future__ import annotations

import re
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
CALL = re.compile(
    r"await\s+[^\n]*\.(?:chat_with_tools|chat_stream_with_tools|"
    r"chat_with_fallback|chat_with_fallback_stream)\s*\("
)


def test_runtime_provider_calls_are_owned_by_scope_or_bottom_seam() -> None:
    bottom_seams = {
        "agent/agent_loop.py",
        "agent/tool_use_shim.py",
        "llm/registry.py",
        "providers/openai_compatible.py",
        "router/hybrid_router.py",
    }
    uncovered: list[str] = []
    paths = [BACKEND / "main.py"]
    for root in ("agent", "deskpet", "llm", "pipeline", "providers"):
        paths.extend((BACKEND / root).rglob("*.py"))
    for path in paths:
        relative = path.relative_to(BACKEND).as_posix()
        if relative.startswith(("tests/", "scripts/", "deskpet/memory/eval/")):
            continue
        source = path.read_text(encoding="utf-8")
        if not CALL.search(source):
            continue
        if relative in bottom_seams:
            continue
        if "provider_purpose_scope" not in source:
            uncovered.append(relative)
    assert uncovered == []


def test_representative_auxiliary_purposes_are_explicit() -> None:
    expected = {
        "agent/capability_gate.py": "capability_gate",
        "deskpet/agent/assembler/classifier.py": "classifier",
        "agent/plan.py": "planner",
        "agent/context_manager.py": "context_manager",
        "deskpet/agent/context_compressor.py": "compressor",
        "deskpet/tools/research_tools.py": "research",
        "deskpet/workflows/adapters/code_runtime.py": "workflow",
    }
    for relative, purpose in expected.items():
        source = (BACKEND / relative).read_text(encoding="utf-8")
        assert re.search(
            rf'provider_purpose_scope\(\s*"{re.escape(purpose)}"', source
        )


def test_provider_dispatch_bottom_seams_auto_create_attempts() -> None:
    provider = (BACKEND / "providers/openai_compatible.py").read_text(
        encoding="utf-8"
    )
    registry = (BACKEND / "llm/registry.py").read_text(encoding="utf-8")
    router = (BACKEND / "router/hybrid_router.py").read_text(encoding="utf-8")
    assert provider.count("auto_context_attempt_call(") >= 2
    assert provider.count("auto_context_attempt_iter(") >= 2
    assert "auto_context_attempt_call(" in registry
    assert "auto_context_attempt_iter(" in router
