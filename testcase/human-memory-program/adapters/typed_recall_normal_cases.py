"""Input-only real public execution. Parent owns every acceptance assertion."""
import copy
import importlib.util
from pathlib import Path


def load(name):
    spec = importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def control_recipe(recipe):
    """Permitted sibling of a forbidden combination; only the axis under test changes.

    Payload, memory type, privacy, attributes and query stay byte-identical, so a candidate
    count difference between the forbidden and the control run inside the same database is
    attributable to that one axis and to nothing else.
    """
    if recipe['family'] == 'epistemic':
        seed = {k: v for k, v in copy.deepcopy(recipe['seed']).items() if k != 'state'}
        seed['epistemic'], seed['verification'] = 'explicit_user', 'source_bound'
        if seed['memory_type'] == 'prospective':
            seed['state'] = 'pending'
        elif seed['memory_type'] == 'procedure':
            seed['state'] = 'active'
        return {**copy.deepcopy(recipe), 'seed': seed, 'control_axis': 'epistemic_verification'}
    if recipe['family'] == 'disclosure':
        return {**copy.deepcopy(recipe), 'purpose': 'task_execution', 'control_axis': 'disclosure_purpose'}
    return None


def recall_params(recipe, seed=None):
    spec = recipe['seed'] if seed is None else seed
    payload = spec['payload']
    query = str(payload.get('object_value',payload.get('title',payload.get('name',payload.get('action')))))
    return dict(query=query,memory_types=(spec['memory_type'],),
        recipient=recipe.get('recipient','user_self'),purpose=recipe.get('purpose','personalization'),
        modes=recipe.get('modes',('full_text',)))


def projection_canary_probes(case, recipe):
    """Pure public DTO constructions for the half of the sealed canary a strict payload forbids.

    The sealed source_record carries six keys outside allowed_payload_fields. Three of them are
    planted for real elsewhere (the admitted evidence id, the classification and the mutation
    conflict_status); all six are additionally offered to the strict typed payload parser here.
    A refusal is a stronger witness than "stripped after the fact": the field cannot even enter.
    Nothing is asserted here; the parent oracle owns every verdict.
    """
    payload = case.payload(recipe['seed'])
    wire = payload.to_json()
    cls = type(payload)
    probes = {'payload_class': cls.__name__, 'clean_wire': wire, 'fields': {}}
    try:
        cls.from_json(dict(wire))
        probes['clean_accepted'] = True
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        probes['clean_accepted'] = False
        probes['clean_exception'] = {'type': type(exc).__name__, 'reason': str(exc)}
    for key, value in sorted(recipe['projection_canary'].items()):
        try:
            cls.from_json({**wire, key: value})
            probes['fields'][key] = {'constructed': True}
        except Exception as exc:  # noqa: BLE001 - the refusal itself is the observation
            probes['fields'][key] = {'constructed': False,
                'exception': {'type': type(exc).__name__, 'reason': str(exc)}}
    return probes


async def disclosure_dto_probes(case, recipe):
    """Pure public DTO constructions that localise the sealed SEALED_AUTHORITY_REQUIRED rule."""
    helper = load('typed_recall_case_manager')
    h = helper.h
    import dataclasses as dc
    base = case.disclosure
    reasons = base.reason_codes
    if recipe['recipient'].lower() == 'unknown':
        reasons = (*reasons, h.DisclosureReasonCode.UNKNOWN_RECIPIENT)
    audience = {'user_self':'user_self','household':'household','task_collaborator':'task_collaborators',
                'external_party':'external','public':'public','audit_reviewer':'auditor','unknown':'unknown'}
    probes = {}
    def attempt(name, **overrides):
        try:
            value = dc.replace(base, reason_codes=reasons, **overrides)
            probes[name] = {'constructed': True, 'context': value.to_json()}
        except Exception as exc:  # noqa: BLE001 - the refusal itself is the observation
            probes[name] = {'constructed': False, 'exception': {'type':type(exc).__name__,'reason':str(exc)}}
    cell = dict(recipient=h.DeliveryRecipient(recipe['recipient'].lower()),
                intended_audience=h.IntendedAudience(audience[recipe['recipient'].lower()]))
    attempt('cell_audit', **cell, purpose=h.DisclosurePurpose('audit'))
    attempt('cell_audit_with_decision', **cell, purpose=h.DisclosurePurpose('audit'),
            source=h.DisclosureSource('audit_access_decision'))
    attempt('cell_task_execution', **cell, purpose=h.DisclosurePurpose('task_execution'))
    attempt('audit_reviewer_audit_with_decision',
            recipient=h.DeliveryRecipient('audit_reviewer'), intended_audience=h.IntendedAudience('auditor'),
            purpose=h.DisclosurePurpose('audit'), source=h.DisclosureSource('audit_access_decision'))
    attempt('audit_reviewer_audit_without_decision',
            recipient=h.DeliveryRecipient('audit_reviewer'), intended_audience=h.IntendedAudience('auditor'),
            purpose=h.DisclosurePurpose('audit'))
    return probes


