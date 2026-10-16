"""Candidate composition, bundles, creation and transitions."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

from deskpet.capabilities.package_limits import ValidatedCapabilityPackageRefV1

from ..build_admission import CandidateDraftReceiptV1
from ..candidate_composition import (
    CandidateCompositionCommitResultV1,
    CandidateCompositionCommitV1,
    CandidateCompositionError,
    CandidateCompositionFactsV1,
    ExactCandidateBundleV1,
)
from ..contracts import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    CompanionConflictError,
    CompanionStateError,
    OwnerRef,
)
from ..growth import CandidateBindingFenceV1, GrowthTargetIdentityV1
from .core import CompanionStoreCore
from .support import _bytes_hash, canonical_hash, canonical_json


class CandidateMixin(CompanionStoreCore):
    """Candidate composition, bundles, creation and transitions."""

    __slots__ = ()

    @staticmethod
    def _candidate_composition_from_db(db: sqlite3.Connection, build_id: str) -> Any:

        rows = db.execute(
            "SELECT * FROM candidate_builds WHERE build_id=?",
            (str(build_id).strip(),),
        ).fetchall()
        if len(rows) != 1:
            raise CompanionStateError(
                "candidate_build_missing" if not rows else "candidate_build_identity_ambiguous"
            )
        row = rows[0]
        if str(row["status"]) not in {"handoff_pending", "built"}:
            raise CompanionStateError("candidate_build_not_handoff_ready")
        owner = OwnerRef(str(row["profile_id"]), int(row["profile_generation"]))
        proposal = json.loads(str(row["proposal_json"]))
        target_raw = proposal.get("target")
        if not isinstance(target_raw, Mapping):
            raise CompanionStateError("candidate_proposal_target_missing")
        target = GrowthTargetIdentityV1(
            kind=str(target_raw.get("kind") or ""),
            target_id=str(target_raw.get("target_id") or ""),
            stable_name=str(target_raw.get("stable_name") or ""),
            pack_id=str(target_raw.get("pack_id") or ""),
        )
        target_fence_raw = json.loads(str(row["target_fence_json"]))
        target_fence = CandidateBindingFenceV1(**target_fence_raw)
        source_fence = None
        if row["source_fence_json"] not in {None, "", "null"}:
            source_fence = CandidateBindingFenceV1(**json.loads(str(row["source_fence_json"])))
        detail = db.execute(
            """SELECT detail_version FROM companion_detail_versions
               WHERE profile_id=? AND profile_generation=?""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if detail is None:
            raise CompanionStateError("candidate_detail_version_missing")

        existing_attempt = db.execute(
            """SELECT reservation_version,attempt_generation
               FROM candidate_artifacts
               WHERE profile_id=? AND profile_generation=?
                 AND proposal_source_kind=? AND proposal_source_ref=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                str(row["source_kind"]),
                str(row["source_ref"]),
            ),
        ).fetchone()
        reservation_version = None
        if existing_attempt is not None:
            reservation_version = existing_attempt["reservation_version"]
            attempt_generation = int(existing_attempt["attempt_generation"])
        else:
            prior = db.execute(
                """SELECT COALESCE(MAX(attempt_generation),0)
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                (owner.profile_id, owner.profile_generation, target.target_id),
            ).fetchone()
            attempt_generation = int(prior[0]) + 1
            if str(row["candidate_mode"]) == "genesis":
                reservation = db.execute(
                    """SELECT reservation_version
                       FROM growth_target_reservations
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND status='held'
                       ORDER BY reservation_version DESC LIMIT 1""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        target.target_id,
                    ),
                ).fetchone()
                if reservation is None:
                    raise CompanionStateError("candidate_reservation_missing")
                reservation_version = int(reservation["reservation_version"])

        return CandidateCompositionFactsV1.issue_from_store(
            build_id=str(row["build_id"]),
            build_state=str(row["status"]),
            facts_revision=int(detail["detail_version"]),
            owner=owner,
            owner_key=target_fence.owner_key,
            proposal_source_kind=str(row["source_kind"]),
            proposal_ref=str(row["proposal_ref"]),
            proposal_hash=str(row["proposal_hash"]),
            evidence_event_ids=tuple(json.loads(str(row["evidence_set_json"]))),
            evidence_set_hash=str(row["evidence_set_hash"]),
            builder_launch_id=str(row["builder_launch_id"]),
            child_run_id=str(row["builder_child_run_id"]),
            child_start_hash=str(row["expected_child_start_hash"]),
            target=target,
            candidate_mode=str(row["candidate_mode"]),
            target_fence=target_fence,
            source_fence=source_fence,
            reservation_version=(None if reservation_version is None else int(reservation_version)),
            reflection_job_id=(
                None if row["reflection_job_id"] is None else str(row["reflection_job_id"])
            ),
            attempt_generation=attempt_generation,
        )

    def read_current_candidate_composition(self, *, build_id: str) -> Any:
        """Read the current Store-owned facts after the execution receipt."""

        with self.read() as db:
            return self._candidate_composition_from_db(db, build_id)

    def commit_candidate_build(self, commit: Any) -> Any:
        """CAS current facts and atomically freeze package, attempt and build."""

        if not isinstance(commit, CandidateCompositionCommitV1):
            raise CandidateCompositionError("candidate_commit_type_invalid")
        if not isinstance(commit.validated_package_ref, ValidatedCapabilityPackageRefV1):
            raise CandidateCompositionError("candidate_validated_ref_required")
        package = commit.package
        receipt = commit.receipt
        validated = commit.validated_package_ref
        if (
            package.candidate_manifest_hash != receipt.manifest_hash
            or package.archive_hash != receipt.archive_hash
            or package.effect_topology_hash != receipt.effect_topology_hash
            or validated.source_kind != "companion_growth"
            or validated.manifest_hash != receipt.manifest_hash
            or validated.archive_hash != receipt.archive_hash
        ):
            raise CandidateCompositionError("candidate_commit_hash_mismatch")

        with self._write() as db:
            current = self._candidate_composition_from_db(db, commit.attempt.build_id or "")
            if (
                current.owner != commit.owner
                or current.facts_revision != commit.facts_revision
                or current.facts_hash != commit.facts_hash
            ):
                raise CandidateCompositionError("candidate_current_facts_drift")
            artifact = self.create_candidate(commit.owner, commit.package, commit.attempt)
            row = self._find_candidate_build(db, current.build_id)
            expected_candidate_ref = str(artifact["candidate_id"])
            expected_candidate_hash = package.candidate_package_hash
            if str(row["status"]) == "built":
                if (
                    str(row["candidate_ref"] or "") != expected_candidate_ref
                    or str(row["candidate_hash"] or "") != expected_candidate_hash
                    or str(row["draft_receipt_ref"] or "") != receipt.receipt_id
                    or str(row["draft_receipt_hash"] or "") != receipt.receipt_hash
                ):
                    raise CandidateCompositionError("candidate_replay_package_drift")
            else:
                cursor = db.execute(
                    """UPDATE candidate_builds
                       SET status='built',draft_receipt_ref=?,
                           draft_receipt_hash=?,candidate_ref=?,candidate_hash=?,
                           lease_expires_at=NULL,reason_code='candidate_built',
                           updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?
                         AND status='handoff_pending'""",
                    (
                        receipt.receipt_id,
                        receipt.receipt_hash,
                        expected_candidate_ref,
                        expected_candidate_hash,
                        self._now(),
                        commit.owner.profile_id,
                        commit.owner.profile_generation,
                        current.build_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CandidateCompositionError("candidate_build_commit_lost")
            return CandidateCompositionCommitResultV1(
                build_id=current.build_id,
                candidate_id=expected_candidate_ref,
                package_id=package.package_id,
            )

    def get_exact_candidate_bundle(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Any:
        """Rebuild and verify one committed candidate without leaking SQLite.

        The returned ``ExactCandidateBundleV1`` is suitable for immutable risk,
        evaluation and activation gates.  Every package row, file, blob,
        provenance link, build identity and receipt hash is checked under the
        exact owner generation before any bytes leave the Store boundary.
        """

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT
                     a.candidate_id,a.status AS candidate_status,
                     a.package_id,
                     p.candidate_mode,p.pack_id,p.version,
                     p.candidate_content_hash,p.candidate_manifest_hash,
                     p.candidate_package_hash,p.archive_hash,
                     p.source_facts_json,p.target_facts_json,
                     p.effect_topology_hash,p.content_state,
                     p.blob_cleanup_state,p.schema_version AS package_schema_version,
                     s.build_id,s.builder_receipt_ref,
                     s.builder_receipt_hash,s.provenance_state,
                     b.status AS build_status,b.candidate_ref,b.candidate_hash,
                     b.proposal_ref,b.proposal_hash,b.evidence_set_hash,
                     b.target_fence_hash,b.builder_launch_id,
                     b.builder_child_run_id,b.expected_child_start_hash,
                     b.draft_receipt_ref,b.draft_receipt_hash,
                     b.draft_receipt_expectation_json
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN candidate_package_sources s
                     ON s.profile_id=a.profile_id
                    AND s.profile_generation=a.profile_generation
                    AND s.package_id=a.package_id
                    AND s.candidate_id=a.candidate_id
                   JOIN candidate_builds b
                     ON b.profile_id=a.profile_id
                    AND b.profile_generation=a.profile_generation
                    AND b.build_id=s.build_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(candidate_id),
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_bundle_missing")
            if (
                str(row["content_state"]) != "live"
                or str(row["blob_cleanup_state"]) != "live"
                or str(row["provenance_state"]) != "live"
            ):
                raise CompanionStateError("candidate_bundle_content_unavailable")
            if (
                str(row["build_status"]) != "built"
                or str(row["candidate_ref"]) != str(row["candidate_id"])
                or str(row["candidate_hash"]) != str(row["candidate_package_hash"])
                or str(row["builder_receipt_ref"]) != str(row["draft_receipt_ref"])
                or str(row["builder_receipt_hash"]) != str(row["draft_receipt_hash"])
            ):
                raise CompanionConflictError("candidate_bundle_build_identity_mismatch")
            raw_expectation = json.loads(str(row["draft_receipt_expectation_json"] or ""))
            if not isinstance(raw_expectation, dict):
                raise CompanionConflictError("candidate_bundle_receipt_invalid")
            receipt = CandidateDraftReceiptV1.from_authoritative_row(raw_expectation)
            receipt_expected = {
                "receipt_id": row["draft_receipt_ref"],
                "receipt_hash": row["draft_receipt_hash"],
                "builder_launch_id": row["builder_launch_id"],
                "child_run_id": row["builder_child_run_id"],
                "child_start_hash": row["expected_child_start_hash"],
                "proposal_ref": row["proposal_ref"],
                "proposal_hash": row["proposal_hash"],
                "evidence_set_hash": row["evidence_set_hash"],
                "target_fence_hash": row["target_fence_hash"],
                "validated_draft_hash": row["candidate_content_hash"],
                "manifest_hash": row["candidate_manifest_hash"],
                "archive_hash": row["archive_hash"],
                "effect_topology_hash": row["effect_topology_hash"],
            }
            for name, expected in receipt_expected.items():
                if getattr(receipt, name) != expected:
                    raise CompanionConflictError(f"candidate_bundle_receipt_{name}_mismatch")

            blobs, files = self._load_candidate_package_files(db, owner, row)

            return self._verified_candidate_bundle(
                blobs, files=files, owner=owner, receipt=receipt, row=row
            )

    def _load_candidate_package_files(self, db, owner: OwnerRef, row):
        blob_rows = db.execute(
            """SELECT * FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=?
                     AND package_id=?
                   ORDER BY blob_kind,blob_id""",
            (
                owner.profile_id,
                owner.profile_generation,
                row["package_id"],
            ),
        ).fetchall()
        file_rows = db.execute(
            """SELECT * FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=?
                     AND package_id=?
                   ORDER BY relative_path""",
            (
                owner.profile_id,
                owner.profile_generation,
                row["package_id"],
            ),
        ).fetchall()
        if not blob_rows or not file_rows:
            raise CompanionStateError("candidate_bundle_children_missing")
        blobs: list[CandidatePackageBlob] = []
        blob_by_key: dict[tuple[str, str], CandidatePackageBlob] = {}
        for blob_row in blob_rows:
            payload = bytes(blob_row["payload"])
            blob = CandidatePackageBlob(
                blob_kind=str(blob_row["blob_kind"]),
                blob_id=str(blob_row["blob_id"]),
                content_hash=str(blob_row["content_hash"]),
                payload=payload,
            )
            key = (blob.blob_kind, blob.blob_id)
            if (
                key in blob_by_key
                or str(blob_row["cleanup_state"]) != "live"
                or int(blob_row["size_bytes"]) != len(payload)
                or blob.content_hash != _bytes_hash(payload)
            ):
                raise CompanionConflictError("candidate_bundle_blob_integrity_mismatch")
            blob_by_key[key] = blob
            blobs.append(blob)
        manifest_key = (
            "manifest",
            str(row["candidate_manifest_hash"]),
        )
        archive_key = ("archive", str(row["archive_hash"]))
        if (
            manifest_key not in blob_by_key
            or archive_key not in blob_by_key
            or len([key for key in blob_by_key if key[0] == "manifest"]) != 1
            or len([key for key in blob_by_key if key[0] == "archive"]) != 1
        ):
            raise CompanionConflictError("candidate_bundle_root_blob_mismatch")

        files: list[CandidatePackageFile] = []
        for file_row in file_rows:
            relative_path = str(file_row["relative_path"])
            self._validate_relative_path(relative_path)
            file = CandidatePackageFile(
                relative_path=relative_path,
                file_kind=str(file_row["file_kind"]),
                file_mode=int(file_row["file_mode"]),
                content_hash=str(file_row["content_hash"]),
                size_bytes=int(file_row["size_bytes"]),
                blob_id=str(file_row["blob_id"]),
            )
            blob = blob_by_key.get(("file", file.blob_id))
            if (
                str(file_row["blob_kind"]) != "file"
                or blob is None
                or blob.content_hash != file.content_hash
                or len(blob.payload) != file.size_bytes
            ):
                raise CompanionConflictError("candidate_bundle_file_blob_mismatch")
            files.append(file)
        expected_blob_keys = {
            manifest_key,
            archive_key,
            *(("file", item.blob_id) for item in files),
        }
        if set(blob_by_key) != expected_blob_keys:
            raise CompanionConflictError("candidate_bundle_blob_set_mismatch")

        manifest_bytes = blob_by_key[manifest_key].payload
        archive_bytes = blob_by_key[archive_key].payload
        if _bytes_hash(manifest_bytes) != str(row["candidate_manifest_hash"]) or _bytes_hash(
            archive_bytes
        ) != str(row["archive_hash"]):
            raise CompanionConflictError("candidate_bundle_root_hash_mismatch")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                archive_names = archive.namelist()
                archive_payloads = {name: archive.read(name) for name in archive_names}
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            zipfile.BadZipFile,
        ) as exc:
            raise CompanionConflictError("candidate_bundle_archive_invalid") from exc
        expected_names = [
            "deskpet-pack.json",
            *(item.relative_path for item in files),
        ]
        if (
            not isinstance(manifest, dict)
            or str(manifest.get("id") or "") != str(row["pack_id"])
            or str(manifest.get("version") or "") != str(row["version"])
            or archive_names != expected_names
            or archive_payloads["deskpet-pack.json"] != manifest_bytes
            or any(
                archive_payloads[item.relative_path] != blob_by_key[("file", item.blob_id)].payload
                for item in files
            )
        ):
            raise CompanionConflictError("candidate_bundle_archive_content_mismatch")
        declared_files = manifest.get("files")
        expected_declared = [
            {
                "path": item.relative_path,
                "sha256": item.content_hash,
            }
            for item in files
        ]
        if declared_files != expected_declared:
            raise CompanionConflictError("candidate_bundle_manifest_file_set_mismatch")
        return blobs, files

    def _verified_candidate_bundle(
        self,
        blobs: list[CandidatePackageBlob],
        *,
        files: list[CandidatePackageFile],
        owner: OwnerRef,
        receipt,
        row,
    ) -> Any:
        file_set_hash = canonical_hash(
            {
                "schema": "candidate-file-set-v1",
                "files": [
                    {
                        "path": item.relative_path,
                        "mode": item.file_mode,
                        "hash": item.content_hash,
                        "size": item.size_bytes,
                    }
                    for item in files
                ],
            }
        )
        if file_set_hash != receipt.file_set_hash:
            raise CompanionConflictError("candidate_bundle_file_set_hash_mismatch")
        package_hash = canonical_hash(
            {
                "schema": "capability-candidate-package-v1",
                "pack_id": row["pack_id"],
                "version": row["version"],
                "candidate_content_hash": row["candidate_content_hash"],
                "candidate_manifest_hash": row["candidate_manifest_hash"],
                "archive_hash": row["archive_hash"],
                "file_set_hash": file_set_hash,
                "effect_topology_hash": row["effect_topology_hash"],
            }
        )
        if package_hash != str(row["candidate_package_hash"]):
            raise CompanionConflictError("candidate_bundle_package_hash_mismatch")
        owner_key = f"companion:{owner.profile_id}:{owner.profile_generation}"
        package_id = canonical_hash(
            {
                "schema": "candidate-package-id-v1",
                "owner_key": owner_key,
                "pack_id": row["pack_id"],
                "candidate_package_hash": package_hash,
            }
        )
        if package_id != str(row["package_id"]):
            raise CompanionConflictError("candidate_bundle_package_id_mismatch")
        source_facts = json.loads(str(row["source_facts_json"]))
        target_facts = json.loads(str(row["target_facts_json"]))
        if not isinstance(source_facts, dict) or not isinstance(target_facts, dict):
            raise CompanionConflictError("candidate_bundle_facts_invalid")
        package = CandidatePackage(
            package_id=package_id,
            candidate_mode=CandidateMode(str(row["candidate_mode"])),
            pack_id=str(row["pack_id"]),
            version=str(row["version"]),
            candidate_content_hash=str(row["candidate_content_hash"]),
            candidate_manifest_hash=str(row["candidate_manifest_hash"]),
            candidate_package_hash=package_hash,
            archive_hash=str(row["archive_hash"]),
            effect_topology_hash=str(row["effect_topology_hash"]),
            source_facts=source_facts,
            target_facts=target_facts,
            files=tuple(files),
            blobs=tuple(blobs),
            schema_version=int(row["package_schema_version"]),
        )
        return ExactCandidateBundleV1(
            owner=owner,
            candidate_id=str(row["candidate_id"]),
            candidate_status=str(row["candidate_status"]),
            build_id=str(row["build_id"]),
            build_status=str(row["build_status"]),
            candidate_hash=str(row["candidate_hash"]),
            proposal_ref=str(row["proposal_ref"]),
            proposal_hash=str(row["proposal_hash"]),
            evidence_set_hash=str(row["evidence_set_hash"]),
            target_fence_hash=str(row["target_fence_hash"]),
            package=package,
            receipt=receipt,
        )

    def create_candidate(
        self,
        owner: OwnerRef,
        package: CandidatePackage,
        attempt: CandidateAttempt,
    ) -> Mapping[str, Any]:
        if package.candidate_mode != attempt.candidate_mode:
            raise ValueError("candidate_mode_mismatch")
        now = self._now()
        file_rows = sorted(package.files, key=lambda item: item.relative_path)
        blob_rows = sorted(package.blobs, key=lambda item: (item.blob_kind, item.blob_id))
        for item in file_rows:
            self._validate_relative_path(item.relative_path)
        for blob in blob_rows:
            if blob.content_hash != _bytes_hash(blob.payload):
                raise CompanionConflictError(f"candidate_blob_hash_mismatch:{blob.blob_id}")
        if {item.blob_id for item in file_rows} - {
            item.blob_id for item in blob_rows if item.blob_kind == "file"
        }:
            raise ValueError("candidate_file_blob_missing")
        with self._write() as db:
            self._require_owner(db, owner)
            evidence_ids = tuple(sorted(set(attempt.evidence_event_ids)))
            if not evidence_ids:
                raise CompanionStateError("candidate_requires_evidence")
            placeholders = ",".join("?" for _ in evidence_ids)
            live = db.execute(
                f"""SELECT event_id FROM growth_events
                    WHERE profile_id=? AND profile_generation=?
                      AND event_id IN ({placeholders}) AND content_state='live'""",
                (owner.profile_id, owner.profile_generation, *evidence_ids),
            ).fetchall()
            if {str(row["event_id"]) for row in live} != set(evidence_ids):
                raise CompanionStateError("candidate_evidence_missing_or_tombstoned")

            existing_attempt = db.execute(
                """SELECT * FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND (candidate_id=? OR candidate_attempt_key=?
                          OR (proposal_source_kind=? AND proposal_source_ref=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    attempt.candidate_id,
                    attempt.candidate_attempt_key,
                    attempt.proposal_source_kind,
                    attempt.proposal_source_ref,
                ),
            ).fetchone()
            if existing_attempt is not None:
                if (
                    existing_attempt["candidate_id"] != attempt.candidate_id
                    or existing_attempt["candidate_attempt_key"] != attempt.candidate_attempt_key
                    or existing_attempt["package_id"] != package.package_id
                    or existing_attempt["evidence_set_hash"] != attempt.evidence_set_hash
                    or existing_attempt["proposal_source_hash"] != attempt.source_hash
                ):
                    raise CompanionConflictError(
                        f"candidate_attempt_conflict:{attempt.candidate_id}"
                    )
                return dict(existing_attempt)

            package_expected = {
                "candidate_mode": package.candidate_mode.value,
                "pack_id": package.pack_id,
                "version": package.version,
                "candidate_content_hash": package.candidate_content_hash,
                "candidate_manifest_hash": package.candidate_manifest_hash,
                "candidate_package_hash": package.candidate_package_hash,
                "archive_hash": package.archive_hash,
                "source_facts_json": canonical_json(dict(package.source_facts)),
                "target_facts_json": canonical_json(dict(package.target_facts)),
                "effect_topology_hash": package.effect_topology_hash,
                "governance_domain": "companion_growth",
                "content_state": "live",
                "blob_cleanup_state": "live",
                "schema_version": package.schema_version,
            }
            package_row = db.execute(
                """SELECT * FROM candidate_packages
                   WHERE profile_id=? AND profile_generation=?
                     AND (package_id=? OR candidate_content_hash=? OR candidate_package_hash=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package.package_id,
                    package.candidate_content_hash,
                    package.candidate_package_hash,
                ),
            ).fetchone()
            if package_row is None:
                db.execute(
                    """INSERT INTO candidate_packages(
                         profile_id,profile_generation,package_id,candidate_mode,pack_id,version,
                         candidate_content_hash,candidate_manifest_hash,candidate_package_hash,
                         archive_hash,source_facts_json,target_facts_json,effect_topology_hash,
                         governance_domain,content_state,blob_cleanup_state,schema_version,
                         reason_code,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package.package_id,
                        *package_expected.values(),
                        "package_frozen",
                        now,
                        now,
                    ),
                )
            elif package_row["package_id"] != package.package_id or not self._same(
                package_row, package_expected
            ):
                raise CompanionConflictError(f"candidate_package_conflict:{package.package_id}")

            self._insert_or_verify_package_children(
                db, owner, package.package_id, file_rows, blob_rows, now
            )
            self._validate_or_hold_reservation(db, owner, package, attempt, now)
            db.execute(
                """INSERT INTO candidate_artifacts(
                     profile_id,profile_generation,candidate_id,candidate_attempt_key,package_id,
                     proposal_source_kind,proposal_source_ref,proposal_source_hash,reflection_job_id,
                     candidate_mode,target_id,source_owner_key,source_scope,source_scope_key,
                     source_version,source_manifest_hash,source_binding_generation,target_owner_key,
                     target_scope,target_scope_key,target_expected_absent,
                     target_expected_binding_generation,evidence_set_hash,reservation_version,
                     attempt_generation,status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    attempt.candidate_id,
                    attempt.candidate_attempt_key,
                    package.package_id,
                    attempt.proposal_source_kind,
                    attempt.proposal_source_ref,
                    attempt.source_hash,
                    attempt.reflection_job_id,
                    attempt.candidate_mode.value,
                    attempt.target_id,
                    attempt.source_owner_key,
                    attempt.source_scope,
                    attempt.source_scope_key,
                    attempt.source_version,
                    attempt.source_manifest_hash,
                    attempt.source_binding_generation,
                    attempt.target_owner_key,
                    attempt.target_scope,
                    attempt.target_scope_key,
                    int(attempt.target_expected_absent),
                    attempt.target_expected_binding_generation,
                    attempt.evidence_set_hash,
                    attempt.reservation_version,
                    attempt.attempt_generation,
                    "proposed",
                    attempt.reason_code,
                    attempt.schema_version,
                    now,
                    now,
                ),
            )
            for event_id in evidence_ids:
                db.execute(
                    """INSERT INTO candidate_evidence(
                         profile_id,profile_generation,candidate_id,event_id,reason_code,
                         schema_version,created_at
                       ) VALUES (?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        attempt.candidate_id,
                        event_id,
                        "candidate_evidence",
                        now,
                    ),
                )
                db.execute(
                    """INSERT INTO lineage_edges(
                         profile_id,profile_generation,from_kind,from_id,to_kind,to_id,relation,
                         reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        "growth_event",
                        event_id,
                        "candidate",
                        attempt.candidate_id,
                        "supports",
                        "candidate_created",
                        now,
                    ),
                )
            db.execute(
                """INSERT INTO candidate_package_sources(
                     profile_id,profile_generation,package_id,candidate_id,build_id,
                     evidence_set_json,evidence_set_hash,builder_receipt_ref,builder_receipt_hash,
                     provenance_state,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package.package_id,
                    attempt.candidate_id,
                    attempt.build_id,
                    canonical_json(evidence_ids),
                    attempt.evidence_set_hash,
                    attempt.builder_receipt_ref,
                    attempt.builder_receipt_hash,
                    "live",
                    "candidate_source_frozen",
                    now,
                    now,
                ),
            )
            if attempt.candidate_mode.value == "genesis":
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND reservation_version=? AND status='held'""",
                    (
                        attempt.candidate_id,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        attempt.target_id,
                        attempt.reservation_version,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["candidate", package.candidate_package_hash, attempt.candidate_attempt_key]
                ),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM candidate_artifacts
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, attempt.candidate_id),
                ).fetchone()
            )

    def _insert_or_verify_package_children(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        package_id: str,
        files: Sequence[Any],
        blobs: Sequence[Any],
        now: str,
    ) -> None:
        expected_blob_keys: set[tuple[str, str]] = set()
        for blob in blobs:
            expected_blob_keys.add((blob.blob_kind, blob.blob_id))
            row = db.execute(
                """SELECT * FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=? AND package_id=?
                     AND blob_kind=? AND blob_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package_id,
                    blob.blob_kind,
                    blob.blob_id,
                ),
            ).fetchone()
            expected = {
                "content_hash": blob.content_hash,
                "size_bytes": len(blob.payload),
                "payload": blob.payload,
                "cleanup_state": "live",
                "schema_version": 1,
            }
            if row is None:
                db.execute(
                    """INSERT INTO candidate_package_blobs(
                         profile_id,profile_generation,package_id,blob_kind,blob_id,
                         content_hash,size_bytes,payload,cleanup_state,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package_id,
                        blob.blob_kind,
                        blob.blob_id,
                        blob.content_hash,
                        len(blob.payload),
                        blob.payload,
                        "live",
                        "package_frozen",
                        now,
                        now,
                    ),
                )
            elif not self._same(row, expected):
                raise CompanionConflictError(f"candidate_blob_conflict:{blob.blob_id}")
        actual_blob_keys = {
            (str(row["blob_kind"]), str(row["blob_id"]))
            for row in db.execute(
                """SELECT blob_kind,blob_id FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=? AND package_id=?""",
                (owner.profile_id, owner.profile_generation, package_id),
            ).fetchall()
        }
        if actual_blob_keys != expected_blob_keys:
            raise CompanionConflictError(f"candidate_blob_set_conflict:{package_id}")

        expected_paths: set[str] = set()
        for item in files:
            expected_paths.add(item.relative_path)
            row = db.execute(
                """SELECT * FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=? AND package_id=? AND relative_path=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package_id,
                    item.relative_path,
                ),
            ).fetchone()
            expected = {
                "file_kind": item.file_kind,
                "file_mode": item.file_mode,
                "content_hash": item.content_hash,
                "size_bytes": item.size_bytes,
                "blob_kind": "file",
                "blob_id": item.blob_id,
                "schema_version": 1,
            }
            if row is None:
                db.execute(
                    """INSERT INTO candidate_package_files(
                         profile_id,profile_generation,package_id,relative_path,file_kind,file_mode,
                         content_hash,size_bytes,blob_kind,blob_id,reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package_id,
                        item.relative_path,
                        item.file_kind,
                        item.file_mode,
                        item.content_hash,
                        item.size_bytes,
                        "file",
                        item.blob_id,
                        "package_frozen",
                        now,
                    ),
                )
            elif not self._same(row, expected):
                raise CompanionConflictError(f"candidate_file_conflict:{item.relative_path}")
        actual_paths = {
            str(row["relative_path"])
            for row in db.execute(
                """SELECT relative_path FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=? AND package_id=?""",
                (owner.profile_id, owner.profile_generation, package_id),
            ).fetchall()
        }
        if actual_paths != expected_paths:
            raise CompanionConflictError(f"candidate_file_set_conflict:{package_id}")

    @staticmethod
    def _validate_or_hold_reservation(
        db: sqlite3.Connection,
        owner: OwnerRef,
        package: CandidatePackage,
        attempt: CandidateAttempt,
        now: str,
    ) -> None:
        target = db.execute(
            """SELECT * FROM growth_targets
               WHERE profile_id=? AND profile_generation=? AND target_id=?""",
            (owner.profile_id, owner.profile_generation, attempt.target_id),
        ).fetchone()
        if target is None or target["pack_id"] != package.pack_id:
            raise CompanionStateError("candidate_target_missing_or_pack_mismatch")
        if attempt.candidate_mode.value != "genesis":
            return
        if attempt.reservation_version is None:
            raise CompanionStateError("genesis_reservation_required")
        reservation = db.execute(
            """SELECT * FROM growth_target_reservations
               WHERE profile_id=? AND profile_generation=? AND kind=? AND stable_name=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                target["kind"],
                target["stable_name"],
            ),
        ).fetchone()
        if reservation is None:
            if attempt.reservation_version != 1:
                raise CompanionStateError("genesis_reservation_version_mismatch")
            db.execute(
                """INSERT INTO growth_target_reservations(
                     profile_id,profile_generation,kind,stable_name,target_id,pack_id,candidate_id,
                     reservation_version,status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    target["kind"],
                    target["stable_name"],
                    attempt.target_id,
                    package.pack_id,
                    None,
                    1,
                    "held",
                    "genesis_candidate",
                    now,
                    now,
                ),
            )
            return
        if (
            reservation["status"] == "released"
            and int(reservation["reservation_version"]) == attempt.reservation_version
            and reservation["candidate_id"] is None
        ):
            db.execute(
                """UPDATE growth_target_reservations
                   SET status='held',reason_code='genesis_candidate',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND kind=? AND stable_name=?
                     AND status='released' AND reservation_version=?""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    target["kind"],
                    target["stable_name"],
                    attempt.reservation_version,
                ),
            )
            return
        if (
            reservation["status"] != "held"
            or int(reservation["reservation_version"]) != attempt.reservation_version
            or (
                reservation["candidate_id"] is not None
                and reservation["candidate_id"] != attempt.candidate_id
            )
        ):
            raise CompanionStateError("genesis_target_reserved")

    def transition_candidate(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
        expected_status: str,
        next_status: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        allowed: dict[str, set[str]] = {
            "proposed": {
                "preflight_failed",
                "evaluating",
                "awaiting_eval_authorization",
                "invalidated",
                "expired",
                "stale",
            },
            "awaiting_eval_authorization": {"evaluating", "invalidated", "expired", "stale"},
            "evaluating": {
                "eligible",
                "awaiting_activation_confirmation",
                "invalidated",
                "expired",
                "stale",
            },
            "eligible": {
                "activation_pending",
                "awaiting_activation_confirmation",
                "invalidated",
                "expired",
                "stale",
            },
            "awaiting_activation_confirmation": {
                "activation_pending",
                "rejected",
                "stale",
                "invalidated",
                "expired",
            },
            "activation_pending": {"activating", "invalidated", "expired", "stale"},
            "activating": {
                "active",
                "activation_failed",
                "activation_pending",
                "invalidated",
                "stale",
            },
            "active": {"rollback_pending", "quarantined"},
            "rollback_pending": {"rolled_back", "disabled"},
        }
        if next_status not in allowed.get(expected_status, set()):
            raise CompanionStateError(
                f"candidate_transition_invalid:{expected_status}:{next_status}"
            )
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT status,candidate_mode,target_id,reservation_version
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                (owner.profile_id, owner.profile_generation, candidate_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_missing")
            if row["status"] == next_status:
                return dict(row)
            if row["status"] != expected_status:
                raise CompanionConflictError("candidate_state_conflict")
            db.execute(
                """UPDATE candidate_artifacts SET status=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=? AND status=?""",
                (
                    next_status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                    expected_status,
                ),
            )
            if row["candidate_mode"] == "genesis" and next_status in {
                "rejected",
                "expired",
                "invalidated",
            }:
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=NULL,status='released',
                           reservation_version=reservation_version+1,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND candidate_id=? AND status='held'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["target_id"],
                        candidate_id,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "candidate_transition",
                        candidate_id,
                        expected_status,
                        next_status,
                        reason_code,
                    ]
                ),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM candidate_artifacts
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, candidate_id),
                ).fetchone()
            )

    def invalidate_candidate_for_evaluation_replan(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Mapping[str, Any]:
        """Invalidate only an evaluating candidate, without reviving other causes.

        An exact replay of this transition is accepted.  A candidate already
        invalidated by forget, stale fencing, or another authority is not
        eligible to create new evidence or work.
        """

        now = self._now()
        reason_code = "evaluation_failed_replan"
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT status,reason_code,candidate_mode,target_id
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_missing")
            if str(row["status"]) == "invalidated" and str(row["reason_code"]) == reason_code:
                return {
                    **dict(row),
                    "applied": False,
                    "exact_replay": True,
                }
            if str(row["status"]) != "evaluating":
                return {
                    **dict(row),
                    "applied": False,
                    "exact_replay": False,
                }
            cursor = db.execute(
                """UPDATE candidate_artifacts
                   SET status='invalidated',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status='evaluating'""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionConflictError("candidate_evaluation_replan_transition_lost")
            if str(row["candidate_mode"]) == "genesis":
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=NULL,status='released',
                           reservation_version=reservation_version+1,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND target_id=? AND candidate_id=?
                         AND status='held'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["target_id"],
                        candidate_id,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "candidate_evaluation_replan",
                        candidate_id,
                        reason_code,
                    ]
                ),
                now=now,
            )
            current = db.execute(
                """SELECT status,reason_code,candidate_mode,target_id
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            assert current is not None
            return {
                **dict(current),
                "applied": True,
                "exact_replay": False,
            }
