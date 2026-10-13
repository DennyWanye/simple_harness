#!/bin/bash
# 代码质量整治阶段 2：一片"搬一组名字到新模块"的标准流程。
# 用法：scripts/code_quality/slice.sh <目标模块文件> "<文档字符串>" 名字1,名字2,...
#       VERIFY_ONLY=1 scripts/code_quality/slice.sh <目标模块文件> "" 名字1,...   （已搬完，只跑验证）
# 只做改动与验证，不提交。失败即停，留现场给人看。
set -euo pipefail
cd "$(dirname "$0")/../.."
TARGET=$1; DOC=$2; SEEDS=$3
P=backend/.venv/bin/python
M=backend/main.py
RUFF=${RUFF:-../simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff}
ALLOW=${ALLOW:-plans/2026-10-12-code-quality/allow/2.0b.txt}
S=/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/359a282f-b35d-4676-b9da-e79542041810/scratchpad

if [ -z "${VERIFY_ONLY:-}" ]; then
  out=$($P scripts/code_quality/group_ranges.py $M "$SEEDS")
  echo "$out"
  R=$(echo "$out" | grep "^范围：" | sed 's/范围：//')
  NAMES=$(echo "$out" | grep "^闭包：" | sed 's/闭包：//; s/ //g')
  $P scripts/code_quality/move_block.py $M "$TARGET" "$R" --doc "$DOC" 2>&1 | grep -v "^警告" || true
  [ -f "$TARGET" ] || { echo "搬家未执行"; exit 1; }
else
  NAMES=$SEEDS
fi
$RUFF check --config scripts/code_quality/ruff.toml --select F401 --fix -q "$TARGET" || true
$RUFF check --config scripts/code_quality/ruff.toml --select I001 --fix -q $M backend/host
$RUFF format --config scripts/code_quality/ruff.toml -q $M backend/host
python3 scripts/code_quality/import_smoke.py --tree backend | tail -1
python3 scripts/code_quality/patch_targets.py > $S/patch.txt || true
echo "打桩缺失：$(grep -c "^缺失" $S/patch.txt || true)"
# 引用了被搬名字的测试文件
$P - "$NAMES" > $S/slice_tests.txt <<'PY'
import re, sys
from pathlib import Path
names = [n for n in sys.argv[1].split(",") if n]
pat = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b")
hits = sorted(p.relative_to("backend").as_posix() for p in Path("backend/tests").rglob("test_*.py")
              if pat.search(p.read_text(errors="replace")))
print("\n".join(hits))
PY
echo "引用被搬名字的测试文件：$(wc -l < $S/slice_tests.txt | tr -d ' ')"
for n in $(echo "$NAMES" | tr ',' ' '); do grep -E " $n[，,]| 打桩 $n，" $S/patch.txt || true; done | sort -u | head -20
if [ -s $S/slice_tests.txt ]; then python3 scripts/code_quality/run_related.py backend @$S/slice_tests.txt | tail -8 || true; fi
python3 scripts/code_quality/startup_snapshot.py | tail -1
python3 scripts/code_quality/guard.py --allow "$ALLOW" | tail -1
python3 scripts/code_quality/metrics.py check --new-since HEAD | tail -2
# 格式与未定义名字：搬家和改测试都可能留下（2026-10-13 两次教训）
CHANGED=$(git diff --name-only HEAD -- 'backend/*.py'; git ls-files --others --exclude-standard -- backend/host)
$RUFF format --config scripts/code_quality/ruff.toml --check -q $CHANGED && echo "格式：通过" || echo "格式：不通过"
$RUFF check --isolated --select F821 -q $TARGET | grep -v '`Any`' | grep F821 && echo "未定义名字：有" || echo "未定义名字：无"
wc -l $M "$TARGET"
