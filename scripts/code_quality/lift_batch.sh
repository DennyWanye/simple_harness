#!/usr/bin/env bash
# 代码质量整治阶段 3：按批上提函数内导入。每批全部校验通过才提交，否则停下（改动留在工作区）。
# 用法：bash scripts/code_quality/lift_batch.sh <树 backend|sdk> <种类 如 internal> <说明> <路径...>
set -uo pipefail
TREE=$1; KINDS=$2; DESC=$3; shift 3
LOG=${LOG:-/tmp/cq-lift.log}
: > "$LOG"
python3 scripts/code_quality/lift_imports.py --tree "$TREE" --kinds "$KINDS" --paths "$@" | tee -a "$LOG" | tail -3
LOG="$LOG" bash scripts/code_quality/slice_check.sh "$TREE" || exit 1
git add -A backend sdk/simple-harness-sdk/src scripts/code_quality >/dev/null
git commit -q -m "阶段 3.2：$DESC"
git push -q origin "$(git rev-parse --abbrev-ref HEAD)"
echo "已提交 $(git log --oneline -1 | cut -c1-12)"
