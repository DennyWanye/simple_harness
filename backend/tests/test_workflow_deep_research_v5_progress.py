from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import replace

import pytest

from deskpet.workflows.contracts import NodeExecutionIdentity, WorkflowContext
from deskpet.workflows.definitions.deep_research_v5_nodes import normalize_handler
from deskpet.workflows.definitions.deep_research_v5_progress import (
    build_v5_stage_projection,
)
from deskpet.workflows.progress import (
    _V5_PROJECTION_KEYS,
    ProgressPayloadError,
    WorkflowProgressReporter,
    validated_v2_stage_text,
)
from deskpet.workflows.definitions.v5 import DEEP_RESEARCH_V5, deep_research_initial_state
from deskpet.workflows.native import _uses_deep_research_stage_contract


def _coverage(
    dimension_id: str,
    status: str,
    *,
    first_party: bool = False,
    families: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "dimension_id": dimension_id,
        "status": status,
        "evidence_passage_ids": [],
        "winning_evidence_ids": ([f"evidence-{dimension_id}"] if status == "covered" else []),
        "source_family_ids": families or [],
        "first_party_satisfied": first_party,
        "relevance_score": 0.8,
        "gap_reasons": ["raw-gap-reason-must-not-leak"],
    }


def _state() -> dict:
    return {
        "values": {
            "research_brief": {
                "dimensions": [
                    {"dimension_id": "core-a", "importance": "core", "question": "secret question"},
                    {"dimension_id": "core-b", "importance": "core", "question": "secret question"},
                    {"dimension_id": "support-c", "importance": "supporting", "question": "secret question"},
                ]
            },
            "dimension_coverages": [
                _coverage("core-a", "covered", first_party=True, families=["family-secret-a"]),
                _coverage(
                    "core-b", "partially_covered",
                    families=["family-secret-a", "family-secret-b"],
                ),
                _coverage("support-c", "uncovered"),
            ],
            "source_families": [
                {
                    "family_id": "family-secret-a",
                    "page_quality": "valid",
                    "source_tier": "first_party",
                    "canonical_url": "https://secret.example/a",
                },
                {
                    "family_id": "family-secret-b",
                    "page_quality": "valid",
                    "source_tier": "secondary",
                    "canonical_url": "https://secret.example/b",
                },
            ],
            "active_gap_work": {
                "dimension_id": "core-b",
                "work_kind": "query",
                "status": "running",
                "query": "secret raw query",
                "reason": "secret raw reason",
            },
            "loop_policy": {
                "accumulated_active_seconds": 321.9,
                "soft_checkpoint_seconds": 300.0,
                "current_progress": {"quality_score": 77.4},
            },
            "loop_decision": {
                "reason": "lease_renewed_measurable_gain",
                "raw_error": "secret raw error",
            },
            "quality_audit_result": {
                "audit": {
                    "total_score": 82.8,
                    "hard_failures": ["unsupported_key_claim", "secret_failure"],
                    "prompt": "secret prompt",
                }
            },
            "budget_ledger": {
                "committed_input_tokens": 300,
                "committed_output_tokens": 100,
                "max_input_tokens": 700,
                "max_output_tokens": 300,
                "token": "secret token",
            },
            "control_command": {
                "action": "generate_now",
                "status": "observed",
                "payload": {"prompt": "secret command prompt"},
            },
            "operation_lineage": {"parent_operation_id": "secret-parent-operation"},
            "gap_evaluate_route": "gap_work",
            "rejection_reason_counts": {
                "dimension_relevance_below_threshold": 6,
                "body_too_short": 2,
                "raw-secret-rejection": 9,
            },
        }
    }


def _identity(**updates) -> NodeExecutionIdentity:
    values = {
        "workflow_name": "deep_research",
        "workflow_version": "v5",
        "thread_id": "thread-secret",
        "run_id": "run-1",
        "checkpoint_id": "checkpoint-secret",
        "checkpoint_ns": "namespace-secret",
        "task_id": "task-secret",
        "node_id": "gap_evaluate",
        "attempt": 1,
        "activation_id": "activation-gap-loop-2-work-core-b",
        "invocation_key": "gap_evaluate:loop=2:item=core-b",
    }
    values.update(updates)
    return NodeExecutionIdentity(**values)


