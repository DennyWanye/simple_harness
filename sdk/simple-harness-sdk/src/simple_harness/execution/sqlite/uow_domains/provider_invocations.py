# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Tool catalog snapshots, provider invocations, context-use grants and provider budgets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import JsonValue, RequestId, RunId, canonical_json
from simple_harness.execution.budget import BudgetCharge, BudgetPolicy, BudgetSnapshot
from simple_harness.execution.context_authority import (
    ProviderProjectionReceipt,
    ToolCatalogSnapshot,
    frozen_payload,
)
from simple_harness.execution.provider_invocations import (
    ProviderInvocationRecord,
    ProviderInvocationState,
)
from simple_harness.execution.recovery import (
    ReconciliationResolution,
    RecoveryKind,
    ResolutionOutcome,
    recovery_identity,
)
from simple_harness.execution.uow import (
    ExecutionLease,
    FaultHook,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
    WorkflowCheckpoint,
)
from simple_harness.providers import ProviderTarget, ProviderToolSpec
from simple_harness.workflow.lease import WorkflowLease

from ...context_use_requirements import require
from ...provider_accounting import effective_accounting, needs_accounting
from .. import context_use
from ..context_use import prepare, read_grant, require_handoff, verify_terminal, view
from ..provider_accounting import ProviderAccountingMixin
from .core import ExecutionUnitOfWorkCore
from .support import _fault, _reconciliation_resolution, _required, _thaw_json, _time


def _insert_provider_projection_receipt(
    connection: sqlite3.Connection, record: ProviderInvocationRecord
) -> None:
    session_row = connection.execute(
        "SELECT execution_session_id FROM runs WHERE run_id=?", (record.run_id.value,)
    ).fetchone()
    if session_row is None:
        raise UnitOfWorkNotFound(record.run_id.value)
    payload: JsonValue = {
        "invocation_id": record.invocation_id,
        "invocation_version": record.version,
        "run_id": record.run_id.value,
        "execution_session_id": str(session_row["execution_session_id"]),
        "request_id": record.request_id.value,
        "handoff_attempt": record.handoff_attempt,
        "state": record.state.value,
        "target": {
            "provider_id": record.target.provider_id,
            "model": record.target.model,
            "pricing_key": record.target.pricing_key,
            "endpoint_identity": record.target.endpoint_identity,
            "adapter_key": record.target.adapter_key,
        },
        "request_fingerprint": record.request_fingerprint,
        "usage": None if record.usage_json is None else _thaw_json(record.usage_json),
        "error_code": record.error_code,
        "settled_at": record.settled_at,
    }
    encoded = canonical_json(payload)
    payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    connection.execute(
        "INSERT INTO provider_projection_outbox("
        "invocation_id,invocation_version,run_id,execution_session_id,request_id,"
        "payload_json,payload_hash,created_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(invocation_id,invocation_version) DO NOTHING",
        (
            record.invocation_id,
            record.version,
            record.run_id.value,
            str(session_row["execution_session_id"]),
            record.request_id.value,
            encoded,
            payload_hash,
            record.settled_at if record.settled_at is not None else record.claimed_at,
        ),
    )


def _provider_projection_receipt(row: sqlite3.Row) -> ProviderProjectionReceipt:
    payload = json.loads(str(row["payload_json"]))
    return ProviderProjectionReceipt(
        int(row["sequence"]),
        str(row["invocation_id"]),
        int(row["invocation_version"]),
        str(row["run_id"]),
        str(row["execution_session_id"]),
        str(row["request_id"]),
        frozen_payload(payload),
        str(row["payload_hash"]),
        float(row["created_at"]),
    )


