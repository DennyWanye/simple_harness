#!/usr/bin/env python3
"""Mutation challenge on reference semantics only; never edits local SDK or original kit."""
from pathlib import Path
import argparse,ast,json,shutil,subprocess,sys,tempfile

MUTATIONS=[
 ('duplicate_keys',"raise ContractError('DUPLICATE_KEY')","pass"),
 ('bool_as_int',"if type(value) is not int or not minimum <= value <= 9007199254740991:","if not isinstance(value,int) or not minimum <= value <= 9007199254740991:"),
 ('mandatory_ignored',"hard = [grades.get(c, Grade.UNKNOWN) for c in mandatory]","hard = []"),
 ('wrong_evidence_allowed',"if not set(refs).issubset(visible_evidence):","if False:"),
 ('cycle_self_certifies',"supported = closure(anchors, set())","supported = closure(anchors | frozenset(r.conclusion for r in rules), set())"),
 ('conflict_path_used',"return Closure(supported, closure(anchors, conflicts))","return Closure(supported, supported)"),
 ('proof_context_ignored',"if context != (self.mission_id,self.consumer_id,self.purpose,self.scope_id):","if False:"),
 ('expiry_inclusive',"now_ms >= self.not_after_ms","now_ms > self.not_after_ms"),
 ('negative_set_ignored',"if any(sets.get(k) != v for k,v in self.set_digests):","if False:"),
 ('milestone_ignored',"if p.milestone != milestone or p.milestone_policy_hash != policy:","if False:")]

def run(root):
    root=Path(root).resolve();original=(root/'reference/semantics.py').read_text();results=[]
    for name,old,new in MUTATIONS:
        if original.count(old)!=1:raise ValueError('ambiguous mutation '+name)
        changed=original.replace(old,new,1);ast.parse(changed)
        with tempfile.TemporaryDirectory(prefix='assurance-ref-mut-') as tmp:
            d=Path(tmp);shutil.copytree(root/'reference',d/'reference');shutil.copytree(root/'tests',d/'tests');shutil.copytree(root/'sql',d/'sql')
            (d/'reference/semantics.py').write_text(changed)
            p=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(d/'tests'),'-p','test_semantics.py','-q'],cwd=d,capture_output=True,text=True,timeout=30)
            killed=p.returncode!=0 and 'FAIL:' in p.stderr and 'ImportError' not in p.stderr and 'SyntaxError' not in p.stderr
            results.append({'id':name,'scope':'REFERENCE_ONLY','returncode':p.returncode,'verdict':'KILLED' if killed else 'SURVIVED_OR_INVALID','behavior_failures':p.stderr.count('FAIL:')})
    return results

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out)
    if out.exists():raise SystemExit('OUTPUT_EXISTS')
    results=run(a.root);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({'reference_mutations':len(results),'killed':sum(x['verdict']=='KILLED' for x in results),'sdk_mutations_executed':0}))
    return 0 if all(x['verdict']=='KILLED' for x in results) else 3
if __name__=='__main__':raise SystemExit(main())
