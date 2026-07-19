from __future__ import annotations

import pytest

from deskpet.workflows.errors import InvalidStatePatch
from deskpet.workflows.native import NativeWorkflowExecutable


def _terminal_public() -> dict[str, object]:
    return {
        "metrics": {
            "actual_requests": 3,
            "hits": 0,
            "empty": 2,
            "timeouts": 1,
            "cooldown_skips": 4,
            "busy_skips": 1,
            "queue_timeouts": 0,
            "probes": 1,
            "rescue_considered_count": 1,
            "rescue_executed_count": 1,
            "candidates": 0,
        },
        "diagnostic_codes": ["no_results"],
        "skipped_stage_ids": ["fetch", "score", "gap", "rerank", "synth", "cite", "persist"],
        "retry_action_id": "retry_from_start",
    }


def test_failed_v4_terminal_projects_only_strict_public_fields() -> None:
    intents = NativeWorkflowExecutable._terminal_intents(
        {
            "workflow_name": "deep_research",
            "workflow_version": "v4",
            "values": {
                "terminal_public": _terminal_public(),
                "delivery_intents": [],
                "topic": "secret topic must not project",
            }
        },
        run_id="run-v4",
        status="failed",
        error={"code": "deep_research_no_results", "message": "No results"},
        recovery_action="retry_from_start",
    )
    assert [intent["event_type"] for intent in intents] == ["workflow.final"]
    payload = intents[0]["payload"]
    assert payload["metrics"]["actual_requests"] == 3
    assert payload["retry_action_id"] == "retry_from_start"
    assert payload["card"]["skipped_stage_ids"][-1] == "persist"
    assert "topic" not in payload


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"raw_query": "secret"}),
        lambda value: value["metrics"].update({"urls": 1}),
        lambda value: value["metrics"].update({"hits": -1}),
        lambda value: value.update({"diagnostic_codes": ["secret_query"]}),
        lambda value: value.update({"skipped_stage_ids": ["fetch", "fetch"]}),
        lambda value: value.update({"retry_action_id": "retry_with_query"}),
    ),
)
def test_terminal_public_rejects_unknown_or_unbounded_state(mutate) -> None:
    public = _terminal_public()
    mutate(public)
    with pytest.raises(InvalidStatePatch, match="terminal"):
        NativeWorkflowExecutable._terminal_intents(
            {
                "workflow_name": "deep_research",
                "workflow_version": "v4",
                "values": {"terminal_public": public},
            },
            run_id="run-v4",
            status="failed",
            error=None,
            recovery_action=None,
        )


def test_legacy_terminal_without_public_projection_is_byte_shape_compatible() -> None:
    intents = NativeWorkflowExecutable._terminal_intents(
        {"values": {"delivery_intents": []}},
        run_id="run-v3",
        status="failed",
        error={"code": "legacy", "message": "legacy"},
        recovery_action="retry",
    )
    assert set(intents[0]["payload"]) == {"kind", "status", "error", "recovery_action", "card"}
