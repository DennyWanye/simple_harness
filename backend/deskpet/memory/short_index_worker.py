"""Cyclic Host replay owned by MemoryAnalysisLane; projection cost is still global."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

from deskpet.memory.conversation_registration import ConversationRegistrationUnavailable
from deskpet.memory.history_source_authority import HostHistorySourceError
from deskpet.memory.short_indexing import PrimaryShortIndexingService


@dataclass(frozen=True, slots=True)
class ShortIndexStep:
    scanned: int = 0
    confirmed: int = 0
    blocked: tuple[tuple[str, str], ...] = ()
    wrapped: bool = False
    projection: object | None = None
    generation: object | None = None


class PrimaryShortIndexWorker:
    def __init__(self, runtime, *, page_size=16, cache_limit=256,
                 maintenance_seconds=60.0, operation_timeout=5.0,
                 monotonic=time.monotonic, fault_hook=None):
        if (type(page_size) is not int or not 1 <= page_size <= 16
                or type(cache_limit) is not int or not 1 <= cache_limit <= 256
                or maintenance_seconds <= 0 or operation_timeout <= 0):
            raise ValueError("short_worker_config_invalid")
        self.runtime = runtime
        self.authority = runtime.conversation_evidence_authority
        self.page_size, self.cache_limit = page_size, cache_limit
        self.maintenance_seconds, self.operation_timeout = maintenance_seconds, operation_timeout
        self._now, self._fault_hook = monotonic, fault_hook
        self._lock = asyncio.Lock()
        self.reset()

    def reset(self):
        self.last_cognitive_generation = None
        self._manager = None
        self._after, self._upper = 0, None
        self._confirmed = OrderedDict()
        # 2026-09-08 HM-TO-A6：确定性不可受理的组（例如 payload 超出 Memory 内联
        # 上限的终态源）此前每个周期都会被重新注册一次，每次都先把同一条 USER
        # 证据再摄入一遍——线上表现为每分钟 28 条 evidence_ingestion_replayed。
        # 止血的是 register_group 首次写之前的 assert_group_admissible，而不是
        # 这里再加一个负缓存：负缓存的 key 只能覆盖 registrations，漏掉
        # terminal_source，修好那条终态证据之后该组会被永久挡住（Task 6 评审
        # F-5）。每周期多做一次纯读的受理校验，换掉一整类过期 key 缺陷。
        self._last_projection = None
        self._generation_pending = False

    def _fault(self, point):
        if self._fault_hook is not None:
            self._fault_hook(point)

    async def step(self):
        async with self._lock:
            manager = await self.runtime.manager()
            if manager is not self._manager:
                self.reset()
                self._manager = manager
            if self.authority is None or not callable(getattr(manager, "admit_evidence_source", None)):
                raise RuntimeError("short_source_admission_unavailable")
            upper, rows = await self.authority.page_turns(
                after=self._after, upper=self._upper, limit=self.page_size)
            self._upper = upper
            service = PrimaryShortIndexingService(self.authority, manager=manager,
                principal=self.runtime.principal(), fault_hook=self._fault_hook)
            started = self._now()
            blocked, pending = [], []
            scanned = 0
            for sequence, run_id, terminal in rows:
                if scanned and self._now() - started >= self.operation_timeout:
                    break
                self._after = sequence  # scan progress, never durable success
                scanned += 1
                if run_id is None or terminal != "COMPLETED":
                    blocked.append((run_id or f"turn-sequence:{sequence}", "conversation_terminal_pending"))
                    continue
                try:
                    async with asyncio.timeout(self.operation_timeout):
                        group = await self.authority.registrations_for_run(run_id)
                        key = tuple((r.registration_id, r.registration_hash,
                                     r.envelope.evidence_id, r.envelope.envelope_hash)
                                    for r in group.registrations)
                        if key in self._confirmed:
                            observer = getattr(self.runtime, "procedure_runtime", None)
                            if observer is not None:
                                await observer.observe_group(group, manager)
                            self._confirmed.move_to_end(key)
                            continue
                        await service.register_group(group)
                        observer = getattr(self.runtime, "procedure_runtime", None)
                        if observer is not None:
                            await observer.observe_group(group, manager)
                        pending.append(key)
                except HostHistorySourceError:
                    raise
                except (ValueError, TypeError, RuntimeError, TimeoutError) as exc:
                    if isinstance(exc, ConversationRegistrationUnavailable) and exc.code in {
                        "conversation_primary_mismatch", "conversation_epoch_mismatch"}:
                        raise
                    blocked.append((run_id, getattr(exc, "code", "short_group_unavailable")))
            wrapped = not rows or self._after >= upper
            if wrapped:
                self._after, self._upper = 0, None
            projection = generation = None
            if (pending or self._generation_pending or self._last_projection is None
                    or self._now() - self._last_projection >= self.maintenance_seconds):
                # Cache hits are registration facts only. A failed maintenance
                # generation must retry even if every group was already confirmed.
                self._generation_pending = True
                self._fault("short.before_projection")
                async with asyncio.timeout(self.operation_timeout):
                    projection = await manager.rebuild_short_horizon_projection(principal=self.runtime.principal())
                    self._fault("short.after_projection")
                    generation = await manager.rebuild_short_horizon_generation()
                    if projection.projected_chunk_count and not generation.activated:
                        raise RuntimeError("short_generation_not_activated")
                    self._fault("short.after_generation")
                    # Memory 0.6.23: cognitive vector generation shares the same
                    # maintenance tick, embedder and retry semantics.
                    self.last_cognitive_generation = await manager.rebuild_cognitive_vector_generation()
                    self._fault("short.after_cognitive_generation")
                self._generation_pending = False
                self._last_projection = self._now()
                for key in pending:
                    self._confirmed[key] = None
                    self._confirmed.move_to_end(key)
                    while len(self._confirmed) > self.cache_limit:
                        self._confirmed.popitem(last=False)
            return ShortIndexStep(scanned, len(pending), tuple(blocked), wrapped, projection, generation)

    async def close(self):
        async with self._lock:
            self.reset()