class _Outbox:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], dict] = {}

    async def ensure_event(self, **kwargs):
        key = (kwargs["run_id"], kwargs["event_key"])
        if key in self.events:
            assert self.events[key]["payload"] == kwargs["payload"]
            return self.events[key]
        event = {**kwargs, "event_id": f"event-{len(self.events) + 1}"}
        self.events[key] = event
        return event


class _Service:
    def __init__(self) -> None:
        self.outbox = _Outbox()
        self.delivered: list[str] = []

    async def deliver_event_once(self, event_id: str) -> None:
        self.delivered.append(event_id)


def test_v5_projection_is_complete_count_safe_and_never_copies_sensitive_state() -> None:
    previous = _state()
    previous["values"]["dimension_coverages"][0]["status"] = "partially_covered"
    projection = build_v5_stage_projection(
        _state(), stage_id="gap_evaluate", previous_state=previous
    )

    assert set(projection) == {
        "stage_id", "public_stage_id", "action", "result", "discarded",
        "remaining_gap", "next_step", "dimension_counts",
        "dimension_status_changes", "source_counts", "active_gap",
        "elapsed_seconds", "soft_checkpoint", "lease_reason", "quality_score",
        "hard_failures", "predicted_delivery", "token_budget_ratio",
        "control_action", "control_status", "parent_operation",
        "failed_dimensions", "rejection_reasons",
    }
    assert projection["dimension_counts"] == {
        "total": 3, "core_total": 2, "covered": 1, "partially_covered": 1,
        "uncovered": 1, "not_applicable": 0,
        "core_covered": 1, "core_partially_covered": 1, "core_uncovered": 0,
    }
    assert projection["dimension_status_changes"] == {
        "improved": 1, "regressed": 0, "unchanged": 2,
    }
    assert projection["source_counts"] == {"valid": 2, "first_party": 1}
    assert projection["active_gap"] == {
        "status": "running", "work_kind": "query", "dimension_ordinal": 2,
    }
    assert projection["elapsed_seconds"] == 321
    assert projection["soft_checkpoint"] == "reached"
    assert projection["quality_score"] == 82
    assert projection["hard_failures"] == ["unsupported_key_claim"]
    assert projection["token_budget_ratio"] == 40
    assert projection["control_action"] == "generate_now"
    assert projection["control_status"] == "observed"
    assert projection["parent_operation"].startswith("op_")
    assert projection["failed_dimensions"] == [
        {
            "dimension_ordinal": 1,
            "status": "partially_covered",
            "reason_codes": ["evidence_gap"],
        },
        {
            "dimension_ordinal": 2,
            "status": "uncovered",
            "reason_codes": ["evidence_gap"],
        },
    ]
    assert projection["rejection_reasons"] == [
        {"reason_code": "other_rejected", "count": 9},
        {"reason_code": "dimension_relevance_below_threshold", "count": 6},
        {"reason_code": "body_too_short", "count": 2},
    ]
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    for secret in (
        "secret question", "family-secret", "secret raw query", "secret raw reason",
        "secret raw error", "secret prompt", "secret_failure", "secret token",
        "secret command prompt", "secret-parent-operation", "core-b",
    ):
        assert secret not in serialized


def test_v5_completion_payload_is_strict_safe_and_visible_for_user_control() -> None:
    reporter = WorkflowProgressReporter(object(), (("session_message", "session-secret"),))
    projection = build_v5_stage_projection(_state(), stage_id="gap_evaluate")
    intent = reporter.build_completion_intent(_identity(), projection)

    assert intent is not None
    payload = intent["payload"]
    assert payload["schema_version"] == 5
    assert payload["capability"] == "deep_research_progress_v5"
    assert payload["visibility"] == "visible"
    assert payload["action"] == "evaluate_gaps"
    assert payload["result"] == "completed"
    assert payload["discarded"] == "none"
    assert payload["remaining_gap"] == 1
    assert payload["next_step"] == "research_gap"
    assert validated_v2_stage_text(payload) == payload["text"]
    assert "activation-gap" not in json.dumps(payload, ensure_ascii=False)


