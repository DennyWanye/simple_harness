# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Doc5 report quality cannot rely on a Critic the Task did not request.

Test-first commit oracles, not Provider-quality evidence. Reopen a genuinely
frozen Mission before exercising each gate under the current registry. Rejection
must preserve graph, budgets and receipts; the same valid proposal then succeeds.
"""

from contextlib import closing
from dataclasses import replace

import pytest

from agent_orchestrator.contracts import Budget
from agent_orchestrator.governance import domains
from agent_orchestrator.graph.task_graph import TaskGraphProposal
from agent_orchestrator.orchestrator.commit_service import (
    CommitRejected,
    CommitService,
    MissionSpec,
    TaskProposal,
)
from agent_orchestrator.runtime.role_templates import ROLES, template_for_domain
from agent_orchestrator.storage.store import Store

WEAK = ("format_check", "rule_check")
REVIEWED = (*WEAK, "critic_review")


@pytest.fixture(params=["doc3", "doc4", "doc5", "doc6", "code"])
def frozen_service(request, tmp_path, monkeypatch):
    profile = {
        "doc3": domains.DOC_PROFILE_V3,
        "doc4": domains.DOC_PROFILE_V4,
        "doc5": domains.DOC_PROFILE_V5,
        "doc6": domains.DOC_PROFILE_V6,
        "code": domains.CODE_PROFILE,
    }[request.param]
    path = tmp_path / "orchestrator.db"
    with closing(Store.open(path, clock=lambda: 1_000.0)) as store:
        service = CommitService(store, deployed_layers=frozenset(REVIEWED))
        # Create through the production gate as that deployment version, then
        # restore today's registry. No synthetic domain row or checker mock.
        with monkeypatch.context() as patch:
            patch.setattr(domains, "DOMAINS", {**domains.DOMAINS, profile.id: profile})
            mission, _ = service.create_mission(
                MissionSpec(
                    goal="比较资料并形成包含条件与风险的报告",
                    success_criteria=("file:report.md",),
                    tenant_id="doc5-critic-gate",
                    idempotency_key="frozen",
                    domain=profile.id,
                    budget=Budget(max_tokens=100_000, max_attempts=12),
                    orchestration_semantics_version="legacy",
                )
            )
        planning = service.begin_planning(mission.id)
    with closing(Store.open(path, clock=lambda: 1_000.0)) as reopened:
        service = CommitService(reopened, deployed_layers=frozenset(REVIEWED))
        assert domains.resolve_domain(domains.DOC_DOMAIN).version == "9"
        assert service.domain_for(mission.id).to_json() == profile.to_json()
        yield service, planning, request.param in {"doc5", "doc6"}


def _node(key, policy):
    return {
        "key": key,
        "goal": f"核对报告资料 {key} 的条件与风险",
        "rationale": "为资料比较报告提供依据",
        "dependencies": [],
        "success_criteria": [f"file:notes/{key}.md"],
        "verification_policy": list(policy),
        "allowed_tools": [],
        "budget": {"max_tokens": 20_000, "max_attempts": 3},
        "outputs": [f"notes/{key}.md"],
    }


def _state(service, mission_id):
    snapshot = service.store.snapshot(mission_id)
    snapshot.pop("event_count")
    with service.store.transaction() as connection:
        for table, key in (
            ("budget_accounts", "account_id"),
            ("budget_reservations", "reservation_id"),
            ("commit_receipts", "commit_id"),
        ):
            snapshot[table] = [
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {key}")
            ]
    return snapshot


@pytest.mark.parametrize("entry", ["graph", "single"])
def test_frozen_task_submission_requires_critic_only_for_doc5(frozen_service, entry):
    """Doc5's original floor also applies to doc6; older frozen profiles stay weak."""
    service, planning, strict = frozen_service

    def submit(policy):
        node = _node("A", policy)
        if entry == "single":
            proposal = TaskProposal(
                goal=node["goal"],
                rationale=node["rationale"],
                success_criteria=tuple(node["success_criteria"]),
                verification_policy=policy,
                allowed_tools=(),
                budget=Budget.from_json(node["budget"]),
            )
            task, receipt = service.commit_task_proposal(
                planning.id, proposal, base_version=planning.version, source={"planner": "single"}
            )
            return [task], receipt
        return service.commit_task_graph(
            planning.id,
            TaskGraphProposal.from_json({"tasks": [node]}),
            base_version=planning.version,
            source={"planner": "graph"},
        )

    before = _state(service, planning.id)
    events_before = service.store.iter_events(planning.id)
    if strict:
        with pytest.raises(CommitRejected, match="critic_review"):
            submit(WEAK)
        assert _state(service, planning.id) == before
        added = service.store.iter_events(planning.id)[len(events_before) :]
        assert [event.type for event in added] == ([] if entry == "single" else ["TaskGraphRejected"])
        assert all(event.payload["reason"] == "domain" for event in added)
        created, receipt = submit(REVIEWED)
        assert created[0].verification_policy == REVIEWED
    else:
        # Frozen doc3/doc4 and code still admit their previous explicit policy.
        created, receipt = submit(WEAK)
        assert created[0].verification_policy == WEAK
    assert len(created) == 1 and receipt


