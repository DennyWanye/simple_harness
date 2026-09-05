"""Actual Host action admission/reopen; no Memory mutation or Provider fixture."""

import sqlite3
from dataclasses import replace

import pytest
from deskpet.memory.human_memory_service import (
    AppendPrimaryEventRequest,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.primary_cognitive_evidence import (
    CognitiveActionEvidenceError,
    CognitiveActionEvidenceStore,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.task_scope.protocol import canonical_hash

PAYLOAD = {
    "memory_id": "memory-selected",
    "expected_revision": 2,
    "expected_content_hash": "a" * 64,
}


async def fixture(tmp_path):
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    auth = local_owner_auth()
    service = HumanMemoryHostServiceFactory(path, startup).bind(auth)
    await service.open_primary()
    return path, auth, service, CognitiveActionEvidenceStore(path, auth=auth)


@pytest.mark.asyncio
async def test_action_timestamp_exact_replay_and_reopen_without_foreground_or_memory_job(
    tmp_path,
):
    path, auth, _, store = await fixture(tmp_path)
    assert await store.find_action(payload=PAYLOAD, idempotency_key="ui-action") is None
    first = await store.admit_action(payload=PAYLOAD, idempotency_key="ui-action")
    assert first.committed_at > 0
    assert first.suppression_request_id == "primary-forget:" + first.evidence_id
    reopened = CognitiveActionEvidenceStore(path, auth=auth)
    assert (
        await reopened.find_action(payload=PAYLOAD, idempotency_key="ui-action")
        == first
    )
    assert (
        await reopened.admit_action(payload=PAYLOAD, idempotency_key="ui-action")
        == first
    )
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 1
        )
        assert db.execute("SELECT COUNT(*) FROM foreground_runs").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox").fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_action_key_cannot_change_target_or_expected_revision(tmp_path):
    _, _, _, store = await fixture(tmp_path)
    first = await store.admit_action(payload=PAYLOAD, idempotency_key="ui-action")
    for changed in (
        {**PAYLOAD, "memory_id": "other-memory"},
        {**PAYLOAD, "expected_revision": 3},
    ):
        for operation in (store.find_action, store.admit_action):
            with pytest.raises(
                CognitiveActionEvidenceError, match="primary_memory_action_conflict"
            ):
                await operation(payload=changed, idempotency_key="ui-action")
    assert (
        await store.find_action(payload=PAYLOAD, idempotency_key="ui-action") == first
    )


@pytest.mark.asyncio
async def test_generic_primary_append_cannot_forge_prevalidated_action_domain(tmp_path):
    path, auth, service, store = await fixture(tmp_path)
    digest = canonical_hash({"subject": auth.subject, "idempotency_key": "ui-action"})
    # Deliberately collide with the deterministic action ID through the existing
    # generic endpoint; its fixed source_ref still identifies a different domain.
    await service.append_primary_event(
        AppendPrimaryEventRequest(
            {"schema": "primary-memory-forget/v1", **PAYLOAD},
            "cognitive-forget:" + digest,
        )
    )
    for operation in (store.find_action, store.admit_action):
        with pytest.raises(
            CognitiveActionEvidenceError, match="primary_memory_action_conflict"
        ):
            await operation(payload=PAYLOAD, idempotency_key="ui-action")
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 1
        )


@pytest.mark.asyncio
async def test_action_is_subject_bound_and_invalid_input_never_admits(tmp_path):
    path, auth, _, store = await fixture(tmp_path)
    original = await store.admit_action(payload=PAYLOAD, idempotency_key="ui-action")
    foreign = CognitiveActionEvidenceStore(
        path, auth=replace(auth, subject="other-owner")
    )
    assert (
        await foreign.find_action(payload=PAYLOAD, idempotency_key="ui-action") is None
    )
    with pytest.raises(
        CognitiveActionEvidenceError, match="primary_memory_action_invalid"
    ):
        await store.admit_action(
            payload={**PAYLOAD, "expected_revision": True}, idempotency_key="invalid"
        )
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 1
        )
    assert (
        await store.find_action(payload=PAYLOAD, idempotency_key="ui-action")
        == original
    )
