"""Independent input/protocol bindings for the public applicability fixture. No SDK."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def domain(name, value):
    return sha({'domain': name, 'payload': value})


def check(observed, recipe, check_admitted_span):
    proof = observed['procedure_binding']
    seed = recipe['seed']
    states = {'draft', 'eligible', 'active', 'reinforced', 'revised', 'inapplicable', 'superseded'}
    if (seed['memory_type'] != 'procedure' or seed.get('state', 'active') not in states
            or (seed.get('epistemic', 'explicit_user') != 'explicit_user' and seed.get('state') != 'draft')
            or recipe['family'] not in {'lifecycle', 'epistemic', 'procedure_applicability'}):
        raise ValueError('procedure proof outside supported input scope')
    raw = seed['payload']['applicability']
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    app = dict(tool_id=raw['tool'], environment='public-procedure-fixture', tool_version=raw['version'],
        input_schema_hash=sha(schema), fingerprint_version=2)
    fingerprint = domain('simple-harness/procedure-applicability/v2', app)
    if proof['input_schema'] != schema or proof['applicability'] != app or proof['fingerprint'] != fingerprint:
        raise ValueError('procedure applicability differs from original tool/version and fixture schema')
    calls = observed['calls']
    grants = [e for e in calls if e['call'] == 'resolve_procedure_observation_authority']
    records = [e for e in calls if e['call'] == 'record_procedure_observation']
    registrations = [e for e in calls if e['call'] == 'register_procedure_conversation']
    if len(grants) != 1 or len(records) != 1 or len(registrations) != 1:
        raise ValueError('procedure actual public authority/record/replay calls differ')
    grant, record, registered = grants[0], records[0], registrations[0]
    authority = grant['authority']; intent = authority['intent']; span = intent['evidence_span']
    eid = 'procedure-applicability-evidence'
    # Reuse the independent admitted-byte verifier with this fixture's exact input.
    eh = check_admitted_span(observed, {'seed': {'payload': app}}, span, eid,
        'Fixture applicability declaration: ')
    registration = registered['registration']
    ingested = next(e for e in calls if e['call'] == 'ingest_committed_evidence' and e['evidence_id'] == eid)
    rh=domain('simple-harness/conversation-evidence-registration/v3',registration)
    admission=ingested['admission']
    if (registration['evidence_id']!=eid or registration['envelope_hash']!=eh
            or registration['admission_receipt_id']!=admission['receipt_id']
            or registration['admission_receipt_hash']!=span['admission_receipt_hash']
            or registered['registration_hash']!=rh
            or registered['reference']!={'registration_id':registration['registration_id'],
                'registration_hash':rh,'evidence_id':eid,'envelope_hash':eh}
            or registered['result'] != registered['reference']):
        raise ValueError('procedure registration differs from actual admitted evidence')
    metadata = registration['metadata']
    if (metadata['task_scope_id'] != 'procedure-fixture-task'
            or metadata['run_id'] != ingested['envelope']['run_id']
            or metadata['subject'] != 'principal-1' or metadata['evidence_id'] != eid
            or metadata['envelope_hash'] != eh or metadata['tool_causal_link'] is not None
            or metadata['role'] != 'user'):
        raise ValueError('procedure registered source/task/run differs')
    mr=registration['metadata_receipt']
    if (mr['metadata_hash']!=domain('simple-harness/conversation-evidence-metadata/v3',metadata)
            or mr['accepted'] is not True or mr['evidence_id']!=eid
            or mr['envelope_hash']!=eh or mr['run_id']!=metadata['run_id']
            or mr['subject']!=metadata['subject'] or mr['issuer_ref']!='host-procedure-fixture'
            or metadata['ordered_group_manifest_hash']!=sha([{'evidence_id':eid,'envelope_hash':eh,'item_ordinal':1}])):
        raise ValueError('procedure metadata receipt/source manifest differs')
    source = observed['sources'][-1]['receipt']['operations'][0]
    state = seed.get('state', 'active')
    state = 'eligible_for_activation' if state == 'eligible' else state
    expected = dict(schema_version=1, observation_id='procedure-snapshot', subject='principal-1',
        scope={'kind':'personal','owner_id':'principal-1'}, target_memory_id=source['memory_id'],
        target_revision=source['revision'], kind='applicability_snapshot', applicability=app, applicability_fingerprint=fingerprint,
        risk_level=seed['payload']['effective_risk'], hazard='none', task_scope_id='procedure-fixture-task',
        evidence_span=span, evidence_span_hash=domain('simple-harness/evidence-span-ref/v2', span), terminal_receipt_id=None, terminal_receipt_hash=None, outcome=None,
        attributable=False, observed_at=observed['recalls'][0]['now'], transition_from=state,
        transition_to=state, run_id=metadata['run_id'], operation_id='procedure-bind')
    if intent != expected:
        raise ValueError('procedure exact target/intent/source/snapshot-only binding differs')
    if (authority['authority_id']!='procedure-authority' or authority['issuer_ref']!='host-procedure-fixture'
            or authority['nonce']!='procedure-snapshot-nonce' or authority['schema_version']!=1
            or authority['issued_at']!=intent['observed_at']-1 or authority['expires_at']!=intent['observed_at']+300
            or authority['replay_identity']!=domain('simple-harness/procedure-observation-replay-identity/v1',
                {k:authority[k] for k in ('authority_id','intent_hash','nonce','issuer_ref')})):
        raise ValueError('procedure fixture issuance/replay identity differs')
    if (authority['intent_hash'] != domain('simple-harness/procedure-observation-intent/v1', intent)
            or grant['authority_hash'] != domain('simple-harness/procedure-observation-authority/v1', authority)
            or record['reference'] != grant['reference'] or proof['reference'] != record['reference']
            or record['reference']['authority_hash'] != grant['authority_hash']):
        raise ValueError('procedure authority/ref commitment differs')
    result = record['result']
    if (result != proof['result'] or result != record['replay']
            or record['result_hash'] != domain('simple-harness-memory/procedure-observation-result/v1', result)
            or record['replay_hash'] != record['result_hash']
            or result['memory_id'] != source['memory_id'] or result['base_revision'] != source['revision']
            or result['committed_revision'] != source['revision']+1 or result['lifecycle_state'] != state
            or result['observation_id'] != intent['observation_id'] or result['independent_successes'] != 0
            or result['reason_code'] != 'procedure_applicability_bound'
            or result['decided_at'] != intent['observed_at']):
        raise ValueError('procedure actual commit/replay result differs')
    current = [fingerprint]
    if recipe['family'] == 'procedure_applicability':
        row = recipe['applicability_contract']
        if row['bound_fingerprint'] != 'app-v2' or raw != {'tool':'git','version':'2'}:
            raise ValueError('original applicability label/input mapping differs')
        label = row['current_fingerprint']
        if label is None:
            current = []
        elif label == 'app-v3':
            current = [domain('simple-harness/procedure-applicability/v2', {**app,'tool_version':'3'})]
        elif label != 'app-v2':
            raise ValueError('original current applicability label differs')
    if any(r['context']['procedure_applicability_fingerprints'] != current for r in observed['recalls']):
        raise ValueError('procedure current recall fingerprint differs')
    return result['committed_revision'], {'evidence_id':eid, 'content_hash':eh}
