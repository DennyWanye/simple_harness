# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice D (D7-8' / D7-9', S7-07): the sixth verification layer, NEEDS_HUMAN,
Verifier conflicts sent to a person, takeover and comments — a person's word is kept as
HumanOverride with its basis, and it never widens what was authorised."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fixtures_provider import RoleScriptedProvider, critic_step, envelope_step, graph_proposal_step
from graph_helpers7 import complete, drive_to_running
from helpers_step07 import ALICE, BOB, ledger_service

from agent_orchestrator.contracts import AttemptStatus, Budget, MissionStatus, TaskStatus
from agent_orchestrator.orchestrator.action_commits import ActionCommitError
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig

TOOLS = ["workspace_read_file", "workspace_write_file", "workspace_list", "run_tests"]
SEED = {
    "tests/test_ok.py": "def test_ok():\n    assert True\n",
    "tests/test_bad.py": "def test_bad():\n    assert False\n",
}


def _config(tmp_path):
    return OrchestratorConfig(
        evidence_root=Path(tmp_path) / "evidence", max_concurrency=1, test_timeout_seconds=60
    )


def _spec(key, criteria=("file:REPORT.md",)):
    return MissionSpec(
        goal="写一份变更报告",
        success_criteria=tuple(criteria),
        tenant_id="tenant-7",
        idempotency_key=key,
        allowed_tools=tuple(TOOLS),
        budget=Budget(max_tokens=300_000, max_attempts=8),
        workspace_seed=SEED,
        orchestration_semantics_version="legacy",
    )


def _task(key, policy, *, deps=(), criteria=("file:REPORT.md",), attempts=2):
    return {
        "key": key,
        "goal": f"写变更报告（{key}）",
        "rationale": "报告需要人看过才算数",
        "dependencies": list(deps),
        "success_criteria": list(criteria),
        "verification_policy": list(policy),
        "outputs": ["REPORT.md"],
        "allowed_tools": TOOLS,
        "budget": {"max_tokens": 30_000, "max_attempts": attempts},
        "priority": 1.0,
    }


def _worker(text="# 报告\n\n改了一个开关。\n", summary="写好了报告"):
    return [
        ("workspace_list", {}),
        ("workspace_write_file", {"path": "REPORT.md", "content": text}),
        envelope_step(summary=summary, artifacts=["REPORT.md"], claims=["报告已写好"]),
    ]


# ------------------------------------------------------------------ Verifier conflicts
@pytest.mark.parametrize("ruling", ["met", "unmet"])
def test_s7_07_a_judge_that_disagrees_with_the_task_critics_goes_to_a_person(tmp_path, ruling):
    criteria = ("file:REPORT.md", "报告写明了回滚方式")
    provider = RoleScriptedProvider(
        {
            "planner": [
                graph_proposal_step(
                    [
                        _task("A", ["format_check", "rule_check", "critic_review"]),
                        _task("B", ["format_check", "rule_check", "critic_review"], deps=["A"]),
                    ]
                )
            ],
            "worker": _worker() + _worker(text="# 报告\n\n第 3 节写了回滚。\n"),
            "critic": [
                critic_step(verdict="PASS", criteria_met=True),
                critic_step(verdict="PASS", criteria_met=True),
                critic_step(verdict="PASS", criteria_met=False),  # the independent judge disagrees
            ],
        }
    )

    async def case():
        async with Orchestrator(_config(tmp_path), provider) as orchestrator:
            mission = await orchestrator.submit_mission(_spec(f"j-{ruling}", criteria=criteria))
            await orchestrator.run()
            await orchestrator.run()
            store = orchestrator.store
            assert store.get_mission(mission.id).status is MissionStatus.ACTIVE
            [request] = store.list_approvals(mission.id)
            assert (request["kind"], request["topic"]) == ("arbitration", "judgment")
            assert request["context"]["criteria"] == ["报告写明了回滚方式"]
            orchestrator.commit.arbitrate(
                request["request_id"],
                principal=BOB,
                ruling=ruling,
                basis="我读了报告第 3 节",
                nonce="n-1",
            )
            await orchestrator.run()
            final = store.get_mission(mission.id)
            expected = MissionStatus.COMPLETED if ruling == "met" else MissionStatus.FAILED
            assert final.status is expected, orchestrator.progress_log
            judged = {j["criterion"]: j for j in final.final_report["success_criteria"]}
            assert judged["报告写明了回滚方式"]["judge"] == "human_arbitration"
            [override] = store.list_overrides(mission.id)
            assert (
                override["basis"] == "我读了报告第 3 节"
                and override["principal"]["principal_id"] == "bob"
            )

    asyncio.run(case())
    assert provider.by_role.get("critic") == 3  # the judge ran once; waiting did not re-run it


