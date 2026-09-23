#!/usr/bin/env python3
"""Optional author/reference verification, NOT production mutation coverage."""
import ast,json,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
M=[
 ('RM01','reference/runtime_rules.py','self.configured_total-self.prior-self.output-self.safety-self.headroom','self.configured_total-self.output-self.safety-self.headroom','tests.test_rules.ContextRules.test_prior_is_subtracted_once'),
 ('RM02','reference/runtime_rules.py','and t.required_groups<=ids','and bool(t.required_groups)','tests.test_rules.ContextRules.test_partial_old_turn_not_counted'),
 ('RM03','reference/runtime_rules.py',"return 'RECONCILE_ORIGINAL'","return 'CANCEL_UNSENT'",'tests.test_rules.NativeRules.test_unknown_never_replaced'),
 ('RM04','reference/runtime_rules.py','out=out&s','out=out|s','tests.test_rules.NativeRules.test_permissions_intersection'),
 ('RM05','reference/runtime_rules.py',"key=lambda c:(-c['priority'],c['id'])","key=lambda c:(c['priority'],c['id'])",'tests.test_rules.NativeRules.test_provider_priority_actual_field'),
 ('RM06','reference/runtime_rules.py',"'EXTERNAL_EFFECT':'ORIGINAL_OPERATION_INTENT'","'EXTERNAL_EFFECT':'ORIGINAL_TOOL_EXECUTOR'",'tests.test_rules.NativeRules.test_effect_not_direct_tool'),
 ('RM07','reference/schema_codec.py',"need(value['has_more']==(value['next_cursor'] is not None))","need(True)",'tests.test_contracts.Contracts.test_history_cursor_flags_inconsistent_refused'),
 ('RM08','reference/schema_codec.py',"need(value['planned_request_hash']==tr['request_hash'],'REQUEST_HASH_MISMATCH')","need(True)",'tests.test_contracts.Contracts.test_context_and_meter_hashes_match'),
 ('RM09','reference/runtime_rules.py',"if current['now_ms']>=stored['expires_at_ms']:raise RuleError('CURSOR_EXPIRED')","if False:raise RuleError('CURSOR_EXPIRED')",'tests.test_rules.NativeRules.test_cursor_owner_generation_expiry'),
 ('RM10','reference/runtime_rules.py',"if type(required)is not bool or type(degrade)is not bool or not(required or degrade):raise RuleError('STATE_COMBINATION_INVALID')","if type(required)is not bool or type(degrade)is not bool:raise RuleError('STATE_COMBINATION_INVALID')",'tests.test_rules.NativeRules.test_activation_truth_table')]
results=[]
for mid,rel,before,after,test in M:
    with tempfile.TemporaryDirectory(prefix='arp-reference-mutation-')as tmp:
        dst=Path(tmp)
        for name in ('reference','tests','contracts','examples','sql'):
            shutil.copytree(ROOT/name,dst/name,ignore=shutil.ignore_patterns('__pycache__'))
        f=dst/rel;text=f.read_text()
        if text.count(before)!=1:results.append({'id':mid,'status':'INVALID_TARGET','count':text.count(before)});continue
        f.write_text(text.replace(before,after));ast.parse(f.read_text())
        harness="""import json,unittest,sys
r=unittest.TestResult();unittest.defaultTestLoader.loadTestsFromName(sys.argv[1]).run(r)
print(json.dumps({'run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped)}))
"""
        p=subprocess.run([sys.executable,'-B','-c',harness,test],cwd=tmp,capture_output=True,text=True,timeout=20)
        try:counts=json.loads(p.stdout.strip())
        except ValueError:counts={'errors':1,'failures':0,'run':0,'skips':0}
        killed=p.returncode==0 and counts['run']==1 and counts['failures']>0 and counts['errors']==0 and counts['skips']==0
        results.append({'id':mid,'file':rel,'test':test,'status':'KILLED_BY_ASSERTION'if killed else'NOT_PROVEN','counts':counts})
report={'scope':'REFERENCE_MUTATIONS_ONLY','mutations':results,'all_killed':all(x['status']=='KILLED_BY_ASSERTION'for x in results)}
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['all_killed']else 1)
