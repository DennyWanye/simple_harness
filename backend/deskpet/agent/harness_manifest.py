# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Declarative contract for the DeskPet agent harness.

This module intentionally contains data, not runtime orchestration.  The
existing harness evolved as product glue across ``main.py``, ``AgentLoop``,
``ContextAssembler`` and the tool registry.  Keeping the request lifecycle and
service wiring contract in one importable place gives tests a stable target and
lets docs/trace views use the same vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Persistence = Literal["ephemeral", "session", "artifact", "config"]


@dataclass(frozen=True)
class HarnessStage:
    """One lifecycle vocabulary entry.

    ``parent`` marks stages that are re-entrant/nested rather than the next
    item in a strict linear sequence.
    """

    id: str
    owner: str
    responsibility: str
    persistence: Persistence
    recovery: str
    observability: tuple[str, ...] = ()
    parent: str | None = None


@dataclass(frozen=True)
class HarnessService:
    """A service_context wiring contract used by the harness."""

    name: str
    owner: str
    responsibility: str
    required_for: tuple[str, ...]


@dataclass(frozen=True)
class HarnessDefectRemediation:
    """Tracks one known harness weakness back to acceptance criteria."""

    defect_id: str
    symptom: str
    remediation: str
    acceptance_ids: tuple[str, ...]


REQUEST_LIFECYCLE: tuple[HarnessStage, ...] = (
    HarnessStage(
        id="ws_ingress",
        owner="backend/main.py::_run_chat",
        responsibility="Receive chat payload, persist user message, resolve session and provider context.",
        persistence="session",
        recovery="SessionDB keeps the conversation record; active coroutine itself is not durable.",
        observability=("chat_v2_user_echo", "session_messages_response"),
    ),
    HarnessStage(
        id="context_assembly",
        owner="backend/deskpet/agent/assembler/assembler.py::ContextAssembler",
        responsibility="Classify task type, fan out persona/memory/skill/tool components, and produce scoped tool schemas.",
        persistence="ephemeral",
        recovery="Assembler exceptions degrade to chat/default policies; decisions ring is diagnostic only.",
        observability=("assembler_task_classified", "ContextBundle.decisions"),
    ),
    HarnessStage(
        id="pre_loop_problem_pipeline",
        owner="backend/deskpet/agent/problem_pipeline.py::ProblemHandlingPipeline",
        responsibility="Classify intent, short-circuit chitchat, request clarification, and inject intent/contradiction hints after context assembly.",
        persistence="ephemeral",
        recovery="Safe-fail returns an empty PreLoopResult and lets the legacy path continue.",
        observability=("chat_v2_intent", "chat_v2_contradiction"),
    ),
    HarnessStage(
        id="agent_loop",
        owner="backend/agent/agent_loop.py::AgentLoop.run",
        responsibility="Drive the ReAct loop: LLM turn, tool calls, tool results, final/error events.",
        persistence="ephemeral",
        recovery="Auto-resume can spawn a new run after selected ErrorEvents, but no exact node checkpoint exists.",
        observability=("AssistantMessageEvent", "ToolCallEvent", "ToolResultEvent", "FinalEvent", "ErrorEvent"),
    ),
    HarnessStage(
        id="tool_dispatch",
        owner="backend/deskpet/tools/registry.py::ToolRegistry.execute_tool",
        responsibility="Gate, execute, time out, envelope, artifact-wrap, receipt-log and classify tool calls.",
        persistence="session",
        recovery="Receipts/artifacts survive the turn; in-flight handler execution is not resumable.",
        observability=("tool_call", "tool_result", "receipt_store"),
        parent="agent_loop",
    ),
    HarnessStage(
        id="completion_gates",
        owner="backend/agent/agent_loop.py::end_turn gates",
        responsibility="Prevent fake completion via completion_probe, VerifyGate, goal_checker, external evaluator and self-check.",
        persistence="session",
        recovery="Gate nudges are per-run; receipt ledger and goal store provide durable evidence.",
        observability=(
            "chat_v2_evidence_gate",
            "chat_v2_selfcheck",
            "chat_v2_convergence",
            "verify_gate_nudge_injected",
            "goal_checker_invoked",
            "external_evaluator",
        ),
        parent="agent_loop",
    ),
    HarnessStage(
        id="subagent_sidecar",
        owner="backend/deskpet/agent/subagent_scheduler.py",
        responsibility="Optional bounded sidecar execution with process-local subagent runs and SQLite-backed team task/message/permission data.",
        persistence="session",
        recovery="SubagentRegistry execution references are process-local; TeamStore data survives, but workers are not automatically reclaimed after restart.",
        observability=("subagent_progress", "subagent_completion"),
        parent="agent_loop",
    ),
    HarnessStage(
        id="ws_egress",
        owner="backend/main.py::AgentEvent -> WebSocket bridge",
        responsibility="Translate harness events to UI events and persist assistant/tool messages.",
        persistence="session",
        recovery="Persisted session messages can be reloaded; streamed deltas are best-effort.",
        observability=("chat_v2_delta", "chat_response", "chat_v2_final", "chat_v2_error"),
        parent="agent_loop",
    ),
)


