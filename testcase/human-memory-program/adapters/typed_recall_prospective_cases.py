"""Synthetic scheduler contract fixture over public outbox and signal ports.

No external event/Host reminder is asserted. The issuer records its own synthetic
ACK/occurrence before issuing authority; neither gold nor recall results are inputs.
"""
import dataclasses as dc
import hashlib
import json

import simple_harness as h
import simple_harness_memory as m


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def supported(recipe):
    seed=recipe['seed']
    return (seed['memory_type']=='prospective' and recipe['family'] in {'lifecycle','epistemic'}
        and seed.get('state','pending') in {'pending','triggered'})


async def register(case, target, trigger, *, state='pending', identity=None):
    await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
    page=await case.manager.read_outbox(principal=case.principal,limit=100)
    case.events.append({'call':'read_prospective_outbox','entries':[dc.asdict(e) for e in page.entries],
        'next_after':page.next_after})
    if page.next_after is not None:
        raise ValueError('synthetic fixture outbox unexpectedly truncated')
    entries=[e for e in page.entries if e.topic=='memory.prospective.registration.requested'
        and e.payload['memory_id']==target.memory_id and e.payload['prospective_revision']==target.revision]
    if len(entries)!=1:
        raise ValueError('exact public prospective registration command unavailable')
    row=entries[0]
    return await signal(case,target,trigger,kind='registration_accepted',state=state,
        next_state=state,outbox=row,identity=identity)


async def signal(case,target,trigger,*,kind,state,next_state,outbox=None,
                 observed_at=None,identity=None,expires_at=None):
    now=case.now if observed_at is None else observed_at
    identity=identity or 'fixture-'+kind
    registration_ref='synthetic-scheduler-registration'
    # This is an explicit synthetic input receipt, never an assertion that a
    # production scheduler delivered an event. Its immutable fields feed issuance.
    record={'schema':'synthetic-prospective-signal/v1','signal_id':identity,
        'subject':case.principal.actor_id,'run_id':case.disclosure.run_id,
        'memory_id':target.memory_id,'revision':target.revision,'kind':kind,
        'trigger':trigger.to_json(),'scheduler_registration_ref':registration_ref,
        'registration_revision':target.revision,'observed_at':now,
        'outbox_id':None if outbox is None else outbox.outbox_id,
        'outbox_payload_hash':None if outbox is None else outbox.payload_hash}
    if outbox is not None and sha(outbox.payload)!=outbox.payload_hash:
        raise ValueError('public outbox bytes/hash differ')
    receipt_hash=sha(record)
    case.events.append({'call':'synthetic_scheduler_input','record':record,'receipt_hash':receipt_hash})
    intent=h.ProspectiveSignalIntent(signal_id=identity,subject=case.principal.actor_id,
        scope=h.MemoryScopeRef.personal(case.principal.actor_id), target_memory_id=target.memory_id,
        target_revision=target.revision,signal_kind=h.ProspectiveSignalKind(kind),trigger=trigger,
        scheduler_registration_ref=registration_ref,registration_revision=target.revision,
        signal_receipt_id=identity+'-receipt',signal_receipt_hash=receipt_hash,observed_at=now,
        transition_from=h.ProspectiveLifecycleState(state),transition_to=h.ProspectiveLifecycleState(next_state),
        outbox_id=record['outbox_id'],outbox_payload_hash=record['outbox_payload_hash'],
        run_id=case.disclosure.run_id,operation_id=identity+'-operation')
    authority=h.issue_prospective_signal_authority(intent,authority_id=identity+'-authority',
        issued_at=case.now-1,expires_at=case.now+300 if expires_at is None else expires_at,
        nonce=identity+'-nonce',issuer_ref='synthetic-scheduler-fixture')
    case.prospective_authorities[authority.authority_id]=authority
    ref=h.ProspectiveSignalAuthorityRef.from_authority(authority)
    event={'call':'apply_prospective_signal','reference':ref.to_json()}
    case.events.append(event)
    result=await case.manager.apply_prospective_signal(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id),reference=ref)
    event.update(result=result.to_json(),result_hash=result.result_hash)
    replay=await case.manager.apply_prospective_signal(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id),reference=ref)
    event.update(replay=replay.to_json(),replay_hash=replay.result_hash)
    return result


async def bind(case,recipe,target):
    trigger=case.payload(recipe['seed']).trigger
    ack=await register(case,target,trigger)
    results=[ack.to_json()]
    if recipe['seed'].get('state')=='triggered':
        occurrence=await signal(case,target,trigger,kind='event_occurred' if isinstance(trigger,h.ProspectiveEventTrigger)
            else 'time_due',state='pending',next_state='triggered')
        results.append(occurrence.to_json())
    return {'scope':'synthetic-sdk-contract-only','results':results}
