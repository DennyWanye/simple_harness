"""Checks package consistency; not a proof of semantics or actual SDK wiring."""
from pathlib import Path
import argparse,ast,hashlib,json,re,sys

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def pointer(doc,p):
    for token in p.split('/')[1:]:doc=doc[token.replace('~1','/').replace('~0','~')]
    return doc

def check(root:Path):
    root=root.resolve(strict=True);errors=[]
    schema=json.loads((root/'contracts/runtime-plane.schema.json').read_text());defs=schema['$defs']
    rows=json.loads((root/'implementation/field-producers.json').read_text())
    expected={f'/$defs/{n}/properties/{f}' for n,d in defs.items() for f in d.get('properties',{})}
    actual=[x['schema_pointer'] for x in rows]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:errors.append('FIELD_MAP_COVERAGE')
    for x in rows:
        if hashlib.sha256(canonical(pointer(schema,x['schema_pointer']))).hexdigest()!=x['schema_subtree_sha256']:errors.append('SCHEMA_DRIFT:'+x['schema_pointer'])
        if not (root/x['semantics_source']).is_file() or not x['producer']:errors.append('PRODUCER_SOURCE:'+x['schema_pointer'])
    for p in root.rglob('*.py'):
        if '__pycache__' in p.parts:continue
        try:ast.parse(p.read_text(encoding='utf8'))
        except SyntaxError:errors.append('PYTHON_SYNTAX:'+str(p))
    cases=json.loads((root/'implementation/sdk-cases.json').read_text());muts=json.loads((root/'implementation/mutations.json').read_text())
    if isinstance(cases,dict):cases=cases['cases']
    if isinstance(muts,dict):muts=muts['mutations']
    ids=[x['id'] for x in cases]
    if len(ids)!=60 or len(set(ids))!=60:errors.append('CASE_IDS')
    if len(muts)!=16 or len({x['id'] for x in muts})!=16:errors.append('MUTATION_IDS')
    sys.path.insert(0,str(root))
    from reference.schema_codec import decode
    samples=0
    for p in (root/'examples').glob('*.json'):
        try:decode(p.stem,p.read_bytes());samples+=1
        except Exception as e:errors.append('EXAMPLE:'+p.name+':'+str(e))
    required=['implementation/FIELD-CONTRACTS.md','implementation/HOST-DTOS.md','implementation/INTERFACES.md','implementation/BODY-WIRED.md','review/CHALLENGE-REPORT.md','review/INDEPENDENT-REVIEW.md','ARP-EXEC-1.0.zh-CN.md']
    for f in required:
        if not (root/f).is_file():errors.append('MISSING:'+f)
    return {'scope':'PACKAGE_CONSISTENCY_ONLY','status':'PASS' if not errors else 'FAIL','schema_definitions':len(defs),'mapped_fields':len(rows),'structural_examples':samples,'sdk_target_cases':len(cases),'sdk_target_mutations':len(muts),'errors':errors,'sdk_verified':False}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);n=a.parse_args()
    try:r=check(n.root)
    except Exception as e:r={'status':'FAIL','error':repr(e),'sdk_verified':False}
    print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['status']=='PASS' else 3)
