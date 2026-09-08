"""Public fixture-owned applicability snapshot; no fabricated terminal outcome."""
import dataclasses as dc
import hashlib
import json

import simple_harness as h
import simple_harness_memory as m


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


SNAPSHOT_STATES = {'draft', 'eligible', 'active', 'reinforced', 'revised', 'inapplicable', 'superseded'}


def lifecycle_state(recipe):
    state = recipe['seed'].get('state', 'active')
    return 'eligible_for_activation' if state == 'eligible' else state


def promotion_required(recipe):
    """The sealed ELIGIBLE_WITH_APPLICABILITY row for an observed, repeatedly verified Procedure.

    Memory refuses to activate a non-explicit-epistemic Procedure on create, so the only public
    road to an active head is the real observation promotion: three attributable low-risk
    successes, each backed by a registered tool terminal receipt.
    """
    seed = recipe['seed']
    return (recipe['family'] == 'epistemic' and seed['memory_type'] == 'procedure'
            and seed.get('epistemic') == 'observed_behavior'
            and seed.get('verification') == 'repeated_observation')


PROMOTION_STEPS = (('draft', 'draft'), ('draft', 'eligible_for_activation'),
                   ('eligible_for_activation', 'active'))


async def register_observation_item(case, *, ordinal, item_ordinal, eid, envelope, manifest, link,
                                    role, task_scope):
    admitted = case.admitted[eid]
    admission = admitted.receipt
    metadata = h.ConversationEvidenceMetadata(
        metadata_id=f'procedure-observation-metadata-{ordinal}-{item_ordinal}',
        authority_issuer_id='host-procedure-fixture', evidence_id=eid,
        envelope_hash=envelope.envelope_hash, admission_receipt_id=admission.receipt_id,
        admission_receipt_hash=admission.receipt_hash, run_id=envelope.run_id,
        subject=envelope.subject, source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash, conversation_id='procedure-fixture-conversation',
        primary_conversation_id='procedure-fixture-conversation',
        causal_group_id=f'procedure-observation-group-{ordinal}', causal_group_sequence=ordinal + 1,
        item_ordinal=item_ordinal, group_item_count=2, ordered_group_manifest_hash=digest(manifest),
        role=role, occurred_at=case.now, task_scope_id=task_scope, tool_causal_link=link, entities=())
    metadata = h.authorize_conversation_public_text(metadata, admitted)
    receipt = h.ConversationEvidenceMetadataReceipt(
        receipt_id=f'procedure-observation-metadata-receipt-{ordinal}-{item_ordinal}',
        metadata_id=metadata.metadata_id, authority_issuer_id=metadata.authority_issuer_id,
        evidence_id=eid, envelope_hash=envelope.envelope_hash,
        admission_receipt_id=admission.receipt_id, admission_receipt_hash=admission.receipt_hash,
        run_id=envelope.run_id, subject=envelope.subject, source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash, metadata_hash=metadata.metadata_hash,
        issuer_ref=metadata.authority_issuer_id, accepted=True)
    registration = h.ConversationEvidenceRegistration(
        f'procedure-observation-registration-{ordinal}-{item_ordinal}', envelope, admission,
        metadata, receipt, admitted.item_authority)
    reference = h.ConversationEvidenceRegistrationRef(registration.registration_id,
        registration.registration_hash, eid, envelope.envelope_hash)
    case.conversations[registration.registration_id] = registration
    event = {'call': 'register_procedure_observation_conversation', 'ordinal': ordinal,
             'item_ordinal': item_ordinal, 'registration': registration.to_json(),
             'registration_hash': registration.registration_hash, 'reference': dc.asdict(reference)}
    case.events.append(event)
    event['result'] = dc.asdict(await case.manager.register_conversation_evidence(reference))
    return registration


