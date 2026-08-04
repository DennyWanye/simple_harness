"""Task 13 public-API composition contract for the growth production pipeline.

This test deliberately uses no SQL fixture writes.  Test doubles implement
only the execution child/material ports that live outside companion.db; all
growth, candidate, evaluation, risk and activation authority is expected to
flow through production modules and public Store APIs.
"""

from __future__ import annotations

import pytest

from deskpet.capabilities.package_limits import CapabilityPackageValidator
from deskpet.companion.build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
)
from deskpet.companion.candidate_builder import (
    CandidateFileInputV1,
    CandidateSeedInputV1,
    DeterministicCandidateBuilder,
)
from deskpet.companion.candidate_build_coordinator import (
    CompanionCandidateBuildCoordinator,
)
from deskpet.companion.candidate_composition import (
    CandidateCompositionRequestV1,
    CandidateCompositionService,
    CandidateDraftMaterialV1,
)
from deskpet.companion.activation import (
    ActivationDecisionCoordinator,
    ActivationDispatcher,
    PreparedCapabilityMutation,
)
from deskpet.companion.authority import RevocationBarrier
from deskpet.companion.evaluation import (
    EvaluationIdentityV1,
    EvaluationPermitIssuer,
    ImmutableEvaluationGate,
)
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.risk import EffectTopologyDiffV1
from deskpet.companion.store import (
    CompanionStore,
    canonical_hash,
)
from deskpet.companion.contracts import (
    CandidateMode,
    CapabilityMutationReceipt,
    EvaluationCaseLaunch,
    GrowthEvent,
    MutationAction,
    MutationRequest,
)


def _proposal(owner, event_id: str) -> StructuredGrowthProposalV1:
    target = GrowthTargetIdentityV1(
        kind="skill",
        target_id="daily-summary",
        stable_name="daily-summary",
        pack_id="personal.daily-summary",
    )
    target_fence = CandidateBindingFenceV1(
        owner_key=(
            f"companion:{owner.profile_id}:{owner.profile_generation}"
        ),
        scope="user",
        scope_key=owner.profile_id,
        pack_id=target.pack_id,
        expected_absent=True,
        binding_generation=0,
    )
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=(event_id,),
        evidence_set_hash=evidence_set_hash((event_id,)),
        target=target,
        candidate_mode="genesis",
        target_fence=target_fence,
        hypothesis="A concise summary contract is easier to act on.",
        structured_diff={
            "instructions": {"replace": ["use the revised summary contract"]}
        },
        expected_improvement="The summary follows the accepted contract.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "summary-contract"},),
    )


def _candidate_product(owner, proposal):
    assert proposal.target is not None
    assert proposal.target_fence is not None
    return DeterministicCandidateBuilder().build(
        CandidateSeedInputV1(
            owner_key=(
                f"companion:{owner.profile_id}:"
                f"{owner.profile_generation}"
            ),
            target=proposal.target,
            candidate_mode="genesis",
            source_fence=None,
            target_fence=proposal.target_fence,
            files=(
                CandidateFileInputV1(
                    "skills/daily-summary/SKILL.md",
                    (
                        b"---\n"
                        b"name: daily-summary\n"
                        b"description: Daily summary contract\n"
                        b"allowed-tools: []\n"
                        b"---\n"
                        b"Use the revised summary contract.\n"
                    ),
                ),
            ),
            manifest_template={
                "schema_version": 2,
                "id": proposal.target.pack_id,
                "name": "Daily summary",
                "permissions": [],
                "effects": [],
                "entries": {
                    "skills": [
                        {
                            "id": proposal.target.target_id,
                            "path": "skills/daily-summary/SKILL.md",
                        }
                    ]
                },
            },
            permissions=(),
            effect_topology={"effects": []},
        )
    )


