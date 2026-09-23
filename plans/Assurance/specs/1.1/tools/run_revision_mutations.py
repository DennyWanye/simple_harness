#!/usr/bin/env python3
"""F01–F15 reference fault injection. Copies only the package, never the SDK."""
import argparse,ast,json,re,shutil,subprocess,sys,tempfile
from pathlib import Path
MUTATIONS=[
 ('F03_ignore_disclosure','reference/protocol_v11.py'," or label not in actually_disclosed",'',None),
 ('F04_error_becomes_pass','reference/protocol_v11.py',"if not self.source_valid or self.execution_state != 'SUCCEEDED':","if not self.source_valid:",None),
 ('F04_semantic_invented_gate','reference/protocol_v11.py',"return CheckGate(None, (), 'CHECK_NOT_APPLICABLE')","return CheckGate(Grade.UNKNOWN, (), 'CHECK_NOT_APPLICABLE')",None),
 ('F07_accept_backup_grant','reference/protocol_v11.py',"grant['root_incarnation_id']==root_incarnation","True",None),
 ('F08_ignore_duplicate_read_key','reference/protocol_v11.py',"if k in seen: raise ContractError('DUPLICATE_READ_KEY')","if False: raise ContractError('DUPLICATE_READ_KEY')",None),
 ('F11_initial_finalized','sql/assurance_additive.sql',None,None,'assurance_closeout_initial'),
 ('F11_finalized_delete','sql/assurance_additive.sql',None,None,'assurance_closeout_no_delete'),
 ('F11_finalized_replace','sql/assurance_additive.sql',None,None,'assurance_closeouts_no_replace'),
 ('F11_bound_no_review','sql/assurance_additive.sql',None,None,'assurance_blob_pin_bound_review'),
]
def run(root):
 root=Path(root).resolve();results=[]
 for name,file,old,new,trigger in MUTATIONS:
  original=(root/file).read_text()
  if trigger:
   pattern=r'CREATE TRIGGER '+re.escape(trigger)+r'\b.*?\nEND;|CREATE TRIGGER '+re.escape(trigger)+r'\b.*?END;'
   changed,n=re.subn(pattern,'-- mutation removed '+trigger,original,count=1,flags=re.S)
   if n!=1:raise ValueError('MUTATION_TARGET_MISSING:'+trigger)
  else:
   if original.count(old)!=1:raise ValueError('MUTATION_TARGET_AMBIGUOUS:'+name)
   changed=original.replace(old,new,1);ast.parse(changed)
  with tempfile.TemporaryDirectory(prefix='assurance-revision-mutation-') as td:
   p=Path(td)/'kit';shutil.copytree(root,p,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
   (p/file).write_text(changed)
   r=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(p/'tests'),'-p','test_review_findings.py','-q'],cwd=p,capture_output=True,text=True,timeout=30)
   killed=r.returncode!=0 and 'FAIL:' in r.stderr and 'ImportError' not in r.stderr and 'SyntaxError' not in r.stderr
   results.append({'id':name,'scope':'REFERENCE_RULES_AND_SQL_ONLY','result':'KILLED' if killed else 'SURVIVED_OR_INVALID','assertion_failures':r.stderr.count('FAIL:'),'returncode':r.returncode})
 return results

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out)
 if out.exists():raise SystemExit('OUTPUT_EXISTS')
 results=run(a.root);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps({'reference_mutations':len(results),'killed':sum(x['result']=='KILLED' for x in results),'sdk_mutations_executed':0}))
 return 0 if all(x['result']=='KILLED' for x in results) else 3
if __name__=='__main__':raise SystemExit(main())
