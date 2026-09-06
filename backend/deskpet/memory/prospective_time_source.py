"""Host ACK registration facts to one-shot time observations, no SDK head reads."""
from __future__ import annotations

import math

from simple_harness.runtime import ProspectiveSignalIntent, issue_prospective_signal_authority
from simple_harness_memory import MemoryScope

from deskpet.memory.prospective_scheduler import PreparedTimer
from deskpet.memory.prospective_signal_store import TimerConflict
from deskpet.task_scope.protocol import canonical_hash


class PublicTimeAuthoritySource:
    def __init__(self, *, registrations, signals, lifetime_seconds: float = 300.0):
        if registrations.principal != signals.principal or registrations.owner != signals.owner:
            raise ValueError('prospective_time_owner_differs')
        if not math.isfinite(lifetime_seconds) or not 0 < lifetime_seconds <= 86400:
            raise ValueError('prospective_time_lifetime_invalid')
        self.registrations, self.signals = registrations, signals
        self.lifetime_seconds = lifetime_seconds
        self._after, self._upper = 0, None

    async def registration_is_live(self, authority):
        """Only Host ACK state; SDK applies current lifecycle/suppression gates."""
        i=authority.intent
        if i.subject != self.signals.principal.actor_id:
            raise ValueError('prospective_time_subject_differs')
        MemoryScope(i.scope.kind.value,i.scope.owner_id).authorize(self.signals.principal)
        registration=await self.registrations.accepted_registration(
            memory_id=i.target_memory_id,revision=i.target_revision)
        if registration is None:return False
        r=registration.authority.intent
        if (r.scheduler_registration_ref!=i.scheduler_registration_ref
                or r.registration_revision!=i.registration_revision or r.trigger_hash!=i.trigger_hash
                or r.scope!=i.scope or r.run_id!=i.run_id or r.operation_id!=i.operation_id
                or r.transition_to.value not in {'pending','rescheduled'}
                or (i.signal_kind.value=='time_due' and i.transition_from!=r.transition_to)):
            return False
        return await self.registrations.accepted_invalidation(
            memory_id=i.target_memory_id,revision=i.target_revision,
            registration_revision=i.registration_revision,
            registration_ref=i.scheduler_registration_ref) is None

    async def prepare_due(self, *, now, limit):
        if type(now) not in (int,float) or not math.isfinite(now) or now<0:
            raise ValueError('prospective_time_clock_invalid')
        page, after, upper=await self.registrations.page_accepted_registrations(
            after=self._after,upper=self._upper,limit=limit)
        prepared=[]
        for registration in page:
            r=registration.authority.intent
            if r.transition_to.value not in {'pending','rescheduled'} or r.trigger.to_json()['trigger_kind']!='time' or r.trigger.trigger_at>now:
                continue
            signal_id=canonical_hash(['host:prospective-time/v1',self.signals.owner,
                r.scheduler_registration_ref,r.registration_revision,r.target_memory_id,r.target_revision,r.trigger_hash])
            existing=await self.signals.get_prepared(signal_id)
            if existing is not None:
                # Return the durable first observation, not a later now/grant.
                prepared.append(existing)
                continue
            if not await self.registration_is_live(registration.authority):
                continue
            observation=dict(schema_version=1,kind='host_time_observation',owner_key=self.signals.owner,
                subject=r.subject,memory_id=r.target_memory_id,target_revision=r.target_revision,
                scheduler_registration_ref=r.scheduler_registration_ref,registration_revision=r.registration_revision,
                trigger_hash=r.trigger_hash,due_at=r.trigger.trigger_at,observed_at=now,
                run_id=r.run_id,operation_id=r.operation_id)
            digest=canonical_hash(observation)
            intent=ProspectiveSignalIntent(signal_id=signal_id,subject=r.subject,scope=r.scope,
                target_memory_id=r.target_memory_id,target_revision=r.target_revision,signal_kind='time_due',
                trigger=r.trigger,scheduler_registration_ref=r.scheduler_registration_ref,
                registration_revision=r.registration_revision,signal_receipt_id='host:time-observation:'+digest,
                signal_receipt_hash=digest,observed_at=now,transition_from=r.transition_to,transition_to='triggered',
                outbox_id=None,outbox_payload_hash=None,run_id=r.run_id,operation_id=r.operation_id)
            authority=issue_prospective_signal_authority(intent,
                authority_id='host:time-authority:'+signal_id,issued_at=now,
                expires_at=now+self.lifetime_seconds,nonce=signal_id,issuer_ref='host:prospective-time/v1')
            value=PreparedTimer(authority,observation)
            try:
                await self.signals.prepare(value)
            except TimerConflict as exc:
                if str(exc)!='prospective_timer_prepare_replay_differs':raise
                winner=await self.signals.get_prepared(signal_id)
                if winner is None:raise
                value=winner
            prepared.append(value)
        # Scan progress is only an optimization; failures never advance it.
        # Rotate after the frozen upper so processed first pages cannot starve
        # later registrations, and restart safely rescans the durable journal.
        self._after,self._upper=(0,None) if after>=upper else (after,upper)
        return tuple(prepared)
