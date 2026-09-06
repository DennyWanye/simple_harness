"""Optional verified event-source consumer. No default publication provider."""
from __future__ import annotations

import math
from simple_harness.runtime import ProspectiveSignalIntent, issue_prospective_signal_authority
from deskpet.memory.prospective_event_codec import (
    DOMAIN, EventRead, PreparedEvent, check_confirmation, check_cut, event_signal_id,
)
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.prospective_signal_store import TimerConflict
from deskpet.task_scope.protocol import canonical_hash


class PublicEventAuthoritySource(PublicTimeAuthoritySource):
    def __init__(self, *, registrations, signals, reader=None, lifetime_seconds=300.0):
        super().__init__(registrations=registrations, signals=signals,
                         lifetime_seconds=lifetime_seconds)
        self.reader = reader
        self.last_read = EventRead('unverifiable', 'production_event_source_unavailable')

    async def registration_is_live(self, authority):
        i = authority.intent
        if i.trigger.to_json()['trigger_kind'] != 'event':
            return False
        registration = await self.registrations.accepted_registration(
            memory_id=i.target_memory_id, revision=i.target_revision)
        if registration is None or registration.authority.intent.transition_to.value != 'pending':
            return False
        return await super().registration_is_live(authority)

    async def prepare_due(self, *, now, limit):
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError('prospective_event_clock_invalid')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('prospective_event_limit_invalid')
        if self.reader is None:
            self.last_read = EventRead('unverifiable', 'production_event_source_unavailable')
            return ()
        now = float(now)
        page, after, upper = await self.registrations.page_accepted_registrations(
            after=self._after, upper=self._upper, limit=limit)
        prepared = []
        unverifiable = False
        for registration in page:
            r = registration.authority.intent
            if r.trigger.to_json()['trigger_kind'] != 'event' or r.transition_to.value != 'pending':
                continue
            identity = event_signal_id(self.signals.owner, r)
            existing = await self.signals.get_prepared(identity)
            if existing is not None:
                if type(existing) is not PreparedEvent:
                    raise ValueError('prospective_event_replay_domain_differs')
                prepared.append(existing)
                continue
            if not await self.registration_is_live(registration.authority):
                continue
            result = await self.reader.read_confirmed_event(
                principal=self.signals.principal, registration=registration)
            if type(result) is not EventRead:
                raise TypeError('EventRead required')
            self.last_read = result
            if result.status != 'confirmed':
                unverifiable |= result.status == 'unverifiable'
                continue
            confirmation = check_confirmation(result.confirmation)
            cut = check_cut(result.causal_cut)
            if (cut['registration_authority_hash'] != registration.authority.authority_hash
                    or confirmation['event_authority_ref'] != r.trigger.event_authority_ref
                    or confirmation['condition_hash'] != r.trigger.condition_hash):
                raise ValueError('prospective_event_registration_binding_differs')
            # Recheck the actual ACK after the potentially slow public reader.
            if not await self.registration_is_live(registration.authority):
                continue
            observation = dict(schema_version=1, kind='host_event_observation',
                owner_key=self.signals.owner, subject=r.subject, memory_id=r.target_memory_id,
                target_revision=r.target_revision, scheduler_registration_ref=r.scheduler_registration_ref,
                registration_revision=r.registration_revision, trigger_hash=r.trigger_hash,
                observed_at=now, run_id=r.run_id, operation_id=r.operation_id,
                confirmation=confirmation, causal_cut=cut)
            digest = canonical_hash(observation)
            intent = ProspectiveSignalIntent(signal_id=identity, subject=r.subject, scope=r.scope,
                target_memory_id=r.target_memory_id, target_revision=r.target_revision,
                signal_kind='event_occurred', trigger=r.trigger,
                scheduler_registration_ref=r.scheduler_registration_ref,
                registration_revision=r.registration_revision,
                signal_receipt_id='host:event-observation:' + digest, signal_receipt_hash=digest,
                observed_at=now, transition_from='pending', transition_to='triggered',
                outbox_id=None, outbox_payload_hash=None, run_id=r.run_id, operation_id=r.operation_id)
            authority = issue_prospective_signal_authority(intent,
                authority_id='host:event-authority:' + identity, issued_at=now,
                expires_at=now + self.lifetime_seconds, nonce=identity, issuer_ref=DOMAIN)
            value = PreparedEvent(authority, observation)
            try:
                await self.signals.prepare(value)
            except TimerConflict as exc:
                if str(exc) != 'prospective_timer_prepare_replay_differs':
                    raise
                value = await self.signals.get_prepared(identity)
                if type(value) is not PreparedEvent:
                    raise
            prepared.append(value)
        if not unverifiable:
            self._after, self._upper = (0, None) if after >= upper else (after, upper)
        return tuple(prepared)
