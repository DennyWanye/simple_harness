from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    CapabilityGuardIncident,
    CapabilityMutationReceipt,
    CompanionConflictError,
    CompanionLeaseError,
    CompanionStateError,
    EvaluationCaseLaunch,
    EvaluationExecutionPermit,
    GrowthEvent,
    MutationAction,
    MutationRequest,
    OwnerRef,
)
from deskpet.companion.store import (
    SAFE_STATIC_RUNNER_POLICY_HASH,
    CompanionStore,
    canonical_hash,
    canonical_json,
)
from deskpet.companion.evaluation import (
    EvaluationAuthorizationCommandV1,
    EvaluationAuthorizationService,
    EvaluationGateRequestV1,
    EvaluationIdentityV1,
    EvaluationPermitIssuer,
)
from deskpet.companion.activation import ActivationDecisionCoordinator
from deskpet.companion.evaluation_execution import (
    EvaluationCaseCompletionV1,
    EvaluationCaseStartAckV1,
)
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.risk import (
    CapabilityRiskPolicy,
    CandidateRiskInputV1,
    EffectFactV1,
    EffectTopologyDiffV1,
    StaticRiskPreflight,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _permit_hash(**overrides) -> str:
    facts = {
        "schema_version": 1,
        "evaluation_id": "evaluation-1",
        "mode": "safe_auto",
        "candidate_id": "candidate-1",
        "package_hash": "package-1",
        "manifest_hash": "manifest-1",
        "archive_hash": "archive-1",
        "suite_hash": "suite-1",
        "runner_policy_hash": "runner-policy-1",
        "issued_revocation_epoch": 4,
        "preflight_ref": "preflight-1",
        "preflight_hash": "preflight-hash-1",
        "risk_ref": "risk-1",
        "risk_hash": "risk-hash-1",
        "authorization_id": None,
    }
    facts.update(overrides)
    return canonical_hash(facts)


def _launch(
    claim,
    *,
    variant: str,
    launch_id: str,
    claim_owner: str = "worker-1",
) -> tuple[EvaluationCaseLaunch, str]:
    facts = {
        "schema_version": 1,
        "launch_id": launch_id,
        "evaluation_id": "evaluation-1",
        "case_id": "case-1",
        "variant": variant,
        "attempt_ordinal": int(claim["attempt"]),
        "candidate_package_hash": "package-1",
        "candidate_manifest_hash": "manifest-1",
        "candidate_archive_hash": "archive-1",
        "suite_hash": "suite-1",
        "permit_mode": "safe_auto",
        "permit_id": "permit-1",
        "permit_hash": _permit_hash(),
        "case_lease_epoch": int(claim["claim_epoch"]),
        "revocation_epoch": 4,
        "adapter_id": "fixture-adapter",
        "adapter_version": "1",
        "adapter_fingerprint": "adapter-build-1",
    }
    return (
        EvaluationCaseLaunch(
            **facts,
            launch_fingerprint=canonical_hash(facts),
            reason_code="case_launch_claimed",
        ),
        claim_owner,
    )


@pytest.fixture
def evaluation_store(tmp_path):
    clock = MutableClock()
    store = CompanionStore(tmp_path / "companion.db", clock=clock)
    owner = store.create_profile(
        profile_id="alice", generation=1, identity_namespace_hash="relay:alice"
    )
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="event-1",
            source_kind="tool.outcome",
            source_ref="run:1",
            context_key="context:1",
            root_run_id="root-1",
            reason_code="verified",
            payload={"value": "objective evidence"},
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
    package = CandidatePackage(
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
        blobs=(CandidatePackageBlob("file", "skill", digest, payload),),
    )
    attempt = CandidateAttempt(
        candidate_id="candidate-1",
        candidate_attempt_key="attempt-1",
        proposal_source_kind="reflection",
        proposal_source_ref="reflection-1",
        source_hash="reflection-hash-1",
        candidate_mode=CandidateMode.GENESIS,
        target_id="target-1",
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        target_expected_absent=True,
        target_expected_binding_generation=0,
        evidence_event_ids=("event-1",),
        evidence_set_hash="evidence-set-1",
        builder_receipt_ref="builder-1",
        builder_receipt_hash="builder-hash-1",
        reservation_version=1,
    )
    store.create_candidate(owner, package, attempt)
    now = store._now()
    with store._write() as db:
        db.execute(
            """INSERT INTO evaluation_runs(
                 profile_id,profile_generation,evaluation_id,candidate_id,candidate_mode,
                 suite_hash,attempt_key,baseline_kind,old_snapshot_hash,candidate_snapshot_hash,
                 absent_baseline_ref,absent_baseline_hash,runner_id,runner_policy_hash,
                 provider_id,model_id,status,claim_epoch,reason_code,schema_version,
                 created_at,updated_at
               ) VALUES (?,?,?,?,?,'suite-1','eval-attempt-1','capability_absent_v1',
                         'absent-snapshot','candidate-snapshot','absent-v1','absent-hash',
                         'runner-1','runner-policy-1','provider-1','model-1','queued',0,
                         'evaluation_created',1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                "evaluation-1",
                "candidate-1",
                "genesis",
                now,
                now,
            ),
        )
        db.execute(
            """INSERT INTO evaluation_case_inputs(
                 profile_id,profile_generation,evaluation_id,input_id,case_id,source_kind,
                 resource_ref,resource_hash,input_envelope_blob,input_hash,adapter_id,
                 adapter_version,adapter_build_fingerprint,assertion_ref,assertion_hash,
                 read_tool_fixture_blob,read_tool_fixture_root_hash,
                 evaluation_tool_adapter_map_json,content_state,reason_code,schema_version,
                 created_at,updated_at
               ) VALUES (?,?,?,'input-1','case-1','packaged_suite','suite/case-1','resource-hash',
                         ?, 'input-hash','fixture-adapter','1','adapter-build-1',
                         'assertion-1','assertion-hash',?,'fixture-root','{}','live',
                         'input_frozen',1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                "evaluation-1",
                canonical_json({"prompt": "summarize"}),
                canonical_json({"memory": []}),
                now,
                now,
            ),
        )
        for variant in ("old", "candidate"):
            db.execute(
                """INSERT INTO evaluation_cases(
                     profile_id,profile_generation,evaluation_id,case_id,variant,input_id,
                     input_hash,manifest_case_version,blind_label,expected_kind,status,
                     claim_epoch,attempt,recovery_only,reason_code,schema_version,
                     created_at,updated_at
                   ) VALUES (?,?,?,'case-1',?,'input-1','input-hash','1',?,
                             'summary','queued',0,0,0,'case_queued',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    "evaluation-1",
                    variant,
                    f"blind-{variant}",
                    now,
                    now,
                ),
            )
    return store, owner, clock


def _issue_permit(store: CompanionStore, owner: OwnerRef) -> EvaluationExecutionPermit:
    with store._write() as db:
        existing = db.execute(
            """SELECT 1 FROM risk_assessments
               WHERE profile_id=? AND profile_generation=? AND risk_id='risk-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if existing is None:
            db.execute(
                """INSERT INTO risk_assessments(
                     profile_id,profile_generation,risk_id,candidate_id,
                     candidate_package_hash,static_preflight_json,
                     effect_topology_diff_json,risk,risk_hash,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,'risk-1','candidate-1','package-1','{}','{}',
                             'low','risk-hash-1','readonly',1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    store._now(),
                ),
            )
    permit = EvaluationExecutionPermit(
        permit_id="permit-1",
        evaluation_id="evaluation-1",
        mode="safe_auto",
        candidate_id="candidate-1",
        package_hash="package-1",
        manifest_hash="manifest-1",
        archive_hash="archive-1",
        suite_hash="suite-1",
        runner_policy_hash="runner-policy-1",
        issued_revocation_epoch=4,
        preflight_ref="preflight-1",
        preflight_hash="preflight-hash-1",
        risk_ref="risk-1",
        risk_hash="risk-hash-1",
        permit_hash=_permit_hash(),
        reason_code="safe_auto_readonly",
    )
    store.issue_evaluation_execution_permit(owner, permit)
    return permit


def _complete_variant(
    store: CompanionStore,
    owner: OwnerRef,
    *,
    variant: str,
    worker: str,
    launch_id: str,
) -> str:
    claim = store.claim_evaluation_case(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant=variant,
        claim_owner=worker,
        lease_seconds=30,
    )
    launch, _ = _launch(claim, variant=variant, launch_id=launch_id, claim_owner=worker)
    store.create_evaluation_case_launch(owner, launch, claim_owner=worker)
    started = store.settle_evaluation_case_launch(
        owner,
        launch_id=launch_id,
        expected_status="claimed",
        status="started",
        reason_code="started_ack",
    )
    assert started["status"] == "started"
    # Same transition replay is stable and creates no second physical launch.
    assert (
        store.settle_evaluation_case_launch(
            owner,
            launch_id=launch_id,
            expected_status="claimed",
            status="started",
            reason_code="started_ack",
        )["status"]
        == "started"
    )
    store.settle_evaluation_case_launch(
        owner,
        launch_id=launch_id,
        expected_status="started",
        status="completed",
        outcome_ref=f"outcome:{variant}",
        outcome_hash=f"outcome-hash:{variant}",
        reason_code="completed",
    )
    result_payload = {
        "assertions": {"passed": True},
        "judge_result": {"score": 1},
        "usage": {"tokens": 10},
    }
    result_hash = canonical_hash(result_payload)
    store.record_evaluation_result(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant=variant,
        claim_owner=worker,
        claim_epoch=int(claim["claim_epoch"]),
        assertions=result_payload["assertions"],
        judge_result=result_payload["judge_result"],
        usage=result_payload["usage"],
        result_hash=result_hash,
        reason_code="result_committed",
    )
    return result_hash


def test_real_store_evaluation_execution_fence_start_and_completion(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    _issue_permit(store, owner)
    claim = store.claim_evaluation_case(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_owner="real-store-worker",
        lease_seconds=30,
    )
    fence = store.get_evaluation_execution_fence(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
    )
    assert fence["candidate_status"] == "evaluating"
    assert fence["policy_verified"] == 1
    launch, _ = _launch(
        claim,
        variant="old",
        launch_id="real-store-launch",
        claim_owner="real-store-worker",
    )
    store.create_evaluation_case_launch(
        owner, launch, claim_owner="real-store-worker"
    )
    start_facts = {
        "schema_version": 1,
        "launch_id": launch.launch_id,
        "runtime_instance_id": "evaluation:real-store-launch",
        "adapter_identity": "managed-process-test-v1",
        "job_identity": "job-real-store",
        "pid": 4321,
        "process_create_time": 123.5,
        "command_line_hash": "command-real-store",
    }
    ack = EvaluationCaseStartAckV1(
        launch_id=launch.launch_id,
        runtime_instance_id="evaluation:real-store-launch",
        adapter_identity="managed-process-test-v1",
        job_identity="job-real-store",
        pid=4321,
        process_create_time=123.5,
        command_line_hash="command-real-store",
        start_receipt_hash=canonical_hash(start_facts),
    )
    started = store.record_evaluation_case_start_ack(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_owner="real-store-worker",
        claim_epoch=int(claim["claim_epoch"]),
        ack=ack,
        reason_code="start_ack",
    )
    assert started["status"] == "started"
    result_payload = {
        "assertions": {"passed": True},
        "judge_result": {"score": 1},
        "usage": {"tokens": 2},
    }
    completion = EvaluationCaseCompletionV1(
        outcome_ref="outcome:real-store",
        outcome_hash="outcome-hash-real-store",
        assertions=result_payload["assertions"],
        judge_result=result_payload["judge_result"],
        usage=result_payload["usage"],
        result_hash=canonical_hash(result_payload),
        reason_code="completed",
        cleanup_receipt_hash="cleanup-real-store",
        survivor_count=0,
    )
    committed = store.commit_evaluation_case_completion(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_owner="real-store-worker",
        claim_epoch=int(claim["claim_epoch"]),
        launch_id=launch.launch_id,
        completion=completion,
    )
    assert committed["result_hash"] == completion.result_hash
    recovery = store.get_evaluation_case_recovery_state(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_epoch=int(claim["claim_epoch"]),
    )
    assert recovery is not None
    assert recovery["launch_status"] == "completed"
    assert recovery["survivor_count"] == 0


def test_evaluation_permit_case_launch_result_and_report_are_fenced(evaluation_store) -> None:
    store, owner, _ = evaluation_store
    permit = _issue_permit(store, owner)
    assert store.issue_evaluation_execution_permit(owner, permit)["permit_hash"] == permit.permit_hash
    with pytest.raises(CompanionConflictError, match="permit_hash_mismatch"):
        store.issue_evaluation_execution_permit(
            owner, replace(permit, permit_hash="drift")
        )

    old_hash = _complete_variant(
        store, owner, variant="old", worker="worker-old", launch_id="launch-old"
    )
    candidate_hash = _complete_variant(
        store,
        owner,
        variant="candidate",
        worker="worker-candidate",
        launch_id="launch-candidate",
    )
    result_items = [
        {"case_id": "case-1", "variant": "candidate", "result_hash": candidate_hash},
        {"case_id": "case-1", "variant": "old", "result_hash": old_hash},
    ]
    report = store.create_evaluation_report(
        owner,
        report_id="report-1",
        evaluation_id="evaluation-1",
        candidate_package_hash="package-1",
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(("case-1", "old"), ("case-1", "candidate")),
        results_root_hash=canonical_hash(result_items),
        verdict="passed",
        reason_code="all_required_committed",
    )
    assert report["required_case_count"] == 2
    with store.read() as db:
        outbox_payload = json.loads(
            db.execute(
                """SELECT payload_json FROM outbox
                   WHERE profile_id=? AND profile_generation=?
                     AND event_kind='evaluation_reported' AND event_id='report-1'""",
                (owner.profile_id, owner.profile_generation),
            ).fetchone()["payload_json"]
        )
    assert outbox_payload["schema_version"] == 2
    assert outbox_payload["candidate_id"] == "candidate-1"
    assert outbox_payload["pack_id"] == "personal.summarize-day"
    assert outbox_payload["candidate_version"] == "1.0.0"
    assert outbox_payload["evaluation_report_hash"] == canonical_hash(
        result_items
    )
    assert outbox_payload["risk_id"] == "risk-1"
    assert outbox_payload["risk_assessment_hash"] == "risk-hash-1"
    assert outbox_payload["owner_key"] == "companion:alice:1"
    assert outbox_payload["scope"] == "user"
    assert outbox_payload["expected_binding_generation"] == 0
    assert store.create_evaluation_report(
        owner,
        report_id="report-1",
        evaluation_id="evaluation-1",
        candidate_package_hash="package-1",
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(("case-1", "old"), ("case-1", "candidate")),
        results_root_hash=canonical_hash(result_items),
        verdict="passed",
        reason_code="all_required_committed",
    )["report_id"] == "report-1"


def test_failed_report_atomically_invalidates_and_enqueues_genesis_replan(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    _issue_permit(store, owner)
    old_hash = _complete_variant(
        store,
        owner,
        variant="old",
        worker="worker-old",
        launch_id="launch-old",
    )
    candidate_hash = _complete_variant(
        store,
        owner,
        variant="candidate",
        worker="worker-candidate",
        launch_id="launch-candidate",
    )
    result_items = [
        {
            "case_id": "case-1",
            "variant": "candidate",
            "result_hash": candidate_hash,
        },
        {
            "case_id": "case-1",
            "variant": "old",
            "result_hash": old_hash,
        },
    ]
    event = {
        "event_id": "evaluation-replan:event-1",
        "source_kind": "execution_outcome",
        "source_ref": "report-1",
        "context_key": "evaluation:candidate-1",
        "root_run_id": "evaluation-run-1",
        "reason_code": "independent_evaluation_failed_replan",
        "payload": {
            "intent": "fix all evaluator gaps",
            "capability_id": "personal.summarize-day",
            "evaluation_report_id": "report-1",
            "candidate_id": "candidate-1",
            "evaluation_reason_code": "candidate_failed",
            "evaluation_explanation": "missing one required assertion",
            "replan_attempt": 1,
        },
    }
    event_hash = canonical_hash(
        {
            "schema_version": 1,
            **{
                key: event[key]
                for key in (
                    "event_id",
                    "source_kind",
                    "source_ref",
                    "context_key",
                    "root_run_id",
                )
            },
            "retry_of": None,
            "payload": event["payload"],
        }
    )
    job_payload = {
        "purpose": "reflection",
        "owner_key": "companion:alice:1",
        "evidence_ids": [event["event_id"]],
        "capture_growth": False,
        "requires_idle": True,
        "text": "replan from the frozen evaluator explanation",
        "request_payload": {
            "companion_stage": "reflection",
            "growth_signal_ref": event["event_id"],
            "growth_signal_hash": event_hash,
            "semantic_replan_attempt": 1,
            "source_evaluation_report_id": "report-1",
        },
    }
    failed_replan = {
        "candidate_id": "candidate-1",
        "exhausted": False,
        "event": event,
        "job": {
            "job_id": "reflection:evaluation-replan:event-1",
            "kind": "reflection",
            "dedupe_key": "evaluation-replan:report-1",
            "payload": job_payload,
            "budget_reserved_tokens": 4_000,
            "budget_reserved_ms": 120_000,
            "reason_code": (
                "independent_evaluation_failed_replan_queued"
            ),
        },
    }
    report_kwargs = {
        "report_id": "report-1",
        "evaluation_id": "evaluation-1",
        "candidate_package_hash": "package-1",
        "dataset_hash": "dataset-1",
        "suite_hash": "suite-1",
        "required_cases": (
            ("case-1", "old"),
            ("case-1", "candidate"),
        ),
        "results_root_hash": canonical_hash(result_items),
        "verdict": "failed",
        "reason_code": "candidate_failed",
        "failed_replan": failed_replan,
    }
    assert store.create_evaluation_report(
        owner,
        **report_kwargs,
    )["verdict"] == "failed"
    assert store.create_evaluation_report(
        owner,
        **report_kwargs,
    )["report_id"] == "report-1"
    with store.read() as db:
        candidate = db.execute(
            """SELECT status,reason_code FROM candidate_artifacts
               WHERE profile_id=? AND profile_generation=?
                 AND candidate_id='candidate-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        reservation = db.execute(
            """SELECT status,candidate_id,reservation_version
               FROM growth_target_reservations
               WHERE profile_id=? AND profile_generation=?
                 AND target_id='target-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        assert db.execute(
            """SELECT COUNT(*) FROM growth_events
               WHERE profile_id=? AND profile_generation=?
                 AND event_id=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                event["event_id"],
            ),
        ).fetchone()[0] == 1
        assert db.execute(
            """SELECT COUNT(*) FROM jobs
               WHERE profile_id=? AND profile_generation=?
                 AND job_id='reflection:evaluation-replan:event-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()[0] == 1
    assert dict(candidate) == {
        "status": "invalidated",
        "reason_code": "evaluation_failed_replan",
    }
    assert dict(reservation) == {
        "status": "released",
        "candidate_id": None,
        "reservation_version": 2,
    }

    target = GrowthTargetIdentityV1(
        kind="skill",
        target_id="target-1",
        stable_name="summarize-day",
        pack_id="personal.summarize-day",
    )
    replan_proposal = StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=(event["event_id"],),
        evidence_set_hash=evidence_set_hash((event["event_id"],)),
        target=target,
        candidate_mode="genesis",
        source_fence=None,
        target_fence=CandidateBindingFenceV1(
            owner_key="companion:alice:1",
            scope="user",
            scope_key="profile",
            pack_id=target.pack_id,
            expected_absent=True,
            binding_generation=0,
        ),
        hypothesis="The revised candidate covers the evaluator gap.",
        structured_diff={"requested_change": "fix all evaluator gaps"},
        expected_improvement="All assertions pass.",
        risk_hints=("instruction_only",),
        evaluation_plan=(
            {"case_id": "case-1", "assertion": "all gaps covered"},
        ),
    )
    claim = store.claim_job(
        owner,
        claim_owner="reflection-worker",
        lease_seconds=60,
        kinds=("reflection",),
    )
    assert claim is not None
    assert claim.item_id == "reflection:evaluation-replan:event-1"
    admitted = store.admit_reflection_decision(
        owner,
        job_id="reflection:evaluation-replan:event-1",
        decision_id="reflection-decision:replan-1",
        decision="candidate",
        reason_code="reflection_candidate_host_validated",
        evidence_event_ids=(event["event_id"],),
        source_ref="result:replan-1",
        proposal_ref="proposal:replan-1",
        proposal=replan_proposal,
    )
    assert admitted["candidate_build"]["status"] == "proposed"
    with store.read() as db:
        reservation = db.execute(
            """SELECT status,candidate_id,reservation_version
               FROM growth_target_reservations
               WHERE profile_id=? AND profile_generation=?
                 AND target_id='target-1'""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
    assert dict(reservation) == {
        "status": "held",
        "candidate_id": None,
        "reservation_version": 2,
    }


def test_expired_case_lease_is_recovery_only_and_cannot_start_again(evaluation_store) -> None:
    store, owner, clock = evaluation_store
    _issue_permit(store, owner)
    first = store.claim_evaluation_case(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_owner="worker-old",
        lease_seconds=5,
    )
    clock.advance(6)
    recovered = store.claim_evaluation_case(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
        claim_owner="worker-recovery",
        lease_seconds=5,
    )
    assert recovered["recovery_only"] == 1
    assert recovered["claim_epoch"] == first["claim_epoch"] + 1
    launch, _ = _launch(
        recovered, variant="old", launch_id="launch-recovery", claim_owner="worker-recovery"
    )
    with pytest.raises(CompanionStateError, match="recovery_only"):
        store.create_evaluation_case_launch(
            owner, launch, claim_owner="worker-recovery"
        )
    stale_launch, _ = _launch(
        first, variant="old", launch_id="launch-stale", claim_owner="worker-old"
    )
    with pytest.raises(CompanionLeaseError, match="lease_stale"):
        store.create_evaluation_case_launch(owner, stale_launch, claim_owner="worker-old")


def test_safe_auto_permit_rejects_non_low_risk(evaluation_store) -> None:
    store, owner, _ = evaluation_store
    with store._write() as db:
        db.execute(
            """INSERT INTO risk_assessments(
                 profile_id,profile_generation,risk_id,candidate_id,
                 candidate_package_hash,static_preflight_json,
                 effect_topology_diff_json,risk,risk_hash,reason_code,
                 schema_version,created_at
               ) VALUES (?,?,'risk-1','candidate-1','package-1','{}','{}',
                         'high','risk-hash-1','high_risk',1,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                store._now(),
            ),
        )
    with pytest.raises(CompanionStateError, match="risk_invalid"):
        _issue_permit(store, owner)


def _policy_identity() -> EvaluationIdentityV1:
    return EvaluationIdentityV1(
        evaluation_id="evaluation-1",
        candidate_id="candidate-1",
        package_hash="package-1",
        manifest_hash="manifest-1",
        archive_hash="archive-1",
        suite_hash="suite-1",
        runner_policy_hash="runner-policy-1",
        issued_revocation_epoch=4,
        risk_ref="risk-1",
    )


def test_policy_issuer_safe_auto_uses_store_permit_without_activation(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="instruction",
        declared_tool_refs=("memory_recall@v1",),
        referenced_tool_refs=("memory_recall@v1",),
        tool_facts=(
            EffectFactV1("memory_recall@v1", "read_only", True),
        ),
    )
    preflight = StaticRiskPreflight().inspect(candidate)
    risk = CapabilityRiskPolicy().assess(
        preflight, EffectTopologyDiffV1()
    )
    outcome = EvaluationPermitIssuer(store).issue(
        owner,
        EvaluationGateRequestV1(
            identity=_policy_identity(),
            candidate=candidate,
            topology_diff=EffectTopologyDiffV1(),
        ),
    )
    assert outcome.permit is not None
    assert outcome.permit.mode == "safe_auto"
    with store.read() as db:
        assert db.execute(
            "SELECT mode FROM evaluation_execution_permits"
        ).fetchone()[0] == "safe_auto"
        assert db.execute(
            "SELECT count(*) FROM capability_activation_requests"
        ).fetchone()[0] == 0


def test_policy_issuer_safe_static_requires_instruction_and_frozen_zero_tools(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    with store._write() as db:
        db.execute(
            """UPDATE evaluation_runs SET runner_policy_hash=?
               WHERE profile_id=? AND profile_generation=? AND evaluation_id=?""",
            (
                SAFE_STATIC_RUNNER_POLICY_HASH,
                owner.profile_id,
                owner.profile_generation,
                "evaluation-1",
            ),
        )
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="instruction",
        declared_tool_refs=("project_group_send@v1",),
        referenced_tool_refs=("project_group_send@v1",),
        tool_facts=(
            EffectFactV1(
                "project_group_send@v1",
                "external_send",
                False,
            ),
        ),
        permissions_added=("network",),
        topology_expanded=True,
    )
    diff = EffectTopologyDiffV1(
        permissions_added=("network",),
        effects_added=("external_send",),
        topology_expanded=True,
    )
    identity = replace(
        _policy_identity(),
        runner_policy_hash=SAFE_STATIC_RUNNER_POLICY_HASH,
    )
    outcome = EvaluationPermitIssuer(store).issue(
        owner,
        EvaluationGateRequestV1(
            identity=identity,
            candidate=candidate,
            topology_diff=diff,
            zero_tools=True,
        ),
    )
    assert outcome.permit is not None
    assert outcome.permit.mode == "safe_static"
    fence = store.get_evaluation_execution_fence(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="old",
    )
    assert fence["policy_verified"] == 1
    assert fence["authorization_id"] is None


def test_policy_issuer_user_authorized_consumes_eval_only_authorization(
    evaluation_store,
) -> None:
    store, owner, clock = evaluation_store
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="code",
    )
    preflight = StaticRiskPreflight().inspect(candidate)
    risk = CapabilityRiskPolicy().assess(
        preflight, EffectTopologyDiffV1()
    )
    EvaluationAuthorizationService(store, clock=clock).authorize(
        owner,
        EvaluationAuthorizationCommandV1(
            authorization_id="eval-auth-1",
            nonce="eval-nonce-1",
            evaluation_id="evaluation-1",
            candidate_id="candidate-1",
            package_hash="package-1",
            suite_hash="suite-1",
            runner_policy_hash="runner-policy-1",
            reason_code="exact_code_evaluation",
        ),
    )
    outcome = EvaluationPermitIssuer(store).issue(
        owner,
        EvaluationGateRequestV1(
            identity=_policy_identity(),
            candidate=candidate,
            topology_diff=EffectTopologyDiffV1(),
            authorization_id="eval-auth-1",
        ),
    )
    assert outcome.permit is not None
    assert outcome.permit.mode == "user_authorized"
    assert outcome.preflight.direct_os_effects_unverifiable
    with store.read() as db:
        row = db.execute(
            """SELECT p.mode,a.consumed_at
               FROM evaluation_execution_permits AS p
               JOIN evaluation_authorizations AS a
                 ON a.authorization_id=p.authorization_id"""
        ).fetchone()
        assert row["mode"] == "user_authorized"
        assert row["consumed_at"] is not None
        assert db.execute(
            "SELECT count(*) FROM capability_activation_requests"
        ).fetchone()[0] == 0


def test_unknown_risk_can_only_receive_user_authorized_eval_permit(
    evaluation_store,
) -> None:
    store, owner, clock = evaluation_store
    candidate = CandidateRiskInputV1(
        candidate_id="candidate-1",
        package_hash="package-1",
        candidate_kind="unknown",
    )
    diff = EffectTopologyDiffV1(unknown_effects=("unknown-effect",))
    preflight = StaticRiskPreflight().inspect(candidate)
    risk = CapabilityRiskPolicy().assess(preflight, diff)
    blocked = EvaluationPermitIssuer(store).issue(
        owner,
        EvaluationGateRequestV1(
            identity=_policy_identity(),
            candidate=candidate,
            topology_diff=diff,
        ),
    )
    assert blocked.status == "awaiting_eval_authorization"
    assert blocked.permit is None

    EvaluationAuthorizationService(store, clock=clock).authorize(
        owner,
        EvaluationAuthorizationCommandV1(
            authorization_id="eval-auth-unknown",
            nonce="eval-nonce-unknown",
            evaluation_id="evaluation-1",
            candidate_id="candidate-1",
            package_hash="package-1",
            suite_hash="suite-1",
            runner_policy_hash="runner-policy-1",
            reason_code="exact_unknown_evaluation",
        ),
    )
    issued = EvaluationPermitIssuer(store).issue(
        owner,
        EvaluationGateRequestV1(
            identity=_policy_identity(),
            candidate=candidate,
            topology_diff=diff,
            authorization_id="eval-auth-unknown",
        ),
    )
    assert issued.permit is not None
    assert issued.permit.mode == "user_authorized"


def _prepare_install_request(
    store: CompanionStore, owner: OwnerRef
) -> MutationRequest:
    with store._write() as db:
        now = store._now()
        db.execute(
            """UPDATE candidate_artifacts SET status='eligible',updated_at=?
               WHERE profile_id=? AND profile_generation=? AND candidate_id='candidate-1'""",
            (now, owner.profile_id, owner.profile_generation),
        )
        db.execute(
            """INSERT INTO growth_decisions(
                 profile_id,profile_generation,decision_id,nonce,candidate_id,report_id,risk_id,
                 candidate_mode,target_owner_key,target_scope,target_scope_key,
                 target_expected_absent,target_expected_binding_generation,decision,actor,
                 reason_code,activation_risk_ack,decision_hash,schema_version,created_at
               ) VALUES (?,?,'decision-1','nonce-1','candidate-1','report-1','risk-1',
                         'genesis','companion:alice:1','user','profile',1,0,'activate','system',
                         'low_risk','none','decision-hash-1',1,?)""",
            (owner.profile_id, owner.profile_generation, now),
        )
    request = MutationRequest(
        request_id="activation-1",
        request_fingerprint="activation-fingerprint-1",
        action=MutationAction.INSTALL,
        candidate_id="candidate-1",
        candidate_mode=CandidateMode.GENESIS,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        target_version="1.0.0",
        target_manifest_hash="manifest-1",
        target_package_hash="package-1",
        target_archive_hash="archive-1",
        target_expected_absent=True,
        target_expected_binding_generation=0,
        reason_code="low_risk_activation",
    )
    store.create_capability_mutation_request(owner, request)
    with store._write() as db:
        db.execute(
            """UPDATE capability_activation_requests
               SET status='publishing',manager_operation_id='manager-op-1',
                   runtime_set_ref='runtime-set-1',runtime_set_hash='runtime-hash-1'
               WHERE profile_id=? AND profile_generation=? AND activation_request_id='activation-1'""",
            (owner.profile_id, owner.profile_generation),
        )
    return request


def test_low_risk_decision_enqueues_exact_activation_request(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    _issue_permit(store, owner)
    old_hash = _complete_variant(
        store, owner, variant="old", worker="old", launch_id="decision-old"
    )
    candidate_hash = _complete_variant(
        store,
        owner,
        variant="candidate",
        worker="candidate",
        launch_id="decision-candidate",
    )
    results = [
        {"case_id": "case-1", "variant": "candidate", "result_hash": candidate_hash},
        {"case_id": "case-1", "variant": "old", "result_hash": old_hash},
    ]
    store.create_evaluation_report(
        owner,
        report_id="report-decision",
        evaluation_id="evaluation-1",
        candidate_package_hash="package-1",
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(("case-1", "old"), ("case-1", "candidate")),
        results_root_hash=canonical_hash(results),
        verdict="passed",
        reason_code="passed",
    )
    mutation = MutationRequest(
        request_id="activation-decision-1",
        request_fingerprint="activation-decision-fingerprint-1",
        action=MutationAction.INSTALL,
        candidate_id="candidate-1",
        candidate_mode=CandidateMode.GENESIS,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        target_version="1.0.0",
        target_manifest_hash="manifest-1",
        target_package_hash="package-1",
        target_archive_hash="archive-1",
        target_expected_absent=True,
        target_expected_binding_generation=0,
        reason_code="low_risk_auto_activation",
    )
    request = ActivationDecisionCoordinator(store).decide_and_enqueue(
        owner,
        decision_id="decision-auto-1",
        nonce="decision-auto-nonce-1",
        candidate_id="candidate-1",
        report_id="report-decision",
        risk_id="risk-1",
        actor="system",
        activation_package_hash="package-1",
        activation_code_digest=None,
        activation_risk_ack="none",
        reason_code="low_risk_auto_activation",
        mutation=mutation,
    )

    assert request["status"] == "pending"
    assert request["decision_id"] == "decision-auto-1"
    assert store.record_activation_decision(
        owner,
        decision_id="decision-auto-1",
        nonce="decision-auto-nonce-1",
        candidate_id="candidate-1",
        report_id="report-decision",
        risk_id="risk-1",
        decision="activate",
        actor="system",
        activation_package_hash="package-1",
        activation_code_digest=None,
        activation_risk_ack="none",
        reason_code="low_risk_auto_activation",
    )["decision_hash"]


def test_mutation_receipt_settle_guard_and_incident_are_one_way(evaluation_store) -> None:
    store, owner, _ = evaluation_store
    _issue_permit(store, owner)
    old_hash = _complete_variant(
        store, owner, variant="old", worker="old", launch_id="launch-old"
    )
    candidate_hash = _complete_variant(
        store, owner, variant="candidate", worker="candidate", launch_id="launch-candidate"
    )
    result_items = [
        {"case_id": "case-1", "variant": "candidate", "result_hash": candidate_hash},
        {"case_id": "case-1", "variant": "old", "result_hash": old_hash},
    ]
    store.create_evaluation_report(
        owner,
        report_id="report-1",
        evaluation_id="evaluation-1",
        candidate_package_hash="package-1",
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(("case-1", "old"), ("case-1", "candidate")),
        results_root_hash=canonical_hash(result_items),
        verdict="passed",
        reason_code="passed",
    )
    request = _prepare_install_request(store, owner)
    rollback_plan = {
        "action": "rollback",
        "rollback_kind": "same_owner_version",
        "target_version": "0.9.0",
        "target_manifest_hash": "manifest-old",
        "source_fence": {
            "owner_key": "companion:alice:1",
            "scope": "user",
            "scope_key": "profile",
            "version": "0.9.0",
            "manifest_hash": "manifest-old",
            "binding_generation": 1,
        },
    }
    receipt = CapabilityMutationReceipt(
        activation_request_id=request.request_id,
        manager_operation_id="manager-op-1",
        action=MutationAction.INSTALL,
        candidate_mode=CandidateMode.GENESIS,
        pack_id=request.pack_id,
        version=request.target_version,
        manifest_hash=request.target_manifest_hash,
        runtime_set_ref="runtime-set-1",
        runtime_set_hash="runtime-hash-1",
        target_owner_key=request.target_owner_key,
        target_scope=request.target_scope,
        binding_generation=1,
        owner_binding_set_stamp="owner-stamp-1",
        process_projection_fingerprint="projection-1",
        result_hash="manager-result-1",
        reason_code="manager_committed",
        open_guard=True,
        guard_id="guard-1",
        guard_policy_hash="guard-policy-1",
        rollback_plan=rollback_plan,
    )
    settled = store.settle_capability_mutation_receipt(owner, receipt)
    assert settled["result_hash"] == "manager-result-1"
    assert store.settle_capability_mutation_receipt(owner, receipt)["result_hash"] == "manager-result-1"
    queried_receipt = store.get_capability_activation_receipt(
        owner,
        activation_request_id=request.request_id,
    )
    assert queried_receipt is not None
    assert queried_receipt["result_hash"] == "manager-result-1"
    proof = store.get_candidate_activation_proof(
        owner,
        candidate_id="candidate-1",
    )
    assert proof is not None
    assert proof["request_status"] == "succeeded"
    assert proof["evaluation_verdict"] == "passed"
    assert proof["risk"] == "low"
    assert proof["decision"] == "activate"
    assert proof["receipt"]["result_hash"] == "manager-result-1"
    with pytest.raises(CompanionConflictError, match="receipt_conflict"):
        store.settle_capability_mutation_receipt(
            owner, replace(receipt, result_hash="drift")
        )
    with store.read() as db:
        assert db.execute(
            "SELECT status FROM candidate_artifacts WHERE candidate_id='candidate-1'"
        ).fetchone()[0] == "active"
        assert db.execute(
            "SELECT status FROM growth_target_reservations"
        ).fetchone()[0] == "consumed"
        assert db.execute(
            "SELECT status FROM capability_activation_guards WHERE guard_id='guard-1'"
        ).fetchone()[0] == "open"

    incident = CapabilityGuardIncident(
        guard_id="guard-1",
        incident_id="incident-1",
        source_authority="runtime-health",
        source_event_id="runtime-event-1",
        binding_generation=1,
        pack_id=request.pack_id,
        version="1.0.0",
        manifest_hash="manifest-1",
        runtime_generation=1,
        failure_class="runtime_contract",
        failure_fingerprint="runtime-failure-1",
        source_receipt_ref="runtime-receipt-1",
        source_receipt_hash="runtime-receipt-hash-1",
        observed_at=store._now(),
        dedupe_hash="incident-dedupe-1",
        rollback_request_id="rollback-1",
        request_fingerprint="rollback-fingerprint-1",
        reason_code="critical_runtime_contract",
    )
    triggered = store.record_capability_guard_incident(owner, incident)
    assert triggered["status"] == "triggered"
    assert store.record_capability_guard_incident(owner, incident)["status"] == "triggered"
    with pytest.raises(CompanionConflictError, match="incident_conflict"):
        store.record_capability_guard_incident(
            owner, replace(incident, source_receipt_hash="drift")
        )
    with store.read() as db:
        assert db.execute(
            "SELECT status FROM capability_activation_guards WHERE guard_id='guard-1'"
        ).fetchone()[0] == "rollback_pending"
        assert db.execute(
            "SELECT status FROM capability_quarantines WHERE pack_id=?",
            (request.pack_id,),
        ).fetchone()[0] == "rollback_pending"
        rollback = db.execute(
            """SELECT action,candidate_id,candidate_mode,rollback_kind,status
               FROM capability_activation_requests
               WHERE activation_request_id='rollback-1'"""
        ).fetchone()
        assert tuple(rollback) == (
            "rollback",
            None,
            None,
            "same_owner_version",
            "pending",
        )
        assert db.execute(
            """SELECT from_id FROM lineage_edges
               WHERE to_kind='capability_mutation_request'
                 AND to_id='rollback-1' AND relation='rollback'"""
        ).fetchone()[0] == "candidate-1"
        assert db.execute(
            "SELECT status FROM candidate_artifacts WHERE candidate_id='candidate-1'"
        ).fetchone()[0] == "rollback_pending"

    claim = store.claim_capability_mutation_request(
        owner,
        request_id="rollback-1",
        claim_owner="rollback-worker",
        lease_seconds=30,
    )
    store.record_capability_mutation_prepared(
        owner,
        request_id="rollback-1",
        claim_owner="rollback-worker",
        claim_epoch=claim["claim_epoch"],
        manager_operation_id="manager-op-rollback",
        runtime_set_ref=None,
        runtime_set_hash=None,
        reason_code="rollback_prepared",
    )
    store.mark_capability_mutation_publishing(
        owner,
        request_id="rollback-1",
        claim_owner="rollback-worker",
        claim_epoch=claim["claim_epoch"],
        reason_code="rollback_publishing",
    )
    store.settle_capability_mutation_receipt(
        owner,
        CapabilityMutationReceipt(
            activation_request_id="rollback-1",
            manager_operation_id="manager-op-rollback",
            action=MutationAction.ROLLBACK,
            pack_id=request.pack_id,
            version="0.9.0",
            manifest_hash="manifest-old",
            source_fence_hash=canonical_hash(
                rollback_plan["source_fence"]
            ),
            binding_generation=2,
            owner_binding_set_stamp="owner-stamp-rollback",
            result_hash="rollback-result",
            reason_code="rollback_committed",
        ),
    )
    with store.read() as db:
        assert db.execute(
            "SELECT status FROM candidate_artifacts WHERE candidate_id='candidate-1'"
        ).fetchone()[0] == "rolled_back"
        guard = db.execute(
            """SELECT status,rollback_receipt_hash
               FROM capability_activation_guards WHERE guard_id='guard-1'"""
        ).fetchone()
        assert tuple(guard) == ("rolled_back", "rollback-result")
        assert db.execute(
            "SELECT status FROM capability_quarantines WHERE pack_id=?",
            (request.pack_id,),
        ).fetchone()[0] == "released"
        settled_rollback = dict(
            db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE activation_request_id='rollback-1'"""
            ).fetchone()
        )

    replay = store.create_capability_mutation_request(
        owner,
        MutationRequest(
            request_id="rollback-1",
            request_fingerprint="rollback-fingerprint-1",
            action=MutationAction.ROLLBACK,
            candidate_id="candidate-1",
            candidate_mode=CandidateMode.GENESIS,
            target_owner_key=settled_rollback["target_owner_key"],
            target_scope=settled_rollback["target_scope"],
            target_scope_key=settled_rollback["target_scope_key"],
            pack_id=settled_rollback["pack_id"],
            target_version=settled_rollback["target_version"],
            target_manifest_hash=settled_rollback["target_manifest_hash"],
            source_fence=json.loads(settled_rollback["source_fence_json"]),
            target_expected_absent=bool(
                settled_rollback["target_expected_absent"]
            ),
            target_expected_binding_generation=settled_rollback[
                "target_expected_binding_generation"
            ],
            rollback_kind=settled_rollback["rollback_kind"],
            activation_mode=settled_rollback["activation_mode"],
            cause_ref=settled_rollback["cause_ref"],
            reason_code="critical_runtime_contract",
        ),
    )
    assert replay["status"] == "succeeded"
    with store.read() as db:
        assert db.execute(
            """SELECT from_id FROM lineage_edges
               WHERE to_kind='capability_mutation_request'
                 AND to_id='rollback-1' AND relation='rollback'"""
        ).fetchone()[0] == "candidate-1"
        assert db.execute(
            "SELECT status FROM candidate_artifacts WHERE candidate_id='candidate-1'"
        ).fetchone()[0] == "rolled_back"


def test_forget_active_candidate_quarantines_and_enqueues_disable(
    evaluation_store,
) -> None:
    store, owner, _ = evaluation_store
    _issue_permit(store, owner)
    old_hash = _complete_variant(
        store, owner, variant="old", worker="old", launch_id="launch-old"
    )
    candidate_hash = _complete_variant(
        store,
        owner,
        variant="candidate",
        worker="candidate",
        launch_id="launch-candidate",
    )
    results = [
        {"case_id": "case-1", "variant": "candidate", "result_hash": candidate_hash},
        {"case_id": "case-1", "variant": "old", "result_hash": old_hash},
    ]
    store.create_evaluation_report(
        owner,
        report_id="report-1",
        evaluation_id="evaluation-1",
        candidate_package_hash="package-1",
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(("case-1", "old"), ("case-1", "candidate")),
        results_root_hash=canonical_hash(results),
        verdict="passed",
        reason_code="passed",
    )
    request = _prepare_install_request(store, owner)
    store.settle_capability_mutation_receipt(
        owner,
        CapabilityMutationReceipt(
            activation_request_id=request.request_id,
            manager_operation_id="manager-op-1",
            action=MutationAction.INSTALL,
            candidate_mode=CandidateMode.GENESIS,
            pack_id=request.pack_id,
            version=request.target_version,
            manifest_hash=request.target_manifest_hash,
            runtime_set_ref="runtime-set-1",
            runtime_set_hash="runtime-hash-1",
            target_owner_key=request.target_owner_key,
            target_scope=request.target_scope,
            binding_generation=1,
            owner_binding_set_stamp="owner-stamp-1",
            process_projection_fingerprint="projection-1",
            result_hash="manager-result-1",
            reason_code="manager_committed",
        ),
    )

    assert store.forget_growth_event(
        owner,
        event_id="event-1",
        reason_code="user_forget",
    )
    with store.read() as db:
        assert db.execute(
            "SELECT status FROM candidate_artifacts WHERE candidate_id='candidate-1'"
        ).fetchone()[0] == "invalidated"
        assert db.execute(
            "SELECT verdict FROM evaluation_reports WHERE report_id='report-1'"
        ).fetchone()[0] == "inconclusive"
        assert db.execute(
            "SELECT decision FROM growth_decisions WHERE decision_id='decision-1'"
        ).fetchone()[0] == "stale"
        assert db.execute(
            "SELECT status FROM capability_quarantines"
        ).fetchone()[0] == "active"
        follow_up = db.execute(
            """SELECT action,status,cause_ref,target_expected_binding_generation
               FROM capability_activation_requests
               WHERE activation_request_id<>'activation-1'"""
        ).fetchone()
        assert tuple(follow_up) == (
            "disable",
            "pending",
            "growth_event:event-1",
            1,
        )
        assert db.execute(
            """SELECT count(*) FROM outbox
               WHERE event_kind='capability_mutation'
                 AND event_id=(SELECT activation_request_id
                               FROM capability_activation_requests
                               WHERE activation_request_id<>'activation-1')"""
        ).fetchone()[0] == 1


def test_mutation_replay_drift_and_genesis_fence_are_rejected(evaluation_store) -> None:
    store, owner, _ = evaluation_store
    disable = MutationRequest(
        request_id="disable-1",
        request_fingerprint="disable-fingerprint",
        action=MutationAction.DISABLE,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="pack-to-disable",
        target_expected_binding_generation=2,
        cause_ref="forget:event-1",
        reason_code="forgotten",
    )
    store.create_capability_mutation_request(owner, disable)
    with pytest.raises(CompanionConflictError, match="request_conflict"):
        store.create_capability_mutation_request(
            owner, replace(disable, cause_ref="different-cause")
        )

    # Candidate's frozen genesis fence is expected-absent generation 0.
    with store._write() as db:
        db.execute(
            """UPDATE candidate_artifacts SET status='eligible'
               WHERE profile_id=? AND profile_generation=? AND candidate_id='candidate-1'""",
            (owner.profile_id, owner.profile_generation),
        )
    illegal = MutationRequest(
        request_id="illegal-install",
        request_fingerprint="illegal-install-fingerprint",
        action=MutationAction.INSTALL,
        candidate_id="candidate-1",
        candidate_mode=CandidateMode.GENESIS,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        target_version="1.0.0",
        target_manifest_hash="manifest-1",
        target_package_hash="package-1",
        target_archive_hash="archive-1",
        target_expected_absent=False,
        target_expected_binding_generation=1,
        reason_code="illegal_genesis",
    )
    with pytest.raises(CompanionConflictError, match="fence_mismatch"):
        store.create_capability_mutation_request(owner, illegal)


def test_authority_transition_is_strict_and_replay_verifies_payload(evaluation_store) -> None:
    store, _, _ = evaluation_store
    payload = {"cutover_operation_id": "cutover-1", "migration_version": 1}
    state = store.transition_growth_authority_state(
        "legacy",
        "preparing",
        migration_generation=2,
        marker_committed=False,
        journal_payload=payload,
        reason_code="prepare",
    )
    assert state["phase"] == "preparing"
    assert store.transition_growth_authority_state(
        "legacy",
        "preparing",
        migration_generation=2,
        marker_committed=False,
        journal_payload=payload,
        reason_code="prepare",
    )["generation"] == 2
    with pytest.raises(CompanionConflictError, match="replay_conflict"):
        store.transition_growth_authority_state(
            "legacy",
            "preparing",
            migration_generation=2,
            marker_committed=False,
            journal_payload={**payload, "migration_version": 2},
            reason_code="prepare",
        )
    with pytest.raises(CompanionStateError, match="advance_once"):
        store.transition_growth_authority_state(
            "preparing",
            "preparing",
            migration_generation=4,
            marker_committed=True,
            journal_payload={"marker_hash": "marker"},
        )
    store.transition_growth_authority_state(
        "preparing",
        "preparing",
        migration_generation=3,
        marker_committed=True,
        journal_payload={"marker_hash": "marker"},
    )
    with pytest.raises(CompanionStateError, match="cannot_be_cleared"):
        store.transition_growth_authority_state(
            "preparing",
            "legacy",
            migration_generation=4,
            marker_committed=False,
        )
