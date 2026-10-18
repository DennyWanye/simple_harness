"""Connection, transactions and shared row converters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from deskpet.execution.attempt_contracts import (
    AttemptFailureSet,
    AttemptRecord,
    PlanVersionRecord,
    ProfileLaunchTicket,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderTurnFence,
    TaskExternalWait,
    TaskFailureReport,
    TaskGoalRecord,
)
from deskpet.execution.contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchClaim,
    AdmissionLaunchUnknownFence,
    AdmissionPhase,
    CreateRunResult,
    DecisionAuthorization,
    DecisionConflict,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    IdempotencyConflict,
    ProviderActionAdmissionState,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunRecord,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    canonical_json,
    fingerprint_json,
    thaw_json,
)
from deskpet.execution.delivery_contracts import (
    ChildCommandIntent,
    ChildCommandRecord,
    ChildSignalRecord,
    DeliveryRecord,
    DeliverySpec,
    FinalizeRunResult,
    RunEvent,
    RunEventCandidate,
)
from deskpet.execution.ports import RunView
from deskpet.types.task_grants import PreparedAuthorizationCommit
from deskpet.types.task_work_context import (
    ConversationBoundary,
    QueuedUserContinuation,
    TaskRunProjection,
    TaskWorkContext,
)

from ..schema import initialize_workflow_db
from ..write_lane import ExecutionWriteLane, get_execution_write_lane
from .support import ContinuationRecord

if TYPE_CHECKING:
    from ..execution_uow import SqliteExecutionUnitOfWork


_FaultInjector = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ExecutionTx:
    """Typed execution operations bound to a caller-owned SQLite transaction."""

    _unit_of_work: SqliteExecutionUnitOfWork
    _db: aiosqlite.Connection

    async def freeze_tool_presentation_specs(
        self,
        *,
        root_run_id: str,
        policies: Sequence[Mapping[str, Any]],
        created_at: float,
    ) -> None:
        """Insert-or-verify the immutable Root presentation snapshot."""

        for policy in policies:
            tool_name = str(policy.get("tool_name") or "").strip()
            policy_hash = str(policy.get("policy_hash") or "").strip()
            if not tool_name or len(policy_hash) != 64:
                raise ValueError("invalid tool presentation policy descriptor")
            payload = dict(policy)
            payload.pop("policy_hash", None)
            policy_json = canonical_json(payload)
            actual_hash = hashlib.sha256(policy_json.encode("utf-8")).hexdigest()
            if actual_hash != policy_hash:
                raise ValueError("tool presentation policy hash mismatch")
            existing = await (
                await self._db.execute(
                    "SELECT policy_json,policy_hash FROM "
                    "execution_run_tool_presentation_specs "
                    "WHERE root_run_id=? AND tool_name=?",
                    (root_run_id, tool_name),
                )
            ).fetchone()
            if existing is not None:
                if (str(existing["policy_json"]), str(existing["policy_hash"])) != (
                    policy_json,
                    policy_hash,
                ):
                    raise IdempotencyConflict(
                        "tool_presentation_snapshot_conflict",
                        "Root replay supplied different tool presentation metadata",
                    )
                continue
            await self._db.execute(
                "INSERT INTO execution_run_tool_presentation_specs("
                "root_run_id,tool_name,schema_version,policy_json,policy_hash,created_at) "
                "VALUES(?,?,1,?,?,?)",
                (root_run_id, tool_name, policy_json, policy_hash, created_at),
            )

    async def insert_candidate_draft_receipt(
        self,
        values: Mapping[str, str],
        *,
        created_at: str,
    ) -> None:
        """Insert or verify one immutable candidate receipt in this UoW.

        The execution ledger owns this DML so a child terminal event and its
        host-issued draft receipt can only become visible in the same commit.
        """

        fields = (
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
        if set(values) != set(fields):
            raise TypeError("candidate draft receipt fields are incomplete")
        normalized = {name: str(values[name]).strip() for name in fields}
        if any(not value for value in normalized.values()) or not str(created_at).strip():
            raise ValueError("candidate draft receipt values must be non-empty")
        existing = await (
            await self._db.execute(
                """SELECT receipt_id,builder_launch_id,child_run_id,
                child_start_hash,proposal_ref,proposal_hash,evidence_set_hash,
                target_fence_hash,validated_draft_hash,manifest_hash,
                archive_hash,file_set_hash,effect_topology_hash,receipt_hash
                FROM execution_candidate_draft_receipts
                WHERE receipt_id=? OR builder_launch_id=?""",
                (
                    normalized["receipt_id"],
                    normalized["builder_launch_id"],
                ),
            )
        ).fetchall()
        if existing:
            if len(existing) != 1 or any(
                str(existing[0][name]) != normalized[name] for name in fields
            ):
                raise IdempotencyConflict(
                    "candidate_draft_receipt_conflict",
                    "candidate draft receipt replay differs from durable row",
                )
            return
        await self._db.execute(
            """INSERT INTO execution_candidate_draft_receipts(
            receipt_id,builder_launch_id,child_run_id,child_start_hash,
            proposal_ref,proposal_hash,evidence_set_hash,target_fence_hash,
            validated_draft_hash,manifest_hash,archive_hash,file_set_hash,
            effect_topology_hash,receipt_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(normalized[name] for name in fields) + (str(created_at).strip(),),
        )

    async def verify_candidate_draft_receipt(
        self,
        values: Mapping[str, str],
    ) -> None:
        """Fail closed unless this transaction sees the exact receipt row."""

        fields = tuple(values)
        if not fields:
            raise ValueError("candidate draft receipt values are required")
        row = await (
            await self._db.execute(
                """SELECT receipt_id,builder_launch_id,child_run_id,
                child_start_hash,proposal_ref,proposal_hash,evidence_set_hash,
                target_fence_hash,validated_draft_hash,manifest_hash,
                archive_hash,file_set_hash,effect_topology_hash,receipt_hash
                FROM execution_candidate_draft_receipts
                WHERE receipt_id=? AND receipt_hash=?""",
                (values.get("receipt_id"), values.get("receipt_hash")),
            )
        ).fetchone()
        if row is None or any(
            name not in row.keys() or str(row[name]) != str(value) for name, value in values.items()
        ):
            raise IdempotencyConflict(
                "candidate_draft_receipt_missing_or_mismatch",
                "terminal replay does not match the durable candidate receipt",
            )

    async def insert_candidate_draft_material(
        self,
        *,
        receipt_id: str,
        archive_bytes: bytes,
        validated_draft_hash: str,
        manifest_hash: str,
        archive_hash: str,
        file_set_hash: str,
        effect_topology_hash: str,
        created_at: str,
    ) -> None:
        """Freeze exact candidate bytes beside their receipt in this UoW."""

        receipt_id = str(receipt_id).strip()
        hashes = tuple(
            str(value).strip()
            for value in (
                validated_draft_hash,
                manifest_hash,
                archive_hash,
                file_set_hash,
                effect_topology_hash,
            )
        )
        if (
            not receipt_id
            or not isinstance(archive_bytes, bytes)
            or not archive_bytes
            or any(len(value) != 64 for value in hashes)
            or hashlib.sha256(archive_bytes).hexdigest() != hashes[2]
            or not str(created_at).strip()
        ):
            raise ValueError("candidate draft material is invalid")
        row = await (
            await self._db.execute(
                """SELECT archive_blob,validated_draft_hash,manifest_hash,
                          archive_hash,file_set_hash,effect_topology_hash
                   FROM execution_candidate_draft_materials
                   WHERE receipt_id=?""",
                (receipt_id,),
            )
        ).fetchone()
        expected = (archive_bytes, *hashes)
        if row is not None:
            actual = (
                bytes(row["archive_blob"]),
                str(row["validated_draft_hash"]),
                str(row["manifest_hash"]),
                str(row["archive_hash"]),
                str(row["file_set_hash"]),
                str(row["effect_topology_hash"]),
            )
            if actual != expected:
                raise IdempotencyConflict(
                    "candidate_draft_material_conflict",
                    "candidate draft material replay differs from durable bytes",
                )
            return
        await self._db.execute(
            """INSERT INTO execution_candidate_draft_materials(
               receipt_id,archive_blob,validated_draft_hash,manifest_hash,
               archive_hash,file_set_hash,effect_topology_hash,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (receipt_id, archive_bytes, *hashes, str(created_at).strip()),
        )

    async def verify_candidate_draft_material(
        self,
        *,
        receipt_id: str,
        archive_hash: str,
        validated_draft_hash: str,
        manifest_hash: str,
        file_set_hash: str,
        effect_topology_hash: str,
    ) -> None:
        row = await (
            await self._db.execute(
                """SELECT validated_draft_hash,manifest_hash,archive_hash,
                          file_set_hash,effect_topology_hash
                   FROM execution_candidate_draft_materials
                   WHERE receipt_id=?""",
                (str(receipt_id).strip(),),
            )
        ).fetchone()
        expected = (
            str(validated_draft_hash),
            str(manifest_hash),
            str(archive_hash),
            str(file_set_hash),
            str(effect_topology_hash),
        )
        if row is None or tuple(str(row[index]) for index in range(5)) != expected:
            raise IdempotencyConflict(
                "candidate_draft_material_missing_or_mismatch",
                "terminal replay does not match durable candidate bytes",
            )

    async def insert_or_verify_skill_scope_activation(
        self,
        values: Mapping[str, Any],
        *,
        created_at: float,
    ) -> Mapping[str, Any]:
        """Insert one scope activation beside its continuation outcome."""

        text_fields = (
            "activation_id",
            "run_id",
            "root_run_id",
            "scope_id",
            "scope_hash",
            "capability_snapshot_ref",
            "run_catalog_content_stamp",
            "effective_tool_refs_hash",
            "instruction_content_hash",
            "boundary_ref",
            "boundary_hash",
            "status",
            "receipt_ref",
            "receipt_hash",
        )
        string_list_fields = (
            "allowed_tool_names",
            "effective_tool_ref_hashes",
        )
        object_list_fields = ("allowed_tool_refs",)
        required = {
            *text_fields,
            *string_list_fields,
            *object_list_fields,
            "continuation_version",
        }
        if set(values) != required:
            raise TypeError("skill scope activation fields are incomplete")
        normalized = {name: str(values[name]).strip() for name in text_fields}
        if any(not item for item in normalized.values()):
            raise ValueError("skill scope activation values must be non-empty")
        if normalized["status"] != "committed":
            raise ValueError("skill scope activation must be committed")
        digest_fields = (
            "scope_hash",
            "capability_snapshot_ref",
            "effective_tool_refs_hash",
            "instruction_content_hash",
            "boundary_hash",
            "receipt_hash",
        )
        if any(
            len(normalized[name]) != 64
            or any(ch not in "0123456789abcdef" for ch in normalized[name])
            for name in digest_fields
        ):
            raise ValueError("skill scope activation hash is invalid")
        continuation_version = values["continuation_version"]
        if (
            isinstance(continuation_version, bool)
            or not isinstance(continuation_version, int)
            or continuation_version < 1
        ):
            raise ValueError("skill scope continuation version is invalid")
        string_lists: dict[str, tuple[str, ...]] = {}
        for name in string_list_fields:
            raw = values[name]
            if not isinstance(raw, (list, tuple)) or any(
                not isinstance(item, str) or not item.strip() for item in raw
            ):
                raise ValueError(f"{name} must be a string list")
            canonical = tuple(sorted(set(raw)))
            if tuple(raw) != canonical:
                raise ValueError(f"{name} must be sorted and unique")
            if name == "effective_tool_ref_hashes" and any(
                len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item)
                for item in canonical
            ):
                raise ValueError("effective_tool_ref_hashes must contain exact hashes")
            string_lists[name] = canonical
        raw_refs = values["allowed_tool_refs"]
        if not isinstance(raw_refs, (list, tuple)) or any(
            not isinstance(item, Mapping) for item in raw_refs
        ):
            raise ValueError("allowed_tool_refs must be an object list")
        allowed_tool_refs = tuple(
            sorted(
                (thaw_json(dict(item)) for item in raw_refs),
                key=canonical_json,
            )
        )
        if len({canonical_json(item) for item in allowed_tool_refs}) != len(allowed_tool_refs):
            raise ValueError("allowed_tool_refs must be unique")
        if normalized["scope_id"] != ("skill-scope:" + normalized["scope_hash"]):
            raise ValueError("skill scope id does not match scope hash")
        identity = {
            "schema": "skill-scope-activation/v1",
            "run_id": normalized["run_id"],
            "scope_id": normalized["scope_id"],
            "scope_hash": normalized["scope_hash"],
            "capability_snapshot_ref": normalized["capability_snapshot_ref"],
            "instruction_content_hash": normalized["instruction_content_hash"],
            "effective_tool_refs_hash": normalized["effective_tool_refs_hash"],
        }
        expected_activation_id = "skill-scope-activation:" + fingerprint_json(identity)
        if normalized["activation_id"] != expected_activation_id:
            raise ValueError("skill scope activation id mismatch")
        receipt_payload = {
            "schema": "skill-scope-activation-receipt-v1",
            **normalized,
            "receipt_hash": None,
            **{name: list(string_lists[name]) for name in string_list_fields},
            "allowed_tool_refs": list(allowed_tool_refs),
            "continuation_version": continuation_version,
        }
        receipt_payload.pop("receipt_hash")
        if normalized["receipt_hash"] != fingerprint_json(receipt_payload):
            raise ValueError("skill scope activation receipt hash mismatch")

        columns = (
            *text_fields,
            "allowed_tool_names_json",
            "allowed_tool_refs_json",
            "effective_tool_ref_hashes_json",
            "continuation_version",
            "schema_version",
            "created_at",
        )
        row = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=? OR (run_id=? AND scope_id=?)""",
                (
                    normalized["activation_id"],
                    normalized["run_id"],
                    normalized["scope_id"],
                ),
            )
        ).fetchone()
        expected = {
            **normalized,
            "allowed_tool_names_json": canonical_json(list(string_lists["allowed_tool_names"])),
            "allowed_tool_refs_json": canonical_json(list(allowed_tool_refs)),
            "effective_tool_ref_hashes_json": canonical_json(
                list(string_lists["effective_tool_ref_hashes"])
            ),
            "continuation_version": continuation_version,
            "schema_version": 1,
            "created_at": float(created_at),
        }
        if row is not None:
            for name in columns:
                actual = row[name]
                wanted = expected[name]
                if name == "created_at":
                    continue
                if actual != wanted:
                    raise IdempotencyConflict(
                        "skill_scope_activation_conflict",
                        "scope activation replay differs from durable row",
                    )
            return dict(row)
        await self._db.execute(
            """INSERT INTO execution_skill_scope_activations(
            activation_id,run_id,root_run_id,scope_id,scope_hash,
            capability_snapshot_ref,run_catalog_content_stamp,
            effective_tool_refs_hash,instruction_content_hash,boundary_ref,
            boundary_hash,status,receipt_ref,receipt_hash,
            allowed_tool_names_json,allowed_tool_refs_json,
            effective_tool_ref_hashes_json,continuation_version,
            schema_version,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            tuple(expected[name] for name in columns if name != "schema_version"),
        )
        inserted = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=?""",
                (normalized["activation_id"],),
            )
        ).fetchone()
        assert inserted is not None
        return dict(inserted)

    async def read_skill_scope_activation(self, activation_id: str) -> Mapping[str, Any] | None:
        row = await (
            await self._db.execute(
                """SELECT * FROM execution_skill_scope_activations
                WHERE activation_id=?""",
                (str(activation_id).strip(),),
            )
        ).fetchone()
        return None if row is None else dict(row)

    async def insert_or_verify_run_block_signal(
        self, values: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Write one presentation-only block signal in the terminal tx.

        The v29 schema owns this sidecar.  This typed operation intentionally
        exposes no generic SQL surface to terminal extensions.
        """

        required = {
            "signal_id",
            "root_run_id",
            "reason_code",
            "evidence_refs_json",
            "producer",
            "created_event_id",
            "payload_hash",
            "schema_version",
            "created_at",
        }
        if set(values) != required:
            raise TypeError("run block signal fields are incomplete")
        signal_id = str(values["signal_id"]).strip()
        root_run_id = str(values["root_run_id"]).strip()
        if not signal_id or not root_run_id:
            raise ValueError("run block signal identity is empty")
        existing = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals
                WHERE signal_id=? OR root_run_id=?""",
                (signal_id, root_run_id),
            )
        ).fetchone()
        ordered_fields = (
            "signal_id",
            "root_run_id",
            "reason_code",
            "evidence_refs_json",
            "producer",
            "created_event_id",
            "payload_hash",
            "schema_version",
            "created_at",
        )
        expected = tuple(values[name] for name in ordered_fields)
        if existing is not None:
            actual = tuple(existing[name] for name in ordered_fields)
            if actual != expected:
                raise IdempotencyConflict(
                    "run_block_signal_conflict",
                    "root terminal replay supplied a different block signal",
                )
            return dict(existing)
        await self._db.execute(
            """INSERT INTO execution_run_block_signals(
            signal_id,root_run_id,reason_code,evidence_refs_json,producer,
            created_event_id,payload_hash,schema_version,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            expected,
        )
        inserted = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals WHERE signal_id=?""",
                (signal_id,),
            )
        ).fetchone()
        assert inserted is not None
        return dict(inserted)

    async def read_run_block_signal(self, root_run_id: str) -> Mapping[str, Any] | None:
        row = await (
            await self._db.execute(
                """SELECT signal_id,root_run_id,reason_code,evidence_refs_json,
                producer,created_event_id,payload_hash,schema_version,created_at
                FROM execution_run_block_signals WHERE root_run_id=?""",
                (str(root_run_id).strip(),),
            )
        ).fetchone()
        return None if row is None else dict(row)


