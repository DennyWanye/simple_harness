# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""A Worker result that leaves a declared output port unclaimed is a *rejected result*.

Found 2026-10-01 while moving loop fixtures onto the current planning protocol: under
the completion protocol ``record_result`` validates the envelope's port claims and
raised ``OperationCompletionError`` — a ``ContractError`` the collection loop does not
isolate — so one model mistake ended ``run()`` for every Mission in the process.  A
model may be wrong; its mistake must become a recorded refusal, never a crash.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_progress_acceptance_2b import _abc_world, _by_type  # noqa: E402

from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    RoleScriptedProvider,
    envelope_step,
    package_of,
)


class _NeverClaims:
    """Every leaf writes its file and returns an envelope that claims no port."""

    def __init__(self) -> None:
        self.queues: dict[str, list[Any]] = {}

    def __call__(self, request: Any) -> Any:
        attempt_id = str(package_of(request)["attempt"]["attempt_id"])
        if attempt_id not in self.queues:
            self.queues[attempt_id] = [
                ("workspace_write_file", {"path": "verdict.json", "content": "{}\n"}),
                envelope_step(summary="scripted", artifacts=["verdict.json"], claims=["scripted"]),
            ]
        item = self.queues[attempt_id].pop(0)
        return item(request) if callable(item) else item


def test_an_unclaimed_declared_port_is_refused_as_a_result_and_the_loop_survives(tmp_path) -> None:
    world = _abc_world(tmp_path, key="unclaimed-port")
    tasks = _by_type(world)
    evidence = Path(tmp_path) / "evidence"
    world.store.close()
    provider = RoleScriptedProvider({"worker": [_NeverClaims()] * 12, "planner": []})

    async def case() -> dict[str, Any]:
        config = OrchestratorConfig(
            evidence_root=evidence, max_concurrency=2, test_timeout_seconds=30
        )
        async with Orchestrator(config, provider, poll_interval=0.02) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            # ``run`` must come back by itself: before the fix it raised out of the loop.
            await asyncio.wait_for(loop.run(max_cycles=40), timeout=60)
            events = list(loop.store.list_events(world.mission.id))
            return {
                "rejected": [dict(e.payload) | {"task_id": e.task_id}
                             for e in events if e.type == "ResultRejected"],
                "submitted": [e for e in events if e.type == "ResultSubmitted"],
            }

    outcome = asyncio.run(case())
    refused = [row for row in outcome["rejected"] if row["reason"] == "completion_inputs_refused"]
    assert refused, outcome["rejected"]
    assert {row["task_id"] for row in refused} <= {tasks["plan.leaf"], tasks["plan.act"]}
    assert all(row["detail"]["code"] == "OP_COMPLETION_PORT_CLAIMS_UNAVAILABLE" for row in refused)
    assert all("unclaimed" in row["detail"]["error"] for row in refused)
    # nothing was registered as a submitted result for the refused envelopes
    assert outcome["submitted"] == []