async def observe(case, recipe, *, memory_id, revision, ordinal, applicability):
    """One real terminal-outcome observation: tool call + tool result + Host authority."""
    transition_from, transition_to = PROMOTION_STEPS[ordinal - 1]
    task_scope = f'procedure-observation-scope-{ordinal}'
    receipt_id = f'tool-terminal-{ordinal}'
    receipt_hash = digest({'terminal_receipt': receipt_id})
    call_eid = f'procedure-observation-call-{ordinal}'
    tool_eid = f'procedure-observation-evidence-{ordinal}'
    call_envelope, _ = await case.evidence(f'Fixture procedure call {ordinal}', call_eid)
    tool_envelope, span = await case.evidence(f'Fixture terminal outcome observation {ordinal}',
                                              tool_eid, source_kind=h.EvidenceSourceKind.TOOL_RESULT)
    manifest = [{'evidence_id': call_eid, 'envelope_hash': call_envelope.envelope_hash, 'item_ordinal': 1},
                {'evidence_id': tool_eid, 'envelope_hash': tool_envelope.envelope_hash, 'item_ordinal': 2}]
    link = h.ConversationToolCausalLink(f'tool-call-{ordinal}', 'release-check', 1, receipt_id, receipt_hash)
    await register_observation_item(case, ordinal=ordinal, item_ordinal=1, eid=call_eid,
        envelope=call_envelope, manifest=manifest, link=None,
        role=h.ConversationEvidenceRole.USER, task_scope=task_scope)
    await register_observation_item(case, ordinal=ordinal, item_ordinal=2, eid=tool_eid,
        envelope=tool_envelope, manifest=manifest, link=link,
        role=h.ConversationEvidenceRole.TOOL, task_scope=task_scope)
    intent = h.ProcedureObservationIntent(observation_id=f'procedure-observation-{ordinal}',
        subject=case.principal.actor_id, scope=h.MemoryScopeRef('personal', case.principal.actor_id),
        target_memory_id=memory_id, target_revision=revision,
        kind=h.ProcedureObservationKind.TERMINAL_OUTCOME, applicability=applicability,
        risk_level=h.ProcedureRiskLevel(recipe['seed']['payload']['effective_risk']),
        hazard=h.ProcedureHazard.NONE, task_scope_id=task_scope, evidence_span=span,
        terminal_receipt_id=receipt_id, terminal_receipt_hash=receipt_hash,
        outcome=h.ProcedureObservationOutcome.SUCCESS, attributable=True, observed_at=case.now,
        transition_from=h.ProcedureLifecycleState(transition_from),
        transition_to=h.ProcedureLifecycleState(transition_to),
        run_id=tool_envelope.run_id, operation_id=f'procedure-observe-{ordinal}')
    authority = h.issue_procedure_observation_authority(intent,
        authority_id=f'procedure-observation-authority-{ordinal}', issued_at=case.now - 1,
        expires_at=case.now + 300, nonce=f'procedure-observation-nonce-{ordinal}',
        issuer_ref='host-procedure-fixture')
    case.procedure_authorities[authority.authority_id] = authority
    reference = h.ProcedureObservationAuthorityRef.from_authority(authority)
    event = {'call': 'record_procedure_observation', 'reference': reference.to_json()}
    case.events.append(event)
    result = await case.manager.record_procedure_observation(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id), reference=reference)
    event.update(result=result.to_json(), result_hash=result.result_hash)
    replay = await case.manager.record_procedure_observation(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id), reference=reference)
    event.update(replay=replay.to_json(), replay_hash=replay.result_hash)
    return {'ordinal': ordinal, 'task_scope_id': task_scope, 'terminal_receipt_id': receipt_id,
            'terminal_receipt_hash': receipt_hash, 'result': result.to_json(),
            'reference': reference.to_json()}


async def promote(case, recipe, applicability, memory_id, revision):
    steps = []
    for ordinal in range(1, len(PROMOTION_STEPS) + 1):
        step = await observe(case, recipe, memory_id=memory_id, revision=revision,
                             ordinal=ordinal, applicability=applicability)
        revision = step['result']['committed_revision']
        steps.append(step)
    return steps


def supported(recipe):
    # Applicability is bound on the actual final head whatever its lifecycle state, so that an
    # ineligible verdict is attributable to the state/epistemic under test, not to a missing
    # fingerprint. Non-explicit epistemic Procedures only exist publicly as draft (Memory refuses
    # to activate them on create); their draft head still receives a real snapshot.
    seed = recipe['seed']
    # 'projection' reuses the same binding: the minimal-projection cell needs a real applicability
    # snapshot before an active Procedure head can be recalled at all.
    return (recipe['family'] in {'lifecycle', 'epistemic', 'procedure_applicability', 'projection'} and seed['memory_type'] == 'procedure'
        and seed.get('state', 'active') in SNAPSHOT_STATES
        and (seed.get('epistemic', 'explicit_user') == 'explicit_user' or seed.get('state') == 'draft'))