def _provider_invocation_record(row: sqlite3.Row) -> ProviderInvocationRecord:
    target = json.loads(str(row["target_json"]))
    usage = json.loads(str(row["usage_json"]))
    return ProviderInvocationRecord(
        invocation_id=str(row["invocation_id"]),
        run_id=RunId(str(row["run_id"])),
        request_id=RequestId(str(row["request_id"])),
        state=ProviderInvocationState(str(row["state"])),
        request_fingerprint=str(row["request_fingerprint"]),
        target=ProviderTarget(
            provider_id=str(target["provider_id"]),
            model=str(target["model"]),
            pricing_key=str(target["pricing_key"]),
            endpoint_identity=str(target["endpoint_identity"]),
            adapter_key=str(target["adapter_key"]),
        ),
        target_digest=str(row["target_digest"]),
        estimator_snapshot=(
            None if row["estimator_json"] is None else json.loads(str(row["estimator_json"]))
        ),
        estimator_digest=(
            None if row["estimator_digest"] is None else str(row["estimator_digest"])
        ),
        budget_charge=BudgetCharge.from_json(usage["budget"]),
        response_json=(
            None if row["response_json"] is None else json.loads(str(row["response_json"]))
        ),
        usage_json=usage,
        error_code=(None if row["error_code"] is None else str(row["error_code"])),
        claimed_at=float(row["claimed_at"]),
        handed_off_at=(None if row["handed_off_at"] is None else float(row["handed_off_at"])),
        settled_at=(None if row["settled_at"] is None else float(row["settled_at"])),
        version=int(row["version"]),
        request_json=(
            None if row["request_json"] is None else json.loads(str(row["request_json"]))
        ),
        handoff_attempt=int(row["handoff_attempt"]),
        rehandoff_count=int(row["rehandoff_count"]),
    )


