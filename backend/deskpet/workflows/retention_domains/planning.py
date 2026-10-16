"""Startup reconciliation and cleanup plans."""

from __future__ import annotations

import aiosqlite

from ..contracts import TERMINAL_RUN_STATUSES
from .core import RetentionCore
from .support import (
    ResearchReachabilityView,
    RetentionReport,
    _CheckpointKey,
    _CleanupPlan,
    _column_values,
    _placeholders,
    _ResearchCleanupPlan,
)

_REF_OWNER_TABLES = {
    "run": ("workflow_runs", "run_id"),
    "workflow_run": ("workflow_runs", "run_id"),
    "event": ("workflow_events", "event_id"),
    "workflow_event": ("workflow_events", "event_id"),
    "delivery": ("workflow_deliveries", "delivery_id"),
    "workflow_delivery": ("workflow_deliveries", "delivery_id"),
    "node": ("workflow_nodes", "node_execution_id"),
    "workflow_node": ("workflow_nodes", "node_execution_id"),
    "effect": ("workflow_effects", "effect_id"),
    "workflow_effect": ("workflow_effects", "effect_id"),
    "trace": ("trace_runs", "trace_id"),
    "trace_run": ("trace_runs", "trace_id"),
    "span": ("trace_spans", "span_id"),
    "trace_span": ("trace_spans", "span_id"),
    "evaluation": ("evaluations", "evaluation_id"),
}