def _draft_receipt(build, product) -> CandidateDraftReceiptV1:
    return CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id=str(build["builder_launch_id"]),
        child_run_id=str(build["builder_child_run_id"]),
        child_start_hash=str(build["expected_child_start_hash"]),
        proposal_ref=str(build["proposal_ref"]),
        proposal_hash=str(build["proposal_hash"]),
        evidence_set_hash=str(build["evidence_set_hash"]),
        target_fence_hash=str(build["target_fence_hash"]),
        validated_draft_hash=product.package.candidate_content_hash,
        manifest_hash=product.package.candidate_manifest_hash,
        archive_hash=product.package.archive_hash,
        file_set_hash=product.file_set_hash,
        effect_topology_hash=product.package.effect_topology_hash,
    )


class _ReceiptQuery:
    def __init__(self, receipt: CandidateDraftReceiptV1) -> None:
        self.receipt = receipt

    def read_exact(self, expectation):
        expectation.verify(self.receipt)
        return self.receipt


class _MaterialQuery:
    def __init__(self, product, receipt) -> None:
        self.product = product
        self.receipt = receipt

    def read_exact(self, receipt):
        assert receipt == self.receipt
        return CandidateDraftMaterialV1(
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.receipt_hash,
            archive_bytes=self.product.archive_bytes,
            validated_draft_hash=receipt.validated_draft_hash,
            manifest_hash=receipt.manifest_hash,
            file_set_hash=receipt.file_set_hash,
            effect_topology_hash=receipt.effect_topology_hash,
        )


class _ReservedChild:
    def __init__(self, build, receipt) -> None:
        self.build = build
        self.receipt = receipt
        self.state = "missing"

    def _observed(self):
        if self.state == "missing":
            return None
        payload = {
            "builder_launch_id": self.build["builder_launch_id"],
            "child_run_id": self.build["builder_child_run_id"],
            "child_start_hash": self.build["expected_child_start_hash"],
            "status": self.state,
        }
        if self.state == "succeeded":
            payload["draft_receipt"] = {
                name: getattr(self.receipt, name)
                for name in (
                    CandidateDraftReceiptExpectationV1
                    .__dataclass_fields__
                )
            }
        return payload

    async def read_exact(self, _permit):
        return self._observed()

    async def precreate_exact(self, _permit, *, task_workspace):
        assert task_workspace == "candidate-workspace"
        self.state = "precreated"
        return self._observed()

    async def start_precreated(self, _permit):
        self.state = "succeeded"
        return self._observed()


class _CandidateOutput:
    def __init__(self, *, service, build_id, product) -> None:
        self.service = service
        self.build_id = build_id
        self.product = product

    def handoff_candidate(self, *, permit, receipt):
        assert permit.build_id == self.build_id
        expectation = CandidateDraftReceiptExpectationV1(
            **{
                name: getattr(receipt, name)
                for name in (
                    CandidateDraftReceiptExpectationV1
                    .__dataclass_fields__
                )
            }
        )
        result = self.service.create_candidate_from_builder_receipt(
            CandidateCompositionRequestV1(
                build_id=self.build_id,
                receipt_expectation=expectation,
            )
        )
        return {
            "build_id": result.build_id,
            "candidate_ref": result.candidate_id,
            "candidate_hash": (
                self.product.package.candidate_package_hash
            ),
        }