async def bind_and_recall(case, recipe, spec, target, *, key):
    """Establish the applicability/signal authority a type needs, then recall for real."""
    params = recall_params(recipe, spec)
    binding = None
    prospective = load('typed_recall_prospective_cases')
    procedure = load('typed_recall_procedure_cases')
    probe = {**recipe, 'seed': spec, 'family': 'epistemic'}
    if prospective.supported(probe):
        binding = await prospective.bind(case, probe, target)
    elif procedure.supported(probe):
        binding = await procedure.bind(case, probe, target)
        params['fingerprint'] = (binding['fingerprint'],)
    value = await case.recall(**params, key=key)
    value['replay'] = (await case.recall(**params, key=key))['execution']
    return value, binding


async def forbidden_control(case, recipe, observed):
    """Real zero-candidate witness for a combination the public contract refuses at ingress."""
    control = control_recipe(recipe)
    if control is None:
        return
    witness = {'control_recipe': control, 'sources_before_control': copy.deepcopy(case.sources)}
    import simple_harness_memory as memory_sdk
    await case.manager.register_principal_owner(case.principal,
        memory_sdk.MemoryScope.personal(case.principal.actor_id))
    if recipe['family'] == 'disclosure':
        witness['dto_probes'] = await disclosure_dto_probes(case, recipe)
        witness['recall_calls_after_refusal'] = [e['call'] for e in case.events
            if e['call'] == 'execute_typed_recall']
        value, _ = await bind_and_recall(case, control, control['seed'],
            observed['sources'][-1] if observed['sources'] else None, key='disclosure-control')
        witness['control_recall'] = value
        observed['forbidden'] = witness
        return
    if recipe['seed']['memory_type'] == 'prospective' and 'exception' not in observed:
        # The pair forced a non-authoritative lifecycle state. Probe the authoritative state a
        # scheduler registration would need, and read the public outbox for its command.
        probe = {**copy.deepcopy(recipe['seed']), 'state': 'pending'}
        case.events.append({'call':'authoritative_state_probe','input':probe})
        try:
            await case.seed(probe, operation_id='pending-probe', evidence_id='evidence-pending-probe')
            witness['authoritative_state_probe'] = {'admitted': True}
        except Exception as exc:  # noqa: BLE001 - the refusal itself is the observation
            witness['authoritative_state_probe'] = {'admitted': False,
                'exception': {'type':type(exc).__name__,'reason':str(exc)}}
        page = await case.manager.read_outbox(principal=case.principal, limit=100)
        case.events.append({'call':'read_prospective_outbox_probe','count':len(page.entries)})
        target_id = witness['sources_before_control'][-1]['receipt']['operations'][0]['memory_id']
        witness['registration_commands'] = [e.payload for e in page.entries
            if e.topic == 'memory.prospective.registration.requested' and e.payload['memory_id'] == target_id]
    if not case.admitted:
        await case.evidence('Forbidden-combination anchor evidence', 'evidence-anchor')
    params = recall_params(recipe)
    zero = await case.recall(**params, key='forbidden-zero')
    zero['replay'] = (await case.recall(**params, key='forbidden-zero'))['execution']
    witness['zero_recall'] = zero
    witness['forbidden_evidence_ids'] = sorted(case.admitted)
    target = await case.seed(control['seed'], operation_id='control-1', evidence_id='evidence-control-1')
    witness['control_target'] = target.to_json()
    value, binding = await bind_and_recall(case, control, control['seed'], target, key='forbidden-control')
    witness['control_recall'] = value
    witness['control_binding'] = binding
    witness['control_source'] = case.sources[-1]
    observed['forbidden'] = witness


