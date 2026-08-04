from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    CompanionConflictError,
    CompanionLeaseError,
    CompanionOwnerError,
    CompanionStateError,
    GrowthDependency,
    GrowthEvent,
    MutationAction,
    MutationRequest,
    OwnerRef,
    RunGrowthSnapshot,
)
from deskpet.companion.store import CompanionStore, canonical_hash


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 24, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def owner_store(tmp_path):
    clock = MutableClock()
    store = CompanionStore(tmp_path / "companion.db", clock=clock)
    owner = store.create_profile(
        profile_id="alice", generation=1, identity_namespace_hash="relay:alice"
    )
    return store, owner, clock


def event(owner: OwnerRef, event_id: str, value: str = "value") -> GrowthEvent:
    return GrowthEvent(
        owner=owner,
        event_id=event_id,
        source_kind="tool.outcome",
        source_ref=f"run:{event_id}",
        context_key=f"context:{event_id}",
        root_run_id="root",
        reason_code="verified_outcome",
        payload={"value": value},
    )


def package_and_attempt(
    owner: OwnerRef,
    *,
    candidate_id: str,
    source_ref: str,
    attempt_key: str,
    evidence_ids: tuple[str, ...],
    evidence_hash: str,
) -> tuple[CandidatePackage, CandidateAttempt]:
    payload = b"# Summarize day\n"
    digest = hashlib.sha256(payload).hexdigest()
    package = CandidatePackage(
        package_id="pkg-1",
        candidate_mode=CandidateMode.GENESIS,
        pack_id="personal.summarize-day",
        version="1.0.0",
        candidate_content_hash="content-1",
        candidate_manifest_hash="manifest-1",
        candidate_package_hash="package-1",
        archive_hash="archive-1",
        effect_topology_hash="effects-readonly",
        source_facts={},
        target_facts={"expected_absent": True},
        files=(
            CandidatePackageFile(
                relative_path="SKILL.md",
                file_kind="instruction",
                content_hash=digest,
                size_bytes=len(payload),
                blob_id="skill",
            ),
        ),
        blobs=(
            CandidatePackageBlob(
                blob_kind="file",
                blob_id="skill",
                content_hash=digest,
                payload=payload,
            ),
        ),
    )
    attempt = CandidateAttempt(
        candidate_id=candidate_id,
        candidate_attempt_key=attempt_key,
        proposal_source_kind="reflection",
        proposal_source_ref=source_ref,
        source_hash=f"source:{source_ref}",
        candidate_mode=CandidateMode.GENESIS,
        target_id="target-1",
        target_owner_key=f"companion:{owner.profile_id}:{owner.profile_generation}",
        target_scope="user",
        target_scope_key="profile",
        target_expected_absent=True,
        target_expected_binding_generation=0,
        evidence_event_ids=evidence_ids,
        evidence_set_hash=evidence_hash,
        builder_receipt_ref=f"builder:{candidate_id}",
        builder_receipt_hash=f"builder-hash:{candidate_id}",
        reservation_version=1,
    )
    return package, attempt


def test_growth_event_insert_or_verify_and_detail_version(owner_store) -> None:
    store, owner, _ = owner_store
    before = store.get_detail_version(owner)["detail_version"]
    first = store.record_growth_event(event(owner, "event-1"))
    after = store.get_detail_version(owner)["detail_version"]
    replay = store.record_growth_event(event(owner, "event-1"))

    assert first["event_hash"] == replay["event_hash"]
    assert after == before + 1
    assert store.get_detail_version(owner)["detail_version"] == after
    with pytest.raises(CompanionConflictError, match="growth_event_conflict"):
        store.record_growth_event(event(owner, "event-1", "different"))


def test_owner_generation_is_enforced_in_sql_scope(owner_store) -> None:
    store, owner, _ = owner_store
    store.record_growth_event(event(owner, "event-1"))
    store.create_profile(
        profile_id="alice", generation=2, identity_namespace_hash="relay:alice-v2"
    )
    stale = GrowthEvent(
        **{
            **asdict(event(owner, "stale")),
            "owner": OwnerRef("alice", 3),
        }
    )
    with pytest.raises(CompanionOwnerError):
        store.record_growth_event(stale)
    with store.read() as db:
        assert (
            db.execute(
                """SELECT count(*) FROM growth_events
                   WHERE profile_id='alice' AND profile_generation=2"""
            ).fetchone()[0]
            == 0
        )