class CleanupPlanMixin(RetentionCore):
    """Startup reconciliation and cleanup plans."""

    __slots__ = ()

    async def reconcile_startup(self, *, dry_run: bool = False) -> RetentionReport:
        """Reconcile expired leases, then execute the normal ordered cleanup."""

        now = float(self.clock.now())
        reservation = await self._reconcile_reservations(now, dry_run=dry_run)
        cleanup = await self._cleanup_at(now, dry_run=dry_run)
        return RetentionReport(
            dry_run=dry_run,
            observed_at=now,
            stages=(reservation, *cleanup.stages),
        )

    async def _build_plan(
        self, now: float, *, extra_protected_run_ids: frozenset[str] = frozenset()
    ) -> _CleanupPlan:
        terminal_cutoff = now - self.policy.terminal_seconds
        tombstone_cutoff = now - self.policy.evaluation_tombstone_seconds
        terminal_values = tuple(status.value for status in TERMINAL_RUN_STATUSES)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT run_id,ended_at FROM workflow_runs
                    WHERE status IN ({_placeholders(terminal_values)})
                      AND ended_at IS NOT NULL AND ended_at<=?
                    ORDER BY run_id""",
                    (*terminal_values, terminal_cutoff),
                )
            ).fetchall()
            safe: list[str] = []
            ended_at: dict[str, float] = {}
            protected: list[str] = []
            for row in rows:
                run_id = str(row["run_id"])
                reasons: list[str] = []
                if run_id in extra_protected_run_ids:
                    reasons.append("research_lineage")
                active_decision = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_decisions
                        WHERE run_id=? AND status IN ('prepared','open') LIMIT 1""",
                        (run_id,),
                    )
                ).fetchone()
                if active_decision is not None:
                    reasons.append("active_decision")
                undelivered = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_deliveries
                        WHERE run_id=? AND status<>'delivered' LIMIT 1""",
                        (run_id,),
                    )
                ).fetchone()
                if undelivered is not None:
                    reasons.append("undelivered_delivery")
                if reasons:
                    protected.append(f"{run_id}:{'+'.join(reasons)}")
                    continue
                safe.append(run_id)
                ended_at[run_id] = float(row["ended_at"])

            full_run_ids = tuple(safe)
            expired_evaluations: tuple[str, ...] = ()
            if full_run_ids:
                placeholders = _placeholders(full_run_ids)
                expired_evaluations = await _column_values(
                    db,
                    f"""SELECT e.evaluation_id FROM evaluations e
                    WHERE e.run_id IN ({placeholders}) AND e.created_at<=?
                      AND NOT EXISTS(
                        SELECT 1 FROM eval_results r
                        WHERE r.evaluation_id=e.evaluation_id
                      )
                    ORDER BY e.evaluation_id""",
                    (*full_run_ids, tombstone_cutoff),
                )

            final_run_ids: list[str] = []
            for run_id in full_run_ids:
                if ended_at[run_id] > tombstone_cutoff:
                    continue
                retained_evaluation = await (
                    await db.execute(
                        f"""SELECT 1 FROM evaluations WHERE run_id=?
                        {f"AND evaluation_id NOT IN ({_placeholders(expired_evaluations)})" if expired_evaluations else ""}
                        LIMIT 1""",
                        (run_id, *expired_evaluations),
                    )
                ).fetchone()
                if retained_evaluation is None:
                    final_run_ids.append(run_id)

            event_ids = await self._ids_for_runs(db, "workflow_events", "event_id", full_run_ids)
            delivery_ids = await self._ids_for_runs(
                db, "workflow_deliveries", "delivery_id", full_run_ids
            )
            node_ids = await self._ids_for_runs(
                db, "workflow_nodes", "node_execution_id", full_run_ids
            )
            effect_ids = await self._ids_for_runs(db, "workflow_effects", "effect_id", full_run_ids)
            trace_ids = await self._ids_for_runs(db, "trace_runs", "trace_id", full_run_ids)
            span_ids: tuple[str, ...] = ()
            if trace_ids:
                span_ids = await _column_values(
                    db,
                    f"SELECT span_id FROM trace_spans WHERE trace_id IN ({_placeholders(trace_ids)}) ORDER BY span_id",
                    trace_ids,
                )
            checkpoint_keys = await self._checkpoint_candidates(db, full_run_ids)
            dangling_ref_owners = await self._dangling_ref_owners(db, full_run_ids)
            return _CleanupPlan(
                full_run_ids=full_run_ids,
                final_run_ids=tuple(final_run_ids),
                expired_evaluation_ids=expired_evaluations,
                protected=tuple(protected),
                event_ids=event_ids,
                delivery_ids=delivery_ids,
                node_ids=node_ids,
                effect_ids=effect_ids,
                trace_ids=trace_ids,
                span_ids=span_ids,
                checkpoint_keys=checkpoint_keys,
                dangling_ref_owners=dangling_ref_owners,
            )
        finally:
            await db.close()

    async def _has_research_records(self) -> bool:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_run_control_commands
                    UNION SELECT 1 FROM workflow_research_snapshots
                    UNION SELECT 1 FROM workflow_research_snapshot_pins
                    UNION SELECT 1 FROM workflow_research_lineage LIMIT 1"""
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def _build_research_aware_plan(
        self, now: float, reachability: ResearchReachabilityView
    ) -> _CleanupPlan:
        """Protect retained children and every ancestor before deleting run-owned data."""

        protected = set(reachability.protected_run_ids)
        provisional = await self._build_plan(now, extra_protected_run_ids=frozenset(protected))
        final = set(provisional.final_run_ids)
        db = await self._connect()
        try:
            lineage_rows = await (
                await db.execute(
                    """SELECT run_id,parent_run_id FROM workflow_research_lineage
                    ORDER BY operation_id"""
                )
            ).fetchall()
            snapshot_rows = await (
                await db.execute(
                    """SELECT run_id,snapshot_hash FROM workflow_research_snapshots
                    ORDER BY snapshot_hash"""
                )
            ).fetchall()
            v6_head_rows = await (
                await db.execute(
                    """SELECT parent_run_id,child_run_id
                    FROM workflow_research_continuation_heads"""
                )
            ).fetchall()
        finally:
            await db.close()

        # Remaining v6 heads were either live or failed closed in the atomic
        # component pass.  Never allow the legacy per-stage collector to split
        # such a component.
        protected.update(str(row["parent_run_id"]) for row in v6_head_rows)
        protected.update(str(row["child_run_id"]) for row in v6_head_rows)

        # A recent terminal child is still a retention root even though the
        # repository's live-control reachability correctly treats it terminal.
        protected.update(
            str(row["run_id"]) for row in lineage_rows if str(row["run_id"]) not in final
        )
        protected.update(
            str(row["run_id"])
            for row in snapshot_rows
            if str(row["snapshot_hash"]) in reachability.protected_snapshot_hashes
        )
        changed = True
        while changed:
            changed = False
            for row in lineage_rows:
                if str(row["run_id"]) not in protected or row["parent_run_id"] is None:
                    continue
                parent = str(row["parent_run_id"])
                if parent not in protected:
                    protected.add(parent)
                    changed = True
        return await self._build_plan(now, extra_protected_run_ids=frozenset(protected))

    async def _build_research_cleanup_plan(
        self,
        now: float,
        plan: _CleanupPlan,
        reachability: ResearchReachabilityView,
    ) -> _ResearchCleanupPlan:
        final = set(plan.final_run_ids)
        db = await self._connect()
        try:
            lineage_rows = await (
                await db.execute(
                    """SELECT operation_id,run_id,parent_operation_id,snapshot_hash
                    FROM workflow_research_lineage ORDER BY operation_id"""
                )
            ).fetchall()
            pin_rows = await (
                await db.execute(
                    """SELECT pin_id,snapshot_hash,run_id,expires_at
                    FROM workflow_research_snapshot_pins ORDER BY pin_id"""
                )
            ).fetchall()
            snapshot_rows = await (
                await db.execute(
                    """SELECT snapshot_hash,run_id,manifest_ref,expires_at
                    FROM workflow_research_snapshots ORDER BY snapshot_hash"""
                )
            ).fetchall()
            ref_rows = await (
                await db.execute(
                    """SELECT owner_id,sha256 FROM workflow_blob_refs
                    WHERE owner_kind='research_snapshot' ORDER BY owner_id,sha256"""
                )
            ).fetchall()
        finally:
            await db.close()

        lineage_candidates = {
            str(row["operation_id"]) for row in lineage_rows if str(row["run_id"]) in final
        }
        by_operation = {str(row["operation_id"]): row for row in lineage_rows}

        def depth(operation_id: str) -> int:
            seen: set[str] = set()
            current = operation_id
            result = 0
            while current in by_operation and current not in seen:
                seen.add(current)
                parent = by_operation[current]["parent_operation_id"]
                if parent is None:
                    break
                result += 1
                current = str(parent)
            return result

        lineage_order = tuple(sorted(lineage_candidates, key=lambda item: (-depth(item), item)))
        pin_candidates = {
            str(row["pin_id"])
            for row in pin_rows
            if str(row["pin_id"]) not in reachability.protected_pin_ids
            and (
                str(row["run_id"]) in final
                or (row["expires_at"] is not None and float(row["expires_at"]) <= now)
            )
        }
        retained_pin_snapshots = {
            str(row["snapshot_hash"])
            for row in pin_rows
            if str(row["pin_id"]) not in pin_candidates
        }
        retained_lineage_snapshots = {
            str(row["snapshot_hash"])
            for row in lineage_rows
            if row["snapshot_hash"] is not None
            and str(row["operation_id"]) not in lineage_candidates
        }
        snapshot_candidates = {
            str(row["snapshot_hash"])
            for row in snapshot_rows
            if str(row["snapshot_hash"]) not in reachability.protected_snapshot_hashes
            and str(row["snapshot_hash"]) not in retained_pin_snapshots
            and str(row["snapshot_hash"]) not in retained_lineage_snapshots
            and (
                str(row["run_id"]) in final
                or (row["expires_at"] is not None and float(row["expires_at"]) <= now)
            )
        }
        refs = tuple(
            (str(row["owner_id"]), str(row["sha256"]))
            for row in ref_rows
            if str(row["owner_id"]) in snapshot_candidates
        )
        return _ResearchCleanupPlan(
            lineage_operation_ids=lineage_order,
            pin_ids=tuple(sorted(pin_candidates)),
            snapshot_hashes=tuple(sorted(snapshot_candidates)),
            snapshot_blob_refs=refs,
            run_ids=tuple(sorted(final)),
        )

    @staticmethod
    async def _ids_for_runs(
        db: aiosqlite.Connection,
        table: str,
        id_column: str,
        run_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not run_ids:
            return ()
        return await _column_values(
            db,
            f"SELECT {id_column} FROM {table} WHERE run_id IN ({_placeholders(run_ids)}) ORDER BY {id_column}",
            run_ids,
        )

    @staticmethod
    async def _dangling_ref_owners(
        db: aiosqlite.Connection,
        cleanup_run_ids: tuple[str, ...] = (),
    ) -> tuple[tuple[str, str], ...]:
        dangling: set[tuple[str, str]] = set()
        for owner_kind, (table, id_column) in _REF_OWNER_TABLES.items():
            rows = await (
                await db.execute(
                    f"""SELECT DISTINCT refs.owner_id FROM workflow_blob_refs refs
                    WHERE refs.owner_kind=? AND NOT EXISTS(
                        SELECT 1 FROM {table} owner
                        WHERE owner.{id_column}=refs.owner_id
                    )""",
                    (owner_kind,),
                )
            ).fetchall()
            dangling.update((owner_kind, str(row["owner_id"])) for row in rows)
        provisional = await (
            await db.execute(
                """SELECT DISTINCT owner_kind,owner_id FROM workflow_blob_refs
                WHERE owner_kind IN ('run_staging','pending_task')"""
            )
        ).fetchall()
        for row in provisional:
            owner_kind = str(row["owner_kind"])
            owner_id = str(row["owner_id"])
            run_id = owner_id if owner_kind == "run_staging" else owner_id.split(":", 1)[0]
            run = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if (
                run is None
                or run_id in cleanup_run_ids
                or str(run["status"]) in TERMINAL_RUN_STATUSES
            ):
                dangling.add((owner_kind, owner_id))
        return tuple(sorted(dangling))

    @staticmethod
    async def _checkpoint_candidates(
        db: aiosqlite.Connection,
        run_ids: tuple[str, ...],
    ) -> tuple[_CheckpointKey, ...]:
        if not run_ids:
            return ()
        placeholders = _placeholders(run_ids)
        rows = await (
            await db.execute(
                f"""SELECT DISTINCT owned.thread_id,owned.checkpoint_ns,owned.checkpoint_id
                FROM workflow_checkpoint_owners owned
                WHERE owned.run_id IN ({placeholders})
                  AND NOT EXISTS(
                    SELECT 1 FROM workflow_checkpoint_owners retained
                    WHERE retained.thread_id=owned.thread_id
                      AND retained.checkpoint_ns=owned.checkpoint_ns
                      AND retained.checkpoint_id=owned.checkpoint_id
                      AND retained.run_id NOT IN ({placeholders})
                  )
                ORDER BY owned.thread_id,owned.checkpoint_ns,owned.checkpoint_id""",
                (*run_ids, *run_ids),
            )
        ).fetchall()
        return tuple(
            _CheckpointKey(
                thread_id=str(row["thread_id"]),
                checkpoint_ns=str(row["checkpoint_ns"]),
                checkpoint_id=str(row["checkpoint_id"]),
            )
            for row in rows
        )