async def run_cases(recipes, workspace):
    helper = load('typed_recall_case_manager')
    rows = []
    for index, recipe in enumerate(recipes):
        case = helper.CaseManager(workspace/f'normal-{index}.sqlite',
            privacy=recipe.get('privacy','personal'),attributes=recipe.get('attributes',()),
            now=helper.seconds(recipe.get('now',1788170400.0)))
        observed = {'recipe':recipe,'calls':case.events,'sources':case.sources,'recalls':[]}
        opened = False
        try:
            await case.open()
            opened = True
            if recipe['family']=='prospective_trigger':
                observed=await load('typed_recall_trigger_cases').run(case,recipe)
                rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':observed})
                continue
            lifecycle=load('typed_recall_prospective_lifecycle_cases')
            if lifecycle.supported(recipe):
                observed=await lifecycle.run(case,recipe)
                rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':observed})
                continue
            path=recipe.get('lifecycle_path',[recipe['seed']])
            prospective=load('typed_recall_prospective_cases')
            if prospective.supported(recipe) and recipe['seed'].get('state')=='triggered':
                path=[{**recipe['seed'],'state':'pending'}]
            if recipe['family']=='projection':
                observed['projection_canary']=projection_canary_probes(case,recipe)
            previous=None
            for ordinal,spec in enumerate(path):
                kwargs={} if ordinal==0 else dict(kind='supersede' if spec['state']=='superseded' else 'revise',
                    target=helper.h.ExistingMemoryTarget(previous.memory_id,previous.revision))
                # The projection cell admits the sealed canary evidence id itself, so the
                # forbidden identifier really exists in the database behind the recalled item.
                evidence_id=(recipe['projection_canary']['evidence_ids'][0]
                             if recipe['family']=='projection' else f'evidence-case-{ordinal+1}')
                previous=await case.seed(spec,operation_id=f'create-{ordinal+1}',
                    evidence_id=evidence_id,**kwargs)
            if prospective.supported(recipe):
                observed['prospective_binding']=await prospective.bind(case,recipe,previous)
            procedure = load('typed_recall_procedure_cases')
            if procedure.supported(recipe):
                observed['procedure_binding'] = await procedure.bind(case, recipe, previous)
            payload = recipe['seed']['payload']
            query = str(payload.get('object_value',payload.get('title',payload.get('name',payload.get('action')))))
            params = dict(query=query,memory_types=(recipe['seed']['memory_type'],),
                recipient=recipe.get('recipient','user_self'),purpose=recipe.get('purpose','personalization'),
                modes=recipe.get('modes',('full_text',)))
            if 'procedure_binding' in observed:
                params['fingerprint'] = (procedure.current_fingerprints(recipe, observed['procedure_binding'])
                    if recipe['family']=='procedure_applicability' else (observed['procedure_binding']['fingerprint'],))
            if recipe['family']=='budget':
                limits = dict(max_items=8,max_bytes=16384,max_tokens=2048,deadline_ms=2000)
                for ordinal, limit in enumerate(recipe['limits']):
                    limits.update(limit)
                    value = await case.recall(**params,budget=limits.copy(),key=f'budget-{ordinal}')
                    value['replay'] = (await case.recall(**params,budget=limits.copy(),key=f'budget-{ordinal}'))['execution']
                    observed['recalls'].append(value)
            else:
                value = await case.recall(**params)
                value['replay'] = (await case.recall(**params))['execution']
                observed['recalls'].append(value)
            if (recipe['family']=='epistemic' and recipe['seed']['memory_type']=='prospective'
                    and 'prospective_binding' not in observed):
                # The epistemic pair forced a non-authoritative state that can never be
                # registered; the paired control proves the query itself is not vacuous.
                await forbidden_control(case, recipe, observed)
        except Exception as exc:
            observed['exception'] = {'type':type(exc).__name__,'reason':str(exc)}
            if opened:
                try:
                    await forbidden_control(case, recipe, observed)
                except Exception as control_exc:  # noqa: BLE001 - recorded, never swallowed
                    observed['control_exception'] = {'type':type(control_exc).__name__,'reason':str(control_exc)}
        finally:
            if opened:
                await case.close()
        rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':observed})
    return rows
