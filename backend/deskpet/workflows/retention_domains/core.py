"""Construction, connection, cleanup entry points and stage merging."""

from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import aiosqlite

from .support import (
    ResearchReachabilityView,
    RetentionReport,
    RetentionStageResult,
    _CleanupPlan,
    _ResearchCleanupPlan,
)

REACHABILITY_STAGE = "research_lineage_reachability"


class ClockPort(Protocol):
    """Wall-clock port used for every retention boundary."""

    def now(self) -> float: ...


class ResearchRetentionRepository(Protocol):
    async def expired_control_candidates(
        self, *, now: float | None = None
    ) -> dict[str, tuple[str, ...]]: ...

    async def lineage_reachability(
        self, *, now: float | None = None
    ) -> ResearchReachabilityView: ...


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
    ) -> RetentionPolicy:
        return cls(
            terminal_seconds=terminal_days * 24 * 60 * 60,
            evaluation_tombstone_seconds=evaluation_tombstone_days * 24 * 60 * 60,
            orphan_grace_seconds=orphan_grace_hours * 60 * 60,
        )


class RetentionCore:
    """Construction, connection, cleanup entry points and stage merging."""

    if TYPE_CHECKING:

        async def _build_plan(
            self, now: float, *, extra_protected_run_ids: frozenset[str] = ...
        ) -> _CleanupPlan: ...
        async def _build_research_aware_plan(
            self, now: float, reachability: ResearchReachabilityView
        ) -> _CleanupPlan: ...
        async def _build_research_cleanup_plan(
            self, now: float, plan: _CleanupPlan, reachability: ResearchReachabilityView
        ) -> _ResearchCleanupPlan: ...
        async def _cleanup_blob_refs(
            self, plan: _CleanupPlan, *, dry_run: bool, delete_runs: bool = ...
        ) -> RetentionStageResult: ...
        async def _cleanup_checkpoints(
            self, plan: _CleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_deliveries(
            self, plan: _CleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_orphans(
            self,
            now: float,
            *,
            plan: _CleanupPlan,
            dry_run: bool,
            extra_removed_owners: tuple[tuple[str, str], ...] = ...,
        ) -> RetentionStageResult: ...
        async def _cleanup_research_controls(
            self, candidates: dict[str, tuple[str, ...]], *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_research_lineage(
            self, plan: _ResearchCleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_research_runs(
            self, plan: _ResearchCleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_research_snapshots(
            self, plan: _ResearchCleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _cleanup_v6_components(
            self, now: float, *, dry_run: bool
        ) -> RetentionStageResult: ...
        def _diagnostic_path(self, path: Path) -> str: ...
        async def _has_research_records(self) -> bool: ...
        async def _reconcile_reservations(
            self, now: float, *, dry_run: bool
        ) -> RetentionStageResult: ...
        async def _retain_tombstones(
            self, plan: _CleanupPlan, *, dry_run: bool
        ) -> RetentionStageResult: ...
        def _safe_blob_path(self, relative_path: str) -> Path | None: ...

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
            from ..store.research_repository import ResearchWorkflowRepository

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
        control = await self._cleanup_research_controls(control_candidates, dry_run=dry_run)
        reachability = await self.research_repository.lineage_reachability(now=now)
        plan = await self._build_research_aware_plan(now, reachability)
        research_plan = await self._build_research_cleanup_plan(now, plan, reachability)
        reachability_stage = RetentionStageResult(
            name=REACHABILITY_STAGE,
            protected=tuple(
                [
                    *(f"run:{item}" for item in sorted(reachability.protected_run_ids)),
                    *(
                        f"snapshot:{item}"
                        for item in sorted(reachability.protected_snapshot_hashes)
                    ),
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
