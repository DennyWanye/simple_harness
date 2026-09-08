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
            or recipe['family'] not in {'lifecycle', 'epistemic', 'procedure_applicability', 'projection'}):
        raise ValueError('procedure proof outside supported input scope')
    raw = seed['payload']['applicability']
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    app = dict(tool_id=raw['tool'], environment='public-procedure-fixture', tool_version=raw['version'],
        input_schema_hash=sha(schema), fingerprint_version=2)
    fingerprint = domain('simple-harness/procedure-applicability/v2', app)
    if proof['input_schema'] != schema or proof['applicability'] != app or proof['fingerprint'] != fingerprint:
        raise ValueError('procedure applicability differs from original tool/version and fixture schema')
    calls = observed['calls']
    promotion = proof.get('promotion')
    steps = 0 if promotion is None else len(promotion)
    grants = [e for e in calls if e['call'] == 'resolve_procedure_observation_authority']
    records = [e for e in calls if e['call'] == 'record_procedure_observation']
    registrations = [e for e in calls if e['call'] == 'register_procedure_conversation']
    # The applicability snapshot is always exactly one authority/record/registration; a promotion
    # adds exactly one authority, one record and two conversation items per observation.
    if len(grants) != 1 + steps or len(records) != 1 + steps or len(registrations) != 1:
        raise ValueError('procedure actual public authority/record/replay calls differ')
    snapshot_records = [e for e in records if e['result']['observation_id'] == 'procedure-snapshot']
    snapshot_grants = [e for e in grants if e['authority']['intent']['observation_id'] == 'procedure-snapshot']
    if len(snapshot_records) != 1 or len(snapshot_grants) != 1:
        raise ValueError('procedure applicability snapshot is not exactly one observation')
    grant, record, registered = snapshot_grants[0], snapshot_records[0], registrations[0]
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
    committed = result['committed_revision']
    refs = [{'evidence_id': eid, 'content_hash': eh}]
    if promotion is not None:
        committed, promoted_refs = check_promotion(observed, recipe, proof, calls, app, fingerprint,
                                                   memory_id=source['memory_id'], base_revision=committed)
        refs += promoted_refs
    elif required_promotion(recipe):
        raise ValueError('observed repeated-observation Procedure was not promoted')
    return committed, refs


def required_promotion(recipe):
    seed = recipe['seed']
    return (recipe['family'] == 'epistemic' and seed['memory_type'] == 'procedure'
            and seed.get('epistemic') == 'observed_behavior'
            and seed.get('verification') == 'repeated_observation')


PROMOTION_STEPS = (('draft', 'draft', 1), ('draft', 'eligible_for_activation', 2),
                   ('eligible_for_activation', 'active', 3))