class ExecutionUowCore:
    """Connection, transactions and shared row converters."""

    if TYPE_CHECKING:

        async def _admission_tx(self, db: aiosqlite.Connection, run_id: str): ...
        @staticmethod
        def _advance_admission(
            boundary: AdmissionBoundary,
            phase: AdmissionPhase,
            version: int,
            *,
            consumed: bool | None = None,
            resolution_fingerprint: str | None = None,
        ) -> AdmissionBoundary: ...
        async def _append_event_tx(
            self,
            db: aiosqlite.Connection,
            run: aiosqlite.Row,
            *,
            expected_version: int,
            event: RunEventCandidate,
            deliveries: Sequence[DeliverySpec],
        ) -> tuple[RunEvent, aiosqlite.Row, bool]: ...
        @staticmethod
        def _assert_decision_intent(existing: DecisionRecord, requested: DecisionOpen) -> None: ...
        async def _assert_delivery_set_tx(
            self, db: aiosqlite.Connection, event_id: str, deliveries: Sequence[DeliverySpec]
        ) -> None: ...
        def _assert_event_matches(
            self, row: Mapping[str, Any], run: Mapping[str, Any], candidate: RunEventCandidate
        ) -> None: ...
        async def _assert_optional_recovery_fence_tx(
            self, db: aiosqlite.Connection, lease: RecoveryLease | None, run_id: str
        ) -> None: ...
        async def _assert_recovery_fence_tx(
            self, db: aiosqlite.Connection, lease: RecoveryLease, *, run_id: str
        ) -> None: ...
        @staticmethod
        def _assert_run_owner(row: Mapping[str, Any], expected_owner: tuple[str, int]) -> None: ...
        @staticmethod
        def _assert_signal_binding(decision: DecisionRecord, signal: DecisionSignal) -> None: ...
        def _authorize_run_row(
            self, row: Mapping[str, Any], *, expected_session_id: str, actor: ActorContext
        ) -> RunRecord: ...
        def _authorize_view(self, ref: RunRef, actor: ActorContext, view: RunView) -> None: ...
        @staticmethod
        def _continuation_payload_json(payload: Mapping[str, Any]) -> str: ...
        async def _continuation_run_tx(
            self, db: aiosqlite.Connection, run_id: str
        ) -> aiosqlite.Row: ...
        async def _enqueue_child_terminal_signal_tx(
            self,
            db: aiosqlite.Connection,
            operation_id: str,
            *,
            terminal_status: str,
            value: Any = None,
        ) -> ChildSignalRecord: ...
        async def _ensure_start_snapshot_tx(
            self,
            db: aiosqlite.Connection,
            *,
            spec: RunCreate,
            start_snapshot: RunStartSnapshotRecord,
            run_created: bool,
            start_commit_extensions: Sequence[Any],
        ) -> RunStartSnapshotRecord: ...
        @staticmethod
        def _event_json(candidate: RunEventCandidate) -> dict[str, str | None]: ...
        async def _insert_continuation_decision_tx(
            self,
            db: aiosqlite.Connection,
            decision: DecisionOpen,
            *,
            run: Mapping[str, Any],
            now: float,
        ) -> None: ...
        async def _insert_deliveries_tx(
            self,
            db: aiosqlite.Connection,
            *,
            run_id: str,
            event_id: str,
            deliveries: Sequence[DeliverySpec],
            now: float,
            delivery_fence: Mapping[str, Any] | None = None,
            release_receipt: Mapping[str, Any] | None = None,
        ) -> None: ...
        async def _insert_run_for_owner_tx(
            self,
            db: aiosqlite.Connection,
            spec: RunCreate,
            *,
            version: int,
            expected_owner: tuple[str, int],
        ) -> tuple[aiosqlite.Row, bool]: ...
        async def _insert_run_tx(
            self, db: aiosqlite.Connection, spec: RunCreate, *, version: int
        ) -> tuple[aiosqlite.Row, bool]: ...
        async def _load_continuation_tx(
            self, db: aiosqlite.Connection, run_id: str
        ) -> ContinuationRecord | None: ...
        async def _recovery_run_tx(
            self, db: aiosqlite.Connection, run_id: str
        ) -> aiosqlite.Row: ...
        async def _required_recovery_owner_tx(
            self, db: aiosqlite.Connection
        ) -> tuple[str, int]: ...
        async def _required_start_owner_tx(self, db: aiosqlite.Connection) -> tuple[str, int]: ...
        async def _resolve_decision_tx(
            self,
            db: aiosqlite.Connection,
            signal: DecisionSignal,
            actor: ActorContext,
            *,
            now: float,
            authorization_commit: PreparedAuthorizationCommit | None = None,
        ) -> tuple[DecisionRecord, DecisionAuthorization | None, DecisionConflict | None]: ...
        @staticmethod
        def _row_to_continuation(row: Mapping[str, Any]) -> ContinuationRecord: ...
        async def _save_continuation_tx(
            self,
            db: aiosqlite.Connection,
            *,
            run: Mapping[str, Any],
            expected_version: int,
            payload_json: str,
            decision: DecisionOpen | None,
            now: float,
        ) -> tuple[ContinuationRecord, bool]: ...
        @staticmethod
        def _stable_child_link_id(operation_id: str) -> str: ...
        @staticmethod
        def _validate_continuation_version(expected_version: int) -> None: ...
        @staticmethod
        def _with_admission(
            payload: Mapping[str, Any], boundary: AdmissionBoundary
        ) -> dict[str, Any]: ...
        async def commit_run_outcome(
            self,
            run_id: str,
            *,
            expected_version: int,
            terminal_status: RunStatus | None = None,
            event: RunEventCandidate,
            deliveries: Sequence[DeliverySpec] = (),
            parent_signal_operation_id: str | None = None,
            parent_signal_value: Any = None,
            recovery_lease: RecoveryLease | None = None,
            cancel_reason: str | None = None,
            admission_failure: AdmissionLaunchUnknownFence | None = None,
            terminal_commit_extensions: Sequence[Any] = (),
            delivery_fence: Mapping[str, Any] | None = None,
            release_receipt_kind: str | None = None,
        ) -> FinalizeRunResult | RunRecord: ...
        async def persist_react_boundary(
            self,
            run: str | RunCreate,
            expected_continuation_version: int,
            payload: Mapping[str, Any],
            decision: DecisionOpen | None = None,
            *,
            expected_run_version: int | None = None,
            waiting_event: RunEventCandidate | None = None,
            deliveries: Sequence[DeliverySpec] = (),
            recovery_lease: RecoveryLease | None = None,
            admission_launch: AdmissionLaunchClaim | None = None,
            skill_scope_activation_values: Mapping[str, Any] | None = None,
        ) -> ContinuationRecord | tuple[CreateRunResult, ContinuationRecord]: ...

    _WORKFLOW_TERMINAL_EFFECTS = frozenset(
        {"succeeded", "failed", "unknown", "cancelled", "late_reconciled"}
    )
    _WORKFLOW_DECISION_KINDS = frozenset(
        {"permission", "plan", "clarification", "ppt_outline", "skill_candidate"}
    )

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: _FaultInjector | None = None,
        write_lane: ExecutionWriteLane | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._fault_injector = fault_injector
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._write_lane = write_lane or get_execution_write_lane(self.path)
        self._write_lane.acquire_owner()
        self._write_lane_owner_active = True

    def __del__(self) -> None:
        if getattr(self, "_write_lane_owner_active", False):
            self._write_lane_owner_active = False
            self._write_lane.release_owner_nowait()

    def bind(self, db: aiosqlite.Connection) -> ExecutionTx:
        """Bind typed execution DML to a caller-owned open transaction."""

        return ExecutionTx(self, db)

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            if not self._write_lane_owner_active:
                self._write_lane = get_execution_write_lane(self.path)
                self._write_lane.acquire_owner()
                self._write_lane_owner_active = True
            await initialize_workflow_db(self.path)
            await self._write_lane.open()
            self._initialized = True

    async def close(self) -> None:
        """Bounded shutdown for the shared execution writer lane."""

        if self._write_lane_owner_active:
            self._write_lane_owner_active = False
            await self._write_lane.release_owner()
        self._initialized = False

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    @asynccontextmanager
    async def _read_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await self._connect()
        try:
            yield db
        finally:
            await db.close()

    @asynccontextmanager
    async def _write_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        await self.initialize()
        async with self._write_lane.transaction() as db:
            yield db  # type: ignore[misc]

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> RunRecord:
        context = RunContext(
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            parent_run_id=(str(row["parent_run_id"]) if row["parent_run_id"] is not None else None),
            request_id=str(row["request_id"]),
            turn_id=str(row["turn_id"]),
            venue=str(row["venue"]),
            workspace=json.loads(str(row["workspace_json"])),
            capability_hash=str(row["capability_hash"]),
            provider_plan=json.loads(str(row["provider_plan_json"])),
            trace_id=str(row["trace_id"]),
            principal_id=str(row["principal_id"]),
            auth_epoch=int(row["auth_epoch"]),
            owner_key=(None if row["context_owner_key"] is None else str(row["context_owner_key"])),
            profile_generation=int(row["context_profile_generation"]),
            binding_epoch=int(row["context_binding_epoch"]),
            schema_version=int(row["schema_version"]),
        )
        spec = RunCreate(
            run_id=str(row["run_id"]),
            idempotency_key=str(row["idempotency_key"]),
            context=context,
            payload_fingerprint=str(row["payload_fingerprint"]),
            capability_fingerprint=str(row["capability_fingerprint"]),
            driver_kind=str(row["driver_kind"]),
            profile_key=str(row["profile_key"]),
            persistence_level=str(row["persistence_level"]),
            # Creation status is not mutable execution state.  It is kept
            # non-terminal when reconstructing the immutable creation intent.
            status=RunStatus.CREATED,
            schema_version=int(row["schema_version"]),
        )
        return RunRecord(
            spec=spec,
            status=str(row["status"]),
            persistence_level=str(row["persistence_level"]),
            version=int(row["version"]),
            durable_seq=int(row["durable_seq"]),
            terminal_event_id=(
                str(row["terminal_event_id"]) if row["terminal_event_id"] is not None else None
            ),
            cancel_reason=(str(row["cancel_reason"]) if row["cancel_reason"] is not None else None),
            created_at=float(row["created_at"]),
            started_at=(float(row["started_at"]) if row["started_at"] is not None else None),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
            schema_version=int(row["schema_version"]),
        )

    async def read_run(self, run_id: str) -> RunRecord | None:
        """Read the immutable Run intent plus its current lifecycle state."""

        await self.initialize()
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (str(run_id),),
                )
            ).fetchone()
            return None if row is None else self._row_to_record(row)

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> RunEvent:
        candidate = RunEventCandidate(
            event_key=str(row["event_key"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            driver_kind=str(row["driver_kind"]),
            correlation=json.loads(str(row["correlation_json"])),
            payload=json.loads(str(row["payload_json"])),
            error=(json.loads(str(row["error_json"])) if row["error_json"] is not None else None),
            artifact_refs=json.loads(str(row["artifact_refs_json"])),
            schema_version=int(row["schema_version"]),
        )
        return RunEvent(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            root_run_id=str(row["root_run_id"]),
            session_id=str(row["session_id"]),
            durable_seq=int(row["durable_seq"]),
            candidate=candidate,
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_delivery(row: Mapping[str, Any]) -> DeliveryRecord:
        values = dict(row)
        return DeliveryRecord(
            delivery_id=str(row["delivery_id"]),
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            sink_kind=str(row["sink_kind"]),
            sink_instance=str(row["sink_instance"]),
            target_id=str(row["target_id"]),
            policy=str(row["policy"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            delivery_version=int(row["delivery_version"]),
            next_attempt_at=(
                float(row["next_attempt_at"]) if row["next_attempt_at"] is not None else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(float(row["delivered_at"]) if row["delivered_at"] is not None else None),
            delivery_fence_epoch=(
                None
                if values.get("delivery_fence_epoch") is None
                else int(values["delivery_fence_epoch"])
            ),
            delivery_owner_id=(
                None
                if values.get("delivery_owner_id") is None
                else str(values["delivery_owner_id"])
            ),
            delivery_owner_generation=(
                None
                if values.get("delivery_owner_generation") is None
                else int(values["delivery_owner_generation"])
            ),
            delivery_dependency_hash=(
                None
                if values.get("delivery_dependency_hash") is None
                else str(values["delivery_dependency_hash"])
            ),
            delivery_snapshot_ref=(
                None
                if values.get("delivery_snapshot_ref") is None
                else str(values["delivery_snapshot_ref"])
            ),
            delivery_snapshot_hash=(
                None
                if values.get("delivery_snapshot_hash") is None
                else str(values["delivery_snapshot_hash"])
            ),
            release_receipt_ref=(
                None
                if values.get("release_receipt_ref") is None
                else str(values["release_receipt_ref"])
            ),
            release_receipt_hash=(
                None
                if values.get("release_receipt_hash") is None
                else str(values["release_receipt_hash"])
            ),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_child_command(row: Mapping[str, Any]) -> ChildCommandRecord:
        child_spec = RunCreate.from_dict(json.loads(str(row["child_spec_json"])))
        intent = ChildCommandIntent(
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_spec=child_spec,
            child_request=json.loads(str(row["child_request_json"])),
            capability_subset=tuple(
                str(item) for item in json.loads(str(row["capability_subset_json"]))
            ),
            attachment_policy=str(row["join_policy"]),
            capability_snapshot_ref=str(row["capability_snapshot_ref"]),
            schema_version=int(row["schema_version"]),
        )
        if intent.intent_fingerprint != str(row["intent_fingerprint"]):
            raise IdempotencyConflict(
                "child_intent_corrupt", "persisted child command fingerprint differs"
            )
        return ChildCommandRecord(
            intent=intent,
            status=str(row["status"]),
            schedule_lease_owner=(
                str(row["schedule_lease_owner"])
                if row["schedule_lease_owner"] is not None
                else None
            ),
            schedule_lease_epoch=int(row["schedule_lease_epoch"]),
            schedule_lease_expires_at=(
                float(row["schedule_lease_expires_at"])
                if row["schedule_lease_expires_at"] is not None
                else None
            ),
            attempts=int(row["attempts"]),
            next_attempt_at=(
                float(row["next_attempt_at"]) if row["next_attempt_at"] is not None else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] else None),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ack_at=float(row["ack_at"]) if row["ack_at"] is not None else None,
        )

    @staticmethod
    def _row_to_child_signal(row: Mapping[str, Any]) -> ChildSignalRecord:
        return ChildSignalRecord(
            signal_id=str(row["signal_id"]),
            operation_id=str(row["operation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            command_id=str(row["command_id"]),
            child_run_id=str(row["child_run_id"]),
            kind=str(row["kind"]),
            payload=json.loads(str(row["payload_json"])),
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            delivered_at=(float(row["delivered_at"]) if row["delivered_at"] is not None else None),
        )

    @staticmethod
    def _row_to_decision(row: Mapping[str, Any]) -> DecisionRecord:
        request = DecisionOpen(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            nonce=str(row["nonce"]),
            kind=str(row["kind"]),
            prompt_schema_version=int(row["prompt_schema_version"]),
            prompt=json.loads(str(row["prompt_json"])),
            expires_at=(float(row["expires_at"]) if row["expires_at"] is not None else None),
            domain_kind=(str(row["domain_kind"]) if row["domain_kind"] is not None else None),
            domain_id=(str(row["domain_id"]) if row["domain_id"] is not None else None),
            call_id=(str(row["call_id"]) if row["call_id"] is not None else None),
            effect_id=(str(row["effect_id"]) if row["effect_id"] is not None else None),
            tool_name=(str(row["tool_name"]) if row["tool_name"] is not None else None),
            args_hash=(str(row["args_hash"]) if row["args_hash"] is not None else None),
            capability_hash=(
                str(row["capability_hash"]) if row["capability_hash"] is not None else None
            ),
            scope_hash=(str(row["scope_hash"]) if row["scope_hash"] is not None else None),
            schema_version=int(row["schema_version"]),
        )
        response = (
            json.loads(str(row["response_json"])) if row["response_json"] is not None else None
        )
        return DecisionRecord(
            request=request,
            status=str(row["status"]),
            response_schema_version=(
                int(row["response_schema_version"])
                if row["response_schema_version"] is not None
                else None
            ),
            response=response,
            decision_version=int(row["decision_version"]),
            created_at=float(row["created_at"]),
            resolved_at=(float(row["resolved_at"]) if row["resolved_at"] is not None else None),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_authorization(row: Mapping[str, Any]) -> DecisionAuthorization:
        return DecisionAuthorization(
            grant_id=str(row["grant_id"]),
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            call_id=str(row["call_id"]),
            effect_id=str(row["effect_id"]),
            tool_name=str(row["tool_name"]),
            args_hash=str(row["args_hash"]),
            capability_hash=str(row["capability_hash"]),
            scope_hash=str(row["scope_hash"]),
            expires_at=float(row["expires_at"]),
            version=int(row["grant_version"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_task_goal(row: Mapping[str, Any]) -> TaskGoalRecord:
        return TaskGoalRecord(
            goal_id=str(row["goal_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            objective_ref=str(row["objective_ref"]),
            status=str(row["status"]),
            version=int(row["goal_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_task_work_context(row: Mapping[str, Any]) -> TaskWorkContext:
        return TaskWorkContext(
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            workspace_root=(
                str(row["workspace_root"]) if row["workspace_root"] is not None else None
            ),
            workspace_source=str(row["workspace_source"]),
            binding_version=int(row["binding_version"]),
        )

    @staticmethod
    def _row_to_conversation_boundary(
        row: Mapping[str, Any],
    ) -> ConversationBoundary:
        return ConversationBoundary(
            boundary_ref=str(row["boundary_ref"]),
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            seed_message_refs=tuple(
                str(item) for item in json.loads(str(row["seed_message_refs_json"]))
            ),
            continuation_message_refs=tuple(
                str(item) for item in json.loads(str(row["continuation_message_refs_json"]))
            ),
            version=int(row["boundary_version"]),
        )

    @staticmethod
    def _row_to_user_continuation(
        row: Mapping[str, Any],
    ) -> QueuedUserContinuation:
        return QueuedUserContinuation(
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            message_ref=str(row["message_ref"]),
            content=str(row["content"]),
            reserved_boundary_version=int(row["reserved_boundary_version"]),
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            settled_at=(None if row["settled_at"] is None else float(row["settled_at"])),
            error=(None if row["error"] is None else str(row["error"])),
        )

    @staticmethod
    def _row_to_task_run_projection(
        row: Mapping[str, Any],
    ) -> TaskRunProjection:
        return TaskRunProjection(
            projection_id=str(row["projection_id"]),
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            ui_state=str(row["ui_state"]),
            version=int(row["projection_version"]),
        )

    @staticmethod
    def _row_to_plan_version(row: Mapping[str, Any]) -> PlanVersionRecord:
        return PlanVersionRecord(
            root_run_id=str(row["root_run_id"]),
            plan_version=int(row["plan_version"]),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _row_to_provider_turn(row: Mapping[str, Any]) -> ProviderTurnFence:
        return ProviderTurnFence(
            provider_turn_id=str(row["provider_turn_id"]),
            root_run_id=str(row["root_run_id"]),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            state=str(row["state"]),
            accepted_batch_id=(
                str(row["accepted_batch_id"]) if row["accepted_batch_id"] is not None else None
            ),
            version=int(row["fence_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            accepted_at=(float(row["accepted_at"]) if row["accepted_at"] is not None else None),
        )

    @staticmethod
    def _row_to_provider_batch(row: Mapping[str, Any]) -> ProviderActionBatch:
        return ProviderActionBatch(
            provider_batch_id=str(row["provider_batch_id"]),
            root_run_id=str(row["root_run_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            canonical_assistant_batch_ref=str(row["canonical_assistant_batch_ref"]),
            batch_fingerprint=str(row["batch_fingerprint"]),
            pending_call_count=int(row["pending_call_count"]),
            status=str(row["status"]),
            failure_set_id=(
                str(row["failure_set_id"]) if row["failure_set_id"] is not None else None
            ),
            version=int(row["batch_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            settled_at=(float(row["settled_at"]) if row["settled_at"] is not None else None),
        )

    @staticmethod
    def _row_to_provider_call(row: Mapping[str, Any]) -> ProviderActionCall:
        return ProviderActionCall(
            call_record_id=str(row["call_record_id"]),
            root_run_id=str(row["root_run_id"]),
            provider_batch_id=str(row["provider_batch_id"]),
            call_order=int(row["call_order"]),
            provider_call_id=str(row["provider_call_id"]),
            raw_tool_name=str(row["raw_tool_name"]),
            raw_arguments_ref=str(row["raw_arguments_ref"]),
            raw_arguments_hash=str(row["raw_arguments_hash"]),
            parsed_arguments_hash=(
                str(row["parsed_arguments_hash"])
                if row["parsed_arguments_hash"] is not None
                else None
            ),
            admission_state=str(row["admission_state"]),
            prepared_call_ref=(
                str(row["prepared_call_ref"]) if row["prepared_call_ref"] is not None else None
            ),
            command_boundary_ref=(
                str(row["command_boundary_ref"])
                if row["command_boundary_ref"] is not None
                else None
            ),
            terminal_outcome_ref=(
                str(row["terminal_outcome_ref"])
                if row["terminal_outcome_ref"] is not None
                else None
            ),
            version=int(row["call_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _row_to_attempt(row: Mapping[str, Any]) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=str(row["attempt_id"]),
            root_run_id=str(row["root_run_id"]),
            run_id=str(row["run_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            provider_batch_id=str(row["provider_batch_id"]),
            plan_version=int(row["plan_version"]),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            supersedes_attempt_id=(
                str(row["supersedes_attempt_id"])
                if row["supersedes_attempt_id"] is not None
                else None
            ),
            strategy_fingerprint=str(row["strategy_fingerprint"]),
            planned_call_refs=tuple(
                str(item) for item in json.loads(str(row["planned_call_refs_json"]))
            ),
            checkpoint_ref=(
                str(row["checkpoint_ref"]) if row["checkpoint_ref"] is not None else None
            ),
            status=str(row["status"]),
            budget_eligible=bool(row["budget_eligible"]),
            version=int(row["attempt_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_failure_report(row: Mapping[str, Any]) -> TaskFailureReport:
        return TaskFailureReport(
            report_ref=str(row["report_ref"]),
            root_run_id=str(row["root_run_id"]),
            run_id=str(row["run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            attempt_id=str(row["attempt_id"]),
            plan_version=int(row["plan_version"]),
            call_record_id=str(row["call_record_id"]),
            source_kind=str(row["source_kind"]),
            source_identity=str(row["source_identity"]),
            provider_call_id=(
                str(row["provider_call_id"]) if row["provider_call_id"] is not None else None
            ),
            child_run_id=(str(row["child_run_id"]) if row["child_run_id"] is not None else None),
            inner_failure_ref=(
                str(row["inner_failure_ref"]) if row["inner_failure_ref"] is not None else None
            ),
            failed_call_id=(
                str(row["failed_call_id"]) if row["failed_call_id"] is not None else None
            ),
            failed_effect_id=(
                str(row["failed_effect_id"]) if row["failed_effect_id"] is not None else None
            ),
            failed_step=str(row["failed_step"]),
            error_class=str(row["error_class"]),
            error_code=str(row["error_code"]),
            error_fingerprint=str(row["error_fingerprint"]),
            action_fingerprint=str(row["action_fingerprint"]),
            exit_code=(int(row["exit_code"]) if row["exit_code"] is not None else None),
            evidence_refs=tuple(str(item) for item in json.loads(str(row["evidence_refs_json"]))),
            completed_step_refs=tuple(
                str(item) for item in json.loads(str(row["completed_step_refs_json"]))
            ),
            artifact_refs=tuple(str(item) for item in json.loads(str(row["artifact_refs_json"]))),
            checkpoint_ref=(
                str(row["checkpoint_ref"]) if row["checkpoint_ref"] is not None else None
            ),
            prior_strategy_fingerprints=tuple(
                str(item) for item in json.loads(str(row["prior_strategy_fingerprints_json"]))
            ),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_external_wait(row: Mapping[str, Any]) -> TaskExternalWait:
        return TaskExternalWait(
            wait_ref=str(row["wait_ref"]),
            root_run_id=str(row["root_run_id"]),
            attempt_id=str(row["attempt_id"]),
            call_record_id=str(row["call_record_id"]),
            provider_call_id=str(row["provider_call_id"]),
            command_boundary_ref=(
                str(row["command_boundary_ref"])
                if row["command_boundary_ref"] is not None
                else None
            ),
            effect_id=(str(row["effect_id"]) if row["effect_id"] is not None else None),
            wait_kind=str(row["wait_kind"]),
            required_action_ref=str(row["required_action_ref"]),
            checkpoint_ref=(
                str(row["checkpoint_ref"]) if row["checkpoint_ref"] is not None else None
            ),
            resume_admission_state=str(row["resume_admission_state"]),
            evidence_refs=tuple(str(item) for item in json.loads(str(row["evidence_refs_json"]))),
            state=str(row["state"]),
            response_ref=(str(row["response_ref"]) if row["response_ref"] is not None else None),
            version=int(row["wait_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            resolved_at=(float(row["resolved_at"]) if row["resolved_at"] is not None else None),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_profile_ticket(row: Mapping[str, Any]) -> ProfileLaunchTicket:
        return ProfileLaunchTicket(
            ticket_ref=str(row["ticket_ref"]),
            parent_run_id=str(row["parent_run_id"]),
            root_run_id=str(row["root_run_id"]),
            task_scope_id=str(row["task_scope_id"]),
            attempt_id=str(row["attempt_id"]),
            provider_turn_id=str(row["provider_turn_id"]),
            profile_key=str(row["profile_key"]),
            driver_kind=str(row["driver_kind"]),
            profile_catalog_generation=int(row["profile_catalog_generation"]),
            capability_snapshot_ref=str(row["capability_snapshot_ref"]),
            task_grant_ref=str(row["task_grant_ref"]),
            spawn_call_id=str(row["spawn_call_id"]),
            personal_selection_id=(
                str(row["personal_selection_id"])
                if row["personal_selection_id"] is not None
                else None
            ),
            personal_selection_fingerprint=(
                str(row["personal_selection_fingerprint"])
                if row["personal_selection_fingerprint"] is not None
                else None
            ),
            trigger_failure_set_id=(
                str(row["trigger_failure_set_id"])
                if row["trigger_failure_set_id"] is not None
                else None
            ),
            request_fingerprint=str(row["request_fingerprint"]),
            state=str(row["state"]),
            child_command_id=(
                str(row["child_command_id"]) if row["child_command_id"] is not None else None
            ),
            child_run_id=(str(row["child_run_id"]) if row["child_run_id"] is not None else None),
            version=int(row["ticket_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            consumed_at=(float(row["consumed_at"]) if row["consumed_at"] is not None else None),
            cancelled_at=(float(row["cancelled_at"]) if row["cancelled_at"] is not None else None),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    async def _failure_set_from_row(
        db: aiosqlite.Connection, row: Mapping[str, Any]
    ) -> AttemptFailureSet:
        members = await (
            await db.execute(
                """SELECT report_ref FROM execution_attempt_failure_set_members
                WHERE failure_set_id=? ORDER BY provider_call_order,report_ref""",
                (str(row["failure_set_id"]),),
            )
        ).fetchall()
        return AttemptFailureSet(
            failure_set_id=str(row["failure_set_id"]),
            root_run_id=str(row["root_run_id"]),
            failed_attempt_id=str(row["failed_attempt_id"]),
            report_refs=tuple(str(member["report_ref"]) for member in members),
            primary_report_ref=str(row["primary_report_ref"]),
            backfill_state=str(row["backfill_state"]),
            provider_resume_state=str(row["provider_resume_state"]),
            version=int(row["set_version"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _same_goal_intent(existing: TaskGoalRecord, requested: TaskGoalRecord) -> bool:
        return (
            existing.goal_id,
            existing.root_run_id,
            existing.task_scope_id,
            existing.objective_ref,
        ) == (
            requested.goal_id,
            requested.root_run_id,
            requested.task_scope_id,
            requested.objective_ref,
        )

    @staticmethod
    def _same_batch_intent(existing: ProviderActionBatch, requested: ProviderActionBatch) -> bool:
        return (
            existing.provider_batch_id,
            existing.root_run_id,
            existing.provider_turn_id,
            existing.canonical_assistant_batch_ref,
            existing.batch_fingerprint,
        ) == (
            requested.provider_batch_id,
            requested.root_run_id,
            requested.provider_turn_id,
            requested.canonical_assistant_batch_ref,
            requested.batch_fingerprint,
        )

    @staticmethod
    def _same_attempt_intent(existing: AttemptRecord, requested: AttemptRecord) -> bool:
        return (
            existing.attempt_id,
            existing.root_run_id,
            existing.run_id,
            existing.provider_turn_id,
            existing.provider_batch_id,
            existing.plan_version,
            existing.trigger_failure_set_id,
            existing.supersedes_attempt_id,
            existing.strategy_fingerprint,
            tuple(existing.planned_call_refs),
            existing.checkpoint_ref,
        ) == (
            requested.attempt_id,
            requested.root_run_id,
            requested.run_id,
            requested.provider_turn_id,
            requested.provider_batch_id,
            requested.plan_version,
            requested.trigger_failure_set_id,
            requested.supersedes_attempt_id,
            requested.strategy_fingerprint,
            tuple(requested.planned_call_refs),
            requested.checkpoint_ref,
        )

    @staticmethod
    def _same_call_intent(existing: ProviderActionCall, requested: ProviderActionCall) -> bool:
        identity_matches = (
            existing.call_record_id,
            existing.root_run_id,
            existing.provider_batch_id,
            existing.call_order,
            existing.provider_call_id,
            existing.raw_tool_name,
            existing.raw_arguments_ref,
            existing.raw_arguments_hash,
        ) == (
            requested.call_record_id,
            requested.root_run_id,
            requested.provider_batch_id,
            requested.call_order,
            requested.provider_call_id,
            requested.raw_tool_name,
            requested.raw_arguments_ref,
            requested.raw_arguments_hash,
        )
        if not identity_matches:
            return False
        if requested.admission_state is ProviderActionAdmissionState.REJECTED:
            return (
                existing.admission_state is ProviderActionAdmissionState.REJECTED
                and existing.terminal_outcome_ref == requested.terminal_outcome_ref
            )
        return existing.admission_state is not ProviderActionAdmissionState.REJECTED

    @staticmethod
    def _same_ticket_intent(existing: ProfileLaunchTicket, requested: ProfileLaunchTicket) -> bool:
        fields = (
            "ticket_ref",
            "parent_run_id",
            "root_run_id",
            "task_scope_id",
            "attempt_id",
            "provider_turn_id",
            "profile_key",
            "driver_kind",
            "profile_catalog_generation",
            "capability_snapshot_ref",
            "task_grant_ref",
            "spawn_call_id",
            "personal_selection_id",
            "personal_selection_fingerprint",
            "trigger_failure_set_id",
            "request_fingerprint",
        )
        return all(
            getattr(existing, field_name) == getattr(requested, field_name) for field_name in fields
        )
