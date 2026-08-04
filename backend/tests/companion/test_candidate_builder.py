from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO

import pytest

from deskpet.capabilities.manifest import parse_pack_manifest
from deskpet.capabilities.package_limits import CapabilityPackageValidator
from deskpet.companion.build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
    CapabilityBuildPublishPolicy,
    GrowthBuildAdmissionError,
    GrowthBuildAdmissionRouter,
    GrowthCandidateBuildPermitV1,
    RequestedEntryKind,
    TrustedRootBuildRequestV1,
)
from deskpet.companion.candidate_builder import (
    CandidateBuildIdentityError,
    CandidateFileInputV1,
    CandidateSeedInputV1,
    CandidateVersionCollisionGuard,
    CandidateVersionHashCollision,
    DeterministicCandidateBuilder,
)
from deskpet.companion.candidate_composition import (
    CandidateCompositionRequestV1,
    CandidateCompositionService,
    CandidateDraftMaterialV1,
)
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.store import CompanionStore


OWNER = OwnerRef("profile-a", 2)
OWNER_KEY = "companion:profile-a:2"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def fence(*, absent: bool, owner_key: str = OWNER_KEY, generation: int = 0):
    return CandidateBindingFenceV1(
        owner_key=owner_key,
        scope="user",
        scope_key="profile-a",
        pack_id="daily-three-pack",
        expected_absent=absent,
        binding_generation=generation,
        version=None if absent else "1.2.3+old",
        manifest_hash=None if absent else DIGEST_A,
    )


def seed(
    *,
    owner_key: str = OWNER_KEY,
    content: bytes = b"# Daily three\n",
    effect_topology=None,
    mode: str = "genesis",
) -> CandidateSeedInputV1:
    target = GrowthTargetIdentityV1(
        kind="skill",
        target_id="daily-three",
        stable_name="daily-three",
        pack_id="daily-three-pack",
    )
    source = None if mode == "genesis" else fence(absent=False, generation=7)
    target_fence = (
        fence(absent=True)
        if mode in {"genesis", "builtin_override"}
        else fence(absent=False, generation=7)
    )
    return CandidateSeedInputV1(
        owner_key=owner_key,
        target=target,
        candidate_mode=mode,
        source_fence=source,
        target_fence=target_fence,
        files=(CandidateFileInputV1("skills/daily-three/SKILL.md", content),),
        manifest_template={
            "schema_version": 2,
            "id": "daily-three-pack",
            "name": "Daily three",
            "compatibility": {
                "deskpet": ">=0.5.0",
                "os": ["windows", "linux", "macos"],
                "architectures": ["x86_64", "aarch64"],
                "python": ">=3.11",
            },
            "entries": {
                "skills": [
                    {
                        "id": "daily-three",
                        "path": "skills/daily-three/SKILL.md",
                        "allowed_tools": [],
                    }
                ]
            },
            "permissions": [],
            "effects": ["read_only"],
            "dependencies": {"python": [], "commands": []},
            "uninstall": {
                "stop_servers": False,
                "remove_environment_when_unreferenced": False,
            },
        },
        permissions=("filesystem_read",),
        effect_topology=effect_topology or {"effects": []},
    )


def proposal() -> StructuredGrowthProposalV1:
    evidence_ids = ("message-1",)
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=evidence_ids,
        evidence_set_hash=evidence_set_hash(evidence_ids),
        target=GrowthTargetIdentityV1(
            kind="skill",
            target_id="daily-three",
            stable_name="daily-three",
            pack_id="daily-three-pack",
        ),
        candidate_mode="genesis",
        target_fence=fence(absent=True),
        hypothesis="Three priorities are easier to execute.",
        structured_diff={"instructions": {"add": ["three priorities"]}},
        expected_improvement="Exactly three priorities are returned.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "three"},),
    )


