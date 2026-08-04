"""Immutable execution-side persistence for candidate draft receipts.

The repository writes through a caller-owned SQLite transaction.  This lets
the execution UoW commit its child terminal/finalize marker and the immutable
receipt together; the repository never opens a second writer and never
commits on the caller's behalf.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
    GrowthBuildAdmissionError,
)

CANDIDATE_DRAFT_RECEIPT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_candidate_draft_receipts (
    receipt_id TEXT PRIMARY KEY,
    builder_launch_id TEXT NOT NULL UNIQUE,
    child_run_id TEXT NOT NULL,
    child_start_hash TEXT NOT NULL,
    proposal_ref TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    evidence_set_hash TEXT NOT NULL,
    target_fence_hash TEXT NOT NULL,
    validated_draft_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    file_set_hash TEXT NOT NULL,
    effect_topology_hash TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_execution_candidate_draft_receipts_no_update
BEFORE UPDATE ON execution_candidate_draft_receipts
BEGIN
    SELECT RAISE(ABORT, 'execution_candidate_draft_receipt_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_execution_candidate_draft_receipts_no_delete
BEFORE DELETE ON execution_candidate_draft_receipts
BEGIN
    SELECT RAISE(ABORT, 'execution_candidate_draft_receipt_immutable');
END;

CREATE TABLE IF NOT EXISTS execution_candidate_draft_materials (
    receipt_id TEXT PRIMARY KEY,
    archive_blob BLOB NOT NULL,
    validated_draft_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    archive_hash TEXT NOT NULL,
    file_set_hash TEXT NOT NULL,
    effect_topology_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(receipt_id)
      REFERENCES execution_candidate_draft_receipts(receipt_id)
      ON DELETE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS trg_execution_candidate_draft_materials_no_update
BEFORE UPDATE ON execution_candidate_draft_materials
BEGIN
    SELECT RAISE(ABORT, 'execution_candidate_draft_material_immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_execution_candidate_draft_materials_no_delete
BEFORE DELETE ON execution_candidate_draft_materials
BEGIN
    SELECT RAISE(ABORT, 'execution_candidate_draft_material_immutable');
END;
"""


@dataclass(frozen=True, slots=True)
class CandidateFinalizeProofV1:
    """Host query key for the terminal/finalize marker in the same UoW."""

    finalize_marker_ref: str
    finalize_marker_hash: str
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str

    def __post_init__(self) -> None:
        for name in (
            "finalize_marker_ref",
            "builder_launch_id",
            "child_run_id",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name}_required")
        for name in ("finalize_marker_hash", "child_start_hash"):
            value = str(getattr(self, name) or "")
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name}_invalid")

    def verify_receipt(self, receipt: CandidateDraftReceiptV1) -> None:
        for name in ("builder_launch_id", "child_run_id", "child_start_hash"):
            if getattr(self, name) != getattr(receipt, name):
                raise GrowthBuildAdmissionError(
                    f"candidate_finalize_proof_{name}_mismatch"
                )


class CandidateFinalizeProofQueryPort(Protocol):
    def verify_exact_in_uow(
        self,
        db: sqlite3.Connection,
        proof: CandidateFinalizeProofV1,
    ) -> bool: ...


def _row_mapping(cursor: sqlite3.Cursor, row: object) -> Mapping[str, object] | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if not isinstance(row, tuple):
        raise GrowthBuildAdmissionError("candidate_draft_receipt_row_invalid")
    names = tuple(column[0] for column in (cursor.description or ()))
    return dict(zip(names, row, strict=True))


