from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from deskpet.companion.build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
    GrowthBuildAdmissionError,
)
from deskpet.companion.candidate_builder import (
    CandidateFileInputV1,
    CandidateSeedInputV1,
    DeterministicCandidateBuilder,
)
from deskpet.companion.candidate_composition import (
    CandidateCompositionCommitResultV1,
    CandidateCompositionError,
    CandidateCompositionFactsV1,
    CandidateCompositionRequestV1,
    CandidateCompositionService,
    CandidateDraftMaterialV1,
)
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed() -> CandidateSeedInputV1:
    return CandidateSeedInputV1(
        owner_key="companion:profile-1:1",
        target=GrowthTargetIdentityV1(
            kind="skill",
            target_id="daily-three",
            stable_name="daily-three",
            pack_id="personal.daily-three",
        ),
        candidate_mode="genesis",
        target_fence=CandidateBindingFenceV1(
            owner_key="companion:profile-1:1",
            scope="user",
            scope_key="profile-1",
            pack_id="personal.daily-three",
            expected_absent=True,
            binding_generation=0,
        ),
        files=(
            CandidateFileInputV1(
                "skills/daily-three/SKILL.md",
                (
                    b"---\nname: daily-three\n"
                    b"description: Choose three daily priorities\n"
                    b"---\nReturn the three most important tasks.\n"
                ),
            ),
        ),
        manifest_template={
            "schema_version": 2,
            "id": "personal.daily-three",
            "name": "Daily three",
            "entries": {
                "skills": [
                    {
                        "id": "daily-three",
                        "path": "skills/daily-three/SKILL.md",
                    }
                ]
            },
            "permissions": [],
        },
        effect_topology={"effects": []},
    )


def _facts(
    *,
    build_id: str = "build-1",
    proposal_ref: str = "proposal-1",
    proposal_hash: str | None = None,
    evidence_ids: tuple[str, ...] = ("event-1",),
    evidence_hash: str | None = None,
    seed: CandidateSeedInputV1 | None = None,
) -> CandidateCompositionFactsV1:
    return CandidateCompositionFactsV1.issue_from_store(
        build_id=build_id,
        build_state="handoff_pending",
        facts_revision=1,
        owner=OwnerRef("profile-1", 1),
        owner_key="companion:profile-1:1",
        proposal_source_kind="reflection",
        proposal_ref=proposal_ref,
        proposal_hash=proposal_hash or _hash(proposal_ref),
        evidence_event_ids=evidence_ids,
        evidence_set_hash=evidence_hash or _hash("|".join(evidence_ids)),
        builder_launch_id=f"builder:{build_id}",
        child_run_id=f"child:{build_id}",
        child_start_hash=_hash(f"start:{build_id}"),
        target=(seed or _seed()).target,
        candidate_mode=(seed or _seed()).candidate_mode,
        target_fence=(seed or _seed()).target_fence,
        source_fence=(seed or _seed()).source_fence,
        reservation_version=1,
        reflection_job_id=f"reflection:{build_id}",
    )


def _product(seed: CandidateSeedInputV1 | None = None):
    return DeterministicCandidateBuilder().build(seed or _seed())


def _material_values(product):
    return product.package.candidate_content_hash, product.file_set_hash


