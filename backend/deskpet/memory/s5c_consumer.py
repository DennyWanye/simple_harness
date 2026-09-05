"""Recoverable registration delivery, explicitly composed; no timer or grant minting.

The durable outbox is read through Memory's public port. Authority source is a
trusted Host seam for exact source lineage, not a model-supplied grant factory.
This consumer never creates run IDs, renews authorities, or writes Memory SQL.
"""

from __future__ import annotations

import math
from typing import Protocol

from simple_harness.runtime import (
    ProspectiveSignalAuthority,
    ProspectiveSignalAuthorityRef,
)
from simple_harness_memory import MemoryPrincipal, MemoryScope
from simple_harness_memory.core.lifecycle_results import ProspectiveSignalApplyResult
from simple_harness_memory.core.occurrence import OutboxEntryV1, OutboxPageV1

from deskpet.memory.s5c_store import S5cConflict, S5cStore
from deskpet.memory.writer_fence import assert_human_memory_ingress_open


class RegistrationMemoryPort(Protocol):
    async def read_outbox(
        self,
        *,
        principal: MemoryPrincipal,
        states: tuple[str, ...],
        after: tuple[float, str] | None,
        limit: int,
    ) -> OutboxPageV1: ...

    async def apply_prospective_signal(
        self,
        *,
        principal: MemoryPrincipal,
        scope: MemoryScope,
        reference: ProspectiveSignalAuthorityRef,
    ) -> ProspectiveSignalApplyResult: ...


class RegistrationAuthoritySource(Protocol):
    async def prepare_registration(
        self, *, principal: MemoryPrincipal, entry: OutboxEntryV1
    ) -> ProspectiveSignalAuthority:
        """Resolve exact scope/lifecycle/Run/operation and return a fixed grant.

        Missing lineage must raise. A repeated source must not mint a new
        authority or observation time; the consumer reuses persisted grants.
        The production implementation of this seam is not supplied by T3a.
        """
        ...


class ProspectiveRegistrationConsumer:
    def __init__(
        self,
        store: S5cStore,
        memory: RegistrationMemoryPort,
        authority_source: RegistrationAuthoritySource,
    ):
        self.store, self.memory, self.source = store, memory, authority_source
        # Only a scan optimization across irrelevant topics. Restart resumes
        # from the durable relevant cursor and may re-read unrelated entries.
        self._scan_after: tuple[float, str] | None = None

    async def _deliver(self, prepared):
        if prepared.result is not None:
            return
        await assert_human_memory_ingress_open(self.store.path)
        scope = prepared.authority.intent.scope
        result = await self.memory.apply_prospective_signal(
            principal=self.store.principal,
            scope=MemoryScope(scope.kind.value, scope.owner_id),
            reference=prepared.reference,
        )
        await self.store.commit_registration_result(prepared.reference, result)

    async def run_once(self, *, page_size: int = 100, max_pages: int = 4) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be 1..1000")
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be 1..100")
        await assert_human_memory_ingress_open(self.store.path)
        # Recover the oldest fixed commands before preparing anything newer.
        # Exceptions retain prepared records and halt this pass; never DLQ or
        # silently skip an expired, never-consumed command.
        pending = await self.store.pending_registrations(limit=page_size)
        for prepared in pending:
            await self._deliver(prepared)
        if len(pending) == page_size:
            return
        cursor = await self.store.cursor()
        if cursor is not None and (
            self._scan_after is None or cursor > self._scan_after
        ):
            self._scan_after = cursor
        for _ in range(max_pages):
            page = await self.memory.read_outbox(
                principal=self.store.principal,
                states=("pending", "claimed", "applied", "dead_letter"),
                after=self._scan_after,
                limit=page_size,
            )
            self._validate_page(page, self._scan_after, page_size)
            for entry in page.entries:
                if entry.topic.startswith("memory.prospective."):
                    if entry.topic not in {
                        "memory.prospective.registration.requested",
                        "memory.prospective.invalidation.requested",
                    }:
                        raise S5cConflict("s5c_registration_topic_unknown")
                    existing = await self.store.registration(entry.outbox_id)
                    if existing is None:
                        authority = await self.source.prepare_registration(
                            principal=self.store.principal, entry=entry
                        )
                    else:
                        authority = existing.authority
                    # Also checks immutable source on duplicate concurrent read.
                    await self.store.commit_registration(
                        entry, authority, expected_cursor=cursor
                    )
                    cursor = await self.store.cursor()
                    prepared = await self.store.registration(entry.outbox_id)
                    assert prepared is not None
                    await self._deliver(prepared)
                self._scan_after = (entry.created_at, entry.outbox_id)
            if page.next_after is None:
                return

    @staticmethod
    def _validate_page(page, after, limit):
        if type(page) is not OutboxPageV1 or len(page.entries) > limit:
            raise S5cConflict("s5c_outbox_page_invalid")
        prior = after
        for entry in page.entries:
            key = (entry.created_at, entry.outbox_id)
            if not math.isfinite(entry.created_at) or (
                prior is not None and key <= prior
            ):
                raise S5cConflict("s5c_outbox_page_order_differs")
            prior = key
        if page.next_after is not None and (
            not page.entries or page.next_after != prior
        ):
            raise S5cConflict("s5c_outbox_page_cursor_differs")