HARNESS_SERVICES: tuple[HarnessService, ...] = (
    HarnessService(
        name="context_assembler",
        owner="backend/deskpet/agent/assembler",
        responsibility="Pre-loop context and tool schema assembly.",
        required_for=("context_assembly",),
    ),
    HarnessService(
        name="session_goal_store",
        owner="backend/deskpet/agent/goal_store.py",
        responsibility="Durable /goal state for completion checks and goal anchoring.",
        required_for=("completion_gates",),
    ),
    HarnessService(
        name="goal_checker",
        owner="backend/deskpet/agent/goal_checker.py",
        responsibility="LLM judge for active goal completion.",
        required_for=("completion_gates",),
    ),
    HarnessService(
        name="tool_path_recorder",
        owner="backend/deskpet/agent/tool_path.py",
        responsibility="Records successful tool paths for skill self-codification.",
        required_for=("agent_loop", "completion_gates"),
    ),
    HarnessService(
        name="skill_loader",
        owner="backend/deskpet/skills/loader.py",
        responsibility="Loads builtin/user skills and supports skill remount after compaction.",
        required_for=("context_assembly", "agent_loop"),
    ),
    HarnessService(
        name="skill_matcher",
        owner="backend/deskpet/skills/skill_matcher.py",
        responsibility="Semantic skill matching for auto-disclosure/remount.",
        required_for=("context_assembly", "agent_loop"),
    ),
    HarnessService(
        name="subagent_scheduler",
        owner="backend/deskpet/agent/subagent_scheduler.py",
        responsibility="Lane-aware bounded concurrency for child agents.",
        required_for=("subagent_sidecar",),
    ),
    HarnessService(
        name="subagent_registry",
        owner="backend/deskpet/agent/subagent_registry.py",
        responsibility="Non-blocking child run registry, completion queue and cancellation cascade.",
        required_for=("subagent_sidecar", "agent_loop"),
    ),
    HarnessService(
        name="team_store",
        owner="backend/deskpet/agent/team/team_store.py",
        responsibility="Shared task pool/mailbox for spawn_team.",
        required_for=("subagent_sidecar",),
    ),
    HarnessService(
        name="problem_pipeline",
        owner="backend/deskpet/agent/problem_pipeline.py",
        responsibility="Seven-step problem handling pre-loop coordinator.",
        required_for=("pre_loop_problem_pipeline",),
    ),
    HarnessService(
        name="pipeline_evidence_gate",
        owner="backend/deskpet/agent/evidence_gate.py",
        responsibility="Investigation-before-answering in-loop gate.",
        required_for=("completion_gates",),
    ),
    HarnessService(
        name="pipeline_self_check_gate",
        owner="backend/deskpet/agent/self_check_gate.py",
        responsibility="Unified verify/reflection/evaluator self-check gate.",
        required_for=("completion_gates",),
    ),
    HarnessService(
        name="pipeline_convergence_controller",
        owner="backend/deskpet/agent/convergence_controller.py",
        responsibility="Stop-loss and convergence reporting for long loops.",
        required_for=("completion_gates",),
    ),
)


