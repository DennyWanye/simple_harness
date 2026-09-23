#!/usr/bin/env python3
"""Lightweight contract/asset check; no unittest discovery, SDK import or product DB."""
import argparse,ast,copy,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from reference.schema_codec import validate,validate_semantics
from reference.runtime_rules import canonical,RuleError

def pointer(doc,p):
    cur=doc
    for part in p.strip('/').split('/'):
        key=part.replace('~1','/').replace('~0','~');cur=cur[int(key)]if isinstance(cur,list)else cur[key]
    return cur

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',default=str(ROOT));a=ap.parse_args()
    if Path(a.root).resolve()!=ROOT:raise SystemExit('Use the check_plan.py inside the exact --root tree')
    schema=json.loads((ROOT/'contracts/runtime-plane.schema.json').read_text());defs=schema['$defs'];issues=[]
    fields=json.loads((ROOT/'implementation/field-producers.json').read_text());types=json.loads((ROOT/'implementation/type-producers.json').read_text())
    for row in fields:
        sub=pointer(schema,row['pointer']);h=hashlib.sha256(json.dumps(sub,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if h!=row['schema_hash']:issues.append('FIELD_SCHEMA_DRIFT:'+row['pointer'])
        if row['schema']not in types:issues.append('MISSING_PRODUCER:'+row['schema'])
    def walk(x):
        if isinstance(x,dict):
            if '$ref'in x:
                try:pointer(schema,x['$ref'][1:])
                except (KeyError,ValueError):issues.append('BAD_SCHEMA_REF:'+x['$ref'])
            for v in x.values():walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(schema)
    ex=json.loads((ROOT/'examples/typed-fixtures.json').read_text())['fixtures']
    for name,val in ex.items():
        try:validate(val,defs[name]);validate_semantics(name,val)
        except RuleError as e:issues.append('EXAMPLE:'+name+':'+str(e))
    for p in ROOT.rglob('*.py'):
        if 'inputs'in p.parts:continue
        ast.parse(p.read_text())
    ds=json.loads((ROOT/'implementation/decisions.json').read_text())
    if {x['id']for x in ds}!={f'Q{i:02}'for i in range(1,21)}:issues.append('DECISIONS_INCOMPLETE')
    for x in ds:
        for a in x['assets']:
            if not(ROOT/a).is_file():issues.append('MISSING_ASSET:'+a)
    old=json.loads((ROOT/'inputs/original-sdk-cases.json').read_text());new=json.loads((ROOT/'implementation/sdk-cases.json').read_text());nm={x['id']:x for x in new}
    for x in old:
        for f in ('given','when','then'):
            if nm.get(x['id'],{}).get(f)!=x.get(f):issues.append('INHERITED_ASSERTION_CHANGED:'+x['id']+':'+f)
    refs=json.loads((ROOT/'implementation/ref-resolution-map.json').read_text());kinds=set(defs['Pin']['properties']['kind']['enum'])
    if kinds!=set(refs):issues.append('PIN_RESOLVERS_INCOMPLETE')
    events=json.loads((ROOT/'contracts/event-catalogue.json').read_text())
    for e,v in events.items():
        if v['schema']not in defs:issues.append('EVENT_SCHEMA_MISSING:'+e)
    verbs=json.loads((ROOT/'contracts/host-verbs.json').read_text())
    for n,v in verbs.items():
        if v['request_type']not in defs or v['response_type']not in defs:issues.append('DTO_MISSING:'+n)
    # Known documented conflicts must actually be caught, not only counted.
    mutated=json.loads(json.dumps(ex['ContextManifest']));mutated['profile_ref']['kind']='agent'
    try:validate(mutated,defs['ContextManifest']);issues.append('WRONG_PIN_ACCEPTED')
    except RuleError:pass
    # R1-R5 are narrow semantic closures, not file-count-only claims.
    rd=json.loads((ROOT/'implementation/review-decisions.json').read_text())
    if {x['id']for x in rd}!={f'R{i}'for i in range(1,6)}:issues.append('REVIEW_DECISIONS_INCOMPLETE')
    if set(defs)!=set(types):issues.append('TYPE_PRODUCERS_INCOMPLETE')
    if set(defs['ErrorCode']['enum'])!=set(json.loads((ROOT/'contracts/error-catalogue.json').read_text())):issues.append('ERROR_CODES_DRIFT')
    from reference.schema_codec import decode
    from reference.search_scan import ScoredRow,freeze,advance
    a=copy.deepcopy(ex['SearchPage']);a.update(has_more=True,next_cursor='next',page_semantics='PROGRESS')
    a['receipt'].update(query_result_count=None,has_more=True,status='COMPLETE_EMPTY')
    a['receipt']['coverage'].update(phase='SCANNING',rank_scope='NONE',ranking_final=False,snapshot_chunks=1,scanned_chunks=0)
    b=copy.deepcopy(ex['SearchPage']);b['receipt']['coverage'].update(index_coverage='PARTIAL',expected_groups=1,indexed_groups=0)
    for key,value in [('R2-A',a),('R2-B',b)]:
        try:decode('SearchPage',canonical(value));issues.append('SEMANTIC_COUNTEREXAMPLE_ACCEPTED:'+key)
        except RuleError:pass
    for key,cap in [('max_scan_rows_per_page',256),('max_search_cursors',16),('max_retrieval_ms',500),('max_query_total_ms',30000)]:
        value=copy.deepcopy(ex['Policy']);value[key]=cap+1
        try:decode('Policy',canonical(value));issues.append('HARD_CAP_NOT_REJECTED:'+key)
        except RuleError:pass
    rows=(ScoredRow(1,'a',(('words',1),),'z','a'*64,0,1),ScoredRow(2,'z',(('words',1),),'a','a'*64,0,1))
    if advance(freeze(rows),rows,page_rows=2,top_k=1).results[0][0]!='z':issues.append('R4_STABLE_KEY_DRIFT')
    print(json.dumps({'check':'PLAN_ASSET_AND_SELECTED_SEMANTIC_CHECKS','status':'FAIL'if issues else'PASS','schemas':len(defs),'fields':len(fields),'examples':len(ex),'sdk_cases':len(new),'issues':issues},ensure_ascii=False))
    raise SystemExit(2 if issues else 0)
if __name__=='__main__':main()
