#!/usr/bin/env python3
"""Validate the delivered specification and asset relationships. Never marks SDK PASS."""
from pathlib import Path
import argparse, ast, json, sqlite3, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from schema_support import field_rows, validate, ref_target, digest, expanded
from reference.sql_statements import iter_sql_statements
from reference.contract_constants import INTERNAL_REF_KINDS
from reference.protocol_v11 import merge_catalogues
from reference.semantics import validate_subject_shape

def check(root):
    root=Path(root).resolve();errors=[]
    def load(p):return json.loads((root/p).read_text())
    def require(ok,msg):
        if not ok:errors.append(msg)
    cases=load('implementation/sdk-cases.json');ids={x['id'] for x in cases}
    require(len(cases)==len(ids)==48,'original 48 case IDs not retained')
    for c in cases:
        require(not c['actual_nodeids'] and not c['source_fingerprint'] and c['execution_status']=='PENDING_SDK_EXECUTION','unearned SDK evidence '+c['id'])
        for k in ('given','when','then','target_nodeid','production_paths'):require(bool(c[k]),'incomplete case '+c['id']+':'+k)
    for m in load('implementation/mutations.json'):
        require(set(m['must_be_killed_by'])<=ids,'unmapped mutation '+m['id'])
        require(m['sdk_status']=='PENDING_SDK_EXECUTION','unearned mutation PASS')
    originals=load('inputs/aer-66-cases.json');originals=originals.get('cases',originals) if isinstance(originals,dict) else originals
    originals={x['id']:x for x in originals}
    inherited=load('implementation/inherited-coverage.json')
    require(set(x['original_id'] for x in inherited)==set(originals),'inherited case identities differ')
    for r in inherited:
        o=originals[r['original_id']]
        require(r['original_contract']==o,'inherited contract changed '+r['original_id'])
        require(o['then'] in r['required_assertions'],'original MUST assertion lost '+r['original_id'])
        require(bool(r['owner'] and r['trigger_entry'] and r['production_paths'] and r['acceptance_gate']),'unassigned inherited owner '+r['original_id'])
    occ=load('inputs/operation-completion-cases.json')['cases'];covered=load('implementation/occ-coverage.json')
    require({x['id'] for x in occ}=={x['id'] for x in covered},'OCC ids differ')
    for a in occ:
        b=next(x for x in covered if x['id']==a['id'])
        require(all(b.get(k)==v for k,v in a.items()),'OCC original assertions changed '+a['id'])
    seams=load('implementation/seams.json')
    require({x['id'] for x in seams}=={'S%02d'%i for i in range(1,27)},'seam IDs differ')
    for s in seams:require(bool(s['target_path'] and s['symbol'] or s['classification'].startswith('REUSE_REPORTED')),'unresolved producer target '+s['id'])
    require(load('implementation/source-map.seed.json')['entries']==seams,'SOURCE_MAP_SEAM_DRIFT')
    require(load('implementation/integration-map.json')['seams']==seams,'INTEGRATION_MAP_SEAM_DRIFT')
    expected=field_rows(root);actual=load('implementation/field-producers.json')
    require(expected==actual,'FIELD_PRODUCER_SCHEMA_DRIFT: pointers/resolved subtrees/owners differ')
    require(all(x['producer_rule']!='UNMAPPED_MUST_FAIL' for x in expected),'unmapped field producer')
    kinds=load('contracts/common.schema.json')['$defs']['ref']['properties']['kind']['enum']
    require(set(kinds)==set(INTERNAL_REF_KINDS),'INTERNAL_KIND_CODEC_DRIFT')
    refmap=load('implementation/ref-resolution-map.json')
    require({x['kind'] for x in refmap['kinds']}==set(kinds),'REF_RESOLVER_KIND_DRIFT')
    for item in refmap['all_reference_fields']+refmap['pin_fields']:
        from schema_support import pointer
        p=root/item['schema_file'];n=pointer(json.loads(p.read_text()),item['json_pointer'])
        require(digest(expanded(root,p,n))==item['source_hash'],'REF_FIELD_SCHEMA_DRIFT:'+item['json_pointer'])
    for f in load('contracts/structural-fixtures.json'):
        p=root/f['schema'];n=json.loads(p.read_text())
        try:validate(root,p,n,f['valid_structural_example'])
        except ValueError as e:errors.append('valid fixture:'+str(p.name)+':'+str(e))
        try:validate(root,p,n,f['invalid_unknown_field']);errors.append('unknown-field fixture accepted:'+p.name)
        except ValueError:pass
    for f in load('contracts/six-purpose-fixtures.json'):
        p=root/'contracts/review-binding-v2.schema.json'
        try:
            validate(root,p,json.loads(p.read_text()),f['review_binding'])
            validate_subject_shape(f['review_binding']['subject'])
            merge_catalogues(f['review_binding']['review_key'],[f['review_binding']['evidence_catalogue']])
            p=root/'contracts/review-invocation-v1.schema.json';validate(root,p,json.loads(p.read_text()),f['invocation'])
        except ValueError as e:errors.append('six-purpose fixture '+f['purpose']+':'+str(e))
    # Enumerate ALL actually declared side-table columns; no magic 9-table count.
    db=sqlite3.connect(':memory:');db.execute('PRAGMA foreign_keys=ON')
    try:
        for p in ('tests/parent_fixture.sql','sql/assurance_additive.sql'):
            for st in iter_sql_statements((root/p).read_text()):db.execute(st)
        columns={(t[0],c[1],c[2],bool(c[3]),c[5]) for t in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'assurance_%'") for c in db.execute('PRAGMA table_info('+t[0]+')')}
        rows=load('implementation/sql-column-producers.json')
        require(columns=={(x['table'],x['column'],x['sql_type'],x['not_null'],x['primary_key_position']) for x in rows},'SQL_COLUMN_MAP_DRIFT')
        require(all(x['writer'] and x['source_rule'] and x['transaction'] for x in rows),'SQL producer missing')
        require(not list(db.execute('PRAGMA foreign_key_check')),'reference DDL foreign keys')
    except (ValueError,sqlite3.Error) as e:errors.append('DDL:'+str(e))
    finally:db.close()
    doc=(root/'ASSURANCE-EXEC-1.1.zh-CN.md').read_text()
    require(sum(x.startswith('```') for x in doc.splitlines())%2==0,'unclosed code fence')
    for n in range(1,16):require('F%02d'%n in doc,'finding absent')
    for folder in ('reference','tools','tests'):
        for p in (root/folder).glob('*.py'):
            try:ast.parse(p.read_text())
            except SyntaxError:errors.append('syntax:'+str(p.relative_to(root)))
    return errors

def main():
    a=argparse.ArgumentParser();a.add_argument('--root',required=True);args=a.parse_args()
    try:errors=check(args.root)
    except (KeyError,ValueError,OSError,TypeError) as e:errors=['CHECKER_INPUT_ERROR:'+str(e)]
    print(json.dumps({'scope':'SPECIFICATION_ASSET_CONSISTENCY_ONLY','status':'PASS' if not errors else 'FAIL','errors':errors,'sdk_verified':False,'independent_review':False},ensure_ascii=False))
    return 3 if errors else 0
if __name__=='__main__':raise SystemExit(main())