class CandidateDraftReceiptRepository:
    """Insert/replay one receipt without taking ownership of the transaction."""

    _FIELDS = (
        "receipt_id",
        "builder_launch_id",
        "child_run_id",
        "child_start_hash",
        "proposal_ref",
        "proposal_hash",
        "evidence_set_hash",
        "target_fence_hash",
        "validated_draft_hash",
        "manifest_hash",
        "archive_hash",
        "file_set_hash",
        "effect_topology_hash",
        "receipt_hash",
    )

    def __init__(self, finalize_query: CandidateFinalizeProofQueryPort) -> None:
        self._finalize_query = finalize_query

    def insert_in_uow(
        self,
        db: sqlite3.Connection,
        receipt: CandidateDraftReceiptV1,
        *,
        finalize_proof: CandidateFinalizeProofV1,
        created_at: str,
    ) -> CandidateDraftReceiptV1:
        if not isinstance(receipt, CandidateDraftReceiptV1):
            raise GrowthBuildAdmissionError("candidate_draft_receipt_required")
        finalize_proof.verify_receipt(receipt)
        if not str(created_at or "").strip():
            raise ValueError("candidate_draft_receipt_created_at_required")
        if not self._finalize_query.verify_exact_in_uow(db, finalize_proof):
            raise GrowthBuildAdmissionError(
                "candidate_finalize_marker_missing_or_mismatch"
            )

        values = tuple(getattr(receipt, name) for name in self._FIELDS)
        try:
            db.execute(
                """INSERT INTO execution_candidate_draft_receipts(
                     receipt_id,builder_launch_id,child_run_id,child_start_hash,
                     proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
                     validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
                     effect_topology_hash,receipt_hash,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*values, created_at.strip()),
            )
            return receipt
        except sqlite3.IntegrityError as exc:
            existing = self._read_replay_candidate(
                db,
                receipt_id=receipt.receipt_id,
                builder_launch_id=receipt.builder_launch_id,
            )
            if existing is None:
                raise GrowthBuildAdmissionError(
                    "candidate_draft_receipt_insert_conflict"
                ) from exc
            expected = CandidateDraftReceiptExpectationV1(
                **{name: getattr(receipt, name) for name in self._FIELDS}
            )
            try:
                expected.verify(existing)
            except GrowthBuildAdmissionError as mismatch:
                raise GrowthBuildAdmissionError(
                    "candidate_draft_receipt_replay_conflict"
                ) from mismatch
            return existing

    def read_exact_in_uow(
        self,
        db: sqlite3.Connection,
        expectation: CandidateDraftReceiptExpectationV1,
    ) -> CandidateDraftReceiptV1:
        cursor = db.execute(
            """SELECT receipt_id,builder_launch_id,child_run_id,child_start_hash,
                      proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
                      validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
                      effect_topology_hash,receipt_hash
               FROM execution_candidate_draft_receipts
               WHERE receipt_id=? AND receipt_hash=?""",
            (expectation.receipt_id, expectation.receipt_hash),
        )
        row = _row_mapping(cursor, cursor.fetchone())
        if row is None:
            raise GrowthBuildAdmissionError("candidate_draft_receipt_not_found")
        receipt = CandidateDraftReceiptV1.from_authoritative_row(row)
        expectation.verify(receipt)
        return receipt

    def _read_replay_candidate(
        self,
        db: sqlite3.Connection,
        *,
        receipt_id: str,
        builder_launch_id: str,
    ) -> CandidateDraftReceiptV1 | None:
        cursor = db.execute(
            """SELECT receipt_id,builder_launch_id,child_run_id,child_start_hash,
                      proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
                      validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
                      effect_topology_hash,receipt_hash
               FROM execution_candidate_draft_receipts
               WHERE receipt_id=? OR builder_launch_id=?""",
            (receipt_id, builder_launch_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            return None
        row = _row_mapping(cursor, rows[0])
        assert row is not None
        return CandidateDraftReceiptV1.from_authoritative_row(row)


class SqliteCandidateDraftReceiptQuery:
    """Read-only durable query adapter used by the Companion handoff."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).resolve()
        if str(database_path) == ":memory:":
            raise ValueError("candidate_receipt_query_requires_durable_db")
        self._uri = path.as_uri() + "?mode=ro"

    def read_exact(
        self, expectation: CandidateDraftReceiptExpectationV1
    ) -> CandidateDraftReceiptV1:
        db = sqlite3.connect(self._uri, uri=True, isolation_level=None, timeout=5.0)
        try:
            cursor = db.execute(
                """SELECT receipt_id,builder_launch_id,child_run_id,child_start_hash,
                          proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
                          validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
                          effect_topology_hash,receipt_hash
                   FROM execution_candidate_draft_receipts
                   WHERE receipt_id=? AND receipt_hash=?""",
                (expectation.receipt_id, expectation.receipt_hash),
            )
            row = _row_mapping(cursor, cursor.fetchone())
        finally:
            db.close()
        if row is None:
            raise GrowthBuildAdmissionError("candidate_draft_receipt_not_found")
        receipt = CandidateDraftReceiptV1.from_authoritative_row(row)
        expectation.verify(receipt)
        return receipt


class SqliteCandidateDraftMaterialQuery:
    """Read the exact immutable archive paired with a durable receipt."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).resolve()
        if str(database_path) == ":memory:":
            raise ValueError("candidate_material_query_requires_durable_db")
        self._uri = path.as_uri() + "?mode=ro"

    def read_exact(self, receipt: CandidateDraftReceiptV1) -> Any:
        from .candidate_composition import CandidateDraftMaterialV1

        if not isinstance(receipt, CandidateDraftReceiptV1):
            raise GrowthBuildAdmissionError("candidate_draft_receipt_required")
        db = sqlite3.connect(self._uri, uri=True, isolation_level=None, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                """SELECT m.archive_blob,m.validated_draft_hash,m.manifest_hash,
                          m.archive_hash,m.file_set_hash,m.effect_topology_hash
                   FROM execution_candidate_draft_materials AS m
                   JOIN execution_candidate_draft_receipts AS r
                     ON r.receipt_id=m.receipt_id
                   WHERE r.receipt_id=? AND r.receipt_hash=?""",
                (receipt.receipt_id, receipt.receipt_hash),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            raise GrowthBuildAdmissionError("candidate_draft_material_not_found")
        expected = {
            "validated_draft_hash": receipt.validated_draft_hash,
            "manifest_hash": receipt.manifest_hash,
            "archive_hash": receipt.archive_hash,
            "file_set_hash": receipt.file_set_hash,
            "effect_topology_hash": receipt.effect_topology_hash,
        }
        if any(str(row[name]) != value for name, value in expected.items()):
            raise GrowthBuildAdmissionError(
                "candidate_draft_material_receipt_mismatch"
            )
        archive_bytes = bytes(row["archive_blob"])
        if hashlib.sha256(archive_bytes).hexdigest() != receipt.archive_hash:
            raise GrowthBuildAdmissionError(
                "candidate_draft_material_archive_mismatch"
            )
        return CandidateDraftMaterialV1(
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.receipt_hash,
            archive_bytes=archive_bytes,
            validated_draft_hash=receipt.validated_draft_hash,
            manifest_hash=receipt.manifest_hash,
            file_set_hash=receipt.file_set_hash,
            effect_topology_hash=receipt.effect_topology_hash,
        )


@dataclass(frozen=True, slots=True)
class CandidateDraftReceiptTerminalExtension:
    """Atomically bind a host-issued receipt to one child terminal marker."""

    receipt: CandidateDraftReceiptV1
    created_at: str
    completion_lineage_id: str | None = None
    archive_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, CandidateDraftReceiptV1):
            raise TypeError("candidate_draft_receipt_required")
        if not str(self.created_at or "").strip():
            raise ValueError("candidate_draft_receipt_created_at_required")
        if self.completion_lineage_id is not None:
            value = str(self.completion_lineage_id)
            if len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError("candidate_completion_lineage_id_invalid")
        if self.archive_bytes is not None and (
            not isinstance(self.archive_bytes, bytes)
            or hashlib.sha256(self.archive_bytes).hexdigest()
            != self.receipt.archive_hash
        ):
            raise ValueError("candidate_draft_material_archive_mismatch")

    @staticmethod
    def _receipt_values(
        receipt: CandidateDraftReceiptV1,
    ) -> dict[str, str]:
        return {
            name: str(getattr(receipt, name))
            for name in CandidateDraftReceiptRepository._FIELDS
        }

    def _verify_terminal_marker(self, *, record: object, terminal_event: object) -> None:
        if str(getattr(record, "run_id", "")) != self.receipt.child_run_id:
            raise GrowthBuildAdmissionError(
                "candidate_draft_receipt_child_run_mismatch"
            )
        event_key = str(getattr(terminal_event, "event_key", ""))
        if (
            event_key
            not in {"run:final", f"terminal:{self.receipt.child_run_id}"}
            or str(getattr(terminal_event, "kind", "")) != "run.final"
        ):
            raise GrowthBuildAdmissionError(
                "candidate_draft_receipt_terminal_marker_invalid"
            )
        payload = getattr(terminal_event, "payload", None)
        if not isinstance(payload, Mapping):
            raise GrowthBuildAdmissionError(
                "candidate_draft_receipt_terminal_payload_invalid"
            )
        expected = {
            "builder_launch_id": self.receipt.builder_launch_id,
            "child_start_hash": self.receipt.child_start_hash,
            "candidate_draft_receipt_hash": self.receipt.receipt_hash,
        }
        if all(str(payload.get(name, "")) == value for name, value in expected.items()):
            return
        if self.completion_lineage_id is not None:
            completion: object = payload.get("text", payload)
            if isinstance(completion, str):
                try:
                    completion = json.loads(completion)
                except json.JSONDecodeError as exc:
                    raise GrowthBuildAdmissionError(
                        "candidate_draft_receipt_terminal_payload_mismatch"
                    ) from exc
            if (
                isinstance(completion, Mapping)
                and str(completion.get("lineage_id") or "")
                == self.completion_lineage_id
            ):
                return
        raise GrowthBuildAdmissionError(
            "candidate_draft_receipt_terminal_payload_mismatch"
        )

    async def apply_terminal_commit(
        self,
        transaction: object,
        *,
        record: object,
        terminal_event: object,
    ) -> Mapping[str, str]:
        self._verify_terminal_marker(record=record, terminal_event=terminal_event)
        insert = getattr(transaction, "insert_candidate_draft_receipt", None)
        if not callable(insert):
            raise TypeError("candidate receipt requires execution transaction support")
        await insert(
            self._receipt_values(self.receipt),
            created_at=str(self.created_at).strip(),
        )
        if self.archive_bytes is not None:
            insert_material = getattr(
                transaction, "insert_candidate_draft_material", None
            )
            if not callable(insert_material):
                raise TypeError(
                    "candidate material requires execution transaction support"
                )
            await insert_material(
                receipt_id=self.receipt.receipt_id,
                archive_bytes=self.archive_bytes,
                validated_draft_hash=self.receipt.validated_draft_hash,
                manifest_hash=self.receipt.manifest_hash,
                archive_hash=self.receipt.archive_hash,
                file_set_hash=self.receipt.file_set_hash,
                effect_topology_hash=self.receipt.effect_topology_hash,
                created_at=str(self.created_at).strip(),
            )
        return {
            "kind": "deskpet.candidate-draft.v1",
            "ref": self.receipt.receipt_id,
            "content_hash": self.receipt.receipt_hash,
        }

    async def verify_terminal_replay(
        self,
        transaction: object,
        *,
        record: object,
        terminal_event: object,
    ) -> None:
        self._verify_terminal_marker(record=record, terminal_event=terminal_event)
        verify = getattr(transaction, "verify_candidate_draft_receipt", None)
        if not callable(verify):
            raise TypeError("candidate receipt requires execution transaction support")
        await verify(self._receipt_values(self.receipt))
        if self.archive_bytes is not None:
            verify_material = getattr(
                transaction, "verify_candidate_draft_material", None
            )
            if not callable(verify_material):
                raise TypeError(
                    "candidate material requires execution transaction support"
                )
            await verify_material(
                receipt_id=self.receipt.receipt_id,
                validated_draft_hash=self.receipt.validated_draft_hash,
                manifest_hash=self.receipt.manifest_hash,
                archive_hash=self.receipt.archive_hash,
                file_set_hash=self.receipt.file_set_hash,
                effect_topology_hash=self.receipt.effect_topology_hash,
            )


class CandidateDraftReceiptBuildOutput:
    """Issue and atomically persist the host-owned builder draft receipt."""

    def __init__(
        self,
        *,
        created_at: Callable[[], str],
    ) -> None:
        self._created_at = created_at

    async def handoff_candidate(
        self,
        evidence: Any,
        launch: Any,
        context: Any,
        *,
        transaction: Any,
        record: Any,
        terminal_event: Any,
        replay: bool,
    ) -> Mapping[str, str]:
        del context
        admission = getattr(launch, "candidate_admission", None)
        permit = getattr(admission, "permit", None)
        if admission is None or permit is None:
            raise GrowthBuildAdmissionError(
                "candidate_build_admission_required"
            )
        if str(getattr(record, "run_id", "")) != admission.child_run_id:
            raise GrowthBuildAdmissionError(
                "candidate_draft_receipt_child_run_mismatch"
            )
        receipt = CandidateDraftReceiptV1.issue_from_host(
            builder_launch_id=admission.builder_launch_id,
            child_run_id=admission.child_run_id,
            child_start_hash=admission.child_start_hash,
            proposal_ref=permit.proposal_ref,
            proposal_hash=permit.proposal_hash,
            evidence_set_hash=permit.evidence_set_hash,
            target_fence_hash=permit.target_fence_hash,
            validated_draft_hash=str(evidence.validated_draft_hash),
            manifest_hash=str(evidence.manifest_hash),
            archive_hash=str(evidence.archive_hash),
            file_set_hash=str(evidence.file_set_hash),
            effect_topology_hash=str(evidence.effect_topology_hash),
        )
        extension = CandidateDraftReceiptTerminalExtension(
            receipt=receipt,
            created_at=str(self._created_at()),
            completion_lineage_id=str(evidence.lineage_id),
            archive_bytes=bytes(evidence.archive_bytes),
        )
        if replay:
            await extension.verify_terminal_replay(
                transaction,
                record=record,
                terminal_event=terminal_event,
            )
        else:
            await extension.apply_terminal_commit(
                transaction,
                record=record,
                terminal_event=terminal_event,
            )
        return {
            "kind": "deskpet.candidate-draft.v1",
            "ref": receipt.receipt_id,
            "content_hash": receipt.receipt_hash,
        }


__all__ = [
    "CANDIDATE_DRAFT_RECEIPT_SCHEMA_SQL",
    "CandidateDraftReceiptRepository",
    "CandidateDraftReceiptBuildOutput",
    "CandidateDraftReceiptTerminalExtension",
    "CandidateFinalizeProofQueryPort",
    "CandidateFinalizeProofV1",
    "SqliteCandidateDraftMaterialQuery",
    "SqliteCandidateDraftReceiptQuery",
]
