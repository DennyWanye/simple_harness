"""Views, recovery scope, active budgets and completion evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    ActorAction,
    ActorContext,
    AuthorizationError,
    IdempotencyConflict,
    LegacyRunProjection,
    ParentCycleError,
    RecoveryLease,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    StaleRecoveryLease,
    thaw_json,
)
from deskpet.execution.delivery_contracts import RunLinkSpec
from deskpet.execution.evidence import (
    UNKNOWN_EVIDENCE,
    CompletionEvidence,
    EvidenceContext,
    EvidenceSelection,
)
from deskpet.execution.ports import RunView

from .core import ExecutionUowCore


class RecoveryBudgetMixin(ExecutionUowCore):
    """Views, recovery scope, active budgets and completion evidence."""

    __slots__ = ()

    @staticmethod
    def _legacy_status(value: str) -> RunStatus:
        return {
            "created": RunStatus.CREATED,
            "running": RunStatus.RUNNING,
            "waiting": RunStatus.WAITING,
            "retryable": RunStatus.QUEUED,
            "cancel_requested": RunStatus.CANCEL_REQUESTED,
            "cancelling": RunStatus.CANCEL_REQUESTED,
            "blocked": RunStatus.WAITING,
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }.get(value, RunStatus.FAILED)

    async def _legacy_projection_tx(
        self, db: aiosqlite.Connection, run_id: str
    ) -> LegacyRunProjection | None:
        row = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            return None
        root_id = run_id
        parent_id = str(row["parent_run_id"]) if row["parent_run_id"] is not None else None
        current = parent_id
        seen = {run_id}
        while current is not None:
            if current in seen:
                raise ParentCycleError(
                    "legacy_parent_cycle", "legacy workflow lineage contains a cycle"
                )
            seen.add(current)
            ancestor = await (
                await db.execute(
                    "SELECT run_id,parent_run_id FROM workflow_runs WHERE run_id=?", (current,)
                )
            ).fetchone()
            if ancestor is None:
                break
            root_id = str(ancestor["run_id"])
            current = (
                str(ancestor["parent_run_id"]) if ancestor["parent_run_id"] is not None else None
            )
        return LegacyRunProjection(
            run_id=run_id,
            session_id=str(row["session_id"]),
            root_run_id=root_id,
            parent_run_id=parent_id,
            status=self._legacy_status(str(row["status"])),
            driver_kind="workflow",
            profile_key=f"{row['workflow_name']}/{row['workflow_version']}",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        )

    def _authorize_view(
        self,
        ref: RunRef,
        actor: ActorContext,
        view: RunView,
    ) -> None:
        session_id = view.context.session_id if isinstance(view, RunRecord) else view.session_id
        root_run_id = view.context.root_run_id if isinstance(view, RunRecord) else view.root_run_id
        if ref.expected_session_id != session_id or actor.session_id != session_id:
            raise AuthorizationError("actor_not_authorized", "actor does not own the run session")
        if isinstance(view, LegacyRunProjection):
            if actor.internal:
                raise AuthorizationError(
                    "legacy_internal_authority_unavailable",
                    "legacy projections do not carry a verifiable internal authority",
                )
            return
        if actor.internal:
            if (
                actor.root_run_id != root_run_id
                or actor.capability_hash != view.context.capability_hash
                or actor.expires_at is None
                or actor.expires_at <= self._clock()
            ):
                raise AuthorizationError(
                    "actor_not_authorized", "internal authority scope is invalid or expired"
                )
            return
        if (
            actor.principal_id != view.context.principal_id
            or actor.auth_epoch != view.context.auth_epoch
            or (actor.root_run_id is not None and actor.root_run_id != root_run_id)
        ):
            raise AuthorizationError(
                "actor_not_authorized",
                "actor principal, authentication epoch, or run root differs",
            )

    async def authorize(
        self,
        ref: RunRef,
        actor: ActorContext,
        action: ActorAction | str,
    ) -> RunView:
        ActorAction(action)
        async with self._read_connection() as db:
            row = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (ref.run_id,))
            ).fetchone()
            view: RunView | None
            if row is not None:
                view = self._row_to_record(row)
            else:
                view = await self._legacy_projection_tx(db, ref.run_id)
            if view is None:
                raise RunNotFound("run_not_found", "execution run does not exist")
            self._authorize_view(ref, actor, view)
            return view

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView:
        return await self.authorize(ref, actor, ActorAction.OBSERVE)

    async def list_child_links(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunLinkSpec, ...]:
        parent = await self.authorize(ref, actor, ActorAction.OBSERVE)
        if isinstance(parent, LegacyRunProjection):
            return ()
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM execution_run_links
                    WHERE parent_run_id=? AND link_kind='structural'
                    ORDER BY created_at,link_id""",
                    (ref.run_id,),
                )
            ).fetchall()
            return tuple(
                RunLinkSpec(
                    link_id=str(row["link_id"]),
                    root_run_id=str(row["root_run_id"]),
                    parent_run_id=str(row["parent_run_id"]),
                    child_run_id=str(row["child_run_id"]),
                    attachment_policy=str(row["attachment_policy"]),
                    link_kind=str(row["link_kind"]),
                    domain_kind=(
                        str(row["domain_kind"]) if row["domain_kind"] not in (None, "") else None
                    ),
                    domain_id=(
                        str(row["domain_id"]) if row["domain_id"] not in (None, "") else None
                    ),
                )
                for row in rows
            )

    async def list_recoverable(
        self,
        *,
        limit: int = 10_000,
        run_ids: Sequence[str] = (),
    ) -> tuple[RunRecord, ...]:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("recoverable run limit must be positive")
        selected = tuple(dict.fromkeys(run_ids))
        run_filter = "" if not selected else f"AND run_id IN ({','.join('?' for _ in selected)})"
        async with self._read_connection() as db:
            owner_kind, owner_generation = await self._required_recovery_owner_tx(db)
            rows = await (
                await db.execute(
                    f"""SELECT * FROM execution_runs
                    WHERE terminal_event_id IS NULL
                    AND status IN ('created','queued','running','waiting','cancel_requested')
                    AND owner_kind=? AND owner_generation=?
                    {run_filter}
                    ORDER BY created_at,run_id LIMIT ?""",
                    (owner_kind, owner_generation, *selected, int(limit)),
                )
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    async def configure_run_active_budget(
        self,
        run_id: str,
        *,
        limit_seconds: float,
    ) -> None:
        """Freeze one root Run's active-time budget without resetting retries."""

        limit = float(limit_seconds)
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("active execution budget must be positive and finite")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT run.parent_run_id,budget.configured
                    FROM execution_runs AS run
                    LEFT JOIN execution_run_active_budgets AS budget
                        ON budget.run_id=run.run_id
                    WHERE run.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound("run_not_found", f"run does not exist: {run_id}")
            if row["parent_run_id"] is not None:
                return
            if row["configured"] is None:
                raise RuntimeError("root Run active budget was not initialized")
            if not bool(row["configured"]):
                now = float(self._clock())
                await db.execute(
                    """UPDATE execution_run_active_budgets
                    SET limit_seconds=?,configured=1,
                        budget_version=budget_version+1,updated_at=?
                    WHERE run_id=? AND configured=0""",
                    (limit, now, run_id),
                )
            await db.commit()

    async def claim_expired_active_budget_runs(
        self,
        *,
        limit: int = 16,
    ) -> tuple[RunRecord, ...]:
        """Fence due root budgets so cancellation can be retried after crashes."""

        if isinstance(limit, bool) or limit < 1:
            raise ValueError("active budget claim limit must be positive")
        async with self._write_transaction() as db:
            now = float(self._clock())
            rows = await (
                await db.execute(
                    """SELECT budget.run_id,budget.budget_state,
                        budget.budget_version,budget.consumed_seconds,
                        budget.active_since,budget.limit_seconds
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE run.terminal_event_id IS NULL
                    AND (
                        budget.budget_state='expired'
                        OR (
                            budget.budget_state='active'
                            AND budget.active_since IS NOT NULL
                            AND budget.consumed_seconds
                                + MAX(0.0,?-budget.active_since)
                                >= budget.limit_seconds
                        )
                    )
                    ORDER BY budget.updated_at,budget.run_id
                    LIMIT ?""",
                    (now, int(limit)),
                )
            ).fetchall()
            claimed_ids: list[str] = []
            for row in rows:
                run_id = str(row["run_id"])
                if str(row["budget_state"]) == "active":
                    consumed = float(row["consumed_seconds"]) + max(
                        0.0, now - float(row["active_since"])
                    )
                    cursor = await db.execute(
                        """UPDATE execution_run_active_budgets
                        SET consumed_seconds=?,active_since=NULL,
                            budget_state='expired',
                            budget_version=budget_version+1,updated_at=?
                        WHERE run_id=? AND budget_state='active'
                        AND budget_version=?""",
                        (
                            consumed,
                            now,
                            run_id,
                            int(row["budget_version"]),
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                claimed_ids.append(run_id)
            records: list[RunRecord] = []
            for run_id in claimed_ids:
                run = await (
                    await db.execute(
                        """SELECT * FROM execution_runs
                        WHERE run_id=? AND terminal_event_id IS NULL""",
                        (run_id,),
                    )
                ).fetchone()
                if run is not None:
                    records.append(self._row_to_record(run))
            await db.commit()
            return tuple(records)

    async def next_active_budget_delay(self) -> float | None:
        """Return the next active root deadline; paused waits have no deadline."""

        async with self._read_connection() as db:
            now = float(self._clock())
            expired = await (
                await db.execute(
                    """SELECT 1
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE budget.budget_state='expired'
                    AND run.terminal_event_id IS NULL LIMIT 1"""
                )
            ).fetchone()
            if expired is not None:
                return 1.0
            row = await (
                await db.execute(
                    """SELECT MIN(MAX(
                        0.0,
                        budget.limit_seconds-budget.consumed_seconds
                            -MAX(0.0,?-budget.active_since)
                    )) AS delay
                    FROM execution_run_active_budgets AS budget
                    JOIN execution_runs AS run ON run.run_id=budget.run_id
                    WHERE budget.budget_state='active'
                    AND budget.active_since IS NOT NULL
                    AND run.terminal_event_id IS NULL""",
                    (now,),
                )
            ).fetchone()
            if row is None or row["delay"] is None:
                return None
            return max(0.0, float(row["delay"]))

    async def lookup_completion_evidence(
        self,
        context: EvidenceContext,
    ) -> EvidenceSelection:
        """Project exact completion evidence without creating another ledger.

        A normal tool call resolves to its own settled execution effect.  An
        attached ``workflow_spawn`` is different: the parent control call is
        completed by a durable child terminal signal, while the actual tool
        effects live in ``workflow_effects`` under that child Run.  Resolve
        that lineage only when every durable identity agrees and the child's
        workflow audit explicitly passed.  This keeps the query fail-closed
        without discarding the receipts that prove delegated work.
        """

        # execution_effects currently has no explicit target/resource digest.
        # An effect fingerprint is a different identity and must not substitute.
        if context.target_digest is not None:
            return UNKNOWN_EVIDENCE
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT effect.run_id,effect.call_id,effect.effect_id,
                    effect.tool_name,effect.status,effect.receipt_ref,
                    effect.artifact_refs_json,run.turn_id
                    FROM execution_effects AS effect
                    JOIN execution_runs AS run ON run.run_id=effect.run_id
                    WHERE effect.effect_id=?""",
                    (context.effect_id,),
                )
            ).fetchone()
            if row is not None:
                expected = (
                    context.run_id,
                    context.turn_id,
                    context.call_id,
                    context.effect_id,
                )
                actual = (
                    str(row["run_id"]),
                    str(row["turn_id"]),
                    str(row["call_id"]),
                    str(row["effect_id"]),
                )
                artifacts = tuple(str(item) for item in json.loads(str(row["artifact_refs_json"])))
                receipt_ref = str(row["receipt_ref"] or "")
                if (
                    actual != expected
                    or str(row["status"]) != "succeeded"
                    or not receipt_ref
                    or (context.artifact_ref is not None and context.artifact_ref not in artifacts)
                ):
                    return UNKNOWN_EVIDENCE
                record = CompletionEvidence(
                    context=context,
                    tool_name=str(row["tool_name"]),
                    receipt_ref=receipt_ref,
                    artifact_refs=artifacts,
                )
                return EvidenceSelection(status="matched", records=(record,))

            expected_effect_id = hashlib.sha256(
                f"effect|{context.run_id}|{context.call_id}".encode()
            ).hexdigest()
            if context.effect_id != expected_effect_id:
                return UNKNOWN_EVIDENCE

            lineage = await (
                await db.execute(
                    """SELECT parent.turn_id,child.run_id AS child_run_id,
                    child.status AS child_status,
                    child.terminal_event_id,signal.payload_json
                    FROM execution_profile_launch_tickets AS ticket
                    JOIN execution_runs AS parent
                      ON parent.run_id=ticket.parent_run_id
                    JOIN execution_child_commands AS command
                      ON command.parent_run_id=ticket.parent_run_id
                     AND command.command_id=ticket.child_command_id
                     AND command.child_run_id=ticket.child_run_id
                    JOIN execution_child_signal_inbox AS signal
                      ON signal.operation_id=command.operation_id
                     AND signal.kind='terminal'
                    JOIN execution_runs AS child
                      ON child.run_id=ticket.child_run_id
                    WHERE ticket.parent_run_id=?
                      AND ticket.spawn_call_id=?
                      AND ticket.state='consumed'
                      AND command.status='acked'
                      AND signal.delivered_at IS NOT NULL""",
                    (context.run_id, context.call_id),
                )
            ).fetchone()
            if (
                lineage is None
                or str(lineage["turn_id"]) != context.turn_id
                or str(lineage["child_status"]) != "completed"
                or not str(lineage["terminal_event_id"] or "")
            ):
                return UNKNOWN_EVIDENCE
            try:
                terminal = json.loads(str(lineage["payload_json"]))
                value = terminal.get("value")
                if not isinstance(value, Mapping):
                    return UNKNOWN_EVIDENCE
                workflow_report = value.get("workflow_report")
                audit = (
                    workflow_report.get("audit") if isinstance(workflow_report, Mapping) else None
                )
                if (
                    terminal.get("status") != "completed"
                    or value.get("status") != "completed"
                    or value.get("run_id") != str(lineage["child_run_id"])
                    or value.get("terminal_event_id") != str(lineage["terminal_event_id"])
                    or not isinstance(audit, Mapping)
                    or audit.get("passed") is not True
                ):
                    return UNKNOWN_EVIDENCE
            except (TypeError, ValueError, json.JSONDecodeError):
                return UNKNOWN_EVIDENCE

            effect_rows = await (
                await db.execute(
                    """SELECT prepared_json,receipt_ref,artifact_refs_json
                    FROM workflow_effects
                    WHERE run_id=? AND status='committed'
                      AND receipt_ref IS NOT NULL AND receipt_ref<>''
                    ORDER BY started_at,effect_id""",
                    (str(lineage["child_run_id"]),),
                )
            ).fetchall()

        records: list[CompletionEvidence] = []
        all_artifacts: set[str] = set()
        for effect in effect_rows:
            try:
                prepared = json.loads(str(effect["prepared_json"]))
                tool_name = str(prepared.get("tool_name") or "").strip()
                artifacts = tuple(
                    str(item) for item in json.loads(str(effect["artifact_refs_json"]))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                return UNKNOWN_EVIDENCE
            receipt_ref = str(effect["receipt_ref"] or "").strip()
            if not tool_name or not receipt_ref:
                return UNKNOWN_EVIDENCE
            all_artifacts.update(artifacts)
            records.append(
                CompletionEvidence(
                    context=context,
                    tool_name=tool_name,
                    receipt_ref=receipt_ref,
                    artifact_refs=artifacts,
                )
            )
        if not records or (
            context.artifact_ref is not None and context.artifact_ref not in all_artifacts
        ):
            return UNKNOWN_EVIDENCE
        return EvidenceSelection(status="matched", records=tuple(records))

    async def read_effect_outcome(
        self,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
        args_hash: str,
        capability_hash: str,
        scope_hash: str,
    ) -> tuple[str, Mapping[str, Any], str | None, tuple[str, ...]] | None:
        """Read the authoritative execution-effect settlement; never reconciles."""
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT run_id,call_id,args_hash,capability_hash,scope_hash,
                    status,outcome_json,receipt_ref,artifact_refs_json
                    FROM execution_effects WHERE effect_id=? AND status!='running'""",
                    (effect_id,),
                )
            ).fetchone()

        if row is None or row["outcome_json"] is None:
            return None
        expected = (run_id, call_id, args_hash, capability_hash, scope_hash)
        actual = tuple(
            str(row[name])
            for name in ("run_id", "call_id", "args_hash", "capability_hash", "scope_hash")
        )
        if actual != expected:
            raise IdempotencyConflict(
                "effect_read_binding_mismatch", "effect outcome belongs to another intent"
            )
        status = str(row["status"])
        if status == "late_reconciled":
            status = "unknown"  # legacy rows had no durable evidence-verification bit
        return (
            status,
            thaw_json(json.loads(str(row["outcome_json"]))),
            str(row["receipt_ref"]) if row["receipt_ref"] else None,
            tuple(str(item) for item in json.loads(str(row["artifact_refs_json"]))),
        )

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool:
        """Claim, renew, or release one typed recovery lease."""

        if isinstance(subject, RecoveryLease):
            async with self._write_transaction() as db:
                await self._recovery_run_tx(db, subject.run_id)
                if lease_seconds is None:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET recovery_owner=NULL,
                        recovery_expires_at=NULL,recovery_heartbeat_at=NULL
                        WHERE run_id=? AND recovery_owner=? AND recovery_epoch=?""",
                        (subject.run_id, subject.owner, subject.epoch),
                    )
                    await db.commit()
                    return cursor.rowcount == 1
                if lease_seconds <= 0:
                    raise ValueError("lease_seconds must be positive")
                now = float(self._clock())
                new_expiry = now + float(lease_seconds)
                cursor = await db.execute(
                    """UPDATE execution_runs SET recovery_expires_at=?,recovery_heartbeat_at=?
                    WHERE run_id=? AND recovery_owner=? AND recovery_epoch=?
                    AND recovery_expires_at>? AND terminal_event_id IS NULL""",
                    (new_expiry, now, subject.run_id, subject.owner, subject.epoch, now),
                )
                if cursor.rowcount != 1:
                    raise StaleRecoveryLease("recovery lease is stale or expired")
                await db.commit()
                return RecoveryLease(subject.run_id, subject.owner, subject.epoch, new_expiry)
        run_id = subject
        owner = str(owner or "")
        lease_seconds = 30.0 if lease_seconds is None else lease_seconds
        if not owner.strip() or lease_seconds <= 0:
            raise ValueError("recovery owner and positive lease_seconds are required")
        async with self._write_transaction() as db:
            now = float(self._clock())
            row = await self._recovery_run_tx(db, run_id)
            if RunStatus(str(row["status"])) in TERMINAL_RUN_STATUSES:
                raise StaleRecoveryLease("terminal runs cannot be claimed for recovery")
            active_owner = row["recovery_owner"]
            expires_at = row["recovery_expires_at"]
            if (
                active_owner is not None
                and str(active_owner) == owner
                and expires_at is not None
                and float(expires_at) > now
            ):
                new_expiry = now + float(lease_seconds)
                epoch = int(row["recovery_epoch"])
                await db.execute(
                    """UPDATE execution_runs SET recovery_expires_at=?,
                    recovery_heartbeat_at=? WHERE run_id=? AND recovery_owner=?
                    AND recovery_epoch=? AND recovery_expires_at>?""",
                    (new_expiry, now, run_id, owner, epoch, now),
                )
                await db.commit()
                return RecoveryLease(run_id, owner, epoch, new_expiry)
            if (
                active_owner is not None
                and str(active_owner) != owner
                and expires_at is not None
                and float(expires_at) > now
            ):
                raise StaleRecoveryLease("run recovery lease is held by another owner")
            epoch = int(row["recovery_epoch"]) + 1
            new_expiry = now + float(lease_seconds)
            cursor = await db.execute(
                """UPDATE execution_runs SET recovery_owner=?,recovery_epoch=?,
                recovery_expires_at=?,recovery_heartbeat_at=?
                WHERE run_id=? AND recovery_epoch=?""",
                (owner, epoch, new_expiry, now, run_id, int(row["recovery_epoch"])),
            )
            if cursor.rowcount != 1:
                raise StaleRecoveryLease("recovery lease changed while being claimed")
            await db.commit()
            return RecoveryLease(run_id, owner, epoch, new_expiry)

    async def assert_recovery_fence(self, lease: RecoveryLease) -> None:
        async with self._read_connection() as db:
            await self._assert_recovery_fence_tx(db, lease, run_id=lease.run_id)

    async def _assert_optional_recovery_fence_tx(
        self, db: aiosqlite.Connection, lease: RecoveryLease | None, run_id: str
    ) -> None:
        if lease is not None:
            await self._assert_recovery_fence_tx(db, lease, run_id=run_id)

    async def _assert_recovery_fence_tx(
        self,
        db: aiosqlite.Connection,
        lease: RecoveryLease,
        *,
        run_id: str,
    ) -> None:
        if lease.run_id != run_id:
            raise StaleRecoveryLease("recovery lease is bound to another run")
        now = float(self._clock())
        row = await (
            await db.execute(
                """SELECT 1 FROM execution_runs WHERE run_id=?
                AND recovery_owner=? AND recovery_epoch=?
                AND recovery_expires_at>? AND terminal_event_id IS NULL""",
                (run_id, lease.owner, lease.epoch, now),
            )
        ).fetchone()
        if row is None:
            raise StaleRecoveryLease("recovery writer lost its lease fence")
