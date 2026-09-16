#!/usr/bin/env python3
"""Validate the DELIVERABLE only. Never imports or executes SimpleHarness."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
from importlib.metadata import version

ROOT=Path(__file__).resolve().parents[1]
def read(name: str):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)
def main() -> int:
    reqs=read('requirements.json'); wps=read('work-packages.json')
    cases=read('acceptance-scenarios.json'); src=read('sources.json')
    chapter_map=read('original-chapter-map.json')
    ids=lambda xs:{x['id'] for x in xs}
    R,W,T,S=map(ids,(reqs,wps,cases,src))
    for label,data,keys in [('requirement',reqs,R),('package',wps,W),('case',cases,T),('source',src,S)]:
        require(len(data)==len(keys),f'duplicate {label} id')
    require(len(reqs)==60 and sum(r['required'] for r in reqs)==59,'scope count differs')
    require(len(cases)==90,'test specification count differs')
    require([x['chapter'] for x in chapter_map]==list(range(1,32)),'original chapters not complete')
    for r in reqs:
        require(r['work_package'] in W,f"unmapped work package: {r['id']}")
        require(set(r['evidence'])<=S,f"unknown source in {r['id']}")
        require(any(r['id'] in c['requirement_ids'] for c in cases),f"no case for {r['id']}")
        require(r['implementation_state']=='NOT_EXECUTED_IN_THIS_AUDIT','false test status')
    for c in cases:
        require(set(c['requirement_ids'])<=R and c['work_package'] in W,f"bad case refs {c['id']}")
        require(c['status']=='NOT_RUN' and c['runner_node_id'] is None,'case falsely presented as run')
        require(c['steps'] and c['assertions'],'empty behavior test')
    pending={w['id']:set(w['depends_on']) for w in wps};done=set();order=[]
    for w in wps:
        require(set(w['depends_on'])<=W,'unknown dependency')
        require(set(w['requirement_ids'])<=R and set(w['test_ids'])<=T,'bad package references')
    while pending:
        ready=sorted(k for k,v in pending.items() if v<=done)
        require(bool(ready),'cyclic package dependencies')
        for key in ready:pending.pop(key);done.add(key);order.append(key)
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print('Install jsonschema in an isolated environment to validate proposed schema fixtures.',file=sys.stderr)
        return 2
    schemas={p.name:json.loads(p.read_text()) for p in (ROOT/'schemas').glob('*.schema.json')}
    for obj in schemas.values():Draft202012Validator.check_schema(obj)
    outcomes=[]
    for f in read('schema-fixtures/fixtures.json'):
        validator=Draft202012Validator(schemas[f['schema']])
        errors=list(validator.iter_errors(f['payload']))
        actual=not errors
        require(actual==f['expect_structure_valid'],f"schema fixture mismatch: {f['name']}")
        outcomes.append({'name':f['name'],'structural_validation':actual,'expected':f['expect_structure_valid']})
    plan=ROOT/'complete-plan.zh-CN.md'
    text=plan.read_text()
    require(text.count('```')%2==0,'unbalanced Markdown code fences')
    require(all(r['id'] in text for r in reqs),'main document missing requirement id')
    report={
        'result':'DELIVERABLE_STRUCTURE_VALID',
        'scope':'Only artifact references, requirements mapping, dependency graph and proposed JSON schema structures',
        'sdk_tests_run':False,'real_model_tests_run':False,'ui_tests_run':False,'production_semantic_validator_run':False,
        'requirements':len(reqs),'mandatory_requirements':59,'explicit_exclusions':1,
        'work_packages':len(wps),'test_specifications':len(cases),'original_chapters':31,
        'source_entries':len(src),'proposed_schemas':len(schemas),'schema_fixtures':len(outcomes),
        'schema_library_version':version('jsonschema'),'topological_package_order':order,
        'schema_fixture_results':outcomes,
        'main_document_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),
        'note':'Schema validity does not establish HTN semantics, authorization, budget correctness or runnable project integration.'}
    (ROOT/'deliverable-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='schema_fixture_results'},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print(f'DELIVERABLE VALIDATION FAILED: {e}',file=sys.stderr)
        raise SystemExit(1)
