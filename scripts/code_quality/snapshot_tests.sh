#!/usr/bin/env bash
# 代码质量整治：在冻结的快照副本里跑 SDK 相关测试，工作树可以继续改下一片（2026-10-16 用户批准的提速办法 ②）。
# 用法：LOG=日志 bash scripts/code_quality/snapshot_tests.sh <提交> <测试清单文件>
# 做法：git worktree add --detach 到该提交 → CQ_SDK_DIR 指向快照跑 run_related.py → 跑完删掉快照。
# 测试一次只跑一组（不并行）；改坏清单资产测试照旧加进清单。
set -uo pipefail
REF=$1
LIST_IN=$2
LOG=${LOG:-/tmp/cq-snapshot.log}
SHA=$(git rev-parse --short "$REF")
# 放在仓库旁边：有测试按相对路径读 ../simple_harness 下的证据文件
SNAP=${CQ_SNAP_ROOT:-$(cd "$(git rev-parse --show-toplevel)/.." && pwd)}/cq-snap-$SHA
# 一次只跑一组测试：多次调用按锁排队（锁目录随进程退出删除）
LOCK=${TMPDIR:-/tmp}/cq-snapshot-tests.lock
until mkdir "$LOCK" 2>/dev/null; do sleep 20; done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
echo "== 快照测试 $(date '+%F %T') 提交 $SHA 快照 $SNAP" >> "$LOG"
git worktree add --detach "$SNAP" "$REF" >> "$LOG" 2>&1 || { echo "   不通过：建快照"; exit 1; }
LIST=$(mktemp)
cp "$LIST_IN" "$LIST"
grep -qx "tests/orchestrator/acceptance_assets/test_acceptance_assets.py" "$LIST" \
  || echo "tests/orchestrator/acceptance_assets/test_acceptance_assets.py" >> "$LIST"
echo "相关测试文件 $(wc -l < "$LIST" | tr -d ' ') 个" | tee -a "$LOG"
CQ_SDK_DIR="$SNAP/sdk/simple-harness-sdk" python3 scripts/code_quality/run_related.py sdk @"$LIST" >> "$LOG" 2>&1
rc=$?
grep -E "^失败|相关测试：" "$LOG" | tail -15
git worktree remove --force "$SNAP" >> "$LOG" 2>&1
rm -f "$LIST"
echo "== 快照测试结束 $(date '+%F %T') 退出码 $rc" >> "$LOG"
exit $rc
