"""Real public remember/recall/correct/forget, with actual ContextFragment bindings.

No private source or SQL. Original current-use oracle differences remain parent gates.
"""
import dataclasses as dc
import importlib.util
import hashlib
import json
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m

CELLS={
    'current-use/context:receipt-first','current-use/context:duplicate-same-provider-attempt',
    'current-use/context:new-provider-attempt','current-use/context:suppression-first',
    'current-use/context:wrong-snapshot','current-use/authority:suppression',
}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


async def context_bundle(case,recall,label):
    value=recall['execution'];result=value['result'];decision=value['decision'];item=result['items'][0]
    selected=item['selected_item'];item_hash=value['result_item_hashes'][0]
    page=await case.manager.page_typed_recall_result(principal=case.principal,request=h.RecallResultPageRequestV1(
        result['result_id'],value['result_hash'],1,0,1,recall['plan']['budget']['max_bytes'],case.now))
    binding=h.RecallFragmentAuthorityBindingV1(decision['decision_id'],value['decision_hash'],result['result_id'],
        value['result_hash'],selected['item_id'],item_hash,None,None,None,page.page_id,page.page_hash,
        None,None,selected['public_payload_hash'])
    fragment=h.ContextFragmentV2('fragment-'+label,recall['context']['run_id'],recall['context']['subject'],
        h.ContextFragmentType.RECALLED_MEMORY,selected['source_ref'],selected['source_revision'],item['public_payload'],
        selected['public_payload_hash'],len(canonical(item['public_payload'])),len(canonical(item['public_payload'])),
        h.DisclosureContext.from_json(recall['context']['disclosure_context']),
        tuple(h.EvidenceRef.from_json(r) for r in recall['context']['evidence_refs']),binding)
    return dict(recall=recall,page=page.to_json(),page_hash=page.page_hash,
        fragment=fragment.to_json(),fragment_hash=fragment.fragment_hash)


def use_request(case,bundle,attempt):
    recall=bundle['recall'];value=recall['execution'];item=value['result']['items'][0]
    fragments=(h.ContextFragmentBindingV2(bundle['fragment']['fragment_id'],bundle['fragment_hash']),)
    return h.RecallContextUseAuthorizationRequestV1(case.principal.actor_id,recall['context']['run_id'],
        recall['context']['turn_id'],attempt,value['decision']['decision_id'],value['decision_hash'],
        value['result']['result_id'],value['result_hash'],
        (h.RecallItemBindingV1(item['selected_item']['item_id'],value['result_item_hashes'][0]),),
        fragments,hashlib.sha256(canonical([f.to_json() for f in fragments])).hexdigest(),case.now)


async def authorize(case,bundle,request):
    observed={**bundle,'request':request.to_json()}
    case.events.append({'call':'authorize_recall_context_use','request':request.to_json(),'now':case.now})
    try:
        receipt=await case.manager.authorize_recall_context_use(principal=case.principal,request=request,now=case.now)
        observed.update(receipt=receipt.to_json(),receipt_hash=receipt.receipt_hash)
    except Exception as exc:observed['exception']=dict(type=type(exc).__name__,reason=str(exc))
    return observed


async def run_cases(inputs,workspace):
    spec=importlib.util.spec_from_file_location('case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=[]
    for index,name in enumerate(inputs['cells']):
        case=await module.CaseManager(workspace/f'context-use-{index}.sqlite').open()
        o=dict(context_use_cell=name,calls=case.events,sources=case.sources,uses={},phase='register')
        try:
            o['registration']=dc.asdict(await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id)))
            o['phase']='remember'
            first=await case.seed(dict(memory_type='semantic',payload=inputs['payloads']['incumbent']),evidence_id='loop-user-311')
            o['phase']='initial_recall';o['initial']=await case.recall(query='preferred_python',key='loop-initial')
            o['phase']='initial_page';initial=await context_bundle(case,o['initial'],'initial')
            suppression_first=name=='current-use/context:suppression-first'
            if not suppression_first:
                o['phase']='initial_use';request=use_request(case,initial,'attempt-initial')
                o['uses']['initial_use']=await authorize(case,initial,request)
                o['uses']['initial_duplicate']=await authorize(case,initial,request)
                o['uses']['initial_new_attempt']=await authorize(case,initial,use_request(case,initial,'attempt-initial-new'))
            o['phase']='correct'
            await case.seed(dict(memory_type='semantic',payload=inputs['payloads']['challenger']),kind='revise',
                operation_id='loop-correct',evidence_id='loop-user-312',target=h.ExistingMemoryTarget(first.memory_id,first.revision))
            o['phase']='corrected_recall';o['corrected']=await case.recall(query='preferred_python',key='loop-corrected')
            o['phase']='corrected_page';corrected=await context_bundle(case,o['corrected'],'corrected')
            o['phase']='old_after_correction'
            o['uses']['old_after_correction']=await authorize(case,initial,use_request(case,initial,'attempt-old-after-correction'))
            corrected_request=use_request(case,corrected,'attempt-corrected')
            if not suppression_first:
                o['uses']['corrected_use']=await authorize(case,corrected,corrected_request)
            if name=='current-use/context:wrong-snapshot':
                o['wrong_snapshot']={'input':{**corrected_request.to_json(),'snapshot_manifest_hash':'f'*64}}
                try:h.RecallContextUseAuthorizationRequestV1.from_json(o['wrong_snapshot']['input'])
                except Exception as exc:o['wrong_snapshot']['exception']=dict(type=type(exc).__name__,reason=str(exc))
            o['phase']='forget'
            request=m.SuppressionRequest('loop-forget',case.principal.actor_id,m.SuppressionScopeKind.MEMORY,
                first.memory_id,'user_forget',case.now,purpose=None)
            o['suppression']={'request':request.to_json()}
            o['suppression']['decision']=(await case.manager.suppress(principal=case.principal,request=request)).to_json()
            case.events.append({'call':'suppress',**o['suppression']})
            o['phase']='reopen';await case.close();await case.open()
            if not suppression_first:
                o['uses']['corrected_replay_after_reopen']=await authorize(case,corrected,corrected_request)
            o['uses']['new_after_forget']=await authorize(case,corrected,use_request(case,corrected,'attempt-new-after-forget'))
            o['phase']='fresh_after_reopen';o['after_reopen']=await case.recall(query='preferred_python',key='loop-after-forget')
            o['phase']='historical_replay'
            o['old_recall_replay']=case.cases.execution_wire(await case.manager.execute_typed_recall(principal=case.principal,
                context=h.RecallContext.from_json(o['corrected']['context']),plan=h.RecallPlan.from_json(o['corrected']['plan']),now=case.now))
            o['phase']='complete'
        except Exception as exc:o['exception']=dict(type=type(exc).__name__,reason=str(exc))
        finally:await case.close()
        rows.append(dict(cell_id=name,status='OBSERVED',reason='',observations=o))
    return rows