def test_candidate_package_bytes_are_shared_but_attempt_lineage_is_not(owner_store) -> None:
    store, owner, _ = owner_store
    store.record_growth_event(event(owner, "event-1"))
    store.record_growth_event(event(owner, "event-2"))
    store.create_growth_target(
        owner,
        target_id="target-1",
        kind="skill",
        stable_name="summarize-day",
        pack_id="personal.summarize-day",
    )
    package, first = package_and_attempt(
        owner,
        candidate_id="candidate-1",
        source_ref="reflection-1",
        attempt_key="attempt-1",
        evidence_ids=("event-1",),
        evidence_hash="evidence-1",
    )
    assert store.create_candidate(owner, package, first)["candidate_id"] == "candidate-1"
    assert store.create_candidate(owner, package, first)["candidate_id"] == "candidate-1"

    package, second = package_and_attempt(
        owner,
        candidate_id="candidate-2",
        source_ref="reflection-2",
        attempt_key="attempt-2",
        evidence_ids=("event-1", "event-2"),
        evidence_hash="evidence-2",
    )
    # The original genesis reservation stays owned by candidate-1. Releasing it
    # is a state transition; a same-target second attempt cannot race it.
    with pytest.raises(CompanionStateError, match="genesis_target_reserved"):
        store.create_candidate(owner, package, second)

    store.transition_candidate(
        owner,
        candidate_id="candidate-1",
        expected_status="proposed",
        next_status="invalidated",
        reason_code="new_evidence",
    )
    second = replace(second, reservation_version=2)
    assert store.create_candidate(owner, package, second)["candidate_id"] == "candidate-2"
    with store.read() as db:
        assert db.execute("SELECT count(*) FROM candidate_packages").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM candidate_package_files").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM candidate_artifacts").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM candidate_package_sources").fetchone()[0] == 2


def test_candidate_hash_or_blob_conflict_rolls_back_whole_attempt(owner_store) -> None:
    store, owner, _ = owner_store
    store.record_growth_event(event(owner, "event-1"))
    store.create_growth_target(
        owner,
        target_id="target-1",
        kind="skill",
        stable_name="summarize-day",
        pack_id="personal.summarize-day",
    )
    package, attempt = package_and_attempt(
        owner,
        candidate_id="candidate-1",
        source_ref="reflection-1",
        attempt_key="attempt-1",
        evidence_ids=("event-1",),
        evidence_hash="evidence-1",
    )
    bad_blob = CandidatePackageBlob(
        blob_kind="file", blob_id="skill", content_hash="not-sha", payload=b"different"
    )
    bad_package = replace(package, blobs=(bad_blob,))
    with pytest.raises(CompanionConflictError, match="candidate_blob_hash_mismatch"):
        store.create_candidate(owner, bad_package, attempt)
    with store.read() as db:
        assert db.execute("SELECT count(*) FROM candidate_packages").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM candidate_artifacts").fetchone()[0] == 0


def test_job_and_outbox_lease_epoch_reject_stale_settle(owner_store) -> None:
    store, owner, clock = owner_store
    store.enqueue_job(
        owner, job_id="job-1", kind="reflection", dedupe_key="day", payload={"day": "today"}
    )
    first = store.claim_job(owner, claim_owner="worker-a", lease_seconds=5)
    assert first is not None
    clock.advance(6)
    second = store.claim_job(owner, claim_owner="worker-b", lease_seconds=5)
    assert second is not None and second.claim_epoch == first.claim_epoch + 1
    with pytest.raises(CompanionLeaseError):
        store.settle_job(
            owner,
            job_id="job-1",
            claim_owner="worker-a",
            claim_epoch=first.claim_epoch,
            status="succeeded",
            result_ref="r",
            result_hash="h",
            reason_code="done",
        )
    settled = store.settle_job(
        owner,
        job_id="job-1",
        claim_owner="worker-b",
        claim_epoch=second.claim_epoch,
        status="succeeded",
        result_ref="r",
        result_hash="h",
        reason_code="done",
    )
    assert settled["status"] == "succeeded"

    store.enqueue_outbox(
        owner,
        outbox_id="out-1",
        event_kind="notice",
        event_id="notice-1",
        sink_kind="session",
        payload={"notification_id": "notice-1"},
    )
    claim = store.claim_outbox(owner, claim_owner="projector", lease_seconds=5)
    assert claim is not None
    assert (
        store.settle_outbox(
            owner,
            outbox_id="out-1",
            claim_owner="projector",
            claim_epoch=claim.claim_epoch,
            delivered=True,
            result_hash="projected",
            reason_code="done",
        )["status"]
        == "delivered"
    )


