"""Ordered retention and startup reconciliation for durable workflows."""

from __future__ import annotations

import time
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import aiosqlite

from .contracts import TERMINAL_RUN_STATUSES


DELIVERY_STAGE = "delivered_terminal_events"
TOMBSTONE_STAGE = "evaluation_tombstones"
CHECKPOINT_STAGE = "checkpoint_reachability"
BLOB_REF_STAGE = "blob_refs"
ORPHAN_STAGE = "orphan_grace"
RESERVATION_STAGE = "expired_target_reservations"
CLEANUP_STAGE_ORDER = (
    DELIVERY_STAGE,
    TOMBSTONE_STAGE,
    CHECKPOINT_STAGE,
    BLOB_REF_STAGE,
    ORPHAN_STAGE,
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


class ClockPort(Protocol):
    """Wall-clock port used for every retention boundary."""

    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Retention durations in seconds; product defaults belong to the caller."""

    terminal_seconds: float
    evaluation_tombstone_seconds: float
    orphan_grace_seconds: float

    def __post_init__(self) -> None:
        values = (
            self.terminal_seconds,
            self.evaluation_tombstone_seconds,
            self.orphan_grace_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in values
        ):
            raise ValueError("retention durations must be positive")
        if self.evaluation_tombstone_seconds < self.terminal_seconds:
            raise ValueError("evaluation/tombstone retention must cover terminal retention")

    @classmethod
    def from_days(
        cls,
        *,
        terminal_days: float,
        evaluation_tombstone_days: float,
        orphan_grace_hours: float,
    ) -> "RetentionPolicy":
        return cls(
            terminal_seconds=terminal_days * 24 * 60 * 60,
            evaluation_tombstone_seconds=evaluation_tombstone_days * 24 * 60 * 60,
            orphan_grace_seconds=orphan_grace_hours * 60 * 60,
        )


@dataclass(frozen=True, slots=True)
class RetentionStageResult:
    name: str
    candidates: tuple[str, ...] = ()
    applied: int = 0
    protected: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def planned(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True, slots=True)
class RetentionReport:
    dry_run: bool
    observed_at: float
    stages: tuple[RetentionStageResult, ...]

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self.stages)

    def stage(self, name: str) -> RetentionStageResult:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "observed_at": self.observed_at,
            "stages": [
                {
                    "name": stage.name,
                    "planned": stage.planned,
                    "applied": stage.applied,
                    "candidates": list(stage.candidates),
                    "protected": list(stage.protected),
                    "warnings": list(stage.warnings),
                }
                for stage in self.stages
            ],
        }


@dataclass(frozen=True, slots=True)
class _CheckpointKey:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str

    @property
    def diagnostic_id(self) -> str:
        return f"{self.thread_id}:{self.checkpoint_ns}:{self.checkpoint_id}"


@dataclass(frozen=True, slots=True)
class _CleanupPlan:
    full_run_ids: tuple[str, ...]
    final_run_ids: tuple[str, ...]
    expired_evaluation_ids: tuple[str, ...]
    protected: tuple[str, ...]
    event_ids: tuple[str, ...]
    delivery_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    effect_ids: tuple[str, ...]
    trace_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    checkpoint_keys: tuple[_CheckpointKey, ...]
    dangling_ref_owners: tuple[tuple[str, str], ...]


def _placeholders(values: Sequence[object]) -> str:
    return ",".join("?" for _ in values)


async def _column_values(
    db: aiosqlite.Connection,
    query: str,
    params: Sequence[object] = (),
) -> tuple[str, ...]:
    rows = await (await db.execute(query, tuple(params))).fetchall()
    return tuple(str(row[0]) for row in rows)


class WorkflowRetentionManager:
    """Apply the fixed delivery -> tombstone -> reachability -> ref -> grace order."""

    def __init__(
        self,
        db_path: str | Path,
        blob_root: str | Path,
        *,
        policy: RetentionPolicy,
        clock: ClockPort,
    ) -> None:
        self.db_path = Path(db_path)
        self.blob_root = Path(blob_root)
        self.policy = policy
        self.clock = clock

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def cleanup(self, *, dry_run: bool = False) -> RetentionReport:
        """Run retention in its normative order or return the same plan read-only."""

        now = float(self.clock.now())
        return await self._cleanup_at(now, dry_run=dry_run)

    async def _cleanup_at(self, now: float, *, dry_run: bool) -> RetentionReport:
        plan = await self._build_plan(now)
        stages = (
            await self._cleanup_deliveries(plan, dry_run=dry_run),
            await self._retain_tombstones(plan, dry_run=dry_run),
            await self._cleanup_checkpoints(plan, dry_run=dry_run),
            await self._cleanup_blob_refs(plan, dry_run=dry_run),
            await self._cleanup_orphans(now, plan=plan, dry_run=dry_run),
        )
        return RetentionReport(dry_run=dry_run, observed_at=now, stages=stages)

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

    async def _build_plan(self, now: float) -> _CleanupPlan:
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
                        {f'AND evaluation_id NOT IN ({_placeholders(expired_evaluations)})' if expired_evaluations else ''}
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
            dangling_ref_owners = await self._dangling_ref_owners(db)
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

    async def _cleanup_deliveries(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [*(f"delivery:{item}" for item in plan.delivery_ids), *(f"event:{item}" for item in plan.event_ids)]
        )
        applied = 0
        if items and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if plan.delivery_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_deliveries WHERE delivery_id IN ({_placeholders(plan.delivery_ids)})",
                        plan.delivery_ids,
                    )
                    applied += cursor.rowcount
                if plan.event_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_events WHERE event_id IN ({_placeholders(plan.event_ids)})",
                        plan.event_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(
            name=DELIVERY_STAGE,
            candidates=items,
            applied=applied,
            protected=plan.protected,
        )

    async def _retain_tombstones(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        retained = tuple(
            run_id for run_id in plan.full_run_ids if run_id not in set(plan.final_run_ids)
        )
        items = tuple(
            [
                *(f"evaluation:{item}" for item in plan.expired_evaluation_ids),
                *(f"tombstone:{item}" for item in retained),
                *(f"expired-tombstone:{item}" for item in plan.final_run_ids),
            ]
        )
        applied = 0
        if plan.full_run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if plan.expired_evaluation_ids:
                    cursor = await db.execute(
                        f"DELETE FROM evaluations WHERE evaluation_id IN ({_placeholders(plan.expired_evaluation_ids)})",
                        plan.expired_evaluation_ids,
                    )
                    applied += cursor.rowcount
                cursor = await db.execute(
                    f"""UPDATE workflow_runs SET active_nodes_json='[]',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,cancel_reason=NULL,error_json=NULL,
                    recovery_action=NULL WHERE run_id IN ({_placeholders(plan.full_run_ids)})""",
                    plan.full_run_ids,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=TOMBSTONE_STAGE, candidates=items, applied=applied)

    async def _cleanup_checkpoints(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(f"owner:{item}" for item in plan.full_run_ids),
                *(f"checkpoint:{key.diagnostic_id}" for key in plan.checkpoint_keys),
            ]
        )
        applied = 0
        if plan.full_run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"DELETE FROM workflow_checkpoint_owners WHERE run_id IN ({_placeholders(plan.full_run_ids)})",
                    plan.full_run_ids,
                )
                applied += cursor.rowcount
                for key in plan.checkpoint_keys:
                    params = (key.thread_id, key.checkpoint_ns, key.checkpoint_id)
                    cursor = await db.execute(
                        """DELETE FROM workflow_pending_writes
                        WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                    cursor = await db.execute(
                        """DELETE FROM workflow_checkpoint_effects
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                    cursor = await db.execute(
                        """DELETE FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=CHECKPOINT_STAGE, candidates=items, applied=applied)

    async def _cleanup_blob_refs(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        owner_groups = self._owner_groups(plan)
        ref_items = await self._matching_ref_items(owner_groups)
        items = tuple(
            [
                *ref_items,
                *(f"trace:{item}" for item in plan.trace_ids),
                *(f"node:{item}" for item in plan.node_ids),
                *(f"effect:{item}" for item in plan.effect_ids),
                *(f"run:{item}" for item in plan.final_run_ids),
            ]
        )
        applied = 0
        if any(owner_groups.values()) and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                for owner_kind, owner_ids in owner_groups.items():
                    if not owner_ids:
                        continue
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_blob_refs WHERE owner_kind=?
                        AND owner_id IN ({_placeholders(owner_ids)})""",
                        (owner_kind, *owner_ids),
                    )
                    applied += cursor.rowcount
                if plan.node_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_node_effects WHERE node_execution_id IN ({_placeholders(plan.node_ids)})",
                        plan.node_ids,
                    )
                    applied += cursor.rowcount
                if plan.effect_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_checkpoint_effects WHERE effect_id IN ({_placeholders(plan.effect_ids)})",
                        plan.effect_ids,
                    )
                    applied += cursor.rowcount
                if plan.full_run_ids:
                    for table in (
                        "trace_runs",
                        "workflow_nodes",
                        "workflow_decisions",
                        "workflow_effects",
                    ):
                        cursor = await db.execute(
                            f"DELETE FROM {table} WHERE run_id IN ({_placeholders(plan.full_run_ids)})",
                            plan.full_run_ids,
                        )
                        applied += cursor.rowcount
                if plan.final_run_ids:
                    placeholders = _placeholders(plan.final_run_ids)
                    for query in (
                        f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
                        f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
                    ):
                        cursor = await db.execute(query, plan.final_run_ids)
                        applied += cursor.rowcount
                    cursor = await db.execute(
                        f"DELETE FROM workflow_runs WHERE run_id IN ({placeholders})",
                        plan.final_run_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=BLOB_REF_STAGE, candidates=items, applied=applied)

    @staticmethod
    def _owner_groups(plan: _CleanupPlan) -> dict[str, tuple[str, ...]]:
        owner_groups = {
            "run": plan.full_run_ids,
            "workflow_run": plan.full_run_ids,
            "event": plan.event_ids,
            "workflow_event": plan.event_ids,
            "delivery": plan.delivery_ids,
            "workflow_delivery": plan.delivery_ids,
            "node": plan.node_ids,
            "workflow_node": plan.node_ids,
            "effect": plan.effect_ids,
            "workflow_effect": plan.effect_ids,
            "trace": plan.trace_ids,
            "trace_run": plan.trace_ids,
            "span": plan.span_ids,
            "trace_span": plan.span_ids,
            "evaluation": plan.expired_evaluation_ids,
        }
        checkpoint_owner_ids = tuple(
            {key.checkpoint_id for key in plan.checkpoint_keys}
            | {key.diagnostic_id for key in plan.checkpoint_keys}
        )
        owner_groups["checkpoint"] = checkpoint_owner_ids
        owner_groups["workflow_checkpoint"] = checkpoint_owner_ids
        for owner_kind, owner_id in plan.dangling_ref_owners:
            owner_groups[owner_kind] = tuple(
                sorted({*owner_groups.get(owner_kind, ()), owner_id})
            )
        return owner_groups

    async def _matching_ref_items(
        self, owner_groups: dict[str, tuple[str, ...]]
    ) -> tuple[str, ...]:
        db = await self._connect()
        try:
            items: list[str] = []
            for owner_kind, owner_ids in owner_groups.items():
                if not owner_ids:
                    continue
                rows = await (
                    await db.execute(
                        f"""SELECT sha256,owner_id FROM workflow_blob_refs WHERE owner_kind=?
                        AND owner_id IN ({_placeholders(owner_ids)}) ORDER BY sha256,owner_id""",
                        (owner_kind, *owner_ids),
                    )
                ).fetchall()
                items.extend(
                    f"ref:{owner_kind}:{row['owner_id']}:{row['sha256']}" for row in rows
                )
            return tuple(sorted(items))
        finally:
            await db.close()

    async def _cleanup_orphans(
        self, now: float, *, plan: _CleanupPlan, dry_run: bool
    ) -> RetentionStageResult:
        cutoff = now - self.policy.orphan_grace_seconds
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT b.sha256,b.relative_path,b.created_at,r.owner_kind,r.owner_id
                    FROM workflow_blobs b LEFT JOIN workflow_blob_refs r ON r.sha256=b.sha256
                    ORDER BY b.sha256,r.owner_kind,r.owner_id"""
                )
            ).fetchall()
            registered = await (
                await db.execute("SELECT sha256,relative_path FROM workflow_blobs")
            ).fetchall()
        finally:
            await db.close()

        warnings: list[str] = []
        database_files: list[tuple[str, Path]] = []
        registered_paths: set[Path] = set()
        for row in registered:
            path = self._safe_blob_path(str(row["relative_path"]))
            if path is None:
                warnings.append(f"invalid-blob-path:{row['sha256']}")
                continue
            registered_paths.add(path)
            if not path.exists():
                warnings.append(f"missing-blob-file:{row['sha256']}")

        removed_owners = {
            (owner_kind, owner_id)
            for owner_kind, owner_ids in self._owner_groups(plan).items()
            for owner_id in owner_ids
        }
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                str(row["sha256"]),
                {
                    "relative_path": str(row["relative_path"]),
                    "created_at": float(row["created_at"]),
                    "owners": [],
                },
            )
            if row["owner_kind"] is not None:
                item["owners"].append((str(row["owner_kind"]), str(row["owner_id"])))
        for digest, item in grouped.items():
            if item["created_at"] > cutoff:
                continue
            owners = item["owners"]
            if owners and (not dry_run or any(owner not in removed_owners for owner in owners)):
                continue
            path = self._safe_blob_path(item["relative_path"])
            if path is None:
                warnings.append(f"invalid-blob-path:{digest}")
                continue
            database_files.append((digest, path))

        filesystem_orphans: list[Path] = []
        if self.blob_root.exists():
            root = self.blob_root.resolve()
            for path in self.blob_root.rglob("*"):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    warnings.append(f"unsafe-symlink:{path.name}")
                    continue
                if resolved in registered_paths:
                    continue
                try:
                    if path.stat().st_mtime <= cutoff:
                        filesystem_orphans.append(resolved)
                except OSError as exc:
                    warnings.append(
                        f"stat-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )

        items = tuple(
            [
                *(f"blob:{digest}" for digest, _ in database_files),
                *(f"file:{self._diagnostic_path(path)}" for path in sorted(filesystem_orphans)),
            ]
        )
        applied = 0
        if not dry_run:
            if database_files:
                digests = tuple(digest for digest, _ in database_files)
                db = await self._connect()
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_blobs WHERE sha256 IN ({_placeholders(digests)})
                        AND NOT EXISTS(
                            SELECT 1 FROM workflow_blob_refs r
                            WHERE r.sha256=workflow_blobs.sha256
                        )""",
                        digests,
                    )
                    applied += cursor.rowcount
                    await db.commit()
                except BaseException:
                    if db.in_transaction:
                        await db.rollback()
                    raise
                finally:
                    await db.close()
                for _, path in database_files:
                    try:
                        existed = path.exists()
                        path.unlink(missing_ok=True)
                        applied += int(existed)
                    except OSError as exc:
                        warnings.append(
                            f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                        )
            for path in filesystem_orphans:
                try:
                    path.unlink(missing_ok=True)
                    applied += 1
                except OSError as exc:
                    warnings.append(
                        f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )
        return RetentionStageResult(
            name=ORPHAN_STAGE,
            candidates=items,
            applied=applied,
            warnings=tuple(warnings),
        )

    def _safe_blob_path(self, relative_path: str) -> Path | None:
        root = self.blob_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _diagnostic_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.blob_root.resolve()).as_posix()
        except ValueError:
            return "[outside-blob-root]"

    async def _reconcile_reservations(
        self, now: float, *, dry_run: bool
    ) -> RetentionStageResult:
        db = await self._connect()
        try:
            keys = await _column_values(
                db,
                """SELECT reservation_key FROM workflow_target_reservations
                WHERE status IN ('prepared','claimed')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at<=?
                ORDER BY reservation_key""",
                (now,),
            )
            applied = 0
            if keys and not dry_run:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_target_reservations
                    WHERE reservation_key IN ({_placeholders(keys)})
                      AND status IN ('prepared','claimed')
                      AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""",
                    (*keys, now),
                )
                applied = cursor.rowcount
                await db.commit()
            return RetentionStageResult(
                name=RESERVATION_STAGE,
                candidates=tuple(f"reservation:{key}" for key in keys),
                applied=applied,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()


__all__ = [
    "BLOB_REF_STAGE",
    "CHECKPOINT_STAGE",
    "CLEANUP_STAGE_ORDER",
    "ClockPort",
    "DELIVERY_STAGE",
    "ORPHAN_STAGE",
    "RESERVATION_STAGE",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionStageResult",
    "SystemClock",
    "TOMBSTONE_STAGE",
    "WorkflowRetentionManager",
]
