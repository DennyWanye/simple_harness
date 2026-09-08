"""Independent oracle for sealed INELIGIBLE cells the public contract refuses before recall.

The sealed obligation of an INELIGIBLE cell is `eligibility_common_expectations.INELIGIBLE`:
`enters_rank_input: false`, `rank_input_delta: 0`, `public_candidate_count_delta: 0`,
`forbidden_canary_hits: 0`. It carries no reason-code field, so a public contract that refuses
the combination at ingress discharges that obligation in its strongest form: the forbidden
row can never exist, hence can never reach a rank input.

A refusal alone is not accepted as proof. Every cell here also carries a paired control that
runs inside the same database with the same query, disclosure, privacy and payload and differs
only in the axis under test, so the zero is attributable to that axis and not to an empty
store, a non-matching query or a missing precondition. Standard library only.
"""
import hashlib
import json

VERSION = 1

# Exact public refusal for each forbidden epistemic pair on the pinned candidate. A change in
# the refusal (type or reason) makes the cell FAIL rather than silently continue to pass.
INGRESS_REFUSALS = {
    ('verified_external', 'unverified'): ('ValueError', 'verified_external requires source_verified state'),
    ('verified_external', 'source_bound'): ('ValueError', 'verified_external requires source_verified state'),
    ('verified_external', 'user_confirmed'): ('ValueError', 'verified_external requires source_verified state'),
    ('verified_external', 'repeated_observation'): ('ValueError', 'verified_external requires source_verified state'),
    ('llm_inference', 'source_verified'): ('ValueError', 'verified states require trusted typed observation evidence'),
    ('llm_inference', 'repeated_observation'): ('ValueError', 'verified states require trusted typed observation evidence'),
    ('unknown', 'source_verified'): ('ValueError', 'verified states require trusted typed observation evidence'),
    ('unknown', 'repeated_observation'): ('ValueError', 'verified states require trusted typed observation evidence'),
    ('llm_inference', 'source_bound'): ('MemoryValidationError', 'mutation_inference_must_be_unverified'),
    ('llm_inference', 'user_confirmed'): ('MemoryValidationError', 'mutation_inference_must_be_unverified'),
    ('unknown', 'source_bound'): ('MemoryValidationError', 'mutation_unknown_must_be_unverified'),
    ('unknown', 'user_confirmed'): ('MemoryValidationError', 'mutation_unknown_must_be_unverified'),
}

AUDIT_REFUSAL = ('ValueError', 'audit disclosure requires an AuditAccessDecision and audit recipient')

