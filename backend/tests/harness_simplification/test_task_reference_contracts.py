from __future__ import annotations

import pytest

from deskpet.agent.task_reference import TaskReferenceResolver
from deskpet.types.task_reference import (
    TaskReferenceCandidate,
    TaskReferenceKind,
    TaskReferenceStatus,
)


def _rows():
    return [
        {
            "id": 1,
            "role": "user",
            "content": "创建 Godot GemCollector 游戏",
            "root_run_id": "game-root",
            "task_scope_id": "task-game",
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "GemCollector/project.godot 已完成",
            "root_run_id": "game-root",
            "task_scope_id": "task-game",
        },
        {
            "id": 3,
            "role": "assistant",
            "content": "季度预算表已经完成",
            "root_run_id": "budget-root",
            "task_scope_id": "task-budget",
        },
    ]


def test_named_reference_resolves_one_stable_run_identity() -> None:
    resolver = TaskReferenceResolver()
    intent = resolver.intent("请继续打开之前的 Godot GemCollector 游戏")
    candidates = resolver.candidates(_rows())
    result = resolver.resolve(intent, candidates)

    assert intent is not None
    assert intent.kind is TaskReferenceKind.RECENT
    assert result.status is TaskReferenceStatus.RESOLVED
    assert result.selected_reference_id == "run:game-root"


def test_unqualified_demonstrative_is_ambiguous_not_latest_row_guess() -> None:
    resolver = TaskReferenceResolver()
    intent = resolver.intent("打开这个")
    result = resolver.resolve(intent, resolver.candidates(_rows()))

    assert result.status is TaskReferenceStatus.AMBIGUOUS
    assert result.selected_reference_id is None
    assert {item.reference_id for item in result.candidates} == {
        "run:game-root",
        "run:budget-root",
    }


def test_recent_reference_resolves_most_recent_typed_candidate() -> None:
    resolver = TaskReferenceResolver()
    intent = resolver.intent("把刚才的再打开")
    result = resolver.resolve(intent, resolver.candidates(_rows()))

    assert result.status is TaskReferenceStatus.RESOLVED
    assert result.selected_reference_id == "run:budget-root"


def test_recent_reference_with_unknown_name_does_not_fall_back_to_latest() -> None:
    resolver = TaskReferenceResolver()
    intent = resolver.intent("继续上次的火星基地")
    result = resolver.resolve(intent, resolver.candidates(_rows()))

    assert result.status is TaskReferenceStatus.MISSING
    assert result.selected_reference_id is None


def test_single_candidate_unknown_name_is_missing_not_auto_resolved() -> None:
    resolver = TaskReferenceResolver()
    candidates = resolver.candidates([_rows()[-1]])
    intent = resolver.intent("继续上次的火星基地")
    result = resolver.resolve(intent, candidates)

    assert result.status is TaskReferenceStatus.MISSING
    assert result.selected_reference_id is None


def test_rows_without_durable_task_identity_are_never_candidates() -> None:
    resolver = TaskReferenceResolver()
    candidates = resolver.candidates(
        [{"id": 1, "role": "assistant", "content": "unowned"}]
    )
    result = resolver.resolve(resolver.intent("打开这个"), candidates)

    assert candidates == ()
    assert result.status is TaskReferenceStatus.MISSING


def test_candidate_contract_rejects_unowned_identity() -> None:
    with pytest.raises(ValueError, match="root_run_id or task_scope_id"):
        TaskReferenceCandidate(
            reference_id="run:missing",
            root_run_id=None,
            task_scope_id=None,
            row_ids=(),
            preview="missing",
            match_tokens=(),
            recency=0,
        )


def test_english_marker_requires_a_word_boundary() -> None:
    resolver = TaskReferenceResolver()
    assert resolver.intent("work without changing the budget") is None