def test_snapshot_bind_and_forget_revokes_every_reverse_index(owner_store) -> None:
    store, owner, _ = owner_store
    store.record_growth_event(event(owner, "event-1"))
    dependency = GrowthDependency(
        dependency_kind="preference",
        dependency_id="tone",
        content_hash="preference-hash",
        evidence_event_ids=("event-1",),
    )
    payload = {
        "schema_version": 1,
        "profile_id": owner.profile_id,
        "profile_generation": owner.profile_generation,
        "snapshot_id": "snapshot-1",
        "request_id": "request-1",
        "run_id": "run-1",
        "snapshot_generation": 0,
        "prior_snapshot_id": None,
        "prior_snapshot_hash": None,
        "dependencies": [asdict(dependency)],
    }
    snapshot = RunGrowthSnapshot(
        snapshot_id="snapshot-1",
        request_id="request-1",
        run_id="run-1",
        snapshot_generation=0,
        snapshot_hash=canonical_hash(payload),
    )
    store.create_run_growth_snapshot(owner, snapshot, [dependency])
    store.activate_run_binding(
        owner,
        run_id="run-1",
        request_id="request-1",
        root_run_id="root-1",
        snapshot_id="snapshot-1",
        snapshot_hash=snapshot.snapshot_hash,
        snapshot_generation=0,
        all_generations_root_hash="root-hash",
        start_fingerprint="start-hash",
    )
    assert store.forget_growth_event(owner, event_id="event-1", reason_code="user_forget")
    assert not store.forget_growth_event(owner, event_id="event-1", reason_code="user_forget")
    with store.read() as db:
        assert db.execute("SELECT content_state FROM growth_events").fetchone()[0] == "tombstoned"
        assert db.execute("SELECT status FROM run_growth_snapshots").fetchone()[0] == "revoked"
        assert db.execute("SELECT status FROM companion_run_bindings").fetchone()[0] == "revoked"


def test_reminder_mutation_receipt_is_atomic_and_idempotent(owner_store) -> None:
    store, owner, _ = owner_store
    args = {"title": "stand up", "at": "2026-07-25T09:00:00+08:00"}
    request_hash = canonical_hash(args)
    first = store.mutate_reminder(
        owner,
        effect_id="effect-1",
        operation_kind="create",
        args=args,
        request_hash=request_hash,
        reminder_id="reminder-deterministic",
        before_schedule_version=None,
        schedule={"at": args["at"]},
        timezone="Asia/Shanghai",
        quiet_policy={"mode": "defer"},
        result_payload_ref="result:create",
        result_hash="result-hash",
        reason_code="user_request",
    )
    replay = store.mutate_reminder(
        owner,
        effect_id="effect-1",
        operation_kind="create",
        args=args,
        request_hash=request_hash,
        reminder_id="reminder-deterministic",
        before_schedule_version=None,
        schedule={"at": args["at"]},
        timezone="Asia/Shanghai",
        quiet_policy={"mode": "defer"},
        result_payload_ref="result:create",
        result_hash="result-hash",
        reason_code="user_request",
    )
    assert first["result_hash"] == replay["result_hash"]
    with pytest.raises(CompanionConflictError):
        store.mutate_reminder(
            owner,
            effect_id="effect-1",
            operation_kind="create",
            args={"title": "different"},
            request_hash=canonical_hash({"title": "different"}),
            reminder_id="reminder-deterministic",
            before_schedule_version=None,
            schedule={},
            timezone="Asia/Shanghai",
            quiet_policy={},
            result_payload_ref="result:create",
            result_hash="different",
            reason_code="user_request",
        )


def test_action_discriminated_mutation_request_reserves_target_atomically(owner_store) -> None:
    store, owner, _ = owner_store
    request = MutationRequest(
        request_id="mutation-1",
        request_fingerprint="fingerprint-1",
        action=MutationAction.DISABLE,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        target_expected_binding_generation=3,
        reason_code="forgotten_without_fallback",
        cause_ref="forget:event-1",
    )
    first = store.create_capability_mutation_request(owner, request)
    replay = store.create_capability_mutation_request(owner, request)
    assert first["activation_request_id"] == replay["activation_request_id"]

    competing = MutationRequest(
        request_id="mutation-2",
        request_fingerprint="fingerprint-2",
        action=MutationAction.UNINSTALL,
        target_owner_key=request.target_owner_key,
        target_scope=request.target_scope,
        target_scope_key=request.target_scope_key,
        pack_id=request.pack_id,
        target_expected_binding_generation=3,
        reason_code="profile_deleted",
        cause_ref="delete:alice:1",
    )
    with pytest.raises(CompanionStateError, match="capability_mutation_in_progress:mutation-1"):
        store.create_capability_mutation_request(owner, competing)
    with store.read() as db:
        assert db.execute("SELECT count(*) FROM capability_activation_requests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM outbox WHERE event_kind='capability_mutation'"
        ).fetchone()[0] == 1


def test_same_rollback_intent_adopts_legacy_pending_request(owner_store) -> None:
    store, owner, _ = owner_store
    first_request = MutationRequest(
        request_id="legacy-rollback",
        request_fingerprint="legacy-rollback-fingerprint",
        action=MutationAction.ROLLBACK,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        target_expected_binding_generation=3,
        reason_code="trusted_message_panel_rollback",
        rollback_kind="remove_override",
        cause_ref="notification:activation-1",
    )
    first = store.create_capability_mutation_request(owner, first_request)

    refreshed_request = replace(
        first_request,
        request_id="stable-rollback",
        request_fingerprint="stable-rollback-fingerprint",
    )
    adopted = store.create_capability_mutation_request(
        owner,
        refreshed_request,
    )

    assert adopted["activation_request_id"] == first["activation_request_id"]
    with store.read() as db:
        assert db.execute(
            "SELECT count(*) FROM capability_activation_requests"
        ).fetchone()[0] == 1
