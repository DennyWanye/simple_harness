"""Schema51 timer journal; migrations remain with the Host schema owner.

All claim receipts are captured inside BEGIN IMMEDIATE. Cross-database call
recovery reuses the immutable signal authority; leases never change its identity.
"""
from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from simple_harness.runtime import ProspectiveSignalAuthority, ProspectiveSignalAuthorityRef
from simple_harness_memory import MemoryPrincipal

from deskpet.memory.prospective_scheduler import PreparedTimer, TimerClaim
from deskpet.memory.s5c_timer_schema import validate_s5c_timer_state_db
from deskpet.task_scope.protocol import canonical_hash, canonical_json


class TimerConflict(ValueError):
    pass


class ProspectiveSignalStore:
    def __init__(self, path, principal: MemoryPrincipal):
        if type(principal) is not MemoryPrincipal:
            raise TypeError('MemoryPrincipal required')
        self.path, self.principal = Path(path), principal
        validate_s5c_timer_state_db(self.path)
        self.owner = canonical_hash([principal.deployment_id,principal.household_id,principal.actor_id])

    @asynccontextmanager
    async def _tx(self):
        async with aiosqlite.connect(f'{self.path.resolve().as_uri()}?mode=rw',uri=True) as db:
            db.row_factory=aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            try:
                # Never create implicit schema or mutate a schema50 installation.
                version=await (await db.execute('PRAGMA user_version')).fetchone()
                if version[0]!=51:
                    raise TimerConflict('prospective_timer_schema51_required')
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _rows(self, db, signal_id):
        rows=await (await db.execute('SELECT * FROM prospective_timer_events WHERE owner_key=? AND signal_id=? ORDER BY sequence',
            (self.owner,signal_id))).fetchall()
        prior='0'*64
        for n,row in enumerate(rows,1):
            body=json.loads(row['body_json'])
            header=[self.owner,signal_id,n,row['phase'],row['claim_owner'],row['claim_epoch'],row['lease_until'],body,prior]
            if (row['sequence']!=n or row['prior_hash']!=prior or row['body_hash']!=canonical_hash(body)
                    or row['record_hash']!=canonical_hash(header)):
                raise TimerConflict('prospective_timer_event_corrupt')
            prior=row['record_hash']
        return rows

    async def _append(self, db, signal_id, rows, phase, body, *, owner=None, epoch=0, lease=None):
        # SQLite REAL round-trips as float, including integer caller clocks.
        lease=None if lease is None else float(lease)
        n=len(rows)+1;prior=rows[-1]['record_hash'] if rows else '0'*64
        digest=canonical_hash([self.owner,signal_id,n,phase,owner,epoch,lease,body,prior])
        await db.execute('INSERT INTO prospective_timer_events '
            '(owner_key,signal_id,sequence,phase,claim_owner,claim_epoch,lease_until,body_json,body_hash,prior_hash,record_hash) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (self.owner,signal_id,n,phase,owner,epoch,lease,canonical_json(body),canonical_hash(body),prior,digest))

    async def prepare(self, prepared):
        if type(prepared) is not PreparedTimer:
            raise TypeError('PreparedTimer required')
        authority=prepared.authority
        if type(authority) is not ProspectiveSignalAuthority:
            raise TypeError('ProspectiveSignalAuthority required')
        i=authority.intent
        if (i.subject!=self.principal.actor_id or i.signal_kind.value!='time_due'
                or i.trigger.to_json()['trigger_kind']!='time' or i.observed_at<i.trigger.trigger_at
                or i.outbox_id is not None or i.outbox_payload_hash is not None
                or i.transition_from.value not in {'pending','rescheduled'} or i.transition_to.value!='triggered'):
            raise TimerConflict('prospective_timer_authority_invalid')
        expected=canonical_hash(['host:prospective-time/v1',self.owner,i.scheduler_registration_ref,
            i.registration_revision,i.target_memory_id,i.target_revision,i.trigger_hash])
        if i.signal_id!=expected:
            raise TimerConflict('prospective_timer_signal_identity_differs')
        observation=self._check_observation(authority,prepared.observation)
        body={'authority':authority.to_json(),'authority_hash':authority.authority_hash,
              'observation':observation,'observation_hash':canonical_hash(observation)}
        async with self._tx() as db:
            rows=await self._rows(db,i.signal_id)
            if rows:
                if json.loads(rows[0]['body_json'])!=body:
                    raise TimerConflict('prospective_timer_prepare_replay_differs')
                return
            await self._append(db,i.signal_id,rows,'prepared',body)

    def _check_observation(self,authority,observation):
        i=authority.intent
        expected=dict(schema_version=1,kind='host_time_observation',owner_key=self.owner,
            subject=i.subject,memory_id=i.target_memory_id,target_revision=i.target_revision,
            scheduler_registration_ref=i.scheduler_registration_ref,
            registration_revision=i.registration_revision,trigger_hash=i.trigger_hash,
            due_at=i.trigger.trigger_at,observed_at=i.observed_at,
            run_id=i.run_id,operation_id=i.operation_id)
        if observation!=expected or i.signal_receipt_hash!=canonical_hash(expected):
            raise TimerConflict('prospective_timer_observation_binding_differs')
        if i.signal_receipt_id!='host:time-observation:'+canonical_hash(expected):
            raise TimerConflict('prospective_timer_observation_identity_differs')
        return expected

    def _authority(self,rows):
        body=json.loads(rows[0]['body_json'])
        a=ProspectiveSignalAuthority.from_json(body['authority'])
        if a.authority_hash!=body['authority_hash']:
            raise TimerConflict('prospective_timer_authority_hash_differs')
        self._check_observation(a,body['observation'])
        if body['observation_hash']!=canonical_hash(body['observation']):
            raise TimerConflict('prospective_timer_observation_hash_differs')
        return a

    async def claim(self, *, owner, now, lease_seconds):
        if not owner or not math.isfinite(now) or not math.isfinite(lease_seconds) or lease_seconds<=0:
            raise ValueError('prospective_timer_claim_invalid')
        async with self._tx() as db:
            candidates=await (await db.execute("SELECT e.signal_id FROM prospective_timer_events e "
                "WHERE e.owner_key=? AND e.phase IN ('prepared','claimed','handed_off') AND (e.lease_until IS NULL OR e.lease_until<=?) "
                "AND NOT EXISTS (SELECT 1 FROM prospective_timer_events n WHERE n.owner_key=e.owner_key "
                "AND n.signal_id=e.signal_id AND n.sequence>e.sequence) ORDER BY e.signal_id LIMIT 1",(self.owner,now))).fetchall()
            if not candidates:return None
            signal_id=candidates[0]['signal_id'];rows=await self._rows(db,signal_id)
            a=self._authority(rows);epoch=rows[-1]['claim_epoch']+1
            await self._append(db,signal_id,rows,'claimed',{'prepared_hash':rows[0]['record_hash']},owner=owner,epoch=epoch,lease=now+lease_seconds)
            return TimerClaim(signal_id,owner,epoch,a,any(r['phase']=='handed_off' for r in rows))

    def _check_claim(self,rows,claim,now):
        if not rows or rows[-1]['phase'] not in {'claimed','handed_off'} or rows[-1]['claim_owner']!=claim.owner or rows[-1]['claim_epoch']!=claim.epoch or rows[-1]['lease_until']<=now:
            raise TimerConflict('prospective_timer_stale_claim')
        if not math.isfinite(now):
            raise TimerConflict('prospective_timer_clock_invalid')
        if claim.handed_off != any(r['phase']=='handed_off' for r in rows):
            raise TimerConflict('prospective_timer_claim_handoff_differs')
        if self._authority(rows)!=claim.authority:
            raise TimerConflict('prospective_timer_claim_authority_differs')

    async def get_prepared(self, signal_id):
        """Return the actual first grant; never renew it from a later clock."""
        async with self._tx() as db:
            rows=await self._rows(db,signal_id)
            return PreparedTimer(self._authority(rows),json.loads(rows[0]['body_json'])['observation']) if rows else None

    async def handoff(self,claim,*,now):
        async with self._tx() as db:
            rows=await self._rows(db,claim.signal_id);self._check_claim(rows,claim,now)
            if rows[-1]['phase']!='handed_off':
                await self._append(db,claim.signal_id,rows,'handed_off',
                    {'prepared_hash':rows[0]['record_hash']},owner=claim.owner,
                    epoch=claim.epoch,lease=rows[-1]['lease_until'])
            return TimerClaim(claim.signal_id,claim.owner,claim.epoch,claim.authority,True)

    async def assert_claim(self,claim,*,now):
        async with self._tx() as db:
            self._check_claim(await self._rows(db,claim.signal_id),claim,now)

    async def invalidate(self,claim,*,now):
        async with self._tx() as db:
            rows=await self._rows(db,claim.signal_id);self._check_claim(rows,claim,now)
            if claim.handed_off:
                raise TimerConflict('prospective_timer_handoff_requires_replay')
            await self._append(db,claim.signal_id,rows,'invalidated',{'reason':'public_source_not_current'},owner=claim.owner,epoch=claim.epoch)

    async def applied(self,claim,result,*,now):
        i=claim.authority.intent
        if result.signal_id!=i.signal_id or result.memory_id!=i.target_memory_id or result.base_revision!=i.target_revision:
            raise TimerConflict('prospective_timer_result_identity_differs')
        async with self._tx() as db:
            rows=await self._rows(db,claim.signal_id);self._check_claim(rows,claim,now)
            if not claim.handed_off:
                raise TimerConflict('prospective_timer_result_without_handoff')
            await self._append(db,claim.signal_id,rows,'applied',{'result':result.to_json(),'result_hash':result.result_hash},owner=claim.owner,epoch=claim.epoch)

    async def resolve_prospective_signal_authority(self,reference):
        if type(reference) is not ProspectiveSignalAuthorityRef:
            raise TypeError('ProspectiveSignalAuthorityRef required')
        # Schema owner supplies an index on prepared authority_id JSON; the
        # full public reference is still checked against the immutable grant.
        async with self._tx() as db:
            row=await (await db.execute("SELECT signal_id FROM prospective_timer_events WHERE owner_key=? AND phase='prepared' "
                "AND json_extract(body_json,'$.authority.authority_id')=? LIMIT 2",(self.owner,reference.authority_id))).fetchall()
            if len(row)!=1:raise TimerConflict('prospective_timer_authority_not_found')
            a=self._authority(await self._rows(db,row[0]['signal_id']))
            if ProspectiveSignalAuthorityRef.from_authority(a)!=reference:
                raise TimerConflict('prospective_timer_authority_ref_differs')
            return a
