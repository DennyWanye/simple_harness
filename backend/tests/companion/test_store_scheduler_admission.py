from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    CompanionConflictError,
    CompanionStateError,
    GrowthEvent,
)
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.store import CompanionStore


def _owner_store(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="relay:alice",
    )
    return store, owner


def _proposal(owner, event_id: str) -> StructuredGrowthProposalV1:
    evidence_hash = evidence_set_hash((event_id,))
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=(event_id,),
        evidence_set_hash=evidence_hash,
        target=GrowthTargetIdentityV1(
            kind="skill",
            target_id="summarize-day",
            stable_name="summarize-day",
            pack_id="core.summarize-day",
        ),
        candidate_mode="builtin_override",
        source_fence=CandidateBindingFenceV1(
            owner_key="builtin",
            scope="builtin",
            scope_key="global",
            pack_id="core.summarize-day",
            expected_absent=False,
            binding_generation=1,
            version="1.0.0",
            manifest_hash="a" * 64,
        ),
        target_fence=CandidateBindingFenceV1(
            owner_key=(
                f"companion:{owner.profile_id}:{owner.profile_generation}"
            ),
            scope="user",
            scope_key=owner.profile_id,
            pack_id="core.summarize-day",
            expected_absent=True,
            binding_generation=0,
        ),
        hypothesis="A shorter summary is easier to act on.",
        structured_diff={"instructions": {"replace": ["summary contract"]}},
        expected_improvement="The summary follows the corrected format.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "daily-summary-contract"},),
    )


def test_reflection_decision_admits_and_claims_one_owner_fenced_build(
    tmp_path,
) -> None:
    store, owner = _owner_store(tmp_path)
    event_id = "message-event-1"
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id=event_id,
            source_kind="message_ingress",
            source_ref="message:1",
            context_key="session:1",
            root_run_id="run:1",
            reason_code="explicit_correction",
            payload={"message_ref": "message:1"},
        )
    )
    store.enqueue_job(
        owner,
        job_id="reflection-job-1",
        kind="reflection",
        dedupe_key="reflection:message:1",
        payload={"event_id": event_id},
    )
    assert store.claim_job(
        owner,
        claim_owner="reflector-1",
        lease_seconds=60,
        kinds=("reflection",),
    ) is not None
    proposal = _proposal(owner, event_id)

    admitted = store.admit_reflection_decision(
        owner,
        job_id="reflection-job-1",
        decision_id="reflection-decision-1",
        decision="candidate",
        reason_code="explicit_user_correction",
        evidence_event_ids=(event_id,),
        source_ref="reflection-job-1:result",
        proposal_ref="reflection-job-1:proposal",
        proposal=proposal,
    )
    replay = store.admit_reflection_decision(
        owner,
        job_id="reflection-job-1",
        decision_id="reflection-decision-1",
        decision="candidate",
        reason_code="explicit_user_correction",
        evidence_event_ids=(event_id,),
        source_ref="reflection-job-1:result",
        proposal_ref="reflection-job-1:proposal",
        proposal=proposal,
    )

    build = admitted["candidate_build"]
    assert build is not None
    assert build["status"] == "proposed"
    assert build["candidate_mode"] == "builtin_override"
    recovery = store.get_candidate_build_recovery(
        owner,
        build_id=build["build_id"],
    )
    assert recovery == {
        "build_id": build["build_id"],
        "status": "proposed",
        "candidate_ref": None,
    }
    assert replay["candidate_build"]["build_id"] == build["build_id"]
    assert (
        store.get_next_candidate_build(owner)["build_id"]
        == build["build_id"]
    )
    permit = store.claim_next_candidate_build(
        owner,
        claim_owner="candidate-scheduler-1",
        lease_seconds=60,
    )
    assert permit is not None
    assert permit.build_id == build["build_id"]
    assert store.claim_next_candidate_build(
        owner,
        claim_owner="candidate-scheduler-1",
        lease_seconds=60,
    ) == permit

    other = store.create_profile(
        profile_id="bob",
        generation=1,
        identity_namespace_hash="relay:bob",
    )
    assert store.get_next_candidate_build(other) is None
    with pytest.raises(
        CompanionStateError, match="candidate_build_missing"
    ):
        store.issue_for_claim(
            build_id=build["build_id"],
            claim_owner="candidate-scheduler-2",
            claim_epoch=permit.lease_epoch + 1,
            owner=other,
        )


