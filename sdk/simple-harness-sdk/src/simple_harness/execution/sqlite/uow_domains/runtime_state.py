# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Runtime activation leases, run fences, waits, cancellation requests and ReAct checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Mapping

from simple_harness.contracts import JsonValue, RunId, canonical_json
from simple_harness.execution.effects import EffectState
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.provider_invocations import ProviderInvocationState
from simple_harness.execution.recovery import (
    ReconciliationResolution,
    RecoveryKind,
    ResolutionOutcome,
    WaitActivationReceipt,
    WaitBlockerRecord,
    WaitBlockerSpec,
)
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ExecutionLease,
    FaultHook,
    LegacyTurnCursorRecord,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
    WorkflowCheckpoint,
)

from .core import ExecutionUnitOfWorkCore
from .support import (
    _fault,
    _object_json,
    _reconciliation_resolution,
    _required,
    _run_record,
    _time,
    _wait_blocker_record,
    _workflow_checkpoint,
)


def _wait_activation_receipt(row: sqlite3.Row) -> WaitActivationReceipt:
    return WaitActivationReceipt(
        receipt_id=str(row["receipt_id"]),
        blocker_id=str(row["blocker_id"]),
        run_id=str(row["run_id"]),
        owner_id=str(row["owner_id"]),
        runtime_lease_epoch=int(row["runtime_lease_epoch"]),
        outcome_hash=str(row["outcome_hash"]),
    )


