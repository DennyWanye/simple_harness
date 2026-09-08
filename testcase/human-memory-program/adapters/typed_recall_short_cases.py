"""Real public conversation registration -> projection -> typed recall. No models/SQL.

Every observation here is produced by public Host/Memory calls only. The parent oracle
(runners/typed_recall_a2_oracle.assess_short) owns every verdict; nothing in this file
asserts an outcome. Three public seams beyond the 0.6.29 flow are used:

  * ``resolve_typed_short_horizon_sources`` expands a selected typed-short item into its
    exact Host registration and original hashes (evidence/envelope/source/sanitized/admission/
    registration), and reports the chunk's own ``valid_until``;
  * that ``valid_until`` is the chunk expiry the product derived, so the expiry boundary is
    replayed at exactly that clock instead of assuming a retention constant;
  * a registration whose Host classification was never authorized carries no public_text and a
    registry that answers with a different durable registration is refused by Memory itself.
"""
import dataclasses as dc
import importlib.util
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m


def helper():
    spec=importlib.util.spec_from_file_location('short_case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def build_registration(case,sequence,text,occurred_at,*,authorize=True,item_authority=True,evidence_id=None):
    """Host-side registration objects; ``authorize`` gates the public-text classification."""
    eid=evidence_id or f'conversation-evidence-{sequence}'
    envelope=case.admitted[eid].envelope
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
    if authorize:
        metadata=h.authorize_conversation_public_text(metadata,authority)
    receipt=h.ConversationEvidenceMetadataReceipt(receipt_id=f'metadata-receipt-{sequence}',metadata_id=metadata.metadata_id,
        authority_issuer_id=metadata.authority_issuer_id,evidence_id=eid,envelope_hash=envelope.envelope_hash,
        admission_receipt_id=admission.receipt_id,admission_receipt_hash=admission.receipt_hash,
        run_id=envelope.run_id,subject=envelope.subject,source_hash=envelope.source_hash,sanitized_hash=envelope.sanitized_hash,
        metadata_hash=metadata.metadata_hash,issuer_ref=metadata.authority_issuer_id,accepted=True)
    registration=h.ConversationEvidenceRegistration(f'registration-{sequence}',envelope,admission,metadata,receipt,
        authority.item_authority if item_authority else None)
    ref=h.ConversationEvidenceRegistrationRef(registration.registration_id,registration.registration_hash,eid,envelope.envelope_hash)
    case.conversations[registration.registration_id]=registration
    return registration,ref,manifest


async def register(case,sequence,text,occurred_at,*,authorize=True,item_authority=True,evidence_id=None,reference=None):
    eid=evidence_id or f'conversation-evidence-{sequence}'
    envelope,span=await case.evidence(text,eid)
    registration,ref,manifest=build_registration(case,sequence,text,occurred_at,authorize=authorize,
        item_authority=item_authority,evidence_id=eid)
    if reference is not None:
        ref=reference
    event={'call':'register_conversation_evidence','registration':registration.to_json(),
        'reference':dc.asdict(ref),'input_text':text,'group_manifest':manifest,
        'short_horizon_eligible':registration.short_horizon_eligible,
        'has_authorized_public_text':registration.metadata.has_authorized_public_text,
        'effective_privacy_class':None if registration.metadata.effective_privacy_class is None
            else registration.metadata.effective_privacy_class.value,
        'occurred_at':occurred_at}
    case.events.append(event)
    result=await case.manager.register_conversation_evidence(ref)
    event['result']=result.to_json() if hasattr(result,'to_json') else str(result)
    return registration


async def typed_short_sources(case,bundle,*,tamper=False):
    """Expand every selected item of one durable result into its exact Host sources."""
    wire=bundle['execution'];result=wire['result']
    hashes=list(wire['result_item_hashes'])
    if tamper:
        hashes=[('0' if value[0]!='0' else '1')+value[1:] for value in hashes]
    bindings=tuple(m.HistoryRecallBinding(result_id=result['result_id'],result_hash=wire['result_hash'],
        item_id=item['selected_item']['item_id'],item_hash=item_hash)
        for item,item_hash in zip(result['items'],hashes))
    request=[binding.to_json() for binding in bindings]
    case.events.append({'call':'resolve_typed_short_horizon_sources','bindings':request,'tamper':tamper})
    snapshot=await case.manager.resolve_typed_short_horizon_sources(principal=case.principal,
        disclosure_context=case.disclosure,bindings=bindings)
    return {'bindings':request,'snapshot':snapshot.to_json(),'snapshot_hash':snapshot.snapshot_hash}


async def seed_history(case,inputs,recipe,*,target_time,fillers=True,authorize=True,item_authority=True):
    """One target group outside the recent-10 window plus ten fresher filler groups."""
    await register(case,1,inputs['text'],target_time,authorize=authorize,item_authority=item_authority,
        evidence_id=recipe.get('evidence_id'))
    if fillers:
        for sequence in range(2,12):
            await register(case,sequence,f'unrelated filler {sequence}',case.now-11+sequence-1)


async def run_case(case,inputs,recipe,observed):
    name=recipe['cell_id']
    mixed=name=='protocol/mixed-long-short'
    if mixed:
        previous=None
        for revision in range(1,4):
            previous=await case.seed(inputs['semantic'],operation_id=f'short-mixed-{revision}',evidence_id=f'mixed-evidence-{revision}',
                **({} if previous is None else dict(kind='revise',target=h.ExistingMemoryTarget(previous.memory_id,previous.revision))))
    module=helper()
    target_time=module.seconds(recipe.get('occurred_at',inputs['occurred_at']))
    query='3.11 task' if mixed else inputs['query']
    args=dict(query=query,memory_types=('semantic',),short=True)
    if name=='eligibility/short-classification-invalid':
        # Host never authorized a public_text/classification for the target registration.
        await seed_history(case,inputs,recipe,target_time=target_time,authorize=False,item_authority=False)
        owner=await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
        observed['owner_registration']=dc.asdict(owner)
        observed['projection_build']=dc.asdict(await case.manager.rebuild_short_horizon_projection(principal=case.principal,now=case.now))
        observed['recall']=await case.recall(**args,key='short-final')
        observed['replay']=(await case.recall(**args,key='short-final'))['execution']
        # Paired control in a second database: the identical group WITH the Host classification.
        control=module.CaseManager(case.path.with_name('short-classification-control.sqlite'),now=case.now)
        await control.open()
        try:
            await seed_history(control,inputs,recipe,target_time=target_time)
            await control.manager.register_principal_owner(control.principal,m.MemoryScope.personal(control.principal.actor_id))
            observed['control_projection_build']=dc.asdict(
                await control.manager.rebuild_short_horizon_projection(principal=control.principal,now=control.now))
            observed['control_recall']=await control.recall(**args,key='short-control')
            observed['control_calls']=control.events
        finally:
            await control.close()
        return
    if name=='eligibility/short-registration-invalid':
        # Ten accepted filler groups fill the recent-10 window, so the target group is the only
        # one that could ever be projected. The registry then answers the target registration id
        # with filler registration-2's durable object, which Memory itself must refuse.
        for sequence in range(2,12):
            await register(case,sequence,f'unrelated filler {sequence}',case.now-11+sequence-1)
        await case.evidence(inputs['text'],'conversation-evidence-1')
        registration,ref,_=build_registration(case,1,inputs['text'],target_time)
        case.conversation_misbinding[registration.registration_id]='registration-2'
        observed['misbound_reference']=dc.asdict(ref)
        observed['misbound_registration_id']=registration.registration_id
        observed['answered_registration_id']='registration-2'
        try:
            await case.manager.register_conversation_evidence(ref)
            observed['registration_result']='accepted'
        except Exception as exc:  # noqa: BLE001 - the parent oracle owns the verdict
            observed['registration_rejection']={'type':type(exc).__name__,'reason':str(exc)}
        case.conversation_misbinding.clear()
        owner=await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
        observed['owner_registration']=dc.asdict(owner)
        observed['projection_build']=dc.asdict(await case.manager.rebuild_short_horizon_projection(principal=case.principal,now=case.now))
        observed['recall']=await case.recall(**args,key='short-final')
        observed['replay']=(await case.recall(**args,key='short-final'))['execution']
        # Control: the same registration accepted through the honest registry binding.
        control=module.CaseManager(case.path.with_name('short-registration-control.sqlite'),now=case.now)
        await control.open()
        try:
            await seed_history(control,inputs,recipe,target_time=target_time)
            await control.manager.register_principal_owner(control.principal,m.MemoryScope.personal(control.principal.actor_id))
            observed['control_projection_build']=dc.asdict(
                await control.manager.rebuild_short_horizon_projection(principal=control.principal,now=control.now))
            observed['control_recall']=await control.recall(**args,key='short-control')
            observed['control_calls']=control.events
        finally:
            await control.close()
        return
    await seed_history(case,inputs,recipe,target_time=target_time)
    owner=await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id))
    observed['owner_registration']=dc.asdict(owner)
    case.events.append({'call':'register_principal_owner'})
    built=await case.manager.rebuild_short_horizon_projection(principal=case.principal,now=case.now)
    observed['projection_build']=dc.asdict(built)
    observed['before']=await case.recall(**args,key='short-before')
    if observed['before']['execution']['result']['items']:
        observed['before_typed_sources']=await typed_short_sources(case,observed['before'])
    if name=='eligibility/short-source-suppressed':
        request=m.SuppressionRequest('short-suppression',case.principal.actor_id,m.SuppressionScopeKind.EVIDENCE,
            'conversation-evidence-1','user_requested',case.now,purpose=m.OrdinaryMemoryPurpose.RECALL)
        value=await case.manager.suppress(principal=case.principal,request=request)
        observed['suppression']={'request':request.to_json(),'decision':value.to_json()}
    if name=='eligibility/short-expiry-equals-now':
        # The product's own chunk expiry, read back from the public source snapshot, is the
        # only clock this boundary may be replayed at.
        expiry=observed['before_typed_sources']['snapshot']['valid_until']
        observed['chunk_expiry']=expiry
        observed['before_now']=case.now
        case.now=expiry-1.0
        observed['just_before']=await case.recall(**args,key='short-just-before')
        case.now=expiry
    observed['recall']=await case.recall(**args,key='short-final')
    observed['replay']=(await case.recall(**args,key='short-final'))['execution']
    observed['recall_now']=case.now
    if observed['recall']['execution']['result']['items']:
        observed['typed_sources']=await typed_short_sources(case,observed['recall'])
        try:
            observed['tampered_sources']=await typed_short_sources(case,observed['recall'],tamper=True)
        except Exception as exc:  # noqa: BLE001
            observed['tampered_sources']={'exception':{'type':type(exc).__name__,'reason':str(exc)}}


async def run_cases(inputs,workspace):
    module=helper();rows=[]
    for ordinal,recipe in enumerate(inputs['cases']):
        case=module.CaseManager(workspace/f'short-{ordinal}.sqlite',now=module.seconds(recipe.get('now',1788170400.0)),
            privacy=recipe.get('privacy','personal'))
        await case.open()
        o={'short_recipe':recipe,'calls':case.events,'sources':case.sources,'privacy':recipe.get('privacy','personal')}
        try:
            await run_case(case,inputs,recipe,o)
        except Exception as exc:
            o['exception']={'type':type(exc).__name__,'reason':str(exc)}
        finally:
            await case.close()
        rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':o})
    return rows
