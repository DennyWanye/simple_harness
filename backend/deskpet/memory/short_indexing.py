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


# 2026-09-08 HM-TO-A6: a group whose terminal/message envelope exceeds Memory's
# inline-payload ceiling can never be admitted.  Detecting it *before* the first
# write turns an endless per-cycle re-registration (each cycle re-ingested the
# same USER envelope — 28 `memory.evidence_ingestion_replayed` lines a minute in
# `native-a6-7cec5249`) into one deterministic, permanently blocked group.
SOURCE_UNADMISSIBLE = "short_group_source_unadmissible"


def assert_group_admissible(group) -> None:
    """Fail the whole group before its first write if any source is doomed.

    Every pair here is handed to ``ingest_committed_evidence`` /
    ``admit_evidence_source``, both of which run the SDK's own
    ``validate_sanitized_evidence``.  Checking first turns "ingest the USER
    envelope, then raise on the terminal" into one deterministic rejection.
    """

    from deskpet.memory.primary_visibility import (
        PrimaryVisibilityError,
        assert_source_admissible,
    )

    pairs = [(r.envelope, r.admission_receipt) for r in group.registrations]
    terminal = getattr(group, "terminal_source", None)
    if terminal:
        pairs.append((terminal[0], terminal[1]))
    for envelope, receipt in pairs:
        try:
            assert_source_admissible(envelope, receipt)
        except PrimaryVisibilityError as exc:
            raise ConversationRegistrationUnavailable(SOURCE_UNADMISSIBLE) from exc


class PrimaryShortIndexingService:
    def __init__(self, authority: PrimaryConversationAuthority, *, manager, principal, fault_hook=None):
        if principal.actor_id != authority.subject:
            raise ValueError("short_indexing_subject_mismatch")
        self.authority, self.manager, self.principal = authority, manager, principal
        self._fault_hook = fault_hook

    def _fault(self, point):
        if self._fault_hook is not None:
            self._fault_hook(point)

    async def register_group(self, group):
        """Replay a verified whole group; registration ACKs are not projection ACKs."""
        admit_source = getattr(self.manager, "admit_evidence_source", None)
        if not callable(admit_source):
            raise ConversationRegistrationUnavailable("short_source_admission_unavailable")
        assert_group_admissible(group)
        for index, registration in enumerate(group.registrations):
            # Preserve the actual USER lineage even if the Memory DB was
            # recreated while the Host's delivered outbox remained intact.
            if index == 0:
                await self.manager.ingest_committed_evidence(
                    registration.envelope, registration.admission_receipt,
                    analysis_lineage=group.user_analysis_lineage)
                # Message envelopes reference the real completed terminal. Keep
                # that ancestor in S1 as source-only evidence before indexing;
                # otherwise suppression traversal fails beyond the recent window.
                # It is not an extra message, registration or analysis job.
                terminal, terminal_receipt = group.terminal_source
                await admit_source(principal=self.principal,
                    envelope=terminal, receipt=terminal_receipt)
                self._fault("short.after_terminal_source")
            else:
                await admit_source(principal=self.principal,
                    envelope=registration.envelope, receipt=registration.admission_receipt)
            self._fault("short.after_ingest")
            reference = registration_ref(registration)
            acknowledged = await self.manager.register_conversation_evidence(reference)
            if acknowledged != reference:
                raise RuntimeError("short_registration_ack_mismatch")
            self._fault("short.after_registration")

    async def reconcile(self) -> ShortIndexingResult:
        admit_source = getattr(self.manager, "admit_evidence_source", None)
        if not callable(admit_source):
            raise ConversationRegistrationUnavailable("short_source_admission_unavailable")
        groups, blocked = [], []
        # Validate whole groups before the first write. Corruption aborts the
        # scan; known source/representation gaps remain explicit blocked rows.
        for run_id in await self.authority.completed_run_ids():
            try:
                group = await self.authority.registrations_for_run(run_id)
                # Task 6 review F-7: a group Memory can never admit is a
                # `blocked` row like any other source gap. Aborting the whole
                # scan would let one doomed Run stop every later Run from
                # being indexed.
                assert_group_admissible(group)
            except ConversationRegistrationUnavailable as exc:
                blocked.append((run_id, exc.code))
                continue
            groups.append(group)
        for group in groups:
            await self.register_group(group)
        self._fault("short.before_projection")
        projection = await self.manager.rebuild_short_horizon_projection(principal=self.principal)
        self._fault("short.after_projection")
        return ShortIndexingResult(tuple(groups), tuple(blocked), projection)
