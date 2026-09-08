"""Two-item public Context-use traces. No SQL, private imports, or expected gold."""
import dataclasses as dc
import importlib.util
import hashlib
import json
from pathlib import Path
import simple_harness as h
import simple_harness_memory as m

CELLS = {
    'current-use/context:receipt-first', 'current-use/context:duplicate-same-provider-attempt',
    'current-use/context:new-provider-attempt', 'current-use/context:suppression-first',
    'current-use/context:wrong-snapshot', 'current-use/authority:suppression',
}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def error(exc):
    return {'type': type(exc).__name__, 'reason': str(exc)}


async def recall(case, recipe, key):
    context, plan = case.request(query=recipe['query'], key=key)
    disclosure = dc.replace(context.disclosure_context, run_id=recipe['run_id'])
    context = dc.replace(context, run_id=recipe['run_id'], turn_id=recipe['turn_id'],
        expires_at=recipe['context_expires_at'], disclosure_context=disclosure)
    plan = dc.replace(plan, run_id=context.run_id, context_hash=context.context_hash, disclosure_context=disclosure)
    case.events.append({'call':'execute_typed_recall','context':context.to_json(),'plan':plan.to_json(),'now':case.now})
    execution = await case.manager.execute_typed_recall(principal=case.principal,context=context,plan=plan,now=case.now)
    return {'context':context.to_json(),'plan':plan.to_json(),'execution':case.cases.execution_wire(execution),'now':case.now}


async def context_bundle(case, recalled):
    value=recalled['execution'];result=value['result'];decision=value['decision']
    if len(result['items']) != 2:
        raise ValueError('two independently materialized items required')
    pages,fragments=[],[]
    for i,item in enumerate(result['items']):
        selected=item['selected_item']
        request=h.RecallResultPageRequestV1(result['result_id'],value['result_hash'],i+1,i,1,
            recalled['plan']['budget']['max_bytes'],case.now)
        case.events.append({'call':'page_typed_recall_result','request':request.to_json(),'now':case.now})
        page=await case.manager.page_typed_recall_result(principal=case.principal,request=request)
        pages.append({'request':request.to_json(),'page':page.to_json(),'page_hash':page.page_hash})
        binding=h.RecallFragmentAuthorityBindingV1(decision['decision_id'],value['decision_hash'],result['result_id'],
            value['result_hash'],selected['item_id'],value['result_item_hashes'][i],None,None,None,
            page.page_id,page.page_hash,None,None,selected['public_payload_hash'])
        size=len(canonical(item['public_payload']))
        # Public fragment discriminant follows the selected source kind: short-horizon items carry
        # no revision and use the SHORT_HORIZON fragment type (Harness ContextFragmentV2 contract).
        short=selected['source_kind']=='short_horizon'
        fragment=h.ContextFragmentV2('context-use-item-'+str(i+1),recalled['context']['run_id'],case.principal.actor_id,
            h.ContextFragmentType.SHORT_HORIZON if short else h.ContextFragmentType.RECALLED_MEMORY,
            selected['source_ref'],selected['source_revision'],item['public_payload'],
            selected['public_payload_hash'],size,size,h.DisclosureContext.from_json(recalled['context']['disclosure_context']),
            tuple(h.EvidenceRef.from_json(r) for r in recalled['context']['evidence_refs']),binding)
        fragments.append({'fragment':fragment.to_json(),'fragment_hash':fragment.fragment_hash})
    return dict(executor_version=2,recall=recalled,pages=pages,fragments=fragments)


def use_request(case,bundle,attempt,at):
    recalled=bundle['recall'];value=recalled['execution']
    fragments=tuple(h.ContextFragmentBindingV2(r['fragment']['fragment_id'],r['fragment_hash']) for r in bundle['fragments'])
    return h.RecallContextUseAuthorizationRequestV1(case.principal.actor_id,recalled['context']['run_id'],
        recalled['context']['turn_id'],attempt,value['decision']['decision_id'],value['decision_hash'],
        value['result']['result_id'],value['result_hash'],tuple(h.RecallItemBindingV1(item['selected_item']['item_id'],ih)
            for item,ih in zip(value['result']['items'],value['result_item_hashes'],strict=True)),
        fragments,hashlib.sha256(canonical([f.to_json() for f in fragments])).hexdigest(),at)


async def authorize(case,bundle,request):
    observed={**bundle,'request':request.to_json(),'now':case.now}
    event={'call':'authorize_recall_context_use','request':request.to_json(),'now':case.now}
    case.events.append(event)
    try:
        receipt=await case.manager.authorize_recall_context_use(principal=case.principal,request=request,now=case.now)
        observed.update(receipt=receipt.to_json(),receipt_hash=receipt.receipt_hash)
        event.update(receipt=receipt.to_json(),receipt_hash=receipt.receipt_hash)
    except Exception as exc:
        observed['exception']=error(exc);event['exception']=error(exc)
    return observed


