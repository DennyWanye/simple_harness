# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""NEXT-TG-1.0 §9: the tool circuit breaker and auto-resume work with the supervisor Agent off.

The user switched the supervisor Agent off; self-healing used to be assembled only
inside ``if [supervisor].enabled`` and silently disappeared with it.  Now both are
wired outside that branch, and without the supervisor the resume hint is a fixed,
model-free text per failure reason.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from agent.auto_resume import AutoResumeOrchestrator, DeterministicResumePolicy, is_auto_resume_trigger
from agent.session_activity import SessionActivityStore

MAIN = Path(__file__).parents[1] / "main.py"


def _supervisor_branch() -> ast.If:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "_sup_cfg.get(\"enabled\"" in ast.unparse(node.test).replace("'", '"'):
            return node
    raise AssertionError("supervisor branch not found")


def test_breaker_and_auto_resume_are_not_under_the_supervisor_switch():
    branch = ast.unparse(_supervisor_branch())
    assert "ToolCircuitBreaker" not in branch and "AutoResumeOrchestrator" not in branch
    source = MAIN.read_text(encoding="utf-8")
    assert "ToolCircuitBreaker" in source and "AutoResumeOrchestrator" in source
    assert 'service_context.get("supervisor") or _DetPolicy()' in source


def test_session_activity_is_registered_regardless_of_the_supervisor_switch():
    """Auto-resume counts attempts per session in the real activity store; it must exist."""
    source = MAIN.read_text(encoding="utf-8")
    branch = ast.unparse(_supervisor_branch())
    assert 'service_context.register("session_activity"' not in branch
    registered = source.index('service_context.register("session_activity"')
    assert registered < source.index("activity_store=service_context.get(\"session_activity\")")


def test_deterministic_policy_resumes_known_failures_without_a_model():
    async def case():
        policy = DeterministicResumePolicy()
        for reason in ("max_iterations", "circuit_open", "permanent_tool_error", "hallucination",
                       "verify_exhausted", "evaluator_revise"):
            assert is_auto_resume_trigger(reason)
            action = await policy.diagnose("s", {"reason": reason})
            assert action.action == "nudge" and action.hint_for_main_agent
        assert (await policy.diagnose("s", {"reason": "something_else"})).action == "ask_user"

        dispatched = []

        async def dispatch(sid, msgs):
            dispatched.append(msgs[-1]["content"])

        orchestrator = AutoResumeOrchestrator(supervisor=policy, chat_dispatcher=dispatch,
                                              activity_store=SessionActivityStore(), max_attempts=2)
        first = await orchestrator.handle_failure("s", "circuit_open", {"reason": "circuit_open"}, [])
        assert first.action == "spawned" and "不要再调用同一个工具" in dispatched[0]
        await orchestrator.handle_failure("s", "circuit_open", {"reason": "circuit_open"}, [])
        third = await orchestrator.handle_failure("s", "circuit_open", {"reason": "circuit_open"}, [])
        assert third.action == "exhausted" and len(dispatched) == 2  # bounded by max_attempts

    asyncio.run(case())
