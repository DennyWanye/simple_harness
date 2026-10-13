#!/usr/bin/env bash
# 代码质量整治：按组连续搬家。每组：搬家 → 收尾 → 控制通道快照；全部通过才提交，否则停下。
# 用法：bash scripts/code_quality/slice_batch.sh <组清单文件>
# 清单每行：模块名|说明|名字1,名字2,...
set -uo pipefail
LIST=$1
LOG_DIR=${LOG_DIR:-/tmp/cq-batch}
mkdir -p "$LOG_DIR"
while IFS='|' read -r MOD DESC NAMES; do
  [ -z "$MOD" ] && continue
  case "$MOD" in \#*) continue ;; esac
  LOG="$LOG_DIR/$MOD.log"
  echo "== $MOD"
  bash scripts/code_quality/slice.sh "backend/host/$MOD.py" "$DESC" "$NAMES" > "$LOG" 2>&1
  bash scripts/code_quality/slice_finish.sh "$MOD" "$NAMES" >> "$LOG" 2>&1
  python3 scripts/code_quality/control_snapshot.py >> "$LOG" 2>&1
  FIN=$(awk '/^backend: 导入/{buf=""} {buf=buf"\n"$0} END{print buf}' "$LOG")
  ok=1
  for want in "导入 .*失败 0" "打桩缺失：0" "相关测试：.*失败 0" "启动快照：一致" "禁区比对：通过" "棘轮检查：通过" "格式：通过" "未定义名字：无" "控制通道快照：一致"; do
    if ! echo "$FIN" | grep -qE "$want"; then echo "   不通过：$want"; ok=0; fi
  done
  if grep -q "警告：源文件不再导入这些 host 模块" "$LOG"; then echo "   不通过：主入口不再导入某个 host 模块，需人工确认导入期副作用"; ok=0; fi
  if [ $ok -ne 1 ]; then echo "停在 $MOD，日志 $LOG"; exit 1; fi
  git add -A backend/main.py backend/host backend/tests >/dev/null
  git commit -q -m "阶段 2.25：$DESC 搬到 host/$MOD.py"
  echo "   已提交 $(git log --oneline -1 | cut -c1-12)"
done < "$LIST"
git push -q origin "$(git rev-parse --abbrev-ref HEAD)"
echo "全部完成并推送"
