# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_agent_harness_lifecycle_doc_exists_and_mentions_contracts() -> None:
    doc = REPO_ROOT / "docs" / "agent-harness-lifecycle.md"
    text = doc.read_text(encoding="utf-8")
    for needle in [
        "build_agent",
        "AgentLoop",
        "ContextAssembler",
        "ServiceContext",
        "completion_queue",
        "SubagentRegistry",
        "trace",
        "lifecycle",
        "windows-mcp",
    ]:
        assert needle in text
