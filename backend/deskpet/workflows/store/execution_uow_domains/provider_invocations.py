"""Provider invocation claim, completion and settlement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from deskpet.execution.contracts import (
    IdempotencyConflict,
    ProviderInvocationOutcome,
    ProviderInvocationRecord,
    ProviderInvocationStatus,
    RunNotFound,
    VersionConflict,
)

from .core import ExecutionUowCore


class ProviderInvocationMixin(ExecutionUowCore):
    """Provider invocation claim, completion and settlement."""

    __slots__ = ()

    @staticmethod
    def _row_to_provider_invocation(
        row: Mapping[str, Any],
    ) -> ProviderInvocationRecord:
        return ProviderInvocationRecord(
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            adapter_id=str(row["adapter_id"]),
            policy_snapshot_json=str(row["policy_snapshot_json"]),
            request_hash=str(row["request_hash"]),
            idempotency_group_id=str(row["idempotency_group_id"]),
            attempt_ordinal=int(row["attempt_ordinal"]),
            status=str(row["status"]),
            claim_owner=str(row["claim_owner"]),
            claim_epoch=int(row["claim_epoch"]),
            claimed_at=float(row["claimed_at"]),
            revocation_epoch=int(row["revocation_epoch"]),
            dispatch_started_ack_ref=(
                None
                if row["dispatch_started_ack_ref"] is None
                else str(row["dispatch_started_ack_ref"])
            ),
            dispatch_started_ack_hash=(
                None
                if row["dispatch_started_ack_hash"] is None
                else str(row["dispatch_started_ack_hash"])
            ),
            dispatch_started_at=(
                None if row["dispatch_started_at"] is None else float(row["dispatch_started_at"])
            ),
            stream_epoch=int(row["stream_epoch"]),
            outcome_ref=(None if row["outcome_ref"] is None else str(row["outcome_ref"])),
            outcome_hash=(None if row["outcome_hash"] is None else str(row["outcome_hash"])),
            updated_at=float(row["updated_at"]),
            schema_version=int(row["schema_version"]),
        )

    @staticmethod
    def _row_to_provider_outcome(
        row: Mapping[str, Any],
    ) -> ProviderInvocationOutcome:
        return ProviderInvocationOutcome(
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            payload_json=str(row["payload_json"]),
            payload_hash=str(row["payload_hash"]),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    async def _record_provider_invocation_input_tx(
        self,
        db: Any,
        run_id: str,
        invocation_id: str,
        *,
        payload_json: str,
        payload_hash: str,
        created_at: float,
    ) -> None:
        try:
            normalized_payload = json.dumps(
                json.loads(payload_json),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("provider input projection must be canonical JSON") from exc
        expected_hash = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
        if payload_hash != expected_hash:
            raise ValueError("provider input projection hash mismatch")
        existing = await (
            await db.execute(
                """SELECT payload_json,payload_hash
                FROM execution_provider_invocation_inputs
                WHERE run_id=? AND invocation_id=?""",
                (run_id, invocation_id),
            )
        ).fetchone()
        if existing is not None:
            if (
                str(existing["payload_json"]) != normalized_payload
                or str(existing["payload_hash"]) != payload_hash
            ):
                raise IdempotencyConflict(
                    "provider_invocation_input_conflict",
                    "invocation already has a different input projection",
                )
            return
        await db.execute(
            """INSERT INTO execution_provider_invocation_inputs(
            run_id,invocation_id,schema_version,payload_json,payload_hash,
            created_at
            ) VALUES(?,?,1,?,?,?)""",
            (
                run_id,
                invocation_id,
                normalized_payload,
                payload_hash,
                float(created_at),
            ),
        )

    async def claim_provider_invocation(
        self,
        record: ProviderInvocationRecord,
        *,
        input_payload_json: str | None = None,
        input_payload_hash: str | None = None,
        input_created_at: float | None = None,
    ) -> ProviderInvocationRecord:
        if record.status is not ProviderInvocationStatus.CLAIMED:
            raise IdempotencyConflict(
                "provider_invocation_not_claimed",
                "new provider invocation rows must begin as claimed",
            )
        if record.outcome_ref is not None:
            raise IdempotencyConflict(
                "provider_invocation_has_outcome",
                "claimed provider invocation cannot already have an outcome",
            )
        input_values = (
            input_payload_json,
            input_payload_hash,
            input_created_at,
        )
        if any(value is not None for value in input_values) and not all(
            value is not None for value in input_values
        ):
            raise ValueError("provider input projection fields must be supplied together")
        async with self._write_transaction() as db:
            run = await (
                await db.execute("SELECT 1 FROM execution_runs WHERE run_id=?", (record.run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found",
                    "provider invocation requires a durable Run",
                )
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (record.run_id, record.invocation_id),
                )
            ).fetchone()
            if existing is not None:
                stored = self._row_to_provider_invocation(existing)
                immutable_fields = (
                    "run_id",
                    "invocation_id",
                    "schema_version",
                    "provider_id",
                    "model_id",
                    "adapter_id",
                    "policy_snapshot_json",
                    "request_hash",
                    "idempotency_group_id",
                    "attempt_ordinal",
                    "status",
                    "claim_owner",
                    "claim_epoch",
                    "revocation_epoch",
                    "dispatch_started_ack_ref",
                    "dispatch_started_ack_hash",
                    "dispatch_started_at",
                    "stream_epoch",
                    "outcome_ref",
                    "outcome_hash",
                )
                if any(getattr(stored, name) != getattr(record, name) for name in immutable_fields):
                    raise IdempotencyConflict(
                        "provider_invocation_conflict",
                        "invocation id already names a different dispatch",
                    )
                if input_payload_json is not None:
                    await self._record_provider_invocation_input_tx(
                        db,
                        record.run_id,
                        record.invocation_id,
                        payload_json=input_payload_json,
                        payload_hash=str(input_payload_hash),
                        created_at=float(input_created_at),
                    )
                return stored
            await db.execute(
                """INSERT INTO execution_provider_invocations(
                run_id,invocation_id,schema_version,provider_id,model_id,
                adapter_id,policy_snapshot_json,request_hash,
                idempotency_group_id,attempt_ordinal,status,claim_owner,
                claim_epoch,claimed_at,revocation_epoch,
                dispatch_started_ack_ref,dispatch_started_ack_hash,
                dispatch_started_at,stream_epoch,outcome_ref,outcome_hash,
                updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.run_id,
                    record.invocation_id,
                    record.schema_version,
                    record.provider_id,
                    record.model_id,
                    record.adapter_id,
                    record.policy_snapshot_json,
                    record.request_hash,
                    record.idempotency_group_id,
                    record.attempt_ordinal,
                    record.claim_owner,
                    record.claim_epoch,
                    record.claimed_at,
                    record.revocation_epoch,
                    record.dispatch_started_ack_ref,
                    record.dispatch_started_ack_hash,
                    record.dispatch_started_at,
                    record.stream_epoch,
                    record.outcome_ref,
                    record.outcome_hash,
                    record.updated_at,
                ),
            )
            if input_payload_json is not None:
                await self._record_provider_invocation_input_tx(
                    db,
                    record.run_id,
                    record.invocation_id,
                    payload_json=input_payload_json,
                    payload_hash=str(input_payload_hash),
                    created_at=float(input_created_at),
                )
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (record.run_id, record.invocation_id),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_provider_invocation(row)

    async def read_provider_invocation(
        self, run_id: str, invocation_id: str
    ) -> ProviderInvocationRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            return None if row is None else self._row_to_provider_invocation(row)

    async def read_provider_invocation_outcome(
        self, run_id: str, invocation_id: str
    ) -> ProviderInvocationOutcome | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocation_outcomes
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            return None if row is None else self._row_to_provider_outcome(row)

    @staticmethod
    def _validate_dispatch_ack(ref: str | None, digest: str | None, at: float | None) -> None:
        if (ref is None, digest is None, at is None) not in {
            (True, True, True),
            (False, False, False),
        }:
            raise ValueError("dispatch ack ref/hash/time must be supplied together")
        if digest is not None and (
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("dispatch ack hash must be lowercase SHA-256")

    async def complete_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome: ProviderInvocationOutcome,
        dispatch_started_ack_ref: str | None,
        dispatch_started_ack_hash: str | None,
        dispatch_started_at: float | None,
    ) -> ProviderInvocationRecord:
        if (outcome.run_id, outcome.invocation_id) != (run_id, invocation_id):
            raise IdempotencyConflict(
                "provider_outcome_scope_conflict",
                "provider outcome is bound to another invocation",
            )
        self._validate_dispatch_ack(
            dispatch_started_ack_ref,
            dispatch_started_ack_hash,
            dispatch_started_at,
        )
        outcome_ref = f"provider-outcome:{run_id}:{invocation_id}"
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_invocation_missing",
                    "provider outcome has no claimed invocation",
                )
            stored = self._row_to_provider_invocation(row)
            if stored.status is ProviderInvocationStatus.COMPLETED:
                existing_outcome = await (
                    await db.execute(
                        """SELECT * FROM execution_provider_invocation_outcomes
                        WHERE run_id=? AND invocation_id=?""",
                        (run_id, invocation_id),
                    )
                ).fetchone()
                if (
                    existing_outcome is None
                    or self._row_to_provider_outcome(existing_outcome) != outcome
                    or stored.outcome_ref != outcome_ref
                    or stored.outcome_hash != outcome.payload_hash
                ):
                    raise IdempotencyConflict(
                        "provider_outcome_conflict",
                        "completed invocation has a different outcome",
                    )
                return stored
            if (
                stored.status is not ProviderInvocationStatus.CLAIMED
                or stored.claim_epoch != expected_claim_epoch
            ):
                raise IdempotencyConflict(
                    "provider_invocation_not_claimed",
                    "provider invocation cannot complete from its current fence",
                )
            await db.execute(
                """INSERT INTO execution_provider_invocation_outcomes(
                run_id,invocation_id,schema_version,payload_json,payload_hash,
                created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    outcome.run_id,
                    outcome.invocation_id,
                    outcome.schema_version,
                    outcome.payload_json,
                    outcome.payload_hash,
                    outcome.created_at,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_provider_invocations
                SET status='completed',dispatch_started_ack_ref=?,
                    dispatch_started_ack_hash=?,dispatch_started_at=?,
                    outcome_ref=?,outcome_hash=?,updated_at=?
                WHERE run_id=? AND invocation_id=? AND status='claimed'
                  AND claim_epoch=?""",
                (
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                    outcome_ref,
                    outcome.payload_hash,
                    float(self._clock()),
                    run_id,
                    invocation_id,
                    expected_claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_invocation_cas_conflict",
                    "provider invocation completion lost its claim fence",
                )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_invocation(updated)

    async def _settle_noncompleted_provider_invocation(
        self,
        status: ProviderInvocationStatus,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None,
        outcome_hash: str | None,
        dispatch_started_ack_ref: str | None,
        dispatch_started_ack_hash: str | None,
        dispatch_started_at: float | None,
        audit_reason: str | None,
        audit_error_type: str | None,
        audit_error_message: str | None,
    ) -> ProviderInvocationRecord:
        if status not in {
            ProviderInvocationStatus.FAILED,
            ProviderInvocationStatus.UNKNOWN,
        }:
            raise ValueError("noncompleted provider status is invalid")
        self._validate_dispatch_ack(
            dispatch_started_ack_ref,
            dispatch_started_ack_hash,
            dispatch_started_at,
        )
        if (outcome_ref is None) != (outcome_hash is None):
            raise ValueError("provider audit outcome ref/hash must be paired")
        normalized_reason = str(audit_reason or status.value).strip()[:500]
        if not normalized_reason:
            normalized_reason = status.value
        normalized_error_type = (
            str(audit_error_type).strip()[:200] if audit_error_type is not None else None
        )
        normalized_error_message = (
            str(audit_error_message).replace("\x00", "").strip()[:1000]
            if audit_error_message is not None
            else None
        )
        if normalized_error_type == "":
            normalized_error_type = None
        if normalized_error_message == "":
            normalized_error_message = None
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "provider_invocation_missing",
                    "provider settlement has no claimed invocation",
                )
            stored = self._row_to_provider_invocation(row)
            if stored.status is status:
                expected = (
                    outcome_ref,
                    outcome_hash,
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                )
                actual = (
                    stored.outcome_ref,
                    stored.outcome_hash,
                    stored.dispatch_started_ack_ref,
                    stored.dispatch_started_ack_hash,
                    stored.dispatch_started_at,
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "provider_settlement_conflict",
                        "provider settlement replay differs",
                    )
                existing_audit = await (
                    await db.execute(
                        """SELECT terminal_status,reason,error_type,error_message
                        FROM execution_provider_invocation_audits
                        WHERE run_id=? AND invocation_id=?""",
                        (run_id, invocation_id),
                    )
                ).fetchone()
                expected_audit = (
                    status.value,
                    normalized_reason,
                    normalized_error_type,
                    normalized_error_message,
                )
                if existing_audit is None:
                    await db.execute(
                        """INSERT INTO execution_provider_invocation_audits(
                        run_id,invocation_id,schema_version,terminal_status,
                        reason,error_type,error_message,created_at
                        ) VALUES(?,?,1,?,?,?,?,?)""",
                        (
                            run_id,
                            invocation_id,
                            status.value,
                            normalized_reason,
                            normalized_error_type,
                            normalized_error_message,
                            float(self._clock()),
                        ),
                    )
                elif tuple(existing_audit) != expected_audit:
                    raise IdempotencyConflict(
                        "provider_audit_conflict",
                        "provider invocation audit replay differs",
                    )
                return stored
            if (
                stored.status is not ProviderInvocationStatus.CLAIMED
                or stored.claim_epoch != expected_claim_epoch
            ):
                raise IdempotencyConflict(
                    "provider_invocation_not_claimed",
                    "provider invocation cannot settle from its current fence",
                )
            cursor = await db.execute(
                """UPDATE execution_provider_invocations
                SET status=?,dispatch_started_ack_ref=?,
                    dispatch_started_ack_hash=?,dispatch_started_at=?,
                    outcome_ref=?,outcome_hash=?,updated_at=?
                WHERE run_id=? AND invocation_id=? AND status='claimed'
                  AND claim_epoch=?""",
                (
                    status.value,
                    dispatch_started_ack_ref,
                    dispatch_started_ack_hash,
                    dispatch_started_at,
                    outcome_ref,
                    outcome_hash,
                    float(self._clock()),
                    run_id,
                    invocation_id,
                    expected_claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "provider_invocation_cas_conflict",
                    "provider invocation settlement lost its claim fence",
                )
            await db.execute(
                """INSERT INTO execution_provider_invocation_audits(
                run_id,invocation_id,schema_version,terminal_status,
                reason,error_type,error_message,created_at
                ) VALUES(?,?,1,?,?,?,?,?)""",
                (
                    run_id,
                    invocation_id,
                    status.value,
                    normalized_reason,
                    normalized_error_type,
                    normalized_error_message,
                    float(self._clock()),
                ),
            )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_provider_invocations
                    WHERE run_id=? AND invocation_id=?""",
                    (run_id, invocation_id),
                )
            ).fetchone()
            assert updated is not None
            return self._row_to_provider_invocation(updated)

    async def fail_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        dispatch_started_ack_ref: str | None = None,
        dispatch_started_ack_hash: str | None = None,
        dispatch_started_at: float | None = None,
        audit_reason: str | None = None,
        audit_error_type: str | None = None,
        audit_error_message: str | None = None,
    ) -> ProviderInvocationRecord:
        return await self._settle_noncompleted_provider_invocation(
            ProviderInvocationStatus.FAILED,
            run_id,
            invocation_id,
            expected_claim_epoch=expected_claim_epoch,
            outcome_ref=outcome_ref,
            outcome_hash=outcome_hash,
            dispatch_started_ack_ref=dispatch_started_ack_ref,
            dispatch_started_ack_hash=dispatch_started_ack_hash,
            dispatch_started_at=dispatch_started_at,
            audit_reason=audit_reason,
            audit_error_type=audit_error_type,
            audit_error_message=audit_error_message,
        )

    async def mark_unknown_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        *,
        expected_claim_epoch: int,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        dispatch_started_ack_ref: str | None = None,
        dispatch_started_ack_hash: str | None = None,
        dispatch_started_at: float | None = None,
        audit_reason: str | None = None,
        audit_error_type: str | None = None,
        audit_error_message: str | None = None,
    ) -> ProviderInvocationRecord:
        return await self._settle_noncompleted_provider_invocation(
            ProviderInvocationStatus.UNKNOWN,
            run_id,
            invocation_id,
            expected_claim_epoch=expected_claim_epoch,
            outcome_ref=outcome_ref,
            outcome_hash=outcome_hash,
            dispatch_started_ack_ref=dispatch_started_ack_ref,
            dispatch_started_ack_hash=dispatch_started_ack_hash,
            dispatch_started_at=dispatch_started_at,
            audit_reason=audit_reason,
            audit_error_type=audit_error_type,
            audit_error_message=audit_error_message,
        )