def test_candidate_identity_and_archive_are_deterministic() -> None:
    builder = DeterministicCandidateBuilder()

    first = builder.build(seed())
    second = builder.build(seed())

    assert first == second
    assert first.package.version.startswith("1.0.0+g.")
    assert len(first.package.version) <= 64
    assert first.package.candidate_manifest_hash == hashlib.sha256(
        first.manifest_bytes
    ).hexdigest()
    assert first.package.archive_hash == hashlib.sha256(
        first.archive_bytes
    ).hexdigest()
    with zipfile.ZipFile(BytesIO(first.archive_bytes)) as archive:
        assert archive.namelist() == [
            "deskpet-pack.json",
            "skills/daily-three/SKILL.md",
        ]
        assert archive.read("deskpet-pack.json") == first.manifest_bytes
    manifest = parse_pack_manifest(dict(first.manifest))
    assert manifest.source.type == "companion_growth"
    assert manifest.source.uri == "companion-growth:daily-three-pack"
    assert manifest.source.revision == first.package.version


def test_seed_changes_for_owner_bytes_and_effect_topology() -> None:
    builder = DeterministicCandidateBuilder()
    base = builder.build(seed())
    other_owner = builder.build(seed(owner_key="companion:profile-b:1"))
    other_bytes = builder.build(seed(content=b"# Different\n"))
    other_effect = builder.build(seed(effect_topology={"effects": ["external_send"]}))

    assert len(
        {
            base.package.candidate_content_hash,
            other_owner.package.candidate_content_hash,
            other_bytes.package.candidate_content_hash,
            other_effect.package.candidate_content_hash,
        }
    ) == 4
    assert len(
        {
            base.package.version,
            other_owner.package.version,
            other_bytes.package.version,
            other_effect.package.version,
        }
    ) == 4


def test_update_uses_exact_source_next_patch_core() -> None:
    product = DeterministicCandidateBuilder().build(seed(mode="update"))
    assert product.package.version.startswith("1.2.4+g.")


def test_candidate_seed_rejects_custom_source_masquerading_as_builtin() -> None:
    with pytest.raises(
        CandidateBuildIdentityError,
        match="candidate_builtin_override_fence_invalid",
    ):
        seed(mode="builtin_override")


def test_injected_digest_collision_fails_closed_on_second_exact_seed() -> None:
    guard = CandidateVersionCollisionGuard()
    builder = DeterministicCandidateBuilder(
        digest=lambda _payload: DIGEST_A,
        collision_guard=guard,
    )
    original = builder.build(seed())

    with pytest.raises(CandidateVersionHashCollision) as exc:
        builder.build(seed(content=b"# Different exact bytes\n"))

    assert exc.value.code == "version_hash_collision"
    assert original.archive_bytes != b""


class AdmissionStore:
    def __init__(self) -> None:
        self.admissions = []

    def admit_explicit_build(self, admission):
        self.admissions.append(admission)
        return {"build_id": admission.build_id, "status": "proposed"}


def trusted(*kinds: RequestedEntryKind) -> TrustedRootBuildRequestV1:
    return TrustedRootBuildRequestV1.issue(
        owner=OWNER,
        owner_key=OWNER_KEY,
        request_id="request-1",
        root_run_id="run-1",
        user_message_ref="message-1",
        user_message_hash=DIGEST_A,
        requested_entry_kinds=kinds,
        explicit_capability_request=True,
    )


def test_admission_routes_skill_before_general_builder_and_is_replay_stable() -> None:
    store = AdmissionStore()
    router = GrowthBuildAdmissionRouter(store)

    first = router.admit(trusted(RequestedEntryKind.SKILL), proposal=proposal())
    second = router.admit(trusted(RequestedEntryKind.SKILL), proposal=proposal())

    assert first.publish_policy is CapabilityBuildPublishPolicy.CANDIDATE_ONLY
    assert first.admission == second.admission
    assert first.admission is not None
    assert first.admission.source_ref.startswith("explicit-user-build:")
    assert len(store.admissions) == 2  # Store owns durable replay dedupe.


