#!/bin/bash
O=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-30/final-opt94
cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/sdk/simple-harness-sdk
P="-q -p no:cacheprovider -rfE -o faulthandler_timeout=240 --continue-on-collection-errors"
uv run --frozen --group dev --extra local-capacity pytest $P tests/orchestrator/full_target > $O/full_target.log 2>&1
echo "full_target $(grep -E '[0-9]+ (passed|failed)' $O/full_target.log | tail -1)"
for d in host_support p32 p33 p35 step08 step09 lc2 p34 step02 step03 step04 step05 step06 step07; do
  uv run --frozen pytest $P tests/orchestrator/$d > $O/$d.log 2>&1
  echo "$d $(grep -E '[0-9]+ (passed|failed)' $O/$d.log | tail -1)"
done
for d in gap_phase1 p36; do
  uv run --frozen --group dev --extra local-capacity pytest $P tests/orchestrator/$d > $O/$d.log 2>&1
  echo "$d $(grep -E '[0-9]+ (passed|failed)' $O/$d.log | tail -1)"
done
uv run --frozen --group dev --extra local-capacity pytest $P tests/orchestrator/p33_replay_audit.py tests/orchestrator/test_code_test_no_tests_collected.py tests/orchestrator/test_collection_refusal_isolation.py tests/orchestrator/test_critic_test_evidence_order.py > $O/orch_root.log 2>&1
echo "orch_root $(grep -E '[0-9]+ (passed|failed)' $O/orch_root.log | tail -1)"
uv run --frozen --group dev --extra local-capacity pytest $P tests --ignore=tests/orchestrator > $O/non_orch.log 2>&1
echo "non_orch $(grep -E '[0-9]+ (passed|failed)' $O/non_orch.log | tail -1)"
cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend
uv run --frozen pytest -q -p no:cacheprovider -rfE tests/orchestration tests/sdk_adapters > $O/host.log 2>&1
echo "host $(grep -E '[0-9]+ (passed|failed)' $O/host.log | tail -1)"
cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/tauri-app
npx vitest run > $O/frontend.log 2>&1
echo "frontend $(grep -E 'Tests +[0-9]' $O/frontend.log | tail -1)"
echo ALL-DONE
