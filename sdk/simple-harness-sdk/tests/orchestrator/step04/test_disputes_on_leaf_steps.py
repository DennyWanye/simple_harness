# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""两步结论矛盾（删旧平面模式 第二刀）：争议只从声明本身得出——两条声明都标"有争议"、
各自记着对方是谁；后来的那条不进知识库；``mission_disputes`` 如实列出这一对。冲突表、
冲突任务、仲裁都已删除，这里不依赖它们。"""

from __future__ import annotations

from knowledge_helpers import (
    claim,
    drive_to_running,
    envelope,
    passed_layers,
    submit,
    two_leaf_service,
    verify_claims_citing_pytest,
)

from agent_orchestrator.contracts import ClaimStatus
from agent_orchestrator.memory.summaries import build_summaries
from agent_orchestrator.verification.conflicts import mission_disputes

KEY = "impl_a.empty_input"
PROBE = "tests/probe/test_impl_a.py"


def _second(service, task_b, *, evidence=()):
    b1 = drive_to_running(service, task_b, agent="agent-2", turn="turn-2")
    sb = submit(
        service,
        b1,
        envelope(
            b1,
            claims=[claim("impl_a 对空输入返回 {}", key=KEY, stance="affirms", evidence=list(evidence))],
            artifacts=("notes/b.md",),
        ),
        artifact_paths=("notes/b.md",),
        turn="turn-2",
    )
    service.accept_result(sb.envelope.id, verifier_results=passed_layers(*([PROBE] if evidence else [])))
    return service.store.list_claims(sb.envelope.id)[0]


def test_two_supported_claims_that_contradict_are_both_disputed_and_name_each_other(tmp_path):
    service, mission, (task_a, task_b) = two_leaf_service(tmp_path, key="k-cand")
    a1 = drive_to_running(service, task_a)
    sa = submit(service, a1, envelope(a1, claims=[claim("impl_a 抛错（未测）", key=KEY, stance="refutes")]))
    service.accept_result(sa.envelope.id, verifier_results=passed_layers())
    first = service.store.list_claims(sa.envelope.id)[0]
    assert first.status is ClaimStatus.SUPPORTED

    second = _second(service, task_b)
    first = service.store.get_claim(first.id)
    assert first.status is ClaimStatus.DISPUTED and second.status is ClaimStatus.DISPUTED
    assert first.disputed_by == (second.id,) and second.disputed_by == (first.id,)
    assert service.store.list_knowledge(mission.id) == []

    disputes = mission_disputes(service.store, mission.id)
    assert [d["claim_id"] for d in disputes] == sorted([first.id, second.id])
    by_id = {d["claim_id"]: d for d in disputes}
    assert by_id[first.id]["disputed_by"] == [second.id]
    assert by_id[second.id]["disputed_by"] == [first.id]
    assert {d["key"] for d in disputes} == {KEY}
    assert {d["stance"] for d in disputes} == {"refutes", "affirms"}
    assert not any(d["in_knowledge"] for d in disputes)


def test_a_claim_against_verified_knowledge_is_disputed_and_kept_out_of_knowledge(tmp_path, monkeypatch):
    verify_claims_citing_pytest(monkeypatch)
    service, mission, (task_a, task_b) = two_leaf_service(tmp_path, key="k-know")
    a1 = drive_to_running(service, task_a)
    sa = submit(
        service,
        a1,
        envelope(a1, claims=[claim("impl_a 对空输入抛 ValueError", key=KEY, stance="refutes", evidence=[f"pytest:{PROBE}"])]),
    )
    service.accept_result(sa.envelope.id, verifier_results=passed_layers(PROBE))
    (knowledge,) = service.store.list_knowledge(mission.id)
    assert knowledge.status == "VERIFIED"

    second = _second(service, task_b, evidence=[f"pytest:{PROBE}"])
    assert second.status is ClaimStatus.DISPUTED
    assert second.disputed_by == (knowledge.claim_id,)
    # VERIFIED has no edge to DISPUTED: the knowledge stays, marked as contested
    (still,) = service.store.list_knowledge(mission.id)
    assert still.id == knowledge.id and still.status == "VERIFIED"
    assert still.disputed_by == (second.id,)
    assert service.store.get_claim(knowledge.claim_id).disputed_by == (second.id,)
    assert service.store.count_events(mission.id, "ClaimDisputed") == 1

    disputes = {d["claim_id"]: d for d in mission_disputes(service.store, mission.id)}
    assert set(disputes) == {knowledge.claim_id, second.id}
    assert disputes[knowledge.claim_id]["in_knowledge"] is True
    assert disputes[second.id]["in_knowledge"] is False
    assert disputes[second.id]["status"] == "DISPUTED"

    summary = build_summaries(service.store, mission.id)[f"mission:{mission.id}"]
    assert second.id in summary["disputed"]
    assert "open_conflicts" not in summary


def test_two_claims_of_one_result_against_the_same_claim_are_both_recorded(tmp_path):
    service, mission, (task_a, task_b) = two_leaf_service(tmp_path, key="k-two")
    a1 = drive_to_running(service, task_a)
    sa = submit(service, a1, envelope(a1, claims=[claim("impl_a 抛错（未测）", key=KEY, stance="refutes")]))
    service.accept_result(sa.envelope.id, verifier_results=passed_layers())
    first = service.store.list_claims(sa.envelope.id)[0]

    b1 = drive_to_running(service, task_b, agent="agent-2", turn="turn-2")
    sb = submit(
        service,
        b1,
        envelope(
            b1,
            claims=[
                claim("impl_a 返回 {}（未测）", key=KEY, stance="affirms"),
                claim("impl_a 对空输入不抛错（未测）", key=KEY, stance="affirms"),
            ],
            artifacts=("notes/b.md",),
        ),
        artifact_paths=("notes/b.md",),
        turn="turn-2",
    )
    service.accept_result(sb.envelope.id, verifier_results=passed_layers())
    later = service.store.list_claims(sb.envelope.id)
    assert len(later) == 2 and all(c.status is ClaimStatus.DISPUTED for c in later)
    # both later claims are on the record of the one they contradict — none overwritten
    assert set(service.store.get_claim(first.id).disputed_by) == {c.id for c in later}
    disputes = {d["claim_id"]: d for d in mission_disputes(service.store, mission.id)}
    assert set(disputes[first.id]["disputed_by"]) == {c.id for c in later}