@pytest.mark.parametrize("version", ["5", "6", "7", "8", "9"])
def test_doc5_empty_single_policy_cannot_bypass_the_floor(tmp_path, monkeypatch, version):
    with closing(Store.open(tmp_path / "empty.db")) as store:
        service = CommitService(store, deployed_layers=frozenset(REVIEWED))
        with monkeypatch.context() as patch:
            if version in {"5", "6", "7", "8"}:
                patch.setattr(domains, "DOMAINS", {
                    **domains.DOMAINS,
                    domains.DOC_DOMAIN: getattr(domains, f"DOC_PROFILE_V{version}"),
                })
            mission, _ = service.create_mission(
                MissionSpec(
                    goal="核对报告质量",
                    success_criteria=("file:report.md",),
                    tenant_id="doc5-empty",
                    idempotency_key="empty",
                    domain=domains.DOC_DOMAIN,
                    budget=Budget(max_tokens=100_000, max_attempts=12),
                    orchestration_semantics_version="legacy",
                )
            )
        assert domains.resolve_domain(domains.DOC_DOMAIN).version == "9"
        assert service.domain_for(mission.id).version == version
        planning = service.begin_planning(mission.id)
        proposal = TaskProposal(
            goal="核对报告质量",
            rationale="保证报告条件与风险完整",
            success_criteria=("file:report.md",),
            verification_policy=(),
            allowed_tools=(),
            budget=Budget(max_tokens=20_000, max_attempts=3),
        )
        before = _state(service, mission.id)
        with pytest.raises(CommitRejected, match="critic_review"):
            service.commit_task_proposal(
                mission.id, proposal, base_version=planning.version, source={"planner": "empty"}
            )
        assert _state(service, mission.id) == before
        task, _ = service.commit_task_proposal(
            mission.id,
            replace(proposal, verification_policy=REVIEWED),
            base_version=planning.version,
            source={"planner": "valid"},
        )
        assert task.verification_policy == REVIEWED


@pytest.mark.parametrize("role", ["planner"])
@pytest.mark.parametrize("version", ["5", "6"])
def test_doc5_planning_prompts_require_actual_critic_policy(role, version):
    profile = domains.DOC_PROFILE_V5 if version == "5" else domains.DOC_PROFILE_V6
    current = template_for_domain(ROLES[role], profile, {})
    prompt_version = "2" if version == "5" else "3"
    assert current.prompt_version == f"{role}-doc-research-v{prompt_version}"
    assert "每个文档 Task 的 verification_policy 必须包含 critic_review" in current.instructions
    for profile in (domains.DOC_PROFILE_V3, domains.DOC_PROFILE_V4):
        old = template_for_domain(ROLES[role], profile, {})
        assert old.prompt_version == f"{role}-doc-research-v1"
        assert "每个文档 Task 的 verification_policy 必须包含 critic_review" not in old.instructions


def test_doc5_declares_quality_floor_without_changing_published_profiles():
    assert domains.DOC_PROFILE_V5.version == "5"
    assert domains.DOC_PROFILE_V5.planner_floor == REVIEWED
    assert domains.DOC_PROFILE_V6.version == "6"
    assert domains.DOC_PROFILE.version == "9"
    assert domains.DOC_PROFILE.planner_floor == REVIEWED
    assert domains.DOC_PROFILE_V3.planner_floor == domains.DOC_PROFILE_V4.planner_floor == WEAK
    assert domains.CODE_PROFILE.planner_floor == ()
