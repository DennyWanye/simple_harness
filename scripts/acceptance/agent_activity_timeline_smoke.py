#!/usr/bin/env python3
"""Deterministic contract smoke for the Agent activity timeline slice.

This intentionally tests the public read/context boundary without pretending to
be a desktop UI run. UI acceptance remains in testcase S-1..S-5.
"""
from __future__ import annotations

from deskpet.agent.assembler.bundle import ContextFragment
from deskpet.execution.harness_public_read_service import _public_content, _public_views
from deskpet.execution.run_read_model import PublicFactEnvelope
from deskpet.execution.semantic_projection import reduce_public_manifest
from deskpet.execution.run_read_model import ReadCutV1, ReadSourceCutV1


def main() -> None:
    fact = PublicFactEnvelope(
        source="workflow",
        stable_id="tool:smoke",
        root_run_id="root-smoke",
        kind="tool",
        public_payload={
            "tool_name": "读取文件",
            "action_code": "inspect",
            "status": "completed",
            "safe_target_label": "README.md",
            "safe_input": {"path": "README.md"},
            "bounded_result": {"summary": "ok"},
            "reasoning_content": "must not cross",
        },
        created_at=1.0,
    )
    activities, tools, messages = _public_views(
        (fact,), ({"phase_id": "execute", "items": [{"stable_id": "tool:smoke"}]},),
        {"status": "completed", "ended_at": 2.0}, "root-smoke",
    )
    assert tools[0]["context_visibility"] == "exclude"
    assert "reasoning_content" not in repr(activities)
    assert _public_content("<think>hidden</think>visible")[0] == "visible"
    try:
        ContextFragment(
            fragment_id="activity",
            source="harness",
            role="system",
            content=[{"context_visibility": "exclude"}],
            lifetime="task",
            placement="control",
            priority=1,
            trim_policy="drop",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("activity projection crossed ContextFragment boundary")
    conflict = (
        PublicFactEnvelope("workflow", "terminal:a", "root-smoke", "run_terminal", {"run_id": "root-smoke", "role": "root", "status": "completed"}, 2.0),
        PublicFactEnvelope("workflow", "terminal:b", "root-smoke", "run_terminal", {"run_id": "root-smoke", "role": "root", "status": "failed"}, 3.0),
    )
    aggregate, _, complete, diagnostics = reduce_public_manifest(
        conflict,
        ReadCutV1(ReadSourceCutV1(1.0, 1, True), ReadSourceCutV1(1.0, 1, True)),
        "session-smoke", "root-smoke",
    )
    assert aggregate["status"] == "unknown" and not complete and "terminal_status_conflict" in diagnostics
    print("AGENT_ACTIVITY_TIMELINE_SMOKE_PASS")


if __name__ == "__main__":
    main()