def _complete_case(
    store,
    owner,
    *,
    bundle,
    permit,
    variant: str,
) -> str:
    worker = f"evaluation-worker-{variant}"
    claim = store.claim_evaluation_case(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant=variant,
        claim_owner=worker,
        lease_seconds=60,
    )
    launch_facts = {
        "schema_version": 1,
        "launch_id": f"evaluation-launch-{variant}",
        "evaluation_id": "evaluation-1",
        "case_id": "case-1",
        "variant": variant,
        "attempt_ordinal": int(claim["attempt"]),
        "candidate_package_hash": (
            bundle.package.candidate_package_hash
        ),
        "candidate_manifest_hash": (
            bundle.package.candidate_manifest_hash
        ),
        "candidate_archive_hash": bundle.package.archive_hash,
        "suite_hash": "suite-1",
        "permit_mode": permit.mode,
        "permit_id": permit.permit_id,
        "permit_hash": permit.permit_hash,
        "case_lease_epoch": int(claim["claim_epoch"]),
        "revocation_epoch": permit.issued_revocation_epoch,
        "adapter_id": "fixture-adapter",
        "adapter_version": "1",
        "adapter_fingerprint": "adapter-build-1",
    }
    launch = EvaluationCaseLaunch(
        **launch_facts,
        launch_fingerprint=canonical_hash(launch_facts),
        reason_code="evaluation_case_launch_claimed",
    )
    store.create_evaluation_case_launch(
        owner,
        launch,
        claim_owner=worker,
    )
    store.settle_evaluation_case_launch(
        owner,
        launch_id=launch.launch_id,
        expected_status="claimed",
        status="started",
        reason_code="evaluation_start_ack",
    )
    store.settle_evaluation_case_launch(
        owner,
        launch_id=launch.launch_id,
        expected_status="started",
        status="completed",
        outcome_ref=f"outcome:{variant}",
        outcome_hash=canonical_hash(["outcome", variant]),
        reason_code="evaluation_completed",
    )
    payload = {
        "assertions": {"contract_satisfied": True},
        "judge_result": {"improved": variant == "candidate"},
        "usage": {"tokens": 1},
    }
    result_hash = canonical_hash(payload)
    store.record_evaluation_result(
        owner,
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant=variant,
        claim_owner=worker,
        claim_epoch=int(claim["claim_epoch"]),
        assertions=payload["assertions"],
        judge_result=payload["judge_result"],
        usage=payload["usage"],
        result_hash=result_hash,
        reason_code="evaluation_result_committed",
    )
    return result_hash


class _CommittedManagerPlatform:
    async def prepare_mutation(
        self,
        _authorization,
        *,
        persisted_operation_id,
        persisted_runtime_set_ref,
        persisted_runtime_set_hash,
    ):
        assert persisted_operation_id is None
        assert persisted_runtime_set_ref is None
        assert persisted_runtime_set_hash is None
        return PreparedCapabilityMutation(
            manager_operation_id="manager-operation-1",
            runtime_set_ref=None,
            runtime_set_hash=None,
            instances=(),
            prepared_hash=canonical_hash(
                {
                    "schema_version": 1,
                    "manager_operation_id": "manager-operation-1",
                    "runtime_set_ref": None,
                    "runtime_set_hash": None,
                    "instances": [],
                }
            ),
        )

    async def start_runtime_instance(
        self, _authorization, _prepared, _instance
    ):
        raise AssertionError(
            "instruction-only candidate has no runtime instance"
        )

    async def await_runtime_instance_health(
        self, _authorization, _prepared, _instance, _start
    ):
        raise AssertionError(
            "instruction-only candidate has no runtime healthcheck"
        )

    async def activate_mutation(self, authorization, prepared):
        assert prepared.manager_operation_id == "manager-operation-1"
        return CapabilityMutationReceipt(
            activation_request_id=authorization.request_id,
            manager_operation_id=prepared.manager_operation_id,
            action=MutationAction.INSTALL,
            candidate_mode=CandidateMode.GENESIS,
            pack_id=authorization.pack_id,
            version=authorization.target_version,
            manifest_hash=authorization.target_manifest_hash,
            target_owner_key=authorization.target_owner_key,
            target_scope=authorization.target_scope,
            binding_generation=1,
            owner_binding_set_stamp="owner-binding-set-1",
            process_projection_fingerprint="process-projection-1",
            result_hash="manager-result-1",
            reason_code="manager_committed",
        )

    async def abort_mutation(
        self, _authorization, _prepared, *, reason_code
    ):
        del reason_code
        return True


