#!/usr/bin/env python3
"""Five R1-R5 author/reference mutations; not SDK mutation evidence."""
import ast,json,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
M=[
 ('RR1','reference/purge_rules.py',"and session.get('destroy_command_id')is None and session.get('purge_progress')is None",'', 'tests.test_review_r1_r5.PurgeReview.test_R1_block_is_durable_without_reviving'),
 ('RR2','reference/retrieval_status.py',"need(r['status'] == status_for(c,total))","need(True)",'tests.test_review_r1_r5.SearchReview.test_R2_A_and_B_rejected_receipt_page_and_management'),
 ('RR3','reference/context_recall.py',"r.record_seq<=request['highwater'] and r.group_id not in exclusions","r.group_id not in exclusions",'tests.test_review_r1_r5.RecallReview.test_R3_composer_reaches_later_page_and_excludes_frozen_groups'),
 ('RR4','reference/search_scan.py',"key=lambda x:(-x[1],keys[x[0]])","key=lambda x:(-x[1],x[0])",'tests.test_review_r1_r5.RankAndLimitReview.test_R4_channel_top1_uses_full_key_not_chunk'),
 ('RR5','contracts/runtime-plane.schema.json',None,None,'tests.test_review_r1_r5.RankAndLimitReview.test_R5_policy_upper_bounds_and_exact_boundary')]
results=[]
for mid,rel,before,after,test in M:
 with tempfile.TemporaryDirectory(prefix='arp-r-review-mutation-')as tmp:
  root=Path(tmp)
  for name in('reference','tests','contracts','examples','sql'):shutil.copytree(ROOT/name,root/name,ignore=shutil.ignore_patterns('__pycache__'))
  path=root/rel
  if mid=='RR5':
   data=json.loads(path.read_text());data['$defs']['Policy']['properties']['max_scan_rows_per_page']['maximum']=4096;path.write_text(json.dumps(data))
  else:
   text=path.read_text()
   if text.count(before)!=1:results.append({'id':mid,'status':'INVALID_TARGET'});continue
   path.write_text(text.replace(before,after));ast.parse(path.read_text())
  harness="import unittest,json,sys;r=unittest.TestResult();unittest.defaultTestLoader.loadTestsFromName(sys.argv[1]).run(r);print(json.dumps({'run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped)}))"
  cp=subprocess.run([sys.executable,'-B','-c',harness,test],cwd=root,capture_output=True,text=True,timeout=20)
  try:counts=json.loads(cp.stdout)
  except ValueError:counts={'run':0,'failures':0,'errors':1,'skips':0}
  passed=cp.returncode==0 and counts['run']==1 and counts['failures']>0 and not counts['errors'] and not counts['skips']
  results.append({'id':mid,'file':rel,'test':test,'status':'KILLED_BY_ASSERTION'if passed else'NOT_PROVEN','counts':counts})
report={'scope':'R1_R5_REFERENCE_MUTATIONS_ONLY','mutations':results,'all_killed':all(x['status']=='KILLED_BY_ASSERTION'for x in results)}
print(json.dumps(report,indent=2));raise SystemExit(0 if report['all_killed']else 1)
