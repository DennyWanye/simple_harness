#!/usr/bin/env bash
# 彻底清场：tauri 包装 + vite + app + **Python backend**。
# 只杀 tauri/app 不会杀掉 Python backend——它会变成孤儿继续占 8100
# （项目 CLAUDE.md 坑 #1）。后果极隐蔽：下一次启动看着正常，实际服务的是
# **上一轮的 user-data 目录**。
# backend 的匹配 pattern 必须是 'venv/bin/python main.py'——
# 'backend/main.py' 永远匹配不到（cwd=backend/，argv 只有 main.py）。
# 2026-08-09 相对 r9 的改动：relay 已移除，vite 不再用 --mode relay。
set -uo pipefail
pkill -f "tauri dev" 2>/dev/null
pkill -f "node_modules/.bin/vite" 2>/dev/null
pkill -f "vite --mode" 2>/dev/null
sleep 1
pkill -f "target/debug/simple-harness" 2>/dev/null
sleep 1
pkill -f "venv/bin/python main.py" 2>/dev/null
sleep 2
app=$(pgrep -f 'target/debug/simple-harness'|wc -l|tr -d ' ')
be=$(pgrep -f 'venv/bin/python main.py'|wc -l|tr -d ' ')
port=$(lsof -nP -iTCP:8100 -sTCP:LISTEN 2>/dev/null|grep -c LISTEN)
vite=$(pgrep -f 'node_modules/.bin/vite'|wc -l|tr -d ' ')
echo "cleanup: app=$app backend=$be port8100=$port vite=$vite"
[ "$app$be$port$vite" = "0000" ] || { echo "CLEANUP_INCOMPLETE" >&2; exit 1; }
