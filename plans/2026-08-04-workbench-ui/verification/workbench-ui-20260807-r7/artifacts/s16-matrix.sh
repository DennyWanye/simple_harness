#!/usr/bin/env bash
# TC-WB-16 退出路径矩阵：红钮 / Cmd+Q / 托盘退出 三条路径 + 隐藏语义反证。
# dev 模式：按 TC 的「dev 模式豁免注」登记包装进程 PID 作对照；主进程与 backend
# 必须零残留。实测三条路径连包装进程也一并消失，故无需动用豁免。
set -uo pipefail
ROOT=/Users/denny/projects/simple_harness
A=$ROOT/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260807-r7/artifacts

check() {  # $1 = 路径名
  sleep 4
  local app bk port tray
  app=$(pgrep -f 'target/debug/simple-harness' | wc -l | tr -d ' ')
  bk=$(pgrep -f 'venv/bin/python main.py' | wc -l | tr -d ' ')
  port=$(lsof -nP -iTCP:8100 -sTCP:LISTEN 2>/dev/null | grep -c LISTEN)
  tray=$(osascript -e 'tell application "System Events" to return (count of (every process whose name is "simple-harness"))' 2>/dev/null || echo "?")
  echo "  [$1] app=$app backend=$bk port8100=$port 进程存在=$tray"
  pkill -f "tauri dev" 2>/dev/null; pkill -f "vite --mode relay" 2>/dev/null; sleep 2
}

start_and_geom() {
  "$A/launch-app.sh" "$1" >/dev/null
  for i in $(seq 1 45); do
    g=$("$A/drive.sh" geom 2>/dev/null)
    [ -n "$g" ] && { echo "  重启 geom=$g"; return 0; }
    sleep 2
  done
  echo "  重启 geom=TIMEOUT"; return 1
}

front() { osascript -e 'tell application "System Events" to set frontmost of (first process whose name is "simple-harness") to true' >/dev/null 2>&1; sleep 0.8; }

echo "=== 步骤1 几何锚点（真 HID 拖拽）==="
front
read -r wx wy ww wh <<< "$("$A/drive.sh" geom)"
echo "  起始 geom=$wx $wy $ww $wh"
# 拖标题栏移动 +60,+40
cliclick m:$((wx+ww/2)),$((wy+14)) w:200 dd:$((wx+ww/2)),$((wy+14)) w:150 \
        m:$((wx+ww/2+30)),$((wy+34)) w:100 m:$((wx+ww/2+60)),$((wy+54)) w:250 du:$((wx+ww/2+60)),$((wy+54))
sleep 1.8
read -r wx wy ww wh <<< "$("$A/drive.sh" geom)"
echo "  锚点 geom=$wx $wy $ww $wh"
ANCHOR="$wx $wy $ww $wh"
"$A/drive.sh" shot r7-S16-anchor >/dev/null
echo "  几何文件=$(cat $ROOT/.testenv/cold-A/window_geometry.json)"

echo "=== 步骤2 路径A 标题栏红钮 ==="
front
# 红钮在标题栏左侧第一个，距窗口左上角约 (13,13) 逻辑点
cliclick m:$((wx+13)),$((wy+13)) w:250 c:$((wx+13)),$((wy+13))
check "路径A 红钮"

echo "=== 步骤3 重启 ==="
start_and_geom s16-r7-a
echo "  锚点对照: [$ANCHOR]"

echo "=== 步骤4 路径B Cmd+Q ==="
front
osascript -e 'tell application "System Events" to keystroke "q" using command down' >/dev/null 2>&1
check "路径B Cmd+Q"

echo "=== 步骤5 重启 ==="
start_and_geom s16-r7-b
echo "  锚点对照: [$ANCHOR]"

echo "=== 步骤6 路径C 托盘退出 ==="
osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to click menu item "退出 Simple Harness" of menu 1 of menu bar item 1 of menu bar 2' >/dev/null 2>&1
check "路径C 托盘退出"

echo "=== 步骤7 重启 ==="
start_and_geom s16-r7-c
echo "  锚点对照: [$ANCHOR]"
"$A/drive.sh" shot r7-S16-restored3 >/dev/null

echo "=== 步骤8 隐藏语义反证（托盘「隐藏主窗」）==="
osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to click menu item "隐藏主窗" of menu 1 of menu bar item 1 of menu bar 2' >/dev/null 2>&1
sleep 3
W=$(osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to return (count of windows)' 2>&1)
echo "  windows=$W app=$(pgrep -f 'target/debug/simple-harness'|wc -l|tr -d ' ') backend=$(pgrep -f 'venv/bin/python main.py'|wc -l|tr -d ' ') port8100=$(lsof -nP -iTCP:8100 -sTCP:LISTEN 2>/dev/null|grep -c LISTEN)"
TRAYOK=$(osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to return (count of menu bar items of menu bar 2)' 2>&1)
echo "  托盘菜单项存在=$TRAYOK"
echo "  → 恢复主窗"
osascript -e 'tell application "System Events" to tell (first process whose name is "simple-harness") to click menu item "显示主窗" of menu 1 of menu bar item 1 of menu bar 2' >/dev/null 2>&1
sleep 2.5
echo "  恢复后 geom=$("$A/drive.sh" geom 2>&1 | head -1)"
echo "=== S16 矩阵完毕 ==="
