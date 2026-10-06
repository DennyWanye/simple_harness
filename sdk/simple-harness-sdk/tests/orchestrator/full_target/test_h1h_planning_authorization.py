"""规划授权签发 / 绑定 / 撤销 / 续期的权威性（H1-H）。

建任务走产品那一条路（2026-10-07 夜间车道 N4）：自 2026-09-27 起每个任务都在建任务事务里绑定
执行图，规划授权的来源变更要求任务已绑定（``taskgraph_source_events.record_source_change``）。
旧夹具直接往库里插任务、不绑执行图，签发时按名拒绝 ``TASKGRAPH_NOT_BOUND``——那是用例口径过时，
不是产品缺陷。现在任务经产品同形部署（:func:`agent_orchestrator.testing.product_world.product_world`，
``auto=False``：规划授权不自动签，由用例自己签发）的门面建出，用例再在部署关闭后的同一个库上
直接调规划授权接口，所守的签发人 / 租户 / 回执保证不变。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import NamedTuple

import pytest

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts import Budget, Mission, MissionStatus
from agent_orchestrator.contracts.planning_decisions import PlanningRequestBinding
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.planning_authorization import (
    PlanPrincipal,
    StorePlanningAuthorityReader,
    build_planning_authorization,
    check_planning_authorization,
)
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.storage.store import Store, StoreConflict
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.testing.product_world import product_world

TENANT = "tenant-a"
ISSUER = Principal("host")
GOAL = "写一份 NOTES.md，列出三条要点。"


def seed_product_missions(root: Path, *keys: str) -> dict[str, str]:
    """Create one Mission per idempotency key the product way (facade → creation transaction
    initializes the root and binds the planning protocol and the TaskGraph), then close the
    deployment.  Returns ``key -> mission_id``; the database is :func:`product_db` ``(root)``."""

    async def build() -> dict[str, str]:
        async with product_world(
            root,
            RoleScriptedProvider({"planner": []}),
            auto=False,
            tenant_id=TENANT,
            principal=ISSUER,
        ) as product:
            return {
                key: str(product.create({
                    "goal": GOAL, "idempotency_key": key, "success_criteria": ["file:NOTES.md"],
                })["mission_id"])
                for key in keys
            }

    return asyncio.run(build())


def product_db(root: Path) -> Path:
    return root / "orchestrator.db"


def _request(
    store: Store, mission_id: str, *, request_id: str = "req-auth", intent_id: str = "intent-auth"
) -> PlanningRequestBinding:
    """A planning request on the Mission's own bound planning protocol."""

    protocol = store.connection.execute(
        "SELECT package_version, prompt_version FROM mission_planning_protocols WHERE mission_id=?",
        (mission_id,),
    ).fetchone()
    assert protocol is not None, mission_id
    return PlanningRequestBinding(
        request_id=request_id,
        mission_id=mission_id,
        protocol_version="planning-decision-v1",
        package_version=int(protocol["package_version"]),
        package_hash="a" * 64,
        base_plan_revision=0,
        requirements_revision=0,
        scope_epoch_digest="b" * 64,
        subject_bindings_hash="c" * 64,
        visible_refs_digest="d" * 64,
        prompt_version=str(protocol["prompt_version"]),
        prompt_hash="e" * 64,
        created_at=store.now,
        intent_id=intent_id,
    )


def foreign_tenant_mission(mission_id: str, tenant_id: str) -> Mission:
    """A Mission of another tenant written straight into this store.  A deployment is single
    tenant (its creation transaction refuses to bind another tenant's Mission), so no product
    path puts one here; it exists only as a target the guards must refuse."""

    return Mission(
        id=mission_id,
        goal="g",
        success_criteria=("ok",),
        stop_conditions=(),
        allowed_tools=(),
        risk_level="sandbox",
        budget=Budget(max_tokens=1000, max_attempts=2),
        tenant_id=tenant_id,
        status=MissionStatus.CREATED,
        created_at=1.0,
        version=1,
        idempotency_key=mission_id,
    )