@pytest.mark.asyncio
async def test_growth_public_api_composes_to_activation_receipt(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="relay:profile-a",
    )
    event_id = "growth-event-1"
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id=event_id,
            source_kind="message_ingress",
            source_ref="message:1",
            context_key="session:1",
            root_run_id="run:1",
            reason_code="explicit_user_correction",
            payload={"message_ref": "message:1"},
        )
    )
    store.enqueue_job(
        owner,
        job_id="reflection-job-1",
        kind="reflection",
        dedupe_key="reflection:growth-event-1",
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
        reason_code="model_proposed_candidate",
        evidence_event_ids=(event_id,),
        source_ref="reflection-job-1:result",
        proposal_ref="reflection-job-1:proposal",
        proposal=proposal,
    )
    build = admitted["candidate_build"]
    assert build is not None
    product = _candidate_product(owner, proposal)
    receipt = _draft_receipt(build, product)
    receipts = _ReceiptQuery(receipt)
    composition = CandidateCompositionService(
        receipt_query=receipts,
        material_query=_MaterialQuery(product, receipt),
        store=store,
        package_validator=CapabilityPackageValidator(),
    )
    permit = store.claim_next_candidate_build(
        owner,
        claim_owner="candidate-scheduler-1",
        lease_seconds=60,
    )
    assert permit is not None
    built = await CompanionCandidateBuildCoordinator(
        permits=store,
        states=store,
        launches=_ReservedChild(build, receipt),
        receipts=receipts,
        output=_CandidateOutput(
            service=composition,
            build_id=permit.build_id,
            product=product,
        ),
    ).reconcile(
        permit,
        task_workspace="candidate-workspace",
    )
    assert built.status.value == "built"
    assert built.candidate_ref is not None
    exact_build = store.get_candidate_build_recovery(
        owner,
        build_id=built.build_id,
    )
    assert exact_build["status"] == "built"
    assert exact_build["candidate_ref"] == built.candidate_ref
    bundle = store.get_exact_candidate_bundle(
        owner,
        candidate_id=built.candidate_ref,
    )
    assert bundle.candidate_hash == (
        bundle.package.candidate_package_hash
    )
    assert bundle.receipt.validated_draft_hash == (
        bundle.package.candidate_content_hash
    )

    store.admit_evaluation_experiment(
        owner,
        evaluation={
            "evaluation_id": "evaluation-1",
            "candidate_id": built.candidate_ref,
            "candidate_mode": "genesis",
            "candidate_package_hash": (
                bundle.package.candidate_package_hash
            ),
            "candidate_manifest_hash": (
                bundle.package.candidate_manifest_hash
            ),
            "candidate_archive_hash": bundle.package.archive_hash,
            "suite_hash": "suite-1",
            "attempt_key": "evaluation-attempt-1",
            "baseline_kind": "capability_absent_v1",
            "old_snapshot_hash": "absent-snapshot-1",
            "candidate_snapshot_hash": "candidate-snapshot-1",
            "absent_baseline_ref": "capability-absent-v1",
            "absent_baseline_hash": "absent-hash-1",
            "runner_id": "local-evaluator-v1",
            "runner_policy_hash": "runner-policy-1",
            "provider_id": "provider-1",
            "model_id": "model-1",
        },
        case_inputs=(
            {
                "input_id": "input-1",
                "case_id": "case-1",
                "source_kind": "packaged_suite",
                "resource_ref": "suite/case-1.json",
                "resource_hash": "resource-hash-1",
                "input_envelope": {"prompt": "apply the summary contract"},
                "adapter_id": "fixture-adapter",
                "adapter_version": "1",
                "adapter_build_fingerprint": "adapter-build-1",
                "assertion_ref": "assertion-1",
                "assertion_hash": "assertion-hash-1",
                "read_tool_fixture": {"records": []},
                "evaluation_tool_adapter_map": {},
            },
        ),
        cases=tuple(
            {
                "case_id": "case-1",
                "variant": variant,
                "input_id": "input-1",
                "manifest_case_version": "1",
                "blind_label": f"blind-{variant}",
                "expected_kind": "summary",
            }
            for variant in ("old", "candidate")
        ),
    )

    gate = ImmutableEvaluationGate(EvaluationPermitIssuer(store)).issue(
        owner,
        identity=EvaluationIdentityV1(
            evaluation_id="evaluation-1",
            candidate_id=built.candidate_ref,
            package_hash=bundle.package.candidate_package_hash,
            manifest_hash=bundle.package.candidate_manifest_hash,
            archive_hash=bundle.package.archive_hash,
            suite_hash="suite-1",
            runner_policy_hash="runner-policy-1",
            issued_revocation_epoch=0,
            risk_ref="risk-1",
        ),
        package=bundle.package,
        receipt=bundle.receipt,
        effect_topology={"effects": []},
        tool_manifest={},
        topology_diff=EffectTopologyDiffV1(),
    )
    assert gate.status == "issued"
    assert gate.permit is not None
    result_hashes = {
        variant: _complete_case(
            store,
            owner,
            bundle=bundle,
            permit=gate.permit,
            variant=variant,
        )
        for variant in ("old", "candidate")
    }
    result_items = [
        {
            "case_id": "case-1",
            "variant": variant,
            "result_hash": result_hashes[variant],
        }
        for variant in ("candidate", "old")
    ]
    store.create_evaluation_report(
        owner,
        report_id="evaluation-report-1",
        evaluation_id="evaluation-1",
        candidate_package_hash=(
            bundle.package.candidate_package_hash
        ),
        dataset_hash="dataset-1",
        suite_hash="suite-1",
        required_cases=(
            ("case-1", "old"),
            ("case-1", "candidate"),
        ),
        results_root_hash=canonical_hash(result_items),
        verdict="passed",
        reason_code="evaluation_passed",
    )
    target = proposal.target_fence
    assert target is not None
    mutation = MutationRequest(
        request_id="activation-request-1",
        request_fingerprint="activation-fingerprint-1",
        action=MutationAction.INSTALL,
        candidate_id=bundle.candidate_id,
        candidate_mode=CandidateMode.GENESIS,
        target_owner_key=target.owner_key,
        target_scope=target.scope,
        target_scope_key=target.scope_key,
        pack_id=bundle.package.pack_id,
        target_version=bundle.package.version,
        target_manifest_hash=bundle.package.candidate_manifest_hash,
        target_package_hash=bundle.package.candidate_package_hash,
        target_archive_hash=bundle.package.archive_hash,
        target_expected_absent=True,
        target_expected_binding_generation=0,
        reason_code="low_risk_auto_activation",
    )
    ActivationDecisionCoordinator(store).decide_and_enqueue(
        owner,
        decision_id="activation-decision-1",
        nonce="activation-nonce-1",
        candidate_id=bundle.candidate_id,
        report_id="evaluation-report-1",
        risk_id="risk-1",
        actor="system",
        activation_package_hash=(
            bundle.package.candidate_package_hash
        ),
        activation_code_digest=None,
        activation_risk_ack="none",
        reason_code="low_risk_auto_activation",
        mutation=mutation,
    )
    settled = await ActivationDispatcher(
        store=store,
        platform=_CommittedManagerPlatform(),
        barrier=RevocationBarrier(),
    ).execute(
        owner,
        request_id=mutation.request_id,
        claim_owner="activation-dispatcher-1",
    )
    assert settled["result_hash"] == "manager-result-1"
    proof = store.get_candidate_activation_proof(
        owner,
        candidate_id=bundle.candidate_id,
    )
    assert proof is not None
    assert proof["request_status"] == "succeeded"
    assert proof["receipt"]["result_hash"] == "manager-result-1"
