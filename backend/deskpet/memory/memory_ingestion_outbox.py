# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 4 — terminal-commit Memory ingestion outbox worker + the single background lane.

``memory_ingestion_outbox`` rows are written by ``ForegroundQueueStore.record_sdk_terminal``
in the Host terminal transaction (design-freeze §5).  This worker:

1. **claims** one row under a lease (``lease_owner`` / ``lease_expires_at``; a
   ``claimed`` row whose lease expired is reclaimable, state monotonic
   ``pending → claimed → delivered | dead_letter``, ``claimed → pending`` only on
   retry);
2. re-reads the Host-durable sanitized envelope/receipt of every linked evidence
   (never re-wraps) and calls exact Memory 0.6.1
   ``ingest_committed_evidence(envelope, receipt, analysis_lineage=AnalysisLineage(...))``
   with the lineage frozen in the outbox row — Memory is idempotent per
   ``source_ref`` so a duplicate delivery returns the same receipt;
3. writes the ingestion receipt(s) back into ``memory_ingestion_outbox.receipt_json``
   (state ``delivered``).  The Host ``task_scope_events`` recorder only accepts
   ``host.turn|file|test`` kinds, so the receipt lives in the outbox row (joined to
   its evidence through ``memory_ingestion_evidence_links``) instead of a new
   event kind — the deliberate choice noted in ARCHITECTURE.
4. on failure: ``attempts + 1`` and a bounded exponential retry (``retry_delays``)
   → ``dead_letter`` + ``last_error``.  Raw Host evidence is never touched.

``MemoryAnalysisLane`` is the one background lane (min-4): each tick drives the
ingestion worker, then ``DurableMemoryJobRunner.run_once()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_json

log = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
MAX_RETRY_DELAY_SECONDS = 60.0
# Task 4 review F-4: evidence_ids_json ≠ memory_ingestion_evidence_links.
LINKS_MISMATCH_ERROR = "memory_ingestion_outbox_links_mismatch"


