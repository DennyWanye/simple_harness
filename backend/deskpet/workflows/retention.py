"""Ordered retention and startup reconciliation for durable workflows."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, Sequence

import aiosqlite

from .contracts import TERMINAL_RUN_STATUSES


DELIVERY_STAGE = "delivered_terminal_events"
TOMBSTONE_STAGE = "evaluation_tombstones"
CHECKPOINT_STAGE = "checkpoint_reachability"
BLOB_REF_STAGE = "blob_refs"
ORPHAN_STAGE = "orphan_grace"
RESERVATION_STAGE = "expired_target_reservations"
CONTROL_STAGE = "expired_research_controls"
REACHABILITY_STAGE = "research_lineage_reachability"
LINEAGE_STAGE = "research_lineage_and_pins"
SNAPSHOT_STAGE = "research_snapshot_refs"
RUN_STAGE = "unreachable_research_runs"
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


class ResearchReachabilityView(Protocol):
    protected_run_ids: frozenset[str]
    protected_snapshot_hashes: frozenset[str]
    protected_pin_ids: frozenset[str]


class ResearchRetentionRepository(Protocol):
    async def expired_control_candidates(
        self, *, now: float | None = None
    ) -> dict[str, tuple[str, ...]]: ...

    async def lineage_reachability(
        self, *, now: float | None = None
    ) -> ResearchReachabilityView: ...


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


@dataclass(frozen=True, slots=True)
class _ResearchCleanupPlan:
    lineage_operation_ids: tuple[str, ...]
    pin_ids: tuple[str, ...]
    snapshot_hashes: tuple[str, ...]
    snapshot_blob_refs: tuple[tuple[str, str], ...]
    run_ids: tuple[str, ...]


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
        research_repository: ResearchRetentionRepository | None = None,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.blob_root = Path(blob_root)
        self.policy = policy
        self.clock = clock
        if research_repository is None:
            # Imported lazily so legacy workflow users do not acquire a new
            # module-level dependency or initialization side effect.
            from .store.research_repository import ResearchWorkflowRepository

            research_repository = ResearchWorkflowRepository(self.db_path, clock=clock.now)
        self.research_repository = research_repository
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

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
        if not await self._has_research_records():
            plan = await self._build_plan(now)
            stages = (
                await self._cleanup_deliveries(plan, dry_run=dry_run),
                await self._retain_tombstones(plan, dry_run=dry_run),
                await self._cleanup_checkpoints(plan, dry_run=dry_run),
                await self._cleanup_blob_refs(plan, dry_run=dry_run),
                await self._cleanup_orphans(now, plan=plan, dry_run=dry_run),
            )
            return RetentionReport(dry_run=dry_run, observed_at=now, stages=stages)

        # v6 continuation identity is a single-head connected component.  It
        # must be collected as one unit; the legacy staged collector below is
        # intentionally retained for v1-v5 historical recovery.
        v6_components = await self._cleanup_v6_components(now, dry_run=dry_run)
        control_candidates = await self.research_repository.expired_control_candidates(now=now)
        control = await self._cleanup_research_controls(
            control_candidates, dry_run=dry_run
        )
        reachability = await self.research_repository.lineage_reachability(now=now)
        plan = await self._build_research_aware_plan(now, reachability)
        research_plan = await self._build_research_cleanup_plan(now, plan, reachability)
        reachability_stage = RetentionStageResult(
            name=REACHABILITY_STAGE,
            protected=tuple(
                [
                    *(f"run:{item}" for item in sorted(reachability.protected_run_ids)),
                    *(f"snapshot:{item}" for item in sorted(reachability.protected_snapshot_hashes)),
                    *(f"pin:{item}" for item in sorted(reachability.protected_pin_ids)),
                ]
            ),
        )
        stages = (
            control,
            reachability_stage,
            await self._cleanup_deliveries(plan, dry_run=dry_run),
            await self._retain_tombstones(plan, dry_run=dry_run),
            await self._cleanup_checkpoints(plan, dry_run=dry_run),
            await self._cleanup_blob_refs(plan, dry_run=dry_run, delete_runs=False),
            await self._cleanup_research_lineage(research_plan, dry_run=dry_run),
            await self._cleanup_research_snapshots(research_plan, dry_run=dry_run),
            self._merge_stage(
                await self._cleanup_research_runs(research_plan, dry_run=dry_run),
                v6_components,
            ),
            await self._cleanup_orphans(
                now,
                plan=plan,
                dry_run=dry_run,
                extra_removed_owners=tuple(
                    ("research_snapshot", snapshot_hash)
                    for snapshot_hash in research_plan.snapshot_hashes
                ),
            ),
        )
        return RetentionReport(dry_run=dry_run, observed_at=now, stages=stages)

    @staticmethod
    def _merge_stage(
        legacy: RetentionStageResult, component: RetentionStageResult
    ) -> RetentionStageResult:
        return RetentionStageResult(
            name=legacy.name,
            candidates=tuple(sorted({*legacy.candidates, *component.candidates})),
            applied=legacy.applied + component.applied,
            protected=tuple(sorted({*legacy.protected, *component.protected})),
            warnings=tuple(sorted({*legacy.warnings, *component.warnings})),
        )

    async def _cleanup_v6_components(
        self, now: float, *, dry_run: bool
    ) -> RetentionStageResult:
        """Collect dead v6 continuation components in one SQLite transaction.

        The single-head row is the durable edge.  Reachability is deliberately
        undirected here: retaining either endpoint retains the complete chain.
        Legacy v1-v5 lineage remains on the historical staged collector.
        """

        db = await self._connect()
        deleted: list[str] = []
        protected: list[str] = []
        files: list[Path] = []
        try:
            if not dry_run:
                await db.execute("BEGIN IMMEDIATE")
            components = await self._v6_continuation_components(db)
            for run_ids in components:
                reasons = await self._v6_component_root_reasons(db, run_ids, now)
                label = "+".join(sorted(reasons))
                if reasons:
                    protected.extend(f"{run_id}:{label}" for run_id in run_ids)
                    continue
                deleted.extend(run_ids)
                if not dry_run:
                    files.extend(await self._delete_v6_component(db, run_ids))
            if not dry_run:
                await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

        warnings: list[str] = []
        if not dry_run:
            for path in sorted(set(files)):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    warnings.append(
                        f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )
        return RetentionStageResult(
            name=RUN_STAGE,
            candidates=tuple(f"run:{item}" for item in sorted(deleted)),
            applied=len(deleted) if not dry_run else 0,
            protected=tuple(sorted(protected)),
            warnings=tuple(warnings),
        )

    @staticmethod
    async def _v6_continuation_components(
        db: aiosqlite.Connection,
    ) -> tuple[tuple[str, ...], ...]:
        rows = await (
            await db.execute(
                """SELECT parent_run_id,child_run_id
                FROM workflow_research_continuation_heads
                ORDER BY parent_run_id,child_run_id"""
            )
        ).fetchall()
        adjacency: dict[str, set[str]] = {}
        for row in rows:
            parent = str(row["parent_run_id"])
            child = str(row["child_run_id"])
            adjacency.setdefault(parent, set()).add(child)
            adjacency.setdefault(child, set()).add(parent)
        result: list[tuple[str, ...]] = []
        unseen = set(adjacency)
        while unseen:
            start = min(unseen)
            stack = [start]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency.get(current, ()))
            unseen.difference_update(component)
            result.append(tuple(sorted(component)))
        return tuple(result)

    async def _v6_component_root_reasons(
        self,
        db: aiosqlite.Connection,
        run_ids: tuple[str, ...],
        now: float,
    ) -> set[str]:
        """Return live or fail-closed reasons which protect a component."""

        reasons: set[str] = set()
        placeholders = _placeholders(run_ids)
        runs = await (
            await db.execute(
                f"""SELECT run_id,workflow_name,workflow_version,status,ended_at
                FROM workflow_runs WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        if len(runs) != len(run_ids):
            return {"corrupt_component"}
        terminal_cutoff = now - self.policy.terminal_seconds
        for row in runs:
            if (
                str(row["workflow_name"]) != "deep_research"
                or str(row["workflow_version"]) != "v6"
            ):
                reasons.add("identity_mismatch")
            status = str(row["status"])
            if status not in {item.value for item in TERMINAL_RUN_STATUSES}:
                reasons.add("nonterminal")
            elif row["ended_at"] is None:
                reasons.add("corrupt_terminal")
            elif float(row["ended_at"]) > terminal_cutoff:
                reasons.add("terminal_window")

        active_control = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_run_control_commands
                WHERE run_id IN ({placeholders})
                  AND status IN ('open','accepted','observed','settled') LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        if active_control is not None:
            reasons.add("open_control")

        active_delivery = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_deliveries
                WHERE run_id IN ({placeholders}) AND required_durable=1
                  AND status NOT IN ('delivered','discarded')
                  AND NOT(status='failed' AND next_attempt_at IS NULL AND attempts>=5)
                LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        if active_delivery is not None:
            reasons.add("required_delivery")

        active_pin = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_research_snapshot_pins
                WHERE run_id IN ({placeholders})
                  AND (expires_at IS NULL OR expires_at>?) LIMIT 1""",
                (*run_ids, now),
            )
        ).fetchone()
        if active_pin is not None:
            reasons.add("snapshot_pin")

        if not await self._v6_component_closure_is_valid(db, run_ids):
            reasons.add("closure_invalid")
        if await self._v6_component_has_external_owner(db, run_ids):
            reasons.add("external_blob_owner")
        return reasons

    async def _v6_component_closure_is_valid(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> bool:
        placeholders = _placeholders(run_ids)
        heads = await (
            await db.execute(
                f"""SELECT * FROM workflow_research_continuation_heads
                WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})
                ORDER BY parent_run_id""",
                (*run_ids, *run_ids),
            )
        ).fetchall()
        for head in heads:
            if (
                str(head["parent_run_id"]) not in run_ids
                or str(head["child_run_id"]) not in run_ids
            ):
                return False
            spec = await (
                await db.execute(
                    "SELECT sha256,relative_path FROM workflow_blobs WHERE sha256=?",
                    (head["spec_blob_digest"],),
                )
            ).fetchone()
            if spec is None:
                return False
            spec_path = self._safe_blob_path(str(spec["relative_path"]))
            if spec_path is None or not spec_path.is_file():
                return False
            try:
                if hashlib.sha256(spec_path.read_bytes()).hexdigest() != str(spec["sha256"]):
                    return False
            except OSError:
                return False
            lineage = await (
                await db.execute(
                    """SELECT parent_run_id,parent_operation_id,snapshot_hash
                    FROM workflow_research_lineage
                    WHERE operation_id=? AND run_id=?""",
                    (head["child_operation_id"], head["child_run_id"]),
                )
            ).fetchone()
            if (
                lineage is None
                or str(lineage["parent_run_id"]) != str(head["parent_run_id"])
                or str(lineage["parent_operation_id"]) != str(head["parent_operation_id"])
                or str(lineage["snapshot_hash"]) != str(head["source_snapshot_hash"])
            ):
                return False
            snapshot = await (
                await db.execute(
                    """SELECT run_id,manifest_ref FROM workflow_research_snapshots
                    WHERE snapshot_hash=?""",
                    (head["source_snapshot_hash"],),
                )
            ).fetchone()
            if snapshot is None or str(snapshot["run_id"]) != str(head["parent_run_id"]):
                return False
            refs = await (
                await db.execute(
                    """SELECT b.sha256,b.relative_path FROM workflow_blob_refs r
                    JOIN workflow_blobs b ON b.sha256=r.sha256
                    WHERE r.owner_kind='research_snapshot' AND r.owner_id=?
                    ORDER BY b.sha256""",
                    (head["source_snapshot_hash"],),
                )
            ).fetchall()
            if not refs:
                return False
            manifest_ref = str(snapshot["manifest_ref"])
            manifest_digest = (
                manifest_ref[7:] if manifest_ref.startswith("sha256:") else manifest_ref
            )
            if manifest_digest not in {str(row["sha256"]) for row in refs}:
                return False
            manifest_row = next(
                (row for row in refs if str(row["sha256"]) == manifest_digest), None
            )
            if manifest_row is None:
                return False
            try:
                snapshot_value = json.loads(
                    self._safe_blob_path(str(manifest_row["relative_path"])).read_text(
                        encoding="utf-8"
                    )
                )
                from .store.research_repository import ResearchWorkflowRepository

                ResearchWorkflowRepository._validate_v6_snapshot(
                    snapshot_value, parent_run_id=str(head["parent_run_id"])
                )
                if snapshot_value.get("snapshot_hash") != str(head["source_snapshot_hash"]):
                    return False
                declared_closure = {
                    str(ref)[7:] for ref in snapshot_value.get("closure_refs", ())
                }
                if not declared_closure <= {str(row["sha256"]) for row in refs}:
                    return False
            except Exception:
                return False
            for row in refs:
                path = self._safe_blob_path(str(row["relative_path"]))
                if path is None or not path.is_file():
                    return False
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != str(row["sha256"]):
                        return False
                except OSError:
                    return False

        # Every component checkpoint must remain dereferenceable while it is
        # retained.  Missing owner/checkpoint edges are treated as corruption.
        missing_checkpoint = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_checkpoint_owners owner
                LEFT JOIN workflow_checkpoints checkpoint
                  ON checkpoint.thread_id=owner.thread_id
                 AND checkpoint.checkpoint_ns=owner.checkpoint_ns
                 AND checkpoint.checkpoint_id=owner.checkpoint_id
                WHERE owner.run_id IN ({placeholders})
                  AND checkpoint.checkpoint_id IS NULL LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        return missing_checkpoint is None

    async def _v6_component_owner_pairs(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> set[tuple[str, str]]:
        placeholders = _placeholders(run_ids)
        pairs: set[tuple[str, str]] = set()
        for kind in ("run", "workflow_run", "run_staging"):
            pairs.update((kind, run_id) for run_id in run_ids)
        table_kinds = (
            ("workflow_events", "event_id", ("event", "workflow_event")),
            ("workflow_deliveries", "delivery_id", ("delivery", "workflow_delivery")),
            ("workflow_nodes", "node_execution_id", ("node", "workflow_node")),
            ("workflow_effects", "effect_id", ("effect", "workflow_effect")),
            ("trace_runs", "trace_id", ("trace", "trace_run")),
            ("evaluations", "evaluation_id", ("evaluation",)),
        )
        for table, column, kinds in table_kinds:
            values = await _column_values(
                db,
                f"SELECT {column} FROM {table} WHERE run_id IN ({placeholders})",
                run_ids,
            )
            for kind in kinds:
                pairs.update((kind, value) for value in values)
        trace_ids = tuple(owner_id for kind, owner_id in pairs if kind == "trace")
        if trace_ids:
            span_ids = await _column_values(
                db,
                f"SELECT span_id FROM trace_spans WHERE trace_id IN ({_placeholders(trace_ids)})",
                trace_ids,
            )
            for kind in ("span", "trace_span"):
                pairs.update((kind, value) for value in span_ids)
        checkpoints = await (
            await db.execute(
                f"""SELECT thread_id,checkpoint_ns,checkpoint_id
                FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        for row in checkpoints:
            checkpoint_id = str(row["checkpoint_id"])
            diagnostic = f"{row['thread_id']}:{row['checkpoint_ns']}:{checkpoint_id}"
            pairs.update(
                {
                    ("checkpoint", checkpoint_id),
                    ("workflow_checkpoint", checkpoint_id),
                    ("checkpoint", diagnostic),
                    ("workflow_checkpoint", diagnostic),
                }
            )
        snapshots = await _column_values(
            db,
            f"SELECT snapshot_hash FROM workflow_research_snapshots WHERE run_id IN ({placeholders})",
            run_ids,
        )
        pairs.update(("research_snapshot", item) for item in snapshots)
        pending = await (
            await db.execute(
                "SELECT owner_id FROM workflow_blob_refs WHERE owner_kind='pending_task'"
            )
        ).fetchall()
        pairs.update(
            ("pending_task", str(row["owner_id"]))
            for row in pending
            if str(row["owner_id"]).split(":", 1)[0] in run_ids
        )
        return pairs

    async def _v6_component_has_external_owner(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> bool:
        owner_pairs = await self._v6_component_owner_pairs(db, run_ids)
        if not owner_pairs:
            return False
        owned_digests: set[str] = set()
        for kind, owner_id in owner_pairs:
            if kind not in {
                "checkpoint",
                "workflow_checkpoint",
                "delivery",
                "workflow_delivery",
            }:
                continue
            rows = await (
                await db.execute(
                    "SELECT sha256 FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                    (kind, owner_id),
                )
            ).fetchall()
            owned_digests.update(str(row[0]) for row in rows)
        for digest in owned_digests:
            rows = await (
                await db.execute(
                    "SELECT owner_kind,owner_id FROM workflow_blob_refs WHERE sha256=?",
                    (digest,),
                )
            ).fetchall()
            if any((str(row[0]), str(row[1])) not in owner_pairs for row in rows):
                return True

        # A checkpoint itself may be shared even when its payload has no
        # registered blob-ref row.
        placeholders = _placeholders(run_ids)
        shared_checkpoint = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_checkpoint_owners own
                JOIN workflow_checkpoint_owners external
                  ON external.thread_id=own.thread_id
                 AND external.checkpoint_ns=own.checkpoint_ns
                 AND external.checkpoint_id=own.checkpoint_id
                WHERE own.run_id IN ({placeholders})
                  AND external.run_id NOT IN ({placeholders}) LIMIT 1""",
                (*run_ids, *run_ids),
            )
        ).fetchone()
        return shared_checkpoint is not None

    async def _delete_v6_component(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> tuple[Path, ...]:
        """Delete one already-revalidated component in the contractual order."""

        placeholders = _placeholders(run_ids)
        checkpoint_rows = await (
            await db.execute(
                f"""SELECT thread_id,checkpoint_ns,checkpoint_id
                FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        effect_ids = await _column_values(
            db,
            f"SELECT effect_id FROM workflow_effects WHERE run_id IN ({placeholders})",
            run_ids,
        )
        node_ids = await _column_values(
            db,
            f"SELECT node_execution_id FROM workflow_nodes WHERE run_id IN ({placeholders})",
            run_ids,
        )
        event_ids = await _column_values(
            db,
            f"SELECT event_id FROM workflow_events WHERE run_id IN ({placeholders})",
            run_ids,
        )
        delivery_ids = await _column_values(
            db,
            f"SELECT delivery_id FROM workflow_deliveries WHERE run_id IN ({placeholders})",
            run_ids,
        )
        trace_ids = await _column_values(
            db,
            f"SELECT trace_id FROM trace_runs WHERE run_id IN ({placeholders})",
            run_ids,
        )
        evaluation_ids = await _column_values(
            db,
            f"SELECT evaluation_id FROM evaluations WHERE run_id IN ({placeholders})",
            run_ids,
        )
        snapshots = await _column_values(
            db,
            f"SELECT snapshot_hash FROM workflow_research_snapshots WHERE run_id IN ({placeholders})",
            run_ids,
        )
        owner_pairs = await self._v6_component_owner_pairs(db, run_ids)
        candidate_digests: set[str] = set()
        for kind, owner_id in owner_pairs:
            rows = await (
                await db.execute(
                    "SELECT sha256 FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                    (kind, owner_id),
                )
            ).fetchall()
            candidate_digests.update(str(row[0]) for row in rows)
        head_rows = await (
            await db.execute(
                f"""SELECT spec_blob_digest FROM workflow_research_continuation_heads
                WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})""",
                (*run_ids, *run_ids),
            )
        ).fetchall()
        candidate_digests.update(str(row[0]) for row in head_rows)

        # checkpoint/effect/pending/owner refs -> checkpoint/effect records
        for row in checkpoint_rows:
            params = (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
            await db.execute(
                """DELETE FROM workflow_checkpoint_effects
                WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                params,
            )
            await db.execute(
                """DELETE FROM workflow_pending_writes
                WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?""",
                params,
            )
        await db.execute(
            f"DELETE FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})",
            run_ids,
        )
        for kind, owner_id in owner_pairs:
            if kind in {"research_snapshot", "run_staging"}:
                continue
            await db.execute(
                "DELETE FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                (kind, owner_id),
            )
        for row in checkpoint_rows:
            params = (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
            await db.execute(
                """DELETE FROM workflow_checkpoints
                WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                  AND NOT EXISTS(
                    SELECT 1 FROM workflow_checkpoint_owners owner
                    WHERE owner.thread_id=workflow_checkpoints.thread_id
                      AND owner.checkpoint_ns=workflow_checkpoints.checkpoint_ns
                      AND owner.checkpoint_id=workflow_checkpoints.checkpoint_id
                  )""",
                params,
            )
        if effect_ids:
            effect_placeholders = _placeholders(effect_ids)
            await db.execute(
                f"DELETE FROM workflow_checkpoint_effects WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_effect_attempt_heads WHERE canonical_effect_id IN ({effect_placeholders}) OR run_id IN ({placeholders})",
                (*effect_ids, *run_ids),
            )
            await db.execute(
                f"DELETE FROM workflow_effect_targets WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_research_resource_reservations WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_effect_budget_reservations WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
        if node_ids:
            await db.execute(
                f"DELETE FROM workflow_node_effects WHERE node_execution_id IN ({_placeholders(node_ids)})",
                node_ids,
            )
        await db.execute(
            f"DELETE FROM workflow_effects WHERE run_id IN ({placeholders})", run_ids
        )
        await db.execute(
            f"DELETE FROM workflow_nodes WHERE run_id IN ({placeholders})", run_ids
        )
        await self._fault("retention.v6.after_checkpoint_data")

        # head -> pins -> descendant-to-root lineage
        await db.execute(
            f"""DELETE FROM workflow_research_continuation_heads
            WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})""",
            (*run_ids, *run_ids),
        )
        await db.execute(
            f"""DELETE FROM workflow_blob_refs
            WHERE owner_kind='run_staging' AND owner_id IN ({placeholders})""",
            run_ids,
        )
        await self._fault("retention.v6.after_heads")
        await db.execute(
            f"DELETE FROM workflow_research_snapshot_pins WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await self._fault("retention.v6.after_pins")
        lineage_rows = await (
            await db.execute(
                f"""SELECT operation_id,parent_operation_id FROM workflow_research_lineage
                WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        by_operation = {str(row["operation_id"]): row for row in lineage_rows}

        def depth(operation_id: str) -> int:
            current = operation_id
            seen: set[str] = set()
            value = 0
            while current in by_operation and current not in seen:
                seen.add(current)
                parent = by_operation[current]["parent_operation_id"]
                if parent is None:
                    break
                current = str(parent)
                value += 1
            return value

        for operation_id in sorted(by_operation, key=lambda item: (-depth(item), item)):
            await db.execute(
                "DELETE FROM workflow_research_lineage WHERE operation_id=?",
                (operation_id,),
            )
        await self._fault("retention.v6.after_lineage")

        # component-wide start/audit operations, requests, and session refs
        await db.execute(
            f"DELETE FROM workflow_operations WHERE run_id IN ({placeholders})", run_ids
        )
        await self._fault("retention.v6.after_operations")
        await db.execute(
            f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await self._fault("retention.v6.after_start_requests")
        await db.execute(
            f"DELETE FROM workflow_session_refs WHERE run_id IN ({placeholders})", run_ids
        )
        await self._fault("retention.v6.after_session_refs")

        # Snapshots own the inherited closure.  Release it only after lineage.
        if snapshots:
            snapshot_placeholders = _placeholders(snapshots)
            await db.execute(
                f"""DELETE FROM workflow_blob_refs
                WHERE owner_kind='research_snapshot'
                  AND owner_id IN ({snapshot_placeholders})""",
                snapshots,
            )
            await db.execute(
                f"DELETE FROM workflow_research_snapshots WHERE snapshot_hash IN ({snapshot_placeholders})",
                snapshots,
            )
        await self._fault("retention.v6.after_snapshots")

        # Remove every remaining component-owned non-FK row before run rows.
        if delivery_ids:
            await db.execute(
                f"DELETE FROM workflow_deliveries WHERE delivery_id IN ({_placeholders(delivery_ids)})",
                delivery_ids,
            )
        if event_ids:
            await db.execute(
                f"DELETE FROM workflow_events WHERE event_id IN ({_placeholders(event_ids)})",
                event_ids,
            )
        await db.execute(
            f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_receipt_ledger WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_fork_requests WHERE source_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})",
            (*run_ids, *run_ids),
        )
        if evaluation_ids:
            evaluation_placeholders = _placeholders(evaluation_ids)
            await db.execute(
                f"DELETE FROM eval_results WHERE evaluation_id IN ({evaluation_placeholders}) OR run_id IN ({placeholders})",
                (*evaluation_ids, *run_ids),
            )
            await db.execute(
                f"DELETE FROM evaluations WHERE evaluation_id IN ({evaluation_placeholders})",
                evaluation_ids,
            )
        else:
            await db.execute(
                f"DELETE FROM eval_results WHERE run_id IN ({placeholders})", run_ids
            )
        if trace_ids:
            await db.execute(
                f"DELETE FROM trace_runs WHERE trace_id IN ({_placeholders(trace_ids)})",
                trace_ids,
            )
        await db.execute(
            f"DELETE FROM workflow_decisions WHERE run_id IN ({placeholders})", run_ids
        )
        await db.execute(
            f"DELETE FROM workflow_run_control_commands WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_research_deadlines WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_research_resource_budgets WHERE run_id IN ({placeholders})",
            run_ids,
        )

        paths: list[Path] = []
        for digest in sorted(candidate_digests):
            row = await (
                await db.execute(
                    "SELECT relative_path FROM workflow_blobs WHERE sha256=?",
                    (digest,),
                )
            ).fetchone()
            if row is None:
                continue
            cursor = await db.execute(
                """DELETE FROM workflow_blobs WHERE sha256=?
                  AND NOT EXISTS(SELECT 1 FROM workflow_blob_refs WHERE sha256=?)
                  AND NOT EXISTS(
                    SELECT 1 FROM workflow_research_continuation_heads
                    WHERE spec_blob_digest=?
                  )""",
                (digest, digest, digest),
            )
            if cursor.rowcount:
                path = self._safe_blob_path(str(row["relative_path"]))
                if path is not None:
                    paths.append(path)
        await self._fault("retention.v6.after_blobs")

        # Run rows are last, descendants before the root.
        parent_rows = await (
            await db.execute(
                f"SELECT run_id,parent_run_id FROM workflow_runs WHERE run_id IN ({placeholders})",
                run_ids,
            )
        ).fetchall()
        parent_map = {
            str(row["run_id"]): (
                None if row["parent_run_id"] is None else str(row["parent_run_id"])
            )
            for row in parent_rows
        }

        def run_depth(run_id: str) -> int:
            current = run_id
            seen: set[str] = set()
            value = 0
            while parent_map.get(current) in parent_map and current not in seen:
                seen.add(current)
                current = str(parent_map[current])
                value += 1
            return value

        ordered = sorted(run_ids, key=lambda item: (-run_depth(item), item))
        for run_id in ordered[:-1]:
            await db.execute("DELETE FROM workflow_runs WHERE run_id=?", (run_id,))
        await self._fault("retention.v6.after_children")
        if ordered:
            await db.execute("DELETE FROM workflow_runs WHERE run_id=?", (ordered[-1],))
        await self._fault("retention.v6.after_root")
        return tuple(paths)

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
        provisional = await self._build_plan(
            now, extra_protected_run_ids=frozenset(protected)
        )
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
            str(row["run_id"])
            for row in lineage_rows
            if str(row["run_id"]) not in final
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
        return await self._build_plan(
            now, extra_protected_run_ids=frozenset(protected)
        )

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
            str(row["operation_id"])
            for row in lineage_rows
            if str(row["run_id"]) in final
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

        lineage_order = tuple(
            sorted(lineage_candidates, key=lambda item: (-depth(item), item))
        )
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
            if run is None or run_id in cleanup_run_ids or str(run["status"]) in TERMINAL_RUN_STATUSES:
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
        self, plan: _CleanupPlan, *, dry_run: bool, delete_runs: bool = True
    ) -> RetentionStageResult:
        owner_groups = self._owner_groups(plan)
        ref_items = await self._matching_ref_items(owner_groups)
        items = tuple(
            [
                *ref_items,
                *(f"trace:{item}" for item in plan.trace_ids),
                *(f"node:{item}" for item in plan.node_ids),
                *(f"effect:{item}" for item in plan.effect_ids),
                *(f"run:{item}" for item in plan.final_run_ids if delete_runs),
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
                if plan.final_run_ids and delete_runs:
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

    async def _cleanup_research_controls(
        self,
        candidates: dict[str, tuple[str, ...]],
        *,
        dry_run: bool,
    ) -> RetentionStageResult:
        cleanup_ids = tuple(sorted(candidates.get("cleanup_eligible", ())))
        overdue = tuple(sorted(candidates.get("settle_deadline_exceeded", ())))
        applied = 0
        if cleanup_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_run_control_commands
                    WHERE command_id IN ({_placeholders(cleanup_ids)})
                      AND status IN ('consumed','rejected','expired')""",
                    cleanup_ids,
                )
                applied = cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(
            name=CONTROL_STAGE,
            candidates=tuple(f"command:{item}" for item in cleanup_ids),
            applied=applied,
            protected=tuple(f"settle-overdue:{item}" for item in overdue),
        )

    async def _cleanup_research_lineage(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(f"lineage:{item}" for item in plan.lineage_operation_ids),
                *(f"pin:{item}" for item in plan.pin_ids),
            ]
        )
        applied = 0
        if items and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                for operation_id in plan.lineage_operation_ids:
                    cursor = await db.execute(
                        "DELETE FROM workflow_research_lineage WHERE operation_id=?",
                        (operation_id,),
                    )
                    applied += cursor.rowcount
                if plan.pin_ids:
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_research_snapshot_pins
                        WHERE pin_id IN ({_placeholders(plan.pin_ids)})""",
                        plan.pin_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=LINEAGE_STAGE, candidates=items, applied=applied)

    async def _cleanup_research_snapshots(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(
                    f"snapshot-ref:{snapshot_hash}:{blob_ref}"
                    for snapshot_hash, blob_ref in plan.snapshot_blob_refs
                ),
                *(f"snapshot:{item}" for item in plan.snapshot_hashes),
            ]
        )
        applied = 0
        if plan.snapshot_hashes and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_blob_refs
                    WHERE owner_kind='research_snapshot'
                      AND owner_id IN ({_placeholders(plan.snapshot_hashes)})""",
                    plan.snapshot_hashes,
                )
                applied += cursor.rowcount
                cursor = await db.execute(
                    f"""DELETE FROM workflow_research_snapshots
                    WHERE snapshot_hash IN ({_placeholders(plan.snapshot_hashes)})""",
                    plan.snapshot_hashes,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=SNAPSHOT_STAGE, candidates=items, applied=applied)

    async def _cleanup_research_runs(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(f"run:{item}" for item in plan.run_ids)
        applied = 0
        if plan.run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                placeholders = _placeholders(plan.run_ids)
                for query in (
                    f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
                    f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
                ):
                    cursor = await db.execute(query, plan.run_ids)
                    applied += cursor.rowcount
                cursor = await db.execute(
                    f"DELETE FROM workflow_runs WHERE run_id IN ({placeholders})",
                    plan.run_ids,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=RUN_STAGE, candidates=items, applied=applied)

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
        self,
        now: float,
        *,
        plan: _CleanupPlan,
        dry_run: bool,
        extra_removed_owners: tuple[tuple[str, str], ...] = (),
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
            continuation_spec_digests = {
                str(row[0])
                for row in await (
                    await db.execute(
                        "SELECT spec_blob_digest FROM workflow_research_continuation_heads"
                    )
                ).fetchall()
            }
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
        removed_owners.update(extra_removed_owners)
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
            if digest in continuation_spec_digests:
                continue
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
                        ) AND NOT EXISTS(
                            SELECT 1 FROM workflow_research_continuation_heads h
                            WHERE h.spec_blob_digest=workflow_blobs.sha256
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
    "CONTROL_STAGE",
    "ClockPort",
    "DELIVERY_STAGE",
    "ORPHAN_STAGE",
    "LINEAGE_STAGE",
    "REACHABILITY_STAGE",
    "RESERVATION_STAGE",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionStageResult",
    "RUN_STAGE",
    "SNAPSHOT_STAGE",
    "SystemClock",
    "TOMBSTONE_STAGE",
    "WorkflowRetentionManager",
]