def test_real_store_admission_and_build_state_machine_are_durable(tmp_path) -> None:
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=OWNER.profile_id,
        generation=OWNER.profile_generation,
        identity_namespace_hash=DIGEST_A,
    )
    router = GrowthBuildAdmissionRouter(store)

    first = router.admit(
        trusted(RequestedEntryKind.SKILL), proposal=proposal()
    )
    replay = router.admit(
        trusted(RequestedEntryKind.SKILL), proposal=proposal()
    )
    assert first.durable_receipt == replay.durable_receipt
    assert first.admission is not None
    build_id = first.admission.build_id

    permit = store.issue_for_claim(
        build_id=build_id, claim_owner="scheduler-1", claim_epoch=1
    )
    assert (
        store.issue_for_claim(
            build_id=build_id, claim_owner="scheduler-1", claim_epoch=1
        )
        == permit
    )
    state = store.prepare_launch_pending(
        permit, task_workspace="workspace-1"
    )
    assert state.status.value == "launch_pending"
    state = store.ack_child_precreated(
        permit,
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
    )
    state = store.ack_running(
        permit,
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
    )
    assert state.status.value == "running"

    durable = CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
        proposal_ref=permit.proposal_ref,
        proposal_hash=permit.proposal_hash,
        evidence_set_hash=permit.evidence_set_hash,
        target_fence_hash=permit.target_fence_hash,
        validated_draft_hash=DIGEST_A,
        manifest_hash=DIGEST_A,
        archive_hash=DIGEST_B,
        file_set_hash=DIGEST_A,
        effect_topology_hash=DIGEST_B,
    )
    expected = CandidateDraftReceiptExpectationV1(
        **{
            name: getattr(durable, name)
            for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
        }
    )
    state = store.ack_handoff_pending(permit, expectation=expected)
    assert state.status.value == "handoff_pending"
    state = store.ack_built(
        permit,
        expectation=expected,
        handoff={
            "build_id": build_id,
            "candidate_ref": "candidate-1",
            "candidate_hash": DIGEST_A,
        },
    )
    assert state.status.value == "built"


def test_real_store_atomically_composes_exact_staged_material(tmp_path) -> None:
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=OWNER.profile_id,
        generation=OWNER.profile_generation,
        identity_namespace_hash=DIGEST_A,
    )
    admitted = GrowthBuildAdmissionRouter(store).admit(
        trusted(RequestedEntryKind.SKILL), proposal=proposal()
    )
    assert admitted.admission is not None
    permit = store.issue_for_claim(
        build_id=admitted.admission.build_id,
        claim_owner="scheduler-1",
        claim_epoch=1,
    )
    state = store.prepare_launch_pending(permit, task_workspace="workspace-1")
    state = store.ack_child_precreated(
        permit,
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
    )
    state = store.ack_running(
        permit,
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
    )
    product = DeterministicCandidateBuilder().build(seed())
    material_file_set_hash = product.file_set_hash
    material_validated_hash = product.package.candidate_content_hash
    receipt = CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id=state.builder_launch_id,
        child_run_id=state.child_run_id,
        child_start_hash=state.expected_child_start_hash,
        proposal_ref=permit.proposal_ref,
        proposal_hash=permit.proposal_hash,
        evidence_set_hash=permit.evidence_set_hash,
        target_fence_hash=permit.target_fence_hash,
        validated_draft_hash=material_validated_hash,
        manifest_hash=product.package.candidate_manifest_hash,
        archive_hash=product.package.archive_hash,
        file_set_hash=material_file_set_hash,
        effect_topology_hash=product.package.effect_topology_hash,
    )
    expectation = CandidateDraftReceiptExpectationV1(
        **{
            name: getattr(receipt, name)
            for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
        }
    )
    store.ack_handoff_pending(permit, expectation=expectation)

    class ReceiptQuery:
        def read_exact(self, requested):
            requested.verify(receipt)
            return receipt

    class MaterialQuery:
        def read_exact(self, requested):
            return CandidateDraftMaterialV1(
                receipt_id=requested.receipt_id,
                receipt_hash=requested.receipt_hash,
                archive_bytes=product.archive_bytes,
                validated_draft_hash=receipt.validated_draft_hash,
                manifest_hash=receipt.manifest_hash,
                file_set_hash=receipt.file_set_hash,
                effect_topology_hash=receipt.effect_topology_hash,
            )

    service = CandidateCompositionService(
        receipt_query=ReceiptQuery(),
        material_query=MaterialQuery(),
        store=store,
        package_validator=CapabilityPackageValidator(),
    )
    request = CandidateCompositionRequestV1(
        build_id=permit.build_id,
        receipt_expectation=expectation,
    )
    first = service.create_candidate_from_builder_receipt(request)
    second = service.create_candidate_from_builder_receipt(request)

    assert second == first
    with store.read() as db:
        build = db.execute(
            "SELECT status,candidate_ref,candidate_hash FROM candidate_builds WHERE build_id=?",
            (permit.build_id,),
        ).fetchone()
        assert build["status"] == "built"
        assert build["candidate_ref"] == first.candidate_id
        assert len(str(build["candidate_hash"])) == 64
        assert db.execute("SELECT COUNT(*) FROM candidate_artifacts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM candidate_packages").fetchone()[0] == 1


