#!/usr/bin/env bash
# 彻底清场：tauri 包装 + vite + app + **Python backend**。
#
# 为什么单列一个脚本：只 pkill "tauri dev" 和 "target/debug/simple-harness"
# **不会杀掉 Python backend**，它会变成孤儿继续占着 8100（项目 CLAUDE.md 坑 #1）。
# 后果极隐蔽：下一次启动时 `port8100=1 backend=1` 看着正常，实际服务的是**上一轮
# 的 user-data 目录**；等前端走完 onboarding 真去 spawn 时才炸「端口已被占用」。
# 2026-08-08 r8 的 S01 首轮就是这么作废重跑的。
# backend 的匹配 pattern 必须是 'venv/bin/python main.py'——
# 'backend/main.py' 永远匹配不到（cwd=backend/，argv 只有 main.py）。
set -uo pipefail
pkill -f "tauri dev" 2>/dev/null
pkill -f "vite --mode relay" 2>/dev/null
sleep 1
pkill -f "target/debug/simple-harness" 2>/dev/null
sleep 1
pkill -f "venv/bin/python main.py" 2>/dev/null
sleep 2
app=$(pgrep -f 'target/debug/simple-harness'|wc -l|tr -d ' ')
be=$(pgrep -f 'venv/bin/python main.py'|wc -l|tr -d ' ')
port=$(lsof -nP -iTCP:8100 -sTCP:LISTEN 2>/dev/null|grep -c LISTEN)
vite=$(pgrep -f 'vite --mode relay'|wc -l|tr -d ' ')
echo "cleanup: app=$app backend=$be port8100=$port vite=$vite"
[ "$app$be$port$vite" = "0000" ] || { echo "CLEANUP_INCOMPLETE" >&2; exit 1; }
