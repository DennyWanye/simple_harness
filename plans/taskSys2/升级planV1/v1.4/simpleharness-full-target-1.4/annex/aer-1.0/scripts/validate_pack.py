"""Validate package structure/schemas/mappings only, never production behavior."""
from __future__ import annotations
import hashlib,json,pathlib,sys
from importlib.metadata import version
from jsonschema import Draft202012Validator
P=pathlib.Path(__file__).resolve().parents[1]
def read(name): return json.loads((P/name).read_text(encoding='utf-8'))
def require(value,msg):
    if not value: raise AssertionError(msg)
reqs=read('requirements-map.json');sc=read('acceptance-scenarios.json')
require(len(reqs)==60,'master requirements count changed')
require(len({r['requirement_id'] for r in reqs})==60,'duplicate master ID')
require(len(sc)==48,'scenario count mismatch')
require(len({s['id'] for s in sc})==48,'duplicate scenario')
ids={r['requirement_id'] for r in reqs};sids={s['id'] for s in sc}
for s in sc:
    require(set(s['requirements'])<=ids,'unknown requirement in '+s['id'])
    require(s['status']=='NOT_EXECUTED_ON_SIMPLEHARNESS','false execution status')
for r in reqs:
    require(set(r['scenario_ids'])<=sids,'unknown scenario')
    require(r['upstream_status_not_changed'] is True,'master completion changed')
for name in ['design.zh-CN.md','README.zh-CN.md','sources.json','code-changes.json',
             'reference/protocol_rules.py','tests/test_reference.py','reference-tests.log']:
    require((P/name).is_file(),'missing '+name)
passed=[]
for f in read('fixtures/index.json'):
    schema=read('schemas/'+f['schema']); Draft202012Validator.check_schema(schema)
    fixture=read('fixtures/'+f['fixture']); errors=list(Draft202012Validator(schema).iter_errors(fixture))
    require((len(errors)==0)==f['expect_valid'],'fixture mismatch: '+f['fixture'])
    passed.append({'fixture':f['fixture'],'expected_valid':f['expect_valid'],'matched':True})
log=(P/'reference-tests.log').read_text(encoding='utf-8')
require('Ran 30 tests' in log and log.rstrip().endswith('OK'),'missing actual reference result')
report={'package_validation':'PASS','reference_tests':{'count':30,'result':'PASS','scope':'pure reference rules only'},
        'master_requirements_preserved':60,'production_scenarios_defined':48,
        'production_scenarios_executed':0,'schema_fixture_results':passed,
        'python':sys.version,'jsonschema':version('jsonschema'),
        'not_tested':['SimpleHarness SDK','actual models','database concurrency','external side effects','Host UI','platform executors']}
(P/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=[]
for f in sorted(P.rglob('*')):
    if f.is_file() and f.name!='MANIFEST.sha256' and '__pycache__' not in f.parts:
        lines.append(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.relative_to(P).as_posix())
(P/'MANIFEST.sha256').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'package':'PASS','reference_tests':'30 PASS (pure rules only)',
                  'schema_fixtures':len(passed),'master_requirements':60,
                  'production_scenarios_executed':0},ensure_ascii=False))