def test_v5_normal_completion_is_hidden_and_terminal_completion_is_visible() -> None:
    reporter = WorkflowProgressReporter(object(), ())
    normal_state = _state()
    normal_state["values"]["control_command"] = None
    normal_state["values"].pop("research_brief")
    normal = reporter.build_completion_intent(
        _identity(node_id="score"),
        build_v5_stage_projection(normal_state, stage_id="score"),
    )
    terminal_state = _state()
    terminal_state["values"]["control_command"] = None
    terminal = reporter.build_completion_intent(
        _identity(
            node_id="finalize",
            activation_id="activation-finalize",
            invocation_key="finalize:activation-finalize:0",
        ),
        build_v5_stage_projection(terminal_state, stage_id="finalize"),
    )
    assert normal is not None and terminal is not None
    assert normal["payload"]["visibility"] == "hidden"
    assert terminal["payload"]["visibility"] == "visible"


def test_supporting_coverage_never_masks_an_uncovered_core_dimension() -> None:
    state = _state()
    state["values"]["dimension_coverages"] = [
        _coverage("core-a", "uncovered"),
        _coverage("core-b", "uncovered"),
        _coverage("support-c", "covered", families=["family-secret-b"]),
    ]
    projection = build_v5_stage_projection(state, stage_id="score")
    assert projection["dimension_counts"]["covered"] == 1
    assert projection["dimension_counts"]["core_covered"] == 0
    assert projection["remaining_gap"] == 2
    assert projection["predicted_delivery"] == "insufficient_evidence"


def test_first_party_source_family_is_deduped_across_dimensions_and_not_inferred_without_records() -> None:
    state = _state()
    projection = build_v5_stage_projection(state, stage_id="score")
    assert projection["source_counts"] == {"valid": 2, "first_party": 1}

    del state["values"]["source_families"]
    fallback = build_v5_stage_projection(state, stage_id="score")
    assert fallback["source_counts"] == {"valid": 2, "first_party": 0}


def test_v5_completion_replay_is_stable_but_child_activations_are_distinct() -> None:
    reporter = WorkflowProgressReporter(object(), ())
    projection = build_v5_stage_projection(_state(), stage_id="gap_evaluate")
    identity = _identity()
    retry = replace(identity, attempt=9, checkpoint_id="checkpoint-after-restart")
    child = replace(
        identity,
        activation_id="activation-gap-loop-3-work-core-b",
        invocation_key="gap_evaluate:loop=3:item=core-b",
    )

    first = reporter.build_completion_intent(identity, projection)
    replay = reporter.build_completion_intent(retry, copy.deepcopy(projection))
    next_child = reporter.build_completion_intent(child, projection)
    assert first is not None and replay is not None and next_child is not None
    assert first["event_key"] == replay["event_key"]
    assert first["payload"] == replay["payload"]
    assert first["event_key"] != next_child["event_key"]
    assert first["payload"]["stage_instance_id"] != next_child["payload"]["stage_instance_id"]


def test_v5_completion_fails_closed_without_native_activation_identity_or_on_extra_field() -> None:
    reporter = WorkflowProgressReporter(object(), ())
    projection = build_v5_stage_projection(_state(), stage_id="gap_evaluate")
    assert reporter.build_completion_intent(
        replace(_identity(), activation_id=None), projection
    ) is None
    unsafe = {**projection, "query": "secret raw query"}
    assert reporter.build_completion_intent(_identity(), unsafe) is None


@pytest.mark.asyncio
async def test_v5_lifecycle_events_are_idempotent_and_visibility_does_not_regress() -> None:
    service = _Service()
    reporter = WorkflowProgressReporter(service, (("websocket", "session-1"),))
    identity = _identity(node_id="gap_work")

    started = await reporter.report(identity, "started")
    duplicate = await reporter.report(identity, "started")
    waiting = await reporter.report(identity, "waiting")
    failed = await reporter.report(identity, "failed")

    assert started == duplicate == "event-1"
    assert waiting == "event-2"
    assert failed == "event-3"
    payloads = [event["payload"] for event in service.outbox.events.values()]
    assert [payload["visibility"] for payload in payloads] == ["hidden", "visible", "visible"]
    assert all(validated_v2_stage_text(payload) == payload["text"] for payload in payloads)


