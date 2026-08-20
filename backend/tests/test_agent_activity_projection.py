from __future__ import annotations

import pytest

from deskpet.agent.assembler.bundle import ContextFragment
from deskpet.execution.harness_public_read_service import _public_content
from deskpet.execution.harness_public_read_service import _public_views
from deskpet.execution.run_read_model import (
    PublicFactEnvelope,
    ReadCutV1,
    ReadSourceCutV1,
)
from deskpet.execution.semantic_projection import reduce_public_manifest


def _cut() -> ReadCutV1:
    return ReadCutV1(
        workflow=ReadSourceCutV1(1.0, 1, True),
        state=ReadSourceCutV1(1.0, 1, True),
    )


def _fact(stable_id: str, status: str, *, created_at: float) -> PublicFactEnvelope:
    return PublicFactEnvelope(
        source="workflow",
        stable_id=stable_id,
        root_run_id="root-1",
        kind="run_terminal" if status != "running" else "execution_run",
        public_payload={
            "run_id": "root-1",
            "role": "root",
            "status": status,
        },
        created_at=created_at,
    )


def test_public_fact_is_display_only_and_reasoning_is_removed() -> None:
    with pytest.raises(ValueError):
        PublicFactEnvelope(
            source="workflow",
            stable_id="run:1",
            root_run_id="root-1",
            kind="run",
            public_payload={},
            created_at=1.0,
            context_visibility="conversation",
        )

    text, digest = _public_content("<think>secret chain</think>Answer")
    assert text == "Answer"
    assert digest is None


def test_terminal_status_conflict_fails_closed() -> None:
    aggregate, phases, complete, diagnostics = reduce_public_manifest(
        (
            _fact("run-terminal:a", "completed", created_at=2.0),
            _fact("run-terminal:b", "failed", created_at=3.0),
        ),
        _cut(),
        "session-1",
        "root-1",
    )
    assert aggregate["status"] == "unknown"
    assert aggregate["explanation_code"] == "terminal_status_conflict"
    assert aggregate["evidence_refs"] == ["run-terminal:a", "run-terminal:b"]
    assert complete is False
    assert "terminal_status_conflict" in diagnostics
    assert all(phase["status"] == "unknown" for phase in phases)


def test_context_fragment_rejects_activity_projection() -> None:
    with pytest.raises(ValueError, match="display-only"):
        ContextFragment(
            fragment_id="activity",
            source="harness",
            role="system",
            content=[{"context_visibility": "exclude", "title": "执行工具"}],
            lifetime="task",
            placement="control",
            priority=1,
            trim_policy="drop",
        )


def test_eager_public_views_are_bounded_and_excluded() -> None:
    fact = PublicFactEnvelope(
        source="workflow",
        stable_id="tool:1",
        root_run_id="root-1",
        kind="tool",
        public_payload={
            "tool_name": "读取文件",
            "action_code": "inspect",
            "status": "completed",
            "safe_target_label": "README.md",
            "safe_input": {"path": "README.md"},
            "bounded_result": {"summary": "ok"},
            "reasoning_content": "PRIVATE_REASONING",
            "content": "<think>PRIVATE_THINK</think>公开进度",
        },
        created_at=2.0,
    )
    activities, tools, messages = _public_views(
        (fact,),
        ({"phase_id": "execute", "items": [{"stable_id": "tool:1"}]},),
        {"status": "completed", "ended_at": 3.0},
        "root-1",
    )
    assert tools[0]["public_input"] == {"path": "README.md"}
    assert tools[0]["public_result"] == {"summary": "ok"}
    assert tools[0]["context_visibility"] == "exclude"
    assert activities[-1].context_visibility == "exclude"
    assert "PRIVATE_REASONING" not in repr(activities)
    assert "PRIVATE_THINK" not in repr(activities)
    assert "公开进度" in repr(activities)
    assert messages == ()


def test_eager_public_views_dedupe_and_bound_details() -> None:
    fact = PublicFactEnvelope(
        source="workflow",
        stable_id="tool:duplicate",
        root_run_id="root-1",
        kind="tool",
        public_payload={
            "tool_name": "检查",
            "safe_input": {"path": "x" * 5000, "token": "sk-secret"},
            "bounded_result": {"summary": "ok"},
        },
        created_at=2.0,
    )
    activities, tools, _ = _public_views(
        (fact, fact),
        ({},),
        {"status": "completed", "ended_at": 3.0},
        "root-1",
    )
    assert len([item for item in activities if item.stable_id == "tool:duplicate"]) == 1
    assert tools[0]["truncated"] is True
    assert "sk-secret" not in repr(tools)
