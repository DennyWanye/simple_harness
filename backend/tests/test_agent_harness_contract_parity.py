# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import inspect

from agent.agent_loop import AgentLoop, PipelineEvent
from deskpet.agent.harness_manifest import REQUEST_LIFECYCLE


FACTORY_TO_LOOP_KWARGS = {
    "max_iterations",
    "completion_probe",
    "code_todo_getter",
    "max_completion_nudges",
    "signature_repeat_threshold",
    "session_goal_store",
    "goal_checker",
    "compressor",
    "skill_loader",
    "skill_matcher",
    "tool_path_recorder",
    "memory_curator",
    "evidence_gate",
    "pipeline_problem_type",
    "pipeline_needs_investigation",
    "pipeline_observability",
    "convergence_report_on_stop",
}

LOOP_ONLY_HARNESS_KWARGS = {
    "verify_gate",
    "receipt_store",
    "max_verify_nudges",
    "structured_reflection",
    "external_evaluator",
    "ctx_observability",
    "file_memory",
    "force_finish_via_tool_choice",
    "tracer",
    "subagent_registry",
    "self_check_gate",
}


def test_build_agent_and_agent_loop_share_explicit_harness_kwargs() -> None:
    from main import build_agent

    build_params = set(inspect.signature(build_agent).parameters)
    loop_params = set(inspect.signature(AgentLoop.__init__).parameters)

    missing_from_factory = FACTORY_TO_LOOP_KWARGS - build_params
    missing_from_loop = FACTORY_TO_LOOP_KWARGS - loop_params

    assert not missing_from_factory
    assert not missing_from_loop


def test_loop_only_harness_kwargs_remain_constructor_visible() -> None:
    loop_params = set(inspect.signature(AgentLoop.__init__).parameters)

    missing = LOOP_ONLY_HARNESS_KWARGS - loop_params

    assert not missing


def test_pipeline_event_contract_matches_ws_bridge_shape() -> None:
    event = PipelineEvent(
        type="chat_v2_evidence_gate",
        task_id="task-1",
        iteration=2,
        payload={"blocked": True, "reason": "no_evidence", "nudge_count": 1},
    )

    ws_payload = {"type": event.type, "payload": {"session_id": "sid-1", **event.payload}}

    assert ws_payload == {
        "type": "chat_v2_evidence_gate",
        "payload": {
            "session_id": "sid-1",
            "blocked": True,
            "reason": "no_evidence",
            "nudge_count": 1,
        },
    }


def test_manifest_observability_mentions_runtime_event_types() -> None:
    by_id = {stage.id: stage for stage in REQUEST_LIFECYCLE}

    assert "AssistantMessageEvent" in by_id["agent_loop"].observability
    assert "ToolResultEvent" in by_id["agent_loop"].observability
    assert "chat_v2_evidence_gate" in by_id["completion_gates"].observability
    assert "subagent_completion" in by_id["subagent_sidecar"].observability
