#!/usr/bin/env bash
# 代码质量整治：一片改动的通用校验（阶段 3 上提导入、阶段 5 拆类共用）。全部通过退出码 0。
# 用法：LOG=日志 bash scripts/code_quality/slice_check.sh <树 backend|sdk>
# 依次：重生成构建/部署清单 → 导入冒烟 → 启动与控制通道快照 → 禁区比对（CQ_GUARD_ALLOW 放行清单）
#       → 棘轮 → 启动耗时 → 导入隔离 → 相关测试（SDK 加改坏清单资产测试）。新文件要先 git add。
set -uo pipefail
TREE=$1
LOG=${LOG:-/tmp/cq-slice.log}
echo "== 单片校验 $(date '+%F %T')" >> "$LOG"  # 日志是追加写，看最后一段
if [ "$TREE" = backend ]; then
  ../simple_harness/backend/.venv/bin/python scripts/generate_execution_build_manifest.py --write >> "$LOG" 2>&1
else
  # SDK 源码字节变了：按发版脚本同样的上游证据重生成执行图部署清单（验收结论回到 NOT_RUN，发版时再跑验收门）
  (cd sdk/simple-harness-sdk && uv run --frozen python scripts/build/taskgraph_manifest.py generate \
    --upstream "${CQ_UPSTREAM:-../../../simple_harness/.local-test-evidence/2026-10-05/f2-upstream/evidence.json}") >> "$LOG" 2>&1 \
    || { echo "   不通过：重生成执行图部署清单"; exit 1; }
fi
# 全量回归里已有的失败不算本批新失败
# （Host 用阶段 2 的 Host 全量，SDK 用阶段 1 的全量）
EVID=../simple_harness/.local-test-evidence
[ "$TREE" = backend ] && KNOWN_DEFAULT=$EVID/2026-10-13/cq-phase2-full/summary.json || KNOWN_DEFAULT=$EVID/2026-10-12/cq-phase1/summary.json
export CQ_KNOWN=${CQ_KNOWN:-$KNOWN_DEFAULT}
ok=1
check() { if ! grep -qE "$1" "$LOG"; then echo "   不通过：$1"; ok=0; fi; }
python3 scripts/code_quality/import_smoke.py --fresh-changed HEAD >> "$LOG" 2>&1 || { echo "   不通过：导入冒烟"; ok=0; }
python3 scripts/code_quality/startup_snapshot.py >> "$LOG" 2>&1; check "启动快照：一致"
python3 scripts/code_quality/control_snapshot.py >> "$LOG" 2>&1; check "控制通道快照：一致"
# CQ_GUARD_ALLOW：本批允许变化的字面量清单（原因写进实施记录）
python3 scripts/code_quality/guard.py --ref HEAD ${CQ_GUARD_ALLOW:+--allow "$CQ_GUARD_ALLOW"} >> "$LOG" 2>&1; check "禁区比对：通过"
python3 scripts/code_quality/metrics.py check --new-since HEAD >> "$LOG" 2>&1; check "棘轮检查：通过"
python3 scripts/code_quality/startup_profile.py >> "$LOG" 2>&1; check "启动耗时检查：通过"
python3 scripts/code_quality/import_contracts.py >> "$LOG" 2>&1; check "导入隔离约定：全部满足"
grep -E "^启动耗时|^启动完成后多加载|^多加载了" "$LOG" | tail -3
LIST=$(mktemp)
# CQ_TESTS=清单文件：用调用者挑好的相关测试代替按模块路径自动挑（自动挑超过约 150 个文件时用，见阶段 5 裁决）
if [ -n "${CQ_TESTS:-}" ]; then cp "$CQ_TESTS" "$LIST"; else
python3 scripts/code_quality/tests_for_changes.py --tree "$TREE" > "$LIST"
fi
# 改坏清单用源码原文定锚：SDK 源码一变就要核对锚点（阶段 4 发版门因此挡过一次）
[ "$TREE" = sdk ] && echo "tests/orchestrator/acceptance_assets/test_acceptance_assets.py" >> "$LIST"
echo "相关测试文件 $(wc -l < "$LIST" | tr -d ' ') 个" | tee -a "$LOG"
if [ -s "$LIST" ]; then
  python3 scripts/code_quality/run_related.py "$TREE" @"$LIST" >> "$LOG" 2>&1
  grep -E "^失败|相关测试：" "$LOG" | tail -15
  check "相关测试：.*失败 0"
fi
[ $ok -eq 1 ] || { echo "停下，日志 $LOG"; exit 1; }
