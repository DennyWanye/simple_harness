# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐阶段 A：§10.1 辅助事件"收敛已推进"（TaskGraphConvergenceAdvanced）。

收敛作业的状态每真正变化一次，同一事务里写一条事件，身份由作业与新行版本决定；状态没变的
重复推进不写。这条事件和"派发已绑定"都不进重查事件集，免得自己触发自己循环。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent, HERE.parent / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_convergence_wake_terminal_mission import _waiting_job  # noqa: E402
from test_htn_end_to_end import build_world  # noqa: E402

from agent_orchestrator.orchestrator import taskgraph_notifications  # noqa: E402
from agent_orchestrator.planning.htn.grounding import derive_id  # noqa: E402
from agent_orchestrator.storage.taskgraph_convergence import TaskGraphConvergenceStore  # noqa: E402


class _Permit:
    def require_quiescence(self, store, job): pass
    def require_reconciliation_started(self, store, job): pass
    def require_safe_abandonment(self, store, job, caller, command_id): pass


def _advanced(store, mission_id):
    return [event for event in store.list_events(mission_id) if event.type == "TaskGraphConvergenceAdvanced"]


def test_each_real_state_change_writes_one_advanced_event(tmp_path):
    """**Mutation**: drop the ``append_event`` in ``_transition`` → red; drop the
    ``state == job.state`` early return → red (a repeated WAITING writes an event)."""
    world = build_world(tmp_path, key="converge-advanced")
    store, mission_id = world.service.store, world.mission.id
    _waiting_job(store, mission_id, job_id="tg-converge-fixture")
    jobs = TaskGraphConvergenceStore(store, authority=_Permit())

    still = jobs.advance_state(mission_id, "tg-converge-fixture", expected_version=2, ready=False, now_ms=2_000)
    assert still.state == "WAITING" and _advanced(store, mission_id) == []

    ready = jobs.advance_state(mission_id, "tg-converge-fixture", expected_version=still.row_version,
                               ready=True, now_ms=3_000)
    [event] = _advanced(store, mission_id)
    assert event.id == derive_id("tg-convergence-advanced", "tg-converge-fixture", str(ready.row_version))
    assert event.actor_type == "system" and event.trace_id == "command-fixture"
    assert event.payload == {"schema_version": 1, "job_id": "tg-converge-fixture", "decision_id": "pd-fixture",
                             "from_state": "WAITING", "to_state": "READY", "row_version": ready.row_version}

    jobs.abandon(mission_id, "tg-converge-fixture", expected_version=ready.row_version, caller=None,
                 command_id="operator:abandon", now_ms=4_000)
    assert [(e.payload["from_state"], e.payload["to_state"]) for e in _advanced(store, mission_id)] == [
        ("WAITING", "READY"), ("READY", "ABANDONED")]


def test_auxiliary_events_never_trigger_a_recheck():
    """**Mutation**: add either type to ``_RECHECK_EVENTS`` → red."""
    assert "TaskGraphConvergenceAdvanced" not in taskgraph_notifications._RECHECK_EVENTS
    assert "TaskGraphDispatchBound" not in taskgraph_notifications._RECHECK_EVENTS