def test_v5_projection_property_does_not_echo_arbitrary_raw_strings() -> None:
    rng = random.Random(20260716)
    for index in range(100):
        marker = "raw-sensitive-" + "".join(rng.choice("abcdef0123456789") for _ in range(24))
        state = _state()
        state["values"][f"unknown_{index}"] = {
            "query": marker,
            "url": marker,
            "content": marker,
            "prompt": marker,
            "raw_error": marker,
        }
        projection = build_v5_stage_projection(state, stage_id="score")
        assert marker not in json.dumps(projection, ensure_ascii=False, sort_keys=True)


def test_node_execution_identity_native_fields_are_additive_for_legacy_callers() -> None:
    legacy = NodeExecutionIdentity(
        "deep_research", "v4", "thread", "run", "checkpoint", "", "task", "search", 1
    )
    assert legacy.activation_id is None
    assert legacy.invocation_key is None

    class Info:
        thread_id = "thread"
        run_id = "run"
        checkpoint_id = "checkpoint"
        checkpoint_ns = ""
        task_id = "task"
        node_attempt = 1
        node_first_attempt_time = 1.0
        activation_id = "activation"
        invocation_key = "search:activation:0"

    native = NodeExecutionIdentity.from_execution_info(
        workflow_name="deep_research",
        workflow_version="v5",
        node_id="search",
        execution_info=Info(),
    )
    assert native.activation_id == "activation"
    assert native.invocation_key == "search:activation:0"


def test_historical_v4_completion_payload_bytes_remain_locked() -> None:
    reporter = WorkflowProgressReporter(object(), ())
    identity = NodeExecutionIdentity(
        "deep_research", "v4", "thread", "run", "checkpoint", "", "task-search",
        "search_join", 1,
    )
    projection = {
        "stage_id": "search",
        "metrics": {"providers": 2, "candidates": 8, "kept": 5},
        "duration_ms": 1250,
        "completed_count": 4,
        "degraded": False,
        "next_stage": "direct",
    }
    intent = reporter.build_completion_intent(identity, projection)
    assert intent is not None
    encoded = json.dumps(intent["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == (
        "79973789cad4d7eed59bd9d019b6733bcffa226191ebc653c0d09e45ce366ac8"
    )


def test_v5_delivery_validator_rejects_non_allowlisted_string_even_without_extra_keys() -> None:
    reporter = WorkflowProgressReporter(object(), ())
    projection = build_v5_stage_projection(_state(), stage_id="gap_evaluate")
    intent = reporter.build_completion_intent(_identity(), projection)
    assert intent is not None
    malicious = copy.deepcopy(intent["payload"])
    malicious["lease_reason"] = "raw provider error"
    with pytest.raises(ProgressPayloadError, match="workflow_progress_v5_lease"):
        validated_v2_stage_text(malicious)


def test_v5_control_projection_is_owned_by_committed_brief_and_terminal_matrix() -> None:
    state = _state()
    state["values"].pop("control_command")
    running = build_v5_stage_projection(state, stage_id="model")
    assert (running["control_action"], running["control_status"]) == (
        "generate_now", "open"
    )
    state["values"]["terminal_public"] = {
        "delivery_status": "partial",
        "action_matrix": [{"action_id": "continue_research", "enabled": True}],
    }
    terminal = build_v5_stage_projection(state, stage_id="finalize")
    assert (terminal["control_action"], terminal["control_status"]) == (
        "continue_research", "open"
    )


@pytest.mark.asyncio
async def test_v5_node_commit_attaches_safe_projection_and_native_contract_is_enabled() -> None:
    class Clock:
        def wall_time(self):
            return "2026-07-16T00:00:00+00:00"

    state = deep_research_initial_state(topic="test", run_id="run-v5-progress")
    patch = await normalize_handler(state, WorkflowContext(ports={"clock": Clock()}))
    projection = patch.to_dict()["values"]["public_progress"]["stage_projection"]
    assert set(projection) == _V5_PROJECTION_KEYS
    assert projection["stage_id"] == "normalize"
    assert _uses_deep_research_stage_contract(DEEP_RESEARCH_V5.manifest) is True
