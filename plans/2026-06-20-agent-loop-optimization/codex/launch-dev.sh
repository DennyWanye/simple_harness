#!/bin/bash
# 启动 Tauri dev，注入 worktree(master) backend 环境，跑含 Batch A 改动的源码
set -e
cd /g/projects/deskpet/tauri-app
export DESKPET_BACKEND_DIR="G:/projects/deskpet/backend"
export DESKPET_PYTHON="G:/projects/deskpet/backend/.venv/Scripts/python.exe"
export DESKPET_DEV_MODE=1
echo "[launch] env set, starting npx tauri dev ..."
npx tauri dev
