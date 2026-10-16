# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Target reservation, effect begin, commit and hold."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

import aiosqlite

from ..contracts import EffectKind, EffectPolicy, canonical_json
from ..store import RunFence, StaleRunFence
from .core import EffectJournalCore
from .support import (
    BudgetReservationExceeded,
    BudgetReservationRecord,
    CheckpointEffectLink,
    EffectAction,
    EffectRecord,
    EffectStateConflict,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedToolCall,
    StagingPreconditionFailed,
    ToolOutcomeState,
    effect_fingerprint,
)


@dataclass(frozen=True, slots=True)
class BeginEffectResult:
    action: EffectAction
    effect: EffectRecord


class EffectWriteMixin(EffectJournalCore):
    """Target reservation, effect begin, commit and hold."""

    __slots__ = ()

    async def reserve_targets(
        self,
        fence: RunFence,
        prepared: PreparedToolCall,
        *,
        ttl_seconds: float = 90.0,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self.reserve_targets_tx(
                db,
                fence,
                prepared,
                ttl_seconds=ttl_seconds,
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reserve_targets_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        prepared: PreparedToolCall,
        *,
        ttl_seconds: float = 90.0,
    ) -> None:
        """Reserve prepared targets on the caller-owned transaction."""

        await self._assert_fence(db, fence)
        await self._reserve_targets(db, fence, prepared, "prepared", ttl_seconds)

    async def begin(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
    ) -> BeginEffectResult:
        """Begin a legacy effect without changing its established semantics."""

        return await self._begin(
            fence,
            node_execution_id=node_execution_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=reuse_checkpoint,
            reservation_ttl_seconds=reservation_ttl_seconds,
            budget_reservation=None,
            resource_budget_kind=None,
        )

    async def begin_with_budget(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        ledger_kind: str,
        input_reserved: int,
        output_reserved: int,
        cost_reserved_micros: int,
        capability_key: str = "research_llm_budget",
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
        record_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        """Atomically fence, cap-check and reserve an effect's upstream budget."""

        if not ledger_kind:
            raise ValueError("ledger_kind is required")
        reserved = (input_reserved, output_reserved, cost_reserved_micros)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in reserved
        ):
            raise ValueError("budget reservations must be non-negative integers")
        if not any(reserved):
            raise ValueError("at least one budget reservation must be positive")
        if not capability_key:
            raise ValueError("capability_key is required")
        if resource_budget_kind not in {None, "llm"}:
            raise ValueError("unsupported v6 effect resource budget kind")
        return await self._begin(
            fence,
            node_execution_id=node_execution_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=reuse_checkpoint,
            reservation_ttl_seconds=reservation_ttl_seconds,
            budget_reservation=(
                ledger_kind,
                input_reserved,
                output_reserved,
                cost_reserved_micros,
                capability_key,
            ),
            record_budget_denial=record_denial,
            denial_artifact_refs=denial_artifact_refs,
            resource_budget_kind=resource_budget_kind,
        )

    async def _begin(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None,
        reservation_ttl_seconds: float,
        budget_reservation: tuple[str, int, int, int, str] | None,
        record_budget_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.begin_tx(
                db,
                fence,
                node_execution_id=node_execution_id,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                node_id=node_id,
                logical_effect_key=logical_effect_key,
                prepared=prepared,
                policy=policy,
                reuse_checkpoint=reuse_checkpoint,
                reservation_ttl_seconds=reservation_ttl_seconds,
                budget_reservation=budget_reservation,
                record_budget_denial=record_budget_denial,
                denial_artifact_refs=denial_artifact_refs,
                resource_budget_kind=resource_budget_kind,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def begin_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
        budget_reservation: tuple[str, int, int, int, str] | None = None,
        record_budget_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        """Claim or reuse an effect on the caller-owned transaction.

        The caller owns transaction boundaries and connection lifetime.  This
        primitive deliberately performs no BEGIN, commit, rollback, or close.
        """

        fingerprint = effect_fingerprint(
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            effect_type=prepared.effect_type,
            args_hash=prepared.args_hash,
            policy_version=policy.version,
        )
        await self._assert_fence(db, fence)
        try:
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_effects WHERE run_id=? AND effect_fingerprint=?",
                    (fence.run_id, fingerprint),
                )
            ).fetchone()
            if existing is not None:
                if existing["args_hash"] != prepared.args_hash:
                    raise EffectStateConflict("effect fingerprint resolved to different arguments")
                if budget_reservation is not None:
                    reservation = await (
                        await db.execute(
                            "SELECT * FROM workflow_effect_budget_reservations WHERE effect_id=?",
                            (existing["effect_id"],),
                        )
                    ).fetchone()
                    ledger_kind, input_reserved, output_reserved, cost_reserved, _ = (
                        budget_reservation
                    )
                    if reservation is None or (
                        reservation["run_id"] != fence.run_id
                        or reservation["ledger_kind"] != ledger_kind
                        or int(reservation["input_reserved"]) != input_reserved
                        or int(reservation["output_reserved"]) != output_reserved
                        or int(reservation["cost_reserved_micros"]) != cost_reserved
                    ):
                        raise EffectStateConflict(
                            "existing effect has a different or missing budget reservation"
                        )
                if (
                    existing["status"] == EffectStatus.RUNNING
                    and int(existing["lease_epoch"]) != fence.lease_epoch
                ):
                    now = self._clock()
                    await self._mark_running_effect_uncertain(db, existing, now)
                    existing = await (
                        await db.execute(
                            "SELECT * FROM workflow_effects WHERE effect_id=?",
                            (existing["effect_id"],),
                        )
                    ).fetchone()
                    assert existing is not None
                await db.execute(
                    "INSERT OR IGNORE INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                    (node_execution_id, existing["effect_id"]),
                )
                record = self._record(existing)
                return BeginEffectResult(self._existing_action(record.status), record)

            return await self._begin_effect_attempt(
                budget_reservation,
                db=db,
                denial_artifact_refs=denial_artifact_refs,
                fence=fence,
                fingerprint=fingerprint,
                node_execution_id=node_execution_id,
                policy=policy,
                prepared=prepared,
                record_budget_denial=record_budget_denial,
                reservation_ttl_seconds=reservation_ttl_seconds,
                resource_budget_kind=resource_budget_kind,
                reuse_checkpoint=reuse_checkpoint,
            )
        except BaseException:
            raise

    async def _begin_effect_attempt(
        self,
        budget_reservation: tuple[str, int, int, int, str] | None,
        *,
        db: aiosqlite.Connection,
        denial_artifact_refs: Sequence[str],
        fence: RunFence,
        fingerprint,
        node_execution_id: str,
        policy: EffectPolicy,
        prepared: PreparedToolCall,
        record_budget_denial: bool,
        reservation_ttl_seconds: float,
        resource_budget_kind: str | None,
        reuse_checkpoint: CheckpointEffectLink | None,
    ) -> BeginEffectResult:
        reusable = None
        if (
            budget_reservation is None
            and reuse_checkpoint is not None
            and policy.kind is EffectKind.DETERMINISTIC_REUSABLE
            and policy.reusable_across_branches
        ):
            reusable = await self._find_checkpoint_effect(db, reuse_checkpoint, fingerprint)
        if reusable is not None:
            await db.execute(
                "INSERT OR IGNORE INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                (node_execution_id, reusable["effect_id"]),
            )
            return BeginEffectResult(EffectAction.REUSE, self._record(reusable))

        effect_id = hashlib.sha256(f"{fence.run_id}|{fingerprint}".encode()).hexdigest()
        now = self._clock()
        policy_json = canonical_json(
            {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "kind": policy.kind.value,
                "max_attempts": policy.max_attempts,
                "reusable_across_branches": policy.reusable_across_branches,
            }
        )
        resource_budget = None
        resource_budget_denied = False
        if resource_budget_kind is not None:
            resource_budget = await (
                await db.execute(
                    """SELECT * FROM workflow_research_resource_budgets
                        WHERE run_id=? AND resource_kind=?""",
                    (fence.run_id, resource_budget_kind),
                )
            ).fetchone()
            if resource_budget is None:
                raise EffectStateConflict(f"v6 {resource_budget_kind} route budget is missing")
            resource_budget_denied = int(resource_budget["reserved"]) + int(
                resource_budget["consumed"]
            ) >= int(resource_budget["hard_limit"])
        if budget_reservation is not None:
            ledger_kind, input_reserved, output_reserved, cost_reserved, capability_key = (
                budget_reservation
            )
            limits = await self._budget_limits_for_update(db, fence.run_id, capability_key)
            charged = await self._charged_budget_for_update(db, fence.run_id, ledger_kind)
            requested = (input_reserved, output_reserved, cost_reserved)
            capability_budget_denied = any(
                charged_value + requested_value > limit
                for charged_value, requested_value, limit in zip(charged, requested, limits)
            )
            if resource_budget_denied or capability_budget_denied:
                if record_budget_denial:
                    await self._record_budget_denied_effect(
                        db,
                        denial_artifact_refs=denial_artifact_refs,
                        effect_id=effect_id,
                        fence=fence,
                        fingerprint=fingerprint,
                        node_execution_id=node_execution_id,
                        now=now,
                        policy_json=policy_json,
                        prepared=prepared,
                        resource_budget_denied=resource_budget_denied,
                    )
                    await db.execute(
                        """INSERT INTO workflow_effect_budget_reservations(
                            effect_id,run_id,ledger_kind,input_reserved,output_reserved,
                            cost_reserved_micros,status,dispatch_state,created_at,updated_at
                            ) VALUES(?,?,?,?,?,?,'released','not_started',?,?)""",
                        (
                            effect_id,
                            fence.run_id,
                            ledger_kind,
                            input_reserved,
                            output_reserved,
                            cost_reserved,
                            now,
                            now,
                        ),
                    )
                    row = await self._link_node_effect(
                        db,
                        denial_artifact_refs=denial_artifact_refs,
                        effect_id=effect_id,
                        node_execution_id=node_execution_id,
                        now=now,
                    )
                    return BeginEffectResult(EffectAction.FAILED, self._record(row))
                raise BudgetReservationExceeded(
                    f"{ledger_kind} reservation exceeds run capability snapshot"
                )
        await self._reserve_targets(db, fence, prepared, "claimed", reservation_ttl_seconds)
        return await self._insert_effect_rows(
            budget_reservation,
            db=db,
            effect_id=effect_id,
            fence=fence,
            fingerprint=fingerprint,
            node_execution_id=node_execution_id,
            now=now,
            policy_json=policy_json,
            prepared=prepared,
            resource_budget=resource_budget,
        )

    async def _mark_running_effect_uncertain(self, db: aiosqlite.Connection, existing, now) -> None:
        await db.execute(
            """UPDATE workflow_effects SET status='uncertain',outcome_json=?,updated_at=?
                        WHERE effect_id=? AND status='running'""",
            (
                canonical_json(
                    NormalizedToolOutcome.malformed(
                        "effect owner epoch changed before completion"
                    ).to_dict()
                ),
                now,
                existing["effect_id"],
            ),
        )
        target_rows = await (
            await db.execute(
                "SELECT reservation_key,target_json FROM workflow_effect_targets WHERE effect_id=?",
                (existing["effect_id"],),
            )
        ).fetchall()
        for target_row in target_rows:
            target_payload = json.loads(target_row["target_json"])
            if target_payload["status"] not in {"committed", "rolled_back"}:
                target_payload["status"] = "uncertain"
                await db.execute(
                    """UPDATE workflow_effect_targets SET target_json=?
                                WHERE effect_id=? AND reservation_key=?""",
                    (
                        canonical_json(target_payload),
                        existing["effect_id"],
                        target_row["reservation_key"],
                    ),
                )

    async def _record_budget_denied_effect(
        self,
        db: aiosqlite.Connection,
        *,
        denial_artifact_refs: Sequence[str],
        effect_id,
        fence: RunFence,
        fingerprint,
        node_execution_id: str,
        now,
        policy_json,
        prepared: PreparedToolCall,
        resource_budget_denied,
    ) -> None:
        if prepared.prepared_targets:
            raise EffectStateConflict("recorded budget denial cannot own prepared targets")
        denied = NormalizedToolOutcome.failure(
            "budget_denied",
            (
                "effect round exceeds frozen route resource budget"
                if resource_budget_denied
                else "effect reservation exceeds immutable run capability"
            ),
        )
        await db.execute(
            """INSERT INTO workflow_effects(
                            effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
                            policy_json,args_hash,status,outcome_json,prepared_json,artifact_refs_json,
                            lease_epoch,started_at,updated_at,ended_at
                            ) VALUES(?,?,?,?,?,?,?,'failed',?,?,?, ?,?,?,?)""",
            (
                effect_id,
                fence.run_id,
                node_execution_id,
                fingerprint,
                prepared.effect_type,
                policy_json,
                prepared.args_hash,
                canonical_json(denied.to_dict()),
                canonical_json(prepared.to_dict()),
                canonical_json(list(denial_artifact_refs)),
                fence.lease_epoch,
                now,
                now,
                now,
            ),
        )

    async def _link_node_effect(
        self,
        db: aiosqlite.Connection,
        *,
        denial_artifact_refs: Sequence[str],
        effect_id,
        node_execution_id: str,
        now,
    ):
        await db.execute(
            """INSERT INTO workflow_node_effects(
                            node_execution_id,effect_id) VALUES(?,?)""",
            (node_execution_id, effect_id),
        )
        for artifact_ref in sorted(set(denial_artifact_refs)):
            digest = str(artifact_ref).removeprefix("sha256:")
            blob = await (
                await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,))
            ).fetchone()
            if blob is None:
                raise StagingPreconditionFailed(
                    f"budget denial dependency is not registered: {digest}"
                )
            await db.execute(
                """INSERT OR IGNORE INTO workflow_blob_refs(
                                sha256,owner_kind,owner_id,created_at
                                ) VALUES(?,'effect',?,?)""",
                (digest, effect_id, now),
            )
        row = await (
            await db.execute(
                "SELECT * FROM workflow_effects WHERE effect_id=?",
                (effect_id,),
            )
        ).fetchone()
        assert row is not None
        return row

    async def _insert_effect_rows(
        self,
        budget_reservation: tuple[str, int, int, int, str] | None,
        *,
        db: aiosqlite.Connection,
        effect_id,
        fence: RunFence,
        fingerprint,
        node_execution_id: str,
        now,
        policy_json,
        prepared: PreparedToolCall,
        resource_budget,
    ) -> BeginEffectResult:
        await db.execute(
            """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,policy_json,
                args_hash,status,prepared_json,lease_epoch,started_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'running',?,?,?,?)""",
            (
                effect_id,
                fence.run_id,
                node_execution_id,
                fingerprint,
                prepared.effect_type,
                policy_json,
                prepared.args_hash,
                canonical_json(prepared.to_dict()),
                fence.lease_epoch,
                now,
                now,
            ),
        )
        for target in prepared.prepared_targets:
            await db.execute(
                "INSERT INTO workflow_effect_targets(effect_id,reservation_key,target_json) VALUES(?,?,?)",
                (
                    effect_id,
                    target.reservation_key,
                    canonical_json({"status": "prepared", "target": target.to_dict()}),
                ),
            )
        await db.execute(
            "INSERT INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
            (node_execution_id, effect_id),
        )
        if budget_reservation is not None:
            ledger_kind, input_reserved, output_reserved, cost_reserved, _ = budget_reservation
            await db.execute(
                """INSERT INTO workflow_effect_budget_reservations(
                    effect_id,run_id,ledger_kind,input_reserved,output_reserved,
                    cost_reserved_micros,status,dispatch_state,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,'reserved','not_started',?,?)""",
                (
                    effect_id,
                    fence.run_id,
                    ledger_kind,
                    input_reserved,
                    output_reserved,
                    cost_reserved,
                    now,
                    now,
                ),
            )
        if resource_budget is not None:
            cursor = await db.execute(
                """UPDATE workflow_research_resource_budgets
                    SET consumed=consumed+1,revision=revision+1
                    WHERE budget_id=? AND reserved+consumed<hard_limit""",
                (resource_budget["budget_id"],),
            )
            if cursor.rowcount != 1:
                raise EffectStateConflict("v6 LLM route budget consume CAS lost")
        row = await (
            await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
        ).fetchone()
        assert row is not None
        return BeginEffectResult(EffectAction.EXECUTE, self._record(row))

    async def commit(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.commit_tx(
                db,
                fence,
                effect_id,
                outcome,
                receipt_ref=receipt_ref,
                artifact_refs=artifact_refs,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        """Settle an effect on the caller-owned transaction."""

        await self._assert_fence(db, fence)
        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if int(row["lease_epoch"]) != fence.lease_epoch:
            raise StaleRunFence(f"effect belongs to stale epoch: {effect_id}")
        if row["status"] == EffectStatus.COMMITTED:
            return self._record(row)
        if row["status"] not in {EffectStatus.RUNNING, EffectStatus.UNCERTAIN}:
            raise EffectStateConflict(f"cannot commit effect in state {row['status']}")
        target_status = (
            EffectStatus.COMMITTED
            if outcome.state is ToolOutcomeState.SUCCESS
            else EffectStatus.FAILED
        )
        if target_status is EffectStatus.COMMITTED:
            await self._assert_staged_targets_committed(db, row)
        now = self._clock()
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,receipt_ref=?,
            artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
            (
                target_status.value,
                canonical_json(outcome.to_dict()),
                receipt_ref,
                canonical_json(list(artifact_refs)),
                now,
                now,
                effect_id,
            ),
        )
        reservation_status = "committed" if target_status is EffectStatus.COMMITTED else "released"
        await db.execute(
            """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
            WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
            (reservation_status, now, effect_id),
        )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)

    async def mark_upstream_started(
        self, fence: RunFence, effect_id: str
    ) -> BudgetReservationRecord:
        """CAS a reserved call immediately before entering provider transport."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            if int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"effect belongs to stale epoch: {effect_id}")
            reservation = await self._budget_reservation_for_update(db, fence.run_id, effect_id)
            if reservation["status"] != "reserved":
                raise EffectStateConflict(
                    f"cannot dispatch budget reservation in state {reservation['status']}"
                )
            if reservation["dispatch_state"] == "not_started":
                now = self._clock()
                cursor = await db.execute(
                    """UPDATE workflow_effect_budget_reservations
                    SET dispatch_state='started',upstream_started_at=?,updated_at=?
                    WHERE effect_id=? AND status='reserved' AND dispatch_state='not_started'""",
                    (now, now, effect_id),
                )
                if cursor.rowcount != 1:
                    raise EffectStateConflict("upstream dispatch CAS lost")
                reservation = await self._budget_reservation_for_update(db, fence.run_id, effect_id)
            await db.commit()
            return self._budget_reservation_record(reservation)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_or_hold(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        input_actual: int | None = None,
        output_actual: int | None = None,
        cost_actual_micros: int | None = None,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        """Settle known usage or conservatively hold a dispatched unknown call."""

        actuals = (input_actual, output_actual, cost_actual_micros)
        if any(value is not None for value in actuals) and not all(
            value is not None for value in actuals
        ):
            raise ValueError("actual input, output and cost must be supplied together")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in actuals
            if value is not None
        ):
            raise ValueError("actual usage must be non-negative integers")

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            reservation = await self._budget_reservation_for_update(db, fence.run_id, effect_id)
            if reservation["status"] != "reserved":
                await db.commit()
                return self._record(effect)

            usage_known = input_actual is not None
            if usage_known and int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"late usage belongs to stale epoch: {effect_id}")
            now = self._clock()
            if usage_known:
                reservation_status = "committed"
                effect_status = (
                    EffectStatus.COMMITTED
                    if outcome.state is ToolOutcomeState.SUCCESS
                    else EffectStatus.FAILED
                )
            elif reservation["dispatch_state"] == "started":
                reservation_status = "held_uncertain"
                effect_status = EffectStatus.UNCERTAIN
            else:
                reservation_status = "released"
                effect_status = EffectStatus.FAILED

            if effect_status is EffectStatus.COMMITTED:
                await self._assert_staged_targets_committed(db, effect)
            await db.execute(
                """UPDATE workflow_effect_budget_reservations
                SET input_actual=?,output_actual=?,cost_actual_micros=?,status=?,updated_at=?
                WHERE effect_id=? AND status='reserved'""",
                (
                    input_actual,
                    output_actual,
                    cost_actual_micros,
                    reservation_status,
                    now,
                    effect_id,
                ),
            )
            await db.execute(
                """UPDATE workflow_effects SET status=?,outcome_json=?,receipt_ref=?,
                artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
                (
                    effect_status.value,
                    canonical_json(outcome.to_dict()),
                    receipt_ref,
                    canonical_json(list(artifact_refs)),
                    now,
                    now,
                    effect_id,
                ),
            )
            # Blob dependencies supplied by the effect adapter become durable
            # effect-owned roots in the same transaction as the canonical
            # effect outcome.  Keeping this promotion here (instead of in the
            # blob store) closes the raw-result/outcome crash window: replay can
            # only observe either the old reservation or the committed outcome
            # together with all of its registered blob dependencies.
            for artifact_ref in sorted(set(artifact_refs)):
                digest = str(artifact_ref).strip()
                if digest.startswith("sha256:"):
                    digest = digest[7:]
                if len(digest) != 64 or any(
                    character not in "0123456789abcdef" for character in digest
                ):
                    raise ValueError("artifact_refs must contain lowercase SHA-256 refs")
                blob = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,))
                ).fetchone()
                if blob is None:
                    raise StagingPreconditionFailed(
                        f"effect blob dependency is not registered: {digest}"
                    )
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'effect',?,?)""",
                    (digest, effect_id, now),
                )
            target_status = "committed" if effect_status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (
                    SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?
                )""",
                (target_status, now, effect_id),
            )
            result = await self._effect_for_update(db, fence.run_id, effect_id)
            await db.commit()
            return self._record(result)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