NON_AUTHORITATIVE = {'llm_inference': 'mutation_inference_cannot_be_authoritative',
                     'unknown': 'mutation_unknown_cannot_be_authoritative'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def supported(observed):
    return 'forbidden' in observed


def check_recall(shared, recall, *, items):
    wire = recall['execution']
    decision, result = wire['decision'], wire['result']
    shared['check_execution_wire'](wire, recall['context'], recall['plan'])
    require(len(result['items']) == items and decision['filtered_candidate_count'] == items
            and decision['outcome'] == ('recall' if items else 'no_recall')
            and decision['reason_codes'] == (['recall_user_fact_dependency'] if items else ['recall_no_eligible_memory'])
            and decision['candidate_count_stage'] == 'after_all_eligibility_gates'
            and wire['candidate_query_started'] and wire['candidate_query_count'] == 1
            and not result['confirmation_groups'],
            'actual recall candidate/decision shape differs')
    replay = recall['replay']
    require(replay['replayed'] and replay['candidate_query_count'] == 0
            and replay['result'] == result and replay['decision'] == decision,
            'recall durable exact replay differs')
    return result


def check_disclosure_control(shared, recall, *, allowed):
    """The permitted-purpose control: one item when the sealed row allows it, else a real denial.

    A recipient/purpose denial is decided before the candidate layer (`rejected` /
    `recall_disclosure_denied`, zero candidate queries); a privacy-class denial is decided by the
    eligibility gates (`no_recall`, one candidate query). Both branches are pinned exactly.
    """
    if allowed:
        return check_recall(shared, recall, items=1)
    wire = recall['execution']
    decision, result = wire['decision'], wire['result']
    shared['check_execution_wire'](wire, recall['context'], recall['plan'])
    gate = (decision['outcome'] == 'rejected' and decision['reason_codes'] == ['recall_disclosure_denied']
            and wire['candidate_query_count'] == 0 and not wire['candidate_query_started'])
    candidate = (decision['outcome'] == 'no_recall' and decision['reason_codes'] == ['recall_no_eligible_memory']
                 and wire['candidate_query_count'] == 1 and wire['candidate_query_started'])
    require(not result['items'] and decision['filtered_candidate_count'] == 0
            and decision['candidate_count_stage'] == 'after_all_eligibility_gates' and (gate or candidate),
            'permitted-purpose control denial is neither a disclosure-gate nor an eligibility-gate zero')
    replay = recall['replay']
    require(replay['replayed'] and replay['candidate_query_count'] == 0
            and replay['result'] == result and replay['decision'] == decision,
            'control denial durable exact replay differs')
    return result


def check_control(shared, witness, recipe, observed, kind):
    """The permitted sibling really lands in the rank input for the same query."""
    control = witness['control_recipe']
    seed = control['seed']
    require(seed['memory_type'] == kind and seed['epistemic'] == 'explicit_user'
            and seed['verification'] == 'source_bound'
            and seed['payload'] == recipe['seed']['payload']
            and control.get('privacy') == recipe.get('privacy')
            and control.get('attributes') == recipe.get('attributes')
            and control.get('recipient') == recipe.get('recipient'),
            'control differs from the cell in more than the axis under test')
    result = check_recall(shared, witness['control_recall'], items=1)
    source = witness['control_source']
    target = source['receipt']['operations'][0]
    projection = shared['normal_projection'](seed)
    item = result['items'][0]
    selected = item['selected_item']
    binding = witness.get('control_binding') or {}
    # A Procedure applicability snapshot commits a new head revision; every other control head
    # stays at the create revision. Both are pinned, never merely tolerated.
    head = binding.get('result', {}).get('committed_revision', target['revision'])
    require(binding.get('result', {}).get('base_revision', target['revision']) == target['revision']
            and head in {target['revision'], target['revision'] + 1},
            'control head revision moved outside the applicability snapshot')
    require(selected['source_ref'] == target['memory_id'] and selected['source_revision'] == head
            and target['revision'] == 1 and selected['memory_type'] == kind
            and selected['source_kind'] == 'cognitive_memory'
            and item['public_payload'] == projection
            and selected['public_payload_hash'] == digest(projection),
            'control item is not the permitted sibling head: '
            + canonical({'source_ref': selected['source_ref'], 'memory_id': target['memory_id'],
                         'revision': [selected['source_revision'], target['revision']],
                         'payload': item['public_payload'], 'projection': projection}))
    mutation = next(e['plan'] for e in observed['calls']
                    if e['call'] == 'apply_memory_mutation_plan' and e['plan']['plan_id'] == source['receipt']['plan_id'])
    operation = mutation['operations'][0]
    require(operation['epistemic_status'] == 'explicit_user' and operation['verification_state'] == 'source_bound'
            and operation['kind'] == 'create', 'control mutation is not the admitted epistemic pair')
    return result


def check_canary(witness, *results):
    """No evidence identity of the refused combination reaches any public result."""
    hits = [eid for eid in witness.get('forbidden_evidence_ids', [])
            for result in results if eid in canonical(result)]
    require(not hits, 'forbidden canary reached a public result: ' + canonical(sorted(set(hits))))


def assess_disclosure(fixture, recipe, observed, shared):
    witness = observed['forbidden']
    probes = witness['dto_probes']
    require(recipe['purpose'] == 'AUDIT', 'disclosure refusal outside the AUDIT purpose')
    require(observed['exception'] == {'type': AUDIT_REFUSAL[0], 'reason': AUDIT_REFUSAL[1]},
            'AUDIT disclosure refusal differs from the frozen public contract text')
    for name in ('cell_audit', 'cell_audit_with_decision', 'audit_reviewer_audit_without_decision'):
        require(probes[name]['constructed'] is False
                and probes[name]['exception'] == {'type': AUDIT_REFUSAL[0], 'reason': AUDIT_REFUSAL[1]},
                'AUDIT refusal is not attributable to the sealed authority pair: ' + name)
    for name in ('cell_task_execution', 'audit_reviewer_audit_with_decision'):
        require(probes[name]['constructed'] is True, 'permitted disclosure control was refused: ' + name)
    require(probes['cell_task_execution']['context']['recipient'] == recipe['recipient'].lower()
            and probes['cell_audit']['exception'] == probes['cell_audit_with_decision']['exception'],
            'disclosure probes do not isolate recipient and audit-decision halves')
    require(witness['recall_calls_after_refusal'] == [], 'Memory recall was invoked for the refused AUDIT pair')
    require(len(witness['sources_before_control']) == 1
            and witness['sources_before_control'][0]['receipt']['operations'][0]['revision'] == 1,
            'AUDIT cell has no durable seeded memory to be denied')
    shared['check_seed_authority'](observed, recipe, shared['semantic_source'](**recipe['seed']['payload']),
                                   check_recall_refs=False)
    control = witness['control_recipe']
    require(control['purpose'] == 'task_execution' and control['recipient'] == recipe['recipient']
            and control['privacy'] == recipe['privacy'], 'disclosure control changes more than the purpose')
    row = next((r for r in fixture['disclosure_cases']
                if r['recipient'] == control['recipient'] and r['purpose'] == control['purpose']), None)
    require(row is not None, 'sealed disclosure control row missing')
    check_disclosure_control(shared, witness['control_recall'], allowed=recipe['privacy'] in row['allowed'])
    require(recipe['privacy'] in row['allowed'] or recipe['privacy'] in row['denied'],
            'sealed control row does not classify this privacy class')
    sealed = next((r for r in fixture['disclosure_cases']
                   if r['recipient'] == recipe['recipient'] and r['purpose'] == 'AUDIT'), None)
    if sealed is not None:
        require(sealed['allowed'] == [] and sealed['reason'] == 'SEALED_AUTHORITY_REQUIRED'
                and recipe['privacy'] in sealed['denied'], 'sealed AUDIT row is not a total denial')
    return ['sealed AUDIT row and public DisclosureContext both require the sealed audit authority',
            'exact public DTO refusal with recipient/audit-decision halves isolated',
            'zero Memory recall calls for the refused pair with a durable seeded memory present',
            'same-recipient permitted-purpose control recall matches the sealed allowed/denied row']


def assess_epistemic(fixture, recipe, observed, shared):
    witness = observed['forbidden']
    seed = recipe['seed']
    kind = seed['memory_type']
    pair = (seed['epistemic'], seed['verification'])
    if 'exception' in observed:
        expected = INGRESS_REFUSALS.get(pair)
        require(expected is not None, 'refused pair is not a sealed forbidden combination: ' + canonical(pair))
        require(observed['exception'] == {'type': expected[0], 'reason': expected[1]},
                'ingress refusal differs from the frozen public contract text')
        require(not witness['sources_before_control'], 'a refused combination produced a durable source')
        checks = ['exact public ingress refusal for the forbidden epistemic pair']
    else:
        require(kind == 'prospective' and seed['epistemic'] in NON_AUTHORITATIVE
                and seed['verification'] == 'unverified',
                'non-refused forbidden cell outside the non-authoritative prospective pair')
        probe = witness['authoritative_state_probe']
        require(probe['admitted'] is False
                and probe['exception'] == {'type': 'MemoryValidationError',
                                           'reason': NON_AUTHORITATIVE[seed['epistemic']]},
                'the authoritative-state probe was not refused with the frozen reason')
        require(witness['registration_commands'] == [],
                'a non-authoritative prospective memory emitted a registration command')
        sources = witness['sources_before_control']
        require(len(sources) == 1, 'exactly one non-authoritative source required')
        target = sources[0]['receipt']['operations'][0]
        mutation = next(e['plan'] for e in observed['calls']
                        if e['call'] == 'apply_memory_mutation_plan' and e['plan']['plan_id'] == sources[0]['receipt']['plan_id'])
        operation = mutation['operations'][0]
        require(operation['lifecycle_state'] == 'candidate' and operation['epistemic_status'] == seed['epistemic']
                and operation['verification_state'] == 'unverified' and target['revision'] == 1,
                'the seeded head is not the candidate-state memory under test')
        check_recall(shared, observed['recalls'][0], items=0)
        checks = ['non-authoritative state forced by the epistemic pair, with the authoritative state refused',
                  'no public prospective registration command exists for that head']
    check_recall(shared, witness['zero_recall'], items=0)
    control = check_control(shared, witness, recipe, observed, kind)
    check_canary(witness, witness['zero_recall']['execution']['result'], control)
    return checks + ['real zero-candidate recall on the same database, query and disclosure',
                     'permitted-sibling control recall returns exactly the one expected head',
                     'no refused-combination evidence identity in any public result']


def assess(fixture, cell, shared, recipe, expected_eligible):
    observed = cell['observations']
    try:
        require(expected_eligible is False, 'a forbidden-combination cell must be sealed INELIGIBLE')
        if recipe['family'] == 'disclosure':
            checks = assess_disclosure(fixture, recipe, observed, shared)
        elif recipe['family'] == 'epistemic':
            checks = assess_epistemic(fixture, recipe, observed, shared)
        else:
            return {'status': 'BLOCKED', 'reason': 'FORBIDDEN_WITNESS_FAMILY_UNSUPPORTED:' + recipe['family'],
                    'business_assertions': []}
        require('control_exception' not in observed, 'control run raised: ' + canonical(observed.get('control_exception')))
        common = fixture['eligibility_common_expectations']['INELIGIBLE']
        require(common == {'enters_rank_input': False, 'rank_input_delta': 0,
                           'public_candidate_count_delta': 0, 'forbidden_canary_hits': 0},
                'sealed INELIGIBLE common expectation changed')
        return {'status': 'PASS', 'reason': '', 'business_assertions': checks}
    except Exception as exc:  # noqa: BLE001 - a failed assertion is a cell failure
        return {'status': 'FAIL', 'reason': type(exc).__name__ + ': ' + str(exc), 'business_assertions': []}