def test_exact_build_claim_does_not_steal_an_older_expired_build(
    tmp_path,
) -> None:
    clock = [datetime(2026, 7, 26, 2, 0, tzinfo=UTC)]
    store = CompanionStore(
        tmp_path / "companion.db",
        clock=lambda: clock[0],
    )
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="relay:alice",
    )

    def admit(index: int):
        event_id = f"message-event-{index}"
        job_id = f"reflection-job-{index}"
        store.record_growth_event(
            GrowthEvent(
                owner=owner,
                event_id=event_id,
                source_kind="message_ingress",
                source_ref=f"message:{index}",
                context_key="session:1",
                root_run_id=f"run:{index}",
                reason_code="explicit_correction",
                payload={"message_ref": f"message:{index}"},
            )
        )
        store.enqueue_job(
            owner,
            job_id=job_id,
            kind="reflection",
            dedupe_key=f"reflection:message:{index}",
            payload={"event_id": event_id},
        )
        assert store.claim_job(
            owner,
            claim_owner=f"reflector-{index}",
            lease_seconds=60,
            kinds=("reflection",),
        ) is not None
        return store.admit_reflection_decision(
            owner,
            job_id=job_id,
            decision_id=f"reflection-decision-{index}",
            decision="candidate",
            reason_code="explicit_user_correction",
            evidence_event_ids=(event_id,),
            source_ref=f"{job_id}:result",
            proposal_ref=f"{job_id}:proposal",
            proposal=_proposal(owner, event_id),
        )["candidate_build"]

    older = admit(1)
    assert older is not None
    old_permit = store.claim_next_candidate_build(
        owner,
        claim_owner="old-builder",
        lease_seconds=1,
    )
    assert old_permit is not None
    assert old_permit.build_id == older["build_id"]

    clock[0] += timedelta(seconds=2)
    requested = admit(2)
    assert requested is not None
    exact = store.claim_next_candidate_build(
        owner,
        claim_owner=f"growth-builder:{requested['build_id']}",
        lease_seconds=60,
        build_id=requested["build_id"],
    )
    assert exact is not None
    assert exact.build_id == requested["build_id"]
    with store.read() as db:
        older_after = db.execute(
            """SELECT claim_owner,claim_epoch,attempt
               FROM candidate_builds
               WHERE profile_id=? AND profile_generation=? AND build_id=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                older["build_id"],
            ),
        ).fetchone()
    assert older_after is not None
    assert tuple(older_after) == ("old-builder", 1, 1)


def _create_candidate(store, owner) -> None:
    event_id = "candidate-event-1"
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id=event_id,
            source_kind="message_ingress",
            source_ref="message:1",
            context_key="session:1",
            root_run_id="run:1",
            reason_code="explicit_correction",
            payload={"message_ref": "message:1"},
        )
    )
    store.create_growth_target(
        owner,
        target_id="target-1",
        kind="skill",
        stable_name="summarize-day",
        pack_id="personal.summarize-day",
    )
    payload = b"# Summarize day\n"
    digest = hashlib.sha256(payload).hexdigest()
    store.create_candidate(
        owner,
        CandidatePackage(
            package_id="package-row-1",
            candidate_mode=CandidateMode.GENESIS,
            pack_id="personal.summarize-day",
            version="1.0.0",
            candidate_content_hash="content-1",
            candidate_manifest_hash="manifest-1",
            candidate_package_hash="package-1",
            archive_hash="archive-1",
            effect_topology_hash="readonly-effects",
            source_facts={},
            target_facts={"expected_absent": True},
            files=(
                CandidatePackageFile(
                    "SKILL.md", "instruction", digest, len(payload), "skill"
                ),
            ),
            blobs=(
                CandidatePackageBlob("file", "skill", digest, payload),
            ),
        ),
        CandidateAttempt(
            candidate_id="candidate-1",
            candidate_attempt_key="attempt-1",
            proposal_source_kind="reflection",
            proposal_source_ref="reflection-1",
            source_hash="reflection-hash-1",
            candidate_mode=CandidateMode.GENESIS,
            target_id="target-1",
            target_owner_key=(
                f"companion:{owner.profile_id}:{owner.profile_generation}"
            ),
            target_scope="user",
            target_scope_key=owner.profile_id,
            target_expected_absent=True,
            target_expected_binding_generation=0,
            evidence_event_ids=(event_id,),
            evidence_set_hash=evidence_set_hash((event_id,)),
            builder_receipt_ref="builder-1",
            builder_receipt_hash="builder-hash-1",
            reservation_version=1,
        ),
    )


def test_evaluation_admission_freezes_run_inputs_and_variants_atomically(
    tmp_path,
) -> None:
    store, owner = _owner_store(tmp_path)
    _create_candidate(store, owner)
    evaluation = {
        "evaluation_id": "evaluation-1",
        "candidate_id": "candidate-1",
        "candidate_mode": "genesis",
        "candidate_package_hash": "package-1",
        "candidate_manifest_hash": "manifest-1",
        "candidate_archive_hash": "archive-1",
        "suite_hash": "suite-1",
        "attempt_key": "attempt-1",
        "baseline_kind": "capability_absent_v1",
        "old_snapshot_hash": "absent-snapshot-1",
        "candidate_snapshot_hash": "candidate-snapshot-1",
        "absent_baseline_ref": "capability-absent-v1",
        "absent_baseline_hash": "absent-hash-1",
        "runner_id": "local-evaluator-v1",
        "runner_policy_hash": "runner-policy-1",
        "provider_id": "provider-1",
        "model_id": "model-1",
    }
    case_inputs = (
        {
            "input_id": "input-1",
            "case_id": "case-1",
            "source_kind": "packaged_suite",
            "resource_ref": "suite/case-1.json",
            "resource_hash": "resource-hash-1",
            "input_envelope": {"prompt": "summarize"},
            "adapter_id": "fixture-adapter",
            "adapter_version": "1",
            "adapter_build_fingerprint": "adapter-build-1",
            "assertion_ref": "assertion-1",
            "assertion_hash": "assertion-hash-1",
            "read_tool_fixture": {"memory": []},
            "evaluation_tool_adapter_map": {},
        },
    )
    cases = tuple(
        {
            "case_id": "case-1",
            "variant": variant,
            "input_id": "input-1",
            "manifest_case_version": "1",
            "blind_label": f"blind-{variant}",
            "expected_kind": "summary",
        }
        for variant in ("old", "candidate")
    )

    admitted = store.admit_evaluation_experiment(
        owner,
        evaluation=evaluation,
        case_inputs=case_inputs,
        cases=cases,
    )
    replay = store.admit_evaluation_experiment(
        owner,
        evaluation=evaluation,
        case_inputs=case_inputs,
        cases=cases,
    )

    assert admitted["evaluation"]["status"] == "queued"
    assert admitted["input_count"] == 1
    assert admitted["case_count"] == 2
    assert replay["evaluation"]["evaluation_id"] == "evaluation-1"
    with store.read() as db:
        candidate = db.execute(
            """SELECT status FROM candidate_artifacts
               WHERE profile_id=? AND profile_generation=?
                 AND candidate_id='candidate-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        inputs = db.execute(
            "SELECT input_hash FROM evaluation_case_inputs"
        ).fetchall()
        variants = db.execute(
            """SELECT variant,input_hash FROM evaluation_cases
               ORDER BY variant"""
        ).fetchall()
    assert candidate["status"] == "evaluating"
    assert len(inputs) == 1
    assert {row["input_hash"] for row in variants} == {
        inputs[0]["input_hash"]
    }

    drift = dict(evaluation)
    drift["candidate_snapshot_hash"] = "drift"
    with pytest.raises(
        CompanionConflictError, match="evaluation_experiment_conflict"
    ):
        store.admit_evaluation_experiment(
            owner,
            evaluation=drift,
            case_inputs=case_inputs,
            cases=cases,
        )
