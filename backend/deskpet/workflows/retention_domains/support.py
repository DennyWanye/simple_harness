"""Shared helpers for the split class."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import aiosqlite

RUN_STAGE = "unreachable_research_runs"


class ResearchReachabilityView(Protocol):
    protected_run_ids: frozenset[str]
    protected_snapshot_hashes: frozenset[str]
    protected_pin_ids: frozenset[str]


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
