"""Run explicit mutations on isolated copies. Import/syntax errors never count as kills."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,tempfile,os
MUTATIONS=[
 ('RM01','if actual_or_upper_input_tokens>selection.input_budget:', 'if False:', 'BudgetTests.test_count_tools_and_final_bytes'),
 ('RM02',"if session!=observed_session or generation!=observed_generation:","if False:",'IdentityTests.test_cross_agent'),
 ('RM03',"if live_state!='ACTIVE':","if False:",'IdentityTests.test_closed_not_readable'),
 ('RM04','result=result&s','result=result|s','IdentityTests.test_permissions_intersect'),
 ('RM05',"if not exposed:","if False:",'IdentityTests.test_unexposed_tool'),
 ('RM06',"if effect_class=='EXTERNAL_EFFECT':return 'ORIGINAL_OPERATION_INTENT'","if effect_class=='EXTERNAL_EFFECT':return 'ORIGINAL_TOOL_EXECUTOR'",'IdentityTests.test_effect_not_direct'),
 ('RM07','return closed and all(n==0 for n in counts)','return all(n==0 for n in counts)','IdentityTests.test_purge_crash_not_close'),
 ('RM08',"if k in d: raise RuleError('DUPLICATE_JSON_KEY')","if False: raise RuleError('DUPLICATE_JSON_KEY')",'IdentityTests.test_json_duplicates'),
 ('RM09',"if r.groups & protected:continue","if False:continue",'BudgetTests.test_random_invariants'),
 ('RM10',"if key in seen:raise RuleError('SKILL_PATH_COLLISION')","if False:raise RuleError('SKILL_PATH_COLLISION')",'IdentityTests.test_case_collision'),
]
def run(root:Path):
 root=root.resolve(strict=True);original=(root/'reference/runtime_rules.py').read_text();rows=[]
 for mid,before,after,test in MUTATIONS:
  if original.count(before)!=1:rows.append({'id':mid,'status':'INVALID_MUTATION','matches':original.count(before)});continue
  with tempfile.TemporaryDirectory(prefix='arp-mutation-') as tmp:
   dst=Path(tmp)/'kit';shutil.copytree(root,dst,ignore=shutil.ignore_patterns('__pycache__','*.pyc','reports','inputs'))
   p=dst/'reference/runtime_rules.py';p.write_text(original.replace(before,after,1))
   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
   cp=subprocess.run([sys.executable,'-B','-m','unittest','tests.test_rules.'+test,'-v'],cwd=dst,env=env,capture_output=True,text=True,timeout=30)
   output=cp.stdout+cp.stderr
   killed=cp.returncode!=0 and 'FAIL:' in output and 'ERROR:' not in output and 'SyntaxError' not in output and 'ImportError' not in output
   rows.append({'id':mid,'status':'KILLED_BY_ASSERTION' if killed else 'SURVIVED_OR_INVALID','test':'tests.test_rules.'+test,'returncode':cp.returncode,'mutated_source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(output.encode()).hexdigest()})
 return {'scope':'REFERENCE_MUTATIONS_NOT_SDK','status':'PASS' if all(x['status']=='KILLED_BY_ASSERTION' for x in rows) else 'FAIL','mutations':rows}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a.add_argument('--out',type=Path);n=a.parse_args();r=run(n.root)
 text=json.dumps(r,ensure_ascii=False,indent=2);print(text)
 if n.out:n.out.write_text(text+'\n',encoding='utf8')
 sys.exit(0 if r['status']=='PASS' else 3)
