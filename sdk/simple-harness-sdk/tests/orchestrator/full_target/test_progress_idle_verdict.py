# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 2B: one idle verdict for the stall record and its confirmation.

Real run 2026-09-28 (mission-e5f82ae8c9f24ff8): the root was resolved, success
judged and the assured closeout was converging, yet the stall *record* (which did
not know that wait) wrote HierarchicalMissionStalled. §3.5 also routes an UNKNOWN
action under reconciliation and a pending approval to WAIT, which neither list knew.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.progress import IdleFacts, Route, idle_verdict

WAITS = (
    ("closeout_pending", "CLOSEOUT_CONVERGING"),
    ("root_resolved", "ROOT_RESOLVED_AWAITING_JUDGMENT"),
    ("running_rows", "WORK_RUNNING"),
    ("unknown_actions", "OPERATION_RECONCILIATION"),
    ("approvals_pending", "APPROVAL_PENDING"),
    ("operation_completion", "OPERATION_OUTCOME_PENDING"),
    ("assurance_work", "ASSURANCE_WORK_QUEUED"),
    ("planning_wait", "PLANNING_WAIT"),
    ("taskgraph_sources", "TASKGRAPH_SOURCES_PENDING"),
)


@pytest.mark.parametrize(("fact", "reason"), WAITS)
def test_every_legal_wait_waits_on_a_named_source(fact, reason):
    decision = idle_verdict(IdleFacts("m1", plan_has_work=True, **{fact: True}))
    assert decision.route is Route.WAIT
    assert decision.reason_code == reason and decision.wake


def test_only_a_plan_with_withheld_work_and_no_wait_is_a_stop_candidate():
    assert idle_verdict(IdleFacts("m1", plan_has_work=True)).route is Route.STOP


def test_unclassifiable_is_a_diagnosis_wait_never_slow_or_stop():
    for facts in (IdleFacts("m1"), IdleFacts("m1", plan_has_work=False)):
        decision = idle_verdict(facts)
        assert decision.route is Route.WAIT
        assert decision.reason_code == "SYSTEM_DIAGNOSIS_REQUIRED"


def test_finished_work_is_the_finalizers_business():
    decision = idle_verdict(IdleFacts("m1", all_rows_terminal=True, plan_has_work=True))
    assert decision.route is Route.FAST and decision.action == "FINALIZE"


def _loop(*, closeout: bool, actions=(), approvals=(), dead_end: bool = False):
    notes = []
    mission = SimpleNamespace(id="m1")
    fake = SimpleNamespace(
        _new_mode=lambda mission: SimpleNamespace(
            admissions=lambda mission_id: SimpleNamespace(refusals=("withheld",), readiness=()),
            network=lambda mission_id: SimpleNamespace(root_occurrence_ids=(), occurrences=()),
        ),
        store=SimpleNamespace(
            list_tasks=lambda mission_id: [SimpleNamespace(status="READY")],
            list_actions=lambda mission_id: list(actions),
            list_approvals=lambda mission_id, *states: [a for a in approvals if not states or a["state"] in states],
        ),
        commit=SimpleNamespace(assured_closeout_pending=lambda mission_id: closeout),
        _awaiting_retry_decision=lambda mission_id, task: False,
        _has_pending_operation_completion=lambda mission: False,
        # 2026-10-06 车道 O（Assurance §7.2）：操作那条线具名判死的效果——已判定的任务不再算"等收尾"
        _operation_dead_end=lambda mission: dead_end,
        _has_pending_assurance_work=lambda mission_id: False,
        _has_pending_planning_waits=lambda mission_id: False,
        # 推后第 1 批 A26：签不出 PLAN 证书而不开轮的任务算合法等待（假编排器要带这张表）
        _planning_evidence_waits={},
        _taskgraph_notifications=None,
        _note=notes.append,
        _unrecovered=set(),  # 阶段 B：恢复失败的任务这一轮不判空闲（假编排器要带这张表）
        _handoff_ground_gone=lambda action_key: False,  # 阶段 C 核验：地基没了的交接拒绝不算等人
        _requirements_unconfirmed=lambda mission: False,  # 阶段 E：现行要求在等人确认
    )
    fake._root_resolved = lambda mission, new_mode: False
    return fake, mission


def test_a_judged_mission_converging_its_closeout_is_not_a_stall_candidate():
    fake, mission = _loop(closeout=True)
    facts, admissions, _rows = Orchestrator._idle_facts(fake, mission)
    assert idle_verdict(facts).reason_code == "CLOSEOUT_CONVERGING"
    assert admissions is None  # the plan is not even read


def test_a_judged_mission_whose_effect_can_never_converge_is_a_stall_candidate():
    """2026-10-06 车道 O（Assurance §7.2）：判定不再等效果收敛，所以"已判定、等收尾"只在收尾还能收敛时
    才是合法等待。操作那条线已具名判死的效果（成功却无回执、物化一直被拒、结果审阅用完……）让它回到
    卡死检测：读计划、按"没有可派发的工作"候选停下，而不是永远 CLOSEOUT_CONVERGING。"""
    fake, mission = _loop(closeout=True, actions=[{"state": "SUCCEEDED"}], dead_end=True)
    facts, admissions, _ = Orchestrator._idle_facts(fake, mission)
    assert not facts.closeout_pending and not facts.root_resolved and not facts.all_rows_terminal
    assert admissions is not None and idle_verdict(facts).route is Route.STOP


def test_an_unknown_action_or_a_pending_approval_waits():
    for state, reason in (("UNKNOWN", "OPERATION_RECONCILIATION"),
                          ("AWAITING_APPROVAL", "APPROVAL_PENDING")):
        fake, mission = _loop(closeout=False, actions=[{"state": state, "action_key": "a1"}])
        facts, _, _ = Orchestrator._idle_facts(fake, mission)
        assert idle_verdict(facts).reason_code == reason


def test_without_a_wait_the_plan_is_read_and_withheld_work_is_a_candidate():
    fake, mission = _loop(closeout=False, actions=[{"state": "SUCCEEDED"}])
    facts, admissions, _ = Orchestrator._idle_facts(fake, mission)
    assert admissions is not None and idle_verdict(facts).route is Route.STOP


def test_a_result_suspended_for_a_persons_review_waits_instead_of_stalling():
    # Real run 2026-09-30 (mission-f13b483137b6f435): two reviews INCONCLUSIVE, the
    # result suspended and a review approval requested — and the same cycle failed the
    # Mission "no dispatchable work" and cancelled the approval: only *action*
    # approvals counted as a person's pending approval.
    fake, mission = _loop(closeout=False, actions=[{"state": "SUCCEEDED"}],
                          approvals=[{"kind": "review", "state": "PENDING"}])
    facts, admissions, _ = Orchestrator._idle_facts(fake, mission)
    assert facts.approvals_pending and admissions is None
    assert idle_verdict(facts).reason_code == "APPROVAL_PENDING"
    fake, mission = _loop(closeout=False, actions=[{"state": "SUCCEEDED"}],
                          approvals=[{"kind": "review", "state": "GRANTED"}])
    facts, _, _ = Orchestrator._idle_facts(fake, mission)
    assert not facts.approvals_pending  # a decided one is no longer a wait