def _receipt(
    facts: CandidateCompositionFactsV1, product=None
) -> CandidateDraftReceiptV1:
    product = product or _product()
    validated_draft_hash, file_set_hash = _material_values(product)
    return CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id=facts.builder_launch_id,
        child_run_id=facts.child_run_id,
        child_start_hash=facts.child_start_hash,
        proposal_ref=facts.proposal_ref,
        proposal_hash=facts.proposal_hash,
        evidence_set_hash=facts.evidence_set_hash,
        target_fence_hash=_hash(
            json.dumps(
                facts.target_fence.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        validated_draft_hash=validated_draft_hash,
        manifest_hash=product.package.candidate_manifest_hash,
        archive_hash=product.package.archive_hash,
        file_set_hash=file_set_hash,
        effect_topology_hash=product.package.effect_topology_hash,
    )


def _expectation(receipt: CandidateDraftReceiptV1):
    return CandidateDraftReceiptExpectationV1(
        **{
            name: getattr(receipt, name)
            for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
        }
    )


class FakeReceiptQuery:
    def __init__(self, receipts, calls: list[str]) -> None:
        self.receipts = receipts
        self.calls = calls

    def read_exact(self, expectation):
        self.calls.append("receipt")
        value = self.receipts.get((expectation.receipt_id, expectation.receipt_hash))
        if value is None:
            raise GrowthBuildAdmissionError("candidate_draft_receipt_not_found")
        if isinstance(value, CandidateDraftReceiptV1):
            expectation.verify(value)
        return value


class FakeCompositionStore:
    def __init__(self, facts, calls: list[str]) -> None:
        self.facts = {item.build_id: item for item in facts}
        self.calls = calls
        self.results = {}
        self.package_ids: set[str] = set()
        self.commit_count = 0
        self.drift_on_commit = False

    def read_current_candidate_composition(self, *, build_id: str):
        self.calls.append("current")
        return self.facts[build_id]

    def commit_candidate_build(self, commit):
        self.calls.append("commit")
        self.commit_count += 1
        current = self.facts[commit.attempt.build_id]
        if (
            self.drift_on_commit
            or commit.facts_revision != current.facts_revision
            or commit.facts_hash != current.facts_hash
        ):
            raise CandidateCompositionError("candidate_current_facts_drift")
        result = self.results.get(commit.attempt.candidate_id)
        if result is None:
            result = CandidateCompositionCommitResultV1(
                build_id=commit.attempt.build_id,
                candidate_id=commit.attempt.candidate_id,
                package_id=commit.package.package_id,
            )
            self.results[commit.attempt.candidate_id] = result
            self.package_ids.add(commit.package.package_id)
        elif result.package_id != commit.package.package_id:
            raise CandidateCompositionError("candidate_replay_package_drift")
        return result


class FakeMaterialQuery:
    def __init__(self, products, calls: list[str]) -> None:
        self.products = products
        self.calls = calls

    def read_exact(self, receipt):
        self.calls.append("material")
        product = self.products[receipt.builder_launch_id]
        validated_draft_hash, file_set_hash = _material_values(product)
        return CandidateDraftMaterialV1(
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.receipt_hash,
            archive_bytes=product.archive_bytes,
            validated_draft_hash=validated_draft_hash,
            manifest_hash=product.package.candidate_manifest_hash,
            file_set_hash=file_set_hash,
            effect_topology_hash=product.package.effect_topology_hash,
        )


def _service(*facts: CandidateCompositionFactsV1):
    calls: list[str] = []
    products = {
        item.builder_launch_id: _product()
        for item in facts
    }
    receipts = [_receipt(item, products[item.builder_launch_id]) for item in facts]
    query = FakeReceiptQuery(
        {(item.receipt_id, item.receipt_hash): item for item in receipts},
        calls,
    )
    material_query = FakeMaterialQuery(products, calls)
    store = FakeCompositionStore(facts, calls)
    return (
        CandidateCompositionService(
            receipt_query=query,
            material_query=material_query,
            store=store,
        ),
        store,
        calls,
        receipts,
    )


def test_receipt_is_read_before_current_facts_and_one_atomic_store_port_commits() -> None:
    facts = _facts()
    service, store, calls, receipts = _service(facts)

    result = service.create_candidate_from_builder_receipt(
        CandidateCompositionRequestV1(
            build_id=facts.build_id,
            receipt_expectation=_expectation(receipts[0]),
        )
    )

    assert calls == ["receipt", "current", "material", "commit"]
    assert result.build_state == "built"
    assert result.package_id in store.package_ids
    assert store.commit_count == 1


def test_same_proposal_replay_returns_same_candidate_and_package() -> None:
    facts = _facts()
    service, store, _calls, receipts = _service(facts)
    request = CandidateCompositionRequestV1(
        build_id=facts.build_id,
        receipt_expectation=_expectation(receipts[0]),
    )

    first = service.create_candidate_from_builder_receipt(request)
    second = service.create_candidate_from_builder_receipt(request)

    assert second == first
    assert len(store.results) == 1
    assert len(store.package_ids) == 1


def test_new_evidence_reuses_exact_package_but_creates_new_attempt() -> None:
    first_facts = _facts()
    second_facts = _facts(
        build_id="build-2",
        proposal_ref="proposal-2",
        evidence_ids=("event-1", "event-2"),
    )
    service, store, _calls, receipts = _service(first_facts, second_facts)

    first = service.create_candidate_from_builder_receipt(
        CandidateCompositionRequestV1(
            build_id=first_facts.build_id,
            receipt_expectation=_expectation(receipts[0]),
        )
    )
    second = service.create_candidate_from_builder_receipt(
        CandidateCompositionRequestV1(
            build_id=second_facts.build_id,
            receipt_expectation=_expectation(receipts[1]),
        )
    )

    assert first.package_id == second.package_id
    assert first.candidate_id != second.candidate_id
    assert len(store.package_ids) == 1
    assert len(store.results) == 2


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("proposal_hash", "candidate_current_proposal_hash_mismatch"),
        ("evidence_set_hash", "candidate_current_evidence_set_hash_mismatch"),
        ("target_fence_hash", "candidate_current_target_fence_hash_mismatch"),
        ("manifest_hash", "candidate_receipt_manifest_hash_mismatch"),
        ("archive_hash", "candidate_receipt_archive_hash_mismatch"),
        ("file_set_hash", "candidate_receipt_file_set_hash_mismatch"),
        ("effect_topology_hash", "candidate_receipt_effect_topology_hash_mismatch"),
    ],
)
def test_any_receipt_or_package_hash_drift_fails_before_store_commit(
    field: str, code: str
) -> None:
    facts = _facts()
    service, store, _calls, receipts = _service(facts)
    original = receipts[0]
    values = {
        name: getattr(original, name)
        for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
    }
    values[field] = _hash(f"drift:{field}")
    expectation = CandidateDraftReceiptExpectationV1(**values)
    # Simulate an independently valid but wrong host receipt so the service,
    # not the fake query, proves the current/package comparison.
    drifted = CandidateDraftReceiptV1.issue_from_host(
        **{
            name: value
            for name, value in values.items()
            if name not in {"receipt_id", "receipt_hash"}
        }
    )
    expectation = _expectation(drifted)
    service._receipt_query.receipts[  # type: ignore[attr-defined]
        (drifted.receipt_id, drifted.receipt_hash)
    ] = drifted

    with pytest.raises(CandidateCompositionError) as caught:
        service.create_candidate_from_builder_receipt(
            CandidateCompositionRequestV1(
                build_id=facts.build_id,
                receipt_expectation=expectation,
            )
        )

    assert caught.value.code == code
    assert store.commit_count == 0


