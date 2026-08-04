"""Receipt-first composition of one governed capability candidate.

This module deliberately contains no SQL.  The execution database owns the
immutable builder receipt; the Companion store owns current proposal, evidence,
owner and binding facts.  The service joins those two authorities, rebuilds and
validates the exact immutable package, then asks the Store to perform one
atomic package/attempt/build commit.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Protocol

from deskpet.capabilities.package_limits import (
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
)

from .build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptQueryPort,
    CandidateDraftReceiptV1,
)
from .contracts import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    OwnerRef,
)
from .growth import CandidateBindingFenceV1, GrowthTargetIdentityV1

_DIGEST_LENGTH = 64


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_required")
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _required(value, name)
    if len(text) != _DIGEST_LENGTH or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{name}_invalid")
    return text


class CandidateCompositionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CandidateCompositionRequestV1:
    """Host-typed handoff key; loose child JSON is never accepted."""

    build_id: str
    receipt_expectation: CandidateDraftReceiptExpectationV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_id", _required(self.build_id, "build_id"))
        if not isinstance(
            self.receipt_expectation, CandidateDraftReceiptExpectationV1
        ):
            raise TypeError("candidate_draft_receipt_expectation_required")


@dataclass(frozen=True, slots=True)
class CandidateCompositionFactsV1:
    """Current authoritative Companion facts read after the durable receipt."""

    build_id: str
    build_state: str
    facts_revision: int
    facts_hash: str
    owner: OwnerRef
    owner_key: str
    proposal_source_kind: str
    proposal_ref: str
    proposal_hash: str
    evidence_event_ids: tuple[str, ...]
    evidence_set_hash: str
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str
    target: GrowthTargetIdentityV1
    candidate_mode: CandidateMode | str
    target_fence: CandidateBindingFenceV1
    source_fence: CandidateBindingFenceV1 | None = None
    reservation_version: int | None = None
    reflection_job_id: str | None = None
    attempt_generation: int = 1

    def __post_init__(self) -> None:
        for name in (
            "build_id",
            "build_state",
            "owner_key",
            "proposal_source_kind",
            "proposal_ref",
            "builder_launch_id",
            "child_run_id",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.build_state not in {"handoff_pending", "built"}:
            raise CandidateCompositionError("candidate_build_not_handoff_ready")
        if (
            isinstance(self.facts_revision, bool)
            or self.facts_revision < 1
            or isinstance(self.attempt_generation, bool)
            or self.attempt_generation < 1
        ):
            raise ValueError("candidate_composition_revision_invalid")
        if self.reservation_version is not None and (
            isinstance(self.reservation_version, bool)
            or self.reservation_version < 1
        ):
            raise ValueError("candidate_reservation_version_invalid")
        for name in (
            "facts_hash",
            "proposal_hash",
            "evidence_set_hash",
            "child_start_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if not isinstance(self.owner, OwnerRef):
            raise TypeError("candidate_owner_ref_required")
        if not isinstance(self.target, GrowthTargetIdentityV1):
            raise TypeError("candidate_target_identity_required")
        object.__setattr__(self, "candidate_mode", CandidateMode(self.candidate_mode))
        if not isinstance(self.target_fence, CandidateBindingFenceV1):
            raise TypeError("candidate_target_fence_required")
        if self.source_fence is not None and not isinstance(
            self.source_fence, CandidateBindingFenceV1
        ):
            raise TypeError("candidate_source_fence_invalid")
        evidence = tuple(sorted(set(self.evidence_event_ids)))
        if (
            not evidence
            or any(not isinstance(item, str) or not item.strip() for item in evidence)
        ):
            raise CandidateCompositionError("candidate_evidence_required")
        object.__setattr__(self, "evidence_event_ids", evidence)
        if self.target_fence.owner_key != self.owner_key:
            raise CandidateCompositionError("candidate_owner_seed_mismatch")
        if self.target_fence.pack_id != self.target.pack_id:
            raise CandidateCompositionError("candidate_target_fence_mismatch")
        expected = _hash(self._facts_payload())
        if self.facts_hash != expected:
            raise CandidateCompositionError("candidate_current_facts_hash_mismatch")

    def _facts_payload(self) -> dict[str, object]:
        return {
            "schema": "candidate-composition-facts-v1",
            "build_id": self.build_id,
            "build_state": self.build_state,
            "facts_revision": self.facts_revision,
            "owner": {
                "profile_id": self.owner.profile_id,
                "profile_generation": self.owner.profile_generation,
            },
            "owner_key": self.owner_key,
            "proposal_source_kind": self.proposal_source_kind,
            "proposal_ref": self.proposal_ref,
            "proposal_hash": self.proposal_hash,
            "evidence_event_ids": list(self.evidence_event_ids),
            "evidence_set_hash": self.evidence_set_hash,
            "builder_launch_id": self.builder_launch_id,
            "child_run_id": self.child_run_id,
            "child_start_hash": self.child_start_hash,
            "target": self.target.to_dict(),
            "candidate_mode": CandidateMode(self.candidate_mode).value,
            "target_fence": self.target_fence.to_dict(),
            "source_fence": (
                None if self.source_fence is None else self.source_fence.to_dict()
            ),
            "reservation_version": self.reservation_version,
            "reflection_job_id": self.reflection_job_id,
            "attempt_generation": self.attempt_generation,
        }

    @classmethod
    def issue_from_store(
        cls,
        *,
        build_id: str,
        build_state: str,
        facts_revision: int,
        owner: OwnerRef,
        owner_key: str,
        proposal_source_kind: str,
        proposal_ref: str,
        proposal_hash: str,
        evidence_event_ids: tuple[str, ...],
        evidence_set_hash: str,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
        target: GrowthTargetIdentityV1,
        candidate_mode: CandidateMode | str,
        target_fence: CandidateBindingFenceV1,
        source_fence: CandidateBindingFenceV1 | None = None,
        reservation_version: int | None = None,
        reflection_job_id: str | None = None,
        attempt_generation: int = 1,
    ) -> "CandidateCompositionFactsV1":
        values = {
            "build_id": build_id,
            "build_state": build_state,
            "facts_revision": facts_revision,
            "owner": owner,
            "owner_key": owner_key,
            "proposal_source_kind": proposal_source_kind,
            "proposal_ref": proposal_ref,
            "proposal_hash": proposal_hash,
            "evidence_event_ids": tuple(sorted(set(evidence_event_ids))),
            "evidence_set_hash": evidence_set_hash,
            "builder_launch_id": builder_launch_id,
            "child_run_id": child_run_id,
            "child_start_hash": child_start_hash,
            "target": target,
            "candidate_mode": candidate_mode,
            "target_fence": target_fence,
            "source_fence": source_fence,
            "reservation_version": reservation_version,
            "reflection_job_id": reflection_job_id,
            "attempt_generation": attempt_generation,
        }
        probe = object.__new__(cls)
        # The payload helper only reads fields, so it can calculate the Store
        # snapshot token before the frozen dataclass performs validation.
        for name, value in values.items():
            object.__setattr__(probe, name, value)
        return cls(**values, facts_hash=_hash(probe._facts_payload()))


@dataclass(frozen=True, slots=True)
class CandidateCompositionCommitV1:
    owner: OwnerRef
    facts_revision: int
    facts_hash: str
    package: CandidatePackage
    attempt: CandidateAttempt
    receipt: CandidateDraftReceiptV1
    validated_package_ref: ValidatedCapabilityPackageRefV1


@dataclass(frozen=True, slots=True)
class CandidateCompositionCommitResultV1:
    build_id: str
    candidate_id: str
    package_id: str
    build_state: str = "built"

    def __post_init__(self) -> None:
        for name in ("build_id", "candidate_id", "package_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.build_state != "built":
            raise CandidateCompositionError("candidate_commit_not_built")


@dataclass(frozen=True, slots=True)
class ExactCandidateBundleV1:
    """Owner-fenced immutable candidate package and its trusted build receipt."""

    owner: OwnerRef
    candidate_id: str
    candidate_status: str
    build_id: str
    build_status: str
    candidate_hash: str
    proposal_ref: str
    proposal_hash: str
    evidence_set_hash: str
    target_fence_hash: str
    package: CandidatePackage
    receipt: CandidateDraftReceiptV1

    def __post_init__(self) -> None:
        if not isinstance(self.owner, OwnerRef):
            raise TypeError("candidate_bundle_owner_required")
        if not isinstance(self.package, CandidatePackage):
            raise TypeError("candidate_bundle_package_required")
        if not isinstance(self.receipt, CandidateDraftReceiptV1):
            raise TypeError("candidate_bundle_receipt_required")
        for name in (
            "candidate_id",
            "candidate_status",
            "build_id",
            "build_status",
            "proposal_ref",
        ):
            object.__setattr__(
                self, name, _required(getattr(self, name), name)
            )
        for name in (
            "candidate_hash",
            "proposal_hash",
            "evidence_set_hash",
            "target_fence_hash",
        ):
            object.__setattr__(
                self, name, _digest(getattr(self, name), name)
            )
        if self.build_status != "built":
            raise CandidateCompositionError(
                "candidate_bundle_build_not_committed"
            )
        expected = {
            "candidate_hash": self.package.candidate_package_hash,
            "proposal_ref": self.receipt.proposal_ref,
            "proposal_hash": self.receipt.proposal_hash,
            "evidence_set_hash": self.receipt.evidence_set_hash,
            "target_fence_hash": self.receipt.target_fence_hash,
        }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise CandidateCompositionError(
                    f"candidate_bundle_{name}_mismatch"
                )
        receipt_package = (
            self.receipt.validated_draft_hash,
            self.receipt.manifest_hash,
            self.receipt.archive_hash,
            self.receipt.effect_topology_hash,
        )
        package_identity = (
            self.package.candidate_content_hash,
            self.package.candidate_manifest_hash,
            self.package.archive_hash,
            self.package.effect_topology_hash,
        )
        if receipt_package != package_identity:
            raise CandidateCompositionError(
                "candidate_bundle_receipt_package_mismatch"
            )


class CandidateCompositionStorePort(Protocol):
    """Store implementation must CAS the snapshot and commit in one UoW."""

    def read_current_candidate_composition(
        self, *, build_id: str
    ) -> CandidateCompositionFactsV1: ...

    def commit_candidate_build(
        self, commit: CandidateCompositionCommitV1
    ) -> CandidateCompositionCommitResultV1: ...


@dataclass(frozen=True, slots=True)
class CandidateDraftMaterialV1:
    """Exact host-validated staging bytes bound to one durable receipt."""

    receipt_id: str
    receipt_hash: str
    archive_bytes: bytes
    validated_draft_hash: str
    manifest_hash: str
    file_set_hash: str
    effect_topology_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _required(self.receipt_id, "receipt_id"))
        object.__setattr__(
            self, "receipt_hash", _digest(self.receipt_hash, "receipt_hash")
        )
        if not isinstance(self.archive_bytes, bytes) or not self.archive_bytes:
            raise TypeError("candidate_draft_archive_required")
        for name in (
            "validated_draft_hash",
            "manifest_hash",
            "file_set_hash",
            "effect_topology_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))


class CandidateDraftMaterialQueryPort(Protocol):
    """Resolve existing validated staging bytes; never rerun the child/builder."""

    def read_exact(
        self, receipt: CandidateDraftReceiptV1
    ) -> CandidateDraftMaterialV1: ...


class CandidateCompositionService:
    def __init__(
        self,
        *,
        receipt_query: CandidateDraftReceiptQueryPort,
        material_query: CandidateDraftMaterialQueryPort,
        store: CandidateCompositionStorePort,
        package_validator: CapabilityPackageValidator | None = None,
    ) -> None:
        self._receipt_query = receipt_query
        self._material_query = material_query
        self._store = store
        self._package_validator = package_validator or CapabilityPackageValidator()

    def create_candidate_from_builder_receipt(
        self, request: CandidateCompositionRequestV1
    ) -> CandidateCompositionCommitResultV1:
        if not isinstance(request, CandidateCompositionRequestV1):
            raise CandidateCompositionError("candidate_composition_typed_request_required")

        # Cross-database ordering is deliberate: no Companion writer is held
        # while the execution receipt is read.
        receipt = self._receipt_query.read_exact(request.receipt_expectation)
        if not isinstance(receipt, CandidateDraftReceiptV1):
            raise CandidateCompositionError("candidate_draft_receipt_type_invalid")

        facts = self._store.read_current_candidate_composition(
            build_id=request.build_id
        )
        if not isinstance(facts, CandidateCompositionFactsV1):
            raise CandidateCompositionError("candidate_current_facts_type_invalid")
        self._verify_receipt_against_current_facts(receipt, facts)

        material = self._material_query.read_exact(receipt)
        if not isinstance(material, CandidateDraftMaterialV1):
            raise CandidateCompositionError("candidate_draft_material_type_invalid")
        if (
            material.receipt_id != receipt.receipt_id
            or material.receipt_hash != receipt.receipt_hash
        ):
            raise CandidateCompositionError("candidate_draft_material_receipt_mismatch")
        package = self._package_from_material(facts, material, receipt)
        validated_ref = self._package_validator.validate_zip_archive(
            material.archive_bytes,
            source_kind="companion_growth",
            declared_paths=(
                "deskpet-pack.json",
                *(item.relative_path for item in package.files),
            ),
        )
        if (
            validated_ref.archive_hash != receipt.archive_hash
            or validated_ref.manifest_hash != receipt.manifest_hash
        ):
            raise CandidateCompositionError("candidate_package_validator_hash_mismatch")

        attempt = self._attempt(facts, package, receipt)
        result = self._store.commit_candidate_build(
            CandidateCompositionCommitV1(
                owner=facts.owner,
                facts_revision=facts.facts_revision,
                facts_hash=facts.facts_hash,
                package=package,
                attempt=attempt,
                receipt=receipt,
                validated_package_ref=validated_ref,
            )
        )
        if not isinstance(result, CandidateCompositionCommitResultV1):
            raise CandidateCompositionError("candidate_commit_result_type_invalid")
        if (
            result.build_id != facts.build_id
            or result.candidate_id != attempt.candidate_id
            or result.package_id != package.package_id
        ):
            raise CandidateCompositionError("candidate_commit_result_mismatch")
        return result

    @staticmethod
    def _verify_receipt_against_current_facts(
        receipt: CandidateDraftReceiptV1,
        facts: CandidateCompositionFactsV1,
    ) -> None:
        target_fence_hash = _hash(facts.target_fence.to_dict())
        expected = {
            "builder_launch_id": facts.builder_launch_id,
            "child_run_id": facts.child_run_id,
            "child_start_hash": facts.child_start_hash,
            "proposal_ref": facts.proposal_ref,
            "proposal_hash": facts.proposal_hash,
            "evidence_set_hash": facts.evidence_set_hash,
            "target_fence_hash": target_fence_hash,
        }
        for name, value in expected.items():
            if getattr(receipt, name) != value:
                raise CandidateCompositionError(
                    f"candidate_current_{name}_mismatch"
                )

    @staticmethod
    def _package_from_material(
        facts: CandidateCompositionFactsV1,
        material: CandidateDraftMaterialV1,
        receipt: CandidateDraftReceiptV1,
    ) -> CandidatePackage:
        expected = {
            "validated_draft_hash": material.validated_draft_hash,
            "manifest_hash": material.manifest_hash,
            "archive_hash": hashlib.sha256(material.archive_bytes).hexdigest(),
            "file_set_hash": material.file_set_hash,
            "effect_topology_hash": material.effect_topology_hash,
        }
        for name, value in expected.items():
            if getattr(receipt, name) != value:
                raise CandidateCompositionError(
                    f"candidate_receipt_{name}_mismatch"
                )
        try:
            with zipfile.ZipFile(io.BytesIO(material.archive_bytes)) as archive:
                infos = tuple(archive.infolist())
                members = tuple((item.filename, archive.read(item)) for item in infos)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise CandidateCompositionError("candidate_draft_archive_invalid") from exc
        if not members or members[0][0] != "deskpet-pack.json":
            raise CandidateCompositionError("candidate_manifest_missing")
        manifest_bytes = members[0][1]
        if hashlib.sha256(manifest_bytes).hexdigest() != receipt.manifest_hash:
            raise CandidateCompositionError("candidate_receipt_manifest_hash_mismatch")
        file_set_hash = _hash(
            {
                "schema": "candidate-file-set-v1",
                "files": [
                    {
                        "path": path,
                        "mode": 0o644,
                        "hash": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                    for path, content in members[1:]
                ],
            }
        )
        if file_set_hash != receipt.file_set_hash:
            raise CandidateCompositionError("candidate_receipt_file_set_hash_mismatch")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateCompositionError("candidate_manifest_invalid") from exc
        if not isinstance(manifest, dict):
            raise CandidateCompositionError("candidate_manifest_invalid")
        pack_id = str(manifest.get("id") or "")
        version = str(manifest.get("version") or "")
        if pack_id != facts.target.pack_id or not version:
            raise CandidateCompositionError("candidate_manifest_target_mismatch")

        files: list[CandidatePackageFile] = []
        blobs: list[CandidatePackageBlob] = []
        for path, content in members[1:]:
            digest = hashlib.sha256(content).hexdigest()
            blob_id = _hash(
                {
                    "schema": "candidate-file-blob-id-v1",
                    "path": path,
                    "content_hash": digest,
                }
            )
            files.append(
                CandidatePackageFile(
                    relative_path=path,
                    file_kind="file",
                    content_hash=digest,
                    size_bytes=len(content),
                    blob_id=blob_id,
                    file_mode=0o644,
                )
            )
            blobs.append(
                CandidatePackageBlob(
                    blob_kind="file",
                    blob_id=blob_id,
                    content_hash=digest,
                    payload=content,
                )
            )
        blobs.extend(
            (
                CandidatePackageBlob(
                    blob_kind="manifest",
                    blob_id=receipt.manifest_hash,
                    content_hash=hashlib.sha256(manifest_bytes).hexdigest(),
                    payload=manifest_bytes,
                ),
                CandidatePackageBlob(
                    blob_kind="archive",
                    blob_id=receipt.archive_hash,
                    content_hash=receipt.archive_hash,
                    payload=material.archive_bytes,
                ),
            )
        )
        package_hash = _hash(
            {
                "schema": "capability-candidate-package-v1",
                "pack_id": pack_id,
                "version": version,
                "candidate_content_hash": receipt.validated_draft_hash,
                "candidate_manifest_hash": receipt.manifest_hash,
                "archive_hash": receipt.archive_hash,
                "file_set_hash": receipt.file_set_hash,
                "effect_topology_hash": receipt.effect_topology_hash,
            }
        )
        package_id = _hash(
            {
                "schema": "candidate-package-id-v1",
                "owner_key": facts.owner_key,
                "pack_id": pack_id,
                "candidate_package_hash": package_hash,
            }
        )
        return CandidatePackage(
            package_id=package_id,
            candidate_mode=CandidateMode(facts.candidate_mode),
            pack_id=pack_id,
            version=version,
            candidate_content_hash=receipt.validated_draft_hash,
            candidate_manifest_hash=receipt.manifest_hash,
            candidate_package_hash=package_hash,
            archive_hash=receipt.archive_hash,
            effect_topology_hash=receipt.effect_topology_hash,
            source_facts=(
                {}
                if facts.source_fence is None
                else facts.source_fence.to_dict()
            ),
            target_facts=facts.target_fence.to_dict(),
            files=tuple(files),
            blobs=tuple(blobs),
        )

    @staticmethod
    def _attempt(
        facts: CandidateCompositionFactsV1,
        package: CandidatePackage,
        receipt: CandidateDraftReceiptV1,
    ) -> CandidateAttempt:
        identity = {
            "schema": "candidate-attempt-identity-v1",
            "owner": {
                "profile_id": facts.owner.profile_id,
                "profile_generation": facts.owner.profile_generation,
            },
            "proposal_source_kind": facts.proposal_source_kind,
            "proposal_ref": facts.proposal_ref,
            "proposal_hash": facts.proposal_hash,
            "evidence_set_hash": facts.evidence_set_hash,
            "candidate_package_hash": package.candidate_package_hash,
        }
        digest = _hash(identity)
        source = facts.source_fence
        target = facts.target_fence
        return CandidateAttempt(
            candidate_id=f"candidate:{digest}",
            candidate_attempt_key=f"candidate-attempt:{digest}",
            proposal_source_kind=facts.proposal_source_kind,
            proposal_source_ref=facts.proposal_ref,
            source_hash=facts.proposal_hash,
            candidate_mode=package.candidate_mode,
            target_id=facts.target.target_id,
            target_owner_key=target.owner_key,
            target_scope=target.scope,
            target_scope_key=target.scope_key,
            target_expected_absent=target.expected_absent,
            target_expected_binding_generation=target.binding_generation,
            evidence_event_ids=facts.evidence_event_ids,
            evidence_set_hash=facts.evidence_set_hash,
            builder_receipt_ref=receipt.receipt_id,
            builder_receipt_hash=receipt.receipt_hash,
            source_owner_key=None if source is None else source.owner_key,
            source_scope=None if source is None else source.scope,
            source_scope_key=None if source is None else source.scope_key,
            source_version=None if source is None else source.version,
            source_manifest_hash=None if source is None else source.manifest_hash,
            source_binding_generation=(
                None if source is None else source.binding_generation
            ),
            reservation_version=facts.reservation_version,
            reflection_job_id=facts.reflection_job_id,
            build_id=facts.build_id,
            attempt_generation=facts.attempt_generation,
            reason_code="candidate_created_from_builder_receipt",
        )


__all__ = [
    "CandidateCompositionCommitResultV1",
    "CandidateCompositionCommitV1",
    "CandidateCompositionError",
    "CandidateCompositionFactsV1",
    "CandidateCompositionRequestV1",
    "CandidateCompositionService",
    "CandidateCompositionStorePort",
    "CandidateDraftMaterialQueryPort",
    "CandidateDraftMaterialV1",
    "ExactCandidateBundleV1",
]
