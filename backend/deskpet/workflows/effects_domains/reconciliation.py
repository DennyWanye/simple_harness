# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Budget ledger rebuild, reconciliation, late finalisation and checkpoint links."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import aiosqlite

from ..contracts import JsonValue, canonical_json, validate_json_value
from ..store import RunFence, StaleRunFence
from .core import EffectJournalCore
from .support import (
    BudgetReservationRecord,
    CheckpointEffectLink,
    EffectRecord,
    EffectStateConflict,
    EffectStatus,
    NormalizedToolOutcome,
    ToolOutcomeState,
)


@dataclass(frozen=True, slots=True)
class BudgetLedgerSnapshot:
    ledger_kind: str
    max_input_tokens: int
    max_output_tokens: int
    max_cost_micros: int
    charged_input_tokens: int
    charged_output_tokens: int
    charged_cost_micros: int
    reservations: tuple[BudgetReservationRecord, ...]


class EffectReconciliationMixin(EffectJournalCore):
    """Budget ledger rebuild, reconciliation, late finalisation and checkpoint links."""

    __slots__ = ()

    async def rebuild_budget_ledger(
        self,
        fence: RunFence,
        *,
        ledger_kind: str,
        capability_key: str = "research_llm_budget",
    ) -> BudgetLedgerSnapshot:
        """Rebuild the authoritative ledger projection from durable reservations."""

        if not ledger_kind or not capability_key:
            raise ValueError("ledger_kind and capability_key are required")
        db = await self._connect()
        try:
            await db.execute("BEGIN")
            await self._assert_fence(db, fence)
            limits = await self._budget_limits_for_update(db, fence.run_id, capability_key)
            charged = await self._charged_budget_for_update(db, fence.run_id, ledger_kind)
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_effect_budget_reservations
                    WHERE run_id=? AND ledger_kind=? ORDER BY created_at,effect_id""",
                    (fence.run_id, ledger_kind),
                )
            ).fetchall()
            await db.commit()
            return BudgetLedgerSnapshot(
                ledger_kind=ledger_kind,
                max_input_tokens=limits[0],
                max_output_tokens=limits[1],
                max_cost_micros=limits[2],
                charged_input_tokens=charged[0],
                charged_output_tokens=charged[1],
                charged_cost_micros=charged[2],
                reservations=tuple(self._budget_reservation_record(row) for row in rows),
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def mark_uncertain(self, fence: RunFence, effect_id: str, reason: str) -> EffectRecord:
        return await self._reconcile_write(
            fence,
            effect_id,
            EffectStatus.UNCERTAIN,
            NormalizedToolOutcome.malformed(reason),
        )

    async def reconcile(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome | None,
        *,
        evidence_verified: bool = False,
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.reconcile_tx(
                db,
                fence,
                effect_id,
                outcome,
                evidence_verified=evidence_verified,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reconcile_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome | None,
        *,
        evidence_verified: bool = False,
    ) -> EffectRecord:
        """Reconcile an uncertain effect on the caller-owned transaction."""

        if outcome is None or outcome.state is ToolOutcomeState.MALFORMED:
            status = EffectStatus.UNCERTAIN
            normalized = outcome or NormalizedToolOutcome.malformed(
                "reconciliation produced no outcome"
            )
        elif outcome.state is ToolOutcomeState.SUCCESS and evidence_verified:
            status = EffectStatus.COMMITTED
            normalized = outcome
        elif outcome.state is ToolOutcomeState.FAILURE:
            status = EffectStatus.FAILED
            normalized = outcome
        else:
            status = EffectStatus.UNCERTAIN
            normalized = NormalizedToolOutcome.malformed(
                "success lacks verified reconciliation evidence"
            )
        return await self._reconcile_write_tx(db, fence, effect_id, status, normalized)

    async def finalize_late(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        """Record a thread result without ever advancing graph state under a stale fence."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.finalize_late_tx(
                db,
                fence,
                effect_id=effect_id,
                args_hash=args_hash,
                lease_epoch=lease_epoch,
                outcome=outcome,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def finalize_late_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        """Finalize a late result on the caller-owned transaction."""

        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if row["args_hash"] != args_hash or int(row["lease_epoch"]) != lease_epoch:
            raise EffectStateConflict("late result identity does not match the prepared effect")
        current = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=? AND lease_epoch=?
                AND run_version=? AND status='running'""",
                (fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
            )
        ).fetchone()
        now = self._clock()
        if row["status"] in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            return self._record(row)
        if current is None:
            status = EffectStatus.LATE_ORPHAN
        elif outcome.state is ToolOutcomeState.SUCCESS:
            status = EffectStatus.COMMITTED
            await self._assert_staged_targets_committed(db, row)
        else:
            status = EffectStatus.FAILED
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=?""",
            (status.value, canonical_json(outcome.to_dict()), now, now, effect_id),
        )
        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            reservation_status = "committed" if status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
                (reservation_status, now, effect_id),
            )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)

    async def record_target_state(
        self,
        fence: RunFence,
        effect_id: str,
        reservation_key: str,
        *,
        expected: Sequence[str],
        status: str,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            if int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"target belongs to stale effect epoch: {effect_id}")
            row = await (
                await db.execute(
                    "SELECT target_json FROM workflow_effect_targets WHERE effect_id=? AND reservation_key=?",
                    (effect_id, reservation_key),
                )
            ).fetchone()
            if row is None:
                raise KeyError(reservation_key)
            payload = json.loads(row["target_json"])
            if payload["status"] not in set(expected):
                raise EffectStateConflict(
                    f"target {reservation_key} is {payload['status']}, expected {tuple(expected)}"
                )
            transitions = {
                "prepared": {"staged", "rolled_back", "uncertain"},
                "staged": {"committing", "rolled_back", "uncertain"},
                "committing": {"committed", "uncertain"},
                "uncertain": {"staged", "committing", "committed", "rolled_back"},
                "committed": set(),
                "rolled_back": set(),
            }
            if status not in transitions.get(payload["status"], set()):
                raise EffectStateConflict(
                    f"invalid target transition {payload['status']} -> {status}"
                )
            payload["status"] = status
            if evidence is not None:
                copied = copy.deepcopy(dict(evidence))
                validate_json_value(copied)
                payload["evidence"] = copied
            await db.execute(
                "UPDATE workflow_effect_targets SET target_json=? WHERE effect_id=? AND reservation_key=?",
                (canonical_json(payload), effect_id, reservation_key),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def link_checkpoint(
        self,
        fence: RunFence,
        link: CheckpointEffectLink,
        effect_id: str,
        node_execution_id: str,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            row = await (
                await db.execute(
                    """SELECT effect.* FROM workflow_effects effect
                    WHERE effect.effect_id=? AND (
                        effect.run_id=? OR EXISTS(
                            SELECT 1 FROM workflow_node_effects node_link
                            WHERE node_link.node_execution_id=? AND node_link.effect_id=effect.effect_id
                        )
                    )""",
                    (effect_id, fence.run_id, node_execution_id),
                )
            ).fetchone()
            if row is None:
                raise EffectStateConflict("effect is not linked to the fenced run node")
            if row["status"] != EffectStatus.COMMITTED:
                raise EffectStateConflict("only committed effects may be linked to a checkpoint")
            checkpoint = await (
                await db.execute(
                    """SELECT 1 FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=?
                    AND checkpoint_id=? AND run_id=?""",
                    (link.thread_id, link.checkpoint_ns, link.checkpoint_id, fence.run_id),
                )
            ).fetchone()
            if checkpoint is None:
                raise EffectStateConflict("checkpoint link does not belong to the fenced run")
            await db.execute(
                """INSERT OR IGNORE INTO workflow_checkpoint_effects(
                thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
                ) VALUES(?,?,?,?,?)""",
                (
                    link.thread_id,
                    link.checkpoint_ns,
                    link.checkpoint_id,
                    effect_id,
                    node_execution_id,
                ),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get(self, effect_id: str) -> EffectRecord | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            return self._record(row) if row is not None else None
        finally:
            await db.close()

    async def _reconcile_write(
        self,
        fence: RunFence,
        effect_id: str,
        status: EffectStatus,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self._reconcile_write_tx(
                db,
                fence,
                effect_id,
                status,
                outcome,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _reconcile_write_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        status: EffectStatus,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        await self._assert_fence(db, fence)
        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if row["status"] in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            if row["status"] == status:
                return self._record(row)
            raise EffectStateConflict(
                f"terminal effect {effect_id} cannot transition from {row['status']} to {status.value}"
            )
        if status is EffectStatus.COMMITTED:
            await self._assert_staged_targets_committed(db, row)
        now = self._clock()
        ended_at = now if status in {EffectStatus.COMMITTED, EffectStatus.FAILED} else None
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=?""",
            (status.value, canonical_json(outcome.to_dict()), now, ended_at, effect_id),
        )
        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            reservation_status = "committed" if status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
                (reservation_status, now, effect_id),
            )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)
