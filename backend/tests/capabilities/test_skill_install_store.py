from __future__ import annotations

import pytest

from deskpet.capabilities.store import (
    CAPABILITY_SCHEMA_SQL,
    CAPABILITY_SCHEMA_V1_SQL,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    CapabilityStoreConflict,
    initialize_capability_database,
)


def _intent(*, status: str = "staging", version: int = 1) -> CapabilitySkillInstallIntent:
    return CapabilitySkillInstallIntent(
        intent_id="si-1", effect_id="effect-1", call_id="call-1",
        root_run_id="root-1", run_id="run-1", channel="chat",
        project_scope_key="project:v1:p1:r1", principal_id="user-1",
        source={"url": "https://github.com/acme/skills"}, exact_commit="a" * 40,
        archive_hash="archive", raw_tree_hash="tree", member_set_stamp="set-1",
        permission_set_hash="permissions", confirmation_nonce="nonce-1",
        confirmation_version=1, expires_at=200.0, status=status,
        state_version=version, settlement_ref=None, cleanup_ref=None,
        verification_ref=None, error=None, created_at=100.0, updated_at=100.0,
    )


def _members() -> tuple[CapabilitySkillInstallMember, ...]:
    return tuple(
        CapabilitySkillInstallMember(
            intent_id="si-1", ordinal=i, normalized_name=name, pack_id=name,
            version="1.0.0", manifest_hash=f"manifest-{i}", content_hash=f"content-{i}",
            source_digest=f"source-{i}", member={"name": name},
        )
        for i, name in enumerate(("alpha", "beta"))
    )


def test_v1_contract_is_frozen_and_v2_is_a_superset() -> None:
    assert "skill_install_batch" not in CAPABILITY_SCHEMA_V1_SQL
    assert "batch_files_materialized" not in CAPABILITY_SCHEMA_V1_SQL
    assert "skill_install_batch" in CAPABILITY_SCHEMA_SQL
    assert "batch_files_materialized" in CAPABILITY_SCHEMA_SQL
    assert "capability_skill_install_intents" in CAPABILITY_SCHEMA_SQL


@pytest.mark.asyncio
async def test_install_intent_members_are_immutable_and_state_is_cas(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 101.0)
    intent = _intent()
    assert await store.create_skill_install_intent(intent, _members()) == intent
    assert await store.create_skill_install_intent(intent, _members()) == intent
    assert [m.normalized_name for m in await store.skill_install_members("si-1")] == ["alpha", "beta"]

    awaiting = await store.cas_skill_install_intent(
        "si-1", expected_state_version=1, status="awaiting_confirmation"
    )
    assert (awaiting.status, awaiting.state_version) == ("awaiting_confirmation", 2)
    bound = await store.bind_skill_install_confirmation(
        "si-1", expected_state_version=2,
        confirmation_nonce="sdk-final", confirmation_version=4,
    )
    assert (bound.confirmation_nonce, bound.confirmation_version, bound.state_version) == (
        "sdk-final", 4, 3
    )
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.cas_skill_install_intent(
            "si-1", expected_state_version=1, status="denied_cleanup_pending"
        )
    assert caught.value.code == "skill_install_intent_cas_conflict"
    with pytest.raises(CapabilityStoreConflict) as invalid:
        await store.cas_skill_install_intent(
            "si-1", expected_state_version=3, status="succeeded"
        )
    assert invalid.value.code == "skill_install_intent_transition_conflict"


@pytest.mark.asyncio
async def test_install_intent_accepts_sdk_initial_decision_version_zero(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 101.0)
    await store.create_skill_install_intent(_intent(), _members())
    awaiting = await store.cas_skill_install_intent(
        "si-1", expected_state_version=1, status="awaiting_confirmation"
    )

    bound = await store.bind_skill_install_confirmation(
        "si-1",
        expected_state_version=awaiting.state_version,
        confirmation_nonce="sdk-initial",
        confirmation_version=0,
    )

    assert (bound.confirmation_nonce, bound.confirmation_version) == (
        "sdk-initial",
        0,
    )


@pytest.mark.asyncio
async def test_confirmation_handoff_creates_one_exact_batch(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 101.0)
    await store.create_skill_install_intent(_intent(), _members())
    await store.cas_skill_install_intent(
        "si-1", expected_state_version=1, status="awaiting_confirmation"
    )
    handoff = await store.handoff_skill_install_intent(
        "si-1", expected_state_version=2, operation_id="op-1",
        idempotency_key="install:set-1", confirmation_receipt_hash="receipt-1",
    )
    assert handoff.member_set_stamp == "set-1"
    operation = await store.get_operation("op-1")
    assert operation is not None
    assert (operation.kind, operation.phase) == ("skill_install_batch", "batch_staged")
    assert [m.normalized_name for m in await store.operation_members("op-1")] == ["alpha", "beta"]
    assert (await store.get_skill_install_intent("si-1")).status == "publishing"  # type: ignore[union-attr]