class ProviderInvocationMixin(ExecutionUnitOfWorkCore, ProviderAccountingMixin):
    """Tool catalog snapshots, provider invocations, context-use grants and provider budgets."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _audit_operation_head(self, connection, kind, identity, now): ...
        @staticmethod
        def _require_workflow_handoff_authority(
            connection: sqlite3.Connection,
            *,
            run_id: str,
            execution_lease: ExecutionLease,
            workflow_lease: WorkflowLease | None,
            now: float,
        ) -> None: ...
        def read_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...

    def put_tool_catalog_snapshot(
        self,
        specs: Sequence[ProviderToolSpec],
        *,
        created_at: float | None = None,
        catalog_envelope: JsonValue | None = None,
    ) -> ToolCatalogSnapshot:
        ordered = tuple(specs)
        if len({spec.name for spec in ordered}) != len(ordered):
            raise ValueError("tool catalog contains duplicate names")
        payload: JsonValue = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": _thaw_json(spec.parameters),
            }
            for spec in ordered
        ]
        encoded = canonical_json(payload)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        envelope_encoded: str | None = None
        envelope_digest: str | None = None
        if catalog_envelope is not None:
            if (
                not isinstance(catalog_envelope, dict)
                or catalog_envelope.get("schema_version") != 6
            ):
                raise ValueError("catalog envelope must use schema_version 6")
            envelope_encoded = canonical_json(catalog_envelope)
            envelope_digest = hashlib.sha256(envelope_encoded.encode()).hexdigest()
        now = _time(time.time() if created_at is None else created_at)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO tool_catalog_snapshots(content_fingerprint,specs_json,created_at,"
                "provider_specs_fingerprint,catalog_envelope_json,catalog_envelope_digest_v6) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(content_fingerprint) DO NOTHING",
                (fingerprint, encoded, now, fingerprint, envelope_encoded, envelope_digest),
            )
        snapshot = self.read_tool_catalog_snapshot(content_fingerprint=fingerprint)
        assert snapshot is not None
        if envelope_digest is not None and snapshot.catalog_envelope_digest_v6 != envelope_digest:
            raise UnitOfWorkConflict(
                "Tool catalog Provider projection matches but v6 envelope differs"
            )
        return snapshot

    def read_tool_catalog_snapshot(
        self,
        generation: int | None = None,
        *,
        content_fingerprint: str | None = None,
    ) -> ToolCatalogSnapshot | None:
        if (generation is None) == (content_fingerprint is None):
            raise ValueError("provide exactly one catalog identity")
        row = self.database.connection.execute(
            "SELECT * FROM tool_catalog_snapshots WHERE "
            + ("generation=?" if generation is not None else "content_fingerprint=?"),
            (generation if generation is not None else content_fingerprint,),
        ).fetchone()
        if row is None:
            return None
        raw = json.loads(str(row["specs_json"]))
        if not isinstance(raw, list):
            raise RuntimeError("stored tool catalog is malformed")
        specs = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("parameters"), dict):
                raise RuntimeError("stored tool catalog spec is malformed")
            specs.append(
                ProviderToolSpec(str(item["name"]), str(item["description"]), item["parameters"])
            )
        return ToolCatalogSnapshot(
            int(row["generation"]),
            str(row["content_fingerprint"]),
            tuple(specs),
            float(row["created_at"]),
            None
            if row["catalog_envelope_json"] is None
            else frozen_payload(json.loads(str(row["catalog_envelope_json"]))),
            None
            if row["catalog_envelope_digest_v6"] is None
            else str(row["catalog_envelope_digest_v6"]),
        )

    def current_tool_catalog_generation(self) -> int:
        row = self.database.connection.execute(
            "SELECT COALESCE(MAX(generation), 0) FROM tool_catalog_snapshots"
        ).fetchone()
        return int(row[0])

    def list_provider_projection_receipts(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 256,
    ) -> tuple[ProviderProjectionReceipt, ...]:
        if after_sequence < 0 or not 1 <= limit <= 10_000:
            raise ValueError("invalid projection cursor or limit")
        rows = self.database.connection.execute(
            "SELECT * FROM provider_projection_outbox WHERE sequence>? ORDER BY sequence LIMIT ?",
            (after_sequence, limit),
        ).fetchall()
        return tuple(_provider_projection_receipt(row) for row in rows)

    def claim_provider_invocation(
        self,
        record: ProviderInvocationRecord,
        *,
        budget_policy: BudgetPolicy,
        execution_lease: ExecutionLease,
        context_use_grant=None,
    ) -> ProviderInvocationRecord:

        existing = self._provider_invocation_by_logical_call(record.run_id, record.request_id.value)
        if existing is not None:
            self._verify_existing_context_use(existing, context_use_grant)
            return existing
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=record.claimed_at)
            if execution_lease.run_id != record.run_id.value:
                raise UnitOfWorkConflict("provider invocation lease belongs to another Run")
            existing_row = connection.execute(
                "SELECT * FROM provider_invocations WHERE run_id = ? AND request_id = ?",
                (record.run_id.value, record.request_id.value),
            ).fetchone()
            if existing_row is not None:
                existing = _provider_invocation_record(existing_row)
                self._verify_existing_context_use(existing, context_use_grant)
                return existing

            require(connection, record.run_id.value, self._context_use_scope)
            if context_use_grant is None and self._context_use_scope is not None:
                raise UnitOfWorkConflict("context_use_grant_missing")
            if context_use_grant is not None:
                if (
                    context_use_grant.attempt.handoff_ordinal != 1
                    or context_use_grant.attempt.request_fingerprint != record.request_fingerprint
                ):
                    raise UnitOfWorkConflict("context_use_initial_claim_identity_differs")
                context_use.require_live(
                    self, connection, context_use_grant.attempt, execution_lease, record.claimed_at
                )
                context_use_grant.validate_handoff(record.claimed_at)
            else:
                stored_context = self.read_react_checkpoint(record.run_id.value)
                protected_checkpoint = stored_context is not None and (
                    cast(dict, _thaw_json(stored_context.checkpoint)).get("schema_version") == 7
                    or cast(dict, _thaw_json(stored_context.checkpoint)).get(
                        "context_use_authority_scope"
                    )
                    is not None
                )
                if (
                    protected_checkpoint
                    or context_use.read_attempt(connection, record.invocation_id, 1) is not None
                ):
                    raise UnitOfWorkConflict("context_use_grant_missing")
            budget_policy.authorize(
                self._provider_budget(connection, record.run_id),
                reservation_micros=record.budget_charge.amount_micros,
            )
            target_json = canonical_json(
                {
                    "provider_id": record.target.provider_id,
                    "model": record.target.model,
                    "pricing_key": record.target.pricing_key,
                    "endpoint_identity": record.target.endpoint_identity,
                    "adapter_key": record.target.adapter_key,
                }
            )
            estimator_json = (
                None
                if record.estimator_snapshot is None
                else canonical_json(_thaw_json(record.estimator_snapshot))
            )
            connection.execute(
                """
                INSERT INTO provider_invocations(
                    invocation_id, run_id, request_id, request_fingerprint,
                    request_json, target_json, target_digest, estimator_json, estimator_digest,
                    state, response_json, usage_json, error_code, claimed_at,
                    handed_off_at, settled_at, version, handoff_attempt, rehandoff_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'claimed', NULL, ?, NULL, ?, NULL, NULL, ?, 0, 0)
                """,  # noqa: E501
                (
                    record.invocation_id,
                    record.run_id.value,
                    record.request_id.value,
                    record.request_fingerprint,
                    (
                        None
                        if record.request_json is None
                        else canonical_json(_thaw_json(record.request_json))
                    ),
                    target_json,
                    record.target_digest,
                    estimator_json,
                    record.estimator_digest,
                    canonical_json(_thaw_json(record.usage_json)),
                    record.claimed_at,
                    record.version,
                ),
            )
            if context_use_grant is not None:
                context_use.bind(connection, record.invocation_id, context_use_grant)
            self._audit_operation_head(
                connection, "provider", record.invocation_id, record.claimed_at
            )
        stored = self.read_provider_invocation(record.invocation_id)
        assert stored is not None
        return stored

    def _verify_existing_context_use(self, record, grant):

        original = context_use.read_attempt(self.database.connection, record.invocation_id, 1)
        if original is None:
            if grant is not None:
                raise UnitOfWorkConflict("context_use_legacy_claim_missing_carrier")
        elif (
            grant is None
            or context_use.read_grant(
                self.database.connection, record.invocation_id, grant.attempt.handoff_ordinal
            )
            != grant
        ):
            raise UnitOfWorkConflict("context_use_claim_binding_differs")

    def prepare_provider_context_use(self, attempt, *, execution_lease, now, retry=False):

        return prepare(self, attempt, execution_lease, now, retry=retry)

    def read_provider_context_use_grant(self, invocation_id, handoff_ordinal):

        return read_grant(self.database.connection, invocation_id, handoff_ordinal)

    def read_provider_context_use(self, run_id, request_id):

        return view(self, run_id, request_id)

    def verify_provider_context_use_terminal(self, run_id, request_id, checkpoint, lease, now):

        return verify_terminal(self, run_id, request_id, checkpoint, lease, now)

    def read_provider_invocation(self, invocation_id: str) -> ProviderInvocationRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM provider_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        return None if row is None else _provider_invocation_record(row)

    def hand_off_provider_invocation(
        self,
        invocation_id: str,
        *,
        expected_version: int,
        handed_off_at: float,
        execution_lease: ExecutionLease,
        workflow_lease: WorkflowLease | None = None,
    ) -> ProviderInvocationRecord:
        handed_off_at = _time(handed_off_at, "handed_off_at")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=handed_off_at)
            self._require_workflow_handoff_authority(
                connection,
                run_id=execution_lease.run_id,
                execution_lease=execution_lease,
                workflow_lease=workflow_lease,
                now=handed_off_at,
            )
            invocation_run = connection.execute(
                "SELECT run_id FROM provider_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            if invocation_run is None or str(invocation_run["run_id"]) != execution_lease.run_id:
                raise UnitOfWorkConflict("provider handoff lease belongs to another Run")

            current = self.read_provider_invocation(invocation_id)
            require_handoff(self, connection, current, execution_lease, handed_off_at)
            changed = connection.execute(
                """
                UPDATE provider_invocations
                SET state = 'handed_off', handed_off_at = ?,
                    handoff_attempt = handoff_attempt + 1, version = version + 1
                WHERE invocation_id = ? AND state = 'claimed' AND version = ?
                """,
                (handed_off_at, invocation_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("provider invocation handoff CAS failed")
            assert current is not None
            self._insert_event(
                connection,
                event_id=f"provider-handoff:{invocation_id}:{current.handoff_attempt + 1}",
                run_id=execution_lease.run_id,
                kind="provider.handoff_authority.v1",
                payload={
                    "invocation_id": invocation_id,
                    "handoff_ordinal": current.handoff_attempt + 1,
                    "owner_id": execution_lease.owner_id,
                    "lease_epoch": execution_lease.epoch,
                },
                now=handed_off_at,
            )
            self._audit_operation_head(connection, "provider", invocation_id, handed_off_at)
        result = self.read_provider_invocation(invocation_id)
        assert result is not None
        return result

    def settle_provider_invocation(
        self,
        record: ProviderInvocationRecord,
        *,
        expected_version: int,
        fault: FaultHook | None = None,
        require_expired_runtime_lease: bool = False,
    ) -> ProviderInvocationRecord:
        if record.state not in {
            ProviderInvocationState.SUCCEEDED,
            ProviderInvocationState.FAILED,
            ProviderInvocationState.UNKNOWN,
        }:
            raise ValueError("provider settlement requires a terminal state")
        response_json = (
            None
            if record.response_json is None
            else canonical_json(_thaw_json(record.response_json))
        )
        usage_json = (
            None if record.usage_json is None else canonical_json(_thaw_json(record.usage_json))
        )
        with self.database.transaction() as connection:
            if require_expired_runtime_lease:
                if (
                    record.state is not ProviderInvocationState.UNKNOWN
                    or record.error_code != "recovered_after_handoff"
                ):
                    raise ValueError("lease-guarded recovery requires an abandoned handoff")
                assert record.settled_at is not None
                lease = connection.execute(
                    "SELECT owner_id,epoch,expires_at FROM workflow_leases"
                    " WHERE run_id=? AND namespace='runtime.kernel'",
                    (record.run_id.value,),
                ).fetchone()
                fact = connection.execute(
                    "SELECT payload_json FROM run_events WHERE run_id=? AND event_id=?"
                    " AND kind='provider.handoff_authority.v1'",
                    (
                        record.run_id.value,
                        f"provider-handoff:{record.invocation_id}:{record.handoff_attempt}",
                    ),
                ).fetchone()
                authority = None if fact is None else json.loads(fact[0])
                same_handoff_owner = authority is None or (
                    lease is not None
                    and authority["owner_id"] == lease["owner_id"]
                    and authority["lease_epoch"] == lease["epoch"]
                )
                if (
                    lease is not None
                    and float(lease["expires_at"]) > record.settled_at
                    and same_handoff_owner
                ):
                    # The lease read and refusal share the settlement transaction:
                    # a concurrent renewal cannot race a read-then-UNKNOWN write.
                    # Legacy handoffs without an authority fact conservatively
                    # remain held while any runtime lease is live.
                    current = connection.execute(
                        "SELECT * FROM provider_invocations WHERE invocation_id=?",
                        (record.invocation_id,),
                    ).fetchone()
                    if current is None:
                        raise UnitOfWorkNotFound(record.invocation_id)
                    return _provider_invocation_record(current)
            changed = connection.execute(
                """
                UPDATE provider_invocations
                SET state = ?, response_json = ?, usage_json = ?, error_code = ?,
                    settled_at = ?, version = version + 1
                WHERE invocation_id = ? AND state = 'handed_off' AND version = ?
                  AND request_fingerprint = ? AND target_digest = ?
                  AND COALESCE(estimator_digest, '') = COALESCE(?, '')
                """,
                (
                    record.state.value,
                    response_json,
                    usage_json,
                    record.error_code,
                    record.settled_at,
                    record.invocation_id,
                    expected_version,
                    record.request_fingerprint,
                    record.target_digest,
                    record.estimator_digest,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("provider invocation settlement CAS failed")
            _fault(fault, "provider_settlement.ledger.after_write")
            _insert_provider_projection_receipt(connection, record)
            _fault(fault, "provider_settlement.outbox.after_write")
            self._audit_operation_head(
                connection, "provider", record.invocation_id, record.settled_at
            )
        result = self.read_provider_invocation(record.invocation_id)
        assert result is not None
        return result

    def record_provider_reconciliation(
        self,
        record: ProviderInvocationRecord,
        *,
        outcome: ResolutionOutcome,
        response_json: object | None,
        usage_json: object | None,
        budget_charge: BudgetCharge,
        evidence_ref: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> ProviderInvocationRecord:
        outcome = ResolutionOutcome(outcome)
        evidence_ref = _required(evidence_ref, "evidence_ref")
        now = _time(now)
        if record.state is not ProviderInvocationState.UNKNOWN:
            raise UnitOfWorkConflict("Provider reconciliation requires unknown ledger")
        if record.handoff_attempt < 1:
            raise UnitOfWorkConflict("Provider reconciliation requires handoff attempt")
        if outcome is ResolutionOutcome.COMPLETED:
            if response_json is None or usage_json is None:
                raise ValueError("completed Provider resolution requires response and usage")
            response_payload = json.loads(canonical_json(response_json))  # type: ignore[arg-type]
            usage_payload = json.loads(canonical_json(usage_json))  # type: ignore[arg-type]
            if not isinstance(response_payload, dict) or not isinstance(usage_payload, dict):
                raise TypeError("Provider resolution payloads must be objects")
            if response_payload.get("request_id") != record.request_id.value:
                raise UnitOfWorkConflict("Provider resolution request identity mismatch")
            usage_payload = dict(usage_payload)
            usage_payload["budget"] = budget_charge.to_json()
        else:
            if response_json is not None or usage_json is not None:
                raise ValueError("not-started Provider resolution cannot carry response")
            response_payload = None
            usage_payload = None
        resolution_payload: dict[str, JsonValue] = {
            "outcome": outcome.value,
            "evidence_ref": evidence_ref,
            "request_fingerprint": record.request_fingerprint,
            "target_digest": record.target_digest,
            "response": response_payload,
            "usage": usage_payload,
        }
        outcome_hash = hashlib.sha256(canonical_json(resolution_payload).encode()).hexdigest()
        resolution_id = f"resolution:{recovery_identity(RecoveryKind.PROVIDER, record.invocation_id, record.handoff_attempt)}"  # noqa: E501
        with self.database.transaction() as connection:
            current_row = connection.execute(
                "SELECT * FROM provider_invocations WHERE invocation_id=?",
                (record.invocation_id,),
            ).fetchone()
            if current_row is None:
                raise UnitOfWorkNotFound(record.invocation_id)
            current = _provider_invocation_record(current_row)
            if (
                current.run_id != record.run_id
                or current.request_id != record.request_id
                or current.request_fingerprint != record.request_fingerprint
                or current.target_digest != record.target_digest
                or current.handoff_attempt != record.handoff_attempt
            ):
                raise UnitOfWorkConflict("Provider reconciliation identity conflict")
            existing = connection.execute(
                """
                SELECT * FROM reconciliation_resolutions
                WHERE kind='provider' AND ledger_identity=? AND handoff_attempt=?
                """,
                (record.invocation_id, record.handoff_attempt),
            ).fetchone()
            if existing is not None:
                stored = _reconciliation_resolution(existing)
                if stored.outcome_hash != outcome_hash:
                    raise UnitOfWorkConflict("Provider resolution outcome conflict")
                return current
            if (
                current.state is not ProviderInvocationState.UNKNOWN
                or current.version != record.version
            ):
                raise UnitOfWorkConflict("Provider reconciliation CAS failed")
            _fault(fault, "provider_reconciliation.resolution.before_write")
            connection.execute(
                """
                INSERT INTO reconciliation_resolutions(
                    resolution_id,kind,ledger_identity,handoff_attempt,outcome,
                    outcome_hash,evidence_ref,payload_json,created_at
                ) VALUES (?, 'provider', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolution_id,
                    record.invocation_id,
                    record.handoff_attempt,
                    outcome.value,
                    outcome_hash,
                    evidence_ref,
                    canonical_json(resolution_payload),
                    now,
                ),
            )
            _fault(fault, "provider_reconciliation.resolution.after_write")
            if outcome is ResolutionOutcome.COMPLETED:
                changed = connection.execute(
                    """
                    UPDATE provider_invocations
                    SET state='succeeded', response_json=?, usage_json=?,
                        error_code=NULL, settled_at=?, version=version+1
                    WHERE invocation_id=? AND state='unknown' AND version=?
                      AND request_fingerprint=? AND target_digest=?
                      AND handoff_attempt=?
                    """,
                    (
                        canonical_json(response_payload),
                        canonical_json(usage_payload),
                        now,
                        record.invocation_id,
                        record.version,
                        record.request_fingerprint,
                        record.target_digest,
                        record.handoff_attempt,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("Provider completed recovery CAS failed")
                projected_row = connection.execute(
                    "SELECT * FROM provider_invocations WHERE invocation_id=?",
                    (record.invocation_id,),
                ).fetchone()
                assert projected_row is not None
                _insert_provider_projection_receipt(
                    connection, _provider_invocation_record(projected_row)
                )
                _fault(fault, "provider_reconciliation.ledger.after_write")
                self._audit_operation_head(connection, "provider", record.invocation_id, now)
            connection.execute(
                """
                UPDATE run_wait_blockers SET resolution_id=?, resolved_at=?,
                    version=version+1
                WHERE kind='provider' AND ledger_identity=? AND handoff_attempt=?
                  AND resolution_id IS NULL
                """,
                (resolution_id, now, record.invocation_id, record.handoff_attempt),
            )
            _fault(fault, "provider_reconciliation.blocker.after_write")
        _fault(fault, "provider_reconciliation.after_commit")
        result = self.read_provider_invocation(record.invocation_id)
        assert result is not None
        return result

    def reauthorize_provider_not_started(
        self,
        record: ProviderInvocationRecord,
        *,
        resolution: ReconciliationResolution,
        execution_lease: ExecutionLease,
        now: float,
        context_use_grant=None,
    ) -> ProviderInvocationRecord:
        now = _time(now)
        if (
            resolution.kind is not RecoveryKind.PROVIDER
            or resolution.ledger_identity != record.invocation_id
            or resolution.handoff_attempt != record.handoff_attempt
            or resolution.outcome is not ResolutionOutcome.CONFIRMED_NOT_STARTED
        ):
            raise UnitOfWorkConflict("Provider reauthorization resolution mismatch")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            if execution_lease.run_id != record.run_id.value:
                raise UnitOfWorkConflict("Provider retry lease belongs to another Run")

            protected = context_use.read_attempt(connection, record.invocation_id, 1)
            if protected is not None:
                if (
                    context_use_grant is None
                    or context_use_grant.attempt.handoff_ordinal != record.handoff_attempt + 1
                ):
                    raise UnitOfWorkConflict("context_use_retry_requires_new_grant")
                context_use.require_live(
                    self, connection, context_use_grant.attempt, execution_lease, now, retry=True
                )
                context_use_grant.validate_handoff(now)
            elif context_use_grant is not None:
                raise UnitOfWorkConflict("context_use_legacy_retry_missing_carrier")
            row = connection.execute(
                "SELECT outcome_hash,evidence_ref FROM reconciliation_resolutions WHERE"
                " resolution_id=?",
                (resolution.resolution_id,),
            ).fetchone()
            if (
                row is None
                or str(row["outcome_hash"]) != resolution.outcome_hash
                or str(row["evidence_ref"]) != resolution.evidence_ref
            ):
                raise UnitOfWorkConflict("Provider reauthorization evidence mismatch")
            changed = connection.execute(
                """
                UPDATE provider_invocations SET state='claimed', error_code=NULL,
                    handed_off_at=NULL, settled_at=NULL, rehandoff_count=rehandoff_count+1,
                    version=version+1
                WHERE invocation_id=? AND state='unknown' AND version=?
                  AND request_fingerprint=? AND target_digest=?
                  AND handoff_attempt=? AND rehandoff_count=0
                """,
                (
                    record.invocation_id,
                    record.version,
                    record.request_fingerprint,
                    record.target_digest,
                    record.handoff_attempt,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("Provider reauthorization CAS failed")
            if context_use_grant is not None:
                context_use.bind(connection, record.invocation_id, context_use_grant)
            self._audit_operation_head(connection, "provider", record.invocation_id, now)
        refreshed = self.read_provider_invocation(record.invocation_id)
        assert refreshed is not None
        return refreshed

    def list_incomplete_provider_invocations(
        self,
    ) -> tuple[ProviderInvocationRecord, ...]:
        rows = self.database.connection.execute(
            """
            SELECT * FROM provider_invocations
            WHERE state IN ('claimed', 'handed_off', 'unknown')
            ORDER BY claimed_at, invocation_id
            """
        ).fetchall()
        return tuple(_provider_invocation_record(row) for row in rows)

    def read_provider_budget(self, run_id: RunId) -> BudgetSnapshot:
        return self._provider_budget(self.database.connection, run_id)

    def list_provider_invocations(self, run_id: RunId) -> tuple[ProviderInvocationRecord, ...]:
        """Every provider invocation of one Run, oldest first (read-only cost facts).

        Added for the orchestration layer (ORCH-BUILD §12.2: the SDK invocation
        ledger is the fact layer for actual model cost); it exposes nothing that
        ``read_provider_invocation`` does not already return per row.
        """

        rows = self.database.connection.execute(
            "SELECT * FROM provider_invocations WHERE run_id = ?"
            " ORDER BY claimed_at, invocation_id",
            (run_id.value,),
        ).fetchall()
        return tuple(_provider_invocation_record(row) for row in rows)

    def list_provider_invocation_liveness(
        self, run_id: RunId
    ) -> tuple[tuple[str, float | None, float | None], ...]:
        """``(state, handed_off_at, settled_at)`` of every provider invocation of one Run.

        The orchestration layer's stall question ("is a call still on the wire; when did the
        last one settle") needs these three columns only; decoding whole records for it
        (V32, 2026-10-11) was measurable work on every liveness tick.
        """

        rows = self.database.connection.execute(
            "SELECT state, handed_off_at, settled_at FROM provider_invocations WHERE run_id = ?"
            " ORDER BY claimed_at, invocation_id",
            (run_id.value,),
        ).fetchall()
        return tuple(
            (
                str(row["state"]),
                None if row["handed_off_at"] is None else float(row["handed_off_at"]),
                None if row["settled_at"] is None else float(row["settled_at"]),
            )
            for row in rows
        )

    def _provider_budget(self, connection: sqlite3.Connection, run_id: RunId) -> BudgetSnapshot:
        """一个 Run 的预算快照。

        每次模型调用前后都要算，而长期会话的 Agent 调用记录只增不减；逐行解码全部记录会让
        每句话的开销随历史线性涨。所以按行缓存费用：行一改 version 必加一；唯一不经 version
        的改动是晚到的记账回执，它只落在"还需要记账、回执还没来"的行上，这样的行不进缓存、
        下次照常重算。缓存只在本进程本对象里，读到的 version 不同就作废。
        """
        committed = 0
        reserved = 0
        unknown = False
        rows = connection.execute(
            "SELECT invocation_id, version FROM provider_invocations WHERE run_id = ?",
            (run_id.value,),
        ).fetchall()
        cache = self._budget_cache.setdefault(run_id.value, {})
        for row in rows:
            invocation_id = str(row["invocation_id"])
            version = int(row["version"])
            cached = cache.get(invocation_id)
            if cached is not None and cached[0] == version:
                _, amount, in_flight = cached
            else:
                original = self.read_provider_invocation(invocation_id)
                assert original is not None
                receipt = self.read_provider_accounting_receipt(invocation_id)
                record = original if receipt is None else effective_accounting(original, receipt)
                amount = record.budget_charge.amount_micros
                in_flight = record.state in {
                    ProviderInvocationState.CLAIMED,
                    ProviderInvocationState.HANDED_OFF,
                }
                if receipt is not None or not needs_accounting(record):
                    cache[invocation_id] = (version, amount, in_flight)
            if amount is None:
                unknown = True
            elif in_flight:
                reserved += amount
            else:
                committed += amount
        return BudgetSnapshot(committed, reserved, unknown)

    def _provider_invocation_by_logical_call(
        self, run_id: RunId, request_id: str
    ) -> ProviderInvocationRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM provider_invocations WHERE run_id = ? AND request_id = ?",
            (run_id.value, request_id),
        ).fetchone()
        return None if row is None else _provider_invocation_record(row)
