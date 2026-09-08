"""Real public strict parser and result page calls, preserving frozen bounds."""
import copy
import dataclasses as dc
import importlib.util
from pathlib import Path
import simple_harness as h


async def run_cases(inputs,workspace):
    if not inputs['cases']:return []
    spec=importlib.util.spec_from_file_location('case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    case=await helper.CaseManager(workspace/'return.sqlite').open()
    rows=[]
    try:
        await case.seed(inputs['seed'])
        context,plan=case.request(query='3.11')
        context=dc.replace(context,expires_at=helper.seconds(inputs['expires_at']))
        plan=dc.replace(plan,context_hash=context.context_hash)
        first=await case.manager.execute_typed_recall(principal=case.principal,context=context,plan=plan,now=case.now)
        baseline=dict(context=context.to_json(),plan=plan.to_json(),execution=case.cases.execution_wire(first))
        for recipe in inputs['cases']:
            name=recipe['id'];o=dict(return_recipe=recipe,baseline=baseline,calls=[],memory_called=False)
            o['before']=await case.snapshot()
            try:
                if name=='strict-v3-rejected':
                    raw=copy.deepcopy(first.decision.to_json());raw['schema_version']=recipe['wire_protocol_version']
                    o.update(parser_input=raw,calls=['RecallDecisionV4.from_json'])
                    o['returned']=h.RecallDecisionV4.from_json(raw).to_json()
                elif name in {'invalid-source-discriminant','cognitive-missing-revision','short-fake-revision'}:
                    raw=copy.deepcopy(first.decision.selected_items[0].to_json())
                    raw.update({k:v for k,v in recipe.items() if k in {'source_kind','source_ref','source_revision','chunk_ref'}})
                    if name=='short-fake-revision':raw.update(memory_type=None,source_ref=raw['chunk_ref'])
                    o.update(parser_input=raw,calls=['RecallSelectedItemV4.from_json'])
                    o['returned']=h.RecallSelectedItemV4.from_json(raw).to_json()
                elif name=='naked-source-ref':
                    o.update(page_input=recipe['request'],calls=['page_typed_recall_result'],memory_called=True)
                    o['returned']=(await case.manager.page_typed_recall_result(principal=case.principal,request=recipe['request'])).to_json()
                else:
                    use_at=helper.seconds(recipe.get('use_at',case.now))
                    if 'expired' in name:case.now=use_at
                    page=h.RecallResultPageRequestV1(first.result.result_id,
                        recipe['result_hash'] if name=='page-wrong-result-hash' else first.result.result_hash,
                        1,recipe['coordinate']-1,1,recipe.get('bounds',{}).get('length',16384),use_at)
                    o.update(page_input=page.to_json(),calls=['page_typed_recall_result'],memory_called=True)
                    if 'bounds' in recipe:
                        # Boundary counter-test for the sealed minimal page budget: one byte
                        # under the bound must be refused, so the bound is a real boundary and
                        # not a threshold migrated to whatever the implementation returns.
                        under=h.RecallResultPageRequestV1(first.result.result_id,first.result.result_hash,
                            1,recipe['coordinate']-1,1,recipe['bounds']['length']-1,use_at)
                        try:
                            await case.manager.page_typed_recall_result(principal=case.principal,request=under)
                            o['under_bound']={'returned':True}
                        except Exception as exc:
                            o['under_bound']={'returned':False,'request':under.to_json(),
                                'exception':dict(type=type(exc).__name__,reason=str(exc))}
                        o['calls'].append('page_typed_recall_result')
                    o['returned']=(await case.manager.page_typed_recall_result(principal=case.principal,request=page)).to_json()
            except Exception as exc:o['exception']=dict(type=type(exc).__name__,reason=str(exc))
            finally:case.now=1788170400.0
            o['after']=await case.snapshot()
            rows.append(dict(cell_id='protocol/'+name,status='OBSERVED',reason='',observations=o))
    finally:await case.close()
    return rows
