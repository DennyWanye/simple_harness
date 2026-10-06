"""A03 real Store/API regressions for issuer and tenant isolation.

任务经产品同形部署建出（建任务事务里绑定执行图，2026-10-07 夜间车道 N4 改夹具，见
test_h1h_planning_authorization 的模块说明）；所守保证不变：外来签发人不能借重放读到原回执、
跨租户不能重放撤销回执、外来主体不能绑定别人的授权、原签发人重放不延长有效期、
提交事务里重查签发人、别的任务的请求拿不到本任务的授权。
"""

from __future__ import annotations

import pytest
from test_h1h_planning_authorization import (
    TENANT,
    Seeded,
    _request,
    foreign_tenant_mission,
    product_db,
    seed_product_missions,
)

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.storage.store import Store, StoreConflict


@pytest.fixture
def seeded(tmp_path):
    root = tmp_path / "product"
    mission = seed_product_missions(root, "m-auth")["m-auth"]
    store = Store.open(product_db(root))
    PlanningDecisionStore(store).insert_planning_request(_request(store, mission))
    yield Seeded(store, mission)
    store.close()


def _api(store, *, tenant: str = "tenant-a", principal: str = "host"):
    return PlanningAuthorizationApi(store, tenant_id=tenant, principal=Principal(principal))


def test_a03_foreign_issuer_cannot_replay_issue_receipt_by_naming_original_grantee(
    seeded,
) -> None:
    authority_store, mission = seeded
    original = _api(authority_store).issue(
        mission,
        command_id="issue-a03",
        planner_principal_id="host",
    )
    before = authority_store.connection.total_changes

    with pytest.raises(StoreConflict) as refused:
        _api(authority_store, principal="foreign-host").issue(
            mission,
            command_id="issue-a03",
            planner_principal_id="host",
        )

    assert original.grant_id not in str(refused.value)
    assert original.issuer_receipt_hash not in str(refused.value)
    assert authority_store.connection.total_changes == before


def test_a03_cross_tenant_caller_cannot_replay_revoke_receipt(seeded) -> None:
    authority_store, mission = seeded
    issuer = _api(authority_store)
    grant = issuer.issue(mission, command_id="issue-for-revoke")
    receipt = issuer.revoke(
        grant.grant_id,
        expected_revision=grant.revision,
        command_id="revoke-a03",
        reason="stop",
    )
    before = authority_store.connection.total_changes

    with pytest.raises(StoreConflict) as refused:
        _api(authority_store, tenant="tenant-foreign", principal="foreign-host").revoke(
            grant.grant_id,
            expected_revision=grant.revision,
            command_id="revoke-a03",
            reason="stop",
        )

    assert receipt.grant_id not in str(refused.value)
    assert receipt.issuer_receipt_hash not in str(refused.value)
    assert authority_store.connection.total_changes == before


def test_a03_foreign_principal_cannot_bind_another_issuers_grant(seeded) -> None:
    authority_store, mission = seeded
    grant = _api(authority_store).issue(mission, command_id="issue-for-binding")
    before = authority_store.connection.total_changes

    with pytest.raises(StoreConflict) as refused:
        _api(authority_store, principal="foreign-host").bind_request(
            "req-auth", grant_id=grant.grant_id
        )

    assert grant.grant_id not in str(refused.value)
    assert grant.issuer_receipt_hash not in str(refused.value)
    assert authority_store.connection.total_changes == before
    assert (
        authority_store.connection.execute(
            "SELECT 1 FROM planning_request_authority_bindings WHERE request_id = ?",
            ("req-auth",),
        ).fetchone()
        is None
    )


def test_a03_original_issuer_can_delegate_bind_and_replay_without_extending_ttl(seeded):
    authority_store, mission = seeded
    issuer = _api(authority_store)
    grant = issuer.issue(
        mission, command_id="delegate-a03", planner_principal_id="planner-service"
    )
    issuer.bind_request("req-auth", grant_id=grant.grant_id)
    before = authority_store.connection.total_changes
    assert (
        issuer.issue(mission, command_id="delegate-a03", planner_principal_id="planner-service")
        == grant
    )
    assert authority_store.connection.total_changes == before
    assert (
        authority_store.connection.execute(
            "SELECT planner_principal_id FROM planning_request_authority_bindings "
            "WHERE request_id=?",
            ("req-auth",),
        ).fetchone()[0]
        == "planner-service"
    )
    renewed = issuer.renew(grant.grant_id, expected_revision=1, command_id="renew-a03")
    before = authority_store.connection.total_changes
    assert issuer.renew(grant.grant_id, expected_revision=1, command_id="renew-a03") == renewed
    assert authority_store.connection.total_changes == before


def test_a03_transaction_replay_rechecks_issuer_after_stale_api_lookup(
    seeded, monkeypatch
):
    authority_store, mission = seeded
    from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore

    original = _api(authority_store).issue(
        mission, command_id="concurrent-issue", planner_principal_id="planner-service"
    )
    # Emulate a competing issue arriving after both pre-transaction lookups.
    # The final put_grant transaction still reads the real durable row.
    monkeypatch.setattr(PlanningAdmissionStore, "get_grant_by_command", lambda *a: None)
    monkeypatch.setattr(PlanningAdmissionStore, "get_grant", lambda *a: None)
    before = authority_store.connection.total_changes
    with pytest.raises(StoreConflict) as refused:
        _api(authority_store, principal="foreign-host").issue(
            mission, command_id="concurrent-issue", planner_principal_id="planner-service"
        )
    assert original.grant_id not in str(refused.value)
    assert original.issuer_receipt_hash not in str(refused.value)
    assert authority_store.connection.total_changes == before


@pytest.mark.parametrize("foreign_tenant", ("tenant-a", "tenant-foreign"))
@pytest.mark.parametrize("entry", ("issue", "bind"))
def test_a03_foreign_mission_request_cannot_receive_this_missions_grant(
    tmp_path, foreign_tenant, entry
):
    # The same tenant's other Mission is created the product way next to this one; another
    # tenant's Mission cannot be (a deployment is single tenant), so it is written straight in.
    root = tmp_path / "product"
    keys = ("m-auth", "other") if foreign_tenant == TENANT else ("m-auth",)
    missions = seed_product_missions(root, *keys)
    authority_store = Store.open(product_db(root))
    mission = missions["m-auth"]
    if foreign_tenant == TENANT:
        other = missions["other"]
    else:
        other = "other-mission"
        authority_store.insert_mission(
            foreign_tenant_mission(other, foreign_tenant), spec_hash="b" * 64
        )
        authority_store.connection.execute(
            "INSERT INTO mission_planning_protocols SELECT ?,protocol_version,"
            "package_version,prompt_version,binding_hash,created_at "
            "FROM mission_planning_protocols WHERE mission_id=?",
            (other, mission),
        )
    PlanningDecisionStore(authority_store).insert_planning_request(
        _request(authority_store, other, request_id="other-request", intent_id="other-intent")
    )
    api = _api(authority_store)
    grant = api.issue(mission, command_id="valid-grant") if entry == "bind" else None
    before = authority_store.connection.total_changes
    with pytest.raises(StoreConflict) as refused:
        if entry == "issue":
            api.issue(mission, command_id="cross-mission", request_id="other-request")
        else:
            api.bind_request("other-request", grant_id=grant.grant_id)
    assert other not in str(refused.value)
    assert authority_store.connection.total_changes == before
    assert (
        authority_store.connection.execute(
            "SELECT 1 FROM planning_request_authority_bindings WHERE request_id=?",
            ("other-request",),
        ).fetchone()
        is None
    )
    authority_store.close()
