#!/usr/bin/env bash
# TC-WB-10 步骤 5-10：B8 旧记录回退 + 畸形 JSON 韧性。
# 路径替换说明：TC 写死 dev 路径 target/debug/userdata/window_geometry.json；
# 本轮全程用 DESKPET_USER_DATA_DIR=.testenv/cold-A 隔离，故几何文件实际为
# .testenv/cold-A/window_geometry.json（同一份 paths::user_data_dir() 产物）。
set -uo pipefail
ROOT=/Users/denny/projects/simple_harness
A=$ROOT/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260808-r8/artifacts
GF=$ROOT/.testenv/cold-A/window_geometry.json

quit_app() {
  osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to click menu item "退出 Simple Harness" of menu 1 of menu bar item 1 of menu bar 2' >/dev/null 2>&1
  sleep 4
  pkill -f "tauri dev" 2>/dev/null; pkill -f "vite --mode relay" 2>/dev/null
  sleep 2
  echo "  teardown: app=$(pgrep -f 'target/debug/simple-harness'|wc -l|tr -d ' ') backend=$(pgrep -f 'venv/bin/python main.py'|wc -l|tr -d ' ') port=$(lsof -nP -iTCP:8100 -sTCP:LISTEN 2>/dev/null|grep -c LISTEN)"
}

start_and_geom() {  # $1 = log tag
  "$A/launch-app.sh" "$1" >/dev/null
  for i in $(seq 1 45); do
    g=$("$A/drive.sh" geom 2>/dev/null)
    [ -n "$g" ] && { echo "  geom=$g"; return 0; }
    sleep 2
  done
  echo "  geom=TIMEOUT(窗口未出现——疑似崩溃)"; return 1
}

echo "=== 步骤5：备份 + 写入旧桌宠记录 500x640 ==="
cp "$GF" "$A/r6-S10-geom-backup.json" && echo "  备份 -> r6-S10-geom-backup.json: $(cat $A/r6-S10-geom-backup.json)"
quit_app
echo '{"width":500,"height":640}' > "$GF"
echo "  cat 确认: $(cat $GF)"

echo "=== 步骤6：启动 → 期望不崩溃、回退默认 1000x700（不是 clamp 到 800x560）==="
start_and_geom s10-fx-step6
grep "apply_saved_geometry" "$A/s10-fx-step6.log" | head -2 | sed 's/^/  LOG /'

echo "=== 步骤7：拖拽缩放一次 → 退出 → 重启，期望新几何正常写入并恢复 ==="
read -r wx wy ww wh <<< "$("$A/drive.sh" geom)"
osascript -e 'tell application "System Events" to set frontmost of (first process whose name is "simple-harness") to true' >/dev/null 2>&1; sleep 0.5
CX=$((wx+ww)); CY=$((wy+wh))
cliclick m:$CX,$CY w:200 dd:$CX,$CY w:150 m:$((CX+40)),$((CY+30)) w:100 m:$((CX+80)),$((CY+50)) w:100 m:$((CX+120)),$((CY+60)) w:250 du:$((CX+120)),$((CY+60))
sleep 1.5
echo "  拖后 geom=$("$A/drive.sh" geom)  file=$(cat $GF)"
quit_app
start_and_geom s10-fx-step7
echo "  file=$(cat $GF)"

echo "=== 步骤8：对照分支 900x700（合法且高于 min）→ 期望被接受恢复，非默认 1000x700 ==="
quit_app
echo '{"width":900,"height":700}' > "$GF"
echo "  cat 确认: $(cat $GF)"
start_and_geom s10-fx-step8
grep "apply_saved_geometry" "$A/s10-fx-step8.log" | head -2 | sed 's/^/  LOG /'

echo "=== 步骤9：畸形 JSON（截断）→ 期望不崩溃、回默认 1000x700 ==="
quit_app
printf '{"width":500' > "$GF"
echo "  cat 确认: $(cat $GF)"
start_and_geom s10-fx-step9
grep "apply_saved_geometry" "$A/s10-fx-step9.log" | head -2 | sed 's/^/  LOG /'

echo "=== 步骤10：空文件 → 期望不崩溃、回默认 1000x700 ==="
quit_app
: > "$GF"
echo "  cat 确认: [$(cat $GF)] size=$(wc -c < $GF | tr -d ' ')"
start_and_geom s10-fx-step10
grep "apply_saved_geometry" "$A/s10-fx-step10.log" | head -2 | sed 's/^/  LOG /'

echo "=== 步骤11：自清理（恢复步骤5备份）==="
quit_app
cp "$A/r6-S10-geom-backup.json" "$GF"
echo "  已恢复: $(cat $GF)"
echo "=== S10 fixture 完毕 ==="
