"""Public procedure applicability snapshot setup; no terminal success fabrication."""
import dataclasses as dc
import importlib.util
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m

CELLS={f'eligibility/procedure-applicability-{name}' for name in ('match','mismatch','absent')}


async def register_snapshot(case,module,app):
    eid='applicability-evidence'
    envelope,span=await case.evidence('Fixture applicability snapshot: '+module.canonical(app.to_json()).decode(),
        eid,'observed_behavior','source_verified')
    # Host verifies its typed setup receipt; the SDK consumes the separate procedure authority.
    await case.authority.resolve_typed_observation(span.typed_observation)
    case.events[-1]['caller']='host_fixture_preflight'
    parent=case.admitted[case.sources[0]['evidence_id']]
    entries=[parent,case.admitted[eid]]
    manifest=[{'evidence_id':entry.envelope.evidence_id,'envelope_hash':entry.envelope.envelope_hash,'item_ordinal':i}
        for i,entry in enumerate(entries,1)]
    for ordinal,authority in enumerate(entries,1):
        envelope=authority.envelope;admission=authority.receipt;eid=envelope.evidence_id
        metadata=h.ConversationEvidenceMetadata(metadata_id=f'app-metadata-{ordinal}',authority_issuer_id='host-conversation-registry',
            evidence_id=eid,envelope_hash=envelope.envelope_hash,admission_receipt_id=admission.receipt_id,
            admission_receipt_hash=admission.receipt_hash,run_id=envelope.run_id,subject=envelope.subject,
            source_hash=envelope.source_hash,sanitized_hash=envelope.sanitized_hash,
            conversation_id='primary-conversation',primary_conversation_id='primary-conversation',
            causal_group_id='app-group',causal_group_sequence=1,item_ordinal=ordinal,group_item_count=2,
            ordered_group_manifest_hash=module.H(manifest),role=h.ConversationEvidenceRole.USER if ordinal==1 else h.ConversationEvidenceRole.TOOL,
            occurred_at=case.now,task_scope_id='app-task',
            tool_causal_link=None if ordinal==1 else h.ConversationToolCausalLink('app-tool-call','fixture-applicability',1,'app-terminal',module.H(app.to_json())),entities=())
        metadata=h.authorize_conversation_public_text(metadata,authority)
        receipt=h.ConversationEvidenceMetadataReceipt(receipt_id=f'app-metadata-receipt-{ordinal}',metadata_id=metadata.metadata_id,
            authority_issuer_id=metadata.authority_issuer_id,evidence_id=eid,envelope_hash=envelope.envelope_hash,
            admission_receipt_id=admission.receipt_id,admission_receipt_hash=admission.receipt_hash,
            run_id=envelope.run_id,subject=envelope.subject,source_hash=envelope.source_hash,sanitized_hash=envelope.sanitized_hash,
            metadata_hash=metadata.metadata_hash,issuer_ref=metadata.authority_issuer_id,accepted=True)
        registration=h.ConversationEvidenceRegistration(f'app-registration-{ordinal}',envelope,admission,metadata,receipt,authority.item_authority)
        ref=h.ConversationEvidenceRegistrationRef(registration.registration_id,registration.registration_hash,eid,envelope.envelope_hash)
        case.conversations[registration.registration_id]=registration
        event=dict(call='register_conversation_evidence',registration=registration.to_json(),reference=dc.asdict(ref),group_manifest=manifest)
        case.events.append(event)
        result=await case.manager.register_conversation_evidence(ref)
        event['result']=result.to_json() if hasattr(result,'to_json') else str(result)
    return span,event



async def run_cases(inputs,workspace):
    spec=importlib.util.spec_from_file_location('case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=[]
    for index,name in enumerate(inputs['cells']):
        case=await module.CaseManager(workspace/f'applicability-{index}.sqlite').open()
        o=dict(applicability_cell=name,calls=case.events,sources=case.sources,phase='seed')
        try:
            await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
            op=await case.seed({'memory_type':'procedure','payload':inputs['payload']})
            app=h.ProcedureApplicabilityContext('git','fixture-macos','2',module.H({'type':'object','properties':{},'additionalProperties':False}))
            o['phase']='register_snapshot';span,o['registration']=await register_snapshot(case,module,app)
            intent=h.ProcedureObservationIntent('app-observation',case.principal.actor_id,h.MemoryScopeRef.personal(case.principal.actor_id),
                op.memory_id,op.revision,h.ProcedureObservationKind.APPLICABILITY_SNAPSHOT,app,h.ProcedureRiskLevel.LOW,
                h.ProcedureHazard.NONE,'app-task',span,None,None,None,False,case.now,h.ProcedureLifecycleState.ACTIVE,
                h.ProcedureLifecycleState.ACTIVE,case.disclosure.run_id,'app-observe')
            grant=h.issue_procedure_observation_authority(intent,authority_id='app-grant',issued_at=case.now-1,
                expires_at=case.now+600,nonce='app-nonce',issuer_ref='host-applicability-fixture')
            ref=h.ProcedureObservationAuthorityRef.from_authority(grant);case.procedure_grants[grant.authority_id]=grant
            o['phase']='record_procedure_observation'
            event=dict(call='record_procedure_observation',grant=grant.to_json(),reference=ref.to_json(),authority_hash=grant.authority_hash)
            case.events.append(event);o['observation']=event
            result=await case.manager.record_procedure_observation(principal=case.principal,scope=m.MemoryScope.personal(case.principal.actor_id),reference=ref)
            event.update(result=result.to_json(),result_hash=result.result_hash)
            fingerprints=() if name.endswith('-absent') else ((dc.replace(app,tool_version='3') if name.endswith('-mismatch') else app).fingerprint,)
            o['phase']='recall';o['recall']=await case.recall(query=inputs['payload']['name'],memory_types=('procedure',),fingerprint=fingerprints)
            o['replay']=(await case.recall(query=inputs['payload']['name'],memory_types=('procedure',),fingerprint=fingerprints))['execution']
            o['phase']='complete'
        except Exception as exc:o['exception']={'type':type(exc).__name__,'reason':str(exc)}
        finally:await case.close()
        rows.append(dict(cell_id=name,status='OBSERVED',reason='',observations=o))
    return rows
