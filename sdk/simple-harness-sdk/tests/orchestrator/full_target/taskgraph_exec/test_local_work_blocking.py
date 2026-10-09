# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""2026-10-10（docopt 局）：收敛作业的拦截集合里，一笔"用量未知"的名额在预留按上限结清之后不再拦截。"""

from __future__ import annotations

from simple_harness.contracts import canonical_json

from agent_orchestrator.runtime.taskgraph_local_work import LocalWorkFacts

SUBJECT = "task-1:attempt-1"


def _facts(*, reservation: str, grant: str, unknown_usage: bool) -> LocalWorkFacts:
    body = {
        "mission_id": "mission-1", "actions": [],
        "attempts": [{"attempt_id": SUBJECT, "task_id": "task-1", "status": "RETRY_WAIT"}],
        "intents": [{"subject_id": SUBJECT, "intent_id": "intent-1", "state": "FAILED", "config": {"task_id": "task-1"}}],
        "reservations": [{"subject_id": SUBJECT, "state": reservation}],
        "provider_grants": [{"subject_id": SUBJECT, "state": grant}],
        "usage": [{"subject_id": SUBJECT, "unknown": 1 if unknown_usage else 0}],
        "tool_calls": [],
    }
    return LocalWorkFacts(mission_id="mission-1", canonical_document=canonical_json(body))


def test_an_unknown_charge_blocks_until_its_reservation_is_counted_at_the_upper_bound():
    """**Mutation**: UNKNOWN grants block regardless of the reservation → red."""
    tasks = frozenset({"task-1"})
    assert SUBJECT in _facts(reservation="RESERVED", grant="UNKNOWN", unknown_usage=True).blocking_subjects(tasks)
    assert SUBJECT in _facts(reservation="RESERVED", grant="SETTLED", unknown_usage=True).blocking_subjects(tasks)
    # 账已按上限关掉：这笔未知不再拦
    assert SUBJECT not in _facts(reservation="SETTLED", grant="UNKNOWN", unknown_usage=True).blocking_subjects(tasks)
    assert SUBJECT not in _facts(reservation="SETTLED", grant="UNKNOWN", unknown_usage=False).blocking_subjects(tasks)
    # 仍占着名额的调用：不管账怎样都拦
    assert SUBJECT in _facts(reservation="SETTLED", grant="HANDED_OFF", unknown_usage=False).blocking_subjects(tasks)
    assert SUBJECT in _facts(reservation="SETTLED", grant="RESERVED", unknown_usage=False).blocking_subjects(tasks)
