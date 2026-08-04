"""Disposable spike: prove the live ReAct collaborator can re-read a new tool set.

This does not implement production refresh.  It only tests the current seam and
prints the durable-state gap that the implementation plan must close.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.harness.drivers.react import (  # noqa: E402
    AgentLoopCollaborator,
    ReactCommandBoundary,
)
from deskpet.harness.live_index import BoundedLiveIndex  # noqa: E402
from deskpet.harness.ports import DriverStart  # noqa: E402


class RecordingLoop:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, messages: list[dict[str, Any]], **kwargs: Any):
        self.calls.append(
            {
                "messages": messages,
                "tool_names_filter": list(kwargs.get("tool_names_filter") or ()),
                "prepared_context": kwargs.get("prepared_context"),
                "task_id": kwargs.get("task_id"),
            }
        )

        async def empty():
            if False:
                yield None

        return empty()


async def drain(iterator) -> None:
    async for _ in iterator:
        pass


async def main() -> None:
    run_id = "spike-refresh-run"
    session_id = "spike-session"
    loop = RecordingLoop()
    live = BoundedLiveIndex(max_runs=2)
    active = live.add(run_id, SimpleNamespace(session_id=session_id))
    collaborator = AgentLoopCollaborator(lambda _request: None)
    collaborator.bind_live_index(live)

    active.driver_runtime = (loop, {"prepared_context": {"revision": 1}})
    await drain(
        collaborator.start(
            DriverStart(
                run_id=run_id,
                session_id=session_id,
                canonical_messages=({"role": "user", "content": "first"},),
                capability_snapshot={"tools": ["old.tool"]},
            )
        )
    )

    active.driver_runtime = (loop, {"prepared_context": {"revision": 2}})
    await drain(
        collaborator.start(
            DriverStart(
                run_id=run_id,
                session_id=session_id,
                canonical_messages=({"role": "user", "content": "resume"},),
                capability_snapshot={"tools": ["old.tool", "spike.echo"]},
            )
        )
    )

    boundary = ReactCommandBoundary(
        run_id=run_id,
        session_id=session_id,
        command_id="command-1",
        command_kind="execute_tools",
        canonical_messages=({"role": "user", "content": "resume"},),
        session_projection_cursor=0,
        prepared_context_ref="prepared-v1",
        tool_set_snapshot_ref="tools-v1",
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={},
        iteration=1,
        completion_state={},
        capability_snapshot={"tools": ["old.tool"]},
    )
    recovered_start = boundary.to_start()

    result = {
        "same_run_id": all(call["task_id"] == run_id for call in loop.calls),
        "first_tools": loop.calls[0]["tool_names_filter"],
        "second_tools": loop.calls[1]["tool_names_filter"],
        "second_context_revision": loop.calls[1]["prepared_context"]["revision"],
        "live_rebind_seam_feasible": (
            loop.calls[1]["tool_names_filter"] == ["old.tool", "spike.echo"]
            and loop.calls[1]["prepared_context"]["revision"] == 2
        ),
        "durable_request_payload_after_boundary_to_start": dict(
            recovered_start.request_payload
        ),
        "durable_gap": (
            "ReactCommandBoundary.to_start() does not preserve request_payload/context_os; "
            "production refresh must persist a canonical prepared snapshot or payload."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