def test_missing_receipt_raw_receipt_and_commit_time_drift_all_fail_closed() -> None:
    facts = _facts()
    service, store, _calls, receipts = _service(facts)
    request = CandidateCompositionRequestV1(
        build_id=facts.build_id,
        receipt_expectation=_expectation(receipts[0]),
    )

    service._receipt_query.receipts.clear()  # type: ignore[attr-defined]
    with pytest.raises(GrowthBuildAdmissionError, match="not_found"):
        service.create_candidate_from_builder_receipt(request)
    assert store.commit_count == 0

    service._receipt_query.receipts[  # type: ignore[attr-defined]
        (receipts[0].receipt_id, receipts[0].receipt_hash)
    ] = {"receipt_id": receipts[0].receipt_id}
    with pytest.raises(
        CandidateCompositionError, match="candidate_draft_receipt_type_invalid"
    ):
        service.create_candidate_from_builder_receipt(request)
    assert store.commit_count == 0

    service._receipt_query.receipts[  # type: ignore[attr-defined]
        (receipts[0].receipt_id, receipts[0].receipt_hash)
    ] = receipts[0]
    store.drift_on_commit = True
    with pytest.raises(CandidateCompositionError, match="candidate_current_facts_drift"):
        service.create_candidate_from_builder_receipt(request)
    assert store.commit_count == 1


def test_raw_request_or_raw_current_facts_are_never_accepted() -> None:
    facts = _facts()
    service, store, _calls, receipts = _service(facts)

    with pytest.raises(
        CandidateCompositionError, match="candidate_composition_typed_request_required"
    ):
        service.create_candidate_from_builder_receipt(  # type: ignore[arg-type]
            {"build_id": facts.build_id, "receipt": receipts[0].receipt_id}
        )

    store.facts[facts.build_id] = {"build_id": facts.build_id}
    with pytest.raises(
        CandidateCompositionError, match="candidate_current_facts_type_invalid"
    ):
        service.create_candidate_from_builder_receipt(
            CandidateCompositionRequestV1(
                build_id=facts.build_id,
                receipt_expectation=_expectation(receipts[0]),
            )
        )