KNOWN_DEFECT_REMEDIATIONS: tuple[HarnessDefectRemediation, ...] = (
    HarnessDefectRemediation(
        defect_id="D1_CONTROL_FLOW_DISPERSED",
        symptom="Request handling is split across main.py, AgentLoop, ContextAssembler, ToolRegistry and gates.",
        remediation="Maintain REQUEST_LIFECYCLE as the single stage vocabulary and test every owner path exists.",
        acceptance_ids=("AC-1", "AC-6"),
    ),
    HarnessDefectRemediation(
        defect_id="D2_NOT_DURABLE_STATE_MACHINE",
        symptom="Long tasks can auto-resume but cannot restart from an exact graph node after process death.",
        remediation="Document persistence/recovery per stage and reserve durable checkpoints for later long-task graphs.",
        acceptance_ids=("AC-2", "AC-8"),
    ),
    HarnessDefectRemediation(
        defect_id="D3_MAIN_GLUE_TOO_THICK",
        symptom="main.py owns provider resolution, service construction, pipeline injection and WS event bridging.",
        remediation="Track harness services in HARNESS_SERVICES and plan extraction by ownership without changing behavior.",
        acceptance_ids=("AC-3", "AC-4"),
    ),
    HarnessDefectRemediation(
        defect_id="D4_FLAG_SERVICE_COMPLEXITY",
        symptom="Feature flags and service_context placeholders can drift from runtime wiring.",
        remediation="Assert every manifest service is accepted by ServiceContext and has an owner/responsibility.",
        acceptance_ids=("AC-4",),
    ),
    HarnessDefectRemediation(
        defect_id="D5_MULTI_AGENT_ADD_ON",
        symptom="Subagents/teams are sidecar tools around one parent AgentLoop; execution references are process-local even where team task data is durable.",
        remediation="Name subagent_sidecar explicitly and distinguish process-local workers from SQLite-backed team data and missing crash reclaim.",
        acceptance_ids=("AC-5",),
    ),
    HarnessDefectRemediation(
        defect_id="D6_TRACE_NOT_STRUCTURED",
        symptom="Operational diagnosis often relies on grep-log anchors rather than one stage taxonomy.",
        remediation="Use lifecycle stage ids as canonical trace vocabulary and enforce unique ordered stage ids.",
        acceptance_ids=("AC-6",),
    ),
    HarnessDefectRemediation(
        defect_id="D7_TEST_COST_HIGH",
        symptom="Harness changes can require expensive UI E2E if routing is unclear.",
        remediation="Document routed harness test strategy: manifest/logic via pytest, UI-affecting paths via windows-mcp.",
        acceptance_ids=("AC-7",),
    ),
    HarnessDefectRemediation(
        defect_id="D8_LOW_FRAMEWORK_REUSE",
        symptom="The harness is highly DeskPet/Tauri/tooling-specific and not a reusable framework.",
        remediation="Keep product-specific runtime; borrow LangGraph/CrewAI patterns only at state/flow/team boundaries.",
        acceptance_ids=("AC-8",),
    ),
)


def lifecycle_stage_ids() -> tuple[str, ...]:
    return tuple(stage.id for stage in REQUEST_LIFECYCLE)


def harness_service_names() -> tuple[str, ...]:
    return tuple(service.name for service in HARNESS_SERVICES)


def acceptance_coverage() -> dict[str, tuple[str, ...]]:
    return {
        item.defect_id: item.acceptance_ids
        for item in KNOWN_DEFECT_REMEDIATIONS
    }


__all__ = [
    "HARNESS_SERVICES",
    "KNOWN_DEFECT_REMEDIATIONS",
    "REQUEST_LIFECYCLE",
    "HarnessDefectRemediation",
    "HarnessService",
    "HarnessStage",
    "acceptance_coverage",
    "harness_service_names",
    "lifecycle_stage_ids",
]