def validate(receipt,request):
    event={'request':request.to_json()}
    try:
        receipt.validate_request(request)
        event['accepted']=True
    except Exception as exc:
        event.update(accepted=False,exception=error(exc))
    return event


async def run_cases(inputs,workspace):
    spec=importlib.util.spec_from_file_location('case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    recipe=inputs['recipe'];rows=[]
    for index,name in enumerate(inputs['cells']):
        case=await module.CaseManager(workspace/f'context-use-{index}.sqlite',now=recipe['evaluated_at']).open()
        o=dict(context_use_cell=name,executor_version=2,input=recipe,calls=case.events,sources=case.sources,
            uses={},order=[],phase='register')
        try:
            o['registration']=dc.asdict(await case.manager.register_principal_owner(case.principal,m.MemoryScope.personal(case.principal.actor_id)))
            o['phase']='two_real_items'
            created=[]
            for i,seed in enumerate(recipe['seeds']):
                created.append(await case.seed(seed,operation_id='context-create-'+str(i+1),evidence_id='context-user-'+str(i+1)))
            o['phase']='two_item_recall';o['initial']=await recall(case,recipe,'context-initial')
            o['phase']='two_actual_pages';bundle=await context_bundle(case,o['initial'])
            case.now=recipe['use_at']
            original=use_request(case,bundle,recipe['attempt'],recipe['use_at'])
            before=name not in {'current-use/context:suppression-first','current-use/authority:suppression'}
            if before:
                o['phase']='first';o['uses']['first']=await authorize(case,bundle,original);o['order'].append('first')
                if 'receipt' not in o['uses']['first']: raise ValueError('initial legal authorization rejected')
                receipt=h.RecallContextUseReceiptV1.from_json(o['uses']['first']['receipt'])
                next_request=use_request(case,bundle,recipe['next_attempt'],recipe['next_use_at'])
                o['receipt_validation']={'same_request':validate(receipt,original),
                    'different_attempt':validate(receipt,dc.replace(original,provider_attempt_id=recipe['next_attempt']))}
                if name.endswith('duplicate-same-provider-attempt'):
                    o['uses']['duplicate']=await authorize(case,bundle,original);o['order'].append('duplicate')
                if name.endswith('new-provider-attempt'):
                    case.now=recipe['next_use_at']
                    o['uses']['new_before_suppression']=await authorize(case,bundle,next_request)
                    o['order'].append('new_before_suppression')
                if name.endswith('wrong-snapshot'):
                    attack={**original.to_json(),'snapshot_manifest_hash':'f'*64}
                    o['wrong_snapshot']={'input':attack}
                    try:h.RecallContextUseAuthorizationRequestV1.from_json(attack)
                    except Exception as exc:o['wrong_snapshot']['exception']=error(exc)
                    changed=dc.replace(original,snapshot_fragment_bindings=tuple(reversed(original.snapshot_fragment_bindings)),
                        snapshot_manifest_hash=hashlib.sha256(canonical([r.to_json() for r in reversed(original.snapshot_fragment_bindings)])).hexdigest())
                    o['changed_snapshot']=await authorize(case,bundle,changed)
            o['phase']='suppression_commit'
            request=m.SuppressionRequest('context-forget',case.principal.actor_id,m.SuppressionScopeKind.MEMORY,
                created[0].memory_id,'user_forget',case.now,purpose=None)
            decision=await case.manager.suppress(principal=case.principal,request=request)
            o['suppression']={'request':request.to_json(),'decision':decision.to_json()}
            case.events.append({'call':'suppress',**o['suppression']});o['order'].append('suppression_commit')
            o['phase']='reopen';await case.close();await case.open()
            if before:
                o['uses']['replay_after_reopen']=await authorize(case,bundle,original)
                o['order'].append('replay_after_reopen');case.now=recipe['next_use_at']
            fresh=use_request(case,bundle,recipe['after_attempt'] if before else recipe['attempt'],case.now)
            o['uses']['after_suppression']=await authorize(case,bundle,fresh);o['order'].append('after_suppression')
            o['phase']='current_epoch_and_unaffected_control';o['after']=await recall(case,recipe,'context-after')
            o['phase']='historical_replay'
            o['historical_replay']=case.cases.execution_wire(await case.manager.execute_typed_recall(principal=case.principal,
                context=h.RecallContext.from_json(o['initial']['context']),plan=h.RecallPlan.from_json(o['initial']['plan']),now=case.now))
            o['phase']='complete'
        except Exception as exc:o['exception']=error(exc)
        finally:await case.close()
        rows.append(dict(cell_id=name,status='OBSERVED',reason='',observations=o))
    return rows