# ------------------------------------------------------------------ takeover and comments
def test_s7_07_a_takeover_stop_ends_the_task_with_its_basis_on_the_books(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    attempt = drive_to_running(service, t["A"])
    record = service.takeover(
        t["A"].id, principal=ALICE, action="stop", basis="卡住 40 分钟没有进展"
    )
    assert record["action"] == "takeover_stop" and "no approval, tool, budget" in record["scope"]
    final = service.store.get_mission(mission.id)
    assert final.status is MissionStatus.FAILED and final.stop_reason == "human_override"
    assert final.final_report["detail"]["basis"] == "卡住 40 分钟没有进展"
    assert service.store.get_attempt(attempt.id).status is AttemptStatus.CANCELLED
    [event] = [e for e in service.store.list_events(mission.id) if e.type == "HumanOverride"]
    assert (event.actor_type, event.actor_id) == ("user", "alice")


def test_s7_07_retry_with_note_stays_within_the_task_and_carries_the_note(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    attempt = drive_to_running(service, t["A"])
    service.takeover(
        t["A"].id,
        principal=BOB,
        action="retry_with_note",
        basis="方向不对",
        note="先读 docs/SPEC.md",
    )
    closed = service.store.get_attempt(attempt.id)
    assert (
        closed.status is AttemptStatus.CANCELLED
        and closed.failure["reason"] == "human_retry_with_note"
    )
    [comment] = [e for e in service.store.list_events(mission.id) if e.type == "HumanCommentAdded"]
    assert (
        comment.payload["target_id"] == t["A"].id and comment.payload["text"] == "先读 docs/SPEC.md"
    )
    assert service.store.get_mission(mission.id).status is MissionStatus.ACTIVE
    task = service.store.get_task(t["A"].id)
    assert (
        task.status is TaskStatus.ACTIVE and task.budget.max_attempts == t["A"].budget.max_attempts
    )
    with pytest.raises(ActionCommitError):  # only stop / retry_with_note exist
        service.takeover(t["A"].id, principal=BOB, action="approve", basis="x")
    with pytest.raises(ActionCommitError):
        service.takeover(
            t["A"].id, principal=BOB, action="stop", basis="sk-" + "a" * 40
        )  # looks like a secret


def test_a_takeover_never_revives_an_ended_task_or_mission(tmp_path):
    service, mission, t, _config, _connectors, _ = ledger_service(tmp_path)
    complete(service, t["A"])
    with pytest.raises(ActionCommitError):
        service.takeover(t["A"].id, principal=ALICE, action="retry_with_note", basis="再来一次")
    service.cancel_mission(mission.id)
    with pytest.raises(ActionCommitError):
        service.takeover(t["B"].id, principal=ALICE, action="stop", basis="停")


def test_comments_are_kept_as_data_on_a_task_or_a_request(tmp_path):
    service, mission, t, _config, connectors, deployment = ledger_service(tmp_path)
    from helpers_step07 import candidate

    action = service.propose_action(
        candidate(),
        mission_id=mission.id,
        task_id=t["A"].id,
        result_id="result-1",
        attempt_id=f"{t['A'].id}:attempt-1",
        artifact_id="artifact-1",
        artifact_hash="a" * 64,
        connectors=connectors,
        deployment=deployment,
    )
    service.add_comment(action["approval_request_id"], principal=ALICE, text="上线窗口在周四")
    service.add_comment(t["A"].id, principal=BOB, text="注意兼容旧客户端")
    request = service.store.get_approval(action["approval_request_id"])
    assert [c["text"] for c in request["comments"]] == ["上线窗口在周四"]
    assert request["state"] == "PENDING"  # a comment decides nothing
    texts = [
        e.payload["text"]
        for e in service.store.list_events(mission.id)
        if e.type == "HumanCommentAdded"
    ]
    assert texts == ["上线窗口在周四", "注意兼容旧客户端"]


# ------------------------------------------------------------------ code review round 1
def test_a_judge_that_could_not_run_is_no_verifier_and_no_conflict(tmp_path):
    """Review P1-2: when the independent judge cannot answer, its criterion stays unmet
    (a missing layer is never a PASS, ORCH §12.4) — nobody is asked to rule it met."""

    criteria = ("file:REPORT.md", "报告写明了回滚方式")
    provider = RoleScriptedProvider(
        {
            "planner": [
                graph_proposal_step(
                    [
                        _task("A", ["format_check", "rule_check", "critic_review"]),
                        _task("B", ["format_check", "rule_check", "critic_review"], deps=["A"]),
                    ]
                )
            ],
            "worker": _worker() + _worker(text="# 报告\n\n第 3 节写了回滚。\n"),
            "critic": [
                critic_step(verdict="PASS", criteria_met=True),
                critic_step(verdict="PASS", criteria_met=True),
            ]
            + ["这不是一个裁决"] * 6,  # the judge answers nothing usable, every time
        }
    )

    async def case():
        async with Orchestrator(_config(tmp_path), provider) as orchestrator:
            mission = await orchestrator.submit_mission(_spec("j-none", criteria=criteria))
            await orchestrator.run()
            store = orchestrator.store
            final = store.get_mission(mission.id)
            assert (
                final.status is MissionStatus.FAILED
                and final.stop_reason == "mission_criteria_unmet"
            )
            assert store.list_approvals(mission.id) == []
            judged = {j["criterion"]: j for j in final.final_report["success_criteria"]}
            assert judged["报告写明了回滚方式"]["source"] == "unavailable"

    asyncio.run(case())
