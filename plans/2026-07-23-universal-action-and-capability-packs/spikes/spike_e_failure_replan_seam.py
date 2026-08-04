"""Disposable spike: inspect current tool-failure and child-failure feedback seams.

This does not implement recovery. It proves that ordinary tool failures already
return through a canonical provider tool result, while current child failures
still return through a host-only system message and therefore need the v0.3
PreparedControlDelegate/FailureReport work.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.execution.contracts import OutcomeStatus  # noqa: E402
from deskpet.harness.drivers.react import ReactCommandBoundary, ReActDriver  # noqa: E402
from deskpet.harness.ports import (  # noqa: E402
    AttachmentPolicy,
    ChildTerminalSignal,
    DelegateRun,
    JoinPolicy,
)
from deskpet.tools.capabilities import ToolExecutionContext  # noqa: E402
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall  # noqa: E402


class FakeUow:
    def __init__(self) -> None:
        self.acked_signals: list[str] = []

    async def ack_child_signal(
        self,
        signal_id,
        *,
        expected_continuation_version,
        continuation_payload,
        event,
        recovery_lease,
    ):
        self.acked_signals.append(str(signal_id))
        saved = SimpleNamespace(version=expected_continuation_version + 1)
        stored_event = SimpleNamespace(run_id="root-run")
        return True, saved, stored_event


def base_boundary(**overrides):
    values = {
        "run_id": "root-run",
        "session_id": "main-session",
        "command_id": "command-1",
        "command_kind": "execute_tools",
        "canonical_messages": ({"role": "user", "content": "完成 Godot 项目"},),
        "session_projection_cursor": 0,
        "prepared_context_ref": "prepared-1",
        "tool_set_snapshot_ref": "tools-1",
        "pending_calls": (),
        "tool_contexts": (),
        "outcomes": (),
        "provider_state": {},
        "iteration": 1,
        "completion_state": {},
        "capability_snapshot": {},
        "version": 4,
    }
    values.update(overrides)
    return ReactCommandBoundary(**values)


async def main() -> None:
    prepared_call = PreparedToolCall.prepare(
        tool_name="run_shell",
        stable_call_id="tool-call-1",
        final_params={"command": "godot --path F:/game"},
        tool_spec_version="1",
        schema_hash="schema-1",
        permission_policy_version="permission-1",
        effect_type="process",
    )
    tool_context = ToolExecutionContext(
        scope_id="scope-1",
        session_id="main-session",
        request_id="request-1",
        root_run_id="root-run",
        turn_id="turn-1",
        run_id="root-run",
        call_id="tool-call-1",
        effect_id="effect-1",
    )
    tool_failure = base_boundary(
        pending_calls=(prepared_call,),
        tool_contexts=(tool_context,),
        outcomes=(
            NormalizedToolOutcome.failure(
                "executable_not_found",
                "godot executable was not found",
            ),
        ),
        outcome_statuses=(OutcomeStatus.FAILED,),
    )
    tool_messages = ReActDriver._tool_messages(tool_failure)

    provider_call_id = "workflow-call-1"
    delegate = DelegateRun(
        run_id="root-run",
        command_id=provider_call_id,
        child_request={"objective": "运行 Godot 项目"},
        route_hint="workflow.durable_task",
        capability_subset=("workflow",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    )
    child_boundary = base_boundary(
        command_id=provider_call_id,
        command_kind="delegate",
        canonical_messages=(
            {"role": "user", "content": "完成 Godot 项目"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": provider_call_id,
                        "type": "function",
                        "function": {
                            "name": "workflow_spawn",
                            "arguments": json.dumps(
                                {
                                    "profile_key": "workflow.durable_task",
                                    "objective": "运行 Godot 项目",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
        ),
        pending_delegate=delegate,
    )
    uow = FakeUow()
    driver = ReActDriver(SimpleNamespace(), uow, SimpleNamespace())
    child_signal = ChildTerminalSignal(
        "root-run",
        provider_call_id,
        "child-run-1",
        "failed",
        value={
            "error_code": "executable_not_found",
            "checkpoint_ref": "project-created",
        },
        signal_id="child-signal-1",
    )
    updated, _event = await driver._apply_child_inbox(
        child_boundary,
        child_signal,
        recovery_lease=None,
    )
    last_child_message = dict(updated.canonical_messages[-1])

    result = {
        "ordinary_tool_failure": {
            "last_role": tool_messages[-1]["role"],
            "tool_call_id": tool_messages[-1]["tool_call_id"],
            "is_provider_canonical_tool_result": (
                tool_messages[-1]["role"] == "tool"
                and tool_messages[-1]["tool_call_id"] == "tool-call-1"
            ),
        },
        "current_child_failure": {
            "last_role": last_child_message.get("role"),
            "uses_host_child_response_system_message": (
                last_child_message.get("role") == "system"
                and "host_child_response" in str(last_child_message.get("content"))
            ),
            "has_provider_tool_call_id": "tool_call_id" in last_child_message,
            "pending_delegate_cleared": updated.pending_delegate is None,
            "signal_acked_once": uow.acked_signals == ["child-signal-1"],
        },
        "conclusion": (
            "Ordinary failure can already re-enter the model through role=tool. "
            "Child failure cannot yet do that: production work must preserve the "
            "original workflow_spawn call and backfill exactly one structured "
            "TaskFailureReport before the parent model can replan."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
