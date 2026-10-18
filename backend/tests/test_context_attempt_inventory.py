from __future__ import annotations

import re
from pathlib import Path

from tests.host_source import host_source_files

BACKEND = Path(__file__).resolve().parents[1]
CALL = re.compile(
    r"await\s+[^\n]*\.(?:chat_with_tools|chat_stream_with_tools|"
    r"chat_with_fallback|chat_with_fallback_stream)\s*\("
)


def test_runtime_provider_calls_are_owned_by_scope_or_bottom_seam() -> None:
    bottom_seams = {
        "providers/openai_compatible.py",
        "router/hybrid_router.py",
    }
    uncovered: list[str] = []
    paths = list(host_source_files())
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
        "deskpet/agent/assembler/classifier.py": "classifier",
        "deskpet/tools/research_tools.py": "research",
    }
    for relative, purpose in expected.items():
        source = (BACKEND / relative).read_text(encoding="utf-8")
        assert re.search(rf'provider_purpose_scope\(\s*"{re.escape(purpose)}"', source)