class RuntimeStateMixin(ExecutionUnitOfWorkCore):
    """Runtime activation leases, run fences, waits, cancellation requests and ReAct checkpoints."""

    __slots__ = ()

    def claim_runtime_activation(
        self,
        *,
        run_id: str,
        owner_id: str,
        namespace: str,
        now: float,
        lease_ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> tuple[RunRecord, ExecutionLease]:
        """Atomically acquire the durable owner lease and activate one Run."""

        run_id = _required(run_id, "run_id")
        owner_id = _required(owner_id, "owner_id")
        namespace = _required(namespace, "namespace")
        now = _time(now)
        if (
            not isinstance(lease_ttl_seconds, (int, float))
            or isinstance(lease_ttl_seconds, bool)
            or not math.isfinite(float(lease_ttl_seconds))
            or lease_ttl_seconds <= 0
        ):
            raise ValueError("lease_ttl_seconds must be finite and positive")
        expires_at = now + float(lease_ttl_seconds)
        with self.database.transaction() as connection:
            run_row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise UnitOfWorkNotFound(run_id)
            run = _run_record(run_row)
            if run.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                raise UnitOfWorkConflict("terminal Run cannot be activated")
            workflow_namespace: str | None = None
            if run.driver_kind == "workflow":
                admission = connection.execute(
                    "SELECT request_json FROM workflow_start_admissions WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if admission is not None:
                    workflow_namespace = str(
                        json.loads(str(admission["request_json"]))["checkpoint_namespace"]
                    )
            lease_row = connection.execute(
                "SELECT owner_id, epoch, expires_at FROM workflow_leases "
                "WHERE run_id = ? AND namespace = ?",
                (run_id, namespace),
            ).fetchone()
            if lease_row is not None and float(lease_row["expires_at"]) > now:
                if str(lease_row["owner_id"]) != owner_id:
                    raise UnitOfWorkConflict("Run already has an active runtime owner")
                if workflow_namespace is not None:
                    projection = connection.execute(
                        "SELECT owner_id,epoch,expires_at FROM workflow_leases "
                        "WHERE run_id=? AND namespace=?",
                        (run_id, workflow_namespace),
                    ).fetchone()
                    if projection is None or tuple(projection) != (
                        owner_id,
                        int(lease_row["epoch"]),
                        float(lease_row["expires_at"]),
                    ):
                        raise UnitOfWorkConflict(
                            "active workflow Runtime lacks its lease projection"
                        )
                lease = ExecutionLease(
                    run_id,
                    namespace,
                    owner_id,
                    int(lease_row["epoch"]),
                    float(lease_row["expires_at"]),
                )
                return run, lease
            fence_row = connection.execute(
                "SELECT runtime_lease_epoch FROM run_fences WHERE run_id=?",
                (run_id,),
            ).fetchone()
            prior_epoch = max(
                0 if lease_row is None else int(lease_row["epoch"]),
                0 if fence_row is None else int(fence_row["runtime_lease_epoch"]),
            )
            epoch = prior_epoch + 1
            _fault(fault, "runtime_activation.lease.before_write")
            connection.execute(
                """
                INSERT INTO workflow_leases(run_id, namespace, owner_id, epoch, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, namespace) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    epoch = excluded.epoch,
                    expires_at = excluded.expires_at
                """,
                (run_id, namespace, owner_id, epoch, expires_at),
            )
            _fault(fault, "runtime_activation.lease.after_write")
            if workflow_namespace is not None:
                _fault(fault, "runtime_activation.workflow_lease.before_write")
                connection.execute(
                    """
                    INSERT INTO workflow_leases(
                        run_id, namespace, owner_id, epoch, expires_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, namespace) DO UPDATE SET
                        owner_id=excluded.owner_id,
                        epoch=excluded.epoch,
                        expires_at=excluded.expires_at
                    """,
                    (run_id, workflow_namespace, owner_id, epoch, expires_at),
                )
                _fault(fault, "runtime_activation.workflow_lease.after_write")
            event_kind = "run.recovered" if run.state is RunState.RUNNING else "run.activated"
            _fault(fault, "runtime_activation.event.before_write")
            self._insert_event(
                connection,
                event_id=f"{run_id}:runtime:{namespace}:{epoch}:activated",
                run_id=run_id,
                kind=event_kind,
                payload={"owner_id": owner_id, "lease_epoch": epoch},
                now=now,
            )
            _fault(fault, "runtime_activation.event.after_write")
            if run.state in {RunState.CREATED, RunState.QUEUED}:
                _fault(fault, "runtime_activation.run.before_write")
                changed = connection.execute(
                    """
                    UPDATE runs SET state = 'running', version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ? AND state IN ('created', 'queued')
                    """,
                    (now, run_id, run.version),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("runtime activation CAS failed")
                _fault(fault, "runtime_activation.run.after_write")
        _fault(fault, "runtime_activation.after_commit")
        activated = self.read_run(run_id)
        assert activated is not None
        return activated, ExecutionLease(run_id, namespace, owner_id, epoch, expires_at)

    def release_runtime_lease(
        self,
        lease: ExecutionLease,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> None:
        now = _time(now)
        with self.database.transaction() as connection:
            dispatch = connection.execute(
                "SELECT owner_id,runtime_lease_epoch,expires_at "
                "FROM runtime_start_dispatch_claims "
                "WHERE run_id=? AND state='claimed'",
                (lease.run_id,),
            ).fetchone()
            if dispatch is not None and tuple(dispatch) != (
                lease.owner_id,
                lease.epoch,
                lease.expires_at,
            ):
                raise UnitOfWorkConflict(
                    "runtime start dispatch record is not co-fenced with runtime"
                )
            projection_rows = connection.execute(
                """
                SELECT namespace, owner_id, epoch, expires_at
                FROM workflow_leases
                WHERE run_id = ? AND namespace != ?
                ORDER BY namespace
                """,
                (lease.run_id, lease.namespace),
            ).fetchall()
            owned_projection_rows: list[sqlite3.Row] = []
            for projection in projection_rows:
                same_authority = (
                    str(projection["owner_id"]) == lease.owner_id
                    and int(projection["epoch"]) == lease.epoch
                )
                if same_authority and float(projection["expires_at"]) != lease.expires_at:
                    raise UnitOfWorkConflict(
                        "workflow projection lease is not co-fenced with runtime"
                    )
                if not same_authority and float(projection["expires_at"]) > now:
                    raise UnitOfWorkConflict(
                        "workflow projection lease is not co-fenced with runtime"
                    )
                if same_authority:
                    owned_projection_rows.append(projection)
            _fault(fault, "runtime_lease_release.before_write")
            changed = connection.execute(
                """
                UPDATE workflow_leases SET expires_at = ?
                WHERE run_id = ? AND namespace = ? AND owner_id = ? AND epoch = ?
                """,
                (now, lease.run_id, lease.namespace, lease.owner_id, lease.epoch),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("runtime lease release CAS failed")
            _fault(fault, "runtime_lease_release.after_write")
            if dispatch is not None:
                _fault(fault, "runtime_lease_release.dispatch.before_write")
                changed = connection.execute(
                    "UPDATE runtime_start_dispatch_claims SET"
                    " expires_at=?,version=version+1,updated_at=? WHERE run_id=? AND owner_id=? AND"
                    " runtime_lease_epoch=? AND expires_at=?",
                    (
                        now,
                        now,
                        lease.run_id,
                        lease.owner_id,
                        lease.epoch,
                        lease.expires_at,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("runtime start dispatch release CAS failed")
                _fault(fault, "runtime_lease_release.dispatch.after_write")
            for projection in owned_projection_rows:
                namespace = str(projection["namespace"])
                _fault(
                    fault,
                    f"runtime_lease_release.{namespace}.before_projection_write",
                )
                changed = connection.execute(
                    """
                    UPDATE workflow_leases SET expires_at = ?
                    WHERE run_id = ? AND namespace = ? AND owner_id = ?
                      AND epoch = ? AND expires_at = ?
                    """,
                    (
                        now,
                        lease.run_id,
                        namespace,
                        lease.owner_id,
                        lease.epoch,
                        lease.expires_at,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("workflow projection lease release CAS failed")
                _fault(
                    fault,
                    f"runtime_lease_release.{namespace}.after_projection_write",
                )
        _fault(fault, "runtime_lease_release.after_commit")

    def renew_runtime_lease(
        self,
        lease: ExecutionLease,
        *,
        now: float,
        lease_ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> ExecutionLease:
        now = _time(now)
        if (
            not isinstance(lease_ttl_seconds, (int, float))
            or isinstance(lease_ttl_seconds, bool)
            or not math.isfinite(float(lease_ttl_seconds))
            or lease_ttl_seconds <= 0
        ):
            raise ValueError("lease_ttl_seconds must be finite and positive")
        expires_at = now + float(lease_ttl_seconds)
        with self.database.transaction() as connection:
            dispatch = connection.execute(
                "SELECT owner_id,runtime_lease_epoch,expires_at "
                "FROM runtime_start_dispatch_claims "
                "WHERE run_id=? AND state='claimed'",
                (lease.run_id,),
            ).fetchone()
            if dispatch is not None and tuple(dispatch) != (
                lease.owner_id,
                lease.epoch,
                lease.expires_at,
            ):
                raise UnitOfWorkConflict(
                    "runtime start dispatch record is not co-fenced with runtime"
                )
            projection_rows = connection.execute(
                """
                SELECT namespace, owner_id, epoch, expires_at
                FROM workflow_leases
                WHERE run_id = ? AND namespace != ?
                ORDER BY namespace
                """,
                (lease.run_id, lease.namespace),
            ).fetchall()
            owned_projection_rows: list[sqlite3.Row] = []
            for projection in projection_rows:
                same_authority = (
                    str(projection["owner_id"]) == lease.owner_id
                    and int(projection["epoch"]) == lease.epoch
                )
                if same_authority and float(projection["expires_at"]) != lease.expires_at:
                    raise UnitOfWorkConflict(
                        "workflow projection lease is not co-fenced with runtime"
                    )
                if not same_authority and float(projection["expires_at"]) > now:
                    raise UnitOfWorkConflict(
                        "workflow projection lease is not co-fenced with runtime"
                    )
                if same_authority:
                    owned_projection_rows.append(projection)
            _fault(fault, "runtime_lease_renew.before_write")
            changed = connection.execute(
                """
                UPDATE workflow_leases SET expires_at = ?
                WHERE run_id = ? AND namespace = ? AND owner_id = ? AND epoch = ?
                  AND expires_at > ?
                """,
                (
                    expires_at,
                    lease.run_id,
                    lease.namespace,
                    lease.owner_id,
                    lease.epoch,
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("runtime lease renew CAS failed")
            _fault(fault, "runtime_lease_renew.after_write")
            if dispatch is not None:
                _fault(fault, "runtime_lease_renew.dispatch.before_write")
                changed = connection.execute(
                    "UPDATE runtime_start_dispatch_claims SET"
                    " expires_at=?,version=version+1,updated_at=? WHERE run_id=? AND owner_id=? AND"
                    " runtime_lease_epoch=? AND expires_at=?",
                    (
                        expires_at,
                        now,
                        lease.run_id,
                        lease.owner_id,
                        lease.epoch,
                        lease.expires_at,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("runtime start dispatch renew CAS failed")
                _fault(fault, "runtime_lease_renew.dispatch.after_write")
            for projection in owned_projection_rows:
                namespace = str(projection["namespace"])
                _fault(
                    fault,
                    f"runtime_lease_renew.{namespace}.before_projection_write",
                )
                changed = connection.execute(
                    """
                    UPDATE workflow_leases SET expires_at = ?
                    WHERE run_id = ? AND namespace = ? AND owner_id = ?
                      AND epoch = ? AND expires_at = ? AND expires_at > ?
                    """,
                    (
                        expires_at,
                        lease.run_id,
                        namespace,
                        lease.owner_id,
                        lease.epoch,
                        lease.expires_at,
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise UnitOfWorkConflict("workflow projection lease renew CAS failed")
                _fault(
                    fault,
                    f"runtime_lease_renew.{namespace}.after_projection_write",
                )
        _fault(fault, "runtime_lease_renew.after_commit")
        return ExecutionLease(
            lease.run_id,
            lease.namespace,
            lease.owner_id,
            lease.epoch,
            expires_at,
        )

    def commit_runtime_state(
        self,
        *,
        run_id: str,
        expected_version: int,
        state: RunState,
        event_id: str,
        payload: Mapping[str, JsonValue],
        lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> RunRecord:
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        state = RunState(state)
        if state not in {RunState.RUNNING, RunState.WAITING}:
            raise ValueError("runtime state command only supports running or waiting")
        now = _time(now)
        payload_json = _object_json(payload, "payload")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, lease, now=now)
            _fault(fault, "runtime_state.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind=f"run.{state.value}",
                payload=json.loads(payload_json),
                now=now,
            )
            _fault(fault, "runtime_state.event.after_write")
            _fault(fault, "runtime_state.run.before_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ?
                  AND state NOT IN ('completed', 'failed', 'cancelled')
                """,
                (state.value, now, run_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("runtime state CAS failed")
            _fault(fault, "runtime_state.run.after_write")
        _fault(fault, "runtime_state.after_commit")
        record = self.read_run(run_id)
        assert record is not None
        return record

    def commit_runtime_wait_with_blocker(
        self,
        *,
        run_id: str,
        expected_version: int,
        event_id: str,
        payload: Mapping[str, JsonValue],
        blocker: WaitBlockerSpec,
        lease: ExecutionLease,
        now: float,
        fault: FaultHook | None = None,
    ) -> tuple[RunRecord, WaitBlockerRecord]:
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        if lease.run_id != run_id:
            raise UnitOfWorkConflict("wait blocker lease belongs to another Run")
        payload_json = _object_json(payload, "payload")
        blocker_id = blocker.blocker_id
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, lease, now=now)
            existing = connection.execute(
                "SELECT * FROM run_wait_blockers WHERE blocker_id = ?",
                (blocker_id,),
            ).fetchone()
            if existing is not None:
                stored = _wait_blocker_record(existing)
                if (
                    stored.run_id != run_id
                    or stored.kind is not blocker.kind
                    or stored.ledger_identity != blocker.ledger_identity
                    or stored.handoff_attempt != blocker.handoff_attempt
                    or stored.observed_version != blocker.observed_version
                ):
                    raise UnitOfWorkConflict("wait blocker identity conflict")
                current = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                assert current is not None
                return _run_record(current), stored
            resolution = connection.execute(
                """
                SELECT resolution_id, outcome FROM reconciliation_resolutions
                WHERE kind = ? AND ledger_identity = ? AND handoff_attempt = ?
                """,
                (blocker.kind.value, blocker.ledger_identity, blocker.handoff_attempt),
            ).fetchone()
            completed_states: tuple[str, ...]
            if blocker.kind is RecoveryKind.PROVIDER:
                ledger = connection.execute(
                    """
                    SELECT run_id,state,version,handoff_attempt
                    FROM provider_invocations WHERE invocation_id=?
                    """,
                    (blocker.ledger_identity,),
                ).fetchone()
                unresolved_state = ProviderInvocationState.UNKNOWN.value
                completed_states = (ProviderInvocationState.SUCCEEDED.value,)
            else:
                ledger = connection.execute(
                    """
                    SELECT run_id,state,version,handoff_attempt
                    FROM execution_effects WHERE effect_id=?
                    """,
                    (blocker.ledger_identity,),
                ).fetchone()
                unresolved_state = EffectState.UNKNOWN.value
                completed_states = (
                    EffectState.SUCCEEDED.value,
                    EffectState.FAILED.value,
                    EffectState.REJECTED.value,
                )
            if (
                ledger is None
                or str(ledger["run_id"]) != run_id
                or int(ledger["handoff_attempt"]) != blocker.handoff_attempt
            ):
                raise UnitOfWorkConflict("wait blocker ledger identity conflict")
            resolution_outcome = None if resolution is None else str(resolution["outcome"])
            if resolution_outcome == ResolutionOutcome.COMPLETED.value:
                if (
                    str(ledger["state"]) not in completed_states
                    or int(ledger["version"]) != blocker.observed_version + 1
                ):
                    raise UnitOfWorkConflict("completed wait resolution and ledger differ")
            elif (
                str(ledger["state"]) != unresolved_state
                or int(ledger["version"]) != blocker.observed_version
            ):
                raise UnitOfWorkConflict("wait blocker observed ledger version is stale")
            _fault(fault, "wait_blocker.event.before_write")
            self._insert_event(
                connection,
                event_id=event_id,
                run_id=run_id,
                kind="run.waiting",
                payload=json.loads(payload_json),
                now=now,
            )
            _fault(fault, "wait_blocker.event.after_write")
            changed = connection.execute(
                """
                UPDATE runs SET state = 'waiting', version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ?
                  AND state NOT IN ('completed', 'failed', 'cancelled')
                """,
                (now, run_id, expected_version),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("wait blocker Run CAS failed")
            _fault(fault, "wait_blocker.run.after_write")
            connection.execute(
                """
                INSERT INTO run_wait_blockers(
                    blocker_id, run_id, kind, ledger_identity, handoff_attempt,
                    observed_version, resolution_id, wake_consumed, created_at,
                    resolved_at, consumed_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, 1)
                """,
                (
                    blocker_id,
                    run_id,
                    blocker.kind.value,
                    blocker.ledger_identity,
                    blocker.handoff_attempt,
                    blocker.observed_version,
                    None if resolution is None else str(resolution["resolution_id"]),
                    now,
                    None if resolution is None else now,
                ),
            )
            _fault(fault, "wait_blocker.blocker.after_write")
        _fault(fault, "wait_blocker.after_commit")
        run = self.read_run(run_id)
        row = self.database.connection.execute(
            "SELECT * FROM run_wait_blockers WHERE blocker_id = ?", (blocker_id,)
        ).fetchone()
        assert run is not None and row is not None
        return run, _wait_blocker_record(row)

    def list_resolved_wait_blockers(
        self,
        *,
        owner_id: str,
        namespace: str,
        now: float,
    ) -> tuple[WaitBlockerRecord, ...]:
        owner_id = _required(owner_id, "owner_id")
        namespace = _required(namespace, "namespace")
        now = _time(now)
        rows = self.database.connection.execute(
            """
            SELECT blockers.* FROM run_wait_blockers AS blockers
            JOIN runs ON runs.run_id = blockers.run_id
            JOIN workflow_leases AS leases
              ON leases.run_id = blockers.run_id AND leases.namespace = ?
            WHERE blockers.resolution_id IS NOT NULL
              AND blockers.wake_consumed = 0 AND runs.state = 'waiting'
              AND ((leases.owner_id = ? AND leases.expires_at > ?)
                   OR leases.expires_at <= ?)
            ORDER BY blockers.created_at, blockers.blocker_id
            """,
            (namespace, owner_id, now, now),
        ).fetchall()
        return tuple(_wait_blocker_record(row) for row in rows)

    def consume_resolved_wait_and_claim_activation(
        self,
        *,
        blocker_id: str,
        owner_id: str,
        namespace: str,
        now: float,
        lease_ttl_seconds: float,
        fault: FaultHook | None = None,
    ) -> tuple[RunRecord, ExecutionLease, WaitActivationReceipt]:
        blocker_id = _required(blocker_id, "blocker_id")
        owner_id = _required(owner_id, "owner_id")
        namespace = _required(namespace, "namespace")
        now = _time(now)
        if not math.isfinite(lease_ttl_seconds) or lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be finite and positive")
        expires_at = now + float(lease_ttl_seconds)
        receipt_id = f"wait-activation:{blocker_id}"
        with self.database.transaction() as connection:
            receipt_row = connection.execute(
                "SELECT * FROM wait_activation_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if receipt_row is not None:
                receipt = _wait_activation_receipt(receipt_row)
                if receipt.owner_id != owner_id:
                    raise UnitOfWorkConflict("activation receipt belongs to another Runtime owner")
                run_row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (receipt.run_id,)
                ).fetchone()
                lease_row = connection.execute(
                    "SELECT * FROM workflow_leases WHERE run_id = ? AND namespace = ?",
                    (receipt.run_id, namespace),
                ).fetchone()
                if run_row is None or lease_row is None:
                    raise UnitOfWorkConflict("activation receipt authority disappeared")
                if (
                    str(lease_row["owner_id"]) != receipt.owner_id
                    or int(lease_row["epoch"]) != receipt.runtime_lease_epoch
                ):
                    raise UnitOfWorkConflict("activation receipt was superseded")
                return (
                    _run_record(run_row),
                    ExecutionLease(
                        receipt.run_id,
                        namespace,
                        receipt.owner_id,
                        receipt.runtime_lease_epoch,
                        float(lease_row["expires_at"]),
                    ),
                    receipt,
                )
            blocker_row = connection.execute(
                """
                SELECT blockers.*, resolutions.outcome_hash
                FROM run_wait_blockers AS blockers
                JOIN reconciliation_resolutions AS resolutions
                  ON resolutions.resolution_id = blockers.resolution_id
                WHERE blockers.blocker_id = ? AND blockers.wake_consumed = 0
                """,
                (blocker_id,),
            ).fetchone()
            if blocker_row is None:
                raise UnitOfWorkConflict("wait blocker is not resolved and unconsumed")
            run_id = str(blocker_row["run_id"])
            run_row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ? AND state = 'waiting'", (run_id,)
            ).fetchone()
            if run_row is None:
                raise UnitOfWorkConflict("resolved blocker Run is not waiting")
            lease_row = connection.execute(
                "SELECT * FROM workflow_leases WHERE run_id = ? AND namespace = ?",
                (run_id, namespace),
            ).fetchone()
            if lease_row is not None and float(lease_row["expires_at"]) > now:
                if str(lease_row["owner_id"]) != owner_id:
                    raise UnitOfWorkConflict("foreign active Runtime owns resolved Run")
                epoch = int(lease_row["epoch"])
            else:
                epoch = 1 if lease_row is None else int(lease_row["epoch"]) + 1
            connection.execute(
                """
                INSERT INTO workflow_leases(run_id, namespace, owner_id, epoch, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, namespace) DO UPDATE SET
                    owner_id=excluded.owner_id, epoch=excluded.epoch,
                    expires_at=excluded.expires_at
                """,
                (run_id, namespace, owner_id, epoch, expires_at),
            )
            _fault(fault, "wait_activation.lease.after_write")
            changed = connection.execute(
                """
                UPDATE runs SET state='running', version=version+1, updated_at=?
                WHERE run_id=? AND state='waiting' AND version=?
                """,
                (now, run_id, int(run_row["version"])),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("wait activation Run CAS failed")
            outcome_hash = str(blocker_row["outcome_hash"])
            connection.execute(
                """
                INSERT INTO wait_activation_receipts(
                    receipt_id, blocker_id, run_id, owner_id,
                    runtime_lease_epoch, outcome_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (receipt_id, blocker_id, run_id, owner_id, epoch, outcome_hash, now),
            )
            _fault(fault, "wait_activation.receipt.after_write")
            connection.execute(
                """
                UPDATE run_wait_blockers SET wake_consumed=1, consumed_at=?,
                    version=version+1 WHERE blocker_id=? AND wake_consumed=0
                """,
                (now, blocker_id),
            )
            self._insert_event(
                connection,
                event_id=f"{run_id}:wait-activation:{blocker_id}",
                run_id=run_id,
                kind="run.recovered",
                payload={
                    "owner_id": owner_id,
                    "lease_epoch": epoch,
                    "blocker_id": blocker_id,
                },
                now=now,
            )
        _fault(fault, "wait_activation.after_commit")
        run = self.read_run(run_id)
        assert run is not None
        return (
            run,
            ExecutionLease(run_id, namespace, owner_id, epoch, expires_at),
            WaitActivationReceipt(receipt_id, blocker_id, run_id, owner_id, epoch, outcome_hash),
        )

    def read_reconciliation_resolution(
        self, *, kind: str, ledger_identity: str, handoff_attempt: int
    ) -> ReconciliationResolution | None:
        row = self.database.connection.execute(
            """
            SELECT * FROM reconciliation_resolutions
            WHERE kind=? AND ledger_identity=? AND handoff_attempt=?
            """,
            (RecoveryKind(kind).value, ledger_identity, handoff_attempt),
        ).fetchone()
        return None if row is None else _reconciliation_resolution(row)

    def _request_run_cancel_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        expected_version: int,
        event_id: str,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        _fault(fault, "runtime_cancel.event.before_write")
        self._insert_event(
            connection,
            event_id=event_id,
            run_id=run_id,
            kind="run.cancel_requested",
            payload={},
            now=now,
        )
        _fault(fault, "runtime_cancel.event.after_write")
        open_decisions = connection.execute(
            "SELECT decision_id FROM decisions WHERE run_id=? AND state='open'", (run_id,)
        ).fetchall()
        for row in open_decisions:
            decision_id = str(row["decision_id"])
            _fault(fault, "runtime_cancel.decision.before_write")
            changed = connection.execute(
                "UPDATE decisions SET state='cancelled',response_json=?,"
                "version=version+1,resolved_at=? "
                "WHERE decision_id=? AND run_id=? AND state='open'",
                (
                    canonical_json({"reason": "run_cancelled"}),
                    now,
                    decision_id,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise UnitOfWorkConflict("decision cancellation CAS failed")
            self._insert_event(
                connection,
                event_id=f"{event_id}:decision:{decision_id}",
                run_id=run_id,
                kind="decision.cancelled",
                payload={"decision_id": decision_id},
                now=now,
            )
            _fault(fault, "runtime_cancel.decision.after_write")
        _fault(fault, "runtime_cancel.run.before_write")
        changed = connection.execute(
            """
            UPDATE runs SET state='cancel_requested',version=version+1,updated_at=?
            WHERE run_id=? AND version=?
              AND state NOT IN ('completed','failed','cancelled','cancel_requested')
            """,
            (now, run_id, expected_version),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("runtime cancellation CAS failed")
        _fault(fault, "runtime_cancel.run.after_write")

    def request_run_cancel(
        self,
        *,
        run_id: str,
        expected_version: int,
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> RunRecord:
        run_id = _required(run_id, "run_id")
        event_id = _required(event_id, "event_id")
        now = _time(now)
        existing = self.read_run(run_id)
        if existing is None:
            raise UnitOfWorkNotFound(run_id)
        if existing.state in {
            RunState.CANCEL_REQUESTED,
            RunState.CANCELLED,
            RunState.COMPLETED,
            RunState.FAILED,
        }:
            return existing
        with self.database.transaction() as connection:
            self._request_run_cancel_on_connection(
                connection,
                run_id=run_id,
                expected_version=expected_version,
                event_id=event_id,
                now=now,
                fault=fault,
            )
        _fault(fault, "runtime_cancel.after_commit")
        record = self.read_run(run_id)
        assert record is not None
        return record

    def read_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        row = self.database.connection.execute(
            """
            SELECT * FROM workflow_checkpoints
            WHERE run_id = ? AND namespace = 'react.termination.v1'
            ORDER BY version DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return None if row is None else _workflow_checkpoint(row)

    def read_initial_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        # Checkpoints are append-only. Version zero is the immutable start anchor,
        # even when later Context-control effects update the current route.
        row = self.database.connection.execute(
            """
            SELECT * FROM workflow_checkpoints
            WHERE run_id = ? AND namespace = 'react.termination.v1' AND version = 0
            """,
            (run_id,),
        ).fetchone()
        return None if row is None else _workflow_checkpoint(row)

    def read_legacy_turn_cursor(self, run_id: str) -> LegacyTurnCursorRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM legacy_turn_cursors WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return LegacyTurnCursorRecord(
            run_id=str(row["run_id"]),
            cursor_version=int(row["cursor_version"]),
            source_key=str(row["source_key"]),
            source_namespace=str(row["source_namespace"]),
            source_event_id=(
                None if row["source_event_id"] is None else str(row["source_event_id"])
            ),
            turn_id=str(row["turn_id"]),
            user_text=str(row["user_text"]),
            input_hash=str(row["input_hash"]),
            write_fence=None if row["write_fence"] is None else str(row["write_fence"]),
            turn_started_at=float(row["turn_started_at"]),
            state=str(row["state"]),
            consumed_terminal_state=(
                None
                if row["consumed_terminal_state"] is None
                else str(row["consumed_terminal_state"])
            ),
            committed_turn_hash=(
                None if row["committed_turn_hash"] is None else str(row["committed_turn_hash"])
            ),
        )

    def cas_react_checkpoint(
        self,
        *,
        run_id: str,
        lease: ExecutionLease,
        expected_version: int | None,
        checkpoint: Mapping[str, JsonValue],
        checkpoint_hash: str,
        now: float,
        fault: FaultHook | None = None,
        companion: Callable[[sqlite3.Connection], None] | None = None,
    ) -> WorkflowCheckpoint:
        run_id = _required(run_id, "run_id")
        if lease.run_id != run_id or lease.namespace != RUNTIME_LEASE_NAMESPACE:
            raise UnitOfWorkConflict("ReAct checkpoint requires canonical Run lease")
        checkpoint_json = _object_json(checkpoint, "checkpoint")
        actual_hash = hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest()
        if checkpoint_hash != actual_hash:
            raise UnitOfWorkConflict("ReAct checkpoint hash mismatch")
        if expected_version is not None and (
            isinstance(expected_version, bool) or expected_version < 0
        ):
            raise ValueError("expected_version must be non-negative or None")
        now = _time(now)
        next_version = 0 if expected_version is None else expected_version + 1
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, lease, now=now)
            latest = connection.execute(
                """
                SELECT version FROM workflow_checkpoints
                WHERE run_id = ? AND namespace = 'react.termination.v1'
                ORDER BY version DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            current_version = None if latest is None else int(latest["version"])
            if current_version != expected_version:
                raise UnitOfWorkConflict("ReAct checkpoint version CAS failed")
            _fault(fault, "react_checkpoint.before_write")
            connection.execute(
                """
                INSERT INTO workflow_checkpoints(
                    checkpoint_id, run_id, namespace, checkpoint_json,
                    checkpoint_hash, lease_epoch, version, created_at
                ) VALUES (?, ?, 'react.termination.v1', ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:react.termination.v1:{next_version}",
                    run_id,
                    checkpoint_json,
                    checkpoint_hash,
                    lease.epoch,
                    next_version,
                    now,
                ),
            )
            _fault(fault, "react_checkpoint.after_write")
            if companion is not None:
                # Same transaction as the checkpoint write (BA31): the caller's durable
                # companion (e.g. an AgentTurn staged result) commits or rolls back with it.
                companion(connection)
                _fault(fault, "react_checkpoint.after_companion_write")
        _fault(fault, "react_checkpoint.after_commit")
        result = self.read_react_checkpoint(run_id)
        assert result is not None
        return result

    async def acquire(
        self,
        run_id: RunId,
        execution_lease: ExecutionLease,
        *,
        now: float,
    ) -> RunFenceLease:
        now = _time(now)
        if (
            execution_lease.run_id != run_id.value
            or execution_lease.namespace != RUNTIME_LEASE_NAMESPACE
        ):
            raise UnitOfWorkConflict("Run fence requires the canonical Run lease")
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            row = connection.execute(
                "SELECT owner_id, runtime_lease_epoch, epoch, state "
                "FROM run_fences WHERE run_id = ?",
                (run_id.value,),
            ).fetchone()
            if row is not None and tuple(row) == (
                execution_lease.owner_id,
                execution_lease.epoch,
                int(row["epoch"]),
                "active",
            ):
                return RunFenceLease(
                    run_id,
                    int(row["epoch"]),
                    execution_lease.owner_id,
                    execution_lease.epoch,
                )
            epoch = 1 if row is None else int(row["epoch"]) + 1
            connection.execute(
                """
                INSERT INTO run_fences(
                    run_id, owner_id, runtime_lease_epoch, epoch,
                    state, acquired_at, released_at
                ) VALUES (?, ?, ?, ?, 'active', ?, NULL)
                ON CONFLICT(run_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    runtime_lease_epoch = excluded.runtime_lease_epoch,
                    epoch = excluded.epoch,
                    state = 'active', acquired_at = excluded.acquired_at,
                    released_at = NULL
                """,
                (
                    run_id.value,
                    execution_lease.owner_id,
                    execution_lease.epoch,
                    epoch,
                    now,
                ),
            )
        return RunFenceLease(run_id, epoch, execution_lease.owner_id, execution_lease.epoch)

    async def current_epoch(self, run_id: RunId) -> int:
        row = self.database.connection.execute(
            "SELECT epoch FROM run_fences WHERE run_id = ?", (run_id.value,)
        ).fetchone()
        if row is None:
            raise UnitOfWorkNotFound(run_id.value)
        return int(row[0])

    async def release(self, lease: RunFenceLease) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE run_fences SET state = 'released', released_at = ?
                WHERE run_id = ? AND owner_id = ? AND epoch = ? AND state = 'active'
                """,
                (time.time(), lease.run_id.value, lease.owner_id, lease.epoch),
            )
