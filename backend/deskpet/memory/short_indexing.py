"""Replay completed Host terminal facts into the public Memory short index.

The original immutable completed facts are the outbox. No success cursor can
skip missing SDK writes after a crash or after opening a fresh Memory database.
"""
from __future__ import annotations

from dataclasses import dataclass

from deskpet.memory.conversation_registration import (
    ConversationRegistrationUnavailable,
    PrimaryConversationAuthority,
    registration_ref,
)


@dataclass(frozen=True)
class ShortIndexingResult:
    groups: tuple
    blocked: tuple[tuple[str, str], ...]
    projection: object

    @property
    def visibility_dependencies(self):
        return {"schema_version": 2, "evidence": [
            {"evidence_id": r.envelope.evidence_id, "envelope_hash": r.envelope.envelope_hash}
            for group in self.groups for r in group.registrations
        ], "recall": [], "short_horizon": []}


class PrimaryShortIndexingService:
    def __init__(self, authority: PrimaryConversationAuthority, *, manager, principal, fault_hook=None):
        if principal.actor_id != authority.subject:
            raise ValueError("short_indexing_subject_mismatch")
        self.authority, self.manager, self.principal = authority, manager, principal
        self._fault_hook = fault_hook

    def _fault(self, point):
        if self._fault_hook is not None:
            self._fault_hook(point)

    async def reconcile(self) -> ShortIndexingResult:
        admit_source = getattr(self.manager, "admit_evidence_source", None)
        if not callable(admit_source):
            raise ConversationRegistrationUnavailable("short_source_admission_unavailable")
        groups, blocked = [], []
        # Validate whole groups before the first write. Corruption aborts the
        # scan; known source/representation gaps remain explicit blocked rows.
        for run_id in await self.authority.completed_run_ids():
            try:
                groups.append(await self.authority.registrations_for_run(run_id))
            except ConversationRegistrationUnavailable as exc:
                blocked.append((run_id, exc.code))
        for group in groups:
            for index, registration in enumerate(group.registrations):
                # Preserve the actual USER lineage even if the Memory DB was
                # recreated while the Host's delivered outbox remained intact.
                if index == 0:
                    await self.manager.ingest_committed_evidence(
                        registration.envelope, registration.admission_receipt,
                        analysis_lineage=group.user_analysis_lineage)
                else:
                    await admit_source(principal=self.principal,
                        envelope=registration.envelope, receipt=registration.admission_receipt)
                self._fault("short.after_ingest")
                await self.manager.register_conversation_evidence(registration_ref(registration))
                self._fault("short.after_registration")
        self._fault("short.before_projection")
        projection = await self.manager.rebuild_short_horizon_projection(principal=self.principal)
        self._fault("short.after_projection")
        return ShortIndexingResult(tuple(groups), tuple(blocked), projection)