def test_tool_only_build_stays_general_and_governed_output_requires_admission() -> None:
    store = AdmissionStore()
    router = GrowthBuildAdmissionRouter(store)

    result = router.admit(trusted(RequestedEntryKind.TOOL), proposal=None)

    assert result.publish_policy is CapabilityBuildPublishPolicy.GENERAL_INSTALL
    assert store.admissions == []

    untrusted_kind = TrustedRootBuildRequestV1.issue(
        owner=OWNER,
        owner_key=OWNER_KEY,
        request_id="request-1",
        root_run_id="run-1",
        user_message_ref="message-1",
        user_message_hash=DIGEST_A,
        requested_entry_kinds=(RequestedEntryKind.SKILL,),
        explicit_capability_request=False,
    )
    with pytest.raises(GrowthBuildAdmissionError, match="governance_admission_required"):
        router.admit(untrusted_kind, proposal=proposal())


def test_host_only_permit_and_receipt_verify_every_hash() -> None:
    permit = GrowthCandidateBuildPermitV1.issue(
        build_id="build-1",
        owner_key=OWNER_KEY,
        proposal_ref="proposal-1",
        proposal_hash=DIGEST_A,
        evidence_set_hash=DIGEST_B,
        source_fence_hash=DIGEST_A,
        target_fence_hash=DIGEST_B,
        lease_epoch=4,
    )
    receipt_values = {
        "receipt_id": "receipt-1",
        "builder_launch_id": "launch-1",
        "child_run_id": "child-1",
        "child_start_hash": DIGEST_A,
        "proposal_ref": "proposal-1",
        "proposal_hash": DIGEST_A,
        "evidence_set_hash": DIGEST_B,
        "target_fence_hash": DIGEST_B,
        "validated_draft_hash": DIGEST_A,
        "manifest_hash": DIGEST_A,
        "archive_hash": DIGEST_B,
        "file_set_hash": DIGEST_A,
        "effect_topology_hash": DIGEST_B,
    }
    receipt_hash = hashlib.sha256(
        json.dumps(
            {"schema": "candidate-draft-receipt-v1", **receipt_values},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    receipt = CandidateDraftReceiptV1.from_authoritative_row(
        {**receipt_values, "receipt_hash": receipt_hash}
    )

    assert permit.lease_epoch == 4
    assert receipt.receipt_hash == receipt_hash
    CandidateDraftReceiptExpectationV1(
        **{**receipt_values, "receipt_hash": receipt_hash}
    ).verify(receipt)
    with pytest.raises(
        GrowthBuildAdmissionError,
        match="candidate_draft_receipt_archive_hash_mismatch",
    ):
        CandidateDraftReceiptExpectationV1(
            **{
                **receipt_values,
                "receipt_hash": receipt_hash,
                "archive_hash": DIGEST_A,
            }
        ).verify(receipt)
    with pytest.raises(GrowthBuildAdmissionError, match="candidate_draft_receipt_hash_mismatch"):
        CandidateDraftReceiptV1.from_authoritative_row(
            {**receipt_values, "receipt_hash": DIGEST_A}
        )


def test_model_json_cannot_construct_host_authority_contracts() -> None:
    with pytest.raises(GrowthBuildAdmissionError, match="trusted_build_request_host_only"):
        TrustedRootBuildRequestV1(
            owner=OWNER,
            owner_key=OWNER_KEY,
            request_id="request-1",
            root_run_id="run-1",
            user_message_ref="message-1",
            user_message_hash=DIGEST_A,
            requested_entry_kinds=(RequestedEntryKind.SKILL,),
            explicit_capability_request=True,
        )