class OutboxRunOutcome(StrEnum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    outbox_id: str
    host_run_id: str
    sdk_run_id: str
    turn_id: str
    subject: str
    evidence_ids: tuple[str, ...]
    analysis_lineage: dict[str, Any]
    attempts: int
    lease_owner: str


class MemoryIngestionOutboxWorker:
    """Claim → ingest (Host-durable envelope/receipt) → receipt write-back; bounded retry → dead-letter."""

    def __init__(
        self,
        db_path: str | Path,
        manager_getter: Callable[[], Any],
        *,
        owner_id: str,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = 60.0,
        max_attempts: int = 5,
        retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
        fault_inject: Callable[[str], None] | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("memory_ingestion_outbox_config_invalid")
        self._db_path = Path(db_path)
        self._manager_getter = manager_getter
        self._owner_id = str(owner_id)
        self._clock = clock
        self._lease_seconds = float(lease_seconds)
        self._max_attempts = int(max_attempts)
        self._retry_delays = tuple(float(v) for v in retry_delays)
        self._fault_inject = fault_inject
        from deskpet.memory.evidence_authority import HostEvidenceAuthority

        self._evidence = HostEvidenceAuthority(self._db_path)

    # -- db ----------------------------------------------------------------

    def _connect(self):  # type: ignore[no-untyped-def]
        return aiosqlite.connect(self._db_path)

    def _fault(self, point: str) -> None:
        if self._fault_inject is not None:
            self._fault_inject(point)

    def _retry_delay(self, attempts: int) -> float:
        if not self._retry_delays:
            return 0.0
        index = min(max(attempts - 1, 0), len(self._retry_delays) - 1)
        return min(self._retry_delays[index], MAX_RETRY_DELAY_SECONDS)

    async def claim(self) -> OutboxClaim | None:
        """Claim the oldest deliverable row: pending & due, or claimed with an expired lease."""

        now = float(self._clock())
        token = f"{self._owner_id}:{uuid.uuid4().hex}"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                cursor = await db.execute(
                    "SELECT * FROM memory_ingestion_outbox WHERE "
                    "(state='pending' AND (lease_expires_at IS NULL OR lease_expires_at<=?)) "
                    "OR (state='claimed' AND lease_expires_at<=?) "
                    "ORDER BY created_at, outbox_id LIMIT 1",
                    (now, now),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    await db.commit()
                    return None
                outbox_id = str(row["outbox_id"])
                cursor = await db.execute(
                    "SELECT evidence_id FROM memory_ingestion_evidence_links WHERE outbox_id=? ORDER BY evidence_id",
                    (outbox_id,),
                )
                links = [str(r[0]) for r in await cursor.fetchall()]
                await cursor.close()
                declared = sorted(str(v) for v in json.loads(str(row["evidence_ids_json"])))
                if declared != sorted(links):
                    # Task 4 review F-4: an inconsistent row (evidence_ids_json ≠
                    # links) can never deliver.  It is judged INSIDE the claim
                    # transaction, bounded like any other failure: each detection
                    # consumes one attempt and the row goes back to pending with
                    # ``last_error`` until ``max_attempts`` → dead_letter — never an
                    # exception that leaves it claimed and endlessly reclaimable.
                    attempts = int(row["attempts"]) + 1
                    error = LINKS_MISMATCH_ERROR
                    if attempts >= self._max_attempts:
                        await db.execute(
                            "UPDATE memory_ingestion_outbox SET state='dead_letter',attempts=?,lease_owner=NULL,"
                            "lease_expires_at=NULL,last_error=?,updated_at=? WHERE outbox_id=?",
                            (attempts, error, now, outbox_id),
                        )
                        log.warning(
                            "memory_ingestion_outbox_dead_letter outbox_id=%s error=%s", outbox_id, error
                        )
                    else:
                        delay = self._retry_delay(attempts)
                        await db.execute(
                            "UPDATE memory_ingestion_outbox SET state='pending',attempts=?,lease_owner=?,"
                            "lease_expires_at=?,last_error=?,updated_at=? WHERE outbox_id=?",
                            (
                                attempts,
                                f"retry:{token}" if delay > 0 else None,
                                now + delay if delay > 0 else None,
                                error, now, outbox_id,
                            ),
                        )
                    await db.commit()
                    return None
                if str(row["state"]) == "claimed":
                    # Lease reclaim: the previous owner's claim expired; every claim
                    # consumes one bounded attempt.
                    await db.execute(
                        "UPDATE memory_ingestion_outbox SET attempts=attempts+1,lease_owner=?,lease_expires_at=?,"
                        "updated_at=? WHERE outbox_id=? AND state='claimed'",
                        (token, now + self._lease_seconds, now, outbox_id),
                    )
                else:
                    await db.execute(
                        "UPDATE memory_ingestion_outbox SET state='claimed',attempts=attempts+1,lease_owner=?,"
                        "lease_expires_at=?,updated_at=? WHERE outbox_id=? AND state='pending'",
                        (token, now + self._lease_seconds, now, outbox_id),
                    )
                cursor = await db.execute(
                    "SELECT * FROM memory_ingestion_outbox WHERE outbox_id=? AND lease_owner=?",
                    (outbox_id, token),
                )
                claimed = await cursor.fetchone()
                await cursor.close()
                self._fault("outbox.claim.before_commit")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        if claimed is None:
            return None
        evidence_ids = tuple(str(v) for v in json.loads(str(claimed["evidence_ids_json"])))
        return OutboxClaim(
            outbox_id=outbox_id,
            host_run_id=str(claimed["host_run_id"]),
            sdk_run_id=str(claimed["sdk_run_id"]),
            turn_id=str(claimed["turn_id"]),
            subject=str(claimed["subject"]),
            evidence_ids=evidence_ids,
            analysis_lineage=json.loads(str(claimed["analysis_lineage_json"])),
            attempts=int(claimed["attempts"]),
            lease_owner=token,
        )

    async def _settle(self, claim: OutboxClaim, *, state: str, receipt_json: str | None, error: str | None) -> bool:
        now = float(self._clock())
        async with self._connect() as db:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                if state == "pending":
                    delay = self._retry_delay(claim.attempts)
                    cursor = await db.execute(
                        "UPDATE memory_ingestion_outbox SET state='pending',lease_owner=NULL,lease_expires_at=NULL,"
                        "last_error=?,updated_at=? WHERE outbox_id=? AND state='claimed' AND lease_owner=?",
                        (error, now, claim.outbox_id, claim.lease_owner),
                    )
                    if cursor.rowcount == 1 and delay > 0:
                        # `lease_expires_at` doubles as "not before" for a pending retry
                        # (CHECK: owner/expiry NULL together → keep a synthetic owner).
                        await db.execute(
                            "UPDATE memory_ingestion_outbox SET lease_owner=?,lease_expires_at=? WHERE outbox_id=?",
                            (f"retry:{claim.lease_owner}", now + delay, claim.outbox_id),
                        )
                else:
                    cursor = await db.execute(
                        "UPDATE memory_ingestion_outbox SET state=?,lease_owner=NULL,lease_expires_at=NULL,"
                        "receipt_json=?,last_error=?,updated_at=? "
                        "WHERE outbox_id=? AND state='claimed' AND lease_owner=?",
                        (state, receipt_json, error, now, claim.outbox_id, claim.lease_owner),
                    )
                self._fault("outbox.settle.before_commit")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return cursor.rowcount == 1

    # -- deliver -------------------------------------------------------------

    async def deliver(self, claim: OutboxClaim) -> list[dict[str, Any]]:
        """Ingest every linked evidence with the frozen lineage; returns public receipt views."""

        from simple_harness_memory.core.jobs import AnalysisLineage

        manager = self._manager_getter()
        if asyncio.iscoroutine(manager):
            manager = await manager
        lineage = AnalysisLineage.from_json(claim.analysis_lineage)
        receipts: list[dict[str, Any]] = []
        for evidence_id in claim.evidence_ids:
            envelope, receipt = await self._evidence.read_admitted(evidence_id)
            if envelope.subject != claim.subject:
                raise RuntimeError("memory_ingestion_evidence_subject_mismatch")
            ingested = await manager.ingest_committed_evidence(envelope, receipt, analysis_lineage=lineage)
            receipts.append(_receipt_view(ingested))
        return receipts

    async def run_once(self) -> OutboxRunOutcome:
        claim = await self.claim()
        if claim is None:
            return OutboxRunOutcome.IDLE
        # Crash seams (fault matrix): a kill here leaves the row `claimed` under its
        # lease; the next owner reclaims after expiry and replays the idempotent ingest.
        self._fault("outbox.before_ingest")
        try:
            receipts = await self.deliver(claim)
        except Exception as exc:  # noqa: BLE001 - bounded retry taxonomy
            error = f"{type(exc).__name__}:{str(exc)[:200]}"
            if claim.attempts >= self._max_attempts:
                await self._settle(claim, state="dead_letter", receipt_json=None, error=error)
                log.warning("memory_ingestion_outbox_dead_letter outbox_id=%s error=%s", claim.outbox_id, error)
                return OutboxRunOutcome.DEAD_LETTER
            await self._settle(claim, state="pending", receipt_json=None, error=error)
            return OutboxRunOutcome.RETRY_SCHEDULED
        self._fault("outbox.before_deliver_commit")
        payload = {"schema_version": 1, "kind": "memory.ingestion.receipt", "receipts": receipts}
        settled = await self._settle(claim, state="delivered", receipt_json=canonical_json(payload), error=None)
        if not settled:
            # Lease lost between ingest and write-back: the next owner replays the
            # same (idempotent) ingest and writes the same receipt.
            return OutboxRunOutcome.RETRY_SCHEDULED
        return OutboxRunOutcome.DELIVERED

    async def counts(self) -> dict[str, int]:
        async with self._connect() as db:
            cursor = await db.execute("SELECT state, COUNT(*) FROM memory_ingestion_outbox GROUP BY state")
            rows = await cursor.fetchall()
            await cursor.close()
        return {str(state): int(count) for state, count in rows}


def _receipt_view(ingested: Any) -> dict[str, Any]:
    to_json = getattr(ingested, "to_json", None)
    if callable(to_json):
        raw = to_json()
        if isinstance(raw, dict):
            return json.loads(canonical_json(raw))
    view: dict[str, Any] = {}
    for name in (
        "receipt_id", "evidence_id", "source_ref", "source_hash", "sanitized_hash", "envelope_hash",
        "admission_receipt_id", "admission_receipt_hash", "mutation_job_id", "outbox_id", "accepted_at",
    ):
        value = getattr(ingested, name, None)
        if value is not None:
            view[name] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return view


# ------------------------------------------------------------------------ lane


def build_worker_config(
    *,
    provider_id: str,
    model_id: str,
    model_config_hash: str,
    deadline_ms: int = 60_000,
    max_attempts: int = 3,
    max_input_tokens: int = 16_384,
    max_output_tokens: int = 2_048,
    max_cost_microunits: int = 5_000_000,
) -> Any:
    """``MemoryJobWorkerConfig`` for the Host lane (design-freeze §6/§9 constants).

    ``lease_seconds = deadline_ms/1000 + 30``; ``batch_size=1`` /
    ``max_batch_wait_seconds=0`` so a claim never waits on wall clock; the
    provider/model/config triple is only the fallback for members without a
    lineage (the terminal outbox always writes one).
    """

    from simple_harness.runtime import AnalysisBudget
    from simple_harness_memory.core.jobs import MemoryJobWorkerConfig

    from deskpet.memory.analysis_proposal import (
        POLICY_VERSION,
        PROMPT_VERSION,
        RESULT_SCHEMA_VERSION,
        VALIDATOR_VERSION,
    )

    delays = tuple(min(2.0 ** index, MAX_RETRY_DELAY_SECONDS) for index in range(max(max_attempts - 1, 0)))
    return MemoryJobWorkerConfig(
        batch_size=1,
        idle_wait_seconds=1.0,
        max_batch_wait_seconds=0.0,
        lease_seconds=deadline_ms / 1000 + 30.0,
        max_attempts=max_attempts,
        retry_delays_seconds=delays,
        max_result_bytes=256 * 1024,
        analysis_budget=AnalysisBudget(max_input_tokens, max_output_tokens, deadline_ms, max_cost_microunits),
        prompt_version=PROMPT_VERSION,
        result_schema_version=RESULT_SCHEMA_VERSION,
        policy_version=POLICY_VERSION,
        validator_version=VALIDATOR_VERSION,
        provider_id=provider_id,
        model_id=model_id,
        model_config_hash=model_config_hash,
    )


class MemoryAnalysisLane:
    """One background lane: ingestion outbox worker → ``DurableMemoryJobRunner.run_once()``."""

    def __init__(
        self,
        *,
        worker: MemoryIngestionOutboxWorker,
        runtime: Any,
        executor: Any,
        config: Any,
        worker_id: str,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = 2.0,
    ) -> None:
        self._worker = worker
        self._runtime = runtime
        self._executor = executor
        self._config = config
        self._worker_id = str(worker_id)
        self._clock = clock
        self._poll_seconds = float(poll_seconds)
        self._runner: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self.last_error: BaseException | None = None

    async def runner(self) -> Any:
        if self._runner is None:
            self._runner = await self._runtime.job_runner(self._executor, self._config, worker_id=self._worker_id, now=self._clock)
        return self._runner

    async def tick(self) -> tuple[OutboxRunOutcome, Any]:
        """Drive one ingestion delivery then one analysis job; never raises the loop dead."""

        outbox = await self._worker.run_once()
        runner = await self.runner()
        try:
            job = await runner.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - finalize/claim faults must not kill the lane
            # Memory raised outside its retry taxonomy (e.g. finalize/claim
            # corruption).  The job keeps its lease until expiry and is
            # reclaimed → dead-letter through Memory's own retry policy.
            self.last_error = exc
            log.warning("memory_analysis_lane_job_error error=%s", f"{type(exc).__name__}:{str(exc)[:200]}")
            job = None
        return outbox, job

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="memory-analysis-lane")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                outbox, job = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = exc
                log.warning("memory_analysis_lane_tick_error error=%s", f"{type(exc).__name__}:{str(exc)[:200]}")
                outbox, job = OutboxRunOutcome.IDLE, None
            busy = outbox is not OutboxRunOutcome.IDLE or (job is not None and str(job) != "idle")
            if busy:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_seconds)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "DEFAULT_RETRY_DELAYS",
    "MemoryAnalysisLane",
    "MemoryIngestionOutboxWorker",
    "OutboxClaim",
    "OutboxRunOutcome",
    "build_worker_config",
]