def check_promotion(observed, recipe, proof, calls, app, fingerprint, *, memory_id, base_revision):
    """Three real attributable low-risk successes carry a draft Procedure head to active.

    Memory never activates a non-explicit-epistemic Procedure on create, so the sealed
    ELIGIBLE_WITH_APPLICABILITY row can only be reached through the public observation
    promotion. Each step is bound here to its own Host tool terminal receipt, its own registered
    tool-result conversation item and its own task scope, and the lifecycle threshold is checked
    against the independent success count Memory reports, never against a runner literal.
    """
    if not required_promotion(recipe):
        raise ValueError('promotion executed outside the sealed repeated-observation row')
    promotion = proof['promotion']
    if len(promotion) != len(PROMOTION_STEPS):
        raise ValueError('procedure promotion did not run the three sealed observations')
    revision = base_revision
    scopes, receipts, refs = set(), set(), []
    ingested = {e['evidence_id']: e for e in calls if e['call'] == 'ingest_committed_evidence'}
    for step, (transition_from, transition_to, successes) in zip(promotion, PROMOTION_STEPS):
        ordinal = step['ordinal']
        grant = next(e for e in calls if e['call'] == 'resolve_procedure_observation_authority'
                     and e['authority']['intent']['observation_id'] == f'procedure-observation-{ordinal}')
        record = next(e for e in calls if e['call'] == 'record_procedure_observation'
                      and e['result']['observation_id'] == f'procedure-observation-{ordinal}')
        authority = grant['authority']
        intent = authority['intent']
        eid = f'procedure-observation-evidence-{ordinal}'
        expected = dict(schema_version=1, observation_id=f'procedure-observation-{ordinal}',
            subject='principal-1', scope={'kind': 'personal', 'owner_id': 'principal-1'},
            target_memory_id=memory_id, target_revision=revision, kind='terminal_outcome',
            applicability=app, applicability_fingerprint=fingerprint,
            risk_level=recipe['seed']['payload']['effective_risk'], hazard='none',
            task_scope_id=step['task_scope_id'], evidence_span=intent['evidence_span'],
            evidence_span_hash=domain('simple-harness/evidence-span-ref/v2', intent['evidence_span']),
            terminal_receipt_id=step['terminal_receipt_id'],
            terminal_receipt_hash=step['terminal_receipt_hash'], outcome='success',
            attributable=True, observed_at=observed['recalls'][0]['now'],
            transition_from=transition_from, transition_to=transition_to,
            run_id=intent['run_id'], operation_id=f'procedure-observe-{ordinal}')
        if intent != expected or intent['evidence_span']['evidence_id'] != eid:
            raise ValueError('procedure promotion intent differs from its exact public inputs')
        if (step['terminal_receipt_hash'] != sha({'terminal_receipt': step['terminal_receipt_id']})
                or step['task_scope_id'] in scopes or step['terminal_receipt_id'] in receipts):
            raise ValueError('promotion observations are not independent scopes/terminal receipts')
        scopes.add(step['task_scope_id']); receipts.add(step['terminal_receipt_id'])
        if (authority['intent_hash'] != domain('simple-harness/procedure-observation-intent/v1', intent)
                or grant['authority_hash'] != domain('simple-harness/procedure-observation-authority/v1', authority)
                or record['reference'] != grant['reference']
                or record['reference']['authority_hash'] != grant['authority_hash']
                or authority['issued_at'] != intent['observed_at'] - 1
                or authority['expires_at'] != intent['observed_at'] + 300):
            raise ValueError('promotion authority/ref commitment differs')
        items = [e for e in calls if e['call'] == 'register_procedure_observation_conversation'
                 and e['ordinal'] == ordinal]
        if len(items) != 2:
            raise ValueError('promotion observation is not one complete two-item causal group')
        call_item, tool_item = sorted(items, key=lambda e: e['item_ordinal'])
        tool_metadata = tool_item['registration']['metadata']
        if (tool_metadata['role'] != 'tool' or call_item['registration']['metadata']['role'] != 'user'
                or tool_metadata['task_scope_id'] != step['task_scope_id']
                or tool_metadata['run_id'] != intent['run_id']
                or tool_metadata['evidence_id'] != eid
                or tool_metadata['tool_causal_link'] != {'tool_call_id': f'tool-call-{ordinal}',
                    'tool_name': 'release-check', 'parent_item_ordinal': 1,
                    'terminal_receipt_id': step['terminal_receipt_id'],
                    'terminal_receipt_hash': step['terminal_receipt_hash']}
                or call_item['registration']['metadata']['tool_causal_link'] is not None):
            raise ValueError('promotion terminal receipt is not carried by a registered tool item')
        result = record['result']
        if (result != step['result'] or result != record['replay']
                or record['result_hash'] != domain('simple-harness-memory/procedure-observation-result/v1', result)
                or record['replay_hash'] != record['result_hash']
                or result['memory_id'] != memory_id or result['base_revision'] != revision
                or result['committed_revision'] != revision + 1
                or result['lifecycle_state'] != transition_to
                or result['independent_successes'] != successes
                or result['reason_code'] != 'procedure_low_risk_success'
                or result['decided_at'] != intent['observed_at']):
            raise ValueError('procedure promotion commit/replay result differs')
        revision = result['committed_revision']
        for identifier, kind in ((f'procedure-observation-call-{ordinal}', 'user_message'),
                                 (eid, 'tool_result')):
            entry = ingested.get(identifier)
            if (entry is None or entry['envelope']['evidence_id'] != identifier
                    or entry['envelope']['source_kind'] != kind):
                raise ValueError('promotion evidence was not really admitted under its own kind')
            refs.append({'evidence_id': identifier, 'content_hash': entry['envelope_hash']})
    if promotion[-1]['result']['lifecycle_state'] != 'active':
        raise ValueError('the three observations did not carry the draft head to active')
    return revision, refs