class Seeded(NamedTuple):
    store: Store
    mission: str


@pytest.fixture
def seeded(tmp_path):
    root = tmp_path / "product"
    mission = seed_product_missions(root, "m-auth")["m-auth"]
    value = Store.open(product_db(root))
    PlanningDecisionStore(value).insert_planning_request(_request(value, mission))
    yield Seeded(value, mission)
    value.close()


def test_issue_bind_snapshot_revoke_and_renew_are_authoritative(seeded):
    store, mission = seeded
    api = PlanningAuthorizationApi(store, tenant_id="tenant-a", principal=Principal("host"))
    issued = api.issue(mission, command_id="cmd-1", request_id="req-auth")
    assert issued.revision == 1 and issued.active
    reader = StorePlanningAuthorityReader(PlanningAdmissionStore(store), store)
    snap = build_planning_authorization(
        "req-auth", read=reader, caller=PlanPrincipal("host"), policy=api.policy, now_ms=2_000
    )
    assert snap.grant_id == issued.grant_id
    assert (
        check_planning_authorization(snap, decision_key="REFINE", now_ms=snap.not_before_ms) is None
    )
    revoked = api.revoke(issued.grant_id, expected_revision=1, command_id="cmd-2", reason="stop")
    assert revoked.revision == 2 and not revoked.active
    stale = build_planning_authorization(
        "req-auth",
        read=reader,
        caller=PlanPrincipal("host"),
        policy=api.policy,
        now_ms=int(store.now * 1000),
    )
    assert (
        check_planning_authorization(stale, decision_key="REFINE", now_ms=int(store.now * 1000))
        == "REQUEST_BINDING_STALE"
    )


def test_issue_command_replay_and_changed_input_conflict(seeded):
    store, mission = seeded
    api = PlanningAuthorizationApi(store, tenant_id="tenant-a", principal=Principal("host"))
    first = api.issue(mission, command_id="cmd-1")
    replay = api.issue(mission, command_id="cmd-1")
    assert replay == first
    with pytest.raises(StoreConflict):
        api.issue(mission, command_id="cmd-1", scope_id="other")


def test_issue_command_replay_includes_request_binding_input(seeded):
    store, mission = seeded
    api = PlanningAuthorizationApi(store, tenant_id="tenant-a", principal=Principal("host"))
    api.issue(mission, command_id="cmd-request", request_id="req-auth")
    with pytest.raises(StoreConflict):
        api.issue(mission, command_id="cmd-request", request_id=None)


def test_revoke_command_replay_includes_human_reason(seeded):
    store, mission = seeded
    api = PlanningAuthorizationApi(store, tenant_id="tenant-a", principal=Principal("host"))
    issued = api.issue(mission, command_id="cmd-reason")
    api.revoke(issued.grant_id, expected_revision=1, command_id="cmd-revoke", reason="stop")
    with pytest.raises(StoreConflict):
        api.revoke(issued.grant_id, expected_revision=1, command_id="cmd-revoke", reason="retry")


def test_issue_records_who_approved_outside_the_command_hash(seeded):
    store, mission = seeded
    # 2026-09-25 UI 全量点击：自动模式下 Host 代签规划授权，必须如实记成自动，不冒充人。
    api = PlanningAuthorizationApi(store, tenant_id="tenant-a", principal=Principal("host"))
    auto = api.issue(mission, command_id="cmd-auto", approval_source="HOST_AUTO_PERMISSION")
    admission = PlanningAdmissionStore(store)
    row = admission.get_grant(auto.grant_id)
    assert row["approval_source"] == "HOST_AUTO_PERMISSION"
    assert row["on_behalf_of_principal_id"] == "host"
    # Replay of the same command keeps its identity even if the source label differs.
    assert api.issue(mission, command_id="cmd-auto") == auto
    human = api.issue(mission, command_id="cmd-human")
    assert admission.get_grant(human.grant_id)["approval_source"] == "HUMAN"
    with pytest.raises(ValueError):
        api.issue(mission, command_id="cmd-bad", approval_source="MODEL")
