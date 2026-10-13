#!/usr/bin/env bash
# 代码质量整治阶段 3：按批上提函数内导入。每批全部校验通过才提交，否则停下（改动留在工作区）。
# 用法：bash scripts/code_quality/lift_batch.sh <树 backend|sdk> <种类 如 internal> <说明> <路径...>
set -uo pipefail
TREE=$1; KINDS=$2; DESC=$3; shift 3
LOG=${LOG:-/tmp/cq-lift.log}
: > "$LOG"
python3 scripts/code_quality/lift_imports.py --tree "$TREE" --kinds "$KINDS" --paths "$@" | tee -a "$LOG" | tail -3
if [ "$TREE" = backend ]; then
  ../simple_harness/backend/.venv/bin/python scripts/generate_execution_build_manifest.py --write >> "$LOG" 2>&1
fi
ok=1
check() { if ! grep -qE "$1" "$LOG"; then echo "   不通过：$1"; ok=0; fi; }
python3 scripts/code_quality/import_smoke.py --fresh-changed HEAD >> "$LOG" 2>&1 || { echo "   不通过：导入冒烟"; ok=0; }
python3 scripts/code_quality/startup_snapshot.py >> "$LOG" 2>&1; check "启动快照：一致"
python3 scripts/code_quality/control_snapshot.py >> "$LOG" 2>&1; check "控制通道快照：一致"
python3 scripts/code_quality/guard.py --ref HEAD >> "$LOG" 2>&1; check "禁区比对：通过"
python3 scripts/code_quality/metrics.py check --new-since HEAD >> "$LOG" 2>&1; check "棘轮检查：通过"
python3 scripts/code_quality/startup_profile.py >> "$LOG" 2>&1; check "启动耗时检查：通过"
python3 scripts/code_quality/import_contracts.py >> "$LOG" 2>&1; check "导入隔离约定：全部满足"
grep -E "^启动耗时|^启动完成后多加载|^多加载了" "$LOG" | tail -3
LIST=$(mktemp)
python3 scripts/code_quality/tests_for_changes.py --tree "$TREE" > "$LIST"
echo "相关测试文件 $(wc -l < "$LIST" | tr -d ' ') 个" | tee -a "$LOG"
if [ -s "$LIST" ]; then
  python3 scripts/code_quality/run_related.py "$TREE" @"$LIST" >> "$LOG" 2>&1
  grep -E "^失败|相关测试：" "$LOG" | tail -15
  check "相关测试：.*失败 0"
fi
if [ $ok -ne 1 ]; then echo "停下，日志 $LOG"; exit 1; fi
git add -A backend sdk/simple-harness-sdk/src scripts/code_quality >/dev/null
git commit -q -m "阶段 3.2：$DESC"
git push -q origin "$(git rev-parse --abbrev-ref HEAD)"
echo "已提交 $(git log --oneline -1 | cut -c1-12)"
