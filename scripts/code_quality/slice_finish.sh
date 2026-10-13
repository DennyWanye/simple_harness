#!/usr/bin/env bash
# 代码质量整治：每片收尾。
# 用法：bash scripts/code_quality/slice_finish.sh <host 模块名> <名字1,名字2,...>
# 1. 测试打桩改到新模块（主入口仍在用的名字不改）
# 2. 找打不到读取方的打桩
# 3. 删主入口里只为测试暂留、现已无用的导入
# 4. 格式化，整理改动测试的导入
# 5. 重跑本片校验（VERIFY_ONLY）
set -uo pipefail
MOD=$1
NAMES=$2
RUFF=${RUFF:-../simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff}
ALT=$(echo "$NAMES" | tr ',' '|')
FILES=$(grep -rlE "main\.($ALT)\b|[\"']($ALT)[\"']" backend/tests | tr '\n' ' ')
if [ -n "$FILES" ]; then
  # shellcheck disable=SC2086
  python3 scripts/code_quality/retarget_tests.py "$MOD" "$NAMES" $FILES
fi
TESTS=$(grep -rlE "^\s*(import main|from host)" backend/tests | tr '\n' ' ')
# shellcheck disable=SC2086
python3 scripts/code_quality/hidden_patches.py "$MOD" $TESTS
python3 scripts/code_quality/prune_host_imports.py
CHANGED_TESTS=$(git diff --name-only -- backend/tests | tr '\n' ' ')
# shellcheck disable=SC2086
[ -n "$CHANGED_TESTS" ] && $RUFF check --config scripts/code_quality/ruff.toml --select I001 --fix -q $CHANGED_TESTS
# shellcheck disable=SC2086
$RUFF format --config scripts/code_quality/ruff.toml -q backend/main.py "backend/host/$MOD.py" $CHANGED_TESTS
python3 scripts/code_quality/patch_targets.py | grep "$MOD" | grep -v "service_context" || true
VERIFY_ONLY=1 bash scripts/code_quality/slice.sh "backend/host/$MOD.py" "-" "$NAMES"