async def bind(case, recipe, target):
    raw = recipe['seed']['payload']['applicability']
    # These are explicit fixture environment/schema inputs, not output-derived IDs.
    if not isinstance(raw, dict):
        raise ValueError('procedure fixture needs original tool/version input')
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    applicability = h.ProcedureApplicabilityContext(raw['tool'],
        'public-procedure-fixture', raw['version'], digest(schema))
    eid = 'procedure-applicability-evidence'
    text = 'Fixture applicability declaration: ' + json.dumps(applicability.to_json(),
        ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    envelope, span = await case.evidence(text, eid)
    admitted = case.admitted[eid]
    admission = admitted.receipt
    scope_id = 'procedure-fixture-task'
    manifest = [{'evidence_id': eid, 'envelope_hash': envelope.envelope_hash, 'item_ordinal': 1}]
    metadata = h.ConversationEvidenceMetadata(metadata_id='procedure-metadata',
        authority_issuer_id='host-procedure-fixture', evidence_id=eid,
        envelope_hash=envelope.envelope_hash, admission_receipt_id=admission.receipt_id,
        admission_receipt_hash=admission.receipt_hash, run_id=envelope.run_id,
        subject=envelope.subject, source_hash=envelope.source_hash, sanitized_hash=envelope.sanitized_hash,
        conversation_id='procedure-fixture-conversation', primary_conversation_id='procedure-fixture-conversation',
        causal_group_id='procedure-fixture-group', causal_group_sequence=1, item_ordinal=1,
        group_item_count=1, ordered_group_manifest_hash=digest(manifest),
        role=h.ConversationEvidenceRole.USER, occurred_at=case.now, task_scope_id=scope_id,
        tool_causal_link=None, entities=())
    metadata = h.authorize_conversation_public_text(metadata, admitted)
    receipt = h.ConversationEvidenceMetadataReceipt(receipt_id='procedure-metadata-receipt',
        metadata_id=metadata.metadata_id, authority_issuer_id=metadata.authority_issuer_id,
        evidence_id=eid, envelope_hash=envelope.envelope_hash,
        admission_receipt_id=admission.receipt_id, admission_receipt_hash=admission.receipt_hash,
        run_id=envelope.run_id, subject=envelope.subject, source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash, metadata_hash=metadata.metadata_hash,
        issuer_ref=metadata.authority_issuer_id, accepted=True)
    registration = h.ConversationEvidenceRegistration('procedure-registration', envelope,
        admission, metadata, receipt, admitted.item_authority)
    regref = h.ConversationEvidenceRegistrationRef(registration.registration_id,
        registration.registration_hash, eid, envelope.envelope_hash)
    case.conversations[registration.registration_id] = registration
    event = {'call': 'register_procedure_conversation', 'registration': registration.to_json(),
        'registration_hash': registration.registration_hash, 'reference': dc.asdict(regref)}
    case.events.append(event)
    registered = await case.manager.register_conversation_evidence(regref)
    event['result'] = dc.asdict(registered)
    state = h.ProcedureLifecycleState(lifecycle_state(recipe))
    intent = h.ProcedureObservationIntent(observation_id='procedure-snapshot',
        subject=case.principal.actor_id, scope=h.MemoryScopeRef('personal', case.principal.actor_id),
        target_memory_id=target.memory_id, target_revision=target.revision,
        kind=h.ProcedureObservationKind.APPLICABILITY_SNAPSHOT, applicability=applicability,
        risk_level=h.ProcedureRiskLevel(recipe['seed']['payload']['effective_risk']),
        hazard=h.ProcedureHazard.NONE, task_scope_id=scope_id, evidence_span=span,
        terminal_receipt_id=None, terminal_receipt_hash=None, outcome=None, attributable=False,
        observed_at=case.now, transition_from=state, transition_to=state,
        run_id=envelope.run_id, operation_id='procedure-bind')
    authority = h.issue_procedure_observation_authority(intent,
        authority_id='procedure-authority', issued_at=case.now-1, expires_at=case.now+300,
        nonce='procedure-snapshot-nonce', issuer_ref='host-procedure-fixture')
    case.procedure_authorities[authority.authority_id] = authority
    reference = h.ProcedureObservationAuthorityRef.from_authority(authority)
    event = {'call': 'record_procedure_observation', 'reference': reference.to_json()}
    case.events.append(event)
    result = await case.manager.record_procedure_observation(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id), reference=reference)
    event.update(result=result.to_json(), result_hash=result.result_hash)
    replay = await case.manager.record_procedure_observation(principal=case.principal,
        scope=m.MemoryScope.personal(case.principal.actor_id), reference=reference)
    event.update(replay=replay.to_json(), replay_hash=replay.result_hash)
    binding = {'input_schema': schema, 'applicability': applicability.to_json(),
        'fingerprint': applicability.fingerprint, 'result': result.to_json(),
        'reference': reference.to_json()}
    if promotion_required(recipe):
        binding['promotion'] = await promote(case, recipe, applicability, target.memory_id,
                                             result.committed_revision)
    return binding


def current_fingerprints(recipe, proof):
    """Frozen app-v2/app-v3 labels map to explicit tool-version contexts."""
    row = recipe['applicability_contract']
    if row['bound_fingerprint'] != 'app-v2':
        raise ValueError('unsupported original bound applicability label')
    label = row['current_fingerprint']
    if label is None:
        return ()
    if label == 'app-v2':
        return (proof['fingerprint'],)
    if label != 'app-v3':
        raise ValueError('unsupported original current applicability label')
    app = proof['applicability']
    return (h.ProcedureApplicabilityContext(app['tool_id'], app['environment'],
        '3', app['input_schema_hash']).fingerprint,)
