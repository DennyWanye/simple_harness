"""Real public conversation registration -> projection -> typed recall. No models/SQL."""
import dataclasses as dc
import importlib.util
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m


def helper():
    spec=importlib.util.spec_from_file_location('short_case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def register(case,sequence,text,occurred_at):
    eid=f'conversation-evidence-{sequence}'
    envelope,span=await case.evidence(text,eid)
    authority=case.admitted[eid];admission=authority.receipt
    manifest=[{'evidence_id':eid,'envelope_hash':envelope.envelope_hash,'item_ordinal':1}]
    metadata=h.ConversationEvidenceMetadata(metadata_id=f'metadata-{sequence}',authority_issuer_id='host-conversation-registry',
        evidence_id=eid,envelope_hash=envelope.envelope_hash,admission_receipt_id=admission.receipt_id,
        admission_receipt_hash=admission.receipt_hash,run_id=envelope.run_id,subject=envelope.subject,
        source_hash=envelope.source_hash,sanitized_hash=envelope.sanitized_hash,
        conversation_id='primary-conversation',primary_conversation_id='primary-conversation',
        causal_group_id=f'group-{sequence}',causal_group_sequence=sequence,item_ordinal=1,group_item_count=1,
        ordered_group_manifest_hash=helper().H(manifest),role=h.ConversationEvidenceRole.USER,
        occurred_at=occurred_at,task_scope_id=None,tool_causal_link=None,entities=())
    metadata=h.authorize_conversation_public_text(metadata,authority)
    receipt=h.ConversationEvidenceMetadataReceipt(receipt_id=f'metadata-receipt-{sequence}',metadata_id=metadata.metadata_id,
        authority_issuer_id=metadata.authority_issuer_id,evidence_id=eid,envelope_hash=envelope.envelope_hash,
        admission_receipt_id=admission.receipt_id,admission_receipt_hash=admission.receipt_hash,
        run_id=envelope.run_id,subject=envelope.subject,source_hash=envelope.source_hash,sanitized_hash=envelope.sanitized_hash,
        metadata_hash=metadata.metadata_hash,issuer_ref=metadata.authority_issuer_id,accepted=True)
    registration=h.ConversationEvidenceRegistration(f'registration-{sequence}',envelope,admission,metadata,receipt,authority.item_authority)
    ref=h.ConversationEvidenceRegistrationRef(registration.registration_id,registration.registration_hash,eid,envelope.envelope_hash)
    case.conversations[registration.registration_id]=registration
    event={'call':'register_conversation_evidence','registration':registration.to_json(),
        'reference':dc.asdict(ref),'input_text':text,'group_manifest':manifest}
    case.events.append(event)
    result=await case.manager.register_conversation_evidence(ref)
    event['result']=result.to_json() if hasattr(result,'to_json') else str(result)


async def run_cases(inputs,workspace):
    module=helper();rows=[]
    for ordinal,recipe in enumerate(inputs['cases']):
        case=await module.CaseManager(workspace/f'short-{ordinal}.sqlite',now=module.seconds(recipe.get('now',1788170400.0))).open()
        o={'short_recipe':recipe,'calls':case.events,'sources':case.sources}
        try:
            mixed=recipe['cell_id']=='protocol/mixed-long-short'
            if mixed:
                previous=None
                for revision in range(1,4):
                    previous=await case.seed(inputs['semantic'],operation_id=f'short-mixed-{revision}',evidence_id=f'mixed-evidence-{revision}',
                        **({} if previous is None else dict(kind='revise',target=h.ExistingMemoryTarget(previous.memory_id,previous.revision))))
            target_time=module.seconds(recipe.get('occurred_at',inputs['occurred_at']))
            for sequence in range(1,12):
                await register(case,sequence,inputs['text'] if sequence==1 else f'unrelated filler {sequence}',
                    target_time if sequence==1 else case.now-11+sequence-1)
            owner=await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
            o['owner_registration']=dc.asdict(owner)
            case.events.append({'call':'register_principal_owner'})
            built=await case.manager.rebuild_short_horizon_projection(principal=case.principal,now=case.now)
            o['projection_build']=dc.asdict(built)
            query='3.11 task' if mixed else 'task'
            args=dict(query=query,memory_types=('semantic',),short=True)
            o['before']=await case.recall(**args,key='short-before')
            if recipe['cell_id']=='eligibility/short-source-suppressed':
                request=m.SuppressionRequest('short-suppression',case.principal.actor_id,m.SuppressionScopeKind.EVIDENCE,
                    'conversation-evidence-1','user_requested',case.now,purpose=m.OrdinaryMemoryPurpose.RECALL)
                value=await case.manager.suppress(principal=case.principal,request=request)
                o['suppression']={'request':request.to_json(),'decision':value.to_json()}
            o['recall']=await case.recall(**args,key='short-final')
            o['replay']=(await case.recall(**args,key='short-final'))['execution']
        except Exception as exc:
            o['exception']={'type':type(exc).__name__,'reason':str(exc)}
        finally:
            await case.close()
        rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':o})
    return rows
