"""Input-only synthetic scheduler oracle; no SDK imports/private storage."""
import hashlib
import json


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def domain(name,value):
    return sha({'domain':name,'payload':value})


def check(o,recipe,*,registration_state='pending',ack_identity='fixture-registration_accepted'):
    seed=recipe['seed'];state=seed.get('state','pending')
    if (seed['memory_type']!='prospective' or state not in {registration_state,'triggered'}
            or recipe['family'] not in {'lifecycle','epistemic'}
            or o['prospective_binding']['scope']!='synthetic-sdk-contract-only'):
        raise ValueError('prospective proof scope differs')
    raw=seed['payload']['trigger']
    if raw!={'kind':'event','event':'release_succeeded'}:
        raise ValueError('original prospective event input differs')
    trigger={'trigger_kind':'event','event_authority_ref':'host-event-release',
        'condition':raw['event'],'condition_hash':sha(raw['event'])}
    th=domain('simple-harness/prospective-trigger/v1',trigger)
    source=o['sources'][-1];target=source['receipt']['operations'][0]
    mutation=next(e['plan'] for e in o['calls'] if e['call']=='apply_memory_mutation_plan')
    now=o['recalls'][0]['now']
    reads=[e for e in o['calls'] if e['call']=='read_prospective_outbox']
    if len(reads)!=1 or reads[0]['next_after'] is not None:raise ValueError('public outbox read incomplete')
    rows=[e for e in reads[0]['entries'] if e['topic']=='memory.prospective.registration.requested'
        and e['payload']['memory_id']==target['memory_id'] and e['payload']['prospective_revision']==target['revision']]
    if len(rows)!=1:raise ValueError('exact public outbox command missing')
    row=rows[0]
    expected_payload=dict(schema_version=1,command='registration',memory_id=target['memory_id'],
        prospective_revision=target['revision'],registration_revision=target['revision'],trigger=trigger,trigger_hash=th)
    if row['payload']!=expected_payload or row['payload_hash']!=sha(expected_payload):
        raise ValueError('public outbox original trigger/target/hash differs')
    kinds=['registration_accepted']+(['event_occurred'] if state=='triggered' else [])
    inputs=[e for e in o['calls'] if e['call']=='synthetic_scheduler_input']
    grants=[e for e in o['calls'] if e['call']=='resolve_prospective_signal_authority']
    applies=[e for e in o['calls'] if e['call']=='apply_prospective_signal']
    if not len(inputs)==len(grants)==len(applies)==len(kinds):raise ValueError('signal actual call cardinality differs')
    results=[]
    for kind,entry,grant,applied in zip(kinds,inputs,grants,applies,strict=True):
        ack=kind=='registration_accepted';identity=ack_identity if ack else 'fixture-'+kind
        expected_record=dict(schema='synthetic-prospective-signal/v1',signal_id=identity,
            subject=mutation['subject'],run_id=mutation['run_id'],memory_id=target['memory_id'],
            revision=target['revision'],kind=kind,trigger=trigger,
            scheduler_registration_ref='synthetic-scheduler-registration',registration_revision=target['revision'],
            observed_at=now,outbox_id=row['outbox_id'] if ack else None,
            outbox_payload_hash=row['payload_hash'] if ack else None)
        if entry['record']!=expected_record or entry['receipt_hash']!=sha(expected_record):
            raise ValueError('synthetic source/run/clock/outbox binding differs')
        authority=grant['authority'];intent=authority['intent']
        expected_intent=dict(schema_version=1,signal_id=identity,subject=mutation['subject'],
            scope={'kind':'personal','owner_id':mutation['subject']},target_memory_id=target['memory_id'],
            target_revision=target['revision'],signal_kind=kind,trigger=trigger,trigger_hash=th,
            scheduler_registration_ref='synthetic-scheduler-registration',registration_revision=target['revision'],
            signal_receipt_id=identity+'-receipt',signal_receipt_hash=sha(expected_record),observed_at=now,
            transition_from=registration_state,transition_to=registration_state if ack else 'triggered',
            outbox_id=expected_record['outbox_id'],outbox_payload_hash=expected_record['outbox_payload_hash'],
            run_id=mutation['run_id'],operation_id=identity+'-operation')
        expected_intent['occurrence_key']=domain('simple-harness/prospective-signal-occurrence/v1',
            {k:expected_intent[k] for k in ('subject','target_memory_id','target_revision','signal_id',
                'signal_kind','signal_receipt_hash','scheduler_registration_ref','registration_revision')})
        if intent!=expected_intent:raise ValueError('signal exact intent differs from synthetic source')
        ih=domain('simple-harness/prospective-signal-intent/v1',intent)
        replay_identity=domain('simple-harness/prospective-signal-replay-identity/v1',
            dict(authority_id=identity+'-authority',intent_hash=ih,nonce=identity+'-nonce',issuer_ref='synthetic-scheduler-fixture'))
        expected_authority=dict(schema_version=1,authority_id=identity+'-authority',intent=intent,intent_hash=ih,
            issued_at=now-1,expires_at=now+300,nonce=identity+'-nonce',issuer_ref='synthetic-scheduler-fixture',
            replay_identity=replay_identity)
        ah=domain('simple-harness/prospective-signal-authority/v1',expected_authority)
        ref=dict(schema_version=1,authority_id=identity+'-authority',authority_hash=ah,
            issuer_ref='synthetic-scheduler-fixture',replay_identity=replay_identity)
        if authority!=expected_authority or grant['authority_hash']!=ah or grant['reference']!=ref or applied['reference']!=ref:
            raise ValueError('signal authority/expiry/ref differs')
        result=applied['result'];results.append(result)
        if (result!=applied['replay'] or applied['result_hash']!=applied['replay_hash']
                or applied['result_hash']!=domain('simple-harness-memory/prospective-signal-result/v1',result)
                or result['signal_id']!=identity or result['memory_id']!=target['memory_id']
                or result['base_revision']!=target['revision']
                or result['committed_revision']!=target['revision']+(0 if ack else 1)
                or result['lifecycle_state']!=expected_intent['transition_to']
                or result['outcome']!=('acknowledged' if ack else 'applied')
                or result['reason_code']!=('prospective_registration_acknowledged' if ack else 'prospective_trigger_matched')
                or result['decided_at']!=now):raise ValueError('signal actual result/replay differs')
    if results!=o['prospective_binding']['results']:raise ValueError('signal result proof differs')
    return results[-1]['committed_revision']
